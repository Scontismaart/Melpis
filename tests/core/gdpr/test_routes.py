import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException
from tests.gdpr_token_fakes import FakeRedis

pytestmark = pytest.mark.usefixtures("reset_db")

API_KEY = "test-gdpr-api-key-12345"


@pytest.fixture(autouse=True)
def set_env():
    os.environ["DATABASE_URL"] = ""
    os.environ["API_KEY_SERVICE"] = API_KEY
    # The test client uses plain HTTP; production/default behavior stays Secure.
    os.environ["AUTH_COOKIE_SECURE"] = "false"
    # Export payloads must use the same encryption contract as non-test runs.
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")
    os.environ["REDIS_URL"] = "redis://gdpr-test.invalid/0"
    os.environ.pop("AIRTABLE_API_KEY", None)
    os.environ.pop("AIRTABLE_BASE_ID", None)
    os.environ.pop("SOFTR_WEBHOOK_URL", None)
    os.environ.pop("SOFTR_API_KEY", None)


@pytest.fixture
def fake_redis(monkeypatch):
    from src.core.gdpr import token_store

    redis = FakeRedis()
    monkeypatch.setattr(token_store, "_redis", None)
    monkeypatch.setattr(token_store, "_get_redis", AsyncMock(return_value=redis))
    return redis


@pytest.fixture
async def async_client(repo, org_id, fake_redis):
    from src.api.main import app
    from src.core.auth.dependencies import (
        get_current_user,
        get_organization_context,
        get_token,
    )

    async def fixed_test_identity(token=Depends(get_token)):
        if token is None:
            raise HTTPException(401, "Test session required")
        if token != f"apikey:{API_KEY}":
            raise HTTPException(403, "Invalid test session")
        return {
            "source": "jwt", "aal": "aal2", "organization_id": str(org_id),
            "ruolo": "owner", "auth_user_id": "gdpr-test-owner",
            "session_id": "gdpr-test-session", "user_id": "gdpr-test-profile",
        }

    app.dependency_overrides[get_current_user] = fixed_test_identity
    app.dependency_overrides[get_organization_context] = fixed_test_identity
    app.state.repo = repo
    repo.get_memberships_by_auth = AsyncMock(return_value=[{
        "organization_id": str(org_id),
        "ruolo": "owner",
        "user_id": "gdpr-test-profile",
    }])
    app.state.pool = MagicMock()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        try:
            yield c
        finally:
            app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def org_id(pg_pool):
    oid = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'GDPR-Test')", oid)
    return oid


def _headers(org_id=None):
    h = {"X-API-Key": API_KEY}
    if org_id:
        h["X-Organization-Id"] = str(org_id)
    return h


# ── Task 7: DPA ────────────────────────────────────────────────

class TestDPA:
    async def test_dpa_endpoint_returns_html(self, async_client):
        resp = await async_client.get("/api/gdpr/dpa")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Data Processing Agreement" in resp.text

    async def test_dpa_endpoint_no_auth_required(self, async_client):
        resp = await async_client.get("/api/gdpr/dpa")
        assert resp.status_code == 200

    async def test_dpa_subprocessors_aligned_with_annex_b(self, async_client):
        resp = await async_client.get("/api/gdpr/dpa")
        assert resp.status_code == 200
        text = resp.text
        assert "Neon" not in text
        assert "OpenRouter" not in text
        assert "Supabase" in text
        assert "Meta Platforms (WhatsApp Business API)" in text
        assert "Groq / provider LLM configurato" in text
        assert "Google (Business Profile, Calendar)" in text
        assert "Stripe" in text
        assert "Sentry" in text
        assert "SMTP provider" in text
        assert "Airtable, Softr" in text

    async def test_dpa_retention_matches_real_policy(self, async_client):
        resp = await async_client.get("/api/gdpr/dpa")
        assert resp.status_code == 200
        text = resp.text
        assert "versioned, per-tenant policy" in text
        assert "No primary-data" in text
        assert "schedule is activated by this template" in text
        assert "message_retention_days" not in text
        assert "30-day grace period" not in text
        assert "do not certify deletion or recoverability" in text

    async def test_dpa_security_reflects_regex_redaction(self, async_client):
        resp = await async_client.get("/api/gdpr/dpa")
        assert resp.status_code == 200
        text = resp.text
        assert "whitelist" not in text
        assert "regex" in text

    async def test_dpa_has_breach_notification_and_no_placeholder_contact(self, async_client):
        resp = await async_client.get("/api/gdpr/dpa")
        assert resp.status_code == 200
        text = resp.text
        assert "48 hours" in text
        assert "dpo@example.com" not in text


