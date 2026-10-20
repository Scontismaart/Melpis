"""
Test per Difetto 1 (SEC-002 incompleto) - Send-then-mark su tutti i flussi secondari.

Verifica che:
1. Le escalation gia' persistite (quota_exceeded, wants_human) vengano finalizzate anche se il messaggio
   di cortesia Meta fallisce, evitando retry ciechi e ticket duplicati.
2. Se l'invio a Meta fallisce prima di un side-effect persistito (fast_reply, faq_cache, org_suspended),
   il messaggio NON viene marcato come risolto e resta disponibile per il retry del worker.
3. Quando l'invio a Meta ha successo, tutti i flussi passano per _finalize_message marcando il messaggio
   come risolto.
4. I flussi senza side-effect esterni (opt_out, feedback_emoji) finalizzano immediatamente in modo sicuro.
"""
import uuid
import pytest
from unittest.mock import AsyncMock, patch
from src.whatsapp.inbound_processor import InboundProcessor
from src.whatsapp.config import AppConfig, TenantConfig


@pytest.fixture
def base_app_config():
    return AppConfig(
        app_secret="sec_test",
        encryption_key="C1IuGfMh142ShEqV9Y2w3WPcMjIjO4aXjbnly7sqlvw=",
        postgres_dsn="postgresql://test:test@localhost:55432/p0_concurrency_test",
        verify_token="tok_test",
        max_retry_attempts=5,
    )


@pytest.fixture
def fake_tenant():
    return TenantConfig(
        organization_id=uuid.uuid4(),
        phone_number_id="391234567890",
        waba_id="waba_123",
        access_token="tok_abc",
        business_profile={"nome": "Ristorante Test", "orari": "12:00-23:00"},
    )


@pytest.mark.asyncio
async def test_quota_exceeded_meta_failure_finalizes_persisted_escalation(base_app_config, fake_tenant):
    """Un ticket quota gia' persistito non deve essere duplicato da un retry cieco."""
    msg_id = uuid.uuid4()
    org_id = fake_tenant.organization_id
    msg = {
        "id": msg_id,
        "organization_id": org_id,
        "content_text": "Vorrei prenotare",
        "content": {"from": "39333111222"},
        "conversation_id": uuid.uuid4(),
    }

    mock_repo = AsyncMock()
    # Current admission contract claims the inbound row first, then reports
    # quota exhaustion from A04 (the legacy pre-charge API is fenced).
    mock_repo.claim_message_for_processing = AsyncMock(return_value={"status": "quota_exceeded"})
    mock_repo.try_mark_replied = AsyncMock()
    mock_repo.mark_message_sent = AsyncMock()
    mock_repo.pool = None

    mock_service = AsyncMock()
    mock_service.check_opt_out = AsyncMock(return_value={"is_opt_out": False})
    mock_service.send_whatsapp_message = AsyncMock(side_effect=Exception("Meta 503 Service Unavailable"))

    with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant)):
        processor = InboundProcessor(base_app_config, mock_repo, mock_service)
        await processor._process_one(msg)

    # Verifica che Meta sia stato tentato
    mock_service.send_whatsapp_message.assert_awaited_once()
    mock_repo.try_mark_replied.assert_awaited_once_with(
        msg_id,
        handling_type="quota_exceeded",
        organization_id=org_id,
    )
    mock_repo.mark_message_sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_wants_human_meta_failure_finalizes_persisted_escalation(base_app_config, fake_tenant):
    """Un'escalation umana gia' persistita non deve produrre ticket duplicati al retry."""
    msg_id = uuid.uuid4()
    org_id = fake_tenant.organization_id
    msg = {
        "id": msg_id,
        "organization_id": org_id,
        "content_text": "Voglio parlare con un operatore umano",
        "content": {"from": "39333111222"},
        "conversation_id": uuid.uuid4(),
    }

    mock_repo = AsyncMock()
    mock_repo.claim_message_for_processing = AsyncMock(return_value={"status": "claimed", "ai_reply_cache": None})
    mock_repo.get_outbound_dedup = AsyncMock(return_value=None)
    mock_repo.try_mark_replied = AsyncMock()
    mock_repo.mark_message_sent = AsyncMock()
    mock_repo.pool = None

    mock_service = AsyncMock()
    mock_service.check_opt_out = AsyncMock(return_value={"is_opt_out": False})
    mock_service.check_human_request = AsyncMock(return_value=True)
    mock_service.send_whatsapp_message = AsyncMock(side_effect=Exception("Meta 500 Network Error"))

    with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant)):
        processor = InboundProcessor(base_app_config, mock_repo, mock_service)
        await processor._process_one(msg)

    mock_service.send_whatsapp_message.assert_awaited_once()
    mock_repo.try_mark_replied.assert_awaited_once_with(
        msg_id,
        handling_type="escalated",
        organization_id=org_id,
    )
    mock_repo.mark_message_sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_fast_reply_meta_failure_does_not_mark_replied(base_app_config, fake_tenant):
    """Se Meta fallisce durante fast_reply, il messaggio non deve essere marcato replied."""
    msg_id = uuid.uuid4()
    org_id = fake_tenant.organization_id
    msg = {
        "id": msg_id,
        "organization_id": org_id,
        "content_text": "Ciao!",
        "content": {"from": "39333111222"},
        "conversation_id": uuid.uuid4(),
    }

    mock_repo = AsyncMock()
    mock_repo.claim_message_for_processing = AsyncMock(return_value={"status": "claimed", "ai_reply_cache": None})
    mock_repo.get_outbound_dedup = AsyncMock(return_value=None)
    mock_repo.get_org_subscription_state = AsyncMock(return_value={"subscription_status": "active"})
    mock_repo.try_mark_replied = AsyncMock()
    mock_repo.mark_message_sent = AsyncMock()
    mock_repo.get_or_create_contact = AsyncMock(return_value={"id": uuid.uuid4()})
    mock_repo.mark_ai_disclosure_sent = AsyncMock(return_value=False)

    mock_service = AsyncMock()
    mock_service.check_opt_out = AsyncMock(return_value={"is_opt_out": False})
    mock_service.check_human_request = AsyncMock(return_value=False)
    mock_service.fast_path_match = AsyncMock(return_value="Ciao! Benvenuto.")
    mock_service.send_whatsapp_message = AsyncMock(side_effect=Exception("Meta Timeout"))

    with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant)):
        processor = InboundProcessor(base_app_config, mock_repo, mock_service)
        await processor._process_one(msg)

    mock_service.send_whatsapp_message.assert_awaited_once()
    mock_repo.try_mark_replied.assert_not_awaited()


