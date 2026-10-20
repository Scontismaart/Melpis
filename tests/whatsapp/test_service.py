import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from src.whatsapp.service import WhatsAppService
from src.whatsapp.config import AppConfig, TenantConfig


OPT_OUT_KEYWORDS = {
    "it": ["stop", "annulla", "basta", "non scrivermi più", "cancellami", "disiscrivi"],
    "en": ["stop", "unsubscribe", "cancel", "opt out", "remove me"],
}


@pytest.fixture(autouse=True)
def sandbox_recipient(monkeypatch):
    monkeypatch.setenv("SANDBOX_ONLY", "true")
    monkeypatch.setenv("WHATSAPP_TEST_RECIPIENTS", "391234567890")


@pytest.fixture
def app_config():
    return AppConfig(
        app_secret="test_secret",
        encryption_key="dGVzdC1rZXktMTIzNDU2Nzg5MDEyMzQ1Njc4OTA=",
        postgres_dsn="",
        verify_token="test_token",
    )


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    messages = {}

    async def upsert_message(**values):
        row = {**values}
        messages[values["id"]] = row
        return row

    async def claim_outbound_delivery(message_id, **_kwargs):
        return messages.get(message_id)

    repo.get_or_create_contact = AsyncMock(return_value={"id": uuid.uuid4(), "marketing_opt_out": False})
    repo.get_or_create_conversation = AsyncMock(return_value={"id": uuid.uuid4()})
    repo.get_contact_prefs = AsyncMock(return_value={"id": uuid.uuid4(), "marketing_opt_out": False})
    repo.upsert_message = AsyncMock(side_effect=upsert_message)
    repo.claim_outbound_delivery = AsyncMock(side_effect=claim_outbound_delivery)
    repo.update_message_status = AsyncMock(return_value={"id": uuid.uuid4(), "status": "sent"})
    repo.check_message_usage = AsyncMock(return_value={"messages_used_this_period": 0, "messages_limit": 100})
    repo.increment_message_usage = AsyncMock(return_value=1)
    return repo


@pytest.fixture
def mock_meta_client():
    client = AsyncMock()
    client.send_message = AsyncMock()
    client.send_message.return_value.messages = [MagicMock(id="wamid.outbound.test")]
    client.send_message.return_value.contacts = [MagicMock(wa_id="391234567890")]
    client.send_message.return_value.messaging_product = "whatsapp"
    return client


