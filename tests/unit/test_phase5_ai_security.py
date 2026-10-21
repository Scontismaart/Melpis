"""Offline regression tests for the Phase 5 application action boundary."""
import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.inbound.legacy_pipeline import LegacyInboundPipeline
from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
from src.core.receptionist.models import OrchestrationInput
from src.core.provider_accounting import ProviderAccountingError
from src.integrations.airtable.wiring import select_airtable_tools
from src.models.schemas import DatiPrenotazione, RispostaOutput
from tests.provider_accounting_fakes import provider_context_stub


async def execute(path, *, blocked=False, error=None, simulation=False, replay_intervention=False):
    repo = AsyncMock()
    repo.check_booking_exists.return_value = replay_intervention
    repo.get_booking_for_message.return_value = {"id": "existing", "richiede_intervento": True}
    repo.list_conversation_messages.return_value = []
    billing = {"subscription_status": "active", "messages_limit": 1000,
               "messages_used_this_period": 0}
    repo.get_org_subscription_state.return_value = billing
    booking = AsyncMock()
    booking.create_booking.return_value = {"id": "test-booking"}
    booking.create_booking.side_effect = error
    booking.verifica_disponibilita.return_value = {}
    profile = {"nome": "Test", "tipo_attivita": "ristorante", "verticale": "ristorante"}
    repo.get_org_business_profile.return_value = profile
    output = RispostaOutput(
        risposta="Il menu costa 15 euro." if blocked else "Prenotazione confermata!",
        richiede_umano=False, motivo="booking", categoria="ristorante",
        prenotazione=DatiPrenotazione(data="2026-12-10", ora="20:00", coperti=2,
                                     telefono="model-selected-customer"))
    org, message = uuid.uuid4(), uuid.uuid4()
    if not simulation:
        context = provider_context_stub(organization_id=org)
        stack = ExitStack()
        stack.enter_context(patch(
            "src.core.provider_accounting.ProviderAccountingContext.open",
            new_callable=AsyncMock,
            return_value=context,
        ))
    else:
        stack = ExitStack()
    module = "src.core.receptionist.conversation_orchestrator" if path == "orchestrator" else "src.core.inbound.legacy_pipeline"
    with stack:
        stack.enter_context(patch("src.core.guardrails.faq_cache.cache_enabled", return_value=False))
        stack.enter_context(patch(module + ".genera_risposta_async", AsyncMock(return_value=output)))
        stack.enter_context(patch(module + ".classifica_intent", AsyncMock(return_value=MagicMock(intent="booking", source="rule", confidence=1.0))))
        stack.enter_context(patch(module + ".recupera_contesto_documenti", AsyncMock(return_value=MagicMock(testo="", chunks=[]))))
        if path == "orchestrator":
            engine = ConversationOrchestrator(org_repo=repo, doc_repo=repo,
                billing_repo=repo, conv_repo=repo, booking_service=booking)
            stack.enter_context(patch.object(engine, "_prefetch_availability", AsyncMock(return_value="")))
            result = await engine.orchestrate(OrchestrationInput(
                organization_id=org, message_id=message, conversation_id="trusted-conversation",
                text="Prenota per due", sender_phone="trusted-sender", business_profile=profile,
                billing_state=billing, is_simulation=simulation,
                auth_user_id=uuid.uuid4() if not simulation else None))
        else:
            engine = LegacyInboundPipeline(repo, booking)
            result = await engine.execute(org_id=org, msg={"id": message, "conversation_id": "trusted-conversation"},
                text="Prenota per due", content={"from": "trusted-sender"}, canale="whatsapp",
                business_profile_raw=profile, state=billing, claim_result={"status": "claimed", "ai_reply_cache": None})
    return result, booking


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["legacy", "orchestrator"])
async def test_blocked_output_never_books(path):
    result, booking = await execute(path, blocked=True)
    booking.create_booking.assert_not_awaited()
    assert result.richiede_umano


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["legacy", "orchestrator"])
async def test_oversized_input_rejected_without_sensitive_exception_payload(path):
    text = "sensitive-message-marker" * 600
    repo, booking = AsyncMock(), AsyncMock()
    with pytest.raises(RuntimeError, match="^AI input rejected$") as failure:
        if path == "orchestrator":
            engine = ConversationOrchestrator(org_repo=repo, doc_repo=repo,
                billing_repo=repo, conv_repo=repo, booking_service=booking)
            await engine.orchestrate(OrchestrationInput(organization_id=uuid.uuid4(), text=text))
        else:
            await LegacyInboundPipeline(repo, booking).execute(org_id=uuid.uuid4(), msg={},
                text=text, content={}, canale="whatsapp", business_profile_raw={},
                state=None, claim_result={})
    assert "sensitive-message-marker" not in str(failure.value)
    assert not repo.mock_calls
    booking.create_booking.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["legacy", "orchestrator"])
@pytest.mark.parametrize("error", [RuntimeError("private-provider-detail"), TimeoutError(), TypeError("after-write")])
async def test_booking_failure_never_confirms_or_retries(path, error, caplog):
    result, booking = await execute(path, error=error)
    # The AI engines are no longer allowed to reach this side effect. A04's
    # committed inbound continuation owns the booking transaction instead.
    booking.create_booking.assert_not_awaited()
    assert "Prenotazione confermata" not in result.response_text
    assert result.richiede_umano
    assert "private-provider-detail" not in caplog.text
    assert "after-write" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["legacy", "orchestrator"])
async def test_model_selected_phone_cannot_authorize_direct_booking(path):
    _, booking = await execute(path)
    booking.create_booking.assert_not_awaited()


@pytest.mark.asyncio
async def test_crm_ai_off_even_for_booking_and_injected_factory(monkeypatch):
    monkeypatch.setenv("CRM_AI_TOOLS_ENABLED", "true")
    factory = AsyncMock(return_value=[MagicMock(name="airtable_update_customer")])
    assert await select_airtable_tools(intent="booking", budget_ratio=1.0,
        organization_id=uuid.uuid4(), factory=factory) == []
    factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_simulator_never_books(monkeypatch):
    monkeypatch.setattr(
        "src.core.provider_accounting.ProviderAccountingContext.open",
        AsyncMock(side_effect=ProviderAccountingError("provider context unavailable")),
    )
    with pytest.raises(ProviderAccountingError):
        await execute("orchestrator", simulation=True)


@pytest.mark.asyncio
async def test_booking_replay_preserves_staff_intervention():
    result, booking = await execute("legacy", replay_intervention=True)
    assert result.richiede_umano
    booking.create_booking.assert_not_awaited()
    assert "confermata" not in result.response_text
