import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response

from src.core.ai_safety import apply_booking_result, record_ai_attempts, replay_requires_intervention
from src.core.bookings.service import BookingService
from src.core.llm_routing import LLMRoute
from src.models.schemas import MessaggioInput, ProfiloAttivita, RispostaOutput
from tests.provider_accounting_fakes import provider_context_stub


@pytest.fixture
def message():
    return MessaggioInput(testo="Vorrei informazioni sui vostri servizi")


@pytest.mark.parametrize("booking,expected", [
    (None, True), ({"id": "b1", "stato": "in_attesa", "richiede_intervento": False}, True),
    ({"id": "b1", "stato": "confermata", "richiede_intervento": True}, True),
    ({"id": "b1", "stato": "confermata", "richiede_intervento": False}, False),
])
def test_pending_replay_escalates_even_if_sync_failure_mark_was_lost(booking, expected):
    assert replay_requires_intervention(booking) is expected


def test_history_bound_preserves_both_sides_of_customer_conversation():
    from src.core.crew_runner import _bounded_history
    history = _bounded_history([("u" * 2500, "a" * 2500)] * 20)
    assert len(history) == 12
    assert all(len(user) == len(reply) == 2000 for user, reply in history)


@pytest.fixture
def profile():
    return ProfiloAttivita(
        nome="Test",
        tipo_attivita="ristorante",
        tono="cordiale",
        orari="Sempre aperto",
    )


@pytest.mark.parametrize(
    "billing",
    [
        {"ai_accounting_blocked": True},
        {"messages_limit": 10, "messages_used_this_period": 10},
    ],
)
def test_sync_runner_does_not_create_model_when_budget_gate_fails(
    message, profile, billing, monkeypatch
):
    from src.core import crew_runner

    create_crew = MagicMock()
    monkeypatch.setattr(crew_runner, "crea_crew", create_crew)

    with pytest.raises(RuntimeError):
        crew_runner.genera_risposta(message, profile, billing=billing)

    create_crew.assert_not_called()


def test_sync_runner_does_not_create_model_for_oversized_untrusted_input(
    profile, monkeypatch
):
    from src.core import crew_runner

    oversized = MessaggioInput.model_construct(testo="x" * 12001)
    create_crew = MagicMock()
    monkeypatch.setattr(crew_runner, "crea_crew", create_crew)

    with pytest.raises(RuntimeError, match="AI input limit"):
        crew_runner.genera_risposta(oversized, profile)

    create_crew.assert_not_called()


def test_runner_fallback_hides_provider_error_and_never_passes_tools(
    message, profile, monkeypatch, caplog
):
    from src.core import crew_runner

    route = LLMRoute(
        model="groq/primary",
        tier="premium",
        reason="test",
        fallback_models=("groq/fallback",),
    )
    attempts = []

    def make_crew(*args, **kwargs):
        attempts.append(kwargs)
        crew = MagicMock()
        crew.kickoff.side_effect = RuntimeError("provider-secret-marker")
        return crew

    monkeypatch.setattr(crew_runner, "route_llm", lambda _request: route)
    monkeypatch.setattr(crew_runner, "crea_crew", make_crew)
    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError) as exc_info:
        crew_runner.genera_risposta(
            message, profile, tools=[object()], provider_context=provider_context_stub()
        )

    assert "provider-secret-marker" not in str(exc_info.value)
    assert "provider-secret-marker" not in caplog.text
    assert len(attempts) == 1 + len(route.fallback_models)
    assert [attempt["model"] for attempt in attempts] == [
        route.model,
        *route.fallback_models,
    ]
    assert all(attempt["tools"] is None for attempt in attempts)
    assert all(attempt["provider_context"] is not None for attempt in attempts)
    assert [attempt["reason"] for attempt in attempts] == [
        "test", "fallback:test",
    ]


@pytest.mark.asyncio
async def test_async_runner_timeout_is_bounded_and_sanitized(
    message, profile, monkeypatch, caplog
):
    from src.core import crew_runner

    route = LLMRoute(
        model="groq/primary", tier="premium", reason="test", fallback_models=()
    )
    crew = MagicMock()

    async def wait_forever():
        await asyncio.Event().wait()

    crew.kickoff_async = wait_forever
    monkeypatch.setattr(crew_runner, "route_llm", lambda _request: route)
    monkeypatch.setattr(crew_runner, "crea_crew", lambda *args, **kwargs: crew)
    monkeypatch.setattr(crew_runner, "LLM_TIMEOUT_SECONDS", 0.01)
    caplog.set_level(logging.ERROR)

    with pytest.raises(RuntimeError) as exc_info:
        await crew_runner.genera_risposta_async(
            message, profile, provider_context=provider_context_stub(),
        )

    assert "TimeoutError" in str(exc_info.value)
    assert "provider-secret-marker" not in str(exc_info.value)
    assert "provider-secret-marker" not in caplog.text