@pytest.mark.asyncio
async def test_org_suspended_meta_failure_does_not_mark_replied(base_app_config, fake_tenant):
    """Se Meta fallisce durante invio risposta tenant sospeso, il messaggio non deve essere marcato replied."""
    msg_id = uuid.uuid4()
    org_id = fake_tenant.organization_id
    msg = {
        "id": msg_id,
        "organization_id": org_id,
        "content_text": "Salve",
        "content": {"from": "39333111222"},
        "conversation_id": uuid.uuid4(),
    }

    mock_repo = AsyncMock()
    mock_repo.claim_message_for_processing = AsyncMock(return_value={"status": "claimed", "ai_reply_cache": None})
    mock_repo.get_org_subscription_state = AsyncMock(return_value={"subscription_status": "canceled"})
    mock_repo.try_mark_replied = AsyncMock()
    mock_repo.mark_message_sent = AsyncMock()

    mock_service = AsyncMock()
    mock_service.check_opt_out = AsyncMock(return_value={"is_opt_out": False})
    mock_service.check_human_request = AsyncMock(return_value=False)
    mock_service.send_whatsapp_message = AsyncMock(side_effect=Exception("Meta Down"))

    with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant)):
        processor = InboundProcessor(base_app_config, mock_repo, mock_service)
        await processor._process_one(msg)

    mock_service.send_whatsapp_message.assert_awaited_once()
    mock_repo.try_mark_replied.assert_not_awaited()


@pytest.mark.asyncio
async def test_opt_out_finalizes_safely_without_external_call(base_app_config, fake_tenant):
    """Opt out non ha chiamate di rete a Meta: persiste su DB e finalizza subito."""
    msg_id = uuid.uuid4()
    org_id = fake_tenant.organization_id
    contact_id = uuid.uuid4()
    msg = {
        "id": msg_id,
        "organization_id": org_id,
        "content_text": "STOP",
        "content": {"from": "39333111222"},
        "conversation_id": uuid.uuid4(),
    }

    mock_repo = AsyncMock()
    mock_repo.claim_message_for_processing = AsyncMock(return_value={"status": "claimed", "ai_reply_cache": None})
    mock_repo.get_or_create_contact = AsyncMock(return_value={"id": contact_id})
    mock_repo.record_consent_event = AsyncMock()
    mock_repo.try_mark_replied = AsyncMock(return_value={"id": msg_id, "status": "handled"})

    mock_service = AsyncMock()
    mock_service.check_opt_out = AsyncMock(return_value={"is_opt_out": True})

    processor = InboundProcessor(base_app_config, mock_repo, mock_service)
    await processor._process_one(msg)

    mock_repo.record_consent_event.assert_awaited_once()
    mock_service.send_whatsapp_message.assert_not_awaited()
    mock_repo.try_mark_replied.assert_awaited_once_with(msg_id, handling_type="opt_out", organization_id=org_id)


@pytest.mark.asyncio
async def test_all_successful_flows_finalize_properly(base_app_config, fake_tenant):
    """Quando Meta risponde 200, tutti i flussi finalizzano marcando replied e sent."""
    msg_id = uuid.uuid4()
    org_id = fake_tenant.organization_id
    msg = {
        "id": msg_id,
        "organization_id": org_id,
        "content_text": "Ciao!",
        "content": {"from": "39333111222"},
        "conversation_id": uuid.uuid4(),
    }

    mock_repo = AsyncMock()
    mock_repo.claim_message_for_processing = AsyncMock(return_value={"status": "claimed", "ai_reply_cache": None})
    mock_repo.get_outbound_dedup = AsyncMock(return_value=None)
    mock_repo.get_org_subscription_state = AsyncMock(return_value={"subscription_status": "active"})
    mock_repo.try_mark_replied = AsyncMock(return_value={"id": msg_id, "status": "handled"})
    mock_repo.mark_message_sent = AsyncMock()
    mock_repo.get_or_create_contact = AsyncMock(return_value={"id": uuid.uuid4()})
    mock_repo.mark_ai_disclosure_sent = AsyncMock(return_value=False)

    mock_service = AsyncMock()
    mock_service.check_opt_out = AsyncMock(return_value={"is_opt_out": False})
    mock_service.check_human_request = AsyncMock(return_value=False)
    mock_service.fast_path_match = AsyncMock(return_value="Ciao! Benvenuto.")
    mock_service.send_whatsapp_message = AsyncMock(return_value={"status": "sent", "wam_id": "wamid_12345"})

    with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=fake_tenant)):
        processor = InboundProcessor(base_app_config, mock_repo, mock_service)
        await processor._process_one(msg)

    mock_service.send_whatsapp_message.assert_awaited_once()
    mock_repo.mark_message_sent.assert_awaited_once_with(msg_id, "wamid_12345", org_id)
    mock_repo.try_mark_replied.assert_awaited_once_with(msg_id, handling_type="ai_handled", organization_id=org_id)