class TestWhatsAppService:
    async def test_send_whatsapp_message_creates_message(self, app_config, mock_repo, mock_meta_client):
        service = WhatsAppService(app_config, mock_repo)
        result = await service.send_whatsapp_message(
            org_id=uuid.uuid4(),
            to_number="391234567890",
            payload={"type": "text", "text": {"body": "Ciao!"}},
            category="utility",
            meta_client=mock_meta_client,
            tenant_config=MagicMock(),
        )
        assert result["status"] == "sent"
        mock_repo.upsert_message.assert_called_once()

    async def test_opt_out_gate_blocks_marketing(self, app_config, mock_repo, mock_meta_client):
        mock_repo.get_contact_prefs = AsyncMock(return_value={"marketing_opt_out": True, "id": uuid.uuid4()})
        service = WhatsAppService(app_config, mock_repo)
        with pytest.raises(service.MessageBlockedByOptOut):
            await service.send_whatsapp_message(
                org_id=uuid.uuid4(),
                to_number="391234567890",
                payload={"type": "text", "text": {"body": "Offerta speciale!"}},
                category="marketing",
                meta_client=mock_meta_client,
                tenant_config=MagicMock(),
            )

    async def test_opt_out_gate_allows_utility(self, app_config, mock_repo, mock_meta_client):
        mock_repo.get_contact_prefs = AsyncMock(return_value={"marketing_opt_out": True, "id": uuid.uuid4()})
        service = WhatsAppService(app_config, mock_repo)
        result = await service.send_whatsapp_message(
            org_id=uuid.uuid4(),
            to_number="391234567890",
            payload={"type": "text", "text": {"body": "Conferma prenotazione #123"}},
            category="utility",
            meta_client=mock_meta_client,
            tenant_config=MagicMock(),
        )
        assert result["status"] == "sent"

    async def test_attempt_delivery_updates_existing_message(self, app_config, mock_repo, mock_meta_client):
        mock_repo.upsert_message.reset_mock()
        payload = {"type": "text", "text": {"body": "Test"}, "to": "391234567890"}
        mock_repo.claim_outbound_delivery = AsyncMock(
            return_value={"id": uuid.uuid4(), "content": payload, "status": "sending_ambiguous"}
        )
        service = WhatsAppService(app_config, mock_repo)
        await service.attempt_delivery(
            message_id=uuid.uuid4(),
            phone_number_id="12345",
            access_token="tok",
            payload=payload,
            meta_client=mock_meta_client,
            organization_id=uuid.uuid4(),
        )
        mock_repo.upsert_message.assert_not_called()
        mock_repo.update_message_status.assert_called()

    async def test_check_opt_out_keyword_match_it(self, app_config, mock_repo):
        service = WhatsAppService(app_config, mock_repo)
        result = await service.check_opt_out("STOP!", "it")
        assert result["is_opt_out"] is True
        assert result["confidence"] == "high"

    async def test_check_opt_out_keyword_match_en(self, app_config, mock_repo):
        service = WhatsAppService(app_config, mock_repo)
        result = await service.check_opt_out("unsubscribe please", "en")
        assert result["is_opt_out"] is True

    async def test_check_opt_out_normal_message(self, app_config, mock_repo):
        service = WhatsAppService(app_config, mock_repo)
        result = await service.check_opt_out("Grazie, arrivederci!", "it")
        assert result["is_opt_out"] is False

    async def test_fast_path_greeting(self, app_config, mock_repo):
        service = WhatsAppService(app_config, mock_repo)
        bp = {"name": "Trattoria Da Mario"}
        result = await service.fast_path_match("Ciao", bp)
        assert result is not None
        assert "Trattoria Da Mario" in result

    async def test_fast_path_hours(self, app_config, mock_repo):
        service = WhatsAppService(app_config, mock_repo)
        bp = {"orari": "Lun-Sab 12:00-22:30"}
        # Da redesign fast path: copre solo saluti/ringraziamenti (<= 2 parole).
        # Le domande sugli orari vanno al flusso LLM completo (risposte migliori).
        result = await service.fast_path_match("Che orari fate?", bp)
        assert result is None

    async def test_fast_path_no_match(self, app_config, mock_repo):
        service = WhatsAppService(app_config, mock_repo)
        bp = {"name": "Test"}
        result = await service.fast_path_match("Quanto costa la pizza?", bp)
        assert result is None

    async def test_check_human_request_keyword_it(self, app_config, mock_repo):
        service = WhatsAppService(app_config, mock_repo)
        assert await service.check_human_request("voglio parlare con un operatore") is True
        assert await service.check_human_request("parlare con una persona") is True

    async def test_check_human_request_keyword_en(self, app_config, mock_repo):
        service = WhatsAppService(app_config, mock_repo)
        assert await service.check_human_request("operator please", "en") is True

    async def test_check_human_request_normal_message(self, app_config, mock_repo):
        service = WhatsAppService(app_config, mock_repo)
        assert await service.check_human_request("quanto costano i menu") is False

    async def test_manual_outbound_remains_available_at_zero_ai_quota(self, app_config, mock_repo, mock_meta_client):
        mock_repo.check_message_usage = AsyncMock(return_value={"messages_used_this_period": 100, "messages_limit": 100})
        service = WhatsAppService(app_config, mock_repo)
        result = await service.send_whatsapp_message(
            org_id=uuid.uuid4(),
            to_number="391234567890",
            payload={"type": "text", "text": {"body": "Ciao!"}},
            category="utility",
            meta_client=mock_meta_client,
            tenant_config=MagicMock(),
        )
        assert result["status"] == "sent"
        mock_repo.check_message_usage.assert_not_awaited()

    async def test_message_usage_null_limit_allows_send(self, app_config, mock_repo, mock_meta_client):
        mock_repo.check_message_usage = AsyncMock(return_value={"messages_used_this_period": 500, "messages_limit": None})
        service = WhatsAppService(app_config, mock_repo)
        result = await service.send_whatsapp_message(
            org_id=uuid.uuid4(),
            to_number="391234567890",
            payload={"type": "text", "text": {"body": "Ciao!"}},
            category="utility",
            meta_client=mock_meta_client,
            tenant_config=MagicMock(),
        )
        assert result["status"] == "sent"

    async def test_manual_outbound_does_not_write_ai_usage(self, app_config, mock_repo, mock_meta_client):
        service = WhatsAppService(app_config, mock_repo)
        await service.send_whatsapp_message(
            org_id=uuid.uuid4(),
            to_number="391234567890",
            payload={"type": "text", "text": {"body": "Ciao!"}},
            category="utility",
            meta_client=mock_meta_client,
            tenant_config=MagicMock(),
        )
        mock_repo.increment_message_usage.assert_not_awaited()

    async def test_idempotency_key_precheck_skips_send(self, app_config, mock_repo, mock_meta_client):
        """Task 6: se l'idempotency_key esiste gia', non si tenta un secondo
        invio (niente chiamata a Meta), si ritorna il messaggio esistente."""
        existing = {
            "id": uuid.uuid4(), "status": "sent",
            "content": {"type": "text", "text": {"body": "Ciao!"}, "to": "391234567890", "_delivery_category": "service"},
        }
        mock_repo.check_idempotency = AsyncMock(return_value=existing)
        service = WhatsAppService(app_config, mock_repo)
        result = await service.send_whatsapp_message(
            org_id=uuid.uuid4(),
            to_number="391234567890",
            payload={"type": "text", "text": {"body": "Ciao!"}},
            category="service",
            meta_client=mock_meta_client,
            tenant_config=MagicMock(),
            idempotency_key="reply-key-001",
        )
        assert result == existing
        mock_meta_client.send_message.assert_not_called()
        mock_repo.upsert_message.assert_not_called()

    async def test_idempotency_key_race_after_precheck_skips_delivery(self, app_config, mock_repo, mock_meta_client):
        """Race genuina: il pre-check non trova nulla, ma upsert_message
        ritorna una riga con id diverso da quello appena generato (un'altra
        richiesta ha vinto l'insert nel frattempo) -> niente doppio invio."""
        mock_repo.check_idempotency = AsyncMock(return_value=None)
        winner_row = {
            "id": uuid.uuid4(), "status": "sent",
            "content": {"type": "text", "text": {"body": "Ciao!"}, "to": "391234567890", "_delivery_category": "service"},
        }
        mock_repo.upsert_message = AsyncMock(return_value=winner_row)
        service = WhatsAppService(app_config, mock_repo)
        result = await service.send_whatsapp_message(
            org_id=uuid.uuid4(),
            to_number="391234567890",
            payload={"type": "text", "text": {"body": "Ciao!"}},
            category="service",
            meta_client=mock_meta_client,
            tenant_config=MagicMock(),
            idempotency_key="reply-key-002",
        )
        assert result == winner_row
        mock_meta_client.send_message.assert_not_called()