@pytest.mark.asyncio
async def test_default_ambiguous_intent_classification_does_not_create_provider(
    monkeypatch
):
    from src.core.guardrails import intent_classifier

    monkeypatch.delenv("GUARDRAIL_INTENT_LLM_ENABLED", raising=False)
    create_llm = MagicMock(side_effect=AssertionError("provider must remain off"))
    monkeypatch.setattr(intent_classifier, "crea_llm", create_llm)

    result = await intent_classifier.classifica_intent(
        "Avrei bisogno di capire meglio una cosa particolare"
    )

    assert result.source == "heuristic"
    create_llm.assert_not_called()


@pytest.mark.parametrize(
    "booking, expected, requires_human",
    [
        ({"id": "b1", "stato": "confermata", "external_sync_status": "failed"}, False, True),
        ({"id": "b1", "stato": "confermata", "richiede_intervento": True}, False, True),
        ({"id": "b1", "stato": "in_attesa"}, False, False),
        ({"id": "b1", "stato": "confermata"}, True, False),
    ],
)
def test_booking_result_only_reports_success_for_persisted_confirmation(
    booking, expected, requires_human
):
    response = RispostaOutput(
        risposta="La tua prenotazione è confermata.",
        richiede_umano=False,
        motivo="",
    )

    apply_booking_result(response, booking)

    assert response.richiede_umano is requires_human
    assert (response.risposta == "Prenotazione confermata.") is expected


@pytest.mark.asyncio
async def test_booking_replay_returns_existing_org_scoped_row_before_capacity_checks():
    existing = {"id": "booking-1", "organization_id": "org-1", "stato": "confermata"}
    repo = MagicMock()
    repo.get_booking_for_message = AsyncMock(return_value=existing)
    service = BookingService(repo=repo)
    service._google_slot_occupato = MagicMock(
        side_effect=AssertionError("replay must not recheck capacity")
    )
    service.verifica_disponibilita = MagicMock(
        side_effect=AssertionError("replay must not recheck capacity")
    )

    result = await service.create_booking(
        org_id="org-1",
        source_message_id="message-1",
        nome_cliente="Customer",
        data="2030-01-02",
        ora="19:00",
        coperti=4,
    )

    assert result is existing
    repo.get_booking_for_message.assert_awaited_once_with("org-1", "message-1")
    service._google_slot_occupato.assert_not_called()
    service.verifica_disponibilita.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_postcall_sink_is_disabled():
    with pytest.raises(RuntimeError, match="provider accounting adapter"):
        await record_ai_attempts(
            SimpleNamespace(pool=object()), "org-1", {"attempts": []}, None, None,
        )


@pytest.mark.asyncio
async def test_simulator_rate_limit_prevents_model_and_reservation(
    monkeypatch,
):
    from src.api.routes import simulator

    user = {
        "source": "jwt",
        "organization_id": "org-limited",
        "user_id": "user-limited",
        "auth_user_id": "00000000-0000-0000-0000-000000000001",
    }
    repo = MagicMock()
    repo.get_organization = AsyncMock(return_value={
        "name": "Org",
        "business_profile": {"nome": "Org"},
    })
    repo.reserve_simulation_request = AsyncMock()
    orchestrator = MagicMock()
    orchestrator.orchestrate = AsyncMock()
    monkeypatch.setattr(
        simulator,
        "get_optional_organization_context",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        simulator,
        "get_billing_snapshot",
        AsyncMock(return_value={
            "subscription_status": "active",
            "messages_limit": 100,
            "messages_used_this_period": 0,
        }),
    )
    monkeypatch.setattr(
        simulator,
        "enforce_org_rate_limit",
        AsyncMock(side_effect=HTTPException(status_code=429, detail="limited")),
    )
    monkeypatch.setattr(simulator, "get_orchestrator", MagicMock(return_value=orchestrator))
    monkeypatch.setattr(
        "src.whatsapp.inbound_processor._profile_from_dict",
        lambda *_args, **_kwargs: ProfiloAttivita(
            nome="Org", tipo_attivita="ristorante", tono="cordiale", orari=""
        ),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=repo)),
        state=SimpleNamespace(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await simulator.ricevi_messaggio(
            request,
            MessaggioInput(testo="Ciao"),
            Response(),
        )

    assert exc_info.value.status_code == 429
    simulator.enforce_org_rate_limit.assert_awaited_once_with(
        "org-limited", "ai_simulator", 20, 60
    )
    repo.reserve_simulation_request.assert_not_awaited()
    orchestrator.orchestrate.assert_not_awaited()
