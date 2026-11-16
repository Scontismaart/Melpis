import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import src.core.provider_recovery as recovery
from src.core.openrouter_recovery_models import (
    recovery_model_matches,
    reviewed_recovery_binding,
)
from src.core.provider_accounting import provider_model_matches

_REQUESTED_RECOVERY_MODEL = "openrouter/xingchen-agi/xing4.0-29b-a4b:free"
_DATED_RECOVERY_MODEL = "xingchen-agi/xing4.0-29b-a4b-20261015:free"


def _real_generation_receipt(attempt_id):
    """Sanitized observed OpenRouter generation receipt from the saved evidence."""
    return {"data": {
        "id": "gen-1792904518-LbF8HxcU9yIuXRy1nHIs",
        "external_user": str(attempt_id),
        "model": _DATED_RECOVERY_MODEL,
        "request_id": "req-1792904518-bt1AjazMADAqQPlJI5qD",
        "native_tokens_prompt": 17,
        "native_tokens_completion": 32,
        "total_cost": Decimal("0"),
        "latency": Decimal("1513"),
        "created_at": "2026-10-25T05:01:58.566Z",
        "finish_reason": "length",
    }}


_OBSERVED_LEASE_START = datetime.fromisoformat("2026-10-25T05:01:56.492083+00:00")
_ORIGINAL_ATTEMPT_CREATED_AT = datetime.fromisoformat("2026-10-25T05:01:57.617982+00:00")
_CATALOG_EVIDENCE = (
    Path(__file__).resolve().parents[2]
    / "docs" / "operations" / "evidence" / "openrouter-xing-canonical-20261025.json"
)


def test_catalog_evidence_sha256_matches_git_canonical_lf_bytes():
    canonical_bytes = _CATALOG_EVIDENCE.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(canonical_bytes).hexdigest() == (
        "94d4db5f5a64d03c63dd64258047f94549284e6b30ce917e667a315cf1a2372e"
    )


class _StreamingResponse:
    def __init__(self, body, *, status=200, headers=None):
        self.body = body
        self.status_code = status
        self.headers = {"content-encoding": "identity", **(headers or {})}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def aiter_raw(self):
        yield self.body


class _Client:
    calls = []
    response = None
    response_sequence = None
    options = None

    def __init__(self, **options):
        type(self).options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, method, url, *, params, headers):
        type(self).calls.append((method, url, params, headers))
        if type(self).response_sequence is not None:
            return type(self).response_sequence.pop(0)
        return type(self).response


@pytest.mark.asyncio
async def test_openrouter_generation_fetch_uses_fixed_authenticated_get(monkeypatch):
    _Client.calls = []
    _Client.response_sequence = None
    body = b'{"data":{"id":"gen-test-safe-001"}}'
    _Client.response = _StreamingResponse(
        body, headers={"content-length": str(len(body))},
    )
    monkeypatch.setattr(recovery.httpx, "AsyncClient", _Client)

    payload = await recovery.fetch_openrouter_generation(
        "synthetic-test-key-not-a-secret", "gen-test-safe-001",
    )

    assert json.loads(payload) == {"data": {"id": "gen-test-safe-001"}}
    assert _Client.options["trust_env"] is False
    assert _Client.options["follow_redirects"] is False
    assert _Client.options["verify"] is True
    assert _Client.options["timeout"].connect == 3.0
    assert _Client.options["timeout"].read == 8.0
    assert _Client.calls == [(
        "GET", "https://openrouter.ai/api/v1/generation",
        {"id": "gen-test-safe-001"},
        {"Authorization": "Bearer synthetic-test-key-not-a-secret",
         "Accept-Encoding": "identity"},
    )]