# ── Task 8: Consent preference center ──────────────────────────

class TestConsentPrefs:
    async def test_get_consent_prefs_no_auth(self, async_client):
        resp = await async_client.get("/api/gdpr/consent-prefs")
        assert resp.status_code == 401

    async def test_get_consent_prefs_invalid_key(self, async_client):
        resp = await async_client.get("/api/gdpr/consent-prefs",
                                      headers={"X-API-Key": "invalid"})
        assert resp.status_code == 403

    async def test_get_consent_prefs_empty(self, async_client, org_id):
        resp = await async_client.get("/api/gdpr/consent-prefs",
                                      headers=_headers(org_id))
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    async def test_get_consent_prefs_with_contacts(self, async_client, org_id, repo):
        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=repo.pool)
        contact = await wrepo.get_or_create_contact(org_id, "393401234567")
        await wrepo.record_consent_event(contact["id"], "opt_in", "manual_staff", organization_id=org_id)
        resp = await async_client.get("/api/gdpr/consent-prefs",
                                      headers=_headers(org_id))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        entry = next(c for c in data if c["phone_number"] == "393401234567")
        assert entry["consent_status"] == "granted"

    async def test_put_consent_prefs_no_auth(self, async_client):
        resp = await async_client.put("/api/gdpr/consent-prefs",
                                      json={"phone_number": "393401234567", "consent_status": "withdrawn"})
        assert resp.status_code == 401

    async def test_put_consent_prefs_updates_contact(self, async_client, org_id, repo):
        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=repo.pool)
        contact = await wrepo.get_or_create_contact(org_id, "393409876543")
        resp = await async_client.put("/api/gdpr/consent-prefs",
                                      json={"phone_number": "393409876543", "consent_status": "withdrawn"},
                                      headers=_headers(org_id))
        assert resp.status_code == 200
        status = await wrepo.get_contact_consent(contact["id"], org_id)
        assert status == "withdrawn"

    async def test_put_consent_prefs_invalid_status(self, async_client, org_id):
        resp = await async_client.put("/api/gdpr/consent-prefs",
                                      json={"phone_number": "393401234567", "consent_status": "invalid"},
                                      headers=_headers(org_id))
        assert resp.status_code == 422


# ── Task 9: Data rights (export + delete) ──────────────────────

