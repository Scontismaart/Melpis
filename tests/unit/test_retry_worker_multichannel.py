import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.core.channels.base import OutboundSendResult
from src.core.channels.router import OutboundChannelRouter
from src.core.workers.retry_worker import MultiChannelRetryWorker, BACKOFF_SCHEDULE
from src.whatsapp.config import AppConfig, TenantConfig
from src.whatsapp.retry_worker import RetryWorker


@pytest.fixture
def app_config():
    return AppConfig(
        app_secret="test_secret",
        encryption_key="key",
        postgres_dsn="",
        verify_token="test_token",
        max_retry_attempts=5,
    )


@pytest.fixture
def mock_tenant():
    return TenantConfig(
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        phone_number_id="123456789",
        waba_id="waba_test",
        access_token="test_access_token",
    )


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.reap_stale_claims = AsyncMock(return_value=[])
    repo.claim_delivery_attempts = AsyncMock(return_value=[])
    repo.reconstruct_payload_for_retry = AsyncMock()
    repo.update_delivery_attempt = AsyncMock()
    repo.insert_delivery_attempt = AsyncMock()
    repo.update_message_status = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_reaper_and_claim_called(app_config, mock_repo):
    worker = MultiChannelRetryWorker(app_config, mock_repo)
    await worker.process_next_batch()
    mock_repo.reap_stale_claims.assert_called_once()
    mock_repo.claim_delivery_attempts.assert_called_once_with(limit=10)


@pytest.mark.asyncio
async def test_whatsapp_retry_success_via_service(app_config, mock_repo, mock_tenant):
    msg_id = uuid.uuid4()
    org_id = mock_tenant.organization_id
    attempt_id = uuid.uuid4()

    mock_repo.claim_delivery_attempts = AsyncMock(return_value=[
        {"id": attempt_id, "message_id": msg_id, "attempt_number": 1, "status": "processing"}
    ])
    mock_repo.reconstruct_payload_for_retry = AsyncMock(return_value={
        "id": msg_id,
        "organization_id": org_id,
        "channel": "whatsapp",
        "content": {"to": "39123456789", "text": {"body": "Test retry"}},
    })

    mock_service = AsyncMock()
    mock_service.attempt_delivery = AsyncMock(return_value={"status": "sent", "wam_id": "wamid.123"})

    with patch("src.whatsapp.config.load_tenant_config", AsyncMock(return_value=mock_tenant)):
        worker = MultiChannelRetryWorker(app_config, mock_repo, service=mock_service)
        await worker.process_next_batch()

    mock_service.attempt_delivery.assert_called_once()
    assert mock_service.attempt_delivery.call_args.kwargs["organization_id"] == org_id
    mock_repo.update_delivery_attempt.assert_called_once_with(attempt_id, "succeeded")


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.asyncio
async def test_booking_confirmation_checks_current_state_before_delivery(
    app_config, mock_repo, mock_tenant, confirmed,
):
    msg_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    mock_repo.reconstruct_payload_for_retry.return_value = {
        "id": msg_id, "organization_id": mock_tenant.organization_id,
        "handling_type": "booking_confirmation", "channel": "whatsapp",
        "content": {"to": "39123456789", "text": {"body": "Prenotazione confermata"}},
    }

    @asynccontextmanager
    async def guard(org_id, message_id):
        assert org_id == mock_tenant.organization_id
        assert message_id == msg_id
        yield confirmed

    mock_repo.booking_confirmation_delivery_guard = guard
    service = AsyncMock()
    service.attempt_delivery.return_value = {"status": "sent", "wam_id": "wamid.123"}
    worker = MultiChannelRetryWorker(app_config, mock_repo, service=service)
    attempt = {"id": attempt_id, "message_id": msg_id, "attempt_number": 1}
    with patch("src.whatsapp.config.load_tenant_config", AsyncMock(return_value=mock_tenant)):
        await worker._process_one(attempt)
    if confirmed:
        service.attempt_delivery.assert_awaited_once()
        mock_repo.update_delivery_attempt.assert_awaited_with(attempt_id, "succeeded")
    else:
        service.attempt_delivery.assert_not_awaited()
        mock_repo.update_delivery_attempt.assert_awaited_once_with(
            attempt_id, "failed", {"error": "booking_no_longer_confirmed"},
        )


