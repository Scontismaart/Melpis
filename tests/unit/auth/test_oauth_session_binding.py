"""Google integration callbacks must belong to the initiating browser session."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException

from src.core.auth import dependencies
from src.core.calendar import routes as calendar
from src.core.reviews import google_routes as reviews

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
USER_A = "33333333-3333-3333-3333-333333333333"
USER_B = "44444444-4444-4444-4444-444444444444"
SESSION_A = "55555555-5555-5555-5555-555555555555"
SESSION_B = "66666666-6666-6666-6666-666666666666"
KEY = "A" * 43 + "="  # Deterministic 32-byte Fernet test fixture.


class NoncePool:
    def __init__(self):
        self.nonces = {}
        self.credential_writes = []

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, query, *args):
        if "INSERT INTO oauth_nonces" in query:
            nonce, org = args
            self.nonces[(nonce, org)] = {"created_at": datetime.now(timezone.utc)}
        elif "credentials" in query:
            self.credential_writes.append(args)
        else:
            raise AssertionError("Unexpected storage operation")

    async def fetchrow(self, query, nonce, org):
        assert "DELETE FROM oauth_nonces" in query and "RETURNING" in query
        return self.nonces.pop((nonce, org), None)


class CalendarConnectionStub:
    """Route-level fake matching the new CalendarConnection lifecycle API."""

    def __init__(self, pool, fernet):
        self.pool, self.fernet = pool, fernet
        self.generations = {}

    async def begin_oauth(self, org, nonce):
        generation = uuid4()
        self.generations[org] = generation
        self.pool.nonces[(nonce, org)] = {
            "created_at": datetime.now(timezone.utc),
            "calendar_generation": generation,
        }

    async def consume_nonce(self, org, nonce):
        row = self.pool.nonces.get((nonce, org))
        if not row or row["calendar_generation"] != self.generations.get(org):
            return None
        return self.pool.nonces.pop((nonce, org))

    async def save_oauth(self, org, generation, creds):
        if generation != self.generations.get(org):
            return False
        if not creds.token or not creds.refresh_token or creds.expiry is None:
            return False
        access = self.fernet.encrypt(creds.token.encode()).decode()
        refresh = self.fernet.encrypt(creds.refresh_token.encode()).decode()
        self.pool.credential_writes.append((org, access, refresh, creds.expiry))
        return True


@pytest.fixture(params=[("calendar", calendar, "/api/calendar"), ("reviews_google", reviews, "/api/reviews/google")])
async def flow_env(request, monkeypatch):
    channel, module, prefix = request.param
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_BUSINESS_ENABLED", "true")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("ENCRYPTION_KEY", KEY)
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    monkeypatch.setattr(dependencies, "is_token_revoked", AsyncMock(return_value=False))

    async def verify(token):
        if token == "expired":
            raise HTTPException(401, "synthetic expired session")
        claims = {"sub": USER_A, "session_id": SESSION_A, "aal": "aal2"}
        if token == "session-b":
            claims["session_id"] = SESSION_B
        elif token == "other-user":
            claims.update(sub=USER_B, session_id=SESSION_B)
        elif token == "no-session":
            claims.pop("session_id")
        elif token == "aal1":
            claims["aal"] = "aal1"
        return claims

    monkeypatch.setattr(dependencies, "verify_supabase_jwt", verify)
    pool = NoncePool()
    membership = {"organization_id": ORG_A, "user_id": USER_A, "ruolo": "owner"}

    async def exact_membership(uid, org):
        return dict(membership) if membership and org == ORG_A else None

    repo = SimpleNamespace(
        get_auth_access_allowed=AsyncMock(return_value=True),
        get_memberships_by_auth=AsyncMock(return_value=[dict(membership)]),
        get_membership_by_auth=AsyncMock(side_effect=exact_membership),
    )
    fernet = Fernet(KEY.encode())
    service = SimpleNamespace(encrypt_secret=lambda value: fernet.encrypt(value.encode()).decode())
    flow = SimpleNamespace(
        authorization_url=MagicMock(side_effect=lambda **kw: (
            "https://accounts.google.test/authorize?" + urlencode({"state": kw["state"]}), kw["state"],
        )),
        fetch_token=MagicMock(),
        oauth2session=SimpleNamespace(token={"scope": module.SCOPES}),
        credentials=SimpleNamespace(token="synthetic-access", refresh_token="synthetic-refresh",
                                     expiry=datetime.now(timezone.utc) + timedelta(hours=1)),
    )
    monkeypatch.setattr(calendar, "_make_flow", lambda: flow)
    monkeypatch.setattr(reviews, "_make_flow", lambda: flow)
    monkeypatch.setattr(reviews, "check_feature_blocked_by_plan", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(calendar.router)
    app.include_router(reviews.router)
    app.state.pool = pool
    app.state.repo = repo
    if channel == "calendar":
        service.connection = CalendarConnectionStub(pool, fernet)
    app.state.calendar_service = service
    app.state.reviews_service = service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        env = SimpleNamespace(channel=channel, prefix=prefix, client=client, pool=pool,
                              membership=membership, repo=repo, flow=flow, fernet=fernet)

        async def start_response(token="session-a", *, params=None, headers=None):
            client.cookies.clear()
            client.cookies.set("wa_at", token)
            return await client.get(prefix + "/auth", params=params, headers=headers)

        async def start(token="session-a", *, params=None, headers=None):
            response = await start_response(token, params=params, headers=headers)
            assert response.status_code == 307
            return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]

        async def callback(state, token="session-a", **params):
            client.cookies.clear()
            if token:
                client.cookies.set("wa_at", token)
            return await client.get(prefix + "/oauth2callback", params={"state": state, "code": "synthetic-code", **params})

        env.start = start
        env.start_response = start_response
        env.callback = callback
        yield env


async def test_multi_org_start_requires_explicit_organization(flow_env):
    env = flow_env
    env.repo.get_memberships_by_auth.return_value = [
        dict(env.membership),
        {"organization_id": ORG_B, "user_id": USER_A, "ruolo": "owner"},
    ]

    response = await env.start_response()

    assert response.status_code == 403
    assert env.pool.nonces == {}
    env.flow.authorization_url.assert_not_called()


async def test_selected_organization_is_bound_into_oauth_state(flow_env):
    env = flow_env

    state = await env.start(params={"organization_id": ORG_A})

    assert state.startswith(ORG_A + ":")
    assert (state.split(":", 1)[1], ORG_A) in env.pool.nonces
    env.repo.get_membership_by_auth.assert_awaited_once_with(USER_A, ORG_A)


async def test_foreign_organization_selector_is_rejected_before_nonce_creation(flow_env):
    env = flow_env

    response = await env.start_response(params={"organization_id": ORG_B})

    assert response.status_code == 403
    assert env.pool.nonces == {}
    env.flow.authorization_url.assert_not_called()


async def test_malformed_organization_selector_is_validation_error(flow_env):
    env = flow_env

    response = await env.start_response(params={"organization_id": "not-a-uuid"})

    assert response.status_code == 422
    assert env.pool.nonces == {}
    env.flow.authorization_url.assert_not_called()


async def test_conflicting_organization_header_and_query_are_rejected(flow_env):
    env = flow_env

    response = await env.start_response(
        params={"organization_id": ORG_A}, headers={"X-Organization-Id": ORG_B},
    )

    assert response.status_code == 403
    assert env.pool.nonces == {}
    env.flow.authorization_url.assert_not_called()


async def test_selected_organization_requires_owner_membership(flow_env):
    env = flow_env
    env.membership["ruolo"] = "manager"

    response = await env.start_response(params={"organization_id": ORG_A})

    assert response.status_code == 403
    assert env.pool.nonces == {}
    env.flow.authorization_url.assert_not_called()


async def test_selected_organization_start_still_requires_aal2(flow_env):
    env = flow_env

    response = await env.start_response(token="aal1", params={"organization_id": ORG_A})

    assert response.status_code == 403
    assert env.pool.nonces == {}
    env.flow.authorization_url.assert_not_called()


def assert_rejected(env, response):
    location = response.headers["location"]
    assert f"{env.channel}=error" in location
    assert "synthetic" not in location and USER_A not in location and SESSION_A not in location
    assert env.pool.credential_writes == []
    env.flow.fetch_token.assert_not_called()


async def test_same_session_saves_encrypted_credentials_and_preserves_callback(flow_env):
    env = flow_env
    state = await env.start()
    response = await env.callback(state)
    assert response.headers["location"] == f"/app/?{env.channel}=connected"
    assert len(env.pool.credential_writes) == 1
    org, access, refresh, _expiry = env.pool.credential_writes[0]
    assert org == ORG_A
    assert env.fernet.decrypt(access.encode()) == b"synthetic-access"
    assert env.fernet.decrypt(refresh.encode()) == b"synthetic-refresh"
    assert USER_A not in state and SESSION_A not in state


@pytest.mark.parametrize("token", ["session-b", "other-user", "no-session", "aal1", "expired", None])
async def test_other_or_invalid_browser_session_cannot_complete_initiator_flow(flow_env, token):
    env = flow_env
    state = await env.start()
    assert_rejected(env, await env.callback(state, token=token))
    # An unrelated browser must not burn the rightful browser's one-shot flow.
    response = await env.callback(state)
    assert response.headers["location"] == f"/app/?{env.channel}=connected"


async def test_access_token_refresh_within_same_session_preserves_flow(flow_env):
    env = flow_env
    state = await env.start()
    assert (await env.callback(state, token="refreshed-a")).headers["location"].endswith("=connected")


@pytest.mark.parametrize("change", ["other-org", "proof", "unsigned"])
async def test_tampered_or_unsigned_state_never_exchanges_code(flow_env, change):
    env = flow_env
    state = await env.start()
    if change == "other-org":
        state = ORG_B + ":" + state.split(":", 1)[1]
    elif change == "proof":
        state = state[:-1] + ("a" if state[-1] != "a" else "b")
    else:
        state = ORG_A + ":" + "a" * 32
    assert_rejected(env, await env.callback(state))


async def test_nonce_replay_is_rejected(flow_env):
    env = flow_env
    state = await env.start()
    await env.callback(state)
    response = await env.callback(state)
    assert response.headers["location"].endswith("reason=invalid_nonce")
    assert len(env.pool.credential_writes) == 1
    assert env.flow.fetch_token.call_count == 1


async def test_expired_nonce_is_rejected_before_exchange(flow_env):
    env = flow_env
    state = await env.start()
    org, nonce = state.split(":", 1)
    env.pool.nonces[(nonce, org)]["created_at"] -= timedelta(minutes=11)
    response = await env.callback(state)
    assert_rejected(env, response)
    assert response.headers["location"].endswith("reason=nonce_expired")


@pytest.mark.parametrize("role", [None, "manager", "staff"])
async def test_lost_or_demoted_owner_membership_rejects_callback(flow_env, role):
    env = flow_env
    state = await env.start()
    if role is None:
        env.membership.clear()
    else:
        env.membership["ruolo"] = role
    assert_rejected(env, await env.callback(state))


async def test_membership_revoked_during_exchange_prevents_credential_write(flow_env):
    env = flow_env
    state = await env.start()
    env.flow.fetch_token.side_effect = lambda **kw: env.membership.clear()
    response = await env.callback(state)
    assert f"{env.channel}=error" in response.headers["location"]
    assert env.pool.credential_writes == []
    env.flow.fetch_token.assert_called_once()


async def test_provider_denial_consumes_nonce_without_leaking_error(flow_env):
    env = flow_env
    state = await env.start()
    denied = await env.callback(state, error="synthetic-private-provider-error")
    assert_rejected(env, denied)
    assert denied.headers["location"].endswith("reason=provider_denied")
    assert (await env.callback(state)).headers["location"].endswith("reason=invalid_nonce")


@pytest.mark.parametrize("key", ["", "invalid-key"])
async def test_missing_or_invalid_binding_key_fails_closed_at_start(flow_env, monkeypatch, key):
    env = flow_env
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    env.client.cookies.set("wa_at", "session-a")
    response = await env.client.get(env.prefix + "/auth")
    assert response.status_code == 503
    assert env.pool.nonces == {}
    env.flow.authorization_url.assert_not_called()


async def test_session_id_stays_internal(flow_env):
    env = flow_env
    from src.core.auth.routes import me
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=env.repo)),
    )
    user = await dependencies.get_current_user(request, token="session-a")
    assert user["session_id"] == SESSION_A
    env.repo.get_auth_access_allowed.assert_awaited_once_with(USER_A)
    assert "session_id" not in await me(user=user)


async def test_state_cannot_be_reused_for_a_different_google_integration(flow_env):
    env = flow_env
    state = await env.start()
    other_prefix = "/api/reviews/google" if env.channel == "calendar" else "/api/calendar"
    response = await env.client.get(other_prefix + "/oauth2callback", params={"state": state, "code": "synthetic-code"})
    assert "=error" in response.headers["location"]
    assert env.pool.credential_writes == []
    env.flow.fetch_token.assert_not_called()
    assert (await env.callback(state)).headers["location"].endswith("=connected")


async def test_callback_requires_browser_cookie_even_with_initiator_bearer_token(flow_env):
    env = flow_env
    state = await env.start()
    env.client.cookies.clear()
    response = await env.client.get(env.prefix + "/oauth2callback", params={"state": state, "code": "synthetic-code"},
                                    headers={"Authorization": "Bearer session-a"})
    assert_rejected(env, response)


async def test_concurrent_callbacks_exchange_and_save_only_once(flow_env):
    import asyncio

    env = flow_env
    state = await env.start()
    responses = await asyncio.gather(env.callback(state), env.callback(state))
    locations = [response.headers["location"] for response in responses]
    assert sum(location.endswith("=connected") for location in locations) == 1
    assert sum(location.endswith("reason=invalid_nonce") for location in locations) == 1
    assert len(env.pool.credential_writes) == 1
    assert env.flow.fetch_token.call_count == 1


async def test_session_revoked_during_exchange_prevents_credential_write(flow_env, monkeypatch):
    env = flow_env
    state = await env.start()
    env.flow.fetch_token.side_effect = lambda **kw: monkeypatch.setattr(
        dependencies, "is_token_revoked", AsyncMock(return_value=True),
    )
    response = await env.callback(state)
    assert "=error" in response.headers["location"]
    assert env.pool.credential_writes == []
    env.flow.fetch_token.assert_called_once()


async def test_missing_binding_key_at_callback_never_exchanges_code(flow_env, monkeypatch):
    env = flow_env
    state = await env.start()
    monkeypatch.delenv("ENCRYPTION_KEY")
    assert_rejected(env, await env.callback(state))
