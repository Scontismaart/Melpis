import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

import src.core.auth.bff as bff_module
from src.core.auth.denylist import is_token_revoked

API_KEY = "test-api-key-12345"

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("API_KEY_SERVICE", API_KEY)
    monkeypatch.setenv("SUPABASE_URL", "https://myproj.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("DEMO_MODE", "false")


@pytest.fixture
async def bff_client():
    """Client con solo il router /api/auth: per login/refresh/logout non
    serve il DB, basta mockare il modulo BFF verso Supabase."""
    from fastapi import FastAPI

    from src.core.auth.routes import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    app.state.repo = SimpleNamespace(get_auth_access_allowed=AsyncMock(return_value=True))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def async_client(repo, pg_pool):
    from src.api.main import app
    app.state.repo = repo
    app.state.pool = pg_pool
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _fake_token_response(access="at.1", refresh="rt.1"):
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": 3600,
        "token_type": "bearer",
        "user": {"id": "u1", "email": "owner@test.com"},
    }


class TestExchangePKCE:
    @pytest.mark.parametrize(
        ("upstream_status", "expected_status", "expected_detail"),
        [
            (400, 401, "Autorizzazione non valida"),
            (401, 401, "Autorizzazione non valida"),
            (403, 401, "Autorizzazione non valida"),
            (408, 503, "Autorizzazione temporaneamente non disponibile"),
            (429, 503, "Autorizzazione temporaneamente non disponibile"),
            (500, 503, "Autorizzazione temporaneamente non disponibile"),
            (503, 503, "Autorizzazione temporaneamente non disponibile"),
        ],
    )
    async def test_exchange_pkce_sanitizes_status_and_retries_transient_failures(
        self, monkeypatch, upstream_status, expected_status, expected_detail
    ):
        response = SimpleNamespace(
            status_code=upstream_status,
            json=lambda: {"message": "synthetic private provider detail"},
        )
        client = SimpleNamespace(post=AsyncMock(return_value=response))
        monkeypatch.setattr(bff_module, "_client", AsyncMock(return_value=client))

        with pytest.raises(HTTPException) as caught:
            await bff_module.exchange_pkce("synthetic-code", "synthetic-verifier")

        assert caught.value.status_code == expected_status
        assert caught.value.detail == expected_detail
        assert "synthetic private provider detail" not in str(caught.value.detail)
        client.post.assert_awaited_once()


async def _seed_membership(pg_pool, sample_org, auth_user_id, ruolo="owner"):
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, 'owner@test.com')",
            auth_user_id,
        )
        up_row = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE auth_user_id = $1", auth_user_id
        )
        await conn.execute("""
            INSERT INTO organization_memberships (organization_id, user_id, ruolo)
            VALUES ($1, $2, $3)
        """, sample_org["id"], up_row["id"], ruolo)


