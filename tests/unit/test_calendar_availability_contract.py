from datetime import datetime, timedelta, timezone

import pytest

from src.core.calendar.availability import (
    AvailabilityStatus,
    aware_utc,
    local_instant,
    parse_freebusy,
)


UTC = timezone.utc
WINDOW_START = datetime(2026, 1, 15, 9, tzinfo=UTC)
WINDOW_END = datetime(2026, 1, 15, 12, tzinfo=UTC)
RESOURCES = ("primary", "room-1")


def freebusy_payload(calendars=None, **overrides):
    payload = {
        "kind": "calendar#freeBusy",
        "timeMin": "2026-01-15T09:00:00Z",
        "timeMax": "2026-01-15T12:00:00Z",
        "calendars": calendars or {resource: {"busy": []} for resource in RESOURCES},
    }
    payload.update(overrides)
    return payload


def test_valid_empty_freebusy_is_verified_free_for_every_requested_resource():
    result = parse_freebusy(freebusy_payload(), RESOURCES, WINDOW_START, WINDOW_END,
                           connection_epoch="epoch-7")

    assert result.status is AvailabilityStatus.FREE
    assert result.verified is True
    assert result.intervals == ()
    assert result.resources == RESOURCES
    assert result.reason_code is None
    assert result.checked_at is not None
    assert result.checked_at.utcoffset() == timedelta(0)
    assert result.connection_epoch == "epoch-7"


@pytest.mark.parametrize(
    "payload, resources",
    [
        (None, RESOURCES),
        ({}, RESOURCES),
        (freebusy_payload(calendars={"primary": {"busy": []}}), RESOURCES),
        (freebusy_payload(calendars={
            "primary": {"busy": []}, "room-1": {"busy": []}, "unexpected": {"busy": []}
        }), RESOURCES),
        (freebusy_payload(calendars={
            "primary": {"busy": []}, "room-1": {"busy": [], "errors": [{"reason": "forbidden"}]}
        }), RESOURCES),
        (freebusy_payload(calendars={"primary": {"busy": []}, "room-1": {}}), RESOURCES),
        (freebusy_payload(calendars={"primary": {"busy": []}, "room-1": {"busy": ""}}), RESOURCES),
        (freebusy_payload(), ()),
        (freebusy_payload(), ("primary", "primary")),
    ],
)
def test_incomplete_or_malformed_provider_evidence_is_unknown_not_free(payload, resources):
    result = parse_freebusy(payload, resources, WINDOW_START, WINDOW_END,
                            connection_epoch="epoch-7")

    assert result.status is AvailabilityStatus.UNKNOWN
    assert result.verified is False
    assert result.intervals == ()
    assert result.reason_code == "invalid_freebusy"
    assert result.connection_epoch == "epoch-7"


def test_busy_intervals_are_utc_normalized_clipped_and_merged_across_resources():
    payload = freebusy_payload({
        "primary": {"busy": [
            {"start": "2026-01-15T08:45:00Z", "end": "2026-01-15T09:20:00Z"},
            {"start": "2026-01-15T10:30:00+01:00", "end": "2026-01-15T11:15:00+01:00"},
            {"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:30:00Z"},
        ]},
        "room-1": {"busy": [
            {"start": "2026-01-15T10:30:00Z", "end": "2026-01-15T13:00:00Z"},
        ]},
    })

    result = parse_freebusy(payload, RESOURCES, WINDOW_START, WINDOW_END)

    assert result.status is AvailabilityStatus.BUSY
    assert result.intervals == (
        (datetime(2026, 1, 15, 9, tzinfo=UTC), datetime(2026, 1, 15, 9, 20, tzinfo=UTC)),
        (datetime(2026, 1, 15, 9, 30, tzinfo=UTC), datetime(2026, 1, 15, 12, tzinfo=UTC)),
    )


def test_busy_intervals_touching_window_edges_are_exclusive():
    payload = freebusy_payload({
        "primary": {"busy": [
            {"start": "2026-01-15T08:00:00Z", "end": "2026-01-15T09:00:00Z"},
            {"start": "2026-01-15T12:00:00Z", "end": "2026-01-15T13:00:00Z"},
        ]},
        "room-1": {"busy": []},
    })

    result = parse_freebusy(payload, RESOURCES, WINDOW_START, WINDOW_END)

    assert result.status is AvailabilityStatus.FREE
    assert result.intervals == ()


@pytest.mark.parametrize(
    "local_start, local_end, expected_hours",
    [
        ("2026-03-29T00:00:00+01:00", "2026-03-30T00:00:00+02:00", 23),
        ("2026-10-25T00:00:00+02:00", "2026-10-26T00:00:00+01:00", 25),
    ],
)
def test_all_day_local_midnight_boundaries_normalize_across_dst(local_start, local_end, expected_hours):
    payload = freebusy_payload({
        "primary": {"busy": [{"start": local_start, "end": local_end}]},
        "room-1": {"busy": []},
    }, timeMin=local_start, timeMax=local_end)
    start = aware_utc(local_start)
    end = aware_utc(local_end)

    result = parse_freebusy(payload, RESOURCES, start, end)

    assert result.status is AvailabilityStatus.BUSY
    assert result.intervals == ((start, end),)
    assert end - start == timedelta(hours=expected_hours)


def test_aware_utc_requires_rfc3339_offset_and_normalizes_to_utc():
    assert aware_utc("2026-04-02T12:30:00+02:00") == datetime(2026, 4, 2, 10, 30, tzinfo=UTC)
    for value in ("2026-04-02T12:30:00", "2026-04-02", "2026-04-02T12:30:00+0200", None):
        with pytest.raises(ValueError):
            aware_utc(value)


def test_local_instant_accepts_unambiguous_time_and_returns_utc():
    assert local_instant("2026-01-15", "09:30:00", "Europe/Rome") == datetime(
        2026, 1, 15, 8, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "day, slot, timezone_name",
    [
        ("2026-10-25", "02:30:00", "Europe/Rome"),  # repeated autumn wall time
        ("2026-03-29", "02:30:00", "Europe/Rome"),  # nonexistent spring wall time
        ("2026-01-15", "09:00:00", "Not/A_Real_Zone"),
    ],
)
def test_local_instant_rejects_dst_fold_gap_and_invalid_iana_zone(day, slot, timezone_name):
    with pytest.raises((ValueError, KeyError)):
        local_instant(day, slot, timezone_name)
