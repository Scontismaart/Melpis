import os
import uuid
import json
from unittest.mock import patch

import httpx
import pytest

API_KEY = "test-api-key-12345"

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def set_env():
    os.environ["DATABASE_URL"] = ""
    os.environ["API_KEY_SERVICE"] = API_KEY


@pytest.fixture
async def async_client(repo, pg_pool, install_test_identity, sample_org, other_org):
    from src.api.main import app
    auth_user_id = uuid.uuid4()
    await pg_pool.execute(
        "INSERT INTO auth.users(id,email) VALUES($1,$2)",
        auth_user_id, f"document-qa-{auth_user_id}@example.test",
    )
    profile_id = await pg_pool.fetchval(
        "SELECT id FROM user_profiles WHERE auth_user_id=$1", auth_user_id,
    )
    await pg_pool.execute(
        "INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) "
        "VALUES($1,$2,'owner',now())",
        sample_org["id"], profile_id,
    )
    await pg_pool.execute(
        "UPDATE organizations SET subscription_status = 'active', plan = 'business', "
        "subscription_id = 'sub_documentQAtest', current_period_start = now() - interval '1 day', "
        "current_period_end = now() + interval '29 days', messages_limit = 500, "
        "messages_used_this_period = 0 WHERE id = ANY($1::uuid[])",
        [sample_org["id"], other_org["id"]],
    )
    install_test_identity(
        app, API_KEY, default_org_id=sample_org["id"], user_id=str(auth_user_id),
    )
    app.state.a04_test_auth_user_id = auth_user_id
    app.state.repo = repo
    app.state.pool = pg_pool
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        try:
            yield c
        finally:
            app.dependency_overrides.clear()
            await pg_pool.execute("DELETE FROM auth.users WHERE id=$1", auth_user_id)


def _headers(org_id):
    return {"X-API-Key": API_KEY, "X-Organization-Id": str(org_id)}


class _FakeLLM:
    calls = 0

    def call(self, prompt):
        type(self).calls += 1
        return "La pizza margherita costa 8 euro."

    async def acall(self, prompt):
        return self.call(prompt)


async def test_chiedi_no_auth(async_client):
    resp = await async_client.post("/api/documenti/chiedi", json={"domanda": "quanto costa?"})
    assert resp.status_code == 401


async def test_carica_e_chiedi_org_scoped(async_client, sample_org, other_org, pg_pool):
    from src.api.main import app
    auth_user_id = app.state.a04_test_auth_user_id
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM organization_memberships om "
        "JOIN user_profiles up ON up.id=om.user_id "
        "WHERE om.organization_id=$1 AND up.auth_user_id=$2 AND om.joined_at IS NOT NULL",
        sample_org["id"], auth_user_id,
    ) == 1
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM auth_access_lifecycle WHERE auth_user_id=$1 AND disabled_at IS NULL",
        auth_user_id,
    ) == 1
    with patch("src.api.main.vettorizza", return_value=[[0.1] * 384]):
        resp = await async_client.post("/api/documenti/carica", json={
            "testo": "Pizza margherita: 8 euro. Pizza diavola: 9 euro.",
            "nome": "menu.pdf",
        }, headers=_headers(sample_org["id"]))
    assert resp.status_code == 200
    doc_id = resp.json()["id"]

    elenco = await async_client.get("/api/documenti/elenco", headers=_headers(sample_org["id"]))
    assert elenco.status_code == 200
    assert any(d["id"] == doc_id for d in elenco.json()["documenti"])
    altra = await async_client.get("/api/documenti/elenco", headers=_headers(other_org["id"]))
    assert altra.status_code == 200
    assert all(d["id"] != doc_id for d in altra.json()["documenti"])

    request_key = str(uuid.uuid4())
    qa_headers = {**_headers(sample_org["id"]), "Idempotency-Key": request_key}
    _FakeLLM.calls = 0
    with patch("src.core.documenti.qa_agent.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.documenti.qa_agent.crea_llm", return_value=_FakeLLM()):
        r = await async_client.post("/api/documenti/chiedi", json={"domanda": "quanto costa la pizza?"},
                                    headers=qa_headers)
        used_once = await pg_pool.fetchval(
            "SELECT messages_used_this_period FROM organizations WHERE id=$1", sample_org["id"]
        )
        replay = await async_client.post("/api/documenti/chiedi", json={"domanda": "quanto costa la pizza?"},
                                         headers=qa_headers)
    assert r.status_code == 200, r.text
    assert r.json()["risposta"] == "La pizza margherita costa 8 euro."
    assert set(r.json()) == {"risposta", "fonti"}
    assert replay.status_code == 200 and replay.json() == r.json()
    used_after_replay = await pg_pool.fetchval(
        "SELECT messages_used_this_period FROM organizations WHERE id=$1", sample_org["id"]
    )
    assert used_after_replay == used_once
    assert _FakeLLM.calls == 1


