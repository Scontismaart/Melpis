import dataclasses

import pytest

from src.core.llm_client_policy import LLMClientPolicy, resolve_llm_client_policy


def test_policy_is_frozen_and_repr_redacts_key_and_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "standard")
    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AI_MODEL", "custom-free-model")
    monkeypatch.setenv("AI_API_KEY", "secret-test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://private-endpoint.example.test/v1")

    policy = resolve_llm_client_policy("custom-free-model")

    assert isinstance(policy, LLMClientPolicy)
    assert "secret-test-key" not in repr(policy)
    assert "private-endpoint" not in repr(policy)
    assert policy.provider.name == "openai_compatible"
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.model = "other-model"


def test_policy_keeps_groq_free_account_confirmation_gate(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.delenv("GROQ_FREE_ACCOUNT_CONFIRMED", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    with pytest.raises(RuntimeError, match="FREE Groq"):
        resolve_llm_client_policy("groq/openai/gpt-oss-20b")

    monkeypatch.setenv("GROQ_FREE_ACCOUNT_CONFIRMED", "true")
    policy = resolve_llm_client_policy("groq/openai/gpt-oss-20b")
    assert policy.provider.name == "groq"
    assert policy.model == "groq/openai/gpt-oss-20b"


def test_policy_requires_provider_key_without_falling_back_to_other_key(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "standard")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        resolve_llm_client_policy("vendor/model")


def test_policy_checks_openrouter_zdr_catalog_in_free_only(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    checks = []
    monkeypatch.setattr(
        "src.core.llm_client_policy.assert_openrouter_catalog_free",
        lambda model, key: checks.append((model, key)),
    )

    policy = resolve_llm_client_policy("openrouter/vendor/model:free")

    assert checks == [("openrouter/vendor/model:free", "or-test-key")]
    params = policy.client_params()
    assert params["additional_params"]["extra_body"]["provider"] == {
        "data_collection": "deny",
        "zdr": True,
        "allow_fallbacks": False,
    }


def test_policy_keeps_standard_explicit_api_key_precedence(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "standard")
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("AI_API_KEY", "explicit-key")
    monkeypatch.setenv("GROQ_API_KEY", "provider-key")

    policy = resolve_llm_client_policy("openai/gpt-oss-20b")

    assert policy.api_key == "explicit-key"
    assert policy.client_params()["api_key"] == "explicit-key"


def test_policy_preserves_free_only_custom_endpoint_and_provider_mismatch_guards(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.setenv("GROQ_FREE_ACCOUNT_CONFIRMED", "true")
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("AI_BASE_URL", "https://paid.example.test/v1")
    with pytest.raises(RuntimeError, match="Budget EUR 0"):
        resolve_llm_client_policy("groq/openai/gpt-oss-20b")

    monkeypatch.delenv("AI_BASE_URL")
    with pytest.raises(RuntimeError, match="non corrisponde"):
        resolve_llm_client_policy("openrouter/vendor/model:free")


def test_policy_preserves_unreviewed_provider_rejection(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.delenv("GROQ_FREE_ACCOUNT_CONFIRMED", raising=False)
    with pytest.raises(RuntimeError, match="Budget EUR 0"):
        resolve_llm_client_policy("mistral/mistral-small-latest")
