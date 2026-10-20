import uuid
from contextlib import asynccontextmanager
from datetime import datetime
import logging
from types import SimpleNamespace
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.auth.dependencies import get_organization_context


class _QuotaReservations:
    def __init__(self, _pool):
        self.ticket = SimpleNamespace(claim_id=uuid.uuid4())
        self.reservation = {"status": "reserved", "ticket": self.ticket}
        self.response = {"risposta": "Risposta simulata", "richiede_umano": False,
                         "motivo": "", "categoria": "generico", "prenotazione": None}
        self.reserve_calls = []
        self.commit_calls = []
        self.release_calls = []

    async def reserve(self, operation):
        self.reserve_calls.append(operation)
        return self.reservation

    async def renew(self, operation, ticket):
        return ticket

    async def commit(self, operation, ticket, final):
        self.commit_calls.append((operation, ticket, final))
        return self.response

    async def release(self, operation, ticket):
        self.release_calls.append((operation, ticket))
        return True


@pytest.fixture
def client():
    return TestClient(app)


def test_recensione_unauthorized(client):
    res = client.post("/api/recensione", json={
        "testo": "Cibo ottimo e personale gentile",
        "valutazione_stelle": 5,
        "fonte": "Google",
        "autore": "Mario Rossi",
    })
    assert res.status_code == 401


