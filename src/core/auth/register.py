"""Email signup and scanner-safe Supabase PKCE confirmation callback."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import secrets
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from src.core.auth import bff, throttle
from src.core.auth.csrf import issue_csrf_token
from src.core.auth.dependencies import get_repo
from src.core.auth.signup_policy import beta_email_is_allowed
from src.core.billing.plans import TRIAL_DAYS

router = APIRouter(prefix="/api/auth", tags=["auth"])

PASSWORD_MIN = 10

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SPECIAL_RE = re.compile(r"[^A-Za-z0-9]")
_UPPER_RE = re.compile(r"[A-Z]")
_NUMBER_RE = re.compile(r"[0-9]")
_CODE_RE = re.compile(r"^[A-Za-z0-9._~-]{16,1024}$")
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_EMAIL_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_PENDING_ID_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_NEXT_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
_SIGNUP_MAX = 5
_SIGNUP_WINDOW = 60 * 60
_SIGNUP_COOKIE_MAX_AGE = 60 * 60
_CODE_COOKIE_MAX_AGE = 300
_PENDING_TTL_SECONDS = 300
_PASSWORD_CAPSULE_TTL_SECONDS = 60 * 60
_CALLBACK_PATH = "/api/auth/signup/callback"
_SUCCESS_LOCATION = "/app/"
_LOGIN_SUCCESS_LOCATION = "/accedi/?conferma=ok"
_ERROR_LOCATION = "/accedi/?errore=conferma"
_FLOW_COOKIES = (
    "wa_signup_verifier",
    "wa_signup_email",
    "wa_signup_code",
    "wa_signup_pending",
    "wa_signup_next",
    "wa_signup_flow",
    "wa_signup_capsule",
)
_PENDING_PREFIX = "auth:signup:pending:"
_PASSWORD_CAPSULE_PREFIX = "auth:signup:password:"
_COMPLETION_PREFIX = "auth:signup:complete:"
_CALLBACK_CLAIM_PREFIX = "auth:signup:callback-claim:"
_CALLBACK_CLAIM_TTL_SECONDS = 120
_COMPLETION_MARKER_TTL_SECONDS = 300
_CALLBACK_CLAIM_RELEASE_SCRIPT = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_pending_memory: dict[str, tuple[float, dict]] = {}
_password_capsule_memory: dict[str, tuple[float, str]] = {}
_callback_claim_memory: dict[str, tuple[float, str]] = {}
_completion_marker_memory: dict[str, tuple[float, str]] = {}
_LOGGER = logging.getLogger(__name__)


class _TransientIdentityCheckError(Exception):
    """Supabase identity lookup may be retried without consuming the proof."""


class RegisterBody(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    email: str
    nome_attivita: str
    next: str | None = None
    password: Any = None


def _client_ip(request: Request) -> str:
    from src.core.auth.trusted_network import get_client_ip

    return str(get_client_ip(request) or "unknown")


def _signup_throttle_key(ip: str) -> str:
    return f"auth:signup:{ip}"


async def _check_signup_throttle(ip: str) -> None:
    if await throttle.is_throttled(
        _signup_throttle_key(ip), _SIGNUP_MAX, _SIGNUP_WINDOW
    ):
        raise HTTPException(
            429,
            "Troppe registrazioni da questo indirizzo. Riprova più tardi.",
        )


def _flow_cookie_name(base_name: str) -> str:
    if bff.cookie_secure():
        return f"__Host-{base_name}"
    return base_name


def _set_flow_cookie(
    response: Response, base_name: str, value: str, max_age: int
) -> None:
    response.set_cookie(
        _flow_cookie_name(base_name),
        value,
        max_age=max_age,
        path="/",
        httponly=True,
        secure=bff.cookie_secure(),
        samesite="lax",
    )


def _set_signup_flow_cookies(
    response: Response,
    *,
    verifier: str,
    email_hash: str,
    next_path: str,
    flow_id: str,
    capsule_id: str,
) -> None:
    """Set a uniform browser-binding header shape for every 202 response."""
    _set_flow_cookie(response, "wa_signup_verifier", verifier, _SIGNUP_COOKIE_MAX_AGE)
    _set_flow_cookie(response, "wa_signup_email", email_hash, _SIGNUP_COOKIE_MAX_AGE)
    _set_flow_cookie(response, "wa_signup_next", next_path, _SIGNUP_COOKIE_MAX_AGE)
    # The stored capsule's server-side expiry remains authoritative and is at
    # most this long; a cookie that outlives it by a second is harmless.
    _set_flow_cookie(response, "wa_signup_flow", flow_id, _SIGNUP_COOKIE_MAX_AGE)
    _set_flow_cookie(response, "wa_signup_capsule", capsule_id, _SIGNUP_COOKIE_MAX_AGE)


def _clear_flow_cookies(response: Response) -> None:
    _clear_flow_cookie_names(response, _FLOW_COOKIES)


def _clear_flow_cookie_names(response: Response, base_names: tuple[str, ...]) -> None:
    for base_name in base_names:
        response.delete_cookie(
            _flow_cookie_name(base_name),
            path="/",
            secure=bff.cookie_secure(),
            httponly=True,
            samesite="lax",
        )


def _raw_cookie_value(request: Request, base_name: str) -> str | None:
    """Read one cookie only; reject duplicate Cookie headers and cookie names."""
    name = _flow_cookie_name(base_name)
    headers = request.headers.getlist("cookie")
    if len(headers) != 1:
        return None
    matches = [
        item.strip().partition("=")[2]
        for item in headers[0].split(";")
        if item.strip().partition("=")[0] == name
    ]
    if len(matches) != 1:
        return None
    return request.cookies.get(name)


def _has_cookie_name(request: Request, names: set[str]) -> bool:
    """Treat malformed or duplicated Cookie headers as an active session."""
    headers = request.headers.getlist("cookie")
    if len(headers) > 1:
        return True
    if not headers:
        return False
    found: set[str] = set()
    for item in headers[0].split(";"):
        name, separator, _value = item.strip().partition("=")
        if separator and name in names:
            if name in found:
                return True
            found.add(name)
    return bool(found)


def _has_session_cookie(request: Request) -> bool:
    return _has_cookie_name(
        request, {bff.access_cookie_name(), bff.refresh_cookie_name()}
    )


def _private_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _callback_redirect(location: str, *, clear_flow: bool = False) -> RedirectResponse:
    response = RedirectResponse(location, status_code=303)
    _private_headers(response)
    if clear_flow:
        _clear_flow_cookies(response)
    return response


def _email_fingerprint(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def _safe_internal_next(value: object) -> str:
    """Allow only unencoded, strict root-relative paths; otherwise use /app/."""
    if not isinstance(value, str) or not value.startswith("/"):
        return _SUCCESS_LOCATION
    if (
        value.startswith("//")
        or "\\" in value
        or "%" in value
        or "?" in value
        or "#" in value
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        return _SUCCESS_LOCATION
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.query or parts.fragment or parts.path != value:
        return _SUCCESS_LOCATION
    segments = parts.path[1:].split("/")
    if segments and segments[-1] == "":
        segments.pop()
    if any(
        not segment
        or segment in {".", ".."}
        or not _NEXT_SEGMENT_RE.fullmatch(segment)
        for segment in segments
    ):
        return _SUCCESS_LOCATION
    return parts.path or _SUCCESS_LOCATION


def _pending_fernet() -> Fernet:
    key = os.getenv("ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("pending retry encryption unavailable")
    return Fernet(key.encode("ascii"))


def _password_capsule_fernet() -> Fernet:
    """Derive a purpose-specific key so capsule ciphertext cannot cross uses."""
    key = os.getenv("ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("signup password capsule encryption unavailable")
    try:
        encoded_key = key.encode("ascii")
        Fernet(encoded_key)
        key_material = base64.b64decode(encoded_key, altchars=b"-_", validate=True)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("signup password capsule encryption unavailable") from exc
    derived_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"melpis/auth/signup-password-capsule/salt/v1",
        info=b"melpis/auth/signup-password-capsule/encryption/v1",
    ).derive(key_material)
    return Fernet(base64.urlsafe_b64encode(derived_key))


def _pending_redis_url() -> str | None:
    configured_url = os.getenv("REDIS_URL", "").strip()
    if configured_url:
        return configured_url
    app_env = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development"))
    if app_env.strip().lower() in {"development", "test", "staging"}:
        return None
    return "redis://valkey:6379/0"


def _prune_pending_memory(now: float | None = None) -> None:
    current_time = time.monotonic() if now is None else now
    for retry_id, (expires_at, _state) in list(_pending_memory.items()):
        if expires_at <= current_time:
            _pending_memory.pop(retry_id, None)


def _pending_now() -> float:
    return time.time()


def _password_capsule_expiry_remaining(expires_at: object) -> int:
    if (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or not math.isfinite(expires_at)
    ):
        raise ValueError("signup password capsule expiry invalid")
    remaining = expires_at - _pending_now()
    if remaining <= 0 or remaining > _PASSWORD_CAPSULE_TTL_SECONDS:
        raise ValueError("signup password capsule expired or invalid")
    ttl = int(remaining)
    if ttl <= 0:
        raise ValueError("signup password capsule has no remaining lifetime")
    return ttl


def _prune_password_capsule_memory(now: float | None = None) -> None:
    current_time = time.monotonic() if now is None else now
    for capsule_id, (expires_at, _encrypted) in list(_password_capsule_memory.items()):
        if expires_at <= current_time:
            _password_capsule_memory.pop(capsule_id, None)


async def _claim_signup_callback(flow_id: str) -> str | None:
    """Allow only one in-flight callback POST for a signup flow."""
    if not _PENDING_ID_RE.fullmatch(flow_id):
        return None
    token = secrets.token_urlsafe(32)
    redis_url = _pending_redis_url()
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            claimed = await client.set(
                _CALLBACK_CLAIM_PREFIX + flow_id,
                token,
                ex=_CALLBACK_CLAIM_TTL_SECONDS,
                nx=True,
            )
            return token if claimed else None
        finally:
            await client.aclose()

    now = time.monotonic()
    for claimed_flow, (expires_at, _claimed_token) in list(_callback_claim_memory.items()):
        if expires_at <= now:
            _callback_claim_memory.pop(claimed_flow, None)
    if flow_id in _callback_claim_memory:
        return None
    _callback_claim_memory[flow_id] = (now + _CALLBACK_CLAIM_TTL_SECONDS, token)
    return token


async def _release_signup_callback(flow_id: str, token: str) -> None:
    """Release only this request's claim; a timed-out successor stays protected."""
    redis_url = _pending_redis_url()
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await client.eval(
                _CALLBACK_CLAIM_RELEASE_SCRIPT,
                1,
                _CALLBACK_CLAIM_PREFIX + flow_id,
                token,
            )
        finally:
            await client.aclose()
        return

    entry = _callback_claim_memory.get(flow_id)
    if entry is not None and secrets.compare_digest(entry[1], token):
        _callback_claim_memory.pop(flow_id, None)


