"""Booking event entry points use the durable tenant-scoped Calendar queue."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from src.core.calendar.service import GoogleCalendarService


ORG_ID = "00000000-0000-0000-0000-000000000001"
BOOKING_ID = "00000000-0000-0000-0000-000000000002"


def _service(monkeypatch):
    monkeypatch.setattr("src.core.calendar.service.google_calendar_enabled", lambda: True)
    connection = MagicMock()
    connection.fetchval = AsyncMock(return_value="existing-event")
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=connection)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    service = GoogleCalendarService(SimpleNamespace(pool=pool), Fernet.generate_key())
    service.process_pending = AsyncMock(return_value=1)
    service._build_service = AsyncMock()
    return service, connection


def _booking(**overrides):
    return {
        "id": BOOKING_ID,
        "organization_id": ORG_ID,
        "google_event_id": "existing-event",
        **overrides,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["create_event", "update_event", "delete_event"])
async def test_all_legacy_event_entry_points_process_only_persisted_queue_intent(monkeypatch, method_name):
    service, conn = _service(monkeypatch)

    result = await getattr(service, method_name)(_booking(), ORG_ID)

    assert result == "existing-event"
    service.process_pending.assert_awaited_once_with(
        1, organization_id=ORG_ID, booking_id=BOOKING_ID
    )
    conn.fetchval.assert_awaited_once()
    # Provider mutations are exclusively performed by CalendarLifecycle.
    service._build_service.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["create_event", "update_event", "delete_event"])
async def test_event_entry_points_reject_booking_from_another_tenant_before_queue(monkeypatch, method_name):
    service, conn = _service(monkeypatch)

    result = await getattr(service, method_name)(
        _booking(organization_id="00000000-0000-0000-0000-000000000099"), ORG_ID
    )

    assert result is None
    service.process_pending.assert_not_awaited()
    conn.fetchval.assert_not_awaited()
    service._build_service.assert_not_awaited()
