import os
import pytest
import httpx
import json
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
    await pg_pool.execute(
        "UPDATE organizations SET subscription_status = 'active', plan = 'business', "
        "messages_limit = 500, messages_used_this_period = 0 WHERE id = ANY($1::uuid[])",
        [sample_org["id"], other_org["id"]],
    )
    install_test_identity(app, API_KEY)
    app.state.repo = repo
    app.state.pool = pg_pool
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        try:
            yield c
        finally:
            app.dependency_overrides.clear()


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


async def test_preview_org_scoped_e_usage_logged(async_client, repo, sample_org, other_org):
    risposta = RispostaOutput(
        risposta="Certo, per le 17 ci stiamo.",
        richiede_umano=False,
        motivo="",
        categoria="booking",
    )
    async def fake_generate(*args, usage_sink=None, **kwargs):
        usage_sink["attempts"].append({
            "model": "groq/llama-3.1-8b-instant", "prompt_tokens": 20,
            "completion_tokens": 8, "total_tokens": 28, "latency_ms": 145,
            "reason": "onboarding_preview", "fallback": False,
        })
        usage_sink["attempts"].append({
            "model": "groq/openai/gpt-oss-120b", "prompt_tokens": 22,
            "completion_tokens": 9, "total_tokens": 31, "latency_ms": 310,
            "reason": "fallback", "fallback": True,
        })
        return risposta

    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.onboarding.genera_risposta_async", new=AsyncMock(side_effect=fake_generate)) as mocked:
        r = await async_client.post("/api/onboarding/preview", json={
            "profilo": PAYLOAD,
            "messaggio": "Vorrei prenotare per le 17",
        }, headers=_headers(sample_org["id"]))
    assert r.status_code == 200
    assert r.json()["risposta"] == "Certo, per le 17 ci stiamo."
    mocked.assert_awaited_once()

    # La route persiste i dati reali di ogni tentativo provider, org-scoped.
    async with repo.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT metadata FROM usage_events WHERE organization_id = $1 ORDER BY created_at",
            sample_org["id"],
        )
        assert await conn.fetchval(
            "SELECT count(*) FROM usage_events WHERE organization_id = $1", other_org["id"]
        ) == 0
        assert await conn.fetchval(
            "SELECT messages_used_this_period FROM organizations WHERE id = $1", sample_org["id"]
        ) == 0
    metadata = [json.loads(row["metadata"]) for row in rows]
    assert len(metadata) == 2
    assert [item["model"] for item in metadata] == [
        "groq/llama-3.1-8b-instant", "groq/openai/gpt-oss-120b"
    ]
    assert [item["task_type"] for item in metadata] == ["onboarding_preview"] * 2
    assert [item["accounting_status"] for item in metadata] == ["recorded"] * 2
    assert [(item["prompt_tokens"], item["completion_tokens"], item["total_tokens"])
            for item in metadata] == [(20, 8, 28), (22, 9, 31)]
    assert [item["latency_ms"] for item in metadata] == [145, 310]
    assert [item["fallback"] for item in metadata] == [False, True]
    assert [item["estimated_cost_eur"] for item in metadata] == [0.0, 0.000007]
    assert all(not ({"prompt", "user_text", "error", "api_key", "key"} & item.keys())
               for item in metadata)


