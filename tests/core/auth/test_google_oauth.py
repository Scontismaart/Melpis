"""Test login Google OAuth PKCE server-side (/api/auth/google/*).

Contratto:
- start: 302 verso authorize Supabase con challenge S256 + cookie HttpOnly
  temporanei (verifier, next). Nessuno `state` custom nell'URL: lo genera
  Supabase internamente (passarne uno rompe il flusso con bad_oauth_state).
- callback: Supabase valida il proprio state prima di emettere il code; il
  callback PKCE dell'app può essere code-only. L'app rifiuta parametri
  ambigui/malformati, lega il code al verifier cookie e imposta i cookie BFF.
  Qualsiasi anomalia (cookie assenti, errore OAuth,
  scambio fallito, provisioning fallito) fa fail-closed con redirect a
  /accedi/?errore=google senza cookie di sessione.
- primo accesso Google: se l'utente non ha membership viene creata org +
  owner con trial (JIT provisioning); idempotente ai login successivi.
- `next` non è mai aperto a domini esterni (open redirect).
"""

import base64
import hashlib

import httpx
import pytest

import src.core.auth.bff as bff_module

API_KEY = "test-api-key-12345"

pytestmark = pytest.mark.asyncio


class FakeRepo:
    """Stub di Repository per il callback: registra le chiamate."""

    def __init__(self, memberships=None):
        self.memberships = memberships or []
        self.create_calls = []
        self.guarded_calls = []

    async def get_memberships_by_auth(self, auth_user_id):
        return self.memberships

    async def get_or_create_organization_with_owner(
        self, auth_user_id, nome_attivita, trial_days
    ):
        self.guarded_calls.append((auth_user_id, nome_attivita, trial_days))
        if not self.memberships:
            self.create_calls.append((auth_user_id, nome_attivita, trial_days))
        return {"organization_id": "org-1"}


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("API_KEY_SERVICE", API_KEY)
    monkeypatch.setenv("SUPABASE_URL", "https://myproj.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.test")


@pytest.fixture
async def oauth_client():
    """Client con solo il router /api/auth e un FakeRepo su app.state."""
    from fastapi import FastAPI

    from src.core.auth.routes import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    app.state.repo = FakeRepo()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _get_repo(app) -> FakeRepo:
    return app.state.repo


def _fake_token_response(access="at.oauth", refresh="rt.oauth", metadata=None):
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": 3600,
        "token_type": "bearer",
        "user": {
            "id": "u1",
            "email": "owner@test.com",
            "user_metadata": metadata or {},
        },
    }


def _cookie_header(resp) -> str:
    parts = [f"{k}={v}" for k, v in resp.cookies.items()]
    return "; ".join(parts)


class TestGoogleStart:
    async def test_start_redirects_to_supabase_authorize(self, oauth_client):
        resp = await oauth_client.get("/api/auth/google/start")
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert loc.startswith("https://myproj.supabase.co/auth/v1/authorize?")
        assert "provider=google" in loc
        assert "redirect_to=https%3A%2F%2Fapp.test%2Fapi%2Fauth%2Fgoogle%2Fcallback" in loc
        assert "code_challenge_method=S256" in loc
        assert "code_challenge=" in loc
        # Lo state è generato da Supabase: un custom nell'URL rompe il flusso
        assert "state=" not in loc

    async def test_start_sets_httponly_cookies_with_verifier_matching_challenge(
        self, oauth_client
    ):
        resp = await oauth_client.get("/api/auth/google/start")
        assert "wa_oauth_verifier" in resp.cookies
        assert "wa_oauth_next" in resp.cookies
        verifier = resp.cookies["wa_oauth_verifier"]
        # La challenge nell'URL deve corrispondere al verifier nel cookie (S256)
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert f"code_challenge={expected}" in resp.headers["location"]
        # HttpOnly: il JS non legge mai il verifier
        for c in resp.headers.get_list("set-cookie"):
            if c.startswith("wa_oauth_"):
                assert "httponly" in c.lower()

    async def test_start_next_path_preserved(self, oauth_client):
        resp = await oauth_client.get(
            "/api/auth/google/start", params={"next": "/app/inbox?filter=open"}
        )
        assert resp.cookies["wa_oauth_next"].strip('"') == "/app/inbox?filter=open"

    async def test_start_settings_tab_next_path_preserved(self, oauth_client):
        next_path = "/app/settings?tab=calendar&source=google"
        resp = await oauth_client.get(
            "/api/auth/google/start", params={"next": next_path}
        )
        assert resp.cookies["wa_oauth_next"].strip('"') == next_path

    async def test_start_open_redirect_blocked(self, oauth_client):
        resp = await oauth_client.get(
            "/api/auth/google/start", params={"next": "//evil.com"}
        )
        assert resp.cookies["wa_oauth_next"].strip('"') == "/app/"

    @pytest.mark.parametrize(
        "next_path",
        [
            "https://evil.com", "//evil.com", r"/\\evil.com", "/%0A/evil.com",
            "/%250A/evil.com", "/%2f%2fevil.com", "/%252f%252fevil.com",
            "/line\nbreak", "/tab\tpath", "/del\x7fpath", "/c0\x01path",
        ],
    )
    async def test_start_rejects_unsafe_next_paths(self, oauth_client, next_path):
        resp = await oauth_client.get(
            "/api/auth/google/start", params={"next": next_path}
        )
        assert resp.cookies["wa_oauth_next"].strip('"') == "/app/"

    async def test_start_without_public_app_url_500(self, oauth_client, monkeypatch):
        monkeypatch.delenv("PUBLIC_APP_URL", raising=False)
        resp = await oauth_client.get("/api/auth/google/start")
        assert resp.status_code == 500