async def test_simulator_route_commits_once_and_replays_durable_response(
    async_client, sample_org, pg_pool,
):
    from src.api.main import app
    from src.core.auth.dependencies import get_organization_context
    from src.core.billing.quota_reservations import validate_final_response
    from src.core.receptionist.models import OrchestrationOutput
    from src.models.schemas import RispostaOutput

    await pg_pool.execute(
        "UPDATE organizations SET subscription_id='sub_simulatorA04', "
        "current_period_start=now()-interval '1 day', current_period_end=now()+interval '29 days', "
        "messages_limit=500, messages_used_this_period=0, "
        "business_profile=$2::jsonb WHERE id=$1",
        sample_org["id"], json.dumps({
            "nome_attivita": "Pizzeria A04", "tipo_attivita": "ristorante",
            "citta": "Roma", "orari_apertura": "Sempre aperto",
        }),
    )

    class Orchestrator:
        calls = 0

        async def orchestrate(self, request):
            self.calls += 1
            response = RispostaOutput(
                risposta="Siamo aperti oggi.", richiede_umano=False,
                motivo="", categoria="faq",
            )
            output = OrchestrationOutput(
                response_text=response.risposta, source="llm", intent="faq",
                richiede_umano=False,
            )
            output._quota_final = validate_final_response(
                request.quota_operation, request.quota_ticket, response,
            )
            return output

    orchestrator = Orchestrator()
    app.state.orchestrator = orchestrator
    auth_user_id = app.state.a04_test_auth_user_id
    app.dependency_overrides[get_organization_context] = lambda: {
        "source": "jwt", "organization_id": str(sample_org["id"]),
        "auth_user_id": str(auth_user_id), "user_id": str(auth_user_id), "ruolo": "owner",
    }
    request_id = str(uuid.uuid4())
    headers = {**_headers(sample_org["id"]), "Idempotency-Key": request_id}
    payload = {"testo": "Siete aperti oggi?", "id_conversazione": "a04-simulator-test"}
    first = await async_client.post("/api/messaggio", json=payload, headers=headers)
    used_after_first = await pg_pool.fetchval(
        "SELECT messages_used_this_period FROM organizations WHERE id=$1", sample_org["id"],
    )
    replay = await async_client.post("/api/messaggio", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["risposta"] == "Siamo aperti oggi."
    assert replay.status_code == 200 and replay.json() == first.json()
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert set(first.json()) <= {"risposta", "richiede_umano", "motivo", "categoria", "prenotazione"}
    assert used_after_first == 1
    assert await pg_pool.fetchval(
        "SELECT messages_used_this_period FROM organizations WHERE id=$1", sample_org["id"],
    ) == 1
    assert orchestrator.calls == 1
    stored = await pg_pool.fetchrow(
        "SELECT status,response FROM simulation_requests WHERE organization_id=$1 AND request_id="
        "(SELECT id FROM usage_events WHERE organization_id=$1 AND event_type='system.quota_reservation' "
        "AND metadata->>'channel'='simulator' LIMIT 1)", sample_org["id"],
    )
    assert stored is not None and stored["status"] == "completed"


async def test_delete_cross_tenant_non_tocca_org_altrui(async_client, sample_org, other_org):
    with patch("src.api.main.vettorizza", return_value=[[0.1] * 384]):
        resp = await async_client.post("/api/documenti/carica", json={
            "testo": "Documento segreto dell'org A",
            "nome": "segreti.txt",
        }, headers=_headers(sample_org["id"]))
    assert resp.status_code == 200
    doc_id = resp.json()["id"]

    resp_b = await async_client.delete(f"/api/documenti/{doc_id}", headers=_headers(other_org["id"]))
    assert resp_b.status_code == 404

    resp_a = await async_client.delete(f"/api/documenti/{doc_id}", headers=_headers(sample_org["id"]))
    assert resp_a.status_code == 200


async def test_carica_file_org_scoped(async_client, sample_org, other_org):
    with patch("src.api.main.vettorizza", return_value=[[0.1] * 384]):
        resp = await async_client.post(
            "/api/documenti/carica-file",
            files={"file": ("menu.txt", b"Antipasto della casa: 12 euro", "text/plain")},
            headers=_headers(sample_org["id"]),
        )
    assert resp.status_code == 200
    doc_id = resp.json()["id"]
    altra = await async_client.get("/api/documenti/elenco", headers=_headers(other_org["id"]))
    assert all(d["id"] != doc_id for d in altra.json()["documenti"])


async def test_upload_documento_invalida_cache_faq(async_client, repo, pg_pool, sample_org):
    """Task 12: nuovo documento = knowledge base cambiata = le risposte FAQ
    in cache potrebbero essere superate (prezzi vecchi). L'upload svuota
    la cache dell'org (e solo quella)."""
    from src.whatsapp.repository import Repository as WhatsAppRepository

    wrepo = WhatsAppRepository(pool=pg_pool)
    await wrepo.faq_cache_store(str(sample_org["id"]), "quanto costa?", "8 euro", [0.1] * 384)

    with patch("src.api.main.vettorizza", return_value=[[0.1] * 384]):
        resp = await async_client.post("/api/documenti/carica", json={
            "testo": "Nuovo listino: tutto a 10 euro.",
            "nome": "nuovo-menu.txt",
        }, headers=_headers(sample_org["id"]))
    assert resp.status_code == 200

    assert await wrepo.faq_cache_lookup(str(sample_org["id"]), [0.1] * 384) is None
