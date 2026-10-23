"""Audit 1.4 — AAL2 (MFA) step-up gate sui Tier-1 sensibili.

Cosa si testa qui:
1. Un JWT senza claim `aal` (sessione password-only) viene rifiutato con
   403 quando colpisce un endpoint protetto da require_mfa().
2. Un JWT con `aal="aal2"` (secondo fattore verificato) passa.
3. Le chiavi di servizio non sono sessioni utente e non superano il gate.
4. Bonus: conferma end-to-end che un token firmato da un issuer diverso
   (progetto Supabase sbagliato) viene rifiutato con 403 — questo copre
   la richiesta "test con iss sbagliato" andando oltre il solo livello
   funzione gia' coperto in test_jwt_issuer.py.

Questi test isolano il gate MFA; la verifica crittografica e issuer sono
coperte dai test JWT con chiavi sintetiche reali.
"""
import uuid
import time
import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from src.core.auth.dependencies import (
    get_current_user,
    require_mfa,
    SENSITIVE_AAL2_PATHS,
)
import src.core.auth.dependencies as deps


def _install_signed_test_tokens(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(ECAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "mfa-test", "alg": "ES256", "use": "sig"})

    async def jwks():
        return [jwk]

    async def not_revoked(_token):
        return False

    monkeypatch.setenv("SUPABASE_URL", "https://myproj.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_AUD", "authenticated")
    monkeypatch.setattr(deps, "_get_supabase_jwks", jwks)
    monkeypatch.setattr(deps, "is_token_revoked", not_revoked)

    def token(sub, *, aal="aal2", iss="https://myproj.supabase.co/auth/v1", exp_offset=600):
        now = int(time.time())
        return jwt.encode(
            {"sub": sub, "aal": aal, "aud": "authenticated", "iss": iss,
             "iat": now, "exp": now + exp_offset},
            key, algorithm="ES256", headers={"kid": jwk["kid"]},
        )

    return token


# ── Fixture: mini-app con un endpoint Tier-1 fittizio ──────────────────


from fastapi import Depends  # noqa: E402


def _build_test_app_real() -> FastAPI:
    """App minimale che monta /api/gdpr/export protetto da require_mfa(),
    per testare la dipendenza in isolamento senza repository o Stripe."""
    app = FastAPI()
    app.state.repo = SimpleNamespace(get_auth_access_allowed=AsyncMock(return_value=True))

    @app.get("/api/gdpr/export")
    async def _export(
        user: dict = Depends(get_current_user),
        mfa: dict = Depends(require_mfa()),
    ):
        # Raggiungiamo questo handler solo se sia get_current_user che
        # require_mfa passano. user/mfa sono lo stesso dict qui (require_mfa
        # ritorna il user che gli arriva), quindi controlliamo aal su mfa.
        return {"ok": True, "aal": mfa.get("aal")}

    return app


# ── Test 1+2: require_mfa() a livello di dipendenza ────────────────────


class TestRequireMfa:
    async def test_jwt_without_aal_is_rejected_with_403(self, monkeypatch):
        # Simula get_current_user che ritorna una sessione password-only:
        # il claim aal non e' presente (utente senza MFA abilitata, o token
        # legacy pre-MFA). Deve essere bloccato.
        async def _fake_get_current_user():
            return {
                "source": "jwt",
                "auth_user_id": str(uuid.uuid4()),
                "ruolo": "owner",
                "aal": None,
            }
        monkeypatch.setattr(deps, "get_current_user", _fake_get_current_user)

        check = require_mfa()
        with pytest.raises(HTTPException) as exc:
            await check(user=await _fake_get_current_user())
        assert exc.value.status_code == 403
        assert "MFA" in exc.value.detail or "due fattori" in exc.value.detail
        # L'header custom serve al frontend per reindirizzare al setup MFA
        # invece di mostrare un generico 403.
        assert exc.value.headers.get("X-MFA-Required") == "true"

    async def test_jwt_with_aal1_is_rejected_with_403(self, monkeypatch):
        # aal="aal1" = password verificata ma senza secondo fattore.
        async def _fake_get_current_user():
            return {"source": "jwt", "auth_user_id": str(uuid.uuid4()), "aal": "aal1"}
        check = require_mfa()
        with pytest.raises(HTTPException) as exc:
            await check(user=await _fake_get_current_user())
        assert exc.value.status_code == 403

    async def test_jwt_with_aal2_is_allowed(self, monkeypatch):
        async def _fake_get_current_user():
            return {"source": "jwt", "auth_user_id": str(uuid.uuid4()), "aal": "aal2"}
        check = require_mfa()
        user = await _fake_get_current_user()
        result = await check(user=user)
        assert result["aal"] == "aal2"

    async def test_api_key_source_cannot_bypass_mfa(self):
        user = {"source": "api_key", "ruolo": "service_role", "aal": None}
        check = require_mfa()
        with pytest.raises(HTTPException) as exc:
            await check(user=user)
        assert exc.value.status_code == 403


