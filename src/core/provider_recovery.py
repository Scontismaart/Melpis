"""Authenticated, read-only provider reconciliation for A03 V1.

The request path never accepts user-supplied metrics. OpenRouter evidence is
looked up server-side using the configured key and a generation ID captured
from the provider response header. The response body is bounded and redacted
from errors/logs.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from src.core.provider_accounting import RawProviderUsage
from src.core.openrouter_recovery_models import recovery_model_matches
from src.core.provider_evidence import safe_provider_generation_id, safe_provider_request_id

_ISSUER = object()
_MAX_BODY = 32 * 1024
_GENERATION_ENDPOINT = "https://openrouter.ai/api/v1/generation"


class ProviderReconciliationUnavailable(Exception):
    """Evidence could not safely resolve a dispatched attempt."""


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedOpenRouterGeneration:
    attempt_id: uuid.UUID
    generation_id: str
    request_id: str | None
    actual_model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    provider_cost_usd: str
    created_at: datetime
    finish_reason: str
    _issuer: object = field(default=None, repr=False, compare=False)

    def __repr__(self):
        return "VerifiedOpenRouterGeneration(<verified>)"


def _safe_decimal(value) -> Decimal:
    if type(value) not in {int, Decimal} or isinstance(value, bool):
        raise ProviderReconciliationUnavailable
    try:
        result = Decimal(value) if type(value) is int else value
    except (InvalidOperation, TypeError, ValueError):
        raise ProviderReconciliationUnavailable from None
    parts = result.as_tuple()
    if (not result.is_finite() or result < 0 or len(parts.digits) > 128
            or abs(parts.exponent) > 128 or result.adjusted() > 12):
        raise ProviderReconciliationUnavailable
    return result


def verify_openrouter_generation(
    payload: bytes | dict,
    *,
    generation_id: str,
    attempt_id: uuid.UUID,
    requested_model: str,
    attempt_created_at: datetime,
    expected_request_id: str | None,
) -> VerifiedOpenRouterGeneration:
    """Verify account-authenticated OpenRouter metadata against one attempt."""
    if (not safe_provider_generation_id(generation_id)
            or type(attempt_id) is not uuid.UUID or not attempt_id.int
            or type(requested_model) is not str or not requested_model
            or not isinstance(attempt_created_at, datetime)
            or attempt_created_at.tzinfo is None
            or (expected_request_id is not None
                and not safe_provider_request_id(expected_request_id))):
        raise ProviderReconciliationUnavailable
    if type(payload) is bytes:
        if not payload or len(payload) > _MAX_BODY:
            raise ProviderReconciliationUnavailable
        try:
            payload = json.loads(payload, parse_float=Decimal)
        except (ValueError, TypeError, RecursionError):
            raise ProviderReconciliationUnavailable from None
    if type(payload) is not dict or type(payload.get("data")) is not dict:
        raise ProviderReconciliationUnavailable
    data = payload["data"]
    provider_cost_usd = _safe_decimal(data.get("total_cost"))
    returned_id = data.get("id")
    if (returned_id != generation_id or data.get("external_user") != str(attempt_id)
            or type(data.get("model")) is not str
            or not recovery_model_matches(requested_model, data.get("model"), provider_cost_usd)):
        raise ProviderReconciliationUnavailable
    request_id = data.get("request_id")
    if request_id is not None and not safe_provider_request_id(request_id):
        raise ProviderReconciliationUnavailable
    if expected_request_id is not None and request_id != expected_request_id:
        raise ProviderReconciliationUnavailable
    try:
        created_at = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ProviderReconciliationUnavailable from None
    if created_at.tzinfo is None:
        raise ProviderReconciliationUnavailable
    created_at = created_at.astimezone(timezone.utc)
    expected_at = attempt_created_at.astimezone(timezone.utc)
    if abs((created_at - expected_at).total_seconds()) > 900:
        raise ProviderReconciliationUnavailable
    finish_reason = data.get("finish_reason")
    if type(finish_reason) is not str or not finish_reason or len(finish_reason) > 64:
        raise ProviderReconciliationUnavailable

    # Native counts are authoritative for provider accounting. Normalized
    # tokens_prompt/completion are never used as a fallback.
    prompt_tokens, completion_tokens = (
        data.get("native_tokens_prompt"), data.get("native_tokens_completion"),
    )
    try:
        usage = RawProviderUsage(
            data["model"], prompt_tokens, completion_tokens,
            prompt_tokens + completion_tokens,
        )
    except Exception:
        raise ProviderReconciliationUnavailable from None

    try:
        latency = _safe_decimal(data.get("latency"))
    except ProviderReconciliationUnavailable:
        raise ProviderReconciliationUnavailable
    return VerifiedOpenRouterGeneration(
        attempt_id=attempt_id,
        generation_id=generation_id,
        request_id=request_id,
        actual_model=usage.actual_model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        latency_ms=int(round(latency)),
        provider_cost_usd=format(provider_cost_usd, "f"),
        created_at=created_at,
        finish_reason=finish_reason,
        _issuer=_ISSUER,
    )


def is_verified_openrouter_generation(value) -> bool:
    return type(value) is VerifiedOpenRouterGeneration and value._issuer is _ISSUER


async def fetch_openrouter_generation(api_key: str, generation_id: str) -> bytes:
    """Read one OpenRouter generation with fixed origin, bounded response, no redirects."""
    if (type(api_key) is not str or len(api_key) < 16
            or not safe_provider_generation_id(generation_id)):
        raise ProviderReconciliationUnavailable
    try:
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, verify=True,
            timeout=httpx.Timeout(8.0, connect=3.0),
        ) as client:
            async with client.stream(
                "GET", _GENERATION_ENDPOINT, params={"id": generation_id},
                headers={"Authorization": f"Bearer {api_key}", "Accept-Encoding": "identity"},
            ) as response:
                if response.status_code != 200:
                    raise ProviderReconciliationUnavailable
                if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                    raise ProviderReconciliationUnavailable
                length = response.headers.get("content-length")
                if length is not None and (not length.isdigit() or int(length) > _MAX_BODY):
                    raise ProviderReconciliationUnavailable
                chunks = bytearray()
                async for chunk in response.aiter_raw():
                    if len(chunks) + len(chunk) > _MAX_BODY:
                        raise ProviderReconciliationUnavailable
                    chunks.extend(chunk)
                return bytes(chunks)
    except ProviderReconciliationUnavailable:
        raise
    except Exception:
        raise ProviderReconciliationUnavailable from None


def configured_openrouter_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if len(key) < 16:
        raise ProviderReconciliationUnavailable
    return key
