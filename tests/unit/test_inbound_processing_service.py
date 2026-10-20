import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.inbound.service import InboundProcessingService
from src.core.receptionist.models import OrchestrationOutput
from src.whatsapp.config import AppConfig


@pytest.fixture
def base_config():
    return AppConfig(
        app_secret="test-secret",
        encryption_key="MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=",
        postgres_dsn="postgresql://test:test@localhost:5432/test",
        verify_token="test-token",
        use_conversation_orchestrator=True,
    )


@pytest.fixture
def mock_deps(monkeypatch):
    repo = AsyncMock()
    service = AsyncMock()
    booking_service = AsyncMock()

    class QuotaReservationsStub:
        def __init__(self):
            self.reserve = AsyncMock(return_value={"status": "reserved", "ticket": object()})
            self.commit = AsyncMock()
            self.release = AsyncMock()

    quota = QuotaReservationsStub()
    monkeypatch.setattr(
        "src.core.billing.quota_reservations.QuotaReservations",
        lambda _pool: quota,
    )

    service.check_opt_out = AsyncMock(return_value={"is_opt_out": False})
    service.check_human_request = AsyncMock(return_value=False)
    service.fast_path_match = AsyncMock(return_value=None)
    booking_service.handle_reminder_reply = AsyncMock(return_value=None)

    repo.claim_message_for_processing = AsyncMock(
        return_value={"status": "claimed", "ai_reply_cache": None}
    )
    repo.get_outbound_dedup = AsyncMock(return_value=None)
    repo.check_booking_exists = AsyncMock(return_value=False)
    repo.save_ai_reply = AsyncMock()
    repo.save_outbound_dedup = AsyncMock()
    repo.mark_message_sent = AsyncMock()
    repo.try_mark_replied = AsyncMock(return_value=True)
    repo.get_conversation = AsyncMock(return_value={"ticket_status": "AI_ACTIVE"})
    repo.get_org_subscription_state = AsyncMock(return_value={"subscription_status": "active"})
    repo.get_org_business_profile = AsyncMock(return_value={"nome": "Pizzeria Da Mario"})
    repo.list_conversation_messages = AsyncMock(return_value=[])
    repo.get_or_create_contact = AsyncMock(return_value={"id": uuid.uuid4(), "ai_disclosure_sent_at": "2026-09-01"})

    return {"repo": repo, "service": service, "booking_service": booking_service, "quota": quota}


@pytest.mark.asyncio
async def test_step1_claim_status_yields_or_ignores(base_config, mock_deps):
    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
    )

    msg = {"id": uuid.uuid4(), "organization_id": uuid.uuid4(), "content_text": "Ciao"}

    # Not found
    mock_deps["repo"].claim_message_for_processing.return_value = {"status": "not_found"}
    res = await inbound_svc.process_message(msg)
    assert res.action == "ignored"

    # Currently processing
    mock_deps["repo"].claim_message_for_processing.return_value = {"status": "currently_processing"}
    res = await inbound_svc.process_message(msg)
    assert res.action == "yielded"


@pytest.mark.asyncio
async def test_step2_opt_out_fail_closed(base_config, mock_deps):
    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
    )

    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "content_text": "STOP",
        "content": {"from": "+393401122334"},
    }

    mock_deps["service"].check_opt_out.return_value = {"is_opt_out": True}

    res = await inbound_svc.process_message(msg)
    assert res.action == "handled"
    assert res.handling_type == "opt_out"
    mock_deps["repo"].record_consent_event.assert_awaited_once()
    mock_deps["repo"].try_mark_replied.assert_awaited_once_with(
        msg["id"], handling_type="opt_out", organization_id=msg["organization_id"]
    )
    mock_deps["repo"].claim_message_for_processing.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_is_recorded_when_quota_is_exhausted(base_config, mock_deps):
    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
    )
    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "content_text": "STOP",
        "content": {"from": "+393401122334"},
    }
    mock_deps["service"].check_opt_out.return_value = {"is_opt_out": True}
    mock_deps["quota"].reserve.return_value = {"status": "quota_exceeded"}
    outcome = await inbound_svc.process_message(msg)
    assert outcome.handling_type == "opt_out"
    mock_deps["repo"].record_consent_event.assert_awaited_once()
    mock_deps["repo"].claim_message_for_processing.assert_not_awaited()


