"""Exercise GDPR export authorization with the real authentication chain."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI

from src.core.auth import bff, dependencies
from src.core.auth import routes as auth_routes
from src.core.gdpr import routes, token_store


class SimulatedRedis:
    """Models Redis GET and atomic Lua compare-and-delete for route tests."""

    def __init__(self):
        self.values = {}
        self._lock = asyncio.Lock()

    async def set(self, key, value, ex=None):
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def eval(self, _script, numkeys, key, expected):
        assert numkeys == 1
        async with self._lock:
            if self.values.get(key) == expected:
                del self.values[key]
                return 1
            return 0


@pytest.fixture
def gdpr_app(monkeypatch):
    monkeypatch.setenv("API_KEY_SERVICE", "internal-service-secret")
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("REDIS_URL", "redis://simulated.test/0")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(token_store, "_memory", {})
    monkeypatch.setattr(token_store, "_redis", None)
    monkeypatch.setattr(token_store, "_is_production", lambda: False)
    redis = SimulatedRedis()

    async def get_redis():
        return redis

    monkeypatch.setattr(token_store, "_get_redis", get_redis)

    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(auth_routes.router)
    app.state.repo = AsyncMock()
    app.state.repo.get_auth_access_allowed.return_value = True
    app.state.repo.get_memberships_by_auth.return_value = [
        {"organization_id": "tenant-owned", "user_id": "owner-1", "ruolo": "owner"},
    ]
    export = AsyncMock(return_value={"organization": "tenant-owned"})
    monkeypatch.setattr(routes, "_export_tenant_data", export)
    monkeypatch.setattr(routes, "audit_log", AsyncMock())
    monkeypatch.setattr(dependencies, "is_token_revoked", AsyncMock(return_value=False))
    verify = AsyncMock(return_value={
        "sub": "auth-owner-1", "session_id": "session-owner-1", "aal": "aal2",
    })
    monkeypatch.setattr(dependencies, "verify_supabase_jwt", verify)
    monkeypatch.setattr(auth_routes, "revoke_token", AsyncMock())
    monkeypatch.setattr(auth_routes.bff, "logout", AsyncMock())
    return app, export, verify


def _auth_headers(org_id=None, token="signed-user-token"):
    headers = {"Authorization": f"Bearer {token}"}
    if org_id:
        headers["X-Organization-Id"] = org_id
    return headers


@pytest.fixture
async def client(gdpr_app):
    app, _, _ = gdpr_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        yield client


async def _issue_export(client, org_id="tenant-owned"):
    response = await client.get("/api/gdpr/export", headers=_auth_headers(org_id))
    assert response.status_code == 200
    return response.cookies.get(token_store.DOWNLOAD_COOKIE)


@pytest.mark.parametrize("org_header", [None, "tenant-owned", "tenant-victim"])
async def test_service_key_cannot_export_any_tenant(gdpr_app, org_header):
    app, export, verify = gdpr_app
    headers = {"X-API-Key": "internal-service-secret"}
    if org_header:
        headers["X-Organization-Id"] = org_header
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
        response = await c.get("/api/gdpr/export", headers=headers)
    assert response.status_code == 403
    verify.assert_not_awaited()
    export.assert_not_awaited()
    app.state.repo.get_memberships_by_auth.assert_not_awaited()


@pytest.mark.parametrize("role,aal", [("owner", "aal1"), ("manager", "aal2"), ("staff", "aal2")])
async def test_export_requires_both_owner_membership_and_mfa(gdpr_app, role, aal):
    app, export, verify = gdpr_app
    verify.return_value = {"sub": "auth-owner-1", "session_id": "session-owner-1", "aal": aal}
    app.state.repo.get_memberships_by_auth.return_value[0]["ruolo"] = role
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/gdpr/export", headers=_auth_headers())
    assert response.status_code == 403
    export.assert_not_awaited()
    if aal == "aal1":
        assert response.headers["x-mfa-required"] == "true"


async def test_export_ignores_unowned_tenant_header_for_single_membership(gdpr_app, client):
    app, export, _ = gdpr_app
    response = await client.get(
        "/api/gdpr/export",
        headers={
            **_auth_headers("tenant-victim"),
            "X-Forwarded-Host": "attacker.example.test",
            "X-Forwarded-Proto": "http",
        },
    )
    assert response.status_code == 200
    assert response.json()["download_url"] == "/api/gdpr/download"
    assert "attacker.example.test" not in response.json()["download_url"]
    export.assert_awaited_once_with(app.state.repo, "tenant-owned")
    app.state.repo.get_memberships_by_auth.assert_awaited_once_with("auth-owner-1")


async def test_export_uses_secure_cookie_policy_and_same_origin_download_path(client, monkeypatch):
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")

    response = await client.get("/api/gdpr/export", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["download_url"] == "/api/gdpr/download"
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/api/gdpr/download" in cookie


async def test_download_api_contract_has_owner_tenant_guard_and_no_bearer_path(gdpr_app):
    app, _, _ = gdpr_app
    baseline_path = Path(__file__).resolve().parents[3] / "docs" / "route_contracts_baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    registered = {
        (route.path, method)
        for route in routes.router.routes
        for method in getattr(route, "methods", ())
    }

    assert ("/api/gdpr/download", "GET") in registered
    assert not any(path == "/api/gdpr/download/{token}" for path, _ in registered)
    assert "/api/gdpr/download/{token}::GET" not in baseline
    contract = baseline["/api/gdpr/download::GET"]
    assert contract["required_roles"] == ["owner"]
    assert contract["has_org_context"] is True
    assert routes.gdpr_download.__api_required_roles__ == ("owner",)
    assert routes.gdpr_download.__api_org_context__ is True


async def test_download_requires_authenticated_user_and_preserves_token(client):
    token = await _issue_export(client)
    client.cookies.set(token_store.DOWNLOAD_COOKIE, token)

    response = await client.get("/api/gdpr/download")

    assert response.status_code == 401
    assert await token_store.read_token(token) is not None
    assert "gdpr_export_token" not in response.headers.get("set-cookie", "").lower()


@pytest.mark.parametrize(
    "claims,expected_status",
    [
        ({"sub": "other-user", "session_id": "session-owner-1", "aal": "aal2"}, 403),
        ({"sub": "auth-owner-1", "session_id": "other-session", "aal": "aal2"}, 403),
        ({"sub": "auth-owner-1", "session_id": "session-owner-1", "aal": "aal1"}, 403),
    ],
)
async def test_download_rejects_account_session_or_mfa_switch_and_preserves_token(
    client, gdpr_app, claims, expected_status
):
    _, _, verify = gdpr_app
    token = await _issue_export(client)
    client.cookies.set(token_store.DOWNLOAD_COOKIE, token)
    verify.return_value = claims

    response = await client.get("/api/gdpr/download", headers=_auth_headers())

    assert response.status_code == expected_status
    assert await token_store.read_token(token) is not None
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "membership,headers",
    [
        (None, _auth_headers()),
        ({"organization_id": "tenant-owned", "user_id": "owner-1", "ruolo": "manager"}, _auth_headers()),
        ({"organization_id": "tenant-owned", "user_id": "different-profile", "ruolo": "owner"}, _auth_headers()),
        ({"organization_id": "tenant-victim", "user_id": "owner-1", "ruolo": "owner"}, _auth_headers()),
        ([
            {"organization_id": "tenant-owned", "user_id": "owner-1", "ruolo": "owner"},
            {"organization_id": "tenant-other", "user_id": "owner-1", "ruolo": "owner"},
        ], _auth_headers()),
    ],
)
async def test_download_rejects_removed_or_wrong_tenant_membership_without_consuming(
    client, gdpr_app, membership, headers
):
    app, _, _ = gdpr_app
    token = await _issue_export(client)
    client.cookies.set(token_store.DOWNLOAD_COOKIE, token)
    app.state.repo.get_memberships_by_auth.return_value = (
        membership if isinstance(membership, list) else ([membership] if membership else [])
    )

    response = await client.get("/api/gdpr/download", headers=headers)

    assert response.status_code == 403
    assert await token_store.read_token(token) is not None
    assert "set-cookie" not in response.headers


async def test_download_requires_matching_selected_tenant_for_multi_org_user(client, gdpr_app):
    app, _, _ = gdpr_app
    token = await _issue_export(client)
    client.cookies.set(token_store.DOWNLOAD_COOKIE, token)
    app.state.repo.get_memberships_by_auth.return_value.append({
        "organization_id": "tenant-other", "user_id": "owner-1", "ruolo": "owner",
    })

    missing_context = await client.get("/api/gdpr/download", headers=_auth_headers())
    assert missing_context.status_code == 403
    assert await token_store.read_token(token) is not None

    wrong_context = await client.get(
        "/api/gdpr/download", headers=_auth_headers("tenant-other")
    )
    assert wrong_context.status_code == 403
    assert await token_store.read_token(token) is not None

    correct_context = await client.get(
        "/api/gdpr/download", headers=_auth_headers("tenant-owned")
    )
    assert correct_context.status_code == 200
    assert correct_context.json() == {"organization": "tenant-owned"}


async def test_download_success_is_one_time_and_denials_do_not_consume(client):
    token = await _issue_export(client)
    client.cookies.set(token_store.DOWNLOAD_COOKIE, token)

    first = await client.get("/api/gdpr/download", headers=_auth_headers())
    repeated = await client.get("/api/gdpr/download", headers=_auth_headers())

    assert first.status_code == 200
    assert first.json() == {"organization": "tenant-owned"}
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["referrer-policy"] == "no-referrer"
    assert repeated.status_code == 404


async def test_logout_clears_download_cookie_and_next_download_is_unauthenticated(client):
    token = await _issue_export(client)
    # BFF cookie-backed access uses the same validated JWT path as the export.
    logout = await client.post(
        "/api/auth/logout",
        headers={"Cookie": f"{bff.access_cookie_name()}=signed-user-token"},
    )
    assert logout.status_code == 200
    set_cookie_headers = logout.headers.get_list("set-cookie")
    assert any(
        header.lower().startswith(f"{token_store.DOWNLOAD_COOKIE}=")
        and "max-age=0" in header.lower()
        and "path=/api/gdpr/download" in header.lower()
        for header in set_cookie_headers
    )
    assert bff.access_cookie_name() not in client.cookies
    assert token_store.DOWNLOAD_COOKIE not in client.cookies

    download = await client.get("/api/gdpr/download")
    assert download.status_code == 401
    assert await token_store.read_token(token) is not None


async def test_disabled_account_cannot_download_and_token_remains(client, gdpr_app):
    app, _, _ = gdpr_app
    token = await _issue_export(client)
    client.cookies.set(token_store.DOWNLOAD_COOKIE, token)
    app.state.repo.get_auth_access_allowed.return_value = False

    response = await client.get("/api/gdpr/download", headers=_auth_headers())

    assert response.status_code == 403
    assert await token_store.read_token(token) is not None


async def test_revoked_session_cannot_download_and_token_remains(client, gdpr_app, monkeypatch):
    token = await _issue_export(client)
    client.cookies.set(token_store.DOWNLOAD_COOKIE, token, path="/api/gdpr/download")
    monkeypatch.setattr(dependencies, "is_token_revoked", AsyncMock(return_value=True))

    response = await client.get("/api/gdpr/download", headers=_auth_headers())

    assert response.status_code == 401
    assert await token_store.read_token(token) is not None


async def test_download_fails_closed_on_membership_store_failure_and_keeps_token(client, gdpr_app):
    app, _, _ = gdpr_app
    token = await _issue_export(client)
    client.cookies.set(token_store.DOWNLOAD_COOKIE, token)
    app.state.repo.get_memberships_by_auth.side_effect = OSError("synthetic repository error")

    response = await client.get("/api/gdpr/download", headers=_auth_headers())

    assert response.status_code == 503
    assert await token_store.read_token(token) is not None


async def test_two_concurrent_downloads_return_data_to_one_request(gdpr_app):
    app, _, _ = gdpr_app
    transport = httpx.ASGITransport(app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as issuer:
        token = await _issue_export(issuer)
    headers = _auth_headers()
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as first_client,
        httpx.AsyncClient(transport=transport, base_url="http://test") as second_client,
    ):
        first_client.cookies.set(token_store.DOWNLOAD_COOKIE, token, path="/api/gdpr/download")
        second_client.cookies.set(token_store.DOWNLOAD_COOKIE, token, path="/api/gdpr/download")
        first, second = await asyncio.gather(
            first_client.get("/api/gdpr/download", headers=headers),
            second_client.get("/api/gdpr/download", headers=headers),
        )

    assert sorted((first.status_code, second.status_code)) == [200, 404]
    winner = first if first.status_code == 200 else second
    assert winner.json() == {"organization": "tenant-owned"}
