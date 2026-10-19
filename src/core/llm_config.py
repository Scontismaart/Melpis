"""Common LLM factory with provider-specific adapters and a fail-closed budget."""

import asyncio
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
from src.core.ai_providers import provider_for_model  # noqa: E402
from src.core.cost_policy import DEFAULT_FREE_MODEL, assert_model_allowed, free_only  # noqa: E402
from src.core.llm_client_policy import resolve_llm_client_policy  # noqa: E402
from src.core.llm_routing import (  # noqa: E402
    LLMRoute,
    LLMRouteRequest,
    budget_ratio_from_billing,
    get_route_fallback_models,
    route_llm,
)

# Modello di default (usato da crea_llm() quando chi chiama non passa ne'
# model ne' route_request). Il controllo costo precede la costruzione del client;
# esaurire il tier gratuito non autorizza un fallback a pagamento.
MODELLO_DEFAULT = os.getenv(
    "OPENROUTER_MODEL",
    DEFAULT_FREE_MODEL
)

# Numero di tentativi in caso di errore/rate limit del modello free.
MAX_RETRY = int(os.getenv("LLM_MAX_RETRY", "3"))
LLM_TIMEOUT_SECONDS = max(1, int(os.getenv("LLM_TIMEOUT_SECONDS", "30")))

# Audit 3.3: senza un limite di concorrenza, un tenant (o piu' tenant
# insieme) puo' saturare il budget/rate-limit condiviso del provider LLM.
# Semaforo globale asyncio: usato solo nel percorso async reale
# (genera_risposta_async, il flusso WhatsApp che scala col volume di
# messaggi). I percorsi sync (crew_runner_review.py, crew_runner_report.py)
# sono a basso volume (dashboard/scheduler) e non lo usano.
LLM_CONCURRENCY_SEM = asyncio.Semaphore(int(os.getenv("LLM_MAX_CONCURRENT", "3")))

# Prefissi provider riconosciuti nei model id. I provider sono whitelistati:
# solo quelli che NON addestrano sui dati possono stare nella chain.
def configured_model() -> str:
    return os.getenv("AI_MODEL", "").strip() or os.getenv("OPENROUTER_MODEL", "").strip() or MODELLO_DEFAULT


def ai_configuration_status() -> dict[str, str]:
    """Report local configuration only; never contact a provider or expose keys."""
    model = configured_model()
    try:
        assert_model_allowed(model)
        provider = provider_for_model(model, os.getenv("AI_PROVIDER", "").strip().lower())
        if free_only() and (provider.name not in {"groq", "openrouter"} or os.getenv("AI_BASE_URL", "").strip()):
            raise RuntimeError("Budget EUR 0: provider o endpoint non autorizzato")
        key_env = provider.key_env
        if provider.name == "openai_compatible" or (os.getenv("AI_PROVIDER") and not free_only()):
            key_env = "AI_API_KEY" if os.getenv("AI_API_KEY", "").strip() else provider.key_env
        key = os.getenv(key_env, "").strip()
        if not key:
            return {"status": "non_configurato", "provider": provider.name, "model": model, "reason": f"{key_env} mancante"}
        if provider.requires_base_url and not os.getenv("AI_BASE_URL", "").strip():
            return {"status": "non_configurato", "provider": provider.name, "model": model, "reason": "AI_BASE_URL mancante"}
        provider.client_params(model, key, os.getenv("AI_BASE_URL", "").strip() or None)
        return {"status": "configurato", "provider": provider.name, "model": model, "reason": "verifica remota non eseguita"}
    except RuntimeError:
        return {"status": "non_configurato", "provider": "non_disponibile", "model": model, "reason": "configurazione o policy non valida"}


def crea_llm(
    model: str | None = None,
    temperature: float = 0.4,
    route_request: LLMRouteRequest | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    *,
    provider_context=None,
    reason: str = "generation",
    sync_transport=None,
    async_transport=None,
):
    from src.core.observed_llm import ObservedChatLLM
    from src.core.provider_accounting import ProviderAccountingContext, ProviderAccountingError

    if type(provider_context) is not ProviderAccountingContext:
        raise ProviderAccountingError("AI generation is not authorized or accounting is unavailable")
    expected_task = {
        "customer_message": {"customer_message", "simulator", "onboarding_preview"},
        "review": {"review"},
        "report": {"report"},
        "document_qa": {"document_qa"},
        "intent_classification": {"intent_classification"},
    }
    route_task = route_request.task_type if route_request is not None else provider_context.task_type
    if provider_context.task_type not in expected_task.get(route_task, set()):
        raise ProviderAccountingError("AI generation is not authorized or accounting is unavailable")
    provider_context.assert_http_admission()
    selected_model = model
    if selected_model is None and route_request is not None:
        selected_model = route_llm(route_request).model
    selected_model = selected_model or configured_model()
    policy = resolve_llm_client_policy(selected_model)

    if max_tokens is None:
        max_tokens = int(os.getenv("LLM_MAX_TOKENS", "250"))

    return ObservedChatLLM(
        policy=policy,
        session=provider_context,
        reason=reason,
        temperature=temperature,
        max_tokens=max(1, min(max_tokens, 1000)),
        timeout=min(LLM_TIMEOUT_SECONDS, max(0.1, timeout)) if timeout is not None else LLM_TIMEOUT_SECONDS,
        sync_transport=sync_transport,
        async_transport=async_transport,
    )


__all__ = [
    "LLM_CONCURRENCY_SEM",
    "LLMRoute",
    "LLMRouteRequest",
    "LLM_TIMEOUT_SECONDS",
    "MAX_RETRY",
    "MODELLO_DEFAULT",
    "ai_configuration_status",
    "configured_model",
    "budget_ratio_from_billing",
    "crea_llm",
    "get_route_fallback_models",
    "route_llm",
]
