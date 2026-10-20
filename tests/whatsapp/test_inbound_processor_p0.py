import contextlib
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, patch
import pytest
from src.whatsapp.inbound_processor import InboundProcessor


@pytest.fixture(autouse=True)
def _mock_a04_admission_for_business_flow_units(monkeypatch):
    from tests.a04_admission_fakes import install_mock_a04_admission
    install_mock_a04_admission(monkeypatch)

class TestP0Blockers:
    async def test_booking_idempotency_on_crash(
        self, app_config, mock_repo, mock_service, fake_tenant_config, sample_msg,
        mock_provider_context_open,
    ):
        from src.core.bookings.service import BookingService
        from src.models.schemas import RispostaOutput
        now = datetime.now(timezone.utc)
        booking_date = (now.astimezone(ZoneInfo("Europe/Rome")).date() + timedelta(days=1)).isoformat()
        sample_msg["content_text"] = (
            f"Vorrei prenotare {booking_date} alle 12:00 per 2 persone"
        )
        sample_msg["created_at"] = now

        # `_booking_context` only accepts a tenant-scoped source message with
        # a valid billing period and recent, explicit customer booking facts.
        source_row = {
            "content": sample_msg["content"],
            "conversation_id": sample_msg["conversation_id"],
            "created_at": now,
            "billed_at": now,
            "canale": "whatsapp",
            "business_profile": {"verticale": "ristorazione"},
            "subscription_status": "active",
            "subscription_id": "sub_test123",
            "trial_start": None,
            "trial_end": None,
            "current_period_start": now - timedelta(days=1),
            "current_period_end": now + timedelta(days=30),
            "ai_accounting_blocked": False,
            "verified_now": now,
        }
        history_row = {
            "id": sample_msg["id"],
            "content_text": sample_msg["content_text"],
            "created_at": now,
        }
        conn = AsyncMock()

        async def fetchrow(query, *args):
            if "FROM messages m JOIN conversations" in query:
                assert args == (sample_msg["organization_id"], sample_msg["id"])
                return source_row
            if "FROM bookings WHERE organization_id=$1 AND source_message_id=$2" in query:
                assert args == (sample_msg["organization_id"], str(sample_msg["id"]))
                return None
            raise AssertionError(f"Unexpected booking authorization query: {query}")

        conn.fetchrow.side_effect = fetchrow
        conn.fetch.return_value = [history_row]

        @contextlib.asynccontextmanager
        async def acquire():
            yield conn

        mock_repo.pool.acquire = acquire
        mock_repo.get_booking_settings = AsyncMock(return_value={"capienze_orarie": {"12:00": 10}})
        mock_repo.list_bookings = AsyncMock(return_value=[])
        mock_repo.create_booking = AsyncMock(return_value={"id": uuid.uuid4()})
        mock_repo.get_outbound_dedup = AsyncMock(return_value=None)
        mock_repo.claim_message_for_processing = AsyncMock(return_value={"status": "claimed", "ai_reply_cache": None})
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
                 prenotazione={"nome_cliente": "Test", "telefono": "123", "data": booking_date, "ora": "12:00", "coperti": 2}
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
        mock_repo.claim_message_for_processing = AsyncMock(return_value={"status": "claimed", "ai_reply_cache": None})
        
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
        mock_repo.claim_message_for_processing = AsyncMock(return_value={"status": "quota_exceeded", "ai_reply_cache": None})
        mock_repo.try_mark_replied = AsyncMock(return_value=True)
        
        with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant_config)):
            processor = InboundProcessor(app_config, mock_repo, mock_service)
            await processor.process_next_batch()
            
        mock_service.send_whatsapp_message.assert_awaited_once()
        body = mock_service.send_whatsapp_message.call_args.kwargs["payload"]["text"]["body"]
        assert "Stiamo ricevendo troppe richieste, attendi l'operatore" in body
        
        mock_repo.try_mark_replied.assert_awaited_with(sample_msg["id"], handling_type="quota_exceeded", organization_id=sample_msg["organization_id"])
