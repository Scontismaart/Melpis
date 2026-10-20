from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from fastapi import HTTPException, Response

from src.api.routes.simulator import ricevi_messaggio
from src.core.receptionist.models import OrchestrationOutput
from src.models.schemas import MessaggioInput
from src.models.schemas import ProfiloAttivita


class _QuotaReservations:
    def __init__(self, _pool):
        self.ticket = SimpleNamespace(claim_id=uuid.uuid4())
        self.reservation = {"status": "reserved", "ticket": self.ticket}
        self.response = {"risposta": "Salve", "richiede_umano": False,
                         "motivo": "", "categoria": "faq", "prenotazione": None}
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


def _request(repo):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=repo)),
        state=SimpleNamespace(),
    )


def _user():
    return {
        "source": "jwt",
        "organization_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "auth_user_id": str(uuid.uuid4()),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "billing,status",
    [
        (None, 503),
        ({"ai_accounting_blocked": True, "subscription_status": "active"}, 503),
        ({"subscription_status": "canceled"}, 403),
    ],
)
async def test_simulator_billing_gate_prevents_orchestrator(billing, status):
    user = _user()
    repo = MagicMock()
    repo.get_organization = AsyncMock()
    with patch("src.api.routes.simulator.get_optional_organization_context", new=AsyncMock(return_value=user)), \
         patch("src.api.routes.simulator.get_billing_snapshot", new=AsyncMock(return_value=billing)), \
         patch("src.api.routes.simulator.get_orchestrator") as get_orchestrator:
        with pytest.raises(HTTPException) as exc:
            await ricevi_messaggio(_request(repo), MessaggioInput(testo="Ciao"), Response())
    assert exc.value.status_code == status
    get_orchestrator.assert_not_called()
    repo.get_organization.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticated_org_without_profile_returns_409_without_demo_fallback(monkeypatch):
    user = _user()
    repo = MagicMock()
    repo.get_organization_billing = AsyncMock()
    repo.get_organization = AsyncMock(return_value={"name": "Org", "business_profile": None})
    with patch("src.api.routes.simulator.get_optional_organization_context", new=AsyncMock(return_value=user)), \
         patch("src.api.routes.simulator.get_billing_snapshot", new=AsyncMock(return_value={
             "subscription_status": "active", "messages_limit": 100, "messages_used_this_period": 0,
         })), \
         patch("src.api.routes.simulator.is_demo_mode", return_value=True), \
         patch("src.api.routes.simulator.get_orchestrator") as get_orchestrator:
        with pytest.raises(HTTPException) as exc:
            await ricevi_messaggio(_request(repo), MessaggioInput(testo="Ciao"), Response())
    assert exc.value.status_code == 409
    get_orchestrator.assert_not_called()