class TestGoogleCallback:
    async def _do_start(self, oauth_client, next_path=None):
        if next_path is not None:
            return await oauth_client.get("/api/auth/google/start", params={"next": next_path})
        return await oauth_client.get("/api/auth/google/start")

    async def test_callback_happy_path_sets_session_cookies(
        self, oauth_client, monkeypatch
    ):
        exchanged = []

        async def fake_exchange(code, verifier):
            exchanged.append((code, verifier))
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "exchange_pkce", fake_exchange)

        start = await self._do_start(oauth_client)

        # Lo state di ritorno è l'uuid interno Supabase: opaco, non confrontato
        resp = await oauth_client.get(
            "/api/auth/google/callback",
            params={"code": "the-code", "state": "9c98eab7-9df1-4ce9-8b15-90d50a712cb8"},
            headers={"Cookie": _cookie_header(start)},
        )
        assert resp.status_code == 302
        # BFF callbacks and frontend are same-origin; keep redirects local.
        assert resp.headers["location"] == "/app/"
        # Il codice è stato scambiato col verifier del cookie
        assert exchanged[0][0] == "the-code"
        assert exchanged[0][1] == start.cookies["wa_oauth_verifier"]
        # Cookie di sessione BFF impostati, token MAI nel body
        set_cookies = resp.headers.get_list("set-cookie")
        assert any(c.startswith("wa_at=") for c in set_cookies)
        assert any(c.startswith("wa_rt=") for c in set_cookies)
        assert any(c.startswith("wa_csrf=") for c in set_cookies)
        # I cookie temporanei OAuth vengono consumati (Max-Age=0)
        assert any(c.startswith("wa_oauth_verifier=") and "Max-Age=0" in c for c in set_cookies)
        assert any(c.startswith("wa_oauth_next=") and "Max-Age=0" in c for c in set_cookies)

    async def test_new_google_user_outside_staging_roster_is_not_provisioned(
        self, oauth_client, monkeypatch
    ):
        monkeypatch.setenv("APP_ENV", "staging")
        monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", "reviewed@example.test")

        async def fake_exchange(_code, _verifier):
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "exchange_pkce", fake_exchange)
        start = await self._do_start(oauth_client)
        response = await oauth_client.get(
            "/api/auth/google/callback",
            params={"code": "the-code"},
            headers={"Cookie": _cookie_header(start)},
        )

        assert response.status_code == 302
        assert response.headers["location"] == "/accedi/?errore=google"
        repo = _get_repo(oauth_client._transport.app)
        assert repo.create_calls == []
        assert not any(cookie.startswith("wa_at=") for cookie in response.headers.get_list("set-cookie"))

    async def test_callback_honors_safe_next_cookie(self, oauth_client, monkeypatch):
        async def fake_exchange(code, verifier):
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "exchange_pkce", fake_exchange)

        start = await self._do_start(oauth_client, next_path="/app/inbox?filter=open")
        resp = await oauth_client.get(
            "/api/auth/google/callback",
            params={"code": "c", "state": "opaque-supabase-uuid"},
            headers={"Cookie": _cookie_header(start)},
        )
        assert resp.headers["location"] == "/app/inbox?filter=open"

    async def test_callback_honors_settings_tab_next_cookie(self, oauth_client, monkeypatch):
        async def fake_exchange(code, verifier):
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "exchange_pkce", fake_exchange)

        next_path = "/app/settings?tab=calendar&source=google"
        start = await self._do_start(oauth_client, next_path=next_path)
        resp = await oauth_client.get(
            "/api/auth/google/callback",
            params={"code": "c", "state": "opaque-supabase-uuid"},
            headers={"Cookie": _cookie_header(start)},
        )
        assert resp.headers["location"] == next_path

    async def test_callback_pkce_code_only_supabase_validates_state(
        self, oauth_client, monkeypatch
    ):
        called = []

        async def fake_exchange(code, verifier):
            called.append(1)
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "exchange_pkce", fake_exchange)

        start = await self._do_start(oauth_client)
        resp = await oauth_client.get(
            "/api/auth/google/callback",
            params={"code": "c"},
            headers={"Cookie": _cookie_header(start)},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/app/"
        assert called

    async def test_supabase_state_error_fails_closed(self, oauth_client, monkeypatch):
        called = []

        async def fake_exchange(code, verifier):
            called.append((code, verifier))
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "exchange_pkce", fake_exchange)
        start = await self._do_start(oauth_client)
        resp = await oauth_client.get(
            "/api/auth/google/callback?error_code=bad_oauth_state",
            headers={"Cookie": _cookie_header(start)},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/accedi/?errore=google"
        assert not called
        assert any(
            c.startswith("wa_oauth_verifier=") and "Max-Age=0" in c
            for c in resp.headers.get_list("set-cookie")
        )

    async def test_callback_rejects_ambiguous_or_malformed_state(
        self, oauth_client, monkeypatch
    ):
        called = []

        async def fake_exchange(code, verifier):
            called.append((code, verifier))
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "exchange_pkce", fake_exchange)
        start = await self._do_start(oauth_client)
        cookies = _cookie_header(start)
        for query in (
            "code=one&code=two",
            "code=c&state=one&state=two",
            "code=c&state=bad%0Astate",
        ):
            resp = await oauth_client.get(
                f"/api/auth/google/callback?{query}",
                headers={"Cookie": cookies},
            )
            assert resp.status_code == 302
            assert resp.headers["location"] == "/accedi/?errore=google"
        assert not called

    async def test_secure_callback_sets_and_consumes_host_cookies(
        self, oauth_client, monkeypatch
    ):
        monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")

        async def fake_exchange(code, verifier):
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "exchange_pkce", fake_exchange)
        start = await self._do_start(oauth_client)
        start_cookies = start.headers.get_list("set-cookie")
        verifier_cookie = next(c for c in start_cookies if c.startswith("__Host-wa_oauth_verifier="))
        assert "Secure" in verifier_cookie
        assert "HttpOnly" in verifier_cookie
        assert "SameSite=lax" in verifier_cookie
        assert "Path=/" in verifier_cookie
        assert "Max-Age=600" in verifier_cookie
        assert "Domain=" not in verifier_cookie

        resp = await oauth_client.get(
            "/api/auth/google/callback?code=short-lived-code",
            headers={"Cookie": _cookie_header(start)},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/app/"
        assert "short-lived-code" not in resp.headers["location"]
        assert "at.oauth" not in resp.text and "rt.oauth" not in resp.text
        cookies = resp.headers.get_list("set-cookie")
        for cookie_name in ("__Host-wa_at", "__Host-wa_rt"):
            cookie = next(c for c in cookies if c.startswith(cookie_name + "="))
            assert "Secure" in cookie
            assert "HttpOnly" in cookie
            assert "SameSite=lax" in cookie
            assert "Path=/" in cookie
            assert "Max-Age=" not in cookie
            assert "Expires=" not in cookie
            assert "Domain=" not in cookie
        csrf_cookie = next(c for c in cookies if c.startswith("__Host-wa_csrf="))
        assert "Secure" in csrf_cookie
        assert "SameSite=strict" in csrf_cookie
        assert "HttpOnly" not in csrf_cookie
        assert "Path=/" in csrf_cookie
        assert "Domain=" not in csrf_cookie
        for cookie_name in ("__Host-wa_oauth_verifier", "__Host-wa_oauth_next"):
            cookie = next(c for c in cookies if c.startswith(cookie_name + "="))
            assert "Max-Age=0" in cookie
            assert "Secure" in cookie
            assert "HttpOnly" in cookie
            assert "SameSite=lax" in cookie
            assert "Path=/" in cookie
            assert "Domain=" not in cookie

    async def test_callback_missing_code_fails_closed(self, oauth_client):
        start = await self._do_start(oauth_client)
        resp = await oauth_client.get(
            "/api/auth/google/callback",
            params={"state": "whatever"},
            headers={"Cookie": _cookie_header(start)},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/accedi/?errore=google"

    async def test_callback_missing_cookies_fails_closed(self, oauth_client):
        resp = await oauth_client.get(
            "/api/auth/google/callback",
            params={"code": "c", "state": "whatever"},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/accedi/?errore=google"

    async def test_callback_provider_error_fails_closed(self, oauth_client):
        resp = await oauth_client.get(
            "/api/auth/google/callback",
            params={"error": "access_denied", "error_description": "nope"},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/accedi/?errore=google"

    async def test_callback_exchange_failure_fails_closed(
        self, oauth_client, monkeypatch
    ):
        from fastapi import HTTPException

        async def failing_exchange(code, verifier):
            raise HTTPException(401, "Autorizzazione non valida")

        monkeypatch.setattr(bff_module, "exchange_pkce", failing_exchange)

        start = await self._do_start(oauth_client)
        resp = await oauth_client.get(
            "/api/auth/google/callback",
            params={"code": "c", "state": "opaque-supabase-uuid"},
            headers={"Cookie": _cookie_header(start)},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/accedi/?errore=google"

    async def test_callback_next_cookie_never_external(self, oauth_client, monkeypatch):
        async def fake_exchange(code, verifier):
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "exchange_pkce", fake_exchange)

        start = await self._do_start(oauth_client)
        # Simula un cookie `next` manomesso verso un dominio esterno
        cookie = _cookie_header(start) + "; wa_oauth_next=https://evil.com"
        resp = await oauth_client.get(
            "/api/auth/google/callback",
            params={"code": "c", "state": "opaque-supabase-uuid"},
            headers={"Cookie": cookie},
        )
        assert resp.headers["location"] == "/app/"


class TestProvisioningPrimoAccesso:
    """Primo accesso Google: JIT provisioning org + owner (trial 7 giorni)."""

    async def _do_callback(self, oauth_client, monkeypatch, token=None):
        async def fake_exchange(code, verifier):
            return token or _fake_token_response()

        monkeypatch.setattr(bff_module, "exchange_pkce", fake_exchange)
        start = await oauth_client.get("/api/auth/google/start")
        return await oauth_client.get(
            "/api/auth/google/callback",
            params={"code": "c", "state": "opaque-supabase-uuid"},
            headers={"Cookie": _cookie_header(start)},
        )

    async def test_primo_accesso_provisiona_org_con_trial_7(self, oauth_client, monkeypatch):
        monkeypatch.setenv("TRIAL_DAYS", "30")
        resp = await self._do_callback(oauth_client, monkeypatch)
        assert resp.status_code == 302
        repo = _get_repo(oauth_client._transport.app)
        assert len(repo.create_calls) == 1
        auth_user_id, nome, trial = repo.create_calls[0]
        assert auth_user_id == "u1"
        # Nessun full_name nei metadata: fallback sul prefisso email
        assert nome == "owner"
        assert trial == 7

    async def test_nome_da_full_name_google(self, oauth_client, monkeypatch):
        token = _fake_token_response(metadata={"full_name": "Mario Rossi & Figli"})
        await self._do_callback(oauth_client, monkeypatch, token)
        repo = _get_repo(oauth_client._transport.app)
        assert repo.create_calls[0][1] == "Mario Rossi & Figli"

    async def test_full_name_solo_spazi_cade_su_email(self, oauth_client, monkeypatch):
        token = _fake_token_response(metadata={"full_name": "   ", "name": "  "})
        await self._do_callback(oauth_client, monkeypatch, token)
        repo = _get_repo(oauth_client._transport.app)
        assert repo.create_calls[0][1] == "owner"

    async def test_utente_gia_membro_esegue_guardia_senza_riprovisionare(self, oauth_client, monkeypatch):
        app = oauth_client._transport.app
        app.state.repo = FakeRepo(memberships=[{"organization_id": "org-1"}])
        resp = await self._do_callback(oauth_client, monkeypatch)
        assert resp.headers["location"] == "/app/"
        assert len(_get_repo(app).guarded_calls) == 1
        assert _get_repo(app).create_calls == []

    async def test_provisioning_failure_fail_closed(self, oauth_client, monkeypatch):
        app = oauth_client._transport.app
        repo = FakeRepo()

        async def boom(*a, **k):
            raise RuntimeError("db non raggiungibile")

        repo.get_or_create_organization_with_owner = boom
        app.state.repo = repo

        resp = await self._do_callback(oauth_client, monkeypatch)
        # Fail-closed: niente sessione, l'utente non entra in uno stato
        # mezzo-provisionato.
        assert resp.headers["location"] == "/accedi/?errore=google"
        set_cookies = resp.headers.get_list("set-cookie")
        assert not any(c.startswith("wa_at=") for c in set_cookies)