async def _save_password_capsule(
    password: str,
    email_hash: str,
    flow_id: str,
    *,
    expires_at: float | None = None,
) -> tuple[str, int, float]:
    """Store only Fernet ciphertext and return its opaque, browser-bound id."""
    absolute_expiry = (
        _pending_now() + _PASSWORD_CAPSULE_TTL_SECONDS
        if expires_at is None
        else expires_at
    )
    ttl = _password_capsule_expiry_remaining(absolute_expiry)
    capsule_id = secrets.token_urlsafe(32)
    payload = {
        "password": password,
        "email_hash": email_hash,
        "flow_id": flow_id,
        "expires_at": absolute_expiry,
    }
    encrypted = _password_capsule_fernet().encrypt(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    redis_url = _pending_redis_url()
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            saved = await client.set(
                _PASSWORD_CAPSULE_PREFIX + capsule_id,
                encrypted,
                ex=ttl,
                nx=True,
            )
            if not saved:
                raise RuntimeError("signup password capsule collision")
        finally:
            await client.aclose()
        return capsule_id, ttl, absolute_expiry

    now = time.monotonic()
    _prune_password_capsule_memory(now)
    _password_capsule_memory[capsule_id] = (now + ttl, encrypted)
    return capsule_id, ttl, absolute_expiry


async def _consume_password_capsule(
    capsule_id: str,
    expected_email_hash: str,
    expected_flow_id: str,
) -> dict | None:
    """Read a retained capsule and validate both flow bindings."""
    if not _PENDING_ID_RE.fullmatch(capsule_id):
        return None
    redis_url = _pending_redis_url()
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            encrypted = await client.get(_PASSWORD_CAPSULE_PREFIX + capsule_id)
        finally:
            await client.aclose()
        if not encrypted:
            return None
    else:
        now = time.monotonic()
        _prune_password_capsule_memory(now)
        entry = _password_capsule_memory.get(capsule_id)
        if entry is None or entry[0] <= now:
            return None
        encrypted = entry[1]

    try:
        plaintext = _password_capsule_fernet().decrypt(encrypted.encode("ascii"))
        state = json.loads(plaintext)
    except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    password = state.get("password")
    email_hash = state.get("email_hash")
    flow_id = state.get("flow_id")
    if (
        not isinstance(password, str)
        or not _valid_password(password)
        or not isinstance(email_hash, str)
        or not isinstance(flow_id, str)
        or not secrets.compare_digest(email_hash, expected_email_hash)
        or not secrets.compare_digest(flow_id, expected_flow_id)
    ):
        return None
    try:
        _password_capsule_expiry_remaining(state.get("expires_at"))
    except (TypeError, ValueError):
        return None
    return state


async def _discard_password_capsule(capsule_id: str | None) -> None:
    """Delete capsule ciphertext without decrypting it on terminal failure."""
    if not isinstance(capsule_id, str) or not _PENDING_ID_RE.fullmatch(capsule_id):
        return
    redis_url = _pending_redis_url()
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await client.getdel(_PASSWORD_CAPSULE_PREFIX + capsule_id)
        finally:
            await client.aclose()
        return
    _password_capsule_memory.pop(capsule_id, None)


def _pending_ttl_remaining(state: dict) -> int:
    expires_at = state.get("expires_at")
    if (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or not math.isfinite(expires_at)
    ):
        raise ValueError("pending retry expiry invalid")
    remaining = expires_at - _pending_now()
    if remaining <= 0 or remaining > _PENDING_TTL_SECONDS:
        raise ValueError("pending retry expired or invalid")
    ttl = int(remaining)
    if ttl <= 0:
        raise ValueError("pending retry has no remaining lifetime")
    return ttl


def _pending_id_for_flow(flow_id: str) -> str:
    if not _PENDING_ID_RE.fullmatch(flow_id):
        raise ValueError("signup flow id invalid")
    digest = hashlib.sha256(
        b"melpis/auth/signup-pending-id/v1:" + flow_id.encode("ascii")
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def _save_pending_session(state: dict) -> tuple[str, int]:
    """Save session credentials privately under a stable id derived from flow."""
    ttl = _pending_ttl_remaining(state)
    flow_id = state.get("flow_id")
    if not isinstance(flow_id, str):
        raise TypeError("signup flow id unavailable")
    retry_id = _pending_id_for_flow(flow_id)
    encrypted = _pending_fernet().encrypt(
        json.dumps(state, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    redis_url = _pending_redis_url()
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            saved = await client.set(
                _PENDING_PREFIX + retry_id,
                encrypted,
                ex=ttl,
                nx=True,
            )
            if not saved:
                existing = await client.get(_PENDING_PREFIX + retry_id)
                if not existing:
                    raise RuntimeError("pending retry write outcome unavailable")
                existing_state = _decrypt_pending_state(existing)
                if existing_state != state:
                    raise RuntimeError("pending retry state conflict")
        finally:
            await client.aclose()
        return retry_id, ttl

    # In local development without Redis, process memory matches the existing
    # token-store fallback. No token is put into the browser's retry cookie.
    now = time.monotonic()
    _prune_pending_memory(now)
    existing = _pending_memory.get(retry_id)
    if existing is not None:
        if existing[1] != state:
            raise RuntimeError("pending retry state conflict")
        return retry_id, ttl
    _pending_memory[retry_id] = (now + ttl, state)
    return retry_id, ttl


def _decrypt_pending_state(encrypted: str) -> dict | None:
    try:
        raw = _pending_fernet().decrypt(encrypted.encode("ascii"))
        state = json.loads(raw)
    except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError):
        return None
    return state if isinstance(state, dict) else None


async def _read_pending_session(retry_id: str) -> dict | None:
    """Read retained retry state without deleting it before successful setup."""
    redis_url = _pending_redis_url()
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            encrypted = await client.get(_PENDING_PREFIX + retry_id)
        finally:
            await client.aclose()
        if not encrypted:
            return None
        state = _decrypt_pending_state(encrypted)
        if state is None:
            return None
        try:
            _pending_ttl_remaining(state)
        except (TypeError, ValueError):
            return None
        return state

    _prune_pending_memory()
    entry = _pending_memory.get(retry_id)
    if entry is None or entry[0] <= time.monotonic():
        return None
    try:
        _pending_ttl_remaining(entry[1])
    except (TypeError, ValueError):
        return None
    return entry[1]


async def _delete_pending_session(retry_id: str) -> None:
    redis_url = _pending_redis_url()
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await client.delete(_PENDING_PREFIX + retry_id)
        finally:
            await client.aclose()
        return
    _pending_memory.pop(retry_id, None)


def _prune_completion_marker_memory(now: float | None = None) -> None:
    current_time = time.monotonic() if now is None else now
    for marker_id, (expires_at, _encrypted) in list(_completion_marker_memory.items()):
        if expires_at <= current_time:
            _completion_marker_memory.pop(marker_id, None)


async def _has_completion_marker(pending_id: str) -> bool:
    redis_url = _pending_redis_url()
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            encrypted = await client.get(_COMPLETION_PREFIX + pending_id)
        finally:
            await client.aclose()
    else:
        now = time.monotonic()
        _prune_completion_marker_memory(now)
        entry = _completion_marker_memory.get(pending_id)
        encrypted = entry[1] if entry is not None and entry[0] > now else None
    # Markers only deny a second session issuance; the deterministic key is
    # derived from the browser-bound flow id, so marker contents grant nothing.
    return bool(encrypted)


async def _write_completion_marker(pending_state: dict) -> bool:
    """Durably mark password setup before deleting its retained retry state."""
    flow_id = pending_state.get("flow_id")
    email_hash = pending_state.get("email_hash")
    user_id = pending_state.get("user_id")
    if (
        not isinstance(flow_id, str)
        or not _PENDING_ID_RE.fullmatch(flow_id)
        or not isinstance(email_hash, str)
        or not _EMAIL_HASH_RE.fullmatch(email_hash)
        or not isinstance(user_id, str)
    ):
        raise ValueError("signup completion marker state invalid")
    pending_id = _pending_id_for_flow(flow_id)
    payload = {
        "flow_id": flow_id,
        "email_hash": email_hash,
        "user_id": user_id,
        "completed_at": _pending_now(),
    }
    encrypted = _pending_fernet().encrypt(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    redis_url = _pending_redis_url()
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            return bool(
                await client.set(
                    _COMPLETION_PREFIX + pending_id,
                    encrypted,
                    ex=_COMPLETION_MARKER_TTL_SECONDS,
                    nx=True,
                )
            )
        finally:
            await client.aclose()

    now = time.monotonic()
    _prune_completion_marker_memory(now)
    if pending_id in _completion_marker_memory:
        return False
    _completion_marker_memory[pending_id] = (
        now + _COMPLETION_MARKER_TTL_SECONDS,
        encrypted,
    )
    return True


async def _cleanup_completed_signup(
    pending_id: str, capsule_id: str, trace_id: str
) -> None:
    for cleanup_name, cleanup in (
        ("pending", _delete_pending_session(pending_id)),
        ("password_capsule", _discard_password_capsule(capsule_id)),
    ):
        try:
            await cleanup
        except Exception as exc:  # noqa: BLE001 — marker already prevents token replay
            _LOGGER.warning(
                "signup_%s_cleanup_deferred trace_id=%s error_type=%s",
                cleanup_name,
                trace_id,
                type(exc).__name__,
            )


async def _pending_retry_redirect(pending_state: dict, trace_id: str) -> Response:
    try:
        flow_id = pending_state.get("flow_id")
        capsule_id = pending_state.get("password_capsule_id")
        if not isinstance(flow_id, str) or not _PENDING_ID_RE.fullmatch(flow_id):
            raise ValueError("signup flow id invalid")
        if not isinstance(capsule_id, str) or not _PENDING_ID_RE.fullmatch(capsule_id):
            raise ValueError("signup password capsule reference invalid")
        retry_id = _pending_id_for_flow(flow_id)
        remaining_ttl = _pending_ttl_remaining(pending_state)
    except Exception as exc:  # noqa: BLE001 — never expose or downgrade failed retry storage
        _LOGGER.warning(
            "signup_retry_state_unavailable trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)
    try:
        await _save_pending_session(pending_state)
    except Exception as exc:  # noqa: BLE001 — never advertise a retry cookie without durable state
        _LOGGER.warning(
            "signup_retry_state_persistence_deferred trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return _callback_redirect(_CALLBACK_PATH)
    response = _callback_redirect(_CALLBACK_PATH)
    _clear_flow_cookie_names(response, ("wa_signup_code", "wa_signup_verifier"))
    _set_flow_cookie(response, "wa_signup_pending", retry_id, remaining_ttl)
    return response


def _pending_state(
    session: dict,
    user: dict,
    auth_user_id: str,
    email: str,
    email_hash: str,
    next_path: str,
    flow_id: str,
    password_capsule_id: str,
) -> dict:
    metadata = user.get("user_metadata")
    business_name = _organization_name(
        {"user_metadata": metadata if isinstance(metadata, dict) else {}}, email
    )
    return {
        "access_token": session["access_token"],
        "refresh_token": session["refresh_token"],
        "user_id": auth_user_id,
        "email": email,
        "email_hash": email_hash,
        "flow_id": flow_id,
        "password_capsule_id": password_capsule_id,
        "business_name": business_name,
        "next": next_path,
        "expires_at": _pending_now() + _PENDING_TTL_SECONDS,
    }


def _validate_pending_state(state: dict, email_hash: str) -> tuple[dict, str, str] | None:
    user_id = state.get("user_id")
    email = state.get("email")
    flow_id = state.get("flow_id")
    capsule_id = state.get("password_capsule_id")
    if (
        not isinstance(user_id, str)
        or not isinstance(email, str)
        or not isinstance(state.get("email_hash"), str)
        or not secrets.compare_digest(state["email_hash"], email_hash)
        or not isinstance(flow_id, str)
        or not _PENDING_ID_RE.fullmatch(flow_id)
        or not isinstance(capsule_id, str)
        or not _PENDING_ID_RE.fullmatch(capsule_id)
    ):
        return None
    user = {
        "id": user_id,
        "email": email,
        "email_confirmed_at": "confirmed",
        "user_metadata": {"nome_attivita": state.get("business_name")},
    }
    session = {
        "access_token": state.get("access_token"),
        "refresh_token": state.get("refresh_token"),
        "user": user,
    }
    validated = _valid_session(session)
    if validated is None:
        return None
    _validated_user, validated_user_id, validated_email = validated
    if (
        validated_user_id != user_id
        or not secrets.compare_digest(
            _email_fingerprint(validated_email), email_hash
        )
    ):
        return None
    return session, validated_user_id, validated_email


def _valid_flow(request: Request) -> tuple[str, str, str, str, str] | None:
    code = _raw_cookie_value(request, "wa_signup_code")
    verifier = _raw_cookie_value(request, "wa_signup_verifier")
    email_hash = _raw_cookie_value(request, "wa_signup_email")
    flow_id = _raw_cookie_value(request, "wa_signup_flow")
    capsule_id = _raw_cookie_value(request, "wa_signup_capsule")
    if (
        not isinstance(code, str)
        or not _CODE_RE.fullmatch(code)
        or not isinstance(verifier, str)
        or not _VERIFIER_RE.fullmatch(verifier)
        or not isinstance(email_hash, str)
        or not _EMAIL_HASH_RE.fullmatch(email_hash)
        or not isinstance(flow_id, str)
        or not _PENDING_ID_RE.fullmatch(flow_id)
        or not isinstance(capsule_id, str)
        or not _PENDING_ID_RE.fullmatch(capsule_id)
    ):
        return None
    return code, verifier, email_hash, flow_id, capsule_id


def _valid_pending_id(request: Request) -> str | None:
    pending_id = _raw_cookie_value(request, "wa_signup_pending")
    if isinstance(pending_id, str) and _PENDING_ID_RE.fullmatch(pending_id):
        return pending_id
    return None


def _has_duplicate_flow_cookie(request: Request) -> bool:
    headers = request.headers.getlist("cookie")
    if len(headers) > 1:
        return True
    if not headers:
        return False
    names = {_flow_cookie_name(name) for name in _FLOW_COOKIES}
    seen: set[str] = set()
    for item in headers[0].split(";"):
        name, separator, _value = item.strip().partition("=")
        if separator and name in names:
            if name in seen:
                return True
            seen.add(name)
    return False


def _has_flow_cookie(request: Request, base_name: str) -> bool:
    return _has_cookie_name(request, {_flow_cookie_name(base_name)})


def _same_public_origin(request: Request) -> bool:
    origins = request.headers.getlist("origin")
    if len(origins) != 1:
        return False
    try:
        origin = urlsplit(origins[0])
        target = urlsplit(bff.public_app_url())
        origin_port = origin.port or (443 if origin.scheme == "https" else 80)
        target_port = target.port or (443 if target.scheme == "https" else 80)
    except (HTTPException, ValueError):
        return False
    return (
        origin.scheme in {"http", "https"}
        and origin.username is None
        and origin.password is None
        and origin.path in {"", "/"}
        and not origin.query
        and not origin.fragment
        and target.scheme in {"http", "https"}
        and target.username is None
        and target.password is None
        and target.path in {"", "/"}
        and not target.query
        and not target.fragment
        and origin.scheme == target.scheme
        and (origin.hostname or "").lower() == (target.hostname or "").lower()
        and origin_port == target_port
    )


def _completion_form() -> HTMLResponse:
    response = HTMLResponse(
        "<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Completa la registrazione</title></head><body><main>"
        "<h1>Conferma il tuo indirizzo email</h1>"
        "<p>Per completare la registrazione e accedere, conferma qui dopo aver aperto il link ricevuto via email.</p>"
        f"<form method=\"post\" action=\"{_CALLBACK_PATH}\">"
        "<button type=\"submit\" name=\"complete\" value=\"1\">"
        "Completa la registrazione</button></form>"
        "</main></body></html>",
        status_code=200,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )
    return response


def _valid_session(session: object) -> tuple[dict, str, str] | None:
    if not isinstance(session, dict):
        return None
    for field in ("access_token", "refresh_token"):
        value = session.get(field)
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 8192
            or value != value.strip()
            or any(ord(char) < 0x21 or ord(char) > 0x7E for char in value)
        ):
            return None
    user = session.get("user")
    if not isinstance(user, dict):
        return None
    user_id = user.get("id")
    email = user.get("email")
    if not isinstance(user_id, str) or not isinstance(email, str):
        return None
    try:
        normalized_user_id = str(uuid.UUID(user_id))
    except (ValueError, AttributeError):
        return None
    email_confirmed_at = user.get("email_confirmed_at") or user.get("confirmed_at")
    if not isinstance(email_confirmed_at, str) or not email_confirmed_at:
        return None
    normalized_email = email.strip().lower()
    if not _EMAIL_RE.fullmatch(normalized_email):
        return None
    return user, normalized_user_id, normalized_email


def _organization_name(user: dict, email: str) -> str:
    metadata = user.get("user_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    name = metadata.get("nome_attivita")
    if isinstance(name, str):
        name = name.strip()
        if name and len(name) <= 120 and not any(ord(char) < 32 for char in name):
            return name
    fallback = email.split("@", 1)[0].replace(".", " ").strip()
    return fallback[:120] or "La mia attività"


async def supabase_signup(
    email: str,
    temporary_password: str,
    redirect_to: str,
    code_challenge: str,
    nome_attivita: str,
) -> int:
    """Create a pending user with an unreturned password and S256 PKCE binding."""
    client = await bff._client()
    response = await client.post(
        f"{bff._supabase_url()}/auth/v1/signup",
        params={"redirect_to": redirect_to},
        json={
            "email": email,
            "password": temporary_password,
            "data": {"nome_attivita": nome_attivita},
            "code_challenge": code_challenge,
            "code_challenge_method": "s256",
        },
        headers={"apikey": bff._anon_key(), "Content-Type": "application/json"},
    )
    return response.status_code


async def _set_supabase_password(access_token: str, password: str) -> None:
    client = await bff._client()
    response = await client.put(
        f"{bff._supabase_url()}/auth/v1/user",
        json={"password": password},
        headers={
            "apikey": bff._anon_key(),
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError("Supabase password update failed")


async def _get_supabase_user(access_token: str) -> dict:
    """Revalidate the confirmation identity before resuming a stored session."""
    client = await bff._client()
    try:
        response = await client.get(
            f"{bff._supabase_url()}/auth/v1/user",
            headers={
                "apikey": bff._anon_key(),
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise _TransientIdentityCheckError(type(exc).__name__) from None
    if response.status_code in {408, 429} or response.status_code >= 500:
        raise _TransientIdentityCheckError(f"HTTP {response.status_code}")
    if not 200 <= response.status_code < 300:
        raise RuntimeError("Supabase identity revalidation failed")
    user = response.json()
    if not isinstance(user, dict):
        raise TypeError("Supabase identity response invalid")
    return user


def _matches_confirmed_identity(
    user: dict, expected_user_id: str, expected_email: str, expected_email_hash: str
) -> bool:
    user_id = user.get("id")
    email = user.get("email")
    confirmed_at = user.get("email_confirmed_at") or user.get("confirmed_at")
    if not isinstance(user_id, str) or not isinstance(email, str):
        return False
    try:
        user_id = str(uuid.UUID(user_id))
    except ValueError:
        return False
    normalized_email = email.strip().lower()
    return (
        user_id == expected_user_id
        and normalized_email == expected_email
        and isinstance(confirmed_at, str)
        and bool(confirmed_at)
        and secrets.compare_digest(
            _email_fingerprint(normalized_email), expected_email_hash
        )
    )


@router.post("/register", status_code=202)
async def register(body: RegisterBody, request: Request, response: Response):
    if not _same_public_origin(request):
        raise HTTPException(403, "Origine richiesta non valida")

    ip = _client_ip(request)
    await _check_signup_throttle(ip)
    await throttle.record_event(_signup_throttle_key(ip), _SIGNUP_WINDOW)

    email = body.email.strip().lower()
    nome_attivita = body.nome_attivita.strip()
    password = body.password
    next_path = _safe_internal_next(body.next)
    if not _EMAIL_RE.fullmatch(email):
        raise HTTPException(422, "Email non valida")
    if (
        not nome_attivita
        or len(nome_attivita) > 120
        or any(ord(char) < 32 for char in nome_attivita)
    ):
        raise HTTPException(422, "Inserisci il nome della tua attività")
    if not isinstance(password, str) or not _valid_password(password):
        raise HTTPException(422, "Password non valida")

    # Keep the public response generic while refusing to create Auth users
    # outside the operator-maintained beta roster. Emit unusable browser
    # binding cookies on this branch too, so response headers do not disclose
    # roster membership. No password capsule or Supabase user is created.
    if not beta_email_is_allowed(email):
        verifier = secrets.token_urlsafe(48)
        flow_id = secrets.token_urlsafe(32)
        email_hash = _email_fingerprint(email)
        capsule_id = secrets.token_urlsafe(32)
        _set_signup_flow_cookies(
            response,
            verifier=verifier,
            email_hash=email_hash,
            next_path=next_path,
            flow_id=flow_id,
            capsule_id=capsule_id,
        )
        response.status_code = 202
        return {
            "ok": True,
            "message": "Se l'indirizzo può essere registrato, riceverai un link per continuare.",
        }

    verifier = secrets.token_urlsafe(48)
    flow_id = secrets.token_urlsafe(32)
    email_hash = _email_fingerprint(email)
    try:
        capsule_id, _capsule_ttl, _capsule_expires_at = await _save_password_capsule(
            password, email_hash, flow_id
        )
    except Exception as exc:  # noqa: BLE001 — fail closed without calling Supabase
        _LOGGER.warning(
            "signup_password_capsule_unavailable trace_id=%s error_type=%s",
            getattr(request.state, "trace_id", "unavailable"),
            type(exc).__name__,
        )
        _set_signup_flow_cookies(
            response,
            verifier=secrets.token_urlsafe(48),
            email_hash=email_hash,
            next_path=next_path,
            flow_id=secrets.token_urlsafe(32),
            capsule_id=secrets.token_urlsafe(32),
        )
        response.status_code = 202
        return {
            "ok": True,
            "message": "Se l'indirizzo può essere registrato, riceverai un link per continuare.",
        }
    # Supabase may enforce a non-alphanumeric character; append one explicitly
    # so the private placeholder cannot fail that policy probabilistically.
    temporary_password = f"{secrets.token_urlsafe(64)}!"
    challenge = bff.pkce_challenge(verifier)
    redirect_to = f"{bff.public_app_url()}{_CALLBACK_PATH}"

    # Set a stable cookie shape regardless of roster/Auth outcome. The stored
    # capsule's server-side expiry remains authoritative.
    _set_signup_flow_cookies(
        response,
        verifier=verifier,
        email_hash=email_hash,
        next_path=next_path,
        flow_id=flow_id,
        capsule_id=capsule_id,
    )

    try:
        status_code = await supabase_signup(
            email, temporary_password, redirect_to, challenge, nome_attivita
        )
        if not 200 <= status_code < 300:
            _LOGGER.warning(
                "signup_upstream_rejected trace_id=%s status=%s error_class=%s",
                getattr(request.state, "trace_id", "unavailable"),
                status_code,
                (
                    "client_error"
                    if 400 <= status_code < 500
                    else "server_error"
                    if 500 <= status_code < 600
                    else "unexpected_status"
                ),
            )
    except Exception as exc:  # noqa: BLE001 — keep signup errors indistinguishable and never log input values
        trace_id = getattr(request.state, "trace_id", "unavailable")
        _LOGGER.warning(
            "signup_upstream_failed trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )

    response.status_code = 202
    return {
        "ok": True,
        "message": "Se l'indirizzo può essere registrato, riceverai un link per continuare.",
    }


@router.get("/signup/callback")
async def signup_callback_get(request: Request):
    """Stage the PKCE code on GET; require a same-origin POST to consume it."""
    if _has_session_cookie(request) or _has_duplicate_flow_cookie(request):
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

    query_items = request.query_params.multi_items()
    if query_items:
        if (
            len(query_items) != 1
            or query_items[0][0] != "code"
            or not _CODE_RE.fullmatch(query_items[0][1])
        ):
            return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

        verifier = _raw_cookie_value(request, "wa_signup_verifier")
        email_hash = _raw_cookie_value(request, "wa_signup_email")
        flow_id = _raw_cookie_value(request, "wa_signup_flow")
        capsule_id = _raw_cookie_value(request, "wa_signup_capsule")
        if (
            _has_flow_cookie(request, "wa_signup_pending")
            or
            not isinstance(verifier, str)
            or not _VERIFIER_RE.fullmatch(verifier)
            or not isinstance(email_hash, str)
            or not _EMAIL_HASH_RE.fullmatch(email_hash)
            or not isinstance(flow_id, str)
            or not _PENDING_ID_RE.fullmatch(flow_id)
            or not isinstance(capsule_id, str)
            or not _PENDING_ID_RE.fullmatch(capsule_id)
        ):
            return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

        response = _callback_redirect(_CALLBACK_PATH)
        _set_flow_cookie(response, "wa_signup_code", query_items[0][1], _CODE_COOKIE_MAX_AGE)
        return response

    flow = _valid_flow(request)
    pending_id = _valid_pending_id(request)
    email_hash = _raw_cookie_value(request, "wa_signup_email")
    flow_id = _raw_cookie_value(request, "wa_signup_flow")
    capsule_id = _raw_cookie_value(request, "wa_signup_capsule")
    pending_cookie_present = _has_flow_cookie(request, "wa_signup_pending")
    pending_has_no_stale_pkce = not any(
        _has_flow_cookie(request, name)
        for name in ("wa_signup_code", "wa_signup_verifier")
    )
    pending_flow_valid = (
        pending_id is not None
        and flow is None
        and pending_has_no_stale_pkce
        and isinstance(email_hash, str)
        and _EMAIL_HASH_RE.fullmatch(email_hash) is not None
        and isinstance(flow_id, str)
        and _PENDING_ID_RE.fullmatch(flow_id) is not None
        and isinstance(capsule_id, str)
        and _PENDING_ID_RE.fullmatch(capsule_id) is not None
    )
    if (
        (flow is None and not pending_flow_valid)
        or (flow is not None and pending_cookie_present)
        or (pending_cookie_present and pending_id is None)
    ):
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)
    return _completion_form()


async def _completion_form_valid(request: Request) -> bool | None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return None
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 256:
                return None
        except ValueError:
            return None
    body = await request.body()
    if len(body) > 256:
        return None
    try:
        fields = parse_qs(
            body.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
        )
    except (UnicodeDecodeError, ValueError):
        return None
    return set(fields) == {"complete"} and fields["complete"] == ["1"]


def _valid_password(password: str) -> bool:
    return (
        PASSWORD_MIN <= len(password) <= 256
        and bool(_SPECIAL_RE.search(password))
        and bool(_UPPER_RE.search(password))
        and bool(_NUMBER_RE.search(password))
    )


@router.post("/signup/callback")
async def signup_callback_post(request: Request):
    if (
        _has_session_cookie(request)
        or _has_duplicate_flow_cookie(request)
        or not _same_public_origin(request)
        or request.query_params.multi_items()
    ):
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

    flow_id = _raw_cookie_value(request, "wa_signup_flow")
    if not isinstance(flow_id, str) or not _PENDING_ID_RE.fullmatch(flow_id):
        return await _signup_callback_post_claimed(request)

    claim_token = await _claim_signup_callback(flow_id)
    if claim_token is None:
        # Leave all flow cookies and encrypted capsule untouched while another
        # request owns the PKCE exchange/password update.
        return _callback_redirect(_CALLBACK_PATH)

    try:
        return await _signup_callback_post_claimed(request)
    finally:
        try:
            await _release_signup_callback(flow_id, claim_token)
        except Exception as exc:  # noqa: BLE001 — the bounded Redis lease expires if release fails
            _LOGGER.warning(
                "signup_callback_claim_release_deferred trace_id=%s error_type=%s",
                getattr(request.state, "trace_id", "unavailable"),
                type(exc).__name__,
            )


async def _signup_callback_post_claimed(request: Request):
    """Complete confirmed signup while retaining retry state until durable finish."""
    if (
        _has_session_cookie(request)
        or _has_duplicate_flow_cookie(request)
        or not _same_public_origin(request)
        or request.query_params.multi_items()
    ):
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

    flow = _valid_flow(request)
    pending_cookie_id = _valid_pending_id(request)
    pending_cookie_present = _has_flow_cookie(request, "wa_signup_pending")
    email_hash = _raw_cookie_value(request, "wa_signup_email")
    flow_id = _raw_cookie_value(request, "wa_signup_flow")
    capsule_id = _raw_cookie_value(request, "wa_signup_capsule")
    form_valid = await _completion_form_valid(request)
    if (
        not form_valid
        or not isinstance(email_hash, str)
        or not _EMAIL_HASH_RE.fullmatch(email_hash)
        or not isinstance(flow_id, str)
        or not _PENDING_ID_RE.fullmatch(flow_id)
        or not isinstance(capsule_id, str)
        or not _PENDING_ID_RE.fullmatch(capsule_id)
        or (pending_cookie_present and pending_cookie_id is None)
    ):
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

    trace_id = getattr(request.state, "trace_id", "unavailable")
    pending_id = _pending_id_for_flow(flow_id)
    if pending_cookie_id is not None and not secrets.compare_digest(
        pending_cookie_id, pending_id
    ):
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

    try:
        completed_marker = await _has_completion_marker(pending_id)
    except Exception as exc:  # noqa: BLE001 — preserve state if the marker store is unavailable
        _LOGGER.warning(
            "signup_completion_marker_read_deferred trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return _callback_redirect(_CALLBACK_PATH)
    if completed_marker:
        await _cleanup_completed_signup(pending_id, capsule_id, trace_id)
        return _callback_redirect(_LOGIN_SUCCESS_LOCATION, clear_flow=True)

    try:
        pending_state = await _read_pending_session(pending_id)
    except Exception as exc:  # noqa: BLE001 — retained data remains available for retry
        _LOGGER.warning(
            "signup_retry_state_read_deferred trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return _callback_redirect(_CALLBACK_PATH)

    if pending_state is None and pending_cookie_present:
        await _cleanup_completed_signup(pending_id, capsule_id, trace_id)
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

    try:
        if pending_state is not None:
            validated_pending = _validate_pending_state(pending_state, email_hash)
            if validated_pending is None:
                raise ValueError("pending signup identity mismatch")
            session, auth_user_id, email = validated_pending
            if (
                not secrets.compare_digest(pending_state.get("flow_id", ""), flow_id)
                or not secrets.compare_digest(
                    pending_state.get("password_capsule_id", ""), capsule_id
                )
            ):
                raise ValueError("pending signup capsule binding mismatch")
            try:
                remote_user = await _get_supabase_user(session["access_token"])
            except _TransientIdentityCheckError as exc:
                _LOGGER.warning(
                    "signup_identity_revalidation_deferred trace_id=%s error_type=%s",
                    trace_id,
                    type(exc).__name__,
                )
                return await _pending_retry_redirect(pending_state, trace_id)
            if not _matches_confirmed_identity(
                remote_user, auth_user_id, email, email_hash
            ):
                raise ValueError("pending signup identity revalidation failed")
            user = session["user"]
            pending_next = _safe_internal_next(pending_state.get("next"))
        else:
            if flow is None:
                raise ValueError("signup proof unavailable")
            code, verifier, expected_email_hash, expected_flow_id, expected_capsule_id = flow
            if (
                not secrets.compare_digest(expected_email_hash, email_hash)
                or not secrets.compare_digest(expected_flow_id, flow_id)
                or not secrets.compare_digest(expected_capsule_id, capsule_id)
            ):
                raise ValueError("signup proof binding mismatch")
            try:
                session = await bff.exchange_pkce(code, verifier)
            except HTTPException as exc:
                if exc.status_code in {400, 401, 403}:
                    _LOGGER.warning(
                        "signup_pkce_exchange_rejected trace_id=%s status=%s",
                        trace_id,
                        exc.status_code,
                    )
                    return _callback_redirect(_ERROR_LOCATION, clear_flow=True)
                _LOGGER.warning(
                    "signup_pkce_exchange_unavailable trace_id=%s error_type=%s",
                    trace_id,
                    type(exc).__name__,
                )
                return _callback_redirect(_CALLBACK_PATH)
            except Exception as exc:  # noqa: BLE001 — retry can discover a saved state by flow id
                _LOGGER.warning(
                    "signup_pkce_exchange_unavailable trace_id=%s error_type=%s",
                    trace_id,
                    type(exc).__name__,
                )
                return _callback_redirect(_CALLBACK_PATH)
            validated = _valid_session(session)
            if validated is None:
                raise ValueError("invalid confirmation session")
            user, auth_user_id, email = validated
            if not secrets.compare_digest(_email_fingerprint(email), expected_email_hash):
                raise ValueError("confirmation identity mismatch")
            pending_next = _safe_internal_next(
                _raw_cookie_value(request, "wa_signup_next")
            )
            pending_state = _pending_state(
                session,
                user,
                auth_user_id,
                email,
                email_hash,
                pending_next,
                flow_id,
                capsule_id,
            )
            try:
                await _save_pending_session(pending_state)
            except Exception as exc:  # noqa: BLE001 — the deterministic key recovers applied writes
                _LOGGER.warning(
                    "signup_retry_state_write_uncertain trace_id=%s error_type=%s",
                    trace_id,
                    type(exc).__name__,
                )
                return await _pending_retry_redirect(pending_state, trace_id)
    except Exception as exc:  # noqa: BLE001 — collapse PKCE and identity errors
        await _cleanup_completed_signup(pending_id, capsule_id, trace_id)
        _LOGGER.warning(
            "signup_confirmation_failed trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

    if not beta_email_is_allowed(email):
        await _cleanup_completed_signup(pending_id, capsule_id, trace_id)
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

    try:
        capsule = await _consume_password_capsule(capsule_id, email_hash, flow_id)
    except Exception as exc:  # noqa: BLE001 — keep retained state across storage errors
        _LOGGER.warning(
            "signup_password_capsule_read_deferred trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return await _pending_retry_redirect(pending_state, trace_id)
    if capsule is None:
        await _cleanup_completed_signup(pending_id, capsule_id, trace_id)
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

    if await get_repo(request).get_auth_access_allowed(auth_user_id) is not True:
        await _cleanup_completed_signup(pending_id, capsule_id, trace_id)
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)

    try:
        await _set_supabase_password(session["access_token"], capsule["password"])
    except Exception as exc:  # noqa: BLE001 — Supabase may have applied the idempotent PUT
        _LOGGER.warning(
            "signup_password_setup_deferred trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return await _pending_retry_redirect(pending_state, trace_id)

    # Provision before committing completion so a transient failure retains the
    # encrypted credentials and business name for an idempotent retry.
    try:
        await get_repo(request).get_or_create_organization_with_owner(
            auth_user_id, _organization_name(user, email), TRIAL_DAYS
        )
    except PermissionError:
        # Durable denial is not a transient provisioning failure/retry loop.
        await _cleanup_completed_signup(pending_id, capsule_id, trace_id)
        return _callback_redirect(_ERROR_LOCATION, clear_flow=True)
    except Exception as exc:  # noqa: BLE001 — provisioning retries use retained signup state
        _LOGGER.warning(
            "signup_provisioning_deferred trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return await _pending_retry_redirect(pending_state, trace_id)

    try:
        marker_written = await _write_completion_marker(pending_state)
    except Exception as exc:  # noqa: BLE001 — do not issue cookies without durable completion
        _LOGGER.warning(
            "signup_completion_marker_write_deferred trace_id=%s error_type=%s",
            trace_id,
            type(exc).__name__,
        )
        return await _pending_retry_redirect(pending_state, trace_id)
    if not marker_written:
        await _cleanup_completed_signup(pending_id, capsule_id, trace_id)
        return _callback_redirect(_LOGIN_SUCCESS_LOCATION, clear_flow=True)

    await _cleanup_completed_signup(pending_id, capsule_id, trace_id)

    response = _callback_redirect(pending_next, clear_flow=True)
    from src.core.auth.routes import _set_session_cookies

    _set_session_cookies(response, session)
    issue_csrf_token(response)
    return response
