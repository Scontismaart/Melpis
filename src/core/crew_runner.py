"""
crew_runner.py
--------------
Punto d'ingresso unico verso la logica agente. Il backend (FastAPI, prossimo
step) chiama SOLO questa funzione — non conosce CrewAI, non conosce prompt,
non conosce il provider LLM. Questo disaccoppiamento è quello che ci permette
di cambiare tutto il resto (UI, canale, provider LLM) senza toccare i
moduli a monte.
"""

import asyncio
import time
from src.agents.responder_agent import crea_crew
from src.core.llm_config import (
    LLM_CONCURRENCY_SEM,
    LLMRouteRequest,
    budget_ratio_from_billing,
    route_llm,
)
from src.models.schemas import CanaleMessaggio, MessaggioInput, ProfiloAttivita, RispostaOutput
from src.core.ai_safety import AI_INPUT_MAX_CHARS
from src.core.llm_config import LLM_TIMEOUT_SECONDS
from src.core.billing.suspension import assert_ai_generation_allowed
from src.core.provider_accounting import ProviderAccountingContext, ProviderAccountingError


def _execution_gate(messaggio, provider_context):
    if len(messaggio.testo) > AI_INPUT_MAX_CHARS:
        raise RuntimeError("AI input limit exceeded")
    if type(provider_context) is not ProviderAccountingContext:
        raise ProviderAccountingError("AI generation is not authorized or accounting is unavailable")
    if provider_context.task_type not in {"customer_message", "simulator", "onboarding_preview"}:
        raise ProviderAccountingError("AI generation is not authorized or accounting is unavailable")
    provider_context.assert_http_admission()
    assert_ai_generation_allowed(provider_context.billing_snapshot)


def _bounded_history(history):
    return [(str(user_text)[:2000], str(reply_text)[:2000]) for user_text, reply_text in (history or [])[-12:]]


def _route_request_for_message(
    messaggio: MessaggioInput, billing: dict, intent: str | None = None
) -> LLMRouteRequest:
    return LLMRouteRequest(
        task_type="customer_message",
        user_text=messaggio.testo,
        remaining_budget_ratio=budget_ratio_from_billing(billing),
        intent=intent,
    )


def _validate_output(risultato) -> RispostaOutput:
    output = risultato.pydantic
    if output is None or not isinstance(output, RispostaOutput):
        raise RuntimeError(
            "Il modello non ha restituito un output conforme a RispostaOutput. "
            "Riprova, o verifica che il modello configurato in llm_config.py "
            "sia ancora disponibile sul provider LLM configurato."
        )
    return output


async def _published_booking_catalog(messaggio, profilo, provider_context):
    from src.core.db.inbound_worker_provenance import private_booking_worker_enabled
    if (not private_booking_worker_enabled() or messaggio.canale != CanaleMessaggio.WHATSAPP
            or provider_context.task_type != 'customer_message'
            or provider_context.worker_message_id is None):
        return None
    from src.core.bookings.catalog_contracts import validate_catalog_json
    from src.core.db.repositories.booking_catalog_repo import BookingCatalogRepository, canonical
    from src.core.db.repositories.booking_repo import BookingRepository

    org = provider_context.organization_id
    state = await BookingCatalogRepository(BookingRepository(provider_context.pool)).read(org)
    if state['published'] is None:
        return None
    catalog = validate_catalog_json(canonical(state['published']), organization_id=org)
    if catalog.catalog.sector != state['organization_sector'] or catalog.catalog.sector != profilo.verticale:
        raise ProviderAccountingError('Published booking catalog does not match current organization sector')
    return catalog


def _validate_structured_output(result, catalog, organization_id):
    from src.core.bookings.conversation_state import _bounded_json, parse_structured_response_json

    raw = _bounded_json(result.raw)
    if (not isinstance(raw, dict) or 'prenotazione' in raw
            or set(raw) - (set(RispostaOutput.model_fields) | {'booking_state_proposal'})):
        raise ValueError('Invalid structured booking response')
    if 'booking_state_proposal' not in raw:
        return RispostaOutput.model_validate(raw)
    return parse_structured_response_json(result.raw, catalog=catalog, organization_id=organization_id)