# ── Test 3: conferma che i Tier-1 path sono i 4 attesi ─────────────────


class TestSensitivePathList:
    def test_tier1_paths_match_design(self):
        # Regression guard: se qualcuno aggiunge/rimuove un path qui per
        # errore, questo test fallisce e forza una revisione esplicita.
        assert SENSITIVE_AAL2_PATHS == frozenset({
            "/api/billing/create-checkout-session",
            "/api/billing/create-portal-session",
            "/api/billing/provider-attempts/reconciliation/proposals",
            "/api/billing/provider-attempts/reconciliation/approvals",
            "/api/billing/provider-attempts/{attempt_id}/recover",
            "/api/gdpr/export",
            "/api/gdpr/download",
            "/api/gdpr/download/revoke",
            "/api/gdpr/delete",
            "/api/gdpr/operations/{operation_id}",
            "/api/gdpr/operations/{operation_id}/retry",
            "/api/calendar/auth",
            "/api/calendar/disconnect",
            "/api/calendar/settings",
            "/api/calendar/check",
            "/api/bookings/{booking_id}/calendar-sync/retry",
            "/api/reviews/google/auth",
            "/api/reviews/google/settings",
            "/api/reviews/google/disconnect",
        })

    def test_consistent_paths_cover_all_tier1_operations(self):
        # Le operazioni irreversibili (delete) e finanziarie (checkout,
        # portal) e di esfiltrazione PII (export) devono essere tutte qui.
        for p in ["/delete", "/export", "/create-checkout-session", "/create-portal-session"]:
            assert any(p in path for path in SENSITIVE_AAL2_PATHS), (
                f"Attenzione: {p} non e' coperto da AAL2 — verificare se e' intenzionale."
            )


# ── Test 4 (bonus): iss sbagliato rifiutato end-to-end ─────────────────


class TestJwtIssuerRejectionEndToEnd:
    """Conferma che un token proveniente da un altro progetto Supabase
    (issuer diverso) non riesce ad autenticarsi. Va oltre test_jwt_issuer
    che testa solo la funzione verify_supabase_jwt: qui verifichiamo che
    il 403 propaghi correttamente attraverso get_current_user, che e'
    la dipendenza usata da tutti gli endpoint."""

    async def test_wrong_issuer_rejected_at_get_current_user(self, monkeypatch):
        make_token = _install_signed_test_tokens(monkeypatch)

        from unittest.mock import MagicMock
        mock_request = MagicMock()
        mock_request.app.state.repo = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await get_current_user(
                request=mock_request,
                token=make_token("user-wrong-issuer", iss="https://other.supabase.co/auth/v1"),
            )
        assert exc.value.status_code == 403
        assert "non valido" in exc.value.detail.lower()


# ── Test 5: integrazione completa via TestClient ───────────────────────


