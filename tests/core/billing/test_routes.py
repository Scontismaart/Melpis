import os
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Depends, HTTPException

pytestmark = pytest.mark.usefixtures("reset_db")

API_KEY = "test-api-key-12345"


@pytest.fixture(autouse=True)
def set_env():
    os.environ["DATABASE_URL"] = ""
    os.environ["API_KEY_SERVICE"] = API_KEY


@pytest.fixture
async def async_client(repo, sample_org):
    from src.api.main import app
    from src.core.auth.dependencies import get_current_user, get_organization_context, get_token
    app.state.repo = repo
    app.state.pool = MagicMock()

    async def fixed_test_identity(token=Depends(get_token)):
        if token is None:
            raise HTTPException(401, "Test session required")
        if token != f"apikey:{API_KEY}":
            raise HTTPException(403, "Invalid test session")
        return {
            "source": "jwt",
            "aal": "aal2",
            "organization_id": str(sample_org["id"]),
            "ruolo": "owner",
            "auth_user_id": "billing-test-owner",
            "user_id": None,
        }

    app.dependency_overrides[get_current_user] = fixed_test_identity
    app.dependency_overrides[get_organization_context] = fixed_test_identity
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        try:
            yield c
        finally:
            app.dependency_overrides.clear()


async def test_create_checkout_session_no_auth(async_client):
    resp = await async_client.post("/api/billing/create-checkout-session", json={
        "plan": "starter",
        "success_url": "http://localhost:5173/success",
        "cancel_url": "http://localhost:5173/cancel",
    })
    assert resp.status_code == 401


async def test_create_checkout_session_invalid_key(async_client):
    resp = await async_client.post("/api/billing/create-checkout-session", json={
        "plan": "starter",
        "success_url": "http://localhost:5173/success",
        "cancel_url": "http://localhost:5173/cancel",
    }, headers={"X-API-Key": "invalid"})
    assert resp.status_code == 403


async def test_create_checkout_session_bad_plan(async_client, sample_org):
    resp = await async_client.post("/api/billing/create-checkout-session", json={
        "plan": "nonexistent",
        "success_url": "http://localhost:5173/success",
        "cancel_url": "http://localhost:5173/cancel",
    }, headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 400


async def test_monthly_checkout_uses_server_price_and_tenant(async_client, sample_org, monkeypatch):
    from src.core.billing.plans import PLANS

    monkeypatch.setenv("APP_BASE_URL", "https://app.example.test")
    monkeypatch.setattr(PLANS["pro"], "stripe_price_id", "price_synthetic_monthly")
    stripe_spy = object()
    monkeypatch.setattr("src.core.billing.routes._get_stripe", lambda: stripe_spy)
    durable_checkout = AsyncMock(return_value={"url": "https://checkout.example.test/synthetic"})
    monkeypatch.setattr("src.core.billing.checkout.create_durable_checkout", durable_checkout)
    response = await async_client.post("/api/billing/create-checkout-session", json={
        "plan": "pro",
        "price_id": "price_ignored",
        "success_url": "https://app.example.test/app/",
        "cancel_url": "https://app.example.test/app/",
    }, headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
        "Idempotency-Key": "monthly-checkout-test",
    })
    assert response.status_code == 200
    args = durable_checkout.await_args.args
    assert args[1:3] == (str(sample_org["id"]), "monthly-checkout-test")
    assert args[3]["price_id"] == "price_synthetic_monthly"
    assert args[3]["interval"] == "monthly"
    assert args[4] is stripe_spy


async def test_get_subscription_authenticated(async_client, sample_org):
    resp = await async_client.get("/api/billing/subscription", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["subscription_status"] == "incomplete"
    assert data["messages_used_this_period"] == 0
    assert data["messages_limit"] is None


async def test_get_usage_authenticated_even_when_trial_quota_is_exhausted(async_client, sample_org, pg_pool):
    await pg_pool.execute(
        "UPDATE organizations SET subscription_status='trialing', messages_limit=150, "
        "messages_used_this_period=150, trial_start=now()-interval '6 days', "
        "trial_end=now()+interval '1 day' WHERE id=$1",
        sample_org["id"],
    )
    resp = await async_client.get("/api/billing/usage", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages_used"] == 150
    assert data["messages_limit"] == 150
    assert data["percentage"] == 100


async def test_subscription_and_usage_share_the_tenant_paid_period_counters(async_client, sample_org, pg_pool):
    headers = {"X-API-Key": API_KEY, "X-Organization-Id": str(sample_org["id"])}
    for used, period_offset, percentage in [(2400, 0, 24), (9999, 0, 99.99), (10000, 0, 100), (0, 1, 0)]:
        await pg_pool.execute(
            "UPDATE organizations SET plan='business', subscription_status='active', "
            "messages_limit=10000, messages_used_this_period=$2, whatsapp_numbers_limit=NULL, "
            "current_period_start='2026-10-01T00:00:00Z'::timestamptz + $3::int * interval '1 month', "
            "current_period_end='2026-11-01T00:00:00Z'::timestamptz + $3::int * interval '1 month' "
            "WHERE id=$1",
            sample_org["id"], used, period_offset,
        )
        subscription = await async_client.get("/api/billing/subscription", headers=headers)
        usage = await async_client.get("/api/billing/usage", headers=headers)
        assert subscription.status_code == usage.status_code == 200
        assert subscription.json()["messages_used_this_period"] == usage.json()["messages_used"] == used
        assert subscription.json()["messages_limit"] == usage.json()["messages_limit"] == 10000
        assert usage.json()["percentage"] == percentage
        assert subscription.json()["whatsapp_numbers_limit"] is None
        assert subscription.json()["current_period_start"].startswith(f"2026-{10 + period_offset}-01")


async def test_trial_read_does_not_invent_missing_quota(async_client, sample_org, pg_pool):
    await pg_pool.execute(
        "UPDATE organizations SET subscription_status='trialing', messages_limit=NULL, "
        "trial_start=now(), trial_end=now()+interval '7 days' WHERE id=$1",
        sample_org["id"],
    )
    headers = {"X-API-Key": API_KEY, "X-Organization-Id": str(sample_org["id"])}
    subscription = await async_client.get("/api/billing/subscription", headers=headers)
    usage = await async_client.get("/api/billing/usage", headers=headers)
    assert subscription.status_code == usage.status_code == 200
    assert subscription.json()["messages_limit"] is None
    assert usage.json() == {"messages_used": 0, "messages_limit": None, "percentage": None}


async def test_create_portal_session_no_customer(async_client, sample_org):
    resp = await async_client.post("/api/billing/create-portal-session", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 400
    assert "No Stripe customer found" in resp.json()["detail"]
