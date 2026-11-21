"""Test per gli endpoint API di integrazione dei gestionali esterni di prenotazione (Fase 6).

Verifica:
1. POST /api/v1/integrations/booking (creazione/aggiornamento credenziali cifrate)
2. Vincolo tassativo di governance per WuBook ZaK (rifiuto modalità diverse da shadow/local_only)
3. GET /api/v1/integrations/booking/status (stato salute e modalità senza esposizione credenziali)
4. PATCH /api/v1/integrations/booking/mode (cambio modalità e rispetto vincolo ZaK)
5. DELETE /api/v1/integrations/booking (disattivazione integrazione)
6. Tenant Isolation e permessi RBAC (owner/manager vs staff vs non autenticato)
"""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.auth.dependencies import get_current_user, get_organization_context


ORG_ID_1 = "11111111-1111-1111-1111-111111111111"
ORG_ID_2 = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "u-R_g_J8F9aI2sU1D3f4G5h6J7k8L9m0N1o2P3q4R5s=")

    mock_repo = MagicMock()
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.execute = AsyncMock(return_value="DELETE 1")

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_acquire
    mock_repo.pool = mock_pool

    app.state.repo = mock_repo
    app.state.pool = mock_pool

    # Reset eventuale istanza cached nel request state
    if hasattr(app.state, "external_booking_repo"):
        delattr(app.state, "external_booking_repo")

    async def verified_mfa_identity():
        from fastapi import HTTPException
        context = app.dependency_overrides.get(get_organization_context)
        if context is None:
            raise HTTPException(401, "Test session absent")
        return {**context(), "aal": "aal2"}

    app.dependency_overrides[get_current_user] = verified_mfa_identity
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── 1. Autenticazione e Autorizzazione (RBAC) ─────────────────

def test_post_booking_integration_unauthorized(client):
    """Senza autenticazione, la richiesta deve essere respinta con 401."""
    resp = client.post("/api/v1/integrations/booking", json={
        "provider": "simplybook",
        "credentials": {"company_login": "salone_test", "api_key": "abc"},
    })
    assert resp.status_code == 401