@pytest.mark.asyncio
async def test_step3_human_request_escalation(base_config, mock_deps):
    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
    )

    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content_text": "Voglio parlare con una persona reale",
        "content": {"from": "+393401122334"},
    }

    mock_deps["service"].check_human_request.return_value = True

    with patch("src.core.inbound.service.load_tenant_config") as mock_tc, \
         patch.object(inbound_svc, "_send_reply", new_callable=AsyncMock) as mock_send:
        mock_tc.return_value = MagicMock()
        mock_send.return_value = {"wam_id": "meta-wait-123"}

        res = await inbound_svc.process_message(msg)
        assert res.action == "handled"
        assert res.handling_type == "escalated"
        mock_deps["repo"].escalate_to_human.assert_awaited_once()
        mock_deps["repo"].mark_message_sent.assert_awaited_once_with(
            msg["id"], "meta-wait-123", msg["organization_id"]
        )


@pytest.mark.asyncio
async def test_step7_ticket_claimed_by_operator_skips_ai(base_config, mock_deps):
    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
    )

    mock_deps["repo"].get_conversation.return_value = {"ticket_status": "CLAIMED"}

    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content_text": "Salve",
    }

    res = await inbound_svc.process_message(msg)
    assert res.action == "handled"
    assert res.handling_type == "claimed_by_operator"
    mock_deps["repo"].try_mark_replied.assert_awaited_once_with(
        msg["id"], handling_type="claimed_by_operator", organization_id=msg["organization_id"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("canale", ["whatsapp", "instagram"])
async def test_step11_12_orchestration_and_send(base_config, mock_deps, canale):
    mock_orchestrator = AsyncMock()
    mock_orchestrator.orchestrate.return_value = OrchestrationOutput(
        response_text="Siamo aperti tutti i giorni dalle 19:00.",
        richiede_umano=False,
        intent="faq",
        source="llm",
    )
    mock_orchestrator.orchestrate.return_value._quota_final = object()

    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
        orchestrator=mock_orchestrator,
    )

    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content_text": "A che ora aprite?",
        "content": {"from": "+393401122334"},
        "canale": canale,
    }

    with patch("src.core.inbound.service.load_tenant_config") as mock_tc, \
         patch.object(inbound_svc, "_send_reply", new_callable=AsyncMock) as mock_send:
        mock_tc.return_value = MagicMock()
        mock_send.return_value = {"wam_id": "meta-reply-456"}

        res = await inbound_svc.process_message(msg)
        assert res.action == "handled"
        assert res.handling_type == "ai_handled"
        assert res.meta_message_id == "meta-reply-456"

        mock_orchestrator.orchestrate.assert_awaited_once()
        orchestration_input = mock_orchestrator.orchestrate.await_args.args[0]
        assert orchestration_input.channel == canale
        quota_operation = mock_deps["quota"].reserve.await_args.args[0]
        assert quota_operation.channel == "inbound"
        mock_deps["repo"].save_outbound_dedup.assert_awaited_once()
        mock_deps["repo"].mark_message_sent.assert_awaited_once_with(
            msg["id"], "meta-reply-456", msg["organization_id"]
        )
        mock_deps["repo"].try_mark_replied.assert_awaited_once_with(
            msg["id"], handling_type="ai_handled", organization_id=msg["organization_id"]
        )


