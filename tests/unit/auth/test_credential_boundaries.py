"""Offline regression tests: owner+MFA and provider verification are mandatory."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.core.auth.dependencies import get_current_user, get_organization_context


@pytest.fixture(autouse=True)
def enable_google_integrations(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_BUSINESS_ENABLED", "true")


def test_invalid_whatsapp_credentials_are_never_echoed_in_validation_response():
    from src.whatsapp.routes import router

    app = FastAPI()
    app.include_router(router)
    identity = {"source": "jwt", "organization_id": "org-1", "ruolo": "owner", "aal": "aal2"}
    app.dependency_overrides[get_current_user] = lambda: identity
    app.dependency_overrides[get_organization_context] = lambda: identity
    private_value = "private-meta-token" * 600
    response = TestClient(app).post("/api/whatsapp/connect", json={
        "phone_number_id": "123", "waba_id": "456", "access_token": private_value,
    })
    assert response.status_code == 422
    assert "private-meta-token" not in response.text


@pytest.mark.parametrize("role,aal", [("manager", "aal2"), ("staff", "aal2"), ("owner", "aal1")])
@pytest.mark.parametrize("channel", ["whatsapp", "instagram"])
def test_channel_credentials_require_owner_mfa(role, aal, channel):
    from src.whatsapp.routes import router as wa
    from src.instagram.routes import router as ig
    app = FastAPI()
    app.include_router(wa)
    app.include_router(ig)
    identity = {"source": "jwt", "organization_id": "org-1", "ruolo": role, "aal": aal}
    app.dependency_overrides[get_current_user] = lambda: identity
    app.dependency_overrides[get_organization_context] = lambda: identity
    response = TestClient(app).delete(f"/api/{channel}/account")
    assert response.status_code == 403


@pytest.mark.parametrize(
    "router_name,method,path,payload",
    [
        ("whatsapp", "post", "/api/whatsapp/account", {
            "phone_number_id": "123", "waba_id": "456", "access_token": "secret",
        }),
        ("instagram", "post", "/api/instagram/account", {
            "ig_user_id": "123", "access_token": "secret",
        }),
        ("calendar", "get", "/api/calendar/auth", None),
        ("reviews", "get", "/api/reviews/google/auth", None),
        ("airtable", "post", "/api/v1/integrations/airtable/connect", {
            "token": "pat-secret", "base_id": "app123", "base_name": "Test",
        }),
        ("booking", "post", "/api/v1/integrations/booking", {
            "provider": "simplybook", "credentials": {"api_key": "secret"},
        }),
    ],
)
@pytest.mark.parametrize("role,aal", [("manager", "aal2"), ("owner", "aal1")])
def test_all_credential_writes_require_owner_and_mfa(
    router_name, method, path, payload, role, aal, monkeypatch, tmp_path,
):
    monkeypatch.setenv("CREWAI_STORAGE_DIR", str(tmp_path / "crewai"))
    from src.api.routes.airtable import router as airtable_router
    from src.api.routes.integrations import router as integrations_router
    from src.core.calendar.routes import router as calendar_router
    from src.core.reviews.google_routes import router as reviews_router
    from src.instagram.routes import router as instagram_router
    from src.whatsapp.routes import router as whatsapp_router

    routers = {
        "whatsapp": whatsapp_router,
        "instagram": instagram_router,
        "calendar": calendar_router,
        "reviews": reviews_router,
        "airtable": airtable_router,
        "booking": integrations_router,
    }
    identity = {
        "source": "jwt", "organization_id": "org-1", "ruolo": role, "aal": aal,
        "auth_user_id": "33333333-3333-3333-3333-333333333333",
        "user_id": "44444444-4444-4444-4444-444444444444",
    }
    app = FastAPI()
    app.include_router(routers[router_name])
    app.state.repo = SimpleNamespace(
        get_memberships_by_auth=AsyncMock(return_value=[identity]),
    )
    app.dependency_overrides[get_current_user] = lambda: identity
    app.dependency_overrides[get_organization_context] = lambda: identity
    response = TestClient(app).request(method, path, json=payload)
    assert response.status_code == 403


def test_service_api_key_cannot_reach_user_credential_route(monkeypatch):
    from src.instagram.routes import router

    monkeypatch.setenv("API_KEY_SERVICE", "service-secret")
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/api/instagram/account",
        headers={"X-API-Key": "service-secret"},
        json={"ig_user_id": "123", "access_token": "credential"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("result", [503, 429, "timeout", "malformed", "wrong_account"])
async def test_whatsapp_validation_failure_never_saves(monkeypatch, result):
    import src.whatsapp.routes as routes
    repo = MagicMock()
    repo.save_tenant_config = AsyncMock()
    monkeypatch.setattr(routes, "_get_wrepo", lambda request: repo)
    client = MagicMock()
    if result == "timeout":
        client.get = AsyncMock(side_effect=httpx.TimeoutException("private-token"))
    elif result == "wrong_account":
        client.get = AsyncMock(side_effect=[httpx.Response(200, json={"id": "123"}), httpx.Response(200, json={"data": [{"id": "other"}]})])
    else:
        response = httpx.Response(200, json={}) if result == "malformed" else httpx.Response(result)
        client.get = AsyncMock(return_value=response)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: context)
    with pytest.raises(HTTPException) as exc:
        await routes.connect_whatsapp_account(
            routes.WhatsAppAccountRequest(phone_number_id="123", waba_id="456", access_token="private-token"),
            SimpleNamespace(), user={"organization_id": "org-1"}, mfa={},
        )
    assert exc.value.status_code in (400, 503)
    assert "private-token" not in exc.value.detail
    repo.save_tenant_config.assert_not_awaited()


async def test_whatsapp_test_send_denies_non_allowlisted_recipient(monkeypatch):
    import src.whatsapp.routes as routes

    monkeypatch.setenv("SANDBOX_ONLY", "true")
    monkeypatch.setenv("WHATSAPP_TEST_RECIPIENTS", "39000000000")
    repo = MagicMock()
    repo.get_tenant_config = AsyncMock(return_value={
        "access_token": "encrypted-token",
        "phone_number_id": "123456",
    })
    repo.decrypt_token.return_value = "private-token"
    monkeypatch.setattr(routes, "_get_wrepo", lambda request: repo)

    class NoNetwork:
        def __init__(self, **kwargs):
            raise AssertionError("network client must not be created")

    monkeypatch.setattr(routes.httpx, "AsyncClient", NoNetwork)
    with pytest.raises(HTTPException) as exc:
        await routes.send_test_message(
            routes.WhatsAppTestMessageRequest(to_phone="+39000000001", idempotency_key=UUID("11111111-1111-4111-8111-111111111111")),
            SimpleNamespace(),
            user={"organization_id": "org-1"},
        )
    assert exc.value.status_code == 403
    assert "39000000001" not in exc.value.detail


async def test_whatsapp_test_send_allows_explicit_sandbox_recipient(monkeypatch):
    import src.whatsapp.routes as routes

    monkeypatch.setenv("SANDBOX_ONLY", "true")
    monkeypatch.setenv("WHATSAPP_TEST_RECIPIENTS", "39000000000")
    repo = MagicMock()
    repo.get_tenant_config = AsyncMock(return_value={
        "access_token": "encrypted-token",
        "phone_number_id": "123456",
    })
    repo.decrypt_token.return_value = "private-token"
    repo.has_recent_whatsapp_inbound = AsyncMock(return_value=True)
    repo.get_contact_prefs = AsyncMock(return_value={"marketing_opt_out": False, "consent_status": "granted"})
    monkeypatch.setattr(routes, "_get_wrepo", lambda request: repo)
    send = AsyncMock(return_value={"status": "sent", "wam_id": "wamid.test"})
    monkeypatch.setattr(routes.WhatsAppService, "send_whatsapp_message", send)

    result = await routes.send_test_message(
        routes.WhatsAppTestMessageRequest(to_phone="+39000000000", idempotency_key=UUID("11111111-1111-4111-8111-111111111111")),
        SimpleNamespace(),
        user={"organization_id": "00000000-0000-4000-8000-000000000001"},
    )
    assert result["status"] == "sent"
    assert result["message_id"] == "wamid.test"
    send.assert_awaited_once()
    assert send.await_args.kwargs["category"] == "marketing"
    assert send.await_args.kwargs["idempotency_key"] == "manual-test:11111111-1111-4111-8111-111111111111"
    assert send.await_args.kwargs["tenant_config"].access_token == "private-token"


@pytest.mark.parametrize("inbound,prefs,expected", [
    (False, {"marketing_opt_out": False}, 403),
    (True, {"marketing_opt_out": True}, 403),
    (True, {"consent_status": "withdrawn"}, 403),
    (True, None, 403),
])
async def test_whatsapp_test_send_requires_recent_inbound_and_consent(monkeypatch, inbound, prefs, expected):
    import src.whatsapp.routes as routes
    monkeypatch.setenv("SANDBOX_ONLY", "true")
    monkeypatch.setenv("WHATSAPP_TEST_RECIPIENTS", "39000000000")
    repo = MagicMock()
    repo.get_tenant_config = AsyncMock(return_value={"access_token": "encrypted", "phone_number_id": "123"})
    repo.decrypt_token.return_value = "private-token"
    repo.has_recent_whatsapp_inbound = AsyncMock(return_value=inbound)
    repo.get_contact_prefs = AsyncMock(return_value=prefs)
    monkeypatch.setattr(routes, "_get_wrepo", lambda request: repo)
    send = AsyncMock()
    monkeypatch.setattr(routes.WhatsAppService, "send_whatsapp_message", send)
    with pytest.raises(HTTPException) as exc:
        await routes.send_test_message(
            routes.WhatsAppTestMessageRequest(to_phone="+39000000000", idempotency_key=UUID("11111111-1111-4111-8111-111111111111")),
            SimpleNamespace(), user={"organization_id": "00000000-0000-4000-8000-000000000001"},
        )
    assert exc.value.status_code == expected
    send.assert_not_awaited()


async def test_whatsapp_test_probe_bearer_only_and_bad_decryption_fails_closed(monkeypatch):
    import src.whatsapp.routes as routes
    repo = MagicMock()
    repo.get_tenant_config = AsyncMock(return_value={"access_token": "ciphertext", "phone_number_id": "123"})
    monkeypatch.setattr(routes, "_get_wrepo", lambda request: repo)
    repo.decrypt_token.side_effect = ValueError("secret-token")
    with pytest.raises(HTTPException) as exc:
        await routes.send_test_message(routes.WhatsAppTestMessageRequest(), SimpleNamespace(), user={"organization_id": "org-1"})
    assert exc.value.status_code == 503
    assert "secret-token" not in exc.value.detail
    repo.decrypt_token.side_effect = None
    repo.decrypt_token.return_value = "private-token"
    client = MagicMock()
    client.get = AsyncMock(return_value=httpx.Response(200, json={"id": "123", "verified_name": "Test"}))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: context)
    result = await routes.send_test_message(routes.WhatsAppTestMessageRequest(), SimpleNamespace(), user={"organization_id": "org-1"})
    assert result["status"] == "connected"
    assert client.get.await_args.kwargs["headers"] == {"Authorization": "Bearer private-token"}
    assert "access_token" not in client.get.await_args.kwargs["params"]


async def test_whatsapp_recent_inbound_query_is_tenant_and_channel_scoped():
    from src.core.db.repositories.contact_repo import ContactRepository
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=True)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=conn)
    context.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = context
    org_id = UUID("00000000-0000-4000-8000-000000000001")
    assert await ContactRepository(pool).has_recent_whatsapp_inbound(org_id, "39000000000") is True
    sql = conn.fetchval.await_args.args[0]
    assert sql.count("organization_id = $1::uuid") == 3
    assert "cv.canale = 'whatsapp'" in sql
    assert "m.direction = 'inbound'" in sql
    assert "NOW() - INTERVAL '24 hours'" in sql


@pytest.mark.parametrize("subscription_response,expected_active", [
    (httpx.Response(403, json={"error": {"message": "private-token"}}), False),
    (httpx.Response(200, json={}), False),
    (httpx.Response(200, json={"success": "true"}), False),
    (httpx.Response(200, json={"success": True}), True),
])
async def test_whatsapp_connect_reports_unconfirmed_subscription(monkeypatch, subscription_response, expected_active):
    import src.whatsapp.routes as routes
    repo = MagicMock()
    repo.save_tenant_config = AsyncMock()
    monkeypatch.setattr(routes, "_get_wrepo", lambda request: repo)
    client = MagicMock()
    client.get = AsyncMock(side_effect=[
        httpx.Response(200, json={"id": "123", "verified_name": "Test"}),
        httpx.Response(200, json={"data": [{"id": "123"}]}),
    ])
    client.post = AsyncMock(return_value=subscription_response)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: context)
    result = await routes.connect_whatsapp_account(
        routes.WhatsAppAccountRequest(phone_number_id="123", waba_id="456", access_token="private-token"),
        SimpleNamespace(), user={"organization_id": "org-1"}, mfa={},
    )
    assert result["status"] == "connected"
    assert result["webhook_subscription_active"] is expected_active
    assert "private-token" not in str(result)
    repo.save_tenant_config.assert_awaited_once()


async def test_whatsapp_connect_rejects_malformed_ownership(monkeypatch):
    import src.whatsapp.routes as routes
    repo = MagicMock()
    repo.save_tenant_config = AsyncMock()
    monkeypatch.setattr(routes, "_get_wrepo", lambda request: repo)
    client = MagicMock()
    client.get = AsyncMock(side_effect=[
        httpx.Response(200, json={"id": "123"}),
        httpx.Response(200, json={"data": "123"}),
    ])
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: context)
    with pytest.raises(HTTPException) as exc:
        await routes.connect_whatsapp_account(
            routes.WhatsAppAccountRequest(phone_number_id="123", waba_id="456", access_token="private-token"),
            SimpleNamespace(), user={"organization_id": "org-1"}, mfa={},
        )
    assert exc.value.status_code == 503
    repo.save_tenant_config.assert_not_awaited()


@pytest.mark.parametrize("channel", ["calendar", "reviews_google"])
async def test_oauth_failure_has_safe_redirect_and_no_secret(channel, caplog):
    from src.core.auth.oauth_callback import safe_oauth_callback
    @safe_oauth_callback(channel)
    async def fails():
        raise RuntimeError("secret-token")
    result = await fails()
    assert result.headers["location"] == f"/app/?{channel}=error&reason=server_error"
    assert "secret-token" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


class _OneShotNonceConnection:
    def __init__(self):
        from datetime import datetime, timezone

        self.row = {"created_at": datetime.now(timezone.utc)}
        self.calls = []

    async def fetchrow(self, query, nonce, organization_id):
        self.calls.append((query, nonce, organization_id))
        row, self.row = self.row, None
        return row


class _AcquireNonceConnection:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return None


class _OneShotNoncePool:
    def __init__(self):
        self.connection = _OneShotNonceConnection()

    def acquire(self):
        return _AcquireNonceConnection(self.connection)


class _CalendarConnectionNonce:
    """The Calendar callback now consumes OAuth state through its connection service."""

    def __init__(self):
        from datetime import datetime, timezone

        self.row = {"created_at": datetime.now(timezone.utc)}
        self.calls = []

    async def consume_nonce(self, organization_id, nonce):
        self.calls.append((organization_id, nonce))
        row, self.row = self.row, None
        return row


@pytest.mark.parametrize(
    "callback,channel",
    [
        ("calendar", "calendar"),
        ("reviews", "reviews_google"),
    ],
)
async def test_oauth_provider_denial_consumes_nonce_once(callback, channel, caplog, monkeypatch):
    if callback == "calendar":
        from src.core.calendar.routes import calendar_oauth2callback as handler
    else:
        from src.core.reviews.google_routes import google_reviews_oauth2callback as handler

    org_id = "11111111-1111-1111-1111-111111111111"
    from src.core.auth import bff, dependencies
    from src.core.auth.oauth_state import create_bound_oauth_nonce

    # Deterministic 32-byte Fernet key for this isolated test.
    monkeypatch.setenv("ENCRYPTION_KEY", "A" * 43 + "=")
    user = {"source": "jwt", "aal": "aal2", "auth_user_id": "synthetic-user", "session_id": "synthetic-session"}
    monkeypatch.setattr(dependencies, "get_current_user", AsyncMock(return_value=user))
    nonce = create_bound_oauth_nonce(channel, org_id, user)
    pool = _OneShotNoncePool()
    calendar_connection = _CalendarConnectionNonce()
    request = SimpleNamespace(
        query_params={
            "state": f"{org_id}:{nonce}",
            "error": "private-provider-error",
        },
        cookies={bff.access_cookie_name(): "synthetic-cookie"},
        app=SimpleNamespace(state=SimpleNamespace(
            pool=pool,
            calendar_service=SimpleNamespace(connection=calendar_connection),
            repo=SimpleNamespace(
                get_membership_by_auth=AsyncMock(return_value={"organization_id": org_id, "ruolo": "owner"}),
            ),
        )),
    )

    denied = await handler(request)
    assert denied.headers["location"] == f"/app/?{channel}=error&reason=provider_denied"
    assert "private-provider-error" not in caplog.text
    if callback == "calendar":
        assert calendar_connection.calls == [(org_id, nonce)]
        assert pool.connection.calls == []
    else:
        query, used_nonce, used_org = pool.connection.calls[0]
        assert "DELETE FROM oauth_nonces" in query
        assert "RETURNING" in query
        assert (used_nonce, used_org) == (nonce, org_id)

    replay = await handler(request)
    assert replay.headers["location"] == f"/app/?{channel}=error&reason=invalid_nonce"
    if callback == "calendar":
        # Replay reaches the one-shot DELETE again, but gets no row back.
        assert calendar_connection.calls == [(org_id, nonce), (org_id, nonce)]
        assert calendar_connection.row is None


@pytest.mark.parametrize("callback", ["calendar", "reviews"])
async def test_oauth_rejects_malformed_nonce_before_database(callback):
    if callback == "calendar":
        from src.core.calendar.routes import calendar_oauth2callback as handler
    else:
        from src.core.reviews.google_routes import google_reviews_oauth2callback as handler

    request = SimpleNamespace(
        query_params={
            "state": "11111111-1111-1111-1111-111111111111:not-a-nonce",
            "code": "unused",
        },
        app=SimpleNamespace(state=SimpleNamespace(pool=None)),
    )
    result = await handler(request)
    assert result.headers["location"].endswith("reason=invalid_state")