@pytest.mark.asyncio
async def test_instagram_retry_success_via_router(app_config, mock_repo):
    msg_id = uuid.uuid4()
    org_id = uuid.uuid4()
    attempt_id = uuid.uuid4()

    mock_repo.claim_delivery_attempts = AsyncMock(return_value=[
        {"id": attempt_id, "message_id": msg_id, "attempt_number": 2, "status": "processing"}
    ])
    mock_repo.reconstruct_payload_for_retry = AsyncMock(return_value={
        "id": msg_id,
        "organization_id": org_id,
        "channel": "instagram",
        "recipient": "ig_user_456",
        "idempotency_key": "reply:inbound-1",
        "text": "Hello from IG retry",
    })

    ig_adapter = AsyncMock()
    ig_adapter.send_reply = AsyncMock(return_value=OutboundSendResult(
        success=True,
        channel="instagram",
        message_id="ig_msg_999",
    ))
    router = OutboundChannelRouter({"instagram": ig_adapter})

    worker = MultiChannelRetryWorker(app_config, mock_repo, channel_router=router)
    await worker.process_next_batch()

    ig_adapter.send_reply.assert_called_once_with(
        org_id=org_id,
        to_destination="ig_user_456",
        text="Hello from IG retry",
        idempotency_key="reply:inbound-1",
    )
    mock_repo.update_delivery_attempt.assert_called_once_with(attempt_id, "succeeded")


@pytest.mark.asyncio
async def test_retry_transient_failure_schedules_backoff(app_config, mock_repo, mock_tenant):
    msg_id = uuid.uuid4()
    org_id = mock_tenant.organization_id
    attempt_id = uuid.uuid4()

    mock_repo.claim_delivery_attempts = AsyncMock(return_value=[
        {"id": attempt_id, "message_id": msg_id, "attempt_number": 1, "status": "processing"}
    ])
    mock_repo.reconstruct_payload_for_retry = AsyncMock(return_value={
        "id": msg_id,
        "organization_id": org_id,
        "channel": "whatsapp",
        "content": {},
    })

    mock_service = AsyncMock()
    mock_service.attempt_delivery = AsyncMock(side_effect=RuntimeError("Meta 500 error"))

    with patch("src.whatsapp.config.load_tenant_config", AsyncMock(return_value=mock_tenant)):
        worker = MultiChannelRetryWorker(app_config, mock_repo, service=mock_service)
        await worker.process_next_batch()

    # Must update attempt as pending with error and schedule next attempt
    assert mock_repo.update_delivery_attempt.called
    update_call = mock_repo.update_delivery_attempt.call_args_list[0]
    assert update_call[0][0] == attempt_id
    assert update_call[0][1] == "pending"
    assert "Meta 500 error" in update_call[0][2]["error"]
    mock_repo.insert_delivery_attempt.assert_called_once()


@pytest.mark.asyncio
async def test_retry_max_attempts_dead_letters(app_config, mock_repo, mock_tenant):
    msg_id = uuid.uuid4()
    org_id = mock_tenant.organization_id
    attempt_id = uuid.uuid4()

    mock_repo.claim_delivery_attempts = AsyncMock(return_value=[
        {"id": attempt_id, "message_id": msg_id, "attempt_number": 5, "status": "processing"}
    ])
    mock_repo.reconstruct_payload_for_retry = AsyncMock(return_value={
        "id": msg_id,
        "organization_id": org_id,
        "channel": "whatsapp",
        "content": {},
    })

    mock_service = AsyncMock()
    mock_service.attempt_delivery = AsyncMock(side_effect=RuntimeError("Permanent failure"))

    with patch("src.whatsapp.config.load_tenant_config", AsyncMock(return_value=mock_tenant)):
        worker = MultiChannelRetryWorker(app_config, mock_repo, service=mock_service)
        await worker.process_next_batch()

    assert mock_repo.update_delivery_attempt.called
    update_call = mock_repo.update_delivery_attempt.call_args_list[0]
    assert update_call[0][1] == "failed"
    mock_repo.update_message_status.assert_called_once_with(
        msg_id, "failed", error_code="max_retries",
        error_title="Permanent failure", organization_id=org_id
    )


@pytest.mark.asyncio
async def test_whatsapp_facade_delegation(app_config, mock_repo, mock_tenant):
    msg_id = uuid.uuid4()
    org_id = mock_tenant.organization_id
    attempt_id = uuid.uuid4()

    mock_repo.claim_delivery_attempts = AsyncMock(return_value=[
        {"id": attempt_id, "message_id": msg_id, "attempt_number": 1, "status": "processing"}
    ])
    mock_repo.reconstruct_payload_for_retry = AsyncMock(return_value={
        "id": msg_id,
        "organization_id": org_id,
        "channel": "whatsapp",
        "content": {},
    })

    mock_service = AsyncMock()
    mock_service.attempt_delivery = AsyncMock(return_value={"status": "sent", "wam_id": "wamid.test"})

    with patch("src.whatsapp.config.load_tenant_config", AsyncMock(return_value=mock_tenant)):
        # Instantiate legacy facade RetryWorker from src.whatsapp.retry_worker
        facade_worker = RetryWorker(app_config, mock_repo, service=mock_service)
        assert facade_worker.max_retries == 5
        await facade_worker.process_next_batch()

    mock_service.attempt_delivery.assert_called_once()
    mock_repo.update_delivery_attempt.assert_called_once_with(attempt_id, "succeeded")
