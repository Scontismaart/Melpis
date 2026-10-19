"""Verification contract for immutable administrative A03 evidence.

The production application intentionally has no default evidence-store client.
An explicitly configured, tenant-scoped store must implement this interface
and guarantee immutable/WORM objects before the recovery routes can proceed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

_ISSUER = object()
_OBJECT_ID = re.compile(r"^a03e_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_PROVIDER_REF = re.compile(r"^(?:acct|req)_[A-Za-z0-9_-]{8,96}$")
_CORRELATION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PROVIDER_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
_PROVIDER_GENERATION_ID = re.compile(r"^gen-[0-9A-Za-z-]{1,120}$")
_MAX_EVIDENCE_BYTES = 16 * 1024
_SECRETISH = re.compile(
    r"(?i)(?:\bBearer\s+|\b(?:sk|gsk|rk|pk|whsec|xox[baprs])[-_][A-Za-z0-9_-]{8,}|"
    r"eyJ[A-Za-z0-9_-]{20,}\.?[A-Za-z0-9_-]*\.?[A-Za-z0-9_-]*)"
)
_PROVIDER_ACCOUNT_ENV = {
    "groq": "GROQ_PROVIDER_ACCOUNT_REF",
    "openrouter": "OPENROUTER_PROVIDER_ACCOUNT_REF",
}
_PROVIDERS = frozenset(_PROVIDER_ACCOUNT_ENV)
_EXPECTED_FIELDS = frozenset({
    "schema_version", "attempt_id", "provider", "provider_account_ref",
    "provider_correlation_id", "provider_request_id", "provider_event_at",
    "terminal", "outcome", "actual_model",
    "prompt_tokens", "completion_tokens", "total_tokens",
})


class EvidenceUnavailable(Exception):
    """The authorized immutable evidence store is unavailable or unconfigured."""


class ImmutableEvidenceStore(Protocol):
    async def read_immutable(
        self, organization_id: uuid.UUID, object_id: str,
    ) -> ImmutableEvidenceBlob | None:
        """Read exact bytes and immutable-retention metadata for a tenant object."""


@dataclass(frozen=True, slots=True, repr=False)
class ImmutableEvidenceBlob:
    data: bytes
    version_id: str
    retention_mode: str
    retained_until: datetime

    def __repr__(self):
        return "ImmutableEvidenceBlob(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedProviderEvidence:
    object_id: str
    object_version_id: str
    sha256: str
    attempt_id: uuid.UUID
    provider: str
    provider_correlation_id: str
    provider_request_id: str
    provider_event_at: datetime
    outcome: str
    actual_model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    _issuer: object = field(default=None, repr=False, compare=False)

    def __repr__(self):
        return "VerifiedProviderEvidence(<verified>)"


def validate_attestations(attestations: dict) -> None:
    required = {
        "provider_account_and_request_verified",
        "terminal_outcome_verified",
        "metrics_verified",
    }
    if (type(attestations) is not dict or set(attestations) != required
            or any(type(value) is not bool or value is not True for value in attestations.values())):
        raise ValueError("Independent provider evidence attestations are required")


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate evidence field")
        result[key] = value
    return result


async def verify_provider_evidence(
    store: ImmutableEvidenceStore | None,
    organization_id: uuid.UUID,
    object_id: str,
    expected_sha256: str,
    attempt_id: uuid.UUID,
    provider: str | None = None,
) -> VerifiedProviderEvidence:
    """Fetch and validate immutable evidence; never persist its contents/refs."""
    if store is None:
        raise EvidenceUnavailable
    if (type(object_id) is not str or not _OBJECT_ID.fullmatch(object_id)
            or type(expected_sha256) is not str or not _SHA256.fullmatch(expected_sha256)):
        raise ValueError("Invalid immutable evidence identifier or digest")
    blob = await store.read_immutable(organization_id, object_id)
    if type(blob) is not ImmutableEvidenceBlob:
        raise EvidenceUnavailable
    if type(blob.data) is not bytes or not blob.data or len(blob.data) > _MAX_EVIDENCE_BYTES:
        raise ValueError("Immutable evidence artifact is empty or exceeds the size limit")
    if (type(blob.data) is not bytes or type(blob.version_id) is not str
            or not _VERSION_ID.fullmatch(blob.version_id)
            or blob.retention_mode != "compliance"
            or not isinstance(blob.retained_until, datetime)
            or blob.retained_until.tzinfo is None
            or blob.retained_until.astimezone(timezone.utc)
            < datetime.now(timezone.utc) + timedelta(days=90)):
        raise EvidenceUnavailable
    digest = hashlib.sha256(blob.data).hexdigest()
    if not hmac.compare_digest(digest, expected_sha256):
        raise ValueError("Immutable evidence digest mismatch")
    try:
        payload = json.loads(blob.data, object_pairs_hook=_pairs_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("Invalid immutable evidence artifact") from None
    if type(payload) is not dict or set(payload) != _EXPECTED_FIELDS:
        raise ValueError("Invalid immutable evidence fields")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("Unsupported immutable evidence version")
    try:
        payload_attempt_id = uuid.UUID(payload["attempt_id"])
    except (TypeError, ValueError, AttributeError):
        raise ValueError("Invalid provider attempt evidence") from None
    payload_provider = payload["provider"]
    if (payload_attempt_id != attempt_id or payload_provider not in _PROVIDERS
            or (provider is not None and payload_provider != provider)
            or payload["terminal"] is not True):
        raise ValueError("Evidence does not match a terminal provider attempt")
    expected_account_ref = os.getenv(_PROVIDER_ACCOUNT_ENV[payload_provider], "").strip()
    if (not expected_account_ref or not _PROVIDER_REF.fullmatch(expected_account_ref)
            or _SECRETISH.search(expected_account_ref)):
        raise EvidenceUnavailable
    account_ref = payload["provider_account_ref"]
    request_ref = payload["provider_correlation_id"]
    provider_request_id = payload["provider_request_id"]
    if (type(account_ref) is not str or not _PROVIDER_REF.fullmatch(account_ref)
            or type(request_ref) is not str or not _CORRELATION_ID.fullmatch(request_ref)
            or _SECRETISH.search(account_ref) or _SECRETISH.search(request_ref)
            or type(provider_request_id) is not str
            or not _PROVIDER_REQUEST_ID.fullmatch(provider_request_id)
            or _SECRETISH.search(provider_request_id)):
        raise ValueError("Provider references are malformed or secret-like")
    if not hmac.compare_digest(account_ref, expected_account_ref):
        raise ValueError("Provider account evidence does not match trusted configuration")
    if request_ref != str(attempt_id):
        raise ValueError("Provider correlation does not match the Melpis attempt")
    try:
        provider_event_at = datetime.fromisoformat(payload["provider_event_at"])
    except (TypeError, ValueError):
        raise ValueError("Provider event timestamp is required") from None
    if provider_event_at.tzinfo is None:
        raise ValueError("Provider event timestamp must include a timezone")
    provider_event_at = provider_event_at.astimezone(timezone.utc)

    outcome = payload["outcome"]
    if outcome == "provider_usage_confirmed":
        model = payload["actual_model"]
        counts = (payload["prompt_tokens"], payload["completion_tokens"], payload["total_tokens"])
        if (type(model) is not str or not 1 <= len(model) <= 256 or _SECRETISH.search(model)
                or any(type(value) is not int or value < 0 or value > 10_000_000 for value in counts)
                or counts[0] + counts[1] != counts[2]):
            raise ValueError("Exact provider metrics are required")
    elif outcome == "provider_confirms_not_billed":
        if (payload["actual_model"] is not None
                or any(payload[key] is not None for key in (
                    "prompt_tokens", "completion_tokens", "total_tokens"))):
            raise ValueError("No-bill evidence cannot claim provider metrics")
        model = None
        counts = (None, None, None)
    else:
        raise ValueError("Provider evidence must state an explicit terminal outcome")
    return VerifiedProviderEvidence(
        object_id=object_id, object_version_id=blob.version_id, sha256=digest,
        attempt_id=attempt_id, provider=payload_provider,
        provider_correlation_id=request_ref, provider_request_id=provider_request_id,
        provider_event_at=provider_event_at,
        outcome=outcome, actual_model=model, prompt_tokens=counts[0],
        completion_tokens=counts[1], total_tokens=counts[2], _issuer=_ISSUER,
    )


def is_verified_provider_evidence(value) -> bool:
    return type(value) is VerifiedProviderEvidence and value._issuer is _ISSUER


def safe_provider_request_id(value) -> bool:
    return (type(value) is str and _PROVIDER_REQUEST_ID.fullmatch(value) is not None
            and _SECRETISH.search(value) is None)


def safe_provider_generation_id(value) -> bool:
    """OpenRouter generation IDs are a separate namespace from request IDs."""
    return (type(value) is str and _PROVIDER_GENERATION_ID.fullmatch(value) is not None
            and _SECRETISH.search(value) is None)
