from datetime import datetime, timezone

import pytest

from src.core.bookings.auto_policy import (
    AutoBookingReason,
    parse_and_validate_auto_booking,
    parse_auto_booking_request,
    validate_auto_booking_configuration,
)


def settings(**overrides):
    a05 = {
        "enabled": True,
        "version": 1,
        "authority": "internal",
        "verticale": "ristorante",
        "service": "ristorante/tavolo",
        "duration_minutes": 60,
        "closed_dates": [],
        "weekly_opening_intervals": {"monday": [{"start": "09:00", "end": "22:00"}]},
    }
    a05.update(overrides)
    return {"config": {"a05": a05}, "capienze_orarie": {"19:00": 8}}


def request(**overrides):
    value = {"service": "ristorante/tavolo", "date": "2030-01-07", "time": "19:00", "party_size": 4}
    value.update(overrides)
    return value


def evaluate(config, proposal=None, **kwargs):
    return parse_and_validate_auto_booking(
        config, "Europe/Rome", request() if proposal is None else proposal,
        now=datetime(2030, 1, 1, tzinfo=timezone.utc), **kwargs,
    )


def test_valid_opted_in_policy_returns_typed_validated_slot():
    result = evaluate(settings())
    assert result.accepted
    assert result.reason is None
    assert result.booking is not None
    assert result.booking.capacity == 8
    assert result.booking.duration_minutes == 60
    assert result.booking.authority == "internal"


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("verticale", "hotel", AutoBookingReason.UNSUPPORTED_SERVICE),
    ("service", "ristorante", AutoBookingReason.UNSUPPORTED_SERVICE),
    ("duration_minutes", 30, AutoBookingReason.UNSUPPORTED_DURATION),
])
def test_policy_requires_explicit_supported_service_facts(field, value, reason):
    assert evaluate(settings(**{field: value})).reason is reason


@pytest.mark.parametrize("config", [{}, {"config": {}}, {"config": {"a05": {"enabled": False}}}])
def test_absent_or_disabled_policy_is_off(config):
    result = evaluate(config)
    assert result.reason is AutoBookingReason.DISABLED


@pytest.mark.parametrize(("proposal", "reason"), [
    (request(service="hotel/camera"), AutoBookingReason.UNSUPPORTED_SERVICE),
    (request(duration_minutes=30), AutoBookingReason.UNSUPPORTED_DURATION),
    (request(time="19:30"), AutoBookingReason.UNSUPPORTED_SLOT_TIME),
    (request(deposit_required=True), AutoBookingReason.UNSUPPORTED_REQUIREMENT),
    (request(resource_required=True), AutoBookingReason.UNSUPPORTED_REQUIREMENT),
    (request(approval_required=True), AutoBookingReason.UNSUPPORTED_REQUIREMENT),
    (request(party_size=0), AutoBookingReason.INVALID_REQUEST),
])
def test_unsupported_or_invalid_request_is_rejected(proposal, reason):
    assert evaluate(settings(), proposal).reason is reason


def test_only_explicit_supported_authorities_are_accepted():
    assert evaluate(settings(authority="guess" )).reason is AutoBookingReason.UNSUPPORTED_AUTHORITY
    for authority in ("internal", "internal_calendar_sync", "external_authoritative"):
        assert evaluate(settings(authority=authority)).accepted


