import json
import uuid
from datetime import datetime, timezone

import pytest

import src.core.provider_recovery as recovery


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
    options = None

    def __init__(self, **options):
        type(self).options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, method, url, *, params, headers):
        type(self).calls.append((method, url, params, headers))
        return type(self).response


@pytest.mark.asyncio
async def test_openrouter_generation_fetch_uses_fixed_authenticated_get(monkeypatch):
    _Client.calls = []
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
    assert _Client.calls == [(
        "GET", "https://openrouter.ai/api/v1/generation",
        {"id": "gen-test-safe-001"},
        {"Authorization": "Bearer synthetic-test-key-not-a-secret",
         "Accept-Encoding": "identity"},
    )]


@pytest.mark.asyncio
async def test_openrouter_generation_fetch_rejects_unavailable_or_oversized_response(monkeypatch):
    monkeypatch.setattr(recovery.httpx, "AsyncClient", _Client)
    _Client.response = _StreamingResponse(b"ignored", status=404)
    with pytest.raises(recovery.ProviderReconciliationUnavailable):
        await recovery.fetch_openrouter_generation(
            "synthetic-test-key-not-a-secret", "gen-test-safe-002",
        )

    _Client.response = _StreamingResponse(b"x" * (recovery._MAX_BODY + 1))
    with pytest.raises(recovery.ProviderReconciliationUnavailable):
        await recovery.fetch_openrouter_generation(
            "synthetic-test-key-not-a-secret", "gen-test-safe-003",
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
