"""Manual internal/optional retry retains durable WhatsApp idempotency."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.bookings.service import BookingService
from src.whatsapp.repository import Repository
from src.whatsapp.service import WhatsAppService
from tests.core.calendar.test_calendar_provider_postgres import setup

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


@pytest.mark.parametrize("mode", ["internal", "internal_calendar_sync"])
@pytest.mark.parametrize("delivery_state", ["sent", "sending_ambiguous"])
async def test_manual_confirmation_retry_after_commit_reuses_one_message(
    pg_pool, sample_org, monkeypatch, mode, delivery_state,
):
    org = sample_org["id"]
    _calendar, google, repo, booking = await setup(pg_pool, org, monkeypatch, mode)
    whatsapp_repo = Repository(pg_pool)
    whatsapp = WhatsAppService(None, whatsapp_repo)
    service = BookingService(repo, whatsapp_service=whatsapp)
    service._load_tenant_config = AsyncMock(side_effect=[RuntimeError("synthetic pre-persist crash")])

    with pytest.raises(RuntimeError, match="synthetic pre-persist crash"):
        await service.confirm(org, booking["id"])
    assert (await repo.get_booking(org, booking["id"]))["stato"] == "confermata"
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM messages WHERE organization_id=$1", org) == 0

    service._load_tenant_config = AsyncMock(return_value=SimpleNamespace(
        phone_number_id="synthetic-phone", access_token="synthetic-token"))
    attempts = []

    async def fake_delivery(**kwargs):
        # Only the provider boundary is replaced. Message persistence, replay
        # comparison and terminal/in-flight deduplication run against PostgreSQL.
        attempts.append(kwargs["message_id"])
        return await whatsapp_repo.update_message_status(
            kwargs["message_id"], delivery_state,
            wam_id="synthetic-wam-id" if delivery_state == "sent" else None,
            organization_id=org,
        )

    whatsapp.attempt_delivery = fake_delivery
    resumed = await service.confirm(org, booking["id"])
    replayed = await service.confirm(org, booking["id"])
    assert resumed["stato"] == replayed["stato"] == "confermata"
    assert len(attempts) == 1
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM messages WHERE organization_id=$1 AND direction='outbound'", org)
        assert len(rows) == 1
        assert rows[0]["status"] == delivery_state
        assert rows[0]["idempotency_key"] == f"booking-confirm-manual:{org}:{booking['id']}:{resumed['calendar_revision']}"
        assert await conn.fetchval("SELECT count(*) FROM usage_events WHERE organization_id=$1", org) == 0
    assert google.calls == []


@pytest.mark.parametrize("mode", ["internal", "internal_calendar_sync"])
async def test_existing_durable_confirmation_notice_prevents_manual_duplicate(pg_pool, sample_org, monkeypatch, mode):
    org = sample_org["id"]
    _calendar, google, repo, booking = await setup(pg_pool, org, monkeypatch, mode)
    confirmed = await repo.update_booking_status(org, booking["id"], "confermata")
    async with pg_pool.acquire() as conn, conn.transaction():
        await repo.enqueue_calendar_confirmation(conn, confirmed, None, "whatsapp")
    service = BookingService(repo)
    service._send_whatsapp = AsyncMock(side_effect=AssertionError("existing durable notice bypassed"))
    assert (await service.confirm(org, booking["id"]))["stato"] == "confermata"
    service._send_whatsapp.assert_not_awaited()
    assert google.calls == []
