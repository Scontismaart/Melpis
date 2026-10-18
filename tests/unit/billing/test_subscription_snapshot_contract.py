from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from src.core.billing.webhook_handler import _validated_subscription_state


@pytest.fixture
def snapshot(monkeypatch):
    from src.core.billing.plans import PLANS
    monkeypatch.setattr(PLANS["starter"], "stripe_price_id", "price_snapshot")
    return {"status": "active", "current_period_start": 1_800_000_000,
            "current_period_end": 1_802_592_000,
            "items": {"has_more": False, "data": [{"quantity": 1, "price": {
                "id": "price_snapshot", "currency": "eur", "unit_amount": 2900,
                "recurring": {"interval": "month", "interval_count": 1}}}]}}


def paid_org():
    return {"subscription_status": "active", "plan": "starter",
            "current_period_start": datetime.fromtimestamp(1_800_000_000, timezone.utc),
            "current_period_end": datetime.fromtimestamp(1_802_592_000, timezone.utc)}


def test_exact_paid_snapshot_preserves_rights(snapshot):
    assert _validated_subscription_state(paid_org(), snapshot) == ("active", None)


@pytest.mark.parametrize("value", [True, 1.5, "1800000000", 0, -1, 10**30, 1_802_592_001])
def test_invalid_period_requires_reconciliation(snapshot, value):
    snapshot["current_period_start"] = value
    with pytest.raises(HTTPException) as error:
        _validated_subscription_state(paid_org(), snapshot)
    assert error.value.status_code == 503


def test_paid_snapshot_cannot_become_new_trial(snapshot):
    snapshot.update(status="trialing", trial_end=1_802_592_000)
    org = {**paid_org(), "trial_end": datetime.fromtimestamp(1_802_592_000, timezone.utc)}
    with pytest.raises(HTTPException) as error:
        _validated_subscription_state(org, snapshot)
    assert error.value.status_code == 503


@pytest.mark.parametrize("deadline", [True, None, 0, -1, "1802592000", 10**30])
def test_invalid_trial_deadline_never_creates_entitlement(snapshot, deadline):
    snapshot.update(status="trialing", trial_end=deadline)
    org = {**paid_org(), "subscription_status": "trialing",
           "trial_end": datetime.fromtimestamp(1_802_592_000, timezone.utc)}
    with pytest.raises(HTTPException) as error:
        _validated_subscription_state(org, snapshot)
    assert error.value.status_code == 503