def genera_risposta(
    messaggio: MessaggioInput,
    profilo: ProfiloAttivita,
    cronologia: list[tuple[str, str]] | None = None,
    billing: dict | None = None,
    intent: str | None = None,
    variante: str = "control",
    contesto_disponibilita: str = "",
    tentativi_falliti: int = 0,
    provider_context: ProviderAccountingContext | None = None,
    tools: list | None = None,
) -> RispostaOutput:
    """Esegue la crew su un singolo messaggio e restituisce l'output
    strutturato e validato.

    Solleva eccezione se, dopo i retry interni di CrewAI/LiteLLM, il
    modello non restituisce un output conforme allo schema: meglio
    un errore esplicito che una risposta silenziosamente sbagliata
    mandata a un cliente reale.
    """
    _execution_gate(messaggio, provider_context)
    billing = provider_context.billing_snapshot
    route_request = _route_request_for_message(messaggio, billing, intent)
    route = route_llm(route_request)
    errors: list[str] = []
    for model_index, model in enumerate([route.model, *route.fallback_models][:3]):
        try:
            attempt_reason = route.reason if model_index == 0 else f"fallback:{route.reason}"
            crew = crea_crew(profilo, messaggio, _bounded_history(cronologia), route_request=route_request,
                             model=model, variante=variante,
                             contesto_disponibilita=contesto_disponibilita,
                             tentativi_falliti=tentativi_falliti, tools=None,
                             provider_context=provider_context, reason=attempt_reason)
            out = _validate_output(crew.kickoff())
            return out
        except Exception as exc:
            errors.append(type(exc).__name__)
    raise RuntimeError("Tutti i modelli configurati hanno fallito. " + " | ".join(errors)) from None


async def genera_risposta_async(
    messaggio: MessaggioInput,
    profilo: ProfiloAttivita,
    cronologia: list[tuple[str, str]] | None = None,
    billing: dict | None = None,
    contesto_documenti: str = "",
    intent: str | None = None,
    variante: str = "control",
    contesto_disponibilita: str = "",
    tentativi_falliti: int = 0,
    provider_context: ProviderAccountingContext | None = None,
    tools: list | None = None,
) -> RispostaOutput:
    """Versione asincrona di genera_risposta per essere usata da route
    FastAPI che girano in un event loop già attivo.

    Audit 3.3: limitata dal semaforo globale LLM_CONCURRENCY_SEM per non
    saturare il rate-limit/budget condiviso del provider LLM quando piu'
    tenant generano risposte in parallelo."""
    _execution_gate(messaggio, provider_context)
    booking_catalog = await _published_booking_catalog(messaggio, profilo, provider_context)
    billing = provider_context.billing_snapshot
    route_request = _route_request_for_message(messaggio, billing, intent)
    route = route_llm(route_request)
    errors: list[str] = []
    inizio = time.monotonic()
    async with LLM_CONCURRENCY_SEM:
        for model_index, model in enumerate([route.model, *route.fallback_models][:3]):
            try:
                attempt_reason = route.reason if model_index == 0 else f"fallback:{route.reason}"
                crew = crea_crew(profilo, messaggio, cronologia=_bounded_history(cronologia),
                                 route_request=route_request, model=model,
                                 contesto_documenti=contesto_documenti[:12000], variante=variante,
                                 contesto_disponibilita=contesto_disponibilita,
                                 tentativi_falliti=tentativi_falliti, tools=None,
                                 provider_context=provider_context, reason=attempt_reason,
                                 **({'booking_catalog': booking_catalog} if booking_catalog is not None else {}))
                remaining = LLM_TIMEOUT_SECONDS - (time.monotonic() - inizio)
                if remaining <= 0:
                    break
                result = await asyncio.wait_for(crew.kickoff_async(), timeout=remaining)
                if booking_catalog is not None:
                    return _validate_structured_output(result, booking_catalog, provider_context.organization_id)
                return _validate_output(result)
            except Exception as exc:
                errors.append(type(exc).__name__)
    raise RuntimeError("Tutti i modelli configurati hanno fallito. " + " | ".join(errors)) from None
