"""Verified Google availability. Unknown is never an empty calendar."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from zoneinfo import ZoneInfo


class AvailabilityStatus(str, Enum):
    FREE = "verified_free"
    BUSY = "verified_busy"
    UNKNOWN = "unknown"
    DISABLED = "disabled_or_unconfigured"


@dataclass(frozen=True)
class CalendarAvailability:
    status: AvailabilityStatus
    intervals: tuple[tuple[datetime, datetime], ...] = ()
    resources: tuple[str, ...] = ()
    reason_code: str | None = None
    checked_at: datetime | None = None
    connection_epoch: str | None = None

    @property
    def verified(self):
        return self.status in (AvailabilityStatus.FREE, AvailabilityStatus.BUSY)


_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


def aware_utc(value):
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise ValueError("Invalid provider timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Provider timestamp is not aware")
    return parsed.astimezone(timezone.utc)


def local_instant(day, slot, timezone_name):
    """Reject nonexistent and ambiguous local times rather than guess DST."""
    zone = ZoneInfo(timezone_name)
    naive = datetime.fromisoformat(f"{day}T{slot}")
    if naive.tzinfo is not None:
        raise ValueError("Local booking time must not carry an offset")
    candidates = set()
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise ValueError("Nonexistent or ambiguous local booking time")
    return candidates.pop()


def parse_freebusy(payload, resources, starts_at, ends_at, *, connection_epoch=None):
    requested = tuple(resources)
    unknown = CalendarAvailability(AvailabilityStatus.UNKNOWN, resources=requested,
                                   reason_code="invalid_freebusy", connection_epoch=connection_epoch)
    try:
        if (not requested or len(set(requested)) != len(requested)
                or not all(isinstance(item, str) and item for item in requested)
                or starts_at.utcoffset() is None or ends_at.utcoffset() is None
                or starts_at >= ends_at or not isinstance(payload, dict)
                or payload.get("kind") != "calendar#freeBusy"
                or aware_utc(payload["timeMin"]) > starts_at
                or aware_utc(payload["timeMax"]) < ends_at
                or payload.get("groups") not in (None, {})):
            return unknown
        calendars = payload["calendars"]
        if not isinstance(calendars, dict) or set(calendars) != set(requested):
            return unknown
        intervals = []
        for resource in requested:
            result = calendars[resource]
            if (not isinstance(result, dict) or result.get("errors") not in (None, [])
                    or not isinstance(result.get("busy"), list)):
                return unknown
            for busy in result["busy"]:
                if not isinstance(busy, dict):
                    return unknown
                start, end = aware_utc(busy["start"]), aware_utc(busy["end"])
                if start >= end:
                    return unknown
                if start < ends_at and starts_at < end:
                    intervals.append((max(start, starts_at), min(end, ends_at)))
        # Union overlapping resources/events; boundaries are [start, end).
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        return CalendarAvailability(
            AvailabilityStatus.BUSY if merged else AvailabilityStatus.FREE,
            tuple(merged), requested, checked_at=datetime.now(timezone.utc),
            connection_epoch=connection_epoch,
        )
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return unknown