def test_invalid_timezone_and_missing_weekly_intervals_fail_closed():
    result = parse_and_validate_auto_booking(settings(), "Mars/Olympus", request(), now=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert result.reason is AutoBookingReason.INVALID_TIMEZONE
    result = evaluate(settings(weekly_opening_intervals={}))
    assert result.reason is AutoBookingReason.OPENING_HOURS_MISSING


def test_capacity_must_be_explicit_positive_and_cover_party():
    missing = settings()
    missing["capienze_orarie"] = {}
    assert evaluate(missing).reason is AutoBookingReason.CAPACITY_MISSING
    zero = settings()
    zero["capienze_orarie"]["19:00"] = 0
    assert evaluate(zero).reason is AutoBookingReason.INVALID_CAPACITY
    too_small = settings()
    too_small["capienze_orarie"]["19:00"] = 2
    assert evaluate(too_small).reason is AutoBookingReason.INVALID_CAPACITY


def test_slot_must_be_future_and_within_opening_hours():
    past = parse_and_validate_auto_booking(settings(), "Europe/Rome", request(date="2029-12-31"), now=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert past.reason is AutoBookingReason.SLOT_NOT_FUTURE
    outside = evaluate(settings(), request(time="22:00"))
    assert outside.reason is AutoBookingReason.OUTSIDE_OPENING_HOURS


def test_daylight_saving_gap_and_fold_are_rejected():
    policy = settings(weekly_opening_intervals={"sunday": [{"start": "00:00", "end": "04:00"}]})
    gap = parse_and_validate_auto_booking(policy, "Europe/Rome", request(date="2030-03-31", time="02:00"), now=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert gap.reason is AutoBookingReason.DST_NONEXISTENT
    fold = parse_and_validate_auto_booking(policy, "Europe/Rome", request(date="2030-10-27", time="02:00"), now=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert fold.reason is AutoBookingReason.DST_AMBIGUOUS


def test_slot_spanning_dst_transition_and_closed_exception_are_rejected():
    spring = settings(weekly_opening_intervals={"sunday": [{"start": "00:00", "end": "04:00"}]})
    gap_span = parse_and_validate_auto_booking(spring, "Europe/Rome", request(date="2030-03-31", time="01:00"), now=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert gap_span.reason is AutoBookingReason.DST_NONEXISTENT

    closed = settings(closed_dates=["2030-01-07"])
    assert evaluate(closed).reason is AutoBookingReason.CLOSED_DATE


def test_invalid_interval_on_another_weekday_rejects_enabled_policy():
    result = evaluate(settings(weekly_opening_intervals={
        "monday": [{"start": "09:00", "end": "22:00"}],
        "friday": [{"start": "not-a-time", "end": "18:00"}],
    }))
    assert result.reason is AutoBookingReason.OPENING_HOURS_MISSING


def test_request_parser_does_not_accept_string_coercion_or_bool_as_integer():
    assert parse_auto_booking_request(request(party_size="4")) is None
    assert parse_auto_booking_request(request(party_size=True)) is None
    assert parse_auto_booking_request(request(deposit_required="false")) is None


def test_configuration_validation_accepts_disabled_and_complete_enabled_policy():
    assert validate_auto_booking_configuration({}, "Europe/Rome", "ristorante").valid
    full_week = {day: ([{"start": "09:00", "end": "22:00"}] if day == "monday" else []) for day in (
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    )}
    config = settings(weekly_opening_intervals=full_week)
    result = validate_auto_booking_configuration(config, "Europe/Rome", "ristorante")
    assert result.valid
    assert result.reason is None


@pytest.mark.parametrize(("config", "timezone_name", "vertical", "reason"), [
    (settings(version=2), "Europe/Rome", "ristorante", AutoBookingReason.INVALID_POLICY),
    (settings(authority="unknown"), "Europe/Rome", "ristorante", AutoBookingReason.UNSUPPORTED_AUTHORITY),
    (settings(), "invalid/zone", "ristorante", AutoBookingReason.INVALID_TIMEZONE),
    (settings(), "Europe/Rome", "studio", AutoBookingReason.UNSUPPORTED_SERVICE),
    (settings(resource_required=True), "Europe/Rome", "ristorante", AutoBookingReason.UNSUPPORTED_REQUIREMENT),
    (settings(deposit_required=True), "Europe/Rome", "ristorante", AutoBookingReason.UNSUPPORTED_REQUIREMENT),
    (settings(approval_required=True), "Europe/Rome", "ristorante", AutoBookingReason.UNSUPPORTED_REQUIREMENT),
])
def test_configuration_validation_rejects_unsupported_or_invalid_settings(config, timezone_name, vertical, reason):
    full_week = {day: [] for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}
    config["config"]["a05"]["weekly_opening_intervals"] = full_week
    result = validate_auto_booking_configuration(config, timezone_name, vertical)
    assert not result.valid
    assert result.reason is reason


def test_configuration_validation_requires_complete_week_closed_dates_and_hour_capacity():
    config = settings(weekly_opening_intervals={"monday": []})
    assert validate_auto_booking_configuration(config, "Europe/Rome", "ristorante").reason is AutoBookingReason.OPENING_HOURS_MISSING

    full_week = {day: [] for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}
    config = settings(weekly_opening_intervals=full_week, closed_dates=["not-a-date"])
    assert validate_auto_booking_configuration(config, "Europe/Rome", "ristorante").reason is AutoBookingReason.INVALID_POLICY
    config = settings(weekly_opening_intervals=full_week)
    config["capienze_orarie"] = {"19:30": 4}
    assert validate_auto_booking_configuration(config, "Europe/Rome", "ristorante").reason is AutoBookingReason.CAPACITY_MISSING
