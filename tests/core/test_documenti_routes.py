import os
import uuid
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
        "subscription_id = 'sub_document_qa_test', current_period_start = now() - interval '1 day', "
        "current_period_end = now() + interval '29 days', messages_limit = 500, "
        "messages_used_this_period = 0 WHERE id = ANY($1::uuid[])",
        [sample_org["id"], other_org["id"]],
    )
    install_test_identity(
        app, API_KEY, default_org_id=sample_org["id"], user_id=str(auth_user_id),
    )
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
    def call(self, prompt):
        return "La pizza margherita costa 8 euro."

    async def acall(self, prompt):
        return self.call(prompt)


async def test_chiedi_no_auth(async_client):
    resp = await async_client.post("/api/documenti/chiedi", json={"domanda": "quanto costa?"})
    assert resp.status_code == 401


async def test_carica_e_chiedi_org_scoped(async_client, sample_org, other_org):
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

    with patch("src.core.documenti.qa_agent.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.documenti.qa_agent.crea_llm", return_value=_FakeLLM()):
        r = await async_client.post("/api/documenti/chiedi", json={"domanda": "quanto costa la pizza?"},
                                    headers=_headers(sample_org["id"]))
    assert r.status_code == 200
    assert r.json()["risposta"] == "La pizza margherita costa 8 euro."


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
