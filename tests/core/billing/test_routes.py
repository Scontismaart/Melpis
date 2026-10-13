import os
import pytest
import httpx
from unittest.mock import patch, MagicMock
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


async def test_get_subscription_authenticated(async_client, sample_org):
    resp = await async_client.get("/api/billing/subscription", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["subscription_status"] == "incomplete"
    assert data["messages_used_this_period"] == 0


async def test_get_usage_authenticated(async_client, sample_org):
    resp = await async_client.get("/api/billing/usage", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages_used"] == 0
    assert data["percentage"] == 0


async def test_public_billing_config_exposes_only_annual_boolean(async_client, monkeypatch):
    monkeypatch.setenv("STRIPE_ANNUAL_PRICING_ENABLED", "true")
    response = await async_client.get("/api/billing/public-config")
    assert response.status_code == 200
    assert response.json() == {"annual_pricing_enabled": True}
    monkeypatch.setenv("STRIPE_ANNUAL_PRICING_ENABLED", "invalid")
    response = await async_client.get("/api/billing/public-config")
    assert response.json() == {"annual_pricing_enabled": False}


async def test_create_portal_session_no_customer(async_client, sample_org):
    resp = await async_client.post("/api/billing/create-portal-session", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 400
    assert "No Stripe customer found" in resp.json()["detail"]
