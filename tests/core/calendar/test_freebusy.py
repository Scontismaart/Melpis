"""Typed local-day availability results (no provider or network)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.calendar.availability import AvailabilityStatus, CalendarAvailability
from src.core.calendar.service import GoogleCalendarService

FERNET_KEY = "GT4pFJ9wm5vlxRS2MSmSF3tjbThnKnon-sgG5TVYILE="


@pytest.fixture(autouse=True)
def calendar_enabled(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")


def _svc():
    return GoogleCalendarService(repo=MagicMock(), encryption_key=FERNET_KEY)


@pytest.mark.asyncio
async def test_get_busy_intervals_queries_full_org_local_day_and_returns_typed_result(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_get_org_timezone", AsyncMock(return_value="Europe/Rome"))
    verified_busy = CalendarAvailability(
        AvailabilityStatus.BUSY,
        intervals=((datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
                    datetime(2026, 9, 2, 11, tzinfo=timezone.utc)),),
        resources=("primary",),
    )
    svc.availability = AsyncMock(return_value=verified_busy)

    result = await svc.get_busy_intervals("org-1", "2026-09-02")

    assert result is verified_busy
    svc.availability.assert_awaited_once()
    org_id, start, end = svc.availability.await_args.args
    assert org_id == "org-1"
    assert start == datetime(2026, 9, 1, 22, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 2, 22, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, reason",
    [
        (AvailabilityStatus.DISABLED, "disabled"),
        (AvailabilityStatus.UNKNOWN, "provider_unavailable"),
    ],
)
async def test_get_busy_intervals_preserves_disabled_and_unknown_states(monkeypatch, status, reason):
    svc = _svc()
    monkeypatch.setattr(svc, "_get_org_timezone", AsyncMock(return_value="Europe/Rome"))
    typed = CalendarAvailability(status, reason_code=reason)
    svc.availability = AsyncMock(return_value=typed)

    result = await svc.get_busy_intervals("org-1", "2026-09-02")

    assert result is typed
    assert result.status is status
    assert result.intervals == ()


@pytest.mark.asyncio
async def test_get_busy_intervals_invalid_timezone_is_unknown_not_verified_free(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_get_org_timezone", AsyncMock(return_value="Mars/Olympus_Mons"))
    svc.availability = AsyncMock()

    result = await svc.get_busy_intervals("org-1", "2026-09-02")

    assert result.status is AvailabilityStatus.UNKNOWN
    assert result.reason_code == "invalid_timezone_or_date"
    assert result.intervals == ()
    svc.availability.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_calendar_does_not_read_timezone_or_database(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "false")
    svc = _svc()
    svc._get_org_timezone = AsyncMock(side_effect=AssertionError("disabled integration accessed DB"))
    result = await svc.get_busy_intervals("org-1", "2026-09-02")
    assert result.status is AvailabilityStatus.DISABLED
    svc._get_org_timezone.assert_not_awaited()