def test_post_booking_integration_staff_forbidden(client):
    """Un utente con ruolo 'staff' non può modificare le credenziali (richiede owner o manager)."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "staff",
        "source": "jwt",
    }
    try:
        resp = client.post("/api/v1/integrations/booking", json={
            "provider": "simplybook",
            "credentials": {"company_login": "salone_test", "api_key": "abc"},
        })
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


# ── 2. Configurazione SimplyBook (Successo & No Creds Leak) ───

def test_post_booking_integration_simplybook_success(client):
    """Configura SimplyBook in modalità authoritative e verifica che le credenziali non trapelino."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "owner",
        "source": "jwt",
    }
    try:
        # Mock fetchrow su external_booking_credentials
        conn = client.app.state.pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value={
            "organization_id": uuid.UUID(ORG_ID_1),
            "provider": "simplybook",
            "credentials_encrypted": "gAAAAAB...",
            "config": '{"mode": "authoritative", "medical_dpa_signed": false}',
            "is_active": True,
            "created_at": "2026-09-06T12:00:00Z",
            "updated_at": "2026-09-06T12:00:00Z",
        })

        resp = client.post("/api/v1/integrations/booking", json={
            "provider": "simplybook",
            "credentials": {
                "company_login": "salone_eleganza",
                "api_key": "super_secret_api_key_12345",
            },
            "mode": "authoritative",
            "config": {"medical_dpa_signed": True, "timeout_seconds": 8},
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["provider"] == "simplybook"
        assert data["mode"] == "authoritative"
        # Invariante 10: Le credenziali non devono MAI comparire nella risposta
        assert "super_secret_api_key_12345" not in resp.text
        assert "credentials" not in data
        assert "api_key" not in data
        assert "company_login" not in data
        saved_config = conn.fetchrow.await_args.args[4]
        assert '"medical_dpa_signed"' not in saved_config
        assert '"timeout_seconds": 8' in saved_config
    finally:
        app.dependency_overrides.clear()


# ── 3. Vincolo Tassativo di Governance WuBook ZaK ─────────────

def test_post_booking_integration_zak_refuses_authoritative_mode(client):
    """WuBook ZaK non è ancora certificato per produzione: rifiuta authoritative con 400."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "owner",
        "source": "jwt",
    }
    try:
        resp = client.post("/api/v1/integrations/booking", json={
            "provider": "zak",
            "credentials": {"property_id": "hotel_1", "api_key": "secret"},
            "mode": "authoritative",
        })
        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "zak" in detail or "wubook" in detail
        assert "template" in detail or "produzione" in detail
    finally:
        app.dependency_overrides.clear()


def test_post_booking_integration_wubook_alias_refuses_mirror_mode(client):
    """Anche usando l'alias 'wubook' o 'wubook_zak', la modalità mirror deve essere rifiutata."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "owner",
        "source": "jwt",
    }
    try:
        resp = client.post("/api/v1/integrations/booking", json={
            "provider": "wubook",
            "credentials": {"property_id": "hotel_1", "api_key": "secret"},
            "mode": "mirror",
        })
        assert resp.status_code == 400
        assert "shadow" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_post_booking_integration_zak_allows_shadow_mode(client):
    """In modalità shadow, ZaK è consentito (per test e monitoraggio)."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "owner",
        "source": "jwt",
    }
    try:
        conn = client.app.state.pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value={
            "organization_id": uuid.UUID(ORG_ID_1),
            "provider": "zak",
            "credentials_encrypted": "gAAAAAB...",
            "config": '{"mode": "shadow"}',
            "is_active": True,
            "created_at": "2026-09-06T12:00:00Z",
            "updated_at": "2026-09-06T12:00:00Z",
        })

        resp = client.post("/api/v1/integrations/booking", json={
            "provider": "zak",
            "credentials": {"property_id": "hotel_1", "api_key": "secret"},
            "mode": "shadow",
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["mode"] == "shadow"
    finally:
        app.dependency_overrides.clear()


# ── 4. Lettura Stato Integrazione (GET /status) ───────────────

def test_get_booking_status_unconfigured(client):
    """Se l'organizzazione non ha un gestionale configurato, restituisce is_configured=False."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "staff",  # Staff può leggere lo stato
        "source": "jwt",
    }
    try:
        conn = client.app.state.pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value=None)

        resp = client.get("/api/v1/integrations/booking/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_configured"] is False
        assert data["mode"] == "local_only"
        assert data["provider"] is None
    finally:
        app.dependency_overrides.clear()


