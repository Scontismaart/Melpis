import os
import pytest
import httpx
import uuid
from unittest.mock import AsyncMock, patch

from src.models.schemas import RispostaOutput

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
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO auth.users(id,email) VALUES($1,$2) ON CONFLICT (id) DO NOTHING",
            auth_user_id, f"onboarding-{auth_user_id}@example.test",
        )
        profile_id = await conn.fetchval(
            "SELECT id FROM user_profiles WHERE auth_user_id=$1", auth_user_id,
        )
        for org in (sample_org, other_org):
            await conn.execute(
                "INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) "
                "VALUES($1,$2,'owner',now()) ON CONFLICT(organization_id,user_id) "
                "DO UPDATE SET ruolo='owner',joined_at=now()",
                org["id"], profile_id,
            )
    await pg_pool.execute(
        "UPDATE organizations SET subscription_status = 'active', plan = 'business', "
        "subscription_id = 'sub_onboarding_test', "
        "current_period_start = now() - interval '1 day', "
        "current_period_end = now() + interval '29 days', "
        "messages_limit = 500, messages_used_this_period = 0 WHERE id = ANY($1::uuid[])",
        [sample_org["id"], other_org["id"]],
    )
    install_test_identity(app, API_KEY, user_id=str(auth_user_id))
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


PAYLOAD = {
    "verticale": "parrucchiere",
    "nome_attivita": "Studio Capelli Nora",
    "orari": "Mar-Sab 09:00-19:00",
    "tono": "gentile, pratico",
    "servizi": ["Taglio", "Colore"],
    "regole_escalation": ["Correzioni colore"],
    "whatsapp_collegato": False,
    "documenti_importati": False,
}


async def test_profilo_no_auth(async_client):
    resp = await async_client.post("/api/onboarding/profilo", json=PAYLOAD)
    assert resp.status_code == 401


async def test_verticali_con_auth(async_client, sample_org):
    r = await async_client.get("/api/onboarding/verticali", headers=_headers(sample_org["id"]))
    assert r.status_code == 200
    assert any(v["id"] == "ristorante" for v in r.json()["verticali"])


async def test_salva_e_leggi_profilo_org_scoped(async_client, sample_org, other_org):
    r = await async_client.post("/api/onboarding/profilo", json=PAYLOAD, headers=_headers(sample_org["id"]))
    assert r.status_code == 200
    assert r.json()["profilo"]["nome_attivita"] == "Studio Capelli Nora"
    assert r.json()["profilo"]["profilo"]["nome"] == "Studio Capelli Nora"

    mio = await async_client.get("/api/onboarding/profilo", headers=_headers(sample_org["id"]))
    assert mio.status_code == 200
    assert mio.json()["profilo"]["nome_attivita"] == "Studio Capelli Nora"

    # nessun leak cross-tenant: l'altra org non vede il profilo
    altro = await async_client.get("/api/onboarding/profilo", headers=_headers(other_org["id"]))
    assert altro.status_code == 200
    assert altro.json()["profilo"] is None


async def test_cross_tenant_non_sovrascrive(async_client, sample_org, other_org):
    await async_client.post(
        "/api/onboarding/profilo",
        json={**PAYLOAD, "nome_attivita": "Studio A"},
        headers=_headers(sample_org["id"]),
    )
    await async_client.post(
        "/api/onboarding/profilo",
        json={**PAYLOAD, "nome_attivita": "Studio B"},
        headers=_headers(other_org["id"]),
    )
    mio = await async_client.get("/api/onboarding/profilo", headers=_headers(sample_org["id"]))
    assert mio.json()["profilo"]["nome_attivita"] == "Studio A"


async def test_preview_is_tenant_scoped_and_passes_durable_context(async_client, repo, sample_org, other_org):
    risposta = RispostaOutput(
        risposta="Certo, per le 17 ci stiamo.",
        richiede_umano=False,
        motivo="",
        categoria="booking",
    )
    contexts = []

    async def fake_generate(*args, provider_context=None, **kwargs):
        contexts.append(provider_context)
        assert provider_context.task_type == "onboarding_preview"
        assert provider_context.organization_id in {
            sample_org["id"], other_org["id"],
        }
        return risposta

    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.onboarding.genera_risposta_async", new=AsyncMock(side_effect=fake_generate)) as mocked:
        r = await async_client.post("/api/onboarding/preview", json={
            "profilo": PAYLOAD,
            "messaggio": "Vorrei prenotare per le 17",
        }, headers=_headers(sample_org["id"]))
        response_b = await async_client.post("/api/onboarding/preview", json={
            "profilo": PAYLOAD,
            "messaggio": "Vorrei prenotare per le 17",
        }, headers=_headers(other_org["id"]))
    assert r.status_code == 200
    assert r.json()["risposta"] == "Certo, per le 17 ci stiamo."
    assert response_b.status_code == 200
    assert response_b.json()["risposta"] == "Certo, per le 17 ci stiamo."
    assert mocked.await_count == 2

    assert len(contexts) == 2
    assert contexts[0]._closed is True
    assert contexts[0].organization_id != contexts[1].organization_id
    assert contexts[1]._closed is True

    # The mocked generator did not dispatch to a provider, so no attempt is
    # fabricated here. The adapter's PostgreSQL contract is tested separately.
    async with repo.pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM usage_events WHERE event_type='system.provider_attempt' "
            "AND organization_id = ANY($1::uuid[])", [sample_org["id"], other_org["id"]]
        ) == 0
        assert await conn.fetchval(
            "SELECT messages_used_this_period FROM organizations WHERE id = $1", sample_org["id"]
        ) == 0


async def test_preview_closes_context_when_generation_raises(async_client, sample_org):
    contexts = []

    async def fail_generation(*args, provider_context=None, **kwargs):
        contexts.append(provider_context)
        raise TimeoutError("synthetic provider failure")

    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.onboarding.genera_risposta_async", new=AsyncMock(side_effect=fail_generation)):
        transport = httpx.ASGITransport(
            app=async_client._transport.app, raise_app_exceptions=False
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/onboarding/preview", json={
                "profilo": PAYLOAD, "messaggio": "Quali sono gli orari?",
            }, headers=_headers(sample_org["id"]))
    assert response.status_code == 500
    assert len(contexts) == 1
    assert contexts[0]._closed is True