class TestLogin:
    async def test_login_rejects_disabled_account_without_setting_cookies(self, bff_client, monkeypatch):
        app = bff_client._transport.app
        app.state.repo.get_auth_access_allowed.return_value = False

        async def fake_login(email, password):
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "login", fake_login)
        response = await bff_client.post(
            "/api/auth/login",
            json={"email": "owner@test.com", "password": "correct-password"},
        )
        assert response.status_code == 403
        assert "set-cookie" not in response.headers
        app.state.repo.get_auth_access_allowed.assert_awaited_once_with("u1")

    async def test_login_success_sets_cookies(self, bff_client, monkeypatch):
        async def fake_login(email, password):
            return _fake_token_response()

        monkeypatch.setattr(bff_module, "login", fake_login)
        resp = await bff_client.post(
            "/api/auth/login",
            json={"email": "owner@test.com", "password": "segretissima"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["email"] == "owner@test.com"
        assert resp.json()["csrf_token"]
        assert "wa_at" in resp.cookies
        assert "wa_rt" in resp.cookies
        assert "wa_csrf" in resp.cookies
        # BFF: il token NON deve mai comparire nel body della risposta
        assert "access_token" not in resp.text

    async def test_login_wrong_credentials_401(self, bff_client, monkeypatch):
        from fastapi import HTTPException

        async def fail_login(email, password):
            raise HTTPException(401, "Credenziali non valide")

        monkeypatch.setattr(bff_module, "login", fail_login)
        resp = await bff_client.post(
            "/api/auth/login",
            json={"email": "a@b.it", "password": "wrong"},
        )
        assert resp.status_code == 401
        assert "wa_at" not in resp.cookies

    async def test_login_throttled_after_5_failures(self, bff_client, monkeypatch):
        from fastapi import HTTPException

        from src.core.rate_limit import reset_memory_rate_limiter

        async def fail_login(email, password):
            raise HTTPException(401, "Credenziali non valide")

        monkeypatch.setattr(bff_module, "login", fail_login)
        # svuota lo stato di throttle tra i run dei test
        reset_memory_rate_limiter()

        for _ in range(5):
            resp = await bff_client.post(
                "/api/auth/login",
                json={"email": "a@b.it", "password": "wrong"},
            )
            assert resp.status_code == 401

        resp = await bff_client.post(
            "/api/auth/login",
            json={"email": "a@b.it", "password": "wrong"},
        )
        assert resp.status_code == 429


class TestMe:
    async def test_me_single_membership_resolves_org(
        self, async_client, sample_org, pg_pool, monkeypatch
    ):
        auth_user_id = os.urandom(16).hex()
        await _seed_membership(pg_pool, sample_org, auth_user_id)

        async def fake_verify(token):
            return {"sub": auth_user_id, "email": "owner@test.com"}

        import src.core.auth.dependencies as deps
        monkeypatch.setattr(deps, "verify_supabase_jwt", fake_verify)

        resp = await async_client.get(
            "/api/auth/me", headers={"Authorization": "Bearer at.fake"}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["organization_id"] == str(sample_org["id"])
        assert data["ruolo"] == "owner"
        assert data["email"] == "owner@test.com"

    async def test_me_no_membership_jit_provisions_org(
        self, async_client, pg_pool, monkeypatch
    ):
        """JIT (Task18): un utente autenticato senza membership riceve
        auto-provisioning di organizzazione + ruolo owner al primo /me."""
        monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", "none@test.com")
        auth_user_id = os.urandom(16).hex()
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO auth.users (id, email) VALUES ($1, 'none@test.com')",
                auth_user_id,
            )

        async def fake_verify(token):
            return {"sub": auth_user_id, "email": "none@test.com"}

        import src.core.auth.dependencies as deps
        monkeypatch.setattr(deps, "verify_supabase_jwt", fake_verify)

        resp = await async_client.get(
            "/api/auth/me", headers={"Authorization": "Bearer at.fake"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["organization_id"]
        assert data["ruolo"] == "owner"
        assert data["email"] == "none@test.com"


class TestRefresh:
    async def test_refresh_rejects_disabled_account_before_refreshing(self, bff_client, monkeypatch):
        app = bff_client._transport.app
        app.state.repo.get_auth_access_allowed.return_value = False
        refresh = AsyncMock(return_value=_fake_token_response(access="new-at", refresh="new-rt"))
        monkeypatch.setattr(bff_module, "refresh", refresh)

        async def fake_verify(token, *, allow_expired=False):
            assert token == "blocked-access-token"
            assert allow_expired is True
            return {"sub": "blocked-user"}

        monkeypatch.setattr("src.core.auth.dependencies.verify_supabase_jwt", fake_verify)
        response = await bff_client.post(
            "/api/auth/refresh",
            headers={"Cookie": "wa_at=blocked-access-token; wa_rt=blocked-refresh-token; wa_csrf=csrf", "Origin": "http://test", "X-CSRF-Token": "csrf"},
        )
        assert response.status_code == 403
        assert "set-cookie" not in response.headers
        refresh.assert_not_awaited()
        app.state.repo.get_auth_access_allowed.assert_awaited_once_with("blocked-user")

    async def test_refresh_rotates_cookie(self, bff_client, monkeypatch):
        calls = []

        async def fake_refresh(rt, user_key):
            calls.append(rt)
            return _fake_token_response(access="at.2", refresh="rt.2")

        async def fake_verify(token, *, allow_expired=False):
            assert token == "old-access-token"
            assert allow_expired is True
            return {"sub": "u1"}

        monkeypatch.setattr(bff_module, "refresh", fake_refresh)
        monkeypatch.setattr("src.core.auth.dependencies.verify_supabase_jwt", fake_verify)
        resp = await bff_client.post(
            "/api/auth/refresh",
            headers={"Cookie": "wa_at=old-access-token; wa_rt=old-refresh-token; wa_csrf=csrf", "Origin": "http://test", "X-CSRF-Token": "csrf"},
        )
        assert resp.status_code == 200
        assert calls == ["old-refresh-token"]
        assert await is_token_revoked("old-refresh-token")
        assert await is_token_revoked("old-access-token")
        set_cookies = resp.headers.get_list("set-cookie")
        assert any(c.startswith("wa_at=at.2") for c in set_cookies)
        assert any(c.startswith("wa_rt=rt.2") for c in set_cookies)

    async def test_refresh_rejects_identity_change_without_setting_cookies(
        self, bff_client, monkeypatch
    ):
        async def fake_refresh(rt, user_key):
            return _fake_token_response(access="at.other", refresh="rt.other") | {
                "user": {"id": "another-user"}
            }

        async def fake_verify(token, *, allow_expired=False):
            return {"sub": "expected-user"}

        revoked = []

        async def fake_logout(access_token, *, scope="local"):
            revoked.append((access_token, scope))

        monkeypatch.setattr(bff_module, "refresh", fake_refresh)
        monkeypatch.setattr("src.core.auth.dependencies.verify_supabase_jwt", fake_verify)
        monkeypatch.setattr(bff_module, "logout", fake_logout)
        resp = await bff_client.post(
            "/api/auth/refresh",
            headers={
                "Cookie": "wa_at=mismatch-access-token; wa_rt=mismatch-refresh-token; wa_csrf=csrf",
                "Origin": "http://test",
                "X-CSRF-Token": "csrf",
            },
        )
        assert resp.status_code == 401
        assert "set-cookie" not in resp.headers
        assert "at.other" not in resp.text and "rt.other" not in resp.text
        assert revoked == [("at.other", "local")]
        assert await is_token_revoked("mismatch-access-token")
        assert await is_token_revoked("mismatch-refresh-token")

    async def test_refresh_single_flight(self, monkeypatch):
        """Il single-flight sta DENTRO bff.refresh (lock per-token): due
        refresh concorrenti sullo stesso token devono fare una sola chiamata
        a Supabase. Mockiamo _token_request (il collo di bottiglia), non
        refresh (che contiene il lock)."""
        import asyncio

        started = asyncio.Event()
        release = asyncio.Event()
        concurrent = 0
        max_concurrent = 0
        calls = []

        async def slow_token_request(payload):
            nonlocal concurrent, max_concurrent
            calls.append(payload.get("refresh_token"))
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            started.set()
            await release.wait()
            concurrent -= 1
            return _fake_token_response(access="at.2", refresh="rt.2")

        monkeypatch.setattr(bff_module, "_token_request", slow_token_request)

        task1 = asyncio.create_task(
            bff_module.refresh("same-refresh-token", "key-1")
        )
        await started.wait()
        task2 = asyncio.create_task(
            bff_module.refresh("same-refresh-token", "key-1")
        )
        await asyncio.sleep(0.05)
        release.set()
        r1 = await task1
        r2 = await task2
        assert r1["access_token"] == "at.2" and r2["access_token"] == "at.2"
        # contratto del lock: MAI chiamate parallele a Supabase (max 1 alla
        # volta); la seconda richiesta attende e ruota poi (grace period).
        assert max_concurrent == 1
        assert len(calls) == 2

    async def test_refresh_no_cookie_401(self, bff_client):
        resp = await bff_client.post("/api/auth/refresh")
        assert resp.status_code == 401


class TestLogout:
    async def test_logout_revokes_and_clears_cookie(self, bff_client, monkeypatch):
        revoked = []

        monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")

        async def fake_logout(access_token, *, scope="local"):
            revoked.append((access_token, scope))

        monkeypatch.setattr(bff_module, "logout", fake_logout)
        resp = await bff_client.post(
            "/api/auth/logout",
            headers={"Cookie": "__Host-wa_at=at.1; __Host-wa_rt=rt.1; __Host-wa_csrf=csrf", "Origin": "http://test", "X-CSRF-Token": "csrf"},
        )
        assert resp.status_code == 200
        assert revoked == [("at.1", "local")]
        set_cookies = resp.headers.get_list("set-cookie")
        # i cookie di sessione vengono scaduti (Max-Age=0 o expiry nel passato)
        for cookie_name in ("__Host-wa_at", "__Host-wa_rt"):
            cookie = next(c for c in set_cookies if c.startswith(cookie_name + "="))
            assert "Max-Age=0" in cookie
            assert "Secure" in cookie
            assert "HttpOnly" in cookie
            assert "SameSite=lax" in cookie
            assert "Path=/" in cookie
            assert "Domain=" not in cookie
        csrf_cookie = next(c for c in set_cookies if c.startswith("__Host-wa_csrf="))
        assert "Max-Age=0" in csrf_cookie
        assert "Secure" in csrf_cookie
        assert "SameSite=strict" in csrf_cookie
        assert "HttpOnly" not in csrf_cookie
        assert "Path=/" in csrf_cookie
        assert "Domain=" not in csrf_cookie

    async def test_supabase_logout_revokes_only_current_session(self, monkeypatch):
        requests = []

        async def capture(request):
            requests.append(request)
            return httpx.Response(204)

        client = httpx.AsyncClient(transport=httpx.MockTransport(capture))

        async def fake_client():
            return client

        monkeypatch.setattr(bff_module, "_client", fake_client)
        monkeypatch.setenv("SUPABASE_URL", "https://myproj.supabase.co")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
        try:
            await bff_module.logout("access-token-test")
        finally:
            await client.aclose()

        assert len(requests) == 1
        assert requests[0].url.path == "/auth/v1/logout"
        assert requests[0].url.params["scope"] == "local"
        assert requests[0].headers["Authorization"] == "Bearer access-token-test"


class TestTenantIsolation:
    async def test_org_scoped_endpoint_uses_resolved_org(
        self, async_client, sample_org, pg_pool, monkeypatch
    ):
        """Un utente con 1 solo membership accede agli endpoint org-scoped
        SENZA alcun header X-Organization-Id: il tenant viene dal JWT."""
        auth_user_id = os.urandom(16).hex()
        await _seed_membership(pg_pool, sample_org, auth_user_id)

        async def fake_verify(token):
            return {"sub": auth_user_id, "email": "owner@test.com"}

        import src.core.auth.dependencies as deps
        monkeypatch.setattr(deps, "verify_supabase_jwt", fake_verify)

        resp = await async_client.get(
            "/api/auth/me", headers={"Authorization": "Bearer at.fake"}
        )
        assert resp.status_code == 200
        assert resp.json()["organization_id"] == str(sample_org["id"])
