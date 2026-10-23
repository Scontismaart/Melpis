"""Deterministic admission policy for the version 1 automatic booking flow.

This module deliberately does not create bookings. It turns trusted booking
settings plus a structured request into either a validated local slot or a
stable, typed rejection reason for the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class AutoBookingReason(str, Enum):
    DISABLED = "disabled"
    INVALID_POLICY = "invalid_policy"
    UNSUPPORTED_AUTHORITY = "unsupported_authority"
    UNSUPPORTED_SERVICE = "unsupported_service"
    UNSUPPORTED_DURATION = "unsupported_duration"
    UNSUPPORTED_SLOT_TIME = "unsupported_slot_time"
    UNSUPPORTED_REQUIREMENT = "unsupported_requirement"
    INVALID_REQUEST = "invalid_request"
    INVALID_TIMEZONE = "invalid_timezone"
    SLOT_NOT_FUTURE = "slot_not_future"
    DST_NONEXISTENT = "dst_nonexistent"
    DST_AMBIGUOUS = "dst_ambiguous"
    OPENING_HOURS_MISSING = "opening_hours_missing"
    CLOSED_DATE = "closed_date"
    OUTSIDE_OPENING_HOURS = "outside_opening_hours"
    CAPACITY_MISSING = "capacity_missing"
    INVALID_CAPACITY = "invalid_capacity"


@dataclass(frozen=True)
class AutoBookingRequest:
    service: str
    date: str
    time: str
    party_size: int
    duration_minutes: int = 60
    resource_required: bool = False
    deposit_required: bool = False
    approval_required: bool = False


@dataclass(frozen=True)
class ValidatedAutoBooking:
    service: str
    date: date
    time: time
    party_size: int
    duration_minutes: int
    timezone: str
    starts_at: datetime
    ends_at: datetime
    authority: str
    capacity: int


@dataclass(frozen=True)
class PolicyDecision:
    accepted: bool
    reason: AutoBookingReason | None = None
    booking: ValidatedAutoBooking | None = None


@dataclass(frozen=True)
class ConfigurationDecision:
    valid: bool
    reason: AutoBookingReason | None = None


_AUTHORITIES = {"internal", "internal_calendar_sync", "external_authoritative", "google_authoritative"}
_WEEKDAYS = {
    "monday": 0, "mon": 0, "lunedi": 0, "lun": 0,
    "tuesday": 1, "tue": 1, "mar": 1,
    "wednesday": 2, "wed": 2, "mercoledi": 2, "mer": 2,
    "thursday": 3, "thu": 3, "gio": 3,
    "friday": 4, "fri": 4, "venerdi": 4, "ven": 4,
    "saturday": 5, "sat": 5, "sabato": 5, "sab": 5,
    "sunday": 6, "sun": 6, "domenica": 6, "dom": 6,
}


def validate_auto_booking_configuration(
    booking_settings: Mapping[str, Any],
    organization_timezone: str | None,
    vertical: str | None,
) -> ConfigurationDecision:
    """Validate A05 settings before an atomic settings/capacity write.

    Absent or disabled A05 config is valid. Enabled config must be fully
    explicit; this function has no calendar or persistence side effects.
    """
    config_root = booking_settings.get("config") if isinstance(booking_settings, Mapping) else None
    policy = config_root.get("a05") if isinstance(config_root, Mapping) else None
    if not isinstance(policy, Mapping) or policy.get("enabled") is not True:
        return ConfigurationDecision(True)
    if policy.get("version") != 1 or isinstance(policy.get("version"), bool):
        return _config_reject(AutoBookingReason.INVALID_POLICY)
    if not isinstance(policy.get("authority"), str) or policy["authority"] not in _AUTHORITIES:
        return _config_reject(AutoBookingReason.UNSUPPORTED_AUTHORITY)
    if vertical != "ristorante" or policy.get("verticale") != vertical:
        return _config_reject(AutoBookingReason.UNSUPPORTED_SERVICE)
    if policy.get("service") != "ristorante/tavolo":
        return _config_reject(AutoBookingReason.UNSUPPORTED_SERVICE)
    if policy.get("duration_minutes") != 60 or isinstance(policy.get("duration_minutes"), bool):
        return _config_reject(AutoBookingReason.UNSUPPORTED_DURATION)
    for flag in ("resource_required", "deposit_required", "approval_required"):
        if flag in policy and not isinstance(policy[flag], bool):
            return _config_reject(AutoBookingReason.INVALID_POLICY)
        if policy.get(flag, False):
            return _config_reject(AutoBookingReason.UNSUPPORTED_REQUIREMENT)
    if not isinstance(organization_timezone, str) or not organization_timezone.strip():
        return _config_reject(AutoBookingReason.INVALID_TIMEZONE)
    try:
        ZoneInfo(organization_timezone.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return _config_reject(AutoBookingReason.INVALID_TIMEZONE)

    schedule = policy.get("weekly_opening_intervals")
    validated_schedule = _validate_weekly_schedule(schedule)
    if validated_schedule is None or set(validated_schedule) != set(range(7)):
        return _config_reject(AutoBookingReason.OPENING_HOURS_MISSING)
    closed_dates = policy.get("closed_dates")
    if not isinstance(closed_dates, list):
        return _config_reject(AutoBookingReason.INVALID_POLICY)
    try:
        parsed_closed = [date.fromisoformat(item) for item in closed_dates if isinstance(item, str)]
    except ValueError:
        return _config_reject(AutoBookingReason.INVALID_POLICY)
    if len(parsed_closed) != len(closed_dates) or len(set(parsed_closed)) != len(parsed_closed):
        return _config_reject(AutoBookingReason.INVALID_POLICY)

    capacities = booking_settings.get("capienze_orarie") if isinstance(booking_settings, Mapping) else None
    if not isinstance(capacities, Mapping):
        return _config_reject(AutoBookingReason.CAPACITY_MISSING)
    valid_capacity_found = False
    for key, capacity in capacities.items():
        if not isinstance(key, str) or not _is_hour_slot(key):
            continue
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            continue
        valid_capacity_found = True
        break
    if not valid_capacity_found:
        return _config_reject(AutoBookingReason.CAPACITY_MISSING)
    return ConfigurationDecision(True)


def _config_reject(reason: AutoBookingReason) -> ConfigurationDecision:
    return ConfigurationDecision(False, reason)


def _is_hour_slot(value: str) -> bool:
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is None and parsed.minute == 0 and parsed.second == 0 and parsed.microsecond == 0


def parse_auto_booking_request(value: Mapping[str, Any]) -> AutoBookingRequest | None:
    """Parse a structured proposal; no natural language or model prose is read.

    Unknown fields are ignored because callers may pass an intent envelope.
    The requirement flags, when present, must be actual booleans.
    """
    if not isinstance(value, Mapping):
        return None
    service, day, slot, size = value.get("service"), value.get("date"), value.get("time"), value.get("party_size")
    if not all(isinstance(item, str) for item in (service, day, slot)):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return None
    flags = ("resource_required", "deposit_required", "approval_required")
    if any(flag in value and not isinstance(value[flag], bool) for flag in flags):
        return None
    duration = value.get("duration_minutes", 60)
    if isinstance(duration, bool) or not isinstance(duration, int):
        return None
    return AutoBookingRequest(
        service=service.strip().casefold(), date=day.strip(), time=slot.strip(), party_size=size,
        duration_minutes=duration,
        resource_required=value.get("resource_required", False),
        deposit_required=value.get("deposit_required", False),
        approval_required=value.get("approval_required", False),
    )


def parse_and_validate_auto_booking(
    booking_settings: Mapping[str, Any],
    organization_timezone: str | None,
    request: AutoBookingRequest | Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> PolicyDecision:
    """Evaluate v1 policy from booking settings, org timezone, and a proposal.

    Expected settings shape: ``{"config": {"a05": {...}},
    "capienze_orarie": {"HH:MM": positive_int}``. A05 config uses
    ``enabled``, ``version``, ``authority``, ``verticale='ristorante'``,
    ``service='ristorante/tavolo'``, ``duration_minutes=60``,
    ``closed_dates`` and ``weekly_opening_intervals``.
    Opening intervals are keyed by ISO weekday (1=Monday..7=Sunday) or an
    English/Italian weekday name and contain ``{"start", "end"}`` pairs.
    """
    config_root = booking_settings.get("config") if isinstance(booking_settings, Mapping) else None
    policy = config_root.get("a05") if isinstance(config_root, Mapping) else None
    if not isinstance(policy, Mapping) or policy.get("enabled") is not True:
        return _reject(AutoBookingReason.DISABLED)
    if policy.get("version") != 1 or isinstance(policy.get("version"), bool):
        return _reject(AutoBookingReason.INVALID_POLICY)
    authority = policy.get("authority")
    if not isinstance(authority, str) or authority not in _AUTHORITIES:
        return _reject(AutoBookingReason.UNSUPPORTED_AUTHORITY)
    if policy.get("verticale") != "ristorante" or policy.get("service") != "ristorante/tavolo":
        return _reject(AutoBookingReason.UNSUPPORTED_SERVICE)
    if policy.get("duration_minutes") != 60 or isinstance(policy.get("duration_minutes"), bool):
        return _reject(AutoBookingReason.UNSUPPORTED_DURATION)
    intervals = policy.get("weekly_opening_intervals")
    validated_schedule = _validate_weekly_schedule(intervals)
    if validated_schedule is None:
        return _reject(AutoBookingReason.OPENING_HOURS_MISSING)

    parsed = request if isinstance(request, AutoBookingRequest) else parse_auto_booking_request(request)
    if parsed is None:
        return _reject(AutoBookingReason.INVALID_REQUEST)
    if parsed.service != policy["service"]:
        return _reject(AutoBookingReason.UNSUPPORTED_SERVICE)
    if parsed.duration_minutes != policy["duration_minutes"]:
        return _reject(AutoBookingReason.UNSUPPORTED_DURATION)
    if parsed.resource_required or parsed.deposit_required or parsed.approval_required:
        return _reject(AutoBookingReason.UNSUPPORTED_REQUIREMENT)

    if not isinstance(organization_timezone, str) or not organization_timezone.strip():
        return _reject(AutoBookingReason.INVALID_TIMEZONE)
    try:
        tz = ZoneInfo(organization_timezone.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return _reject(AutoBookingReason.INVALID_TIMEZONE)

    try:
        day = date.fromisoformat(parsed.date)
        slot = time.fromisoformat(parsed.time)
        if slot.tzinfo is not None or slot.second or slot.microsecond:
            raise ValueError
    except (ValueError, TypeError):
        return _reject(AutoBookingReason.INVALID_REQUEST)
    if slot.minute != 0:
        return _reject(AutoBookingReason.UNSUPPORTED_SLOT_TIME)
    local_naive = datetime.combine(day, slot)
    candidates = []
    for fold in (0, 1):
        candidate = local_naive.replace(tzinfo=tz, fold=fold)
        round_trip = candidate.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
        if round_trip == local_naive:
            candidates.append(candidate)
    if not candidates:
        return _reject(AutoBookingReason.DST_NONEXISTENT)
    unique_offsets = {candidate.utcoffset() for candidate in candidates}
    if len(unique_offsets) > 1:
        return _reject(AutoBookingReason.DST_AMBIGUOUS)
    starts_at = candidates[0]
    local_end_naive = local_naive + timedelta(minutes=60)
    end_candidates = []
    for fold in (0, 1):
        candidate = local_end_naive.replace(tzinfo=tz, fold=fold)
        round_trip = candidate.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
        if round_trip == local_end_naive:
            end_candidates.append(candidate)
    if not end_candidates:
        return _reject(AutoBookingReason.DST_NONEXISTENT)
    if len({candidate.utcoffset() for candidate in end_candidates}) > 1:
        return _reject(AutoBookingReason.DST_AMBIGUOUS)
    ends_at = end_candidates[0]
    if ends_at.astimezone(timezone.utc) - starts_at.astimezone(timezone.utc) != timedelta(minutes=60):
        return _reject(AutoBookingReason.DST_AMBIGUOUS)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        return _reject(AutoBookingReason.INVALID_REQUEST)
    if starts_at.astimezone(timezone.utc) <= current.astimezone(timezone.utc):
        return _reject(AutoBookingReason.SLOT_NOT_FUTURE)

    closed_dates = policy.get("closed_dates")
    if not isinstance(closed_dates, list):
        return _reject(AutoBookingReason.INVALID_POLICY)
    try:
        closed = {date.fromisoformat(value) for value in closed_dates if isinstance(value, str)}
    except ValueError:
        return _reject(AutoBookingReason.INVALID_POLICY)
    if len(closed) != len(closed_dates):
        return _reject(AutoBookingReason.INVALID_POLICY)
    if day in closed:
        return _reject(AutoBookingReason.CLOSED_DATE)
    day_intervals = validated_schedule.get(day.weekday(), [])
    if not any(_inside_interval(slot, local_end_naive.time(), interval) for interval in day_intervals):
        return _reject(AutoBookingReason.OUTSIDE_OPENING_HOURS)

    capacities = booking_settings.get("capienze_orarie") if isinstance(booking_settings, Mapping) else None
    if not isinstance(capacities, Mapping) or slot.strftime("%H:%M") not in capacities:
        return _reject(AutoBookingReason.CAPACITY_MISSING)
    capacity = capacities[slot.strftime("%H:%M")]
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
        return _reject(AutoBookingReason.INVALID_CAPACITY)
    if parsed.party_size > capacity:
        return _reject(AutoBookingReason.INVALID_CAPACITY)

    return PolicyDecision(True, booking=ValidatedAutoBooking(
        service=parsed.service, date=day, time=slot, party_size=parsed.party_size,
        duration_minutes=60, timezone=tz.key, starts_at=starts_at,
        ends_at=ends_at, authority=authority, capacity=capacity,
    ))


def _reject(reason: AutoBookingReason) -> PolicyDecision:
    return PolicyDecision(False, reason=reason)


def _intervals_for_weekday(value: Any, weekday: int) -> list[tuple[time, time]] | None:
    if not isinstance(value, Mapping):
        return None
    raw = None
    for key, candidate in value.items():
        day_number = None
        if isinstance(key, int) and not isinstance(key, bool) and 1 <= key <= 7:
            day_number = key - 1
        elif isinstance(key, str):
            normalized = key.strip().casefold()
            if normalized.isdigit() and 1 <= int(normalized) <= 7:
                day_number = int(normalized) - 1
            else:
                day_number = _WEEKDAYS.get(normalized)
        if day_number == weekday:
            raw = candidate
            break
    if not isinstance(raw, list):
        return None
    parsed: list[tuple[time, time]] = []
    try:
        for item in raw:
            if not isinstance(item, Mapping):
                return None
            start, end = time.fromisoformat(item["start"]), time.fromisoformat(item["end"])
            if start.tzinfo is not None or end.tzinfo is not None or start >= end:
                return None
            parsed.append((start, end))
    except (KeyError, TypeError, ValueError):
        return None
    return parsed


def _validate_weekly_schedule(value: Any) -> dict[int, list[tuple[time, time]]] | None:
    if not isinstance(value, Mapping) or not value:
        return None
    result: dict[int, list[tuple[time, time]]] = {}
    for key in value:
        weekday = None
        if isinstance(key, int) and not isinstance(key, bool) and 1 <= key <= 7:
            weekday = key - 1
        elif isinstance(key, str):
            normalized = key.strip().casefold()
            if normalized.isdigit() and 1 <= int(normalized) <= 7:
                weekday = int(normalized) - 1
            else:
                weekday = _WEEKDAYS.get(normalized)
        if weekday is None or weekday in result:
            return None
        parsed = _intervals_for_weekday({key: value[key]}, weekday)
        if parsed is None:
            return None
        result[weekday] = parsed
    return result


def _inside_interval(start: time, end: time, interval: tuple[time, time]) -> bool:
    return interval[0] <= start and end <= interval[1]
