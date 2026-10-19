"""Recovered preview billing gate: denied tenants never reach retrieval/provider."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth.dependencies import get_organization_context
from src.models.schemas import RispostaOutput
from tests.provider_accounting_fakes import provider_context_stub

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
    context = provider_context_stub(
        task_type="onboarding_preview",
        organization_id="00000000-0000-0000-0000-000000000001",
    )
    open_context = AsyncMock(return_value=context)
    monkeypatch.setattr(organization, "enforce_org_rate_limit", AsyncMock())
    monkeypatch.setattr(organization, "get_billing_snapshot", billing)
    monkeypatch.setattr(organization, "generate_preview", generate)
    monkeypatch.setattr(organization.ProviderAccountingContext, "open", open_context)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, billing, generate, open_context, context


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
    client, billing, generate, open_context, _context = preview
    billing.return_value = state

    response = client.post("/api/onboarding/preview", json=PAYLOAD)

    assert response.status_code == status
    generate.assert_not_awaited()
    open_context.assert_not_awaited()


@pytest.mark.parametrize("state", [
    VALID,
    {**VALID, "messages_used_this_period": 499},
    {**VALID, "subscription_status": "trialing",
     "trial_end": datetime.now(timezone.utc) + timedelta(days=1)},
])
def test_preview_with_verified_remaining_quota_generates_and_records(preview, state):
    client, billing, generate, open_context, context = preview
    billing.return_value = state

    response = client.post("/api/onboarding/preview", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json()["risposta"] == "Siamo aperti dalle 9 alle 18."
    generate.assert_awaited_once()
    assert generate.await_args.kwargs["provider_context"] is context
    open_context.assert_awaited_once()
    assert open_context.await_args.kwargs["task_type"] == "onboarding_preview"
    assert context._closed is True


def test_preview_records_attempts_when_generation_raises(preview):
    client, _, generate, open_context, context = preview
    generate.side_effect = TimeoutError("provider timed out")

    response = client.post("/api/onboarding/preview", json=PAYLOAD)

    assert response.status_code == 500
    open_context.assert_awaited_once()
    assert context._closed is True


def test_preview_accounting_failure_never_dispatches_provider(preview):
    client, _, generate, open_context, _context = preview
    open_context.side_effect = RuntimeError("ledger unavailable")

    response = client.post("/api/onboarding/preview", json=PAYLOAD)

    assert response.status_code == 500
    generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    None, {}, "not-a-billing-snapshot",
    {**VALID, "ai_accounting_blocked": True},
    {**VALID, "subscription_status": "past_due"},
    {**VALID, "subscription_status": "canceled"},
    {**VALID, "subscription_status": "unknown"},
    {**VALID, "subscription_status": "trialing", "trial_end": None},
    {**VALID, "subscription_status": "trialing", "trial_end": "unverified"},
    {**VALID, "subscription_status": "trialing", "trial_end": datetime.now(timezone.utc) - timedelta(days=1)},
    {**VALID, "messages_limit": True},
    {**VALID, "messages_used_this_period": -1},
    {**VALID, "messages_used_this_period": 500},
])
async def test_direct_preview_denies_before_rag_and_provider(monkeypatch, state):
    from src.core import onboarding
    from src.core.billing.suspension import AIGenerationBlocked

    retrieve = AsyncMock()
    generate = AsyncMock()
    monkeypatch.setattr(onboarding, "recupera_contesto_documenti", retrieve)
    monkeypatch.setattr(onboarding, "genera_risposta_async", generate)
    with pytest.raises(AIGenerationBlocked):
        await onboarding.generate_preview("synthetic-org", object(), object(), billing=state)
    retrieve.assert_not_awaited()
    generate.assert_not_awaited()
