"""Synthetic event-listing regressions for excluding a booking's own event."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.calendar.availability import AvailabilityStatus
from src.core.calendar.service import GoogleCalendarService


START = datetime(2026, 11, 1, 5, 0, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)
CONNECTION = {
    "connection_epoch": "epoch-test",
    "calendar_id": "primary",
    "resource_calendar_ids": ["room@example.test"],
}


def _svc(monkeypatch, responses):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    service = GoogleCalendarService(MagicMock(pool=MagicMock()), "GT4pFJ9wm5vlxRS2MSmSF3tjbThnKnon-sgG5TVYILE=")
    service.execute = AsyncMock(side_effect=responses)
    return service


def _event(event_id, start, end, **fields):
    return {
        "id": event_id,
        "status": "confirmed",
        "start": start,
        "end": end,
        **fields,
    }


@pytest.mark.asyncio
async def test_own_event_is_excluded_but_foreign_overlap_and_other_resource_are_busy(monkeypatch):
    service = _svc(monkeypatch, [
        {
            "kind": "calendar#events",
            "timeZone": "America/New_York",
            "items": [
                _event("booking-event", {"dateTime": "2026-11-01T01:00:00-04:00"},
                       {"dateTime": "2026-11-01T02:00:00-05:00"}),
                _event("other-booking", {"dateTime": "2026-11-01T01:30:00-04:00"},
                       {"dateTime": "2026-11-01T02:30:00-05:00"}),
            ],
        },
        {
            "kind": "calendar#freeBusy",
            "timeMin": START.isoformat(),
            "timeMax": END.isoformat(),
            "calendars": {"room@example.test": {"busy": [{
                "start": "2026-11-01T06:30:00Z", "end": "2026-11-01T07:30:00Z",
            }]}},
        },
    ])
    provider = SimpleNamespace(
        events=lambda: SimpleNamespace(list=lambda **kwargs: kwargs),
        freebusy=lambda: SimpleNamespace(query=lambda **kwargs: kwargs),
    )

    result = await service.availability_excluding_event(
        "org-test", START, END, connection=CONNECTION, service=provider, event_id="booking-event"
    )

    assert result.status is AvailabilityStatus.BUSY
    assert result.intervals == ((datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), END),)
    assert result.connection_epoch == "epoch-test"
    assert service.execute.await_count == 2
    first_call = service.execute.await_args_list[0].args[0]
    assert first_call["calendarId"] == "primary"
    assert first_call["singleEvents"] is True
    assert first_call["timeMin"] == START.isoformat()
    assert first_call["timeMax"] == END.isoformat()
    freebusy_call = service.execute.await_args_list[1].args[0]
    assert freebusy_call["body"]["items"] == [{"id": "room@example.test"}]


@pytest.mark.asyncio
async def test_own_overlapping_event_is_free_and_all_day_uses_calendar_timezone_dst_boundary(monkeypatch):
    # New York's 2026 fall-back day runs from 04:00Z to 05:00Z the next day (25h).
    service = _svc(monkeypatch, [{
        "kind": "calendar#events",
        "timeZone": "America/New_York",
        "items": [
            _event("booking-event", {"dateTime": "2026-11-01T00:30:00-04:00"},
                   {"dateTime": "2026-11-01T02:30:00-05:00"}),
            _event("whole-day", {"date": "2026-11-01"}, {"date": "2026-11-02"}),
        ],
    }])
    provider = SimpleNamespace(events=lambda: SimpleNamespace(list=lambda **kwargs: kwargs))
    start = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)

    result = await service.availability_excluding_event(
        "org-test", start, end, connection={**CONNECTION, "resource_calendar_ids": []},
        service=provider, event_id="booking-event",
    )

    assert result.status is AvailabilityStatus.BUSY
    assert result.intervals == ((start, end),)
    assert service.execute.await_count == 1
    request = service.execute.await_args.args[0]
    assert request["calendarId"] == "primary"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pages",
    [
        [{"kind": "calendar#events", "timeZone": "UTC", "items": "not-a-list"}],
        [{"kind": "calendar#events", "timeZone": "Invalid/Zone", "items": []}],
        [
            {"kind": "calendar#events", "timeZone": "UTC", "items": [], "nextPageToken": "repeat"},
            {"kind": "calendar#events", "timeZone": "UTC", "items": [], "nextPageToken": "repeat"},
        ],
    ],
)
async def test_malformed_or_repeating_event_pages_fail_closed(monkeypatch, pages):
    service = _svc(monkeypatch, pages)
    provider = SimpleNamespace(events=lambda: SimpleNamespace(list=lambda **kwargs: kwargs))

    result = await service.availability_excluding_event(
        "org-test", START, END, connection={**CONNECTION, "resource_calendar_ids": []},
        service=provider, event_id="booking-event",
    )

    assert result.status is AvailabilityStatus.UNKNOWN
    assert result.reason_code == "event_availability_unverified"
    assert result.intervals == ()


@pytest.mark.asyncio
async def test_missing_event_identity_or_malformed_interval_fails_closed(monkeypatch):
    service = _svc(monkeypatch, [{
        "kind": "calendar#events",
        "timeZone": "UTC",
        "items": [_event("foreign", {"dateTime": "2026-11-01T07:00:00Z"}, {"dateTime": "invalid"})],
    }])
    provider = SimpleNamespace(events=lambda: SimpleNamespace(list=lambda **kwargs: kwargs))

    result = await service.availability_excluding_event(
        "org-test", START, END, connection={**CONNECTION, "resource_calendar_ids": []},
        service=provider, event_id="booking-event",
    )

    assert result.status is AvailabilityStatus.UNKNOWN
    assert result.reason_code == "event_availability_unverified"