class TestMfaGateOnRoute:
    """Test end-to-end con TestClient: un utente senza MFA che chiama
    /api/gdpr/export riceve 403, uno con MFA riceve 200."""

    def test_no_mfa_session_blocked_from_export(self, monkeypatch):
        app = _build_test_app_real()
        client = TestClient(app)

        # JWT decodificato con successo (iss/aud ok) ma aal="aal1":
        # sessione password-only, niente secondo fattore.
        signed_token = _install_signed_test_tokens(monkeypatch)("user-no-mfa", aal="aal1")

        resp = client.get("/api/gdpr/export", headers={"Authorization": f"Bearer {signed_token}"})
        assert resp.status_code == 403, resp.text
        assert resp.headers.get("x-mfa-required") == "true"

    def test_mfa_session_allowed_on_export(self, monkeypatch):
        app = _build_test_app_real()
        client = TestClient(app)

        signed_token = _install_signed_test_tokens(monkeypatch)("user-with-mfa", aal="aal2")

        resp = client.get("/api/gdpr/export", headers={"Authorization": f"Bearer {signed_token}"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["aal"] == "aal2"

    def test_expired_signed_session_is_denied_on_normal_api(self, monkeypatch):
        app = _build_test_app_real()
        client = TestClient(app)
        signed_token = _install_signed_test_tokens(monkeypatch)(
            "user-expired", exp_offset=-60
        )

        response = client.get(
            "/api/gdpr/export", headers={"Authorization": f"Bearer {signed_token}"}
        )

        assert response.status_code == 401, response.text

    def test_provider_attempt_recovery_route_requires_aal2_and_store_before_repository(self, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from src.core.billing.routes import router as billing_router

        app = FastAPI()
        actor_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        user = {"source": "jwt", "auth_user_id": actor_id, "organization_id": org_id,
                "ruolo": "owner", "aal": "aal1"}
        billing_repo = SimpleNamespace(
            propose_provider_reconciliation=AsyncMock(return_value=1),
            approve_provider_reconciliation=AsyncMock(return_value=True),
        )
        app.state.repo = SimpleNamespace(billing_repo=billing_repo)
        app.include_router(billing_router)
        app.dependency_overrides[deps.get_current_user] = lambda: user
        app.dependency_overrides[deps.get_organization_context] = lambda: user
        client = TestClient(app)
        payload = {
            "attempt_id": str(uuid.uuid4()),
            "evidence_object_id": "a03e_0123456789abcdef0123456789abcdef",
            "evidence_sha256": "a" * 64,
            "attestations": {
                "provider_account_and_request_verified": True,
                "terminal_outcome_verified": True,
                "metrics_verified": True,
            },
        }

        response = client.post("/api/billing/provider-attempts/reconciliation/proposals", json=payload)
        assert response.status_code == 403
        billing_repo.propose_provider_reconciliation.assert_not_awaited()
        approval_payload = {**payload, "proposal_version": 1}
        response = client.post(
            "/api/billing/provider-attempts/reconciliation/approvals", json=approval_payload,
        )
        assert response.status_code == 403
        billing_repo.approve_provider_reconciliation.assert_not_awaited()

        user["aal"] = "aal2"
        response = client.post("/api/billing/provider-attempts/reconciliation/proposals", json=payload)
        assert response.status_code == 503, response.text
        billing_repo.propose_provider_reconciliation.assert_not_awaited()
        response = client.post(
            "/api/billing/provider-attempts/reconciliation/approvals", json=approval_payload,
        )
        assert response.status_code == 503, response.text
        billing_repo.approve_provider_reconciliation.assert_not_awaited()

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/gdpr/download/revoke"),
            ("get", "/api/gdpr/operations/{operation_id}"),
            ("post", "/api/gdpr/operations/{operation_id}/retry"),
        ],
    )
    def test_new_gdpr_operations_reject_aal1_before_repository_access(
        self, monkeypatch, method, path,
    ):
        from src.core.gdpr.routes import router as gdpr_router

        class PoisonRepository:
            @property
            def pool(self):
                raise AssertionError("AAL1 request accessed repository before MFA rejection")

        app = FastAPI()
        app.state.repo = PoisonRepository()
        app.include_router(gdpr_router)
        actor_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        user = {
            "source": "jwt",
            "auth_user_id": actor_id,
            "organization_id": org_id,
            "user_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "ruolo": "owner",
            "aal": "aal1",
        }
        app.dependency_overrides[deps.get_current_user] = lambda: user
        app.dependency_overrides[deps.get_organization_context] = lambda: user
        operation_id = str(uuid.uuid4())
        request_path = path.format(operation_id=operation_id)

        response = getattr(TestClient(app), method)(request_path)
        assert response.status_code == 403, response.text
        assert response.headers.get("x-mfa-required") == "true"