@pytest.mark.asyncio
async def test_simulator_history_key_includes_org_user_and_client_session():
    user = _user()
    repo = MagicMock()
    repo.get_organization = AsyncMock(return_value={"name": "Org", "business_profile": {"nome": "Org"}})
    quota = _QuotaReservations(None)
    history = MagicMock()
    orch = MagicMock()
    orch.orchestrate = AsyncMock(return_value=OrchestrationOutput(
        response_text="Salve", source="test", intent="faq", richiede_umano=False,
    ))
    object.__setattr__(orch.orchestrate.return_value, "_quota_final", object())
    with patch("src.api.routes.simulator.get_optional_organization_context", new=AsyncMock(return_value=user)), \
         patch("src.api.routes.simulator.get_billing_snapshot", new=AsyncMock(return_value={
             "subscription_status": "active", "messages_limit": 100, "messages_used_this_period": 0,
         })), \
         patch("src.api.routes.simulator.get_orchestrator", return_value=orch), \
         patch("src.core.billing.quota_reservations.QuotaReservations", return_value=quota), \
         patch("src.api.routes.simulator.conv_store", history), \
         patch("src.whatsapp.inbound_processor._profile_from_dict", return_value=ProfiloAttivita(
             nome="Org", tipo_attivita="ristorante", tono="cordiale", orari="",
         )):
        await ricevi_messaggio(
            _request(repo),
            MessaggioInput(testo="Ciao", id_conversazione="page-session-123"),
            Response(),
            str(uuid.uuid4()),
        )
    key = history.recupera_cronologia.call_args.args[0]
    assert key == f"{user['organization_id']}:{user['user_id']}:page-session-123"
    assert orch.orchestrate.await_args.args[0].record_billing_usage is True
    assert len(quota.reserve_calls) == 1
    assert len(quota.commit_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reservation,status",
    [
        ({"status": "payload_conflict"}, 409),
        ({"status": "in_progress"}, 409),
        ({"status": "quota_exceeded"}, 429),
    ],
)
async def test_idempotency_conflict_in_progress_and_quota_fail_before_orchestration(reservation, status):
    user = _user()
    repo = MagicMock()
    repo.get_organization = AsyncMock(return_value={"name": "Org", "business_profile": {"nome": "Org"}})
    quota = _QuotaReservations(None)
    quota.reservation = reservation
    orch = MagicMock()
    with patch("src.api.routes.simulator.get_optional_organization_context", new=AsyncMock(return_value=user)), \
         patch("src.api.routes.simulator.get_billing_snapshot", new=AsyncMock(return_value={
             "subscription_status": "active", "messages_limit": 5, "messages_used_this_period": 0,
         })), \
         patch("src.api.routes.simulator.get_orchestrator", return_value=orch), \
         patch("src.core.billing.quota_reservations.QuotaReservations", return_value=quota), \
         patch("src.whatsapp.inbound_processor._profile_from_dict", return_value=ProfiloAttivita(
             nome="Org", tipo_attivita="ristorante", tono="cordiale", orari="",
         )):
        with pytest.raises(HTTPException) as exc:
            await ricevi_messaggio(
                _request(repo), MessaggioInput(testo="Ciao"), Response(), str(uuid.uuid4())
            )
    assert exc.value.status_code == status
    orch.orchestrate.assert_not_called()
    assert quota.commit_calls == []


@pytest.mark.asyncio
async def test_completed_same_key_returns_cached_response_without_orchestration():
    user = _user()
    repo = MagicMock()
    repo.get_organization = AsyncMock(return_value={"name": "Org", "business_profile": {"nome": "Org"}})
    quota = _QuotaReservations(None)
    quota.reservation = {
        "status": "replay",
        "response": {"risposta": "Risposta già salvata", "richiede_umano": False,
                     "motivo": "", "categoria": "faq", "prenotazione": None},
    }
    orch = MagicMock()
    with patch("src.api.routes.simulator.get_optional_organization_context", new=AsyncMock(return_value=user)), \
         patch("src.api.routes.simulator.get_billing_snapshot", new=AsyncMock(return_value={
             "subscription_status": "active", "messages_limit": 1, "messages_used_this_period": 1,
         })), \
         patch("src.api.routes.simulator.get_orchestrator", return_value=orch), \
         patch("src.core.billing.quota_reservations.QuotaReservations", return_value=quota), \
         patch("src.whatsapp.inbound_processor._profile_from_dict", return_value=ProfiloAttivita(
             nome="Org", tipo_attivita="ristorante", tono="cordiale", orari="",
         )):
        response = Response()
        result = await ricevi_messaggio(
            _request(repo), MessaggioInput(testo="Ciao"), response, str(uuid.uuid4())
        )
    assert result.risposta == "Risposta già salvata"
    assert response.headers["Idempotency-Replayed"] == "true"
    orch.orchestrate.assert_not_called()
    assert quota.commit_calls == []


def test_simulation_never_exposes_tenant_airtable_records_to_model():
    from src.core.receptionist.conversation_orchestrator import filter_simulation_airtable_tools

    tools = [
        SimpleNamespace(name="airtable_find_customer"),
        SimpleNamespace(name="airtable_search_records"),
        SimpleNamespace(name="airtable_create_customer"),
        SimpleNamespace(name="unexpected_mutation"),
    ]
    assert filter_simulation_airtable_tools(tools, True) == []
    assert filter_simulation_airtable_tools(tools, False) == tools
