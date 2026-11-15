"""Security contracts for the pending Team invitation boundary."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.core import team_invitations as invitations


@pytest.fixture(autouse=True)
def local_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_):
        return False


class _Connection:
    def __init__(self, *, live=True, email="invitee@example.test", seat_count=1, seat_limit=3):
        self.org = uuid.uuid4()
        self.invitation = uuid.uuid4()
        self.inviter = uuid.uuid4()
        self.profile = uuid.uuid4()
        self.live = live
        self.email = email
        self.seat_count = seat_count
        self.seat_limit = seat_limit
        self.statements = []

    def transaction(self):
        return _Context(self)

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return "UPDATE 1"

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        if "SELECT organization_id FROM team_invitations" in sql:
            return {"organization_id": self.org}
        if "SELECT plan, users_limit FROM organizations" in sql:
            return {"plan": "pro", "users_limit": self.seat_limit}
        if "SELECT id FROM organizations" in sql:
            return {"id": self.org}
        if "SELECT id, email, ruolo, invited_by FROM team_invitations" in sql:
            return {"id": self.invitation, "email": self.email, "ruolo": "staff", "invited_by": self.inviter}
        if "SELECT ruolo FROM organization_memberships" in sql:
            return {"ruolo": "owner"}
        if "SELECT id FROM user_profiles" in sql:
            return {"id": self.profile}
        return None

    async def fetchval(self, sql, *args):
        self.statements.append((sql, args))
        if "SELECT EXISTS" in sql:
            return self.live
        if "SELECT count(*)" in sql:
            return self.seat_count
        return None


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Context(self.conn)


@pytest.mark.asyncio
async def test_create_invitation_never_grants_membership(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", "invitee@example.test")
    conn = _Connection()
    actor = {"user_id": str(uuid.uuid4()), "auth_user_id": str(uuid.uuid4())}
    limiter = AsyncMock()
    limiter.hit.return_value = False
    monkeypatch.setattr(invitations, "get_rate_limiter", AsyncMock(return_value=limiter))
    monkeypatch.setattr(invitations, "delivery_available", lambda: True)
    monkeypatch.setattr(invitations.bff, "public_app_url", lambda: "https://app.example.test")
    monkeypatch.setattr(invitations, "send_invitation", AsyncMock())
    monkeypatch.setattr(invitations, "_dev_links_enabled", lambda: False)
    response = await invitations.create_invitation(
        _Pool(conn), org_id=str(conn.org), actor=actor,
        email="Invitee@Example.Test", role="staff",
    )
    assert "test_link" not in response
    assert any("INSERT INTO team_invitations" in sql for sql, _ in conn.statements)
    assert not any("INSERT INTO organization_memberships" in sql for sql, _ in conn.statements)
    assert all("Invitee@Example.Test" not in str(args) for _, args in conn.statements)


@pytest.mark.asyncio
async def test_accept_invitation_grants_only_after_verified_email(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", "invitee@example.test")
    conn = _Connection()
    token = "A" * 43
    auth_id = str(uuid.uuid4())
    monkeypatch.setattr(invitations, "verified_supabase_identity", AsyncMock(return_value="invitee@example.test"))
    result = await invitations.accept_invitation(
        _Pool(conn), raw_token=token, auth_user_id=auth_id, access_token="verified-session",
    )
    assert result["organization_id"] == str(conn.org)
    assert any("INSERT INTO organization_memberships" in sql for sql, _ in conn.statements)
    assert any("UPDATE team_invitations SET consumed_at" in sql for sql, _ in conn.statements)
    assert any("team.invito_accettato" in str(args) for _, args in conn.statements)
    assert all(token not in str(args) for _, args in conn.statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("live,email,seat_count", [
    (False, "invitee@example.test", 1),
    (True, "different@example.test", 1),
    (True, "invitee@example.test", 3),
])
async def test_accept_rejects_replay_wrong_email_and_full_team(monkeypatch, live, email, seat_count):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", "invitee@example.test")
    conn = _Connection(live=live, email=email, seat_count=seat_count)
    monkeypatch.setattr(invitations, "verified_supabase_identity", AsyncMock(return_value="invitee@example.test"))
    with pytest.raises(HTTPException):
        await invitations.accept_invitation(
            _Pool(conn), raw_token="A" * 43,
            auth_user_id=str(uuid.uuid4()), access_token="verified-session",
        )
    assert not any("INSERT INTO organization_memberships" in sql for sql, _ in conn.statements)


@pytest.mark.asyncio
async def test_unlisted_beta_invitee_cannot_be_created_or_accepted(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", "reviewed@example.test")
    conn = _Connection(email="invitee@example.test")
    actor = {"user_id": str(uuid.uuid4()), "auth_user_id": str(uuid.uuid4())}
    limiter = AsyncMock()
    limiter.hit.return_value = False
    monkeypatch.setattr(invitations, "get_rate_limiter", AsyncMock(return_value=limiter))
    monkeypatch.setattr(invitations, "delivery_available", lambda: True)
    send = AsyncMock()
    monkeypatch.setattr(invitations, "send_invitation", send)

    with pytest.raises(HTTPException) as create_error:
        await invitations.create_invitation(
            _Pool(conn), org_id=str(conn.org), actor=actor,
            email="invitee@example.test", role="staff",
        )
    assert create_error.value.status_code == 403
    assert conn.statements == []
    send.assert_not_awaited()

    monkeypatch.setattr(
        invitations, "verified_supabase_identity", AsyncMock(return_value="invitee@example.test")
    )
    with pytest.raises(HTTPException) as accept_error:
        await invitations.accept_invitation(
            _Pool(conn), raw_token="A" * 43,
            auth_user_id=str(uuid.uuid4()), access_token="verified-session",
        )
    assert accept_error.value.status_code == 400
    assert conn.statements == []


@pytest.mark.asyncio
async def test_production_never_returns_test_link(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TEAM_INVITE_DEV_LINKS", "true")
    assert not invitations._dev_links_enabled()


@pytest.mark.asyncio
async def test_resend_requires_same_live_invitation_under_lock(monkeypatch):
    conn = _Connection()
    actor = {"user_id": str(uuid.uuid4()), "auth_user_id": str(uuid.uuid4())}
    limiter = AsyncMock()
    limiter.hit.return_value = False
    monkeypatch.setattr(invitations, "get_rate_limiter", AsyncMock(return_value=limiter))
    monkeypatch.setattr(invitations, "delivery_available", lambda: True)
    monkeypatch.setattr(invitations.bff, "public_app_url", lambda: "https://app.example.test")
    send = AsyncMock()
    monkeypatch.setattr(invitations, "send_invitation", send)
    with pytest.raises(HTTPException) as error:
        await invitations.create_invitation(
            _Pool(conn), org_id=str(conn.org), actor=actor,
            email="invitee@example.test", role="staff",
            expected_invitation_id=uuid.uuid4(),
        )
    assert error.value.status_code == 404
    assert not any("INSERT INTO team_invitations" in sql for sql, _ in conn.statements)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_public_url_never_creates_unusable_invitation(monkeypatch):
    conn = _Connection()
    actor = {"user_id": str(uuid.uuid4()), "auth_user_id": str(uuid.uuid4())}
    limiter = AsyncMock()
    limiter.hit.return_value = False
    monkeypatch.setattr(invitations, "get_rate_limiter", AsyncMock(return_value=limiter))
    monkeypatch.setattr(invitations, "delivery_available", lambda: True)
    def unavailable_url():
        raise HTTPException(500, "PUBLIC_APP_URL non configurato")
    monkeypatch.setattr(invitations.bff, "public_app_url", unavailable_url)
    with pytest.raises(HTTPException):
        await invitations.create_invitation(
            _Pool(conn), org_id=str(conn.org), actor=actor,
            email="invitee@example.test", role="staff",
        )
    assert not conn.statements


@pytest.mark.asyncio
@pytest.mark.parametrize("identity,status,expected", [
    ({"id": "matching", "email": "Invitee@Example.Test", "email_confirmed_at": "2026-09-27"}, 200, "invitee@example.test"),
    ({"id": "matching", "email": "invitee@example.test", "email_confirmed_at": None}, 403, None),
    ({"id": "different", "email": "invitee@example.test", "email_confirmed_at": "2026-09-27"}, 401, None),
])
async def test_accept_uses_current_verified_supabase_identity(monkeypatch, identity, status, expected):
    response = SimpleNamespace(status_code=200, json=lambda: identity)
    client = SimpleNamespace(get=AsyncMock(return_value=response))
    monkeypatch.setattr(invitations, "get_http_client", AsyncMock(return_value=client))
    monkeypatch.setattr(invitations.bff, "_supabase_url", lambda: "https://auth.example.test")
    monkeypatch.setattr(invitations.bff, "_anon_key", lambda: "test-anon-key")
    if expected is None:
        with pytest.raises(HTTPException) as error:
            await invitations.verified_supabase_identity("session", "matching")
        assert error.value.status_code == status
    else:
        assert await invitations.verified_supabase_identity("session", "matching") == expected
    client.get.assert_awaited_once()


def test_dev_link_requires_explicit_local_environment(monkeypatch):
    monkeypatch.setenv("TEAM_INVITE_DEV_LINKS", "true")
    monkeypatch.setenv("APP_ENV", "staging")
    assert not invitations._dev_links_enabled()
    monkeypatch.setenv("APP_ENV", "development")
    assert invitations._dev_links_enabled()
