import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

import src.core.observed_llm as observed_llm
from src.core.ai_providers import PROVIDERS
from src.core.llm_client_policy import LLMClientPolicy
from src.core.observed_llm import ObservedChatLLM


class _HttpAccountingSession:
    def __init__(self, provider):
        self.attempt = SimpleNamespace(provider=provider, attempt_id=uuid.uuid4())
        self.completed = []

    def assert_http_admission(self):
        return None

    def bridge(self, callback, *args, **kwargs):
        return callback(*args, **kwargs)

    def begin(self, provider, model, reason):
        assert provider == self.attempt.provider
        return self.attempt

    def finish(self, attempt, usage, latency_ms, **kwargs):
        self.completed.append((attempt, usage, latency_ms, kwargs))

    def fail(self, *args, **kwargs):
        raise AssertionError("The mocked provider response should complete successfully")


@pytest.mark.parametrize(
    ("provider_name", "model", "response_model"),
    [
        ("groq", "groq/llama-3.1-8b-instant", "llama-3.1-8b-instant"),
        ("openrouter", "openrouter/vendor/model:free", "vendor/model:free"),
    ],
)
def test_effective_http_body_contains_provider_policy_and_attempt_correlation(
    monkeypatch, provider_name, model, response_model,
):
    monkeypatch.setattr(observed_llm, "assert_model_allowed", lambda _: None)
    monkeypatch.setattr(observed_llm, "ProviderAccountingContext", _HttpAccountingSession)
    session = _HttpAccountingSession(provider_name)
    captured = []

    def capture(request):
        captured.append(json.loads(request.content))
        payload = {
            "id": "provider-request-id-123",
            "model": response_model,
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        body = json.dumps(payload).encode("utf-8")
        return httpx.Response(
            200,
            stream=httpx.ByteStream(body),
            headers={"content-type": "application/json", "content-length": str(len(body))},
        )

    policy = LLMClientPolicy(
        provider=PROVIDERS[provider_name], model=model,
        api_key="offline-test-key", base_url=None,
    )
    llm = ObservedChatLLM(
        policy=policy,
        session=session,
        timeout=2,
        sync_transport=httpx.MockTransport(capture),
    )

    assert llm.call("synthetic test prompt") == "ok"
    assert len(captured) == 1
    body = captured[0]
    assert body["user"] == str(session.attempt.attempt_id)
    assert session.completed and session.completed[0][0] is session.attempt
    if provider_name == "openrouter":
        assert body["provider"] == {
            "data_collection": "deny",
            "zdr": True,
            "allow_fallbacks": False,
        }
    else:
        assert "provider" not in body
