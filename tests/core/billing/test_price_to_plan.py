import copy
import pytest
from src.core.billing.webhook_handler import _resolve_plan_from_subscription


@pytest.fixture
def monthly(monkeypatch):
    from src.core.billing.plans import PLANS
    monkeypatch.setattr(PLANS["starter"], "stripe_price_id", "price_starter_monthly")
    monkeypatch.setattr(PLANS["starter"], "stripe_price_id_yearly", "price_starter_yearly")
    return {"items": {"has_more": False, "data": [{"quantity": 1, "price": {
        "id": "price_starter_monthly", "currency": "eur", "unit_amount": 2900,
        "recurring": {"interval": "month", "interval_count": 1}}}]}}


def test_resolve_complete_monthly(monthly):
    assert _resolve_plan_from_subscription(monthly) == "starter"


@pytest.mark.parametrize("field,value", [
    ("id", "price_starter_yearly"), ("id", "price_unknown"),
    ("currency", "usd"), ("unit_amount", 1), ("unit_amount", True),
    ("recurring", {"interval": "year", "interval_count": 1}),
    ("recurring", {"interval": "month", "interval_count": 2}),
    ("recurring", {"interval": "month", "interval_count": True}),
])
def test_invalid_catalog_rejected(monthly, field, value):
    monthly["items"]["data"][0]["price"][field] = value
    monthly["items"]["data"][0]["price"]["product"] = "prod_starter"
    assert _resolve_plan_from_subscription(monthly) is None


@pytest.mark.parametrize("quantity", [True, 0, 2, "1", None])
def test_invalid_quantity_rejected(monthly, quantity):
    monthly["items"]["data"][0]["quantity"] = quantity
    assert _resolve_plan_from_subscription(monthly) is None


def test_pagination_and_multiple_items_rejected(monthly):
    monthly["items"]["has_more"] = True
    assert _resolve_plan_from_subscription(monthly) is None
    monthly["items"].pop("has_more")
    assert _resolve_plan_from_subscription(monthly) is None
    monthly["items"]["has_more"] = False
    monthly["items"]["data"].append(copy.deepcopy(monthly["items"]["data"][0]))
    assert _resolve_plan_from_subscription(monthly) is None


def test_duplicate_price_configuration_rejected(monthly, monkeypatch):
    from src.core.billing.plans import PLANS
    monkeypatch.setattr(PLANS["pro"], "stripe_price_id", "price_starter_monthly")
    assert _resolve_plan_from_subscription(monthly) is None


@pytest.mark.parametrize(("slug", "price_id", "amount"), [
    ("starter", "price_starter_monthly", 2900),
    ("pro", "price_pro_monthly", 6900),
    ("business", "price_business_monthly", 14900),
])
def test_approved_monthly_catalog_mapping(slug, price_id, amount, monkeypatch):
    from src.core.billing.plans import PLANS

    for plan_slug, plan in PLANS.items():
        monkeypatch.setattr(plan, "stripe_price_id", price_id if plan_slug == slug else f"price_{plan_slug}_monthly")
    subscription = {"items": {"has_more": False, "data": [{
        "quantity": 1,
        "price": {
            "id": price_id,
            "currency": "eur",
            "unit_amount": amount,
            "recurring": {"interval": "month", "interval_count": 1},
        },
    }]}}

    assert _resolve_plan_from_subscription(subscription) == slug
