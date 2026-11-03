from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from src.core.gdpr.product_policy import (
    APPROVAL_STATUS,
    POLICY_VERSION,
    PRODUCT_CATEGORIES,
    product_deadline,
    product_policy_payload,
    retention_days_allowed,
)


def test_product_policy_registry_is_immutable_and_payload_is_detached():
    assert isinstance(PRODUCT_CATEGORIES, tuple)
    with pytest.raises(FrozenInstanceError):
        PRODUCT_CATEGORIES[0].maximum = 1

    first = product_policy_payload(execution_flag=False)
    first["categories"][0]["maximum"] = 1
    first["categories"].clear()
    second = product_policy_payload(execution_flag=False)

    assert len(second["categories"]) == len(PRODUCT_CATEGORIES)
    assert second["categories"][0]["maximum"] == 90


def test_product_registry_contains_approved_categories_and_limits():
    rules = {rule.category: rule for rule in PRODUCT_CATEGORIES}

    assert set(rules) == {
        "messages_content",
        "booking_conversation_content",
        "technical_receipts",
        "completed_bookings_and_stays",
        "backups",
        "financial_records",
        "security_evidence",
    }
    assert (rules["messages_content"].maximum,
            rules["messages_content"].ordinary_availability_days) == (90, 60)
    assert (rules["booking_conversation_content"].maximum,
            rules["booking_conversation_content"].anchor) == (30, "last_pertinent_activity")
    assert (rules["technical_receipts"].maximum,
            rules["technical_receipts"].anchor) == (90, "request_concluded_at")
    assert (rules["completed_bookings_and_stays"].maximum,
            rules["completed_bookings_and_stays"].unit,
            rules["completed_bookings_and_stays"].anchor) == (
                12, "calendar_months", "booking_or_stay_concluded_at",
            )
    assert (rules["backups"].maximum, rules["backups"].unit) == (30, "days")
    assert rules["financial_records"].maximum is None
    assert rules["security_evidence"].maximum is None

    # Only message and C2.4 categories have supported automatic day schedules.
    assert retention_days_allowed("messages_content", 90)
    assert retention_days_allowed("booking_conversation_content", 30)
    assert not retention_days_allowed("messages_content", 91)
    assert not retention_days_allowed("technical_receipts", 90)
    assert not retention_days_allowed("completed_bookings_and_stays", 365)
    assert not retention_days_allowed("backups", 30)
    assert not retention_days_allowed("financial_records", 30)
    assert not retention_days_allowed("security_evidence", 30)
    assert not retention_days_allowed("simulator_responses", 1)


@pytest.mark.parametrize("days", [True, False, 1.0, 1.5, "30", None])
def test_retention_day_schedule_requires_strict_integer(days):
    assert not retention_days_allowed("messages_content", days)


@pytest.mark.parametrize(
    ("category", "days"),
    [("messages_content", 0), ("messages_content", 91),
     ("booking_conversation_content", 0), ("booking_conversation_content", 31),
     ("unknown_category", 1)],
)
def test_retention_day_schedule_rejects_out_of_range_and_unknown_category(category, days):
    assert not retention_days_allowed(category, days)


def test_product_deadline_uses_calendar_months_and_clamps_month_end():
    leap_day = datetime(2024, 2, 29, 10, 30, tzinfo=timezone.utc)
    non_leap_february = datetime(2023, 2, 28, 10, 30, tzinfo=timezone.utc)
    after_non_leap_february = datetime(2023, 3, 1, 10, 30, tzinfo=timezone.utc)
    ordinary_day = datetime(2024, 1, 31, 8, 15, tzinfo=timezone.utc)

    assert product_deadline("completed_bookings_and_stays", leap_day) == datetime(
        2025, 2, 28, 10, 30, tzinfo=timezone.utc,
    )
    assert product_deadline("completed_bookings_and_stays", non_leap_february) == datetime(
        2024, 2, 28, 10, 30, tzinfo=timezone.utc,
    )
    march_deadline = product_deadline("completed_bookings_and_stays", after_non_leap_february)
    assert march_deadline == datetime(
        2024, 3, 1, 10, 30, tzinfo=timezone.utc,
    )
    assert (march_deadline - after_non_leap_february).days == 366
    assert product_deadline("completed_bookings_and_stays", ordinary_day) == datetime(
        2025, 1, 31, 8, 15, tzinfo=timezone.utc,
    )


def test_product_deadline_uses_the_approved_day_caps_and_anchors():
    anchor = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    assert product_deadline("messages_content", anchor) == anchor + timedelta(days=90)
    assert product_deadline("booking_conversation_content", anchor) == anchor + timedelta(days=30)
    assert product_deadline("technical_receipts", anchor) == anchor + timedelta(days=90)
    assert product_deadline("backups", anchor) == anchor + timedelta(days=30)
    assert product_deadline("completed_bookings_and_stays", anchor) == anchor.replace(year=2027)


def test_product_deadline_requires_timezone_and_supported_category_schedule():
    with pytest.raises(ValueError, match="timezone_aware_anchor_required"):
        product_deadline("messages_content", datetime(2024, 1, 1))
    with pytest.raises(ValueError, match="documented_category_schedule_required"):
        product_deadline("financial_records", datetime(2024, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="documented_category_schedule_required"):
        product_deadline("simulator_responses", datetime(2024, 1, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize("execution_flag", [False, True])
def test_execution_flag_never_certifies_enforcement(execution_flag):
    payload = product_policy_payload(execution_flag=execution_flag)

    assert payload["policy_version"] == POLICY_VERSION
    assert payload["product_approval"] == APPROVAL_STATUS
    assert payload["retention_days"] is None
    assert payload["purge_after_days"] is None
    assert payload["product_retention_days"] == 60
    assert payload["product_purge_after_days"] == 90
    assert payload["automatic_erasure_enabled"] is execution_flag
    assert payload["enforcement_status"] == "not_certified_operational_go_required"
    assert payload["legacy_days_scope"] == "effective_tenant_schedule_not_certified"
    assert payload["simulator_response_days"] is None

