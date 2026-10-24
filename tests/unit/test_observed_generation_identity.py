"""Generation IDs are correlation evidence, independent from usage validation."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from src.core.ai_providers import PROVIDERS
from src.core.llm_client_policy import LLMClientPolicy
from src.core.observed_llm import ObservedChatLLM
import src.core.observed_llm as observed_llm


GENERATION_ID = "gen-identity-test-001"
REQUEST_ID = "req_identity_test_001"
MODEL = "openrouter/vendor/model:free"
ACTUAL_MODEL = "vendor/model:free"


class _SyncSession:
    def __init__(self, *, fail_id_persistence=False):
        self.attempt = SimpleNamespace(provider="openrouter", attempt_id=uuid.uuid4())
        self.fail_id_persistence = fail_id_persistence
        self.id_calls = []
        self.generation_id = None
        self.provider_request_id = None
        self.finished = []
        self.failed = []

    def assert_http_admission(self):
        return None

    def bridge(self, callback, *args, **kwargs):
        return callback(*args, **kwargs)

    def begin(self, provider, model, reason):
        assert provider == self.attempt.provider
        return self.attempt

    def mark_dispatching(self, attempt):
        assert attempt is self.attempt
        return True

    def record_provider_ids(self, attempt, *, generation_id=None, provider_request_id=None):
        assert attempt is self.attempt
        self.id_calls.append((generation_id, provider_request_id))
        if self.fail_id_persistence:
            raise RuntimeError("synthetic persistence failure")
        if generation_id is not None:
            self.generation_id = generation_id
        if provider_request_id is not None:
            self.provider_request_id = provider_request_id
        return True

    def finish(self, attempt, usage, latency_ms, **kwargs):
        assert attempt is self.attempt
        self.finished.append((usage, kwargs))
        return True

    def fail(self, attempt, outcome, latency_ms, actual_model=None, usage_present=None, **kwargs):
        assert attempt is self.attempt
        self.failed.append((outcome, actual_model, usage_present, kwargs))
        return True


class _AsyncSession:
    def __init__(self, *, fail_id_persistence=False):
        self.attempt = SimpleNamespace(provider="openrouter", attempt_id=uuid.uuid4())
        self.fail_id_persistence = fail_id_persistence
        self.id_calls = []
        self.generation_id = None
        self.provider_request_id = None
        self.finished = []
        self.failed = []

    def assert_http_admission(self):
        return None

    async def begin(self, provider, model, reason):
        assert provider == self.attempt.provider
        return self.attempt

    async def mark_dispatching(self, attempt):
        assert attempt is self.attempt
        return True

    async def record_provider_ids(self, attempt, *, generation_id=None, provider_request_id=None):
        assert attempt is self.attempt
        self.id_calls.append((generation_id, provider_request_id))
        if self.fail_id_persistence:
            raise RuntimeError("synthetic persistence failure")
        if generation_id is not None:
            self.generation_id = generation_id
        if provider_request_id is not None:
            self.provider_request_id = provider_request_id
        return True

    async def finish(self, attempt, usage, latency_ms, **kwargs):
        assert attempt is self.attempt
        self.finished.append((usage, kwargs))
        return True

    async def fail(self, attempt, outcome, latency_ms, actual_model=None, usage_present=None, **kwargs):
        assert attempt is self.attempt
        self.failed.append((outcome, actual_model, usage_present, kwargs))
        return True

    async def fail_before_dispatch(self, *args, **kwargs):
        raise AssertionError("The mocked response follows authorized dispatch")


def _payload(*, usage="missing", generation_id=GENERATION_ID, model=ACTUAL_MODEL,
             request_id=REQUEST_ID):
    payload = {
        "id": generation_id,
        "model": model,
        "request_id": request_id,
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
    }
    if usage == "valid":
        payload["usage"] = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    elif usage == "malformed":
        payload["usage"] = {"prompt_tokens": 3, "completion_tokens": 2}
    elif usage == "incoherent":
        payload["usage"] = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 99}
    elif usage != "missing":
        raise AssertionError("unknown test usage case")
    return payload


def _llm(monkeypatch, session, transport, *, mode):
    monkeypatch.setattr(observed_llm, "assert_model_allowed", lambda _model: None)
    monkeypatch.setattr(observed_llm, "ProviderAccountingContext", type(session))
    return ObservedChatLLM(
        policy=LLMClientPolicy(
            provider=PROVIDERS["openrouter"], model=MODEL,
            api_key="offline-test-key", base_url=None,
        ),
        session=session,
        timeout=1,
        sync_transport=transport if mode == "sync" else None,
        async_transport=transport if mode == "async" else None,
    )


def _mock_response(payload, *, status=200, headers=None, raw=None):
    if raw is None:
        raw = json.dumps(payload, allow_nan=False).encode()
    return httpx.Response(
        status,
        stream=httpx.ByteStream(raw),
        headers={"content-type": "application/json", "content-length": str(len(raw)),
                 **(headers or {})},
    )


async def _invoke(llm, mode):
    if mode == "sync":
        return await asyncio.to_thread(llm.call, "synthetic provider test")
    return await llm.acall("synthetic provider test")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("usage", ["missing", "malformed", "incoherent"])
async def test_body_generation_identity_survives_unusable_usage(monkeypatch, mode, usage):
    """A valid generation ID must be durable even when usage cannot be trusted."""
    session = _SyncSession() if mode == "sync" else _AsyncSession()
    calls = []

    def handler(request):
        calls.append(request.method)
        return _mock_response(_payload(usage=usage))

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert calls == ["POST"]
    assert session.generation_id == GENERATION_ID, (session.id_calls, session.failed)
    assert session.provider_request_id == REQUEST_ID
    assert session.finished == []
    assert len(session.failed) == 1
    assert session.failed[0][0] == ("invalid_response" if usage == "missing" else "invalid_usage")
    assert session.failed[0][3]["provider_generation_id"] == GENERATION_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_header_body_identity_conflict_fails_closed(monkeypatch, mode):
    session = _SyncSession() if mode == "sync" else _AsyncSession()
    calls = []

    def handler(request):
        calls.append(request.method)
        return _mock_response(
            _payload(usage="valid", generation_id="gen-body-identity-002"),
            headers={"x-generation-id": GENERATION_ID},
        )

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert calls == ["POST"]
    assert session.finished == []
    assert len(session.failed) == 1
    # The evidence may not silently switch from the header identity to body ID.
    assert session.generation_id == GENERATION_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_header_body_request_identity_conflict_fails_closed(monkeypatch, mode):
    session = _SyncSession() if mode == "sync" else _AsyncSession()

    def handler(_request):
        return _mock_response(
            _payload(usage="valid"), headers={"x-request-id": "req_header_identity_002"},
        )

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert session.finished == []
    assert len(session.failed) == 1
    assert session.provider_request_id == "req_header_identity_002"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_response_chat_completion_id_is_not_generation_identity(monkeypatch, mode):
    session = _SyncSession() if mode == "sync" else _AsyncSession()
    payload = _payload(usage="valid", generation_id="chatcmpl-not-a-generation-id")
    calls = []

    def handler(request):
        calls.append(request.method)
        return _mock_response(payload)

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    result = await _invoke(llm, mode)

    assert calls == ["POST"]
    assert session.generation_id is None
    assert session.provider_request_id == REQUEST_ID
    assert len(session.finished) == 1
    assert result is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("bad_id", [None, 42, "req_wrong-namespace-001", "chatcmpl-wrong-namespace"])
async def test_untrusted_body_id_types_and_namespaces_are_not_generation_ids(
    monkeypatch, mode, bad_id,
):
    session = _SyncSession() if mode == "sync" else _AsyncSession()

    def handler(_request):
        return _mock_response(_payload(usage="valid", generation_id=bad_id))

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    await _invoke(llm, mode)

    assert session.generation_id is None
    assert len(session.finished) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_wrong_model_does_not_erase_valid_body_generation_identity(monkeypatch, mode):
    session = _SyncSession() if mode == "sync" else _AsyncSession()

    def handler(_request):
        return _mock_response(_payload(usage="valid", model="vendor/other-model"))

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert session.generation_id == GENERATION_ID
    assert session.finished == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_invalid_or_duplicate_json_keeps_eligible_header_only(monkeypatch, mode):
    session = _SyncSession() if mode == "sync" else _AsyncSession()
    raw = b'{"id":"gen-body-identity-003","id":"gen-body-identity-004"}'

    def handler(_request):
        return _mock_response({}, raw=raw, headers={"x-generation-id": GENERATION_ID})

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert session.generation_id == GENERATION_ID
    assert session.finished == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("raw", [b"{not-json", b'{"id":"gen-one","id":"gen-two"}',
                                  b'{"id":"gen-one","value":NaN}'])
async def test_invalid_duplicate_and_nonfinite_json_keep_header_only(monkeypatch, mode, raw):
    session = _SyncSession() if mode == "sync" else _AsyncSession()

    def handler(_request):
        return _mock_response({}, raw=raw, headers={"x-generation-id": GENERATION_ID})

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert session.generation_id == GENERATION_ID
    assert session.finished == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_non_success_http_response_does_not_trust_generation_identity(monkeypatch, mode):
    session = _SyncSession() if mode == "sync" else _AsyncSession()

    def handler(_request):
        return _mock_response(
            _payload(usage="valid"), status=503,
            headers={"x-generation-id": GENERATION_ID},
        )

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert session.generation_id is None
    assert session.finished == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_non_identity_encoding_rejected_before_header_identity(monkeypatch, mode):
    session = _SyncSession() if mode == "sync" else _AsyncSession()

    def handler(_request):
        return _mock_response(
            _payload(usage="valid"),
            headers={"x-generation-id": GENERATION_ID, "content-encoding": "gzip"},
        )

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert session.generation_id is None
    assert session.finished == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_oversized_body_preserves_observed_header_identity(monkeypatch, mode):
    session = _SyncSession() if mode == "sync" else _AsyncSession()

    if mode == "sync":
        class _OversizedStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"x" * (1_048_576 + 1)
    else:
        class _OversizedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"x" * (1_048_576 + 1)

    def handler(_request):
        return httpx.Response(
            200, stream=_OversizedStream(),
            headers={"content-type": "application/json", "x-generation-id": GENERATION_ID},
        )

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert session.generation_id == GENERATION_ID
    assert session.finished == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_identity_persistence_failure_never_finishes_usage(monkeypatch, mode):
    session = _SyncSession(fail_id_persistence=True) if mode == "sync" else _AsyncSession(
        fail_id_persistence=True,
    )

    def handler(_request):
        return _mock_response(_payload(usage="valid"))

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert session.finished == []
    assert len(session.failed) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_eligible_header_identity_survives_body_timeout(monkeypatch, mode):
    session = _SyncSession() if mode == "sync" else _AsyncSession()
    payload = json.dumps(_payload(usage="valid")).encode()

    class _SyncTimeoutStream(httpx.SyncByteStream):
        def __iter__(self):
            yield payload[:8]
            raise httpx.ReadTimeout("synthetic body timeout")

    class _AsyncTimeoutStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload[:8]
            raise httpx.ReadTimeout("synthetic body timeout")

    stream = _SyncTimeoutStream() if mode == "sync" else _AsyncTimeoutStream()

    def handler(_request):
        return httpx.Response(
            200, stream=stream,
            headers={"content-type": "application/json", "x-generation-id": GENERATION_ID},
        )

    llm = _llm(monkeypatch, session, httpx.MockTransport(handler), mode=mode)
    with pytest.raises(observed_llm.ProviderAccountingError):
        await _invoke(llm, mode)

    assert session.generation_id == GENERATION_ID
    assert session.finished == []
    assert len(session.failed) == 1