def test_messaggio_invalid_profile_404(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    res = client.post("/api/messaggio?profilo_id=non_existing_xyz", json={
        "testo": "Ciao, vorrei info",
        "id_conversazione": "conv-test",
    })
    assert res.status_code == 404
    assert "non trovato" in res.json()["detail"].lower()


def test_messaggio_demo_simulation(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    mock_orchestrator = AsyncMock()
    mock_out = MagicMock()
    mock_out.response_text = "Benvenuto alla Trattoria Da Mario!"
    mock_out.richiede_umano = False
    mock_out.motivo_richiesta_umano = None
    mock_out.intent = "saluto"
    mock_out.prenotazione = None
    mock_out.disponibilita_slot = None
    mock_out.guardrail_action = None
    mock_orchestrator.orchestrate.return_value = mock_out

    with patch.object(app.state, "orchestrator", mock_orchestrator, create=True):
        res = client.post("/api/messaggio", json={
            "testo": "Buongiorno",
            "id_conversazione": f"conv-{uuid.uuid4().hex[:8]}",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["risposta"] == "Benvenuto alla Trattoria Da Mario!"
        assert data["categoria"] == "saluto"
        assert data["richiede_umano"] is False


def test_recensione_feature_blocked_by_plan(client):
    mock_org_id = str(uuid.uuid4())
    mock_user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "organization_id": mock_org_id,
        "auth_user_id": str(uuid.uuid4()),
        "ruolo": "owner",
        "email": "owner@example.com",
    }
    mock_repo = AsyncMock()
    # Assume plan 'starter' does not have review feature
    mock_repo.get_organization_billing.return_value = {
        "plan": "starter", "subscription_status": "active"
    }

    app.dependency_overrides[get_organization_context] = lambda: mock_user
    @asynccontextmanager
    async def claim(*_args, **_kwargs):
        yield True
    with patch.object(app.state, "repo", mock_repo, create=True), \
         patch("src.core.reviews.idempotency.claim_external_review", claim):
        res = client.post("/api/recensione", json={
            "testo": "Ottima cena",
            "valutazione_stelle": 5,
            "fonte": "Google",
            "autore": "Luigi",
            "external_id": "plan-blocked-review",
        })
        assert res.status_code == 403
        assert "non include la gestione delle recensioni" in res.json()["detail"]
    app.dependency_overrides.pop(get_organization_context, None)


def test_messaggio_authenticated_organization(client):
    mock_org_id = str(uuid.uuid4())
    mock_user = {
        "user_id": str(uuid.uuid4()),
        "auth_user_id": str(uuid.uuid4()),
        "organization_id": mock_org_id,
        "ruolo": "owner",
        "email": "owner@example.com",
        "source": "jwt",
    }
    mock_repo = AsyncMock()
    mock_repo.get_organization.return_value = {
        "id": mock_org_id,
        "name": "Pizzeria Napoli",
        "business_profile": {
            "nome_attivita": "Pizzeria Napoli",
            "tipo_attivita": "ristorante",
            "citta": "Napoli",
            "indirizzo": "Via Roma 1",
            "telefono": "+39081000000",
            "orari_apertura": "Tutti i giorni 19:00 - 23:30",
        },
    }
    mock_repo.get_organization_billing.return_value = {
        "plan": "business", "subscription_status": "active",
        "messages_limit": 100, "messages_used_this_period": 0,
    }
    request_id = uuid.uuid4()

    mock_orchestrator = AsyncMock()
    mock_out = MagicMock()
    mock_out.response_text = "Benvenuto a Pizzeria Napoli!"
    mock_out.richiede_umano = False
    mock_out.motivo_richiesta_umano = None
    mock_out.intent = "saluto"
    mock_out.prenotazione = None
    mock_out.disponibilita_slot = None
    mock_out.guardrail_action = None
    mock_orchestrator.orchestrate.return_value = mock_out
    quota = _QuotaReservations(None)
    quota.response = {"risposta": "Benvenuto a Pizzeria Napoli!", "richiede_umano": False,
                      "motivo": "", "categoria": "saluto", "prenotazione": None}

    app.dependency_overrides[get_organization_context] = lambda: mock_user
    with patch.object(app.state, "repo", mock_repo, create=True), \
         patch.object(app.state, "orchestrator", mock_orchestrator, create=True), \
         patch("src.core.billing.quota_reservations.QuotaReservations", return_value=quota):
        res = client.post("/api/messaggio", headers={"Idempotency-Key": str(request_id)}, json={
            "testo": "Siete aperti stasera?",
            "id_conversazione": f"conv-{uuid.uuid4().hex[:8]}",
        })
        assert res.status_code == 200
        assert res.json()["risposta"] == "Benvenuto a Pizzeria Napoli!"
        call_args = mock_orchestrator.orchestrate.call_args[0][0]
        assert call_args.organization_id == mock_org_id
        assert call_args.business_profile.nome == "Pizzeria Napoli"
        assert call_args.record_billing_usage is True
        assert call_args.simulation_request_id == quota.reserve_calls[0].id
        assert quota.reserve_calls[0].source_id == request_id
        assert call_args.simulation_claim_token == quota.ticket.claim_id
        assert "simulation_claim_token" not in call_args.model_dump()
        assert str(quota.ticket.claim_id) not in repr(call_args)
        assert len(quota.commit_calls) == 1
    app.dependency_overrides.pop(get_organization_context, None)


def test_messaggio_provider_failure_logs_only_org_and_trace(client, caplog):
    org_id = str(uuid.uuid4())
    auth_user_id = str(uuid.uuid4())
    mock_user = {
        "user_id": auth_user_id, "auth_user_id": auth_user_id,
        "organization_id": org_id, "ruolo": "owner", "email": "owner@example.com",
        "source": "jwt",
    }
    mock_repo = AsyncMock()
    mock_repo.get_organization_billing.return_value = {
        "plan": "business", "subscription_status": "active",
        "messages_limit": 100, "messages_used_this_period": 0,
    }
    mock_repo.get_organization.return_value = {
        "id": org_id, "name": "Test", "business_profile": {
            "nome_attivita": "Test", "tipo_attivita": "ristorante",
        },
    }
    quota = _QuotaReservations(None)
    mock_orchestrator = AsyncMock()
    mock_orchestrator.orchestrate.side_effect = RuntimeError("provider-secret-response")
    app.dependency_overrides[get_organization_context] = lambda: mock_user
    caplog.set_level(logging.ERROR)
    try:
        with patch.object(app.state, "repo", mock_repo, create=True), \
             patch.object(app.state, "orchestrator", mock_orchestrator, create=True), \
             patch("src.core.billing.quota_reservations.QuotaReservations", return_value=quota):
            response = client.post(
                "/api/messaggio", headers={
                    "Idempotency-Key": str(uuid.uuid4()), "X-Request-ID": "trace-review-001",
                }, json={"testo": "Ciao", "id_conversazione": "conversation-1"},
            )
        assert response.status_code == 502
        assert "provider-secret-response" not in response.text
        assert "provider-secret-response" not in caplog.text
        assert f"org_id={org_id}" in caplog.text
        assert "trace_id=trace-review-001" in caplog.text
        assert len(quota.release_calls) == 1
    finally:
        app.dependency_overrides.pop(get_organization_context, None)