@pytest.mark.asyncio
async def test_openrouter_generation_fetch_retries_404_until_metadata_is_visible(monkeypatch):
    _Client.calls = []
    _Client.response_sequence = [
        _StreamingResponse(b"ignored", status=404),
        _StreamingResponse(b"ignored", status=404),
        _StreamingResponse(b'{"data":{"id":"gen-test-safe-002"}}', status=200),
    ]
    sleeps = []

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(recovery.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(recovery.asyncio, "sleep", record_sleep)

    payload = await recovery.fetch_openrouter_generation(
        "synthetic-test-key-not-a-secret", "gen-test-safe-002",
    )

    assert json.loads(payload) == {"data": {"id": "gen-test-safe-002"}}
    assert len(_Client.calls) == 3
    assert sleeps == [0.5, 1.0]
    assert all(call == _Client.calls[0] for call in _Client.calls)


@pytest.mark.asyncio
async def test_openrouter_generation_fetch_keeps_persistent_404_unavailable(monkeypatch):
    _Client.calls = []
    _Client.response_sequence = [
        _StreamingResponse(b"ignored", status=404)
        for _ in range(len(recovery._GENERATION_LOOKUP_RETRY_DELAYS) + 1)
    ]
    sleeps = []

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(recovery.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(recovery.asyncio, "sleep", record_sleep)

    with pytest.raises(recovery.ProviderReconciliationUnavailable):
        await recovery.fetch_openrouter_generation(
            "synthetic-test-key-not-a-secret", "gen-test-safe-003",
        )

    assert len(_Client.calls) == len(recovery._GENERATION_LOOKUP_RETRY_DELAYS) + 1
    assert sleeps == list(recovery._GENERATION_LOOKUP_RETRY_DELAYS)


@pytest.mark.asyncio
async def test_openrouter_generation_fetch_enforces_global_retry_deadline(monkeypatch):
    _Client.calls = []
    _Client.response_sequence = [
        _StreamingResponse(b"ignored", status=404),
        _StreamingResponse(b'{"data":{"id":"gen-test-safe-004"}}', status=200),
    ]
    monkeypatch.setattr(recovery.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(recovery, "_GENERATION_LOOKUP_RETRY_DELAYS", (1.0,))
    monkeypatch.setattr(recovery, "_GENERATION_LOOKUP_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(recovery.ProviderReconciliationUnavailable):
        await recovery.fetch_openrouter_generation(
            "synthetic-test-key-not-a-secret", "gen-test-safe-004",
        )

    assert len(_Client.calls) == 1


@pytest.mark.asyncio
async def test_openrouter_generation_fetch_propagates_task_cancellation(monkeypatch):
    _Client.calls = []
    _Client.response_sequence = [_StreamingResponse(b"ignored", status=404)]
    waiting_for_backoff = asyncio.Event()

    async def wait_until_cancelled(_delay):
        waiting_for_backoff.set()
        await asyncio.Future()

    monkeypatch.setattr(recovery.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(recovery.asyncio, "sleep", wait_until_cancelled)
    task = asyncio.create_task(recovery.fetch_openrouter_generation(
        "synthetic-test-key-not-a-secret", "gen-test-safe-005",
    ))

    await waiting_for_backoff.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(_Client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 429, 500, 503])
async def test_openrouter_generation_fetch_does_not_retry_other_http_errors(
    monkeypatch, status,
):
    _Client.calls = []
    _Client.response_sequence = [_StreamingResponse(b"ignored", status=status)]
    sleeps = []

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(recovery.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(recovery.asyncio, "sleep", record_sleep)

    with pytest.raises(recovery.ProviderReconciliationUnavailable):
        await recovery.fetch_openrouter_generation(
            "synthetic-test-key-not-a-secret", "gen-test-safe-006",
        )

    assert len(_Client.calls) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_openrouter_generation_fetch_rejects_oversized_response(monkeypatch):
    _Client.response_sequence = None
    _Client.response = _StreamingResponse(b"x" * (recovery._MAX_BODY + 1))
    monkeypatch.setattr(recovery.httpx, "AsyncClient", _Client)
    with pytest.raises(recovery.ProviderReconciliationUnavailable):
        await recovery.fetch_openrouter_generation(
            "synthetic-test-key-not-a-secret", "gen-test-safe-005",
        )


def test_generation_verifier_never_falls_back_to_normalized_token_counts():
    attempt_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    payload = {"data": {
        "id": "gen-test-safe-004", "external_user": str(attempt_id),
        "model": "xingchen-agi/xing4.0-29b-a4b:free",
        "created_at": created_at.isoformat(), "finish_reason": "stop",
        "tokens_prompt": 10, "tokens_completion": 5,
        "latency": 0.2, "total_cost": 0.0,
    }}

    with pytest.raises(recovery.ProviderReconciliationUnavailable):
        recovery.verify_openrouter_generation(
            payload, generation_id="gen-test-safe-004", attempt_id=attempt_id,
            requested_model="openrouter/xingchen-agi/xing4.0-29b-a4b:free",
            attempt_created_at=created_at, expected_request_id=None,
        )


def test_generation_verifier_preserves_provider_cost_decimal_exactly():
    attempt_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    raw = (
        '{"data":{"id":"gen-test-precision-001","external_user":"'
        + str(attempt_id)
        + '","model":"xingchen-agi/xing4.0-29b-a4b:free","created_at":"'
        + created_at.isoformat()
        + '","finish_reason":"stop","native_tokens_prompt":10,'
        '"native_tokens_completion":5,"latency":0.42,'
        '"total_cost":0.123456789012345678901}}'
    ).encode()

    evidence = recovery.verify_openrouter_generation(
        raw, generation_id="gen-test-precision-001", attempt_id=attempt_id,
        requested_model="openrouter/xingchen-agi/xing4.0-29b-a4b:free",
        attempt_created_at=created_at, expected_request_id=None,
    )

    assert evidence.provider_cost_usd == "0.123456789012345678901"


def test_generation_verifier_rejects_unbounded_provider_cost():
    attempt_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    raw = (
        '{"data":{"id":"gen-test-cost-bound-001","external_user":"'
        + str(attempt_id)
        + '","model":"xingchen-agi/xing4.0-29b-a4b:free","created_at":"'
        + created_at.isoformat()
        + '","finish_reason":"stop","native_tokens_prompt":10,'
        '"native_tokens_completion":5,"latency":0.42,"total_cost":1e999999}}'
    ).encode()

    with pytest.raises(recovery.ProviderReconciliationUnavailable):
        recovery.verify_openrouter_generation(
            raw, generation_id="gen-test-cost-bound-001", attempt_id=attempt_id,
            requested_model="openrouter/xingchen-agi/xing4.0-29b-a4b:free",
            attempt_created_at=created_at, expected_request_id=None,
        )


def test_reviewed_recovery_model_requires_the_exact_zero_cost_binding():
    binding = reviewed_recovery_binding(
        _REQUESTED_RECOVERY_MODEL, _DATED_RECOVERY_MODEL, Decimal("0"),
    )
    assert binding == {
        "model_binding_source": "openrouter_public_catalog_20261025",
        "model_binding_sha256": "94d4db5f5a64d03c63dd64258047f94549284e6b30ce917e667a315cf1a2372e",
        "pricing_source": "openrouter_generation_receipt_zero_cost",
        "pricing_model": _DATED_RECOVERY_MODEL,
    }
    assert recovery_model_matches(_REQUESTED_RECOVERY_MODEL, _DATED_RECOVERY_MODEL, "0")

    for requested, actual in (
        (_REQUESTED_RECOVERY_MODEL, "xingchen-agi/xing4.0-29b-a4b-20261016:free"),
        ("groq/xingchen-agi/xing4.0-29b-a4b:free", _DATED_RECOVERY_MODEL),
        ("openrouter/xingchen-agi/xing4.0-29b-a4b", _DATED_RECOVERY_MODEL),
        (_REQUESTED_RECOVERY_MODEL, "xingchen-agi/xing4.0-29b-a4b-20261015"),
    ):
        assert reviewed_recovery_binding(requested, actual, "0") is None
        assert recovery_model_matches(requested, actual, "0") is provider_model_matches(requested, actual)

    # The old generic matcher still accepts its established bare canonical
    # identity; it must not be mistaken for the reviewed dated recovery binding.
    assert reviewed_recovery_binding(
        _REQUESTED_RECOVERY_MODEL, "xingchen-agi/xing4.0-29b-a4b:free", "0",
    ) is None
    assert provider_model_matches(
        _REQUESTED_RECOVERY_MODEL, "xingchen-agi/xing4.0-29b-a4b:free",
    ) is True

    for cost in ("0.0001", "-1", "NaN", "Infinity", True, 0.0, None):
        assert reviewed_recovery_binding(
            _REQUESTED_RECOVERY_MODEL, _DATED_RECOVERY_MODEL, cost,
        ) is None


def test_generation_verifier_accepts_observed_dated_zero_cost_receipt():
    attempt_id = uuid.UUID("ec7beef8-fecf-4c2f-9ad0-61656530da75")
    receipt = _real_generation_receipt(attempt_id)
    assert _OBSERVED_LEASE_START < _ORIGINAL_ATTEMPT_CREATED_AT
    evidence = recovery.verify_openrouter_generation(
        receipt,
        generation_id=receipt["data"]["id"],
        attempt_id=attempt_id,
        requested_model=_REQUESTED_RECOVERY_MODEL,
        # This is the readonly observed original attempt creation time. The
        # observed lease_start is an earlier lower bound, not its substitute.
        attempt_created_at=_ORIGINAL_ATTEMPT_CREATED_AT,
        expected_request_id=None,
    )

    assert evidence.actual_model == _DATED_RECOVERY_MODEL
    assert evidence.generation_id == receipt["data"]["id"]
    assert evidence.request_id == receipt["data"]["request_id"]
    assert (evidence.prompt_tokens, evidence.completion_tokens, evidence.total_tokens) == (17, 32, 49)
    assert evidence.provider_cost_usd == "0"
    assert evidence.latency_ms == 1513
    assert evidence.created_at.isoformat() == "2026-10-25T05:01:58.566000+00:00"


@pytest.mark.parametrize("changes", [
    {"model": "xingchen-agi/xing4.0-29b-a4b-20261016:free"},
    {"model": "xingchen-agi/xing4.0-29b-a4b-20261015"},
    {"total_cost": Decimal("0.0001")},
    {"native_tokens_prompt": None},
    {"external_user": "a-different-attempt"},
])
def test_generation_verifier_rejects_unreviewed_recovery_receipts(changes):
    attempt_id = uuid.UUID("ec7beef8-fecf-4c2f-9ad0-61656530da75")
    receipt = _real_generation_receipt(attempt_id)
    receipt["data"].update(changes)

    with pytest.raises(recovery.ProviderReconciliationUnavailable):
        recovery.verify_openrouter_generation(
            receipt,
            generation_id=receipt["data"]["id"],
            attempt_id=attempt_id,
            requested_model=_REQUESTED_RECOVERY_MODEL,
            attempt_created_at=_ORIGINAL_ATTEMPT_CREATED_AT,
            expected_request_id=None,
        )


def test_generic_provider_model_matching_remains_unchanged():
    assert provider_model_matches(
        "openrouter/xingchen-agi/xing4.0-29b-a4b:free",
        "xingchen-agi/xing4.0-29b-a4b:free",
    ) is True
    assert provider_model_matches(
        _REQUESTED_RECOVERY_MODEL, _DATED_RECOVERY_MODEL,
    ) is False
