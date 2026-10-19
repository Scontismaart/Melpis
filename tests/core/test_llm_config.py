import pytest

from src.core.ai_providers import PROVIDERS
from src.core.llm_client_policy import LLMClientPolicy, resolve_llm_client_policy
from src.core.provider_accounting import ProviderAccountingError


@pytest.fixture(autouse=True)
def offline_client_configuration(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "standard")


def test_crea_llm_requires_private_provider_accounting_context():
    from src.core.llm_config import crea_llm

    with pytest.raises(ProviderAccountingError):
        crea_llm(model="groq/llama-3.1-8b-instant")


def test_openrouter_policy_keeps_deny_training_parameters(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-dummy-key")
    policy = resolve_llm_client_policy("openrouter/vendor/model:free")
    params = policy.client_params()

    assert params["additional_params"]["extra_body"]["provider"] == {
        "data_collection": "deny", "zdr": True, "allow_fallbacks": False,
    }


def test_groq_policy_uses_provider_key_and_no_openrouter_parameters(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "offline-dummy-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    policy = resolve_llm_client_policy("groq/llama-3.1-8b-instant")

    assert policy.provider is PROVIDERS["groq"]
    assert policy.client_params()["model"] == "groq/llama-3.1-8b-instant"
    assert "additional_params" not in policy.client_params()


def test_explicit_provider_and_endpoint_validation_remain_strict(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AI_API_KEY", "offline-dummy-key")
    monkeypatch.setenv("AI_BASE_URL", "https://ai.example.test/v1")
    policy = resolve_llm_client_policy("custom-model")
    assert policy.client_params()["base_url"] == "https://ai.example.test/v1"

    with pytest.raises(RuntimeError, match="non corrisponde"):
        resolve_llm_client_policy("groq/llama-3.1-8b-instant")


def test_client_constructor_keeps_bounds_without_creating_http_request():
    from src.core.observed_llm import ObservedChatLLM

    policy = LLMClientPolicy(
        provider=PROVIDERS["groq"],
        model="groq/llama-3.1-8b-instant",
        api_key="offline-only",
        base_url=None,
    )
    llm = ObservedChatLLM(policy=policy, session=None, max_tokens=5000, timeout=0.01)
    assert llm.max_tokens == 1000
    assert llm._timeout == 0.1


def test_default_model_remains_in_free_catalog():
    from src.core.cost_policy import FREE_MODELS
    from src.core.llm_config import MODELLO_DEFAULT

    assert MODELLO_DEFAULT in FREE_MODELS
    assert not MODELLO_DEFAULT.endswith(":free")