def test_get_booking_status_configured_without_leaking_secrets(client):
    """Restituisce stato di salute, modalità e provider senza MAI esporre credenziali."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "manager",
        "source": "jwt",
    }
    try:
        conn = client.app.state.pool.acquire.return_value.__aenter__.return_value

        async def mock_fetchrow_side_effect(query, *args):
            if "external_booking_credentials" in query:
                return {
                    "organization_id": uuid.UUID(ORG_ID_1),
                    "provider": "simplybook",
                    "credentials_encrypted": "encrypted_blob",
                    "config": '{"mode": "authoritative", "medical_dpa_signed": true}',
                    "is_active": True,
                    "updated_at": "2026-09-06T12:00:00Z",
                }
            if "external_booking_sync" in query:
                return {
                    "id": uuid.uuid4(),
                    "sync_status": "synced",
                    "external_booking_id": "SB-12345",
                    "sync_error": None,
                    "updated_at": "2026-09-06T12:30:00Z",
                }
            return None

        conn.fetchrow = AsyncMock(side_effect=mock_fetchrow_side_effect)
        conn.fetchval = AsyncMock(return_value=True)  # Quarantine schema exists.

        resp = client.get("/api/v1/integrations/booking/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_configured"] is True
        assert data["provider"] == "simplybook"
        assert data["mode"] == "authoritative"
        assert "medical_dpa_signed" not in data
        assert data["last_sync"]["status"] == "synced"
        assert data["last_sync"]["external_booking_id"] == "SB-12345"

        # Sicurezza: Nessuna traccia di credenziali
        assert "credentials" not in data
        assert "credentials_encrypted" not in data
        assert "api_key" not in data
    finally:
        app.dependency_overrides.clear()


# ── 5. Modifica Modalità (PATCH /mode) ────────────────────────

def test_patch_booking_mode_success(client):
    """Cambia la modalità operativa da authoritative a shadow."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "owner",
        "source": "jwt",
    }
    try:
        conn = client.app.state.pool.acquire.return_value.__aenter__.return_value

        # 1st fetchrow: check existing credentials
        conn.fetchrow = AsyncMock(return_value={
            "organization_id": uuid.UUID(ORG_ID_1),
            "provider": "simplybook",
            "credentials_encrypted": "encrypted_blob",
            "config": '{"mode": "authoritative"}',
            "is_active": True,
            "updated_at": "2026-09-06T12:00:00Z",
        })

        resp = client.patch("/api/v1/integrations/booking/mode", json={
            "mode": "shadow",
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["mode"] == "shadow"
    finally:
        app.dependency_overrides.clear()


def test_patch_booking_mode_zak_refuses_authoritative(client):
    """WuBook ZaK non può essere passato ad authoritative tramite PATCH."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "owner",
        "source": "jwt",
    }
    try:
        conn = client.app.state.pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value={
            "organization_id": uuid.UUID(ORG_ID_1),
            "provider": "zak",
            "credentials_encrypted": "encrypted_blob",
            "config": '{"mode": "shadow"}',
            "is_active": True,
            "updated_at": "2026-09-06T12:00:00Z",
        })

        resp = client.patch("/api/v1/integrations/booking/mode", json={
            "mode": "authoritative",
        })
        assert resp.status_code == 400
        assert "zak" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


# ── 6. Rimozione Integrazione (DELETE) ────────────────────────

def test_delete_booking_integration_success(client):
    """Rimuove l'integrazione e cancella le credenziali per l'organizzazione corrente."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "owner",
        "source": "jwt",
    }
    try:
        conn = client.app.state.pool.acquire.return_value.__aenter__.return_value
        conn.execute = AsyncMock(return_value="DELETE 1")

        resp = client.delete("/api/v1/integrations/booking")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
    finally:
        app.dependency_overrides.clear()


# ── 7. Tenant Isolation (Anti-Spoofing) ────────────────────────

def test_tenant_isolation_org_id_strictly_from_auth_context(client):
    """Verifica che un'organizzazione non possa impostare o manipolare i dati di un altro tenant:
    l'org_id usato per salvare le credenziali deriva rigorosamente dal contesto JWT/sessione."""
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": ORG_ID_1,
        "ruolo": "owner",
        "source": "jwt",
    }
    try:
        conn = client.app.state.pool.acquire.return_value.__aenter__.return_value
        captured_sql_args = []

        async def capture_fetchrow(query, *args):
            captured_sql_args.append((query, args))
            return {
                "organization_id": uuid.UUID(ORG_ID_1),
                "provider": "simplybook",
                "credentials_encrypted": "encrypted_blob",
                "config": '{"mode": "authoritative"}',
                "is_active": True,
                "created_at": "2026-09-06T12:00:00Z",
                "updated_at": "2026-09-06T12:00:00Z",
            }

        conn.fetchrow = AsyncMock(side_effect=capture_fetchrow)

        # Il client malevolo tenta di iniettare ORG_ID_2 nel payload
        resp = client.post("/api/v1/integrations/booking", json={
            "provider": "simplybook",
            "organization_id": ORG_ID_2,
            "credentials": {"company_login": "salone_test", "api_key": "123"},
            "mode": "authoritative",
        })

        assert resp.status_code == 200
        # Verifica che la query SQL abbia ricevuto come parametro $1 l'ORG_ID_1 del token, MAI ORG_ID_2
        assert len(captured_sql_args) == 1
        query, args = captured_sql_args[0]
        assert args[0] == uuid.UUID(ORG_ID_1)
        assert args[0] != uuid.UUID(ORG_ID_2)
    finally:
        app.dependency_overrides.clear()

