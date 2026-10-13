import pytest

from src.core.billing.webhook_handler import (
    PRICE_TO_PLAN,
    PRODUCT_TO_PLAN,
    _init_plan_maps,
    _resolve_plan_from_subscription,
)


@pytest.fixture(autouse=True)
def _reset_plan_maps():
    PRICE_TO_PLAN.clear()
    PRODUCT_TO_PLAN.clear()
    yield
    PRICE_TO_PLAN.clear()
    PRODUCT_TO_PLAN.clear()


def _install_fake_plans(monkeypatch):
    class FakePlan:
        def __init__(self, monthly, yearly):
            self.stripe_price_id = monthly
            self.stripe_price_id_yearly = yearly

    fake_plans = {
        "starter": FakePlan("price_starter_m", "price_starter_y"),
        "pro": FakePlan("price_pro_m", "price_pro_y"),
    }
    monkeypatch.setattr(
        "src.core.billing.plans.PLANS", fake_plans, raising=False
    )
    return fake_plans


def test_price_to_plan_monthly_only_by_default(monkeypatch):
    fake_plans = _install_fake_plans(monkeypatch)
    monkeypatch.delenv("STRIPE_ANNUAL_PRICING_ENABLED", raising=False)
    _init_plan_maps()
    for slug, plan in fake_plans.items():
        assert PRICE_TO_PLAN[plan.stripe_price_id] == slug
        assert plan.stripe_price_id_yearly not in PRICE_TO_PLAN


def test_price_to_plan_includes_yearly_when_enabled(monkeypatch):
    fake_plans = _install_fake_plans(monkeypatch)
    monkeypatch.setenv("STRIPE_ANNUAL_PRICING_ENABLED", "true")
    _init_plan_maps()
    for slug, plan in fake_plans.items():
        assert PRICE_TO_PLAN[plan.stripe_price_id] == slug
        assert PRICE_TO_PLAN[plan.stripe_price_id_yearly] == slug


def test_resolve_plan_from_subscription_monthly_only_when_annual_disabled(monkeypatch):
    fake_plans = _install_fake_plans(monkeypatch)
    monkeypatch.delenv("STRIPE_ANNUAL_PRICING_ENABLED", raising=False)
    _init_plan_maps()

    for slug, plan in fake_plans.items():
        monthly = {"items": {"data": [{"price": {"id": plan.stripe_price_id}}]}}
        yearly = {"items": {"data": [{"price": {"id": plan.stripe_price_id_yearly}}]}}
        assert _resolve_plan_from_subscription(monthly) == slug
        assert _resolve_plan_from_subscription(yearly) is None


def test_resolve_plan_from_subscription_monthly_and_yearly_when_annual_enabled(monkeypatch):
    fake_plans = _install_fake_plans(monkeypatch)
    monkeypatch.setenv("STRIPE_ANNUAL_PRICING_ENABLED", "true")
    _init_plan_maps()

    for slug, plan in fake_plans.items():
        monthly = {"items": {"data": [{"price": {"id": plan.stripe_price_id}}]}}
        yearly = {"items": {"data": [{"price": {"id": plan.stripe_price_id_yearly}}]}}
        assert _resolve_plan_from_subscription(monthly) == slug
        assert _resolve_plan_from_subscription(yearly) == slug


def test_resolve_plan_unknown_price_returns_none():
    _init_plan_maps()
    unknown = {"items": {"data": [{"price": {"id": "price_sconosciuto"}}]}}
    assert _resolve_plan_from_subscription(unknown) is None
