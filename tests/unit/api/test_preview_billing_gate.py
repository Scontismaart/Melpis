"""Recovered preview billing gate: denied tenants never reach retrieval/provider."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth.dependencies import get_organization_context
from src.models.schemas import RispostaOutput

VALID = {
    "subscription_status": "active",
    "messages_limit": 500,
    "messages_used_this_period": 0,
}
PAYLOAD = {
    "profilo": {
        "verticale": "parrucchiere",
        "nome_attivita": "Test",
        "orari": "09:00-18:00",
        "tono": "cordiale",
        "servizi": ["Taglio"],
    },
    "messaggio": "Quali sono gli orari?",
}


@pytest.fixture
def preview(monkeypatch):
    from src.api.routes import organization

    app = FastAPI()
    app.include_router(organization.router)
    app.dependency_overrides[get_organization_context] = lambda: {
        "source": "jwt",
        "organization_id": "00000000-0000-0000-0000-000000000001",
        "ruolo": "owner",
    }
    app.state.repo = object()
    billing = AsyncMock(return_value=dict(VALID))
    generate = AsyncMock(return_value=RispostaOutput(
        risposta="Siamo aperti dalle 9 alle 18.", richiede_umano=False,
        motivo="", categoria="generico",
    ))
    record = AsyncMock()
    monkeypatch.setattr(organization, "get_billing_snapshot", billing)
    monkeypatch.setattr(organization, "generate_preview", generate)
    monkeypatch.setattr(organization, "record_ai_usage", record)
    with TestClient(app) as client:
        yield client, billing, generate, record


@pytest.mark.parametrize("state,status", [
    (None, 503),
    ({}, 503),
    ({**VALID, "ai_accounting_blocked": True}, 503),
    ({**VALID, "subscription_status": "past_due"}, 403),
    ({**VALID, "subscription_status": "canceled"}, 403),
    ({**VALID, "subscription_status": "unpaid"}, 403),
    ({**VALID, "subscription_status": "unknown"}, 403),
    ({**VALID, "subscription_status": "trialing",
      "trial_end": datetime.now(timezone.utc) - timedelta(days=1)}, 403),
    ({**VALID, "subscription_status": "trialing", "trial_end": None}, 403),
    ({**VALID, "messages_used_this_period": 500}, 403),
    ({**VALID, "messages_limit": None}, 403),
    ({**VALID, "messages_used_this_period": None}, 403),
    ({**VALID, "messages_limit": True}, 403),
    ({**VALID, "messages_used_this_period": -1}, 403),
])
def test_preview_denies_unverified_entitlement_before_generation(preview, state, status):
    client, billing, generate, record = preview
    billing.return_value = state

    response = client.post("/api/onboarding/preview", json=PAYLOAD)

    assert response.status_code == status
    generate.assert_not_awaited()
    record.assert_not_awaited()


@pytest.mark.parametrize("state", [
    VALID,
    {**VALID, "messages_used_this_period": 499},
    {**VALID, "subscription_status": "trialing",
     "trial_end": datetime.now(timezone.utc) + timedelta(days=1)},
])
def test_preview_with_verified_remaining_quota_generates_and_records(preview, state):
    client, billing, generate, record = preview
    billing.return_value = state

    response = client.post("/api/onboarding/preview", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json()["risposta"] == "Siamo aperti dalle 9 alle 18."
    generate.assert_awaited_once()
    record.assert_awaited_once()
