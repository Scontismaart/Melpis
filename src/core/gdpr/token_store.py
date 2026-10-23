"""Encrypted, identity-bound, one-time storage for GDPR exports.

Redis/Valkey is required in every environment and uses a 15-minute TTL.
Downloads fail closed if the registry or application encryption key is absent.
"""
import asyncio
import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 15 * 60
_PREFIX = "gdpr:export:"
_memory: dict[str, str] = {}
_redis = None
DOWNLOAD_COOKIE = "gdpr_export_token"

_COMPARE_AND_DELETE = """
local value = redis.call('GET', KEYS[1])
if value and value == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class TokenStoreUnavailable(RuntimeError):
    """GDPR export storage or encryption is unavailable."""


@dataclass(frozen=True)
class ExportToken:
    """Decrypted authorization context plus the ciphertext used for CAS consume."""

    org_id: str
    auth_user_id: str
    session_id: str
    user_id: str
    role: str
    data: dict = field(repr=False)
    expires: float
    payload: str = field(repr=False)


def _fernet() -> Fernet:
    key = os.getenv("ENCRYPTION_KEY", "").strip()
    if not key:
        raise TokenStoreUnavailable("ENCRYPTION_KEY is required for GDPR export storage")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise TokenStoreUnavailable("GDPR export encryption is unavailable") from exc


def _storage_key(token: str) -> str:
    return _PREFIX + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _decode_payload(payload: str | bytes) -> dict:
    try:
        encoded = payload.encode("ascii") if isinstance(payload, str) else payload
        if not isinstance(encoded, bytes):
            raise TypeError("Invalid GDPR export payload")
        document = json.loads(_fernet().decrypt(encoded))
        expires = document.get("expires") if isinstance(document, dict) else None
        if (
            not isinstance(document, dict)
            or not all(isinstance(document.get(key), str) and document[key] for key in (
                "org_id", "auth_user_id", "session_id", "user_id", "role",
            ))
            or document.get("role") != "owner"
            or not isinstance(document.get("data"), dict)
            or isinstance(expires, bool)
            or not isinstance(expires, (int, float))
            or not math.isfinite(expires)
        ):
            raise ValueError("Invalid GDPR export payload")
        return document
    except TokenStoreUnavailable:
        raise
    except (InvalidToken, ValueError, TypeError, UnicodeEncodeError) as exc:
        raise TokenStoreUnavailable("GDPR export could not be decrypted") from exc


def _is_production() -> bool:
    from src.core.security.docs import is_production

    return is_production()


async def _get_redis():
    global _redis
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        raise TokenStoreUnavailable("REDIS_URL is required for export tokens")

    try:
        current_loop = asyncio.get_running_loop()
        if _redis is not None:
            pool = getattr(_redis, "connection_pool", None)
            pool_loop = getattr(pool, "_loop", None)
            if pool_loop is not None and (pool_loop != current_loop or pool_loop.is_closed()):
                _redis = None
    except Exception:
        _redis = None

    if _redis is not None:
        return _redis

    try:
        from redis.asyncio import Redis

        _redis = Redis.from_url(url, decode_responses=True)
        return _redis
    except Exception as exc:
        logger.warning("token_store: Redis client initialization failed")
        raise TokenStoreUnavailable("Redis export-token store is unavailable") from exc


async def save_token(
    token: str,
    org_id: str,
    data: dict,
    *,
    auth_user_id: str,
    session_id: str,
    user_id: str,
) -> None:
    if not all((org_id, auth_user_id, session_id, user_id)):
        raise TokenStoreUnavailable("GDPR export requires a bound owner session")
    try:
        serialized = json.dumps(
            {
                "org_id": org_id,
                "auth_user_id": auth_user_id,
                "session_id": session_id,
                "user_id": user_id,
                "role": "owner",
                "data": data,
                "expires": time.time() + TOKEN_TTL_SECONDS,
            }
        ).encode("utf-8")
        payload = _fernet().encrypt(serialized).decode("ascii")
    except TokenStoreUnavailable:
        raise
    except (TypeError, ValueError) as exc:
        raise TokenStoreUnavailable("GDPR export could not be encrypted") from exc

    key = _storage_key(token)
    r = await _get_redis()
    if r is not None:
        try:
            await r.set(key, payload, ex=TOKEN_TTL_SECONDS)
            return
        except Exception as exc:
            logger.warning("token_store: Redis write failed")
            raise TokenStoreUnavailable("Redis export-token write failed") from exc
    raise TokenStoreUnavailable("Shared export-token registry is required")


async def read_token(token: str) -> ExportToken | None:
    """Read and decrypt without consuming; authorization must happen next."""
    key = _storage_key(token)
    r = await _get_redis()
    if r is not None:
        try:
            payload = await r.get(key)
        except Exception as exc:
            logger.warning("token_store: Redis read failed")
            raise TokenStoreUnavailable("Redis export-token read failed") from exc
    else:
        raise TokenStoreUnavailable("Shared export-token registry is required")

    if payload is None:
        return None
    document = _decode_payload(payload)
    if document["expires"] <= time.time():
        return None
    return ExportToken(
        org_id=document["org_id"],
        auth_user_id=document["auth_user_id"],
        session_id=document["session_id"],
        user_id=document["user_id"],
        role=document["role"],
        data=document["data"],
        expires=document["expires"],
        payload=payload.decode("ascii") if isinstance(payload, bytes) else payload,
    )


async def consume_token(token: str, expected: ExportToken) -> dict | None:
    """Compare-and-delete atomically; only the winner receives the export."""
    if expected.expires <= time.time():
        return None
    key = _storage_key(token)
    r = await _get_redis()
    if r is not None:
        try:
            consumed = await r.eval(_COMPARE_AND_DELETE, 1, key, expected.payload)
        except Exception as exc:
            # A lost Redis reply after the script executed has an inherently
            # ambiguous outcome. The caller fails closed and can request a new
            # export; no data is returned unless this request receives success.
            logger.warning("token_store: Redis atomic consume failed")
            raise TokenStoreUnavailable("Redis export-token consume failed") from exc
        return expected.data if int(consumed or 0) == 1 else None

    raise TokenStoreUnavailable("Shared export-token registry is required")


async def revoke_token(token: str, expected: ExportToken) -> bool:
    """Use the same CAS as consume; do not expose the associated export."""
    return await consume_token(token, expected) is not None


async def pop_token(token: str) -> dict | None:
    """Compatibility helper for one-time consumption after a prior read."""
    pending = await read_token(token)
    if pending is None:
        return None
    data = await consume_token(token, pending)
    if data is None:
        return None
    return {
        "org_id": pending.org_id,
        "auth_user_id": pending.auth_user_id,
        "session_id": pending.session_id,
        "user_id": pending.user_id,
        "role": pending.role,
        "data": data,
        "expires": pending.expires,
    }