class TestDataRights:
    async def test_export_no_auth(self, async_client):
        resp = await async_client.get("/api/gdpr/export")
        assert resp.status_code == 401

    async def test_export_returns_download_url(self, async_client, org_id, repo):
        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=repo.pool)
        contact = await wrepo.get_or_create_contact(org_id, "393401111111")
        cvid = (await wrepo.get_or_create_conversation(org_id, contact["id"]))["id"]
        content = {"type": "text", "text": {"body": "Test"}}
        await wrepo.upsert_message(uuid.uuid4(), org_id, cvid, "wam_export_1",
                                   "outbound", "text", content, "Test", "sent")

        resp = await async_client.get("/api/gdpr/export", headers=_headers(org_id))
        assert resp.status_code == 200
        data = resp.json()
        assert data["download_url"] == "/api/gdpr/download"
        assert "expires_in_minutes" in data
        assert data["expires_in_minutes"] == 15
        assert resp.cookies.get("gdpr_export_token")
        assert resp.cookies["gdpr_export_token"] not in data["download_url"]
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["referrer-policy"] == "no-referrer"
        set_cookie = resp.headers["set-cookie"].lower()
        assert "path=/api/gdpr/download" in set_cookie
        assert "httponly" in set_cookie
        assert "samesite=strict" in set_cookie
        assert "max-age=900" in set_cookie

    async def test_export_cookie_is_secure_in_production(
        self, async_client, org_id, monkeypatch
    ):
        monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")

        resp = await async_client.get("/api/gdpr/export", headers=_headers(org_id))

        assert resp.status_code == 200
        assert "; secure" in resp.headers["set-cookie"].lower()

    async def test_download_with_valid_token(self, async_client, org_id, repo):
        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=repo.pool)
        await wrepo.get_or_create_contact(org_id, "393402222222")

        resp = await async_client.get("/api/gdpr/export", headers=_headers(org_id))
        assert resp.status_code == 200
        download_url = resp.json()["download_url"]

        download_resp = await async_client.get(download_url, headers=_headers(org_id))
        assert download_resp.status_code == 200
        data = json.loads(download_resp.content)
        assert data["organization_id"] == str(org_id)
        assert len(data["contacts"]) >= 1
        assert download_resp.headers["cache-control"] == "no-store"
        assert download_resp.headers["referrer-policy"] == "no-referrer"

        # The download token is consumed once and the browser cookie is cleared.
        assert (await async_client.get(download_url, headers=_headers(org_id))).status_code == 404

    async def test_download_expired_token_returns_404(self, async_client, org_id, fake_redis):
        from src.core.gdpr import token_store

        payload = {
            "org_id": str(org_id),
            "auth_user_id": "gdpr-test-owner",
            "session_id": "gdpr-test-session",
            "user_id": "gdpr-test-profile",
            "role": "owner",
            "data": {"test": True},
            "expires": 0,
        }
        token = "expired_test_token"
        fake_redis.values[token_store._storage_key(token)] = Fernet(
            os.environ["ENCRYPTION_KEY"].encode("ascii")
        ).encrypt(json.dumps(payload).encode("utf-8")).decode("ascii")
        async_client.cookies.set(token_store.DOWNLOAD_COOKIE, "expired_test_token")
        resp = await async_client.get("/api/gdpr/download", headers=_headers())
        assert resp.status_code == 404
        assert token_store._storage_key(token) in fake_redis.values

    async def test_download_rejects_corrupt_ciphertext_without_leaking_details(
        self, async_client, fake_redis
    ):
        from src.core.gdpr import token_store

        fake_redis.values[token_store._storage_key("tampered-token")] = "corrupt-ciphertext"
        async_client.cookies.set(token_store.DOWNLOAD_COOKIE, "tampered-token")

        resp = await async_client.get("/api/gdpr/download", headers=_headers())

        assert resp.status_code == 503
        assert resp.json() == {"detail": "Export temporaneamente non disponibile"}
        assert "corrupt-ciphertext" not in resp.text
        assert token_store._storage_key("tampered-token") in fake_redis.values

    async def test_download_nonexistent_token_returns_404(self, async_client):
        resp = await async_client.get("/api/gdpr/download", headers=_headers())
        assert resp.status_code == 404

    async def test_delete_no_auth(self, async_client):
        resp = await async_client.post("/api/gdpr/delete")
        assert resp.status_code == 401

    async def test_delete_without_approved_policy_is_durably_blocked(self, async_client, org_id, repo):
        resp = await async_client.post("/api/gdpr/delete", headers=_headers(org_id),
                                       json={"request_id": str(uuid.uuid4())})
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "blocked"
        assert data["id"]
        assert any(reason.startswith("policy_required.") for reason in data["manifest"]["blockers"])
        async with repo.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
            operation = await conn.fetchrow(
                "SELECT status FROM gdpr_operations WHERE id=$1 AND organization_id=$2",
                uuid.UUID(data["id"]), org_id,
            )
            event = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM gdpr_operation_events WHERE operation_id=$1 AND event_type='erase.blocked')",
                uuid.UUID(data["id"]),
            )
        assert row is not None
        assert operation["status"] == "blocked"
        assert event is True

    async def test_delete_requires_explicit_idempotent_request(self, async_client, org_id, repo, monkeypatch):
        monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")
        resp = await async_client.post("/api/gdpr/delete", headers=_headers(org_id))
        assert resp.status_code == 422
        async with repo.pool.acquire() as conn:
            assert not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM gdpr_operations WHERE organization_id=$1 AND kind='erase')", org_id,
            )

    async def test_retention_policy_endpoint(self, async_client, org_id):
        resp = await async_client.get("/api/gdpr/retention-policy", headers=_headers(org_id))
        assert resp.status_code == 200
        data = resp.json()
        assert data["retention_days"] is None
        assert data["purge_after_days"] is None
        assert data["automatic_erasure_enabled"] is False
        assert data["policy_version"] == "tenant-approval-required"
