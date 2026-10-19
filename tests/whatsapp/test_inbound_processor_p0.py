import contextlib
import uuid
from unittest.mock import AsyncMock, patch
from src.whatsapp.inbound_processor import InboundProcessor

class TestP0Blockers:
    async def test_booking_idempotency_on_crash(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg,
        mock_provider_context_open,
    ):
        from src.core.bookings.service import BookingService
        from src.models.schemas import RispostaOutput
        mock_repo.get_booking_settings = AsyncMock(return_value={"capienze_orarie": {"12:00": 10}})
        mock_repo.list_bookings = AsyncMock(return_value=[])
        mock_repo.create_booking = AsyncMock(return_value={"id": uuid.uuid4()})
        mock_repo.get_outbound_dedup = AsyncMock(return_value=None)
        mock_repo.claim_message_and_check_quota = AsyncMock(return_value={"status": "claimed", "ai_reply_cache": None, "billed_at": None, "sent_at": None, "quota_exceeded_at": None, "processing_at": None})
        # AsyncMock blanket ritorna un truthy MagicMock: senza configurarlo
        # il processor prende il ramo "booking exists" e non arriva mai a
        # create_booking.
        mock_repo.check_booking_exists = AsyncMock(return_value=False)
        # slot_lock su un AsyncMock restituirebbe una coroutine (non un
        # context manager): replica il comportamento del repo reale.
        mock_repo.slot_lock = lambda *a, **k: contextlib.nullcontext()

        booking_service = BookingService(repo=mock_repo)
        
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Prenotato!", richiede_umano=False, motivo="", categoria="info",
                 prenotazione={"nome_cliente": "Test", "telefono": "123", "data": "2026-10-10", "ora": "12:00", "coperti": 2}
             ))):
            processor = InboundProcessor(app_config, mock_repo, mock_service, booking_service=booking_service)
            await processor.process_next_batch()

        mock_repo.create_booking.assert_awaited_once()
        kwargs = mock_repo.create_booking.await_args.kwargs
        assert kwargs["source_message_id"] == str(sample_msg["id"])

    async def test_message_retained_on_api_failure(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg,
        mock_provider_context_open,
    ):
        from src.models.schemas import RispostaOutput
        mock_service.send_whatsapp_message = AsyncMock(side_effect=Exception("API Down"))
        mock_repo.save_outbound_dedup = AsyncMock()
        mock_repo.try_mark_replied = AsyncMock(return_value=True) # it shouldn't be called for 'ai_handled' though
        mock_repo.get_outbound_dedup = AsyncMock(return_value=None)
        mock_repo.claim_message_and_check_quota = AsyncMock(return_value={"status": "claimed", "ai_reply_cache": None, "billed_at": None, "sent_at": None, "quota_exceeded_at": None, "processing_at": None})
        
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch("src.whatsapp.inbound_processor.genera_risposta_async", AsyncMock(return_value=RispostaOutput(
                 risposta="Siamo aperti.", richiede_umano=False, motivo="", categoria="info"
             ))):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()

        mock_service.send_whatsapp_message.assert_awaited_once()
        # Il mock del provider non implementa la coda outbound reale; il
        # requisito critico e' che una consegna non confermata non finalizzi.
        ai_handled_calls = [
            c for c in mock_repo.try_mark_replied.await_args_list
            if c.kwargs.get("handling_type") == "ai_handled"
        ]
        assert len(ai_handled_calls) == 0

    async def test_pricing_enforcement_business_tier(self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg):
        # limit reached
        mock_repo.claim_message_and_check_quota = AsyncMock(return_value={"status": "quota_exceeded", "ai_reply_cache": None, "billed_at": None, "sent_at": None, "quota_exceeded_at": None, "processing_at": None})
        mock_repo.try_mark_replied = AsyncMock(return_value=True)
        
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()
            
        mock_service.send_whatsapp_message.assert_awaited_once()
        body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        assert "Stiamo ricevendo troppe richieste, attendi l'operatore" in body
        
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="quota_exceeded", organization_id=sample_msg["organization_id"])