async def test_preview_unresolved_attempt_blocks_next_preview(async_client, repo, sample_org):
    risposta = RispostaOutput(
        risposta="Anteprima non contabilizzabile.", richiede_umano=False,
        motivo="", categoria="generico",
    )

    async def fake_generate(*args, usage_sink=None, **kwargs):
        usage_sink["attempts"].append({
            "model": "unknown/provider-model", "prompt_tokens": 10,
            "completion_tokens": 5, "total_tokens": 15, "latency_ms": 100,
            "reason": "fallback", "fallback": True,
        })
        return risposta

    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.onboarding.genera_risposta_async", new=AsyncMock(side_effect=fake_generate)):
        transport = httpx.ASGITransport(
            app=async_client._transport.app, raise_app_exceptions=False
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first_preview = await client.post("/api/onboarding/preview", json={
                "profilo": PAYLOAD,
                "messaggio": "Vorrei prenotare per le 17",
            }, headers=_headers(sample_org["id"]))
    assert first_preview.status_code == 500

    async with repo.pool.acquire() as conn:
        usage = await conn.fetchrow(
            "SELECT metadata FROM usage_events WHERE organization_id = $1",
            sample_org["id"],
        )
        blocked = await conn.fetchval(
            "SELECT ai_accounting_blocked FROM organizations WHERE id = $1",
            sample_org["id"],
        )
    metadata = json.loads(usage["metadata"])
    assert metadata["accounting_status"] == "unresolved"
    assert metadata["task_type"] == "onboarding_preview"
    assert blocked is True

    next_preview = await async_client.post("/api/onboarding/preview", json={
        "profilo": PAYLOAD,
        "messaggio": "Vorrei prenotare per le 17",
    }, headers=_headers(sample_org["id"]))
    assert next_preview.status_code == 503


@pytest.mark.parametrize("attempt_state", ["verified", "missing_tokens", "not_started"])
async def test_failed_preview_persists_only_actual_attempts(
    async_client, repo, sample_org, other_org, attempt_state
):
    async def fake_generate(*args, usage_sink=None, **kwargs):
        if attempt_state != "not_started":
            attempt = {
                "model": "groq/llama-3.1-8b-instant", "prompt_tokens": 20,
                "completion_tokens": 8, "total_tokens": 28, "latency_ms": 145,
                "reason": "onboarding_preview", "fallback": False,
                "prompt": "synthetic private input", "error": "synthetic provider error",
                "api_key": "synthetic secret never persist",
            }
            if attempt_state == "missing_tokens":
                attempt.pop("completion_tokens")
            usage_sink["attempts"].append(attempt)
        raise TimeoutError("synthetic provider failure")

    transport = httpx.ASGITransport(app=async_client._transport.app, raise_app_exceptions=False)
    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.onboarding.genera_risposta_async", new=AsyncMock(side_effect=fake_generate)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/onboarding/preview", json={
                "profilo": PAYLOAD, "messaggio": "Quali sono gli orari?",
            }, headers=_headers(sample_org["id"]))
    assert response.status_code == 500
    async with repo.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT metadata FROM usage_events WHERE organization_id = $1", sample_org["id"]
        )
        assert await conn.fetchval(
            "SELECT count(*) FROM usage_events WHERE organization_id = $1", other_org["id"]
        ) == 0
        assert await conn.fetchval(
            "SELECT messages_used_this_period FROM organizations WHERE id = $1", sample_org["id"]
        ) == 0
        blocked = await conn.fetchval(
            "SELECT ai_accounting_blocked FROM organizations WHERE id = $1", sample_org["id"]
        )
    assert blocked is (attempt_state == "missing_tokens")
    if attempt_state == "not_started":
        assert rows == []
        return
    assert len(rows) == 1
    metadata = json.loads(rows[0]["metadata"])
    assert metadata["model"] == "groq/llama-3.1-8b-instant"
    assert metadata["prompt_tokens"] == 20
    assert metadata["latency_ms"] == 145
    assert metadata["accounting_status"] == (
        "unresolved" if attempt_state == "missing_tokens" else "recorded"
    )
    assert not ({"prompt", "error", "api_key"} & metadata.keys())
    assert "synthetic private" not in rows[0]["metadata"]
    if blocked:
        next_preview = await async_client.post("/api/onboarding/preview", json={
            "profilo": PAYLOAD, "messaggio": "Quali sono gli orari?",
        }, headers=_headers(sample_org["id"]))
        assert next_preview.status_code == 503
