import pytest

from src.core.llm_config import LLM_TIMEOUT_SECONDS, MODELLO_DEFAULT, crea_llm
from src.core.llm_routing import LLMRouteRequest


@pytest.fixture(autouse=True)
def offline_client_configuration(monkeypatch):
    # Only constructs clients with dummy keys; no inference/network is allowed.
    monkeypatch.setenv("LLM_COST_POLICY", "standard")


def test_crea_llm_inietta_sempre_data_collection_deny(monkeypatch):
    """Ogni chiamata deve negare l'uso dei dati per training su OpenRouter
    (extra_body provider.data_collection='deny'), incluso il futuro."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    llm = crea_llm(model="acme/modello")

    extra_body = llm.additional_params["extra_body"]
    assert extra_body["provider"]["data_collection"] == "deny"
    assert llm.timeout == LLM_TIMEOUT_SECONDS


def test_crea_llm_usa_modello_da_route_request(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_MODEL_CHEAP", "cheap/model")
    monkeypatch.setenv("OPENROUTER_MODEL_PREMIUM", "premium/model")

    llm = crea_llm(route_request=LLMRouteRequest(task_type="review"))

    assert llm.model == "premium/model"


def test_crea_llm_senza_route_usa_default(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    llm = crea_llm()

    assert llm.model == MODELLO_DEFAULT
    assert not llm.model.endswith(":free")


def test_modello_default_e_ammesso_nel_profilo_gratuito():
    from src.core.cost_policy import FREE_MODELS
    assert MODELLO_DEFAULT in FREE_MODELS
    assert not MODELLO_DEFAULT.endswith(":free")


def test_crea_llm_groq_pass_through_senza_deny(monkeypatch):
    """Il fallback Groq passa per LiteLLM, usa la sua chiave e NON deve
    ricevere il parametro OpenRouter-specifico data_collection (Groq non
    addestra sui dati per policy)."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    llm = crea_llm(model="groq/openai/gpt-oss-20b")

    assert llm.is_litellm is True
    assert llm.model == "groq/openai/gpt-oss-20b"
    assert "extra_body" not in llm.additional_params


def test_explicit_groq_provider_prefixes_bare_model(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    llm = crea_llm(model="openai/gpt-oss-20b")
    assert llm.model == "groq/openai/gpt-oss-20b"


def test_crea_llm_cerebras_pass_through_senza_deny(monkeypatch):
    """Il fallback Cerebras è un provider nativo di CrewAI con base_url
    dedicato e la sua chiave; niente parametro OpenRouter."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-test")

    llm = crea_llm(model="cerebras/gpt-oss-120b")

    assert llm.base_url == "https://api.cerebras.ai/v1"
    assert llm.model == "gpt-oss-120b"
    assert "extra_body" not in llm.additional_params


def test_crea_llm_richiede_la_chiave_del_provider(monkeypatch):
    """Senza GROQ_API_KEY il path Groq deve fallire con errore esplicito,
    non silenziosamente usare la chiave OpenRouter."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        crea_llm(model="groq/openai/gpt-oss-20b")


def test_adapter_openai_compatible_usa_base_url_senza_chiamate_remote(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AI_MODEL", "custom-free-model")
    monkeypatch.setenv("AI_API_KEY", "dummy-offline-key")
    monkeypatch.setenv("AI_BASE_URL", "https://ai.example.test/v1")
    llm = crea_llm()
    assert llm.base_url == "https://ai.example.test/v1"
    assert "custom-free-model" in llm.model


def test_free_profile_non_permette_endpoint_custom_neppure_con_modello_groq(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.setenv("GROQ_FREE_ACCOUNT_CONFIRMED", "true")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("AI_BASE_URL", "https://paid.example.test/v1")
    with pytest.raises(RuntimeError, match="Budget EUR 0"):
        crea_llm(model="groq/openai/gpt-oss-20b")


def test_adapter_rejects_mismatched_provider_and_openrouter_custom_endpoint(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AI_API_KEY", "dummy-offline-key")
    monkeypatch.setenv("AI_BASE_URL", "https://ai.example.test/v1")
    with pytest.raises(RuntimeError, match="non corrisponde"):
        crea_llm(model="groq/openai/gpt-oss-20b")

    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-offline-key")
    with pytest.raises(RuntimeError, match="AI_BASE_URL non supportata"):
        crea_llm(model="openrouter/custom-free-model")


def test_free_openrouter_checks_catalog_before_client(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-offline-key")
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    checked = []
    monkeypatch.setattr("src.core.llm_client_policy.assert_openrouter_catalog_free", lambda model, key: checked.append(model))
    llm = crea_llm(model="openrouter/vendor/model:free")
    assert checked == ["openrouter/vendor/model:free"]
    assert llm.model == "vendor/model:free"
    assert llm.additional_params["extra_body"]["provider"]["zdr"] is True


def test_free_openrouter_catalog_failure_prevents_client(monkeypatch):
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-offline-key")
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    def reject(model, key):
        raise RuntimeError("catalog unavailable")
    monkeypatch.setattr("src.core.llm_client_policy.assert_openrouter_catalog_free", reject)
    with pytest.raises(RuntimeError, match="catalog unavailable"):
        crea_llm(model="openrouter/vendor/model:free")


@pytest.mark.parametrize(
    ("requested_tokens", "requested_timeout", "expected_tokens", "expected_timeout"),
    [(0, 0.01, 1, 0.1), (5000, 999, 1000, LLM_TIMEOUT_SECONDS)],
)
def test_crea_llm_preserves_token_and_timeout_bounds(
    monkeypatch, requested_tokens, requested_timeout, expected_tokens, expected_timeout
):
    monkeypatch.setenv("LLM_COST_POLICY", "standard")
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    captured = {}

    def capture_llm(**kwargs):
        captured.update(kwargs)
        return captured

    monkeypatch.setattr("src.core.llm_config.LLM", capture_llm)

    result = crea_llm(
        model="vendor/model",
        max_tokens=requested_tokens,
        timeout=requested_timeout,
    )

    assert result["max_tokens"] == expected_tokens
    assert captured["timeout"] == expected_timeout
    assert captured["num_retries"] == 1