@pytest.mark.asyncio
async def test_cached_inbound_reply_fails_closed_without_a04_replay(base_config, mock_deps):
    mock_deps["repo"].claim_message_for_processing.return_value = {
        "status": "claimed",
        "ai_reply_cache": {"text": "legacy unverified reply", "richiede_umano": False},
    }
    orchestrator = AsyncMock()
    orchestrator.orchestrate.return_value = OrchestrationOutput(
        response_text="Nuova risposta valida.", richiede_umano=False,
        intent="faq", source="llm",
    )
    orchestrator.orchestrate.return_value._quota_final = object()
    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
        orchestrator=orchestrator,
    )
    msg = {
        "id": uuid.uuid4(), "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(), "content_text": "Domanda?",
        "content": {"from": "+393401122334"}, "canale": "whatsapp",
    }
    with patch("src.core.inbound.service.load_tenant_config") as mock_tc, \
         patch.object(inbound_svc, "_send_reply", new_callable=AsyncMock) as mock_send:
        mock_tc.return_value = MagicMock()
        mock_send.return_value = {"wam_id": "meta-retry-verified"}
        result = await inbound_svc.process_message(msg)

    assert result.action == "error"
    assert result.handling_type == "quota_reconciliation_required"
    mock_deps["quota"].reserve.assert_awaited_once()
    orchestrator.orchestrate.assert_not_awaited()
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_step1_quota_exceeded_hard_cap_bill_001(base_config, mock_deps):
    """Verifica BILL-001 (Invariante 8):
    Se un tenant supera messages_limit, claim_message_and_check_quota restituisce
    status='quota_exceeded'. Il sistema deve bloccare immediatamente ogni chiamata LLM,
    inviare la risposta di cortesia 'troppe richieste', scalare ad operatore umano
    e finalizzare il messaggio come quota_exceeded (hard kill-switch).
    """
    mock_orchestrator = AsyncMock()

    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
        orchestrator=mock_orchestrator,
    )

    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content_text": "Vorrei informazioni sul servizio",
        "content": {"from": "+393401122334"},
        "canale": "whatsapp",
    }

    mock_deps["quota"].reserve.return_value = {"status": "quota_exceeded"}

    with patch("src.core.inbound.service.load_tenant_config") as mock_tc, \
         patch.object(inbound_svc, "_send_reply", new_callable=AsyncMock) as mock_send:
        mock_tc.return_value = MagicMock()
        mock_send.return_value = {"wam_id": "meta-quota-999"}

        res = await inbound_svc.process_message(msg)

        assert res.action == "handled"
        assert res.handling_type == "quota_exceeded"

        # AI Orchestrator NEVER called (0 token/LLM spend)
        mock_orchestrator.orchestrate.assert_not_awaited()

        # Courtesy notification sent
        mock_send.assert_awaited_once()
        assert "molte richieste" in mock_send.await_args[0][4]

        # Escalated and finalized
        mock_deps["repo"].escalate_to_human.assert_awaited_once()
        mock_deps["repo"].try_mark_replied.assert_awaited_once_with(
            msg["id"], handling_type="quota_exceeded", organization_id=msg["organization_id"]
        )


@pytest.mark.asyncio
async def test_quota_exceeded_escalates_even_if_send_reply_fails(base_config, mock_deps):
    """Verifica che se l'invio del messaggio di cortesia fallisce (es. errore rete/Meta),
    l'escalation all'operatore umano avvenga comunque e il messaggio sia finalizzato come quota_exceeded.
    """
    mock_orchestrator = AsyncMock()

    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
        orchestrator=mock_orchestrator,
    )

    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content_text": "Vorrei prenotare",
        "content": {"from": "+393401122334"},
        "canale": "whatsapp",
    }

    mock_deps["quota"].reserve.return_value = {"status": "quota_exceeded"}

    with patch("src.core.inbound.service.load_tenant_config") as mock_tc, \
         patch.object(inbound_svc, "_send_reply", new_callable=AsyncMock) as mock_send:
        mock_tc.return_value = MagicMock()
        mock_send.side_effect = RuntimeError("Meta API unreachable")

        res = await inbound_svc.process_message(msg)

        assert res.action == "handled"
        assert res.handling_type == "quota_exceeded"

        # AI never called
        mock_orchestrator.orchestrate.assert_not_awaited()

        # Send was attempted and failed
        mock_send.assert_awaited_once()

        # Escalation and message finalization still succeeded
        mock_deps["repo"].escalate_to_human.assert_awaited_once_with(
            str(msg["conversation_id"]), msg["organization_id"]
        )
        mock_deps["repo"].try_mark_replied.assert_awaited_once_with(
            msg["id"], handling_type="quota_exceeded", organization_id=msg["organization_id"]
        )


