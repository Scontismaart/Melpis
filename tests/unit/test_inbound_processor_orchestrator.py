import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from src.whatsapp.config import AppConfig
from src.whatsapp.inbound_processor import InboundProcessor
from src.core.receptionist.models import OrchestrationOutput


@pytest.fixture
def base_config():
    return AppConfig(
        app_secret="test-secret",
        encryption_key="MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=",
        postgres_dsn="postgresql://test:test@localhost:5432/test",
        verify_token="test-token",
        use_conversation_orchestrator=False,
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
    repo.mark_message_status = AsyncMock()
    repo.get_conversation = AsyncMock(return_value={"ticket_status": "OPEN"})
    repo.get_org_subscription_state = AsyncMock(return_value={"subscription_status": "active"})
    repo.get_org_business_profile = AsyncMock(return_value={"nome": "Pizzeria Bella", "tipo_attivita": "ristorante"})
    repo.list_conversation_messages = AsyncMock(return_value=[])

    return {"repo": repo, "service": service, "booking_service": booking_service, "quota": quota}


@pytest.mark.asyncio
async def test_inbound_processor_flag_disabled_uses_legacy(
    base_config, mock_deps, mock_provider_context_open,
):
    """Con use_conversation_orchestrator=False, l'orchestrator NON deve essere invocato."""
    mock_orchestrator = AsyncMock()
    processor = InboundProcessor(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
        orchestrator=mock_orchestrator,
    )
    assert processor.use_orchestrator is False

    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content_text": "Ciao, vorrei info",
        "content": {"from": "+393401112233"},
        "canale": "whatsapp",
    }

    with patch("src.whatsapp.inbound_processor.load_tenant_config") as mock_tenant, \
         patch("src.whatsapp.inbound_processor.classifica_intent") as mock_intent, \
         patch("src.whatsapp.inbound_processor.genera_risposta_async") as mock_gen, \
         patch("src.whatsapp.inbound_processor.valida_risposta") as mock_valida, \
         patch("src.whatsapp.inbound_processor.route_llm") as mock_route, \
         patch("src.core.billing.quota_reservations.validate_final_response", return_value=object()), \
         patch.object(processor, "_send_ai_reply", new_callable=AsyncMock) as mock_send, \
         patch.object(processor, "_finalize_message", new_callable=AsyncMock):

        tenant_cfg = MagicMock()
        tenant_cfg.business_profile = {"nome": "Pizzeria Bella"}
        mock_tenant.return_value = tenant_cfg

        intent_obj = MagicMock()
        intent_obj.intent = "generico"
        intent_obj.source = "rule"
        mock_intent.return_value = intent_obj

        risp_obj = MagicMock()
        risp_obj.risposta = "Siamo aperti dalle 19:00"
        risp_obj.richiede_umano = False
        risp_obj.motivo = ""
        risp_obj.prenotazione = None
        mock_gen.return_value = risp_obj

        esito_obj = MagicMock()
        esito_obj.azione = "none"
        mock_valida.return_value = esito_obj

        route_obj = MagicMock()
        route_obj.model = "gpt-4o"
        route_obj.tier = "standard"
        route_obj.reason = "default"
        mock_route.return_value = route_obj

        mock_send.return_value = {"wam_id": "meta-123"}

        await processor._process_one(msg)

        # L'orchestrator NON è stato chiamato
        mock_orchestrator.orchestrate.assert_not_called()
        # Il generatore legacy è stato chiamato
        assert mock_gen.await_count == 1


@pytest.mark.asyncio
async def test_inbound_processor_flag_enabled_uses_orchestrator(base_config, mock_deps):
    """Con use_conversation_orchestrator=True, l'orchestrator DEVE essere invocato."""
    base_config.use_conversation_orchestrator = True
    mock_orchestrator = AsyncMock()
    fake_output = OrchestrationOutput(
        response_text="Benvenuto! Da noi trovi ottime pizze.",
        richiede_umano=False,
        motivo_richiesta_umano=None,
        intent="faq",
        source="llm",
    )
    fake_output._quota_final = object()
    mock_orchestrator.orchestrate.return_value = fake_output

    processor = InboundProcessor(
        app_config=base_config,
        repo=mock_deps["repo"],
        service=mock_deps["service"],
        booking_service=mock_deps["booking_service"],
        orchestrator=mock_orchestrator,
    )
    assert processor.use_orchestrator is True

    msg = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content_text": "Cosa fate?",
        "content": {"from": "+393401112233"},
        "canale": "whatsapp",
    }

    with patch("src.whatsapp.inbound_processor.load_tenant_config") as mock_tenant, \
         patch("src.whatsapp.inbound_processor.genera_risposta_async") as mock_gen, \
         patch.object(processor, "_send_ai_reply", new_callable=AsyncMock) as mock_send, \
         patch.object(processor, "_finalize_message", new_callable=AsyncMock) as mock_fin:

        tenant_cfg = MagicMock()
        tenant_cfg.business_profile = {"nome": "Pizzeria Bella"}
        mock_tenant.return_value = tenant_cfg
        mock_send.return_value = {"wam_id": "meta-456"}

        await processor._process_one(msg)

        # L'orchestrator DEVE essere chiamato con OrchestrationInput
        assert mock_orchestrator.orchestrate.await_count == 1
        call_input = mock_orchestrator.orchestrate.await_args[0][0]
        assert call_input.text == "Cosa fate?"
        assert call_input.channel == "whatsapp"
        assert call_input.is_simulation is False
        assert call_input.record_billing_usage is True

        # Il legacy LLM runner NON deve essere chiamato
        mock_gen.assert_not_called()

        # A04 atomically persists the validated final response via reservation commit.
        mock_deps["quota"].commit.assert_awaited_once()
        mock_send.assert_awaited_once()
        mock_fin.assert_awaited_once_with(msg["id"], handling_type="ai_handled", meta_message_id="meta-456", organization_id=msg["organization_id"])
