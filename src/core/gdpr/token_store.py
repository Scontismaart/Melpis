"""Temporary GDPR export store: encrypted payloads and one-time token hashes.

Redis/Valkey is shared in production and uses a 15-minute TTL; local test/dev
may use encrypted process memory. Production fails closed if Redis or the
application encryption key is unavailable.
"""
import asyncio
import hashlib
import json
import os
import logging
import time
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 15 * 60
_PREFIX = "gdpr:export:"
_memory: dict[str, str] = {}
_redis = None
DOWNLOAD_COOKIE = "gdpr_export_token"


class TokenStoreUnavailable(RuntimeError):
    """GDPR export storage or encryption is unavailable."""


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
        if (
            not isinstance(document, dict)
            or not isinstance(document.get("org_id"), str)
            or not isinstance(document.get("data"), dict)
            or not isinstance(document.get("expires"), (int, float))
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
        if _is_production():
            raise TokenStoreUnavailable("REDIS_URL is required for production export tokens")
        return None

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
        if _is_production():
            raise TokenStoreUnavailable("Redis client could not be initialized") from exc
        return None


async def save_token(token: str, org_id: str, data: dict) -> None:
    try:
        serialized = json.dumps(
            {"org_id": org_id, "data": data, "expires": time.time() + TOKEN_TTL_SECONDS}
        ).encode("utf-8")
        payload = _fernet().encrypt(serialized).decode("ascii")
    except (TypeError, ValueError) as exc:
        raise TokenStoreUnavailable("GDPR export could not be encrypted") from exc
    r = await _get_redis()
    if r is not None:
        try:
            await r.set(_storage_key(token), payload, ex=TOKEN_TTL_SECONDS)
            return
        except Exception as e:
            logger.warning("token_store: redis set failed")
            if _is_production():
                raise TokenStoreUnavailable("Redis export-token write failed") from e
    _memory[_storage_key(token)] = payload


async def pop_token(token: str) -> dict | None:
    """Consuma il token (one-time). None se assente o scaduto."""
    key = _storage_key(token)
    r = await _get_redis()
    if r is not None:
        try:
            # Decrypt before consuming so a missing or rotated key does not
            # destroy an otherwise recoverable, short-lived export.
            raw = await r.get(key)
            if raw is None:
                return None
            doc = _decode_payload(raw)
            if doc["expires"] < time.time():
                await r.getdel(key)
                return None
            # The final GETDEL arbitrates concurrent downloads: only the
            # request that consumes the stored value receives the payload.
            if await r.getdel(key) != raw:
                return None
            return doc
        except TokenStoreUnavailable:
            raise
        except Exception as e:
            logger.warning("token_store: redis getdel failed")
            if _is_production():
                raise TokenStoreUnavailable("Redis export-token read failed") from e
    entry = _memory.get(key)
    if entry is None:
        return None
    doc = _decode_payload(entry)
    if doc["expires"] < time.time():
        _memory.pop(key, None)
        return None
    if _memory.get(key) != entry:
        return None
    _memory.pop(key, None)
    return doc
