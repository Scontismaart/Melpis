"""Multilingua (task 14): propagazione lingue org alle bozze recensioni.

Due path reali: POST /api/recensione (API) e fetch_reviews (sync Google).
In entrambi i casi la lingua dell'org (profilo onboarding) deve arrivare fino
al runner; senza profilo onboarding si passano i default, senza errori.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core import onboarding
from src.models.schemas import OnboardingProfileInput


pytestmark = pytest.mark.usefixtures("reset_db")

TEST_SESSION = "test-reviews-lingue-jwt-session"
ENCRYPTION_KEY = "Y2xvbmUtZmVybmV0LWtleS0zMi1ieXRlcy1sb25nISE="
TEST_AUTH_USER_ID = None


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ENCRYPTION_KEY", ENCRYPTION_KEY)
    monkeypatch.setenv("GOOGLE_BUSINESS_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-test.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret-test")
    monkeypatch.setenv("GOOGLE_REVIEWS_REDIRECT_URI", "http://test/api/reviews/google/oauth2callback")
    monkeypatch.setenv("FRONTEND_URL", "http://test/settings")
    from src.core.rate_limit import reset_memory_rate_limiter
    reset_memory_rate_limiter()
    monkeypatch.setattr(
        "src.core.documenti.rag_context.vettorizza",
        lambda texts, tipo="query": [[0.1] * 384 for _ in texts],
    )


@pytest.fixture
async def async_client(repo, pg_pool, sample_org, monkeypatch):
    from fastapi import Depends, HTTPException
    from src.api.main import app
    from src.core.auth.dependencies import get_current_user, get_organization_context, get_token
    from src.core.reviews.google_service import GoogleBusinessService
    app.state.repo = repo
    app.state.pool = pg_pool
    service = GoogleBusinessService(repo=repo, encryption_key=ENCRYPTION_KEY)
    app.state.reviews_service = service
    await pg_pool.execute(
        "UPDATE organizations SET subscription_status = 'active', plan = 'business', "
        "subscription_id = 'sub_reviews_language_test', "
        "current_period_start = now() - interval '1 day', "
        "current_period_end = now() + interval '29 days', "
        "messages_limit = 500, messages_used_this_period = 0 WHERE id = $1",
        sample_org["id"],
    )
    owner_id = uuid.uuid4()
    monkeypatch.setitem(globals(), "TEST_AUTH_USER_ID", owner_id)
    await pg_pool.execute(
        "INSERT INTO auth.users(id,email) VALUES($1,$2)",
        owner_id, f"review-lingue-{owner_id}@example.test",
    )
    profile_id = await pg_pool.fetchval(
        "SELECT id FROM user_profiles WHERE auth_user_id=$1", owner_id,
    )
    await pg_pool.execute(
        "INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) "
        "VALUES($1,$2,'owner',now())",
        sample_org["id"], profile_id,
    )

    async def fixed_test_identity(token=Depends(get_token)):
        if token != TEST_SESSION:
            raise HTTPException(401, "Test session required")
        return {
            "source": "jwt",
            "aal": "aal2",
            "organization_id": str(sample_org["id"]),
            "ruolo": "owner",
            "auth_user_id": str(owner_id),
            "user_id": str(owner_id),
        }

    app.dependency_overrides[get_current_user] = fixed_test_identity
    app.dependency_overrides[get_organization_context] = fixed_test_identity
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        try:
            yield c, service
        finally:
            app.dependency_overrides.clear()
            await pg_pool.execute("DELETE FROM auth.users WHERE id=$1", owner_id)


def _headers(org_id):
    return {"Authorization": f"Bearer {TEST_SESSION}"}


def _profilo_onboarding(**overrides):
    data = {
        "verticale": "ristorante",
        "nome_attivita": "Trattoria Da Mario",
        "orari": "Lun-Dom 12-23",
        "tono": "caldo, diretto",
        "servizi": ["Pranzo", "Cena"],
        "regole_escalation": ["Allergie"],
    }
    data.update(overrides)
    return OnboardingProfileInput(**data)


class _FakeBozza:
    id = str(uuid.uuid4())
    stato = "bozza_generata"
    bozza_risposta = "Grazie per la recensione!"
    sentiment = "positiva"
    richiede_revisione_urgente = False
    motivo = ""
    categoria = "generico"


# ── /api/recensione ───────────────────────────────────────────

async def test_api_recensione_propaga_le_lingue_dell_org(async_client, repo, sample_org, monkeypatch):
    client, _ = async_client
    await onboarding.save_profile(
        sample_org["id"],
        _profilo_onboarding(lingue_supportate=["it", "de"], lingua_default="de"),
        repo,
    )

    captured = {}

    from types import SimpleNamespace
    async def knowledge(*_args, **_kwargs):
        return SimpleNamespace(testo="Knowledge verificata", chunks=[{}])
    monkeypatch.setattr("src.core.documenti.rag_context.recupera_contesto_documenti", knowledge)

    def fake_genera(**kwargs):
        captured.update(kwargs)
        assert kwargs["provider_context"].task_type == "review"
        return _FakeBozza()

    with patch("src.api.main.genera_risposta_recensione", side_effect=fake_genera):
        resp = await client.post(
            "/api/recensione",
            json={"testo": "Ottimo servizio!", "external_id": "lingue-review-1"},
            headers=_headers(sample_org["id"]),
        )

    assert resp.status_code == 200
    assert captured["lingue_supportate"] == ["it", "de"]
    assert captured["lingua_default"] == "de"


async def test_api_recensione_senza_profilo_usa_default(async_client, repo, sample_org, monkeypatch):
    client, _ = async_client
    captured = {}
    from types import SimpleNamespace
    async def knowledge(*_args, **_kwargs):
        return SimpleNamespace(testo="Knowledge verificata", chunks=[{}])
    monkeypatch.setattr("src.core.documenti.rag_context.recupera_contesto_documenti", knowledge)

    def fake_genera(**kwargs):
        captured.update(kwargs)
        assert kwargs["provider_context"].task_type == "review"
        return _FakeBozza()

    with patch("src.api.main.genera_risposta_recensione", side_effect=fake_genera):
        resp = await client.post(
            "/api/recensione",
            json={"testo": "Ottimo servizio!", "external_id": "lingue-review-2"},
            headers=_headers(sample_org["id"]),
        )

    assert resp.status_code == 200
    # org senza onboarding: nessuna lingua, i default li applica il runner
    assert captured["lingue_supportate"] is None
    assert captured["lingua_default"] is None


# ── Sync Google (fetch_reviews) ───────────────────────────────

async def _inserisci_credenziali(pg_pool, org_id):
    from src.core.reviews.google_service import GoogleBusinessService
    svc = GoogleBusinessService(repo=MagicMock(), encryption_key=ENCRYPTION_KEY)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO google_business_credentials
               (organization_id, access_token, refresh_token, token_expiry,
                account_name, location_name)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            org_id,
            svc.encrypt_secret("access-token"),
            svc.encrypt_secret("refresh-token"),
            datetime.now(timezone.utc) + timedelta(hours=1),
            "accounts/123",
            "accounts/123/locations/456",
        )


async def test_sync_google_propaga_le_lingue_dell_org(async_client, pg_pool, sample_org, repo, monkeypatch):
    client, service = async_client
    await _inserisci_credenziali(pg_pool, sample_org["id"])
    await onboarding.save_profile(
        sample_org["id"],
        _profilo_onboarding(lingue_supportate=["it", "en"], lingua_default="en"),
        repo,
    )

    from types import SimpleNamespace
    async def knowledge(*_args, **_kwargs):
        return SimpleNamespace(testo="Knowledge verificata", chunks=[{}])
    monkeypatch.setattr("src.core.documenti.rag_context.recupera_contesto_documenti", knowledge)
    service._build_service = AsyncMock(return_value=MagicMock())
    service._list_reviews = AsyncMock(
        return_value=[
            {
                "reviewId": "rev-1",
                "starRating": "FIVE",
                "reviewer": {"displayName": "Mario"},
                "comment": {"comment": "Great place!"},
            }
        ]
    )
    captured = {}
    service._genera_bozza = AsyncMock(
        side_effect=lambda *a, **kw: (captured.update(kw) or _FakeBozza())
    )

    resp = await client.post("/api/reviews/google/sync", headers=_headers(sample_org["id"]))
    assert resp.status_code == 200
    assert resp.json()["nuove"] == 1
    assert resp.json()["parziale"] is False
    assert captured["lingue_supportate"] == ["it", "en"]
    assert captured["lingua_default"] == "en"


async def test_sync_google_senza_profilo_usa_default(async_client, pg_pool, sample_org, monkeypatch):
    client, service = async_client
    await _inserisci_credenziali(pg_pool, sample_org["id"])

    service._build_service = AsyncMock(return_value=MagicMock())
    service._list_reviews = AsyncMock(
        return_value=[
            {
                "reviewId": "rev-1",
                "starRating": "FIVE",
                "reviewer": {"displayName": "Mario"},
                "comment": {"comment": "Great place!"},
            }
        ]
    )
    captured = {}
    from types import SimpleNamespace
    async def knowledge(*_args, **_kwargs):
        return SimpleNamespace(testo="Knowledge verificata", chunks=[{}])
    monkeypatch.setattr("src.core.documenti.rag_context.recupera_contesto_documenti", knowledge)
    service._genera_bozza = AsyncMock(
        side_effect=lambda *a, **kw: (captured.update(kw) or _FakeBozza())
    )

    resp = await client.post("/api/reviews/google/sync", headers=_headers(sample_org["id"]))
    assert resp.status_code == 200
    # nessun profilo onboarding: None, il runner applica i default
    assert captured["lingue_supportate"] is None
    assert captured["lingua_default"] is None