@pytest.mark.asyncio
async def test_quota_exceeded_marks_escalation_failed_on_escalate_error(base_config, mock_deps):
    """Se la transizione staff fallisce, conserva un marker retryable e non dichiara handled.
    """
    mock_orchestrator = AsyncMock()

    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
        orchestrator=mock_orchestrator,
    )

    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content_text": "Vorrei info",
        "content": {"from": "+393401122334"},
        "canale": "whatsapp",
    }

    mock_deps["quota"].reserve.return_value = {"status": "quota_exceeded"}
    mock_deps["repo"].escalate_to_human.side_effect = RuntimeError("DB connection timeout during escalation")

    with patch("src.core.inbound.service.load_tenant_config") as mock_tc, \
         patch.object(inbound_svc, "_send_reply", new_callable=AsyncMock) as mock_send:
        mock_tc.return_value = MagicMock()
        mock_send.return_value = {"wam_id": "meta-quota-888"}

        res = await inbound_svc.process_message(msg)

        assert res.action == "error"
        assert res.handling_type == "escalation_failed"

        mock_deps["repo"].try_mark_replied.assert_not_awaited()
        mock_deps["repo"].record_processing_failure.assert_awaited_once_with(
            msg["id"], msg["organization_id"], "escalation_failed"
        )


@pytest.mark.asyncio
async def test_step11_heartbeat_cancelled_in_finally_scenario_5(base_config, mock_deps):
    """Verifica Scenario 5: Task zombie di heartbeat.
    Durante l'orchestrazione cognitiva (chiamata LLM potenzialmente lenta),
    un heartbeat_task background invia heartbeat al DB.
    Se l'orchestrazione fallisce o crasha con un'eccezione imprevista,
    il blocco finally DEVE cancellare il heartbeat_task garantendo 0 task zombie.
    """
    import asyncio

    heartbeat_task_ref = None

    class CrashingOrchestrator:
        async def orchestrate(self, req):
            # Cerca il task di heartbeat attivo nell'event loop
            nonlocal heartbeat_task_ref
            current = asyncio.current_task()
            all_tasks = asyncio.all_tasks()
            for t in all_tasks:
                if t is not current and not t.done():
                    heartbeat_task_ref = t
                    break
            # Simula un crash imprevisto durante l'inferenza LLM
            raise RuntimeError("Simulated LLM network failure")

    inbound_svc = InboundProcessingService(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
        orchestrator=CrashingOrchestrator(),
    )

    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content_text": "Test heartbeat cancellation",
        "content": {"from": "+393401122334"},
        "canale": "whatsapp",
    }

    with patch("src.core.inbound.service.load_tenant_config") as mock_tc:
        mock_tc.return_value = MagicMock()

        # Errori provider sono convertiti in un esito strutturato, ma il lease
        # heartbeat deve comunque essere cancellato.
        outcome = await inbound_svc.process_message(msg)
        assert outcome.action == "error"
        assert outcome.handling_type == "ai_generation_failed"

        # Il heartbeat task è stato cancellato nel finally
        assert heartbeat_task_ref is not None, "Heartbeat task was not detected"
        # In asyncio, task.cancel() sets cancelling state immediately and cancelled() once scheduled
        assert (
            (hasattr(heartbeat_task_ref, "cancelling") and heartbeat_task_ref.cancelling() > 0)
            or heartbeat_task_ref.cancelled()
            or heartbeat_task_ref.done()
        ), "Heartbeat task must be cancelled in finally block"
        # Yield to event loop to confirm clean termination
        await asyncio.sleep(0)
        assert heartbeat_task_ref.cancelled() or heartbeat_task_ref.done()
