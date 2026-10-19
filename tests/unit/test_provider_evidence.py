import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.core.provider_evidence import (
    EvidenceUnavailable,
    ImmutableEvidenceBlob,
    verify_provider_evidence,
)


@pytest.fixture(autouse=True)
def trusted_provider_account(monkeypatch):
    monkeypatch.setenv("GROQ_PROVIDER_ACCOUNT_REF", "acct_123456789")


class _Store:
    def __init__(self, blob):
        self.blob = blob

    async def read_immutable(self, organization_id, object_id):
        return self.blob


def _payload(attempt_id, *, account_ref="acct_123456789", request_ref="req_123456789"):
    return json.dumps({
        "schema_version": 1,
        "attempt_id": str(attempt_id),
        "provider": "groq",
        "provider_account_ref": account_ref,
        "provider_correlation_id": str(attempt_id),
        "provider_request_id": request_ref,
        "provider_event_at": datetime.now(timezone.utc).isoformat(),
        "terminal": True,
        "outcome": "provider_confirms_not_billed",
        "actual_model": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }, sort_keys=True, separators=(",", ":")).encode()


def _blob(data, *, mode="compliance", until=None):
    return ImmutableEvidenceBlob(
        data=data,
        version_id="v_0123456789abcdef0123456789abcdef",
        retention_mode=mode,
        retained_until=until or datetime.now(timezone.utc) + timedelta(days=100),
    )


@pytest.mark.asyncio
async def test_verifier_calculates_digest_and_requires_immutable_retention():
    org_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    raw = _payload(attempt_id)
    verified = await verify_provider_evidence(
        _Store(_blob(raw)), org_id, "a03e_0123456789abcdef0123456789abcdef",
        hashlib.sha256(raw).hexdigest(), attempt_id,
    )
    assert verified.attempt_id == attempt_id
    assert verified.provider == "groq"
    assert verified.outcome == "provider_confirms_not_billed"
    assert verified.object_version_id == "v_0123456789abcdef0123456789abcdef"
    assert "acct_123456789" not in repr(verified)

    with pytest.raises(EvidenceUnavailable):
        await verify_provider_evidence(
            _Store(_blob(raw, mode="governance")), org_id,
            "a03e_0123456789abcdef0123456789abcdef",
            hashlib.sha256(raw).hexdigest(), attempt_id,
        )
    with pytest.raises(EvidenceUnavailable):
        await verify_provider_evidence(
            _Store(_blob(raw, until=datetime.now(timezone.utc) - timedelta(seconds=1))), org_id,
            "a03e_0123456789abcdef0123456789abcdef",
            hashlib.sha256(raw).hexdigest(), attempt_id,
        )


@pytest.mark.asyncio
async def test_verifier_rejects_digest_mismatch_and_secret_like_provider_refs():
    org_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    raw = _payload(attempt_id)
    with pytest.raises(ValueError, match="digest mismatch"):
        await verify_provider_evidence(
            _Store(_blob(raw)), org_id, "a03e_0123456789abcdef0123456789abcdef",
            "0" * 64, attempt_id,
        )

    for secret_ref in (
        "sk_123456789abcdef", "gsk_123456789abcdef", "sk-or-v1-123456789abcdef",
        "Bearer abcdefghijklmnop", "eyJaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ):
        raw = _payload(attempt_id, request_ref=secret_ref)
        with pytest.raises(ValueError, match="secret-like"):
            await verify_provider_evidence(
                _Store(_blob(raw)), org_id, "a03e_0123456789abcdef0123456789abcdef",
                hashlib.sha256(raw).hexdigest(), attempt_id,
            )


@pytest.mark.asyncio
async def test_verifier_binds_artifact_account_to_trusted_configuration(monkeypatch):
    org_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    mismatched = _payload(attempt_id, account_ref="acct_other987654")
    with pytest.raises(ValueError, match="trusted configuration"):
        await verify_provider_evidence(
            _Store(_blob(mismatched)), org_id, "a03e_0123456789abcdef0123456789abcdef",
            hashlib.sha256(mismatched).hexdigest(), attempt_id,
        )

    monkeypatch.delenv("GROQ_PROVIDER_ACCOUNT_REF")
    matching = _payload(attempt_id)
    with pytest.raises(EvidenceUnavailable):
        await verify_provider_evidence(
            _Store(_blob(matching)), org_id, "a03e_0123456789abcdef0123456789abcdef",
            hashlib.sha256(matching).hexdigest(), attempt_id,
        )


@pytest.mark.asyncio
async def test_verifier_rejects_non_strict_object_ids_and_duplicate_json_fields():
    org_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    raw = _payload(attempt_id)
    with pytest.raises(ValueError, match="identifier or digest"):
        await verify_provider_evidence(
            _Store(_blob(raw)), org_id, "../../private",
            hashlib.sha256(raw).hexdigest(), attempt_id,
        )
    duplicate = raw[:-1] + b',"terminal":true}'
    with pytest.raises(ValueError, match="Invalid immutable evidence artifact"):
        await verify_provider_evidence(
            _Store(_blob(duplicate)), org_id,
            "a03e_0123456789abcdef0123456789abcdef",
            hashlib.sha256(duplicate).hexdigest(), attempt_id,
        )


@pytest.mark.asyncio
async def test_verifier_rejects_oversized_immutable_artifact_before_parsing():
    org_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    oversized = b" " * (16 * 1024 + 1)
    with pytest.raises(ValueError, match="exceeds the size limit"):
        await verify_provider_evidence(
            _Store(_blob(oversized)), org_id,
            "a03e_0123456789abcdef0123456789abcdef",
            hashlib.sha256(oversized).hexdigest(), attempt_id,
        )
    with pytest.raises(ValueError, match="empty or exceeds"):
        await verify_provider_evidence(
            _Store(_blob(b"")), org_id,
            "a03e_0123456789abcdef0123456789abcdef",
            hashlib.sha256(b"").hexdigest(), attempt_id,
        )
