"""Calendar credential lifecycle contracts; provider refresh is always mocked."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError

from src.core.calendar import connection as calendar_connection
from src.core.calendar.connection import CalendarConnection, CalendarUnavailable, SCOPES


ORG_A = UUID("11111111-1111-1111-1111-111111111111")
ORG_B = UUID("22222222-2222-2222-2222-222222222222")
EPOCH = UUID("33333333-3333-3333-3333-333333333333")


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _Connection:
    def __init__(self):
        self.execute = AsyncMock()
        self.fetchrow = AsyncMock()
        self.fetchval = AsyncMock()
        self.transaction = MagicMock(side_effect=lambda: _AsyncContext(None))
        self.is_closed = MagicMock(return_value=False)


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


@pytest.fixture
def lifecycle_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "synthetic-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "synthetic-client-secret")
    conn = _Connection()
    fernet = Fernet(Fernet.generate_key())
    lifecycle = CalendarConnection(_Pool(conn), fernet)
    return lifecycle, conn, fernet


def _credential_row(fernet, *, scopes=None):
    return {
        "organization_id": ORG_A,
        "connection_status": "connected",
        "sync_enabled": True,
        "connection_epoch": EPOCH,
        "credential_version": 7,
        "granted_scopes": list(SCOPES) if scopes is None else scopes,
        "access_token": fernet.encrypt(b"synthetic-access-token").decode(),
        "refresh_token": fernet.encrypt(b"synthetic-refresh-token").decode(),
        "token_expiry": datetime.now(timezone.utc) - timedelta(minutes=1),
        "calendar_id": "staging-calendar@example.test",
        "resource_calendar_ids": ["staging-calendar@example.test"],
    }


class _FakeCredentials:
    def __init__(self, *, token, refresh_token, expiry, **_kwargs):
        self.token = token
        self.refresh_token = refresh_token
        self.expiry = expiry
        self.expired = True

    def refresh(self, _request):
        raise AssertionError("test must supply refresh behavior")


def _patch_expired_credentials(monkeypatch, refresh):
    class FakeCredentials(_FakeCredentials):
        def refresh(self, request):
            refresh(self, request)

    monkeypatch.setattr(calendar_connection, "Credentials", FakeCredentials)


@pytest.mark.asyncio
async def test_disconnect_is_tenant_bound_invalidates_epoch_and_nonce_preserving_calendar_identity(
    lifecycle_env,
):
    lifecycle, conn, _fernet = lifecycle_env
    previous = {
        "organization_id": ORG_A,
        "connection_epoch": EPOCH,
        "oauth_generation": UUID("44444444-4444-4444-4444-444444444444"),
        "calendar_id": "staging-calendar@example.test",
    }
    conn.fetchrow.return_value = previous

    returned = await lifecycle.disconnect(ORG_A)

    assert returned == previous
    calls = conn.execute.await_args_list
    lock_call = next(call for call in calls if "pg_advisory_lock" in call.args[0])
    unlock_call = next(call for call in calls if "pg_advisory_unlock" in call.args[0])
    assert lock_call.args[1] == f"calendar-credentials:{ORG_A}"
    assert unlock_call.args[1] == f"calendar-credentials:{ORG_A}"

    credential_update = next(call for call in calls if "UPDATE google_calendar_credentials SET access_token=NULL" in call.args[0])
    credential_sql, credential_org, next_epoch, next_generation = credential_update.args
    assert "WHERE organization_id=$1" in credential_sql
    assert credential_org == ORG_A
    assert next_epoch != EPOCH
    assert next_generation != previous["oauth_generation"]
    assert credential_sql.count("$1") >= 1
    assert str(ORG_A) not in credential_sql
    assert str(ORG_B) not in credential_sql

    nonce_delete = next(call for call in calls if "DELETE FROM oauth_nonces" in call.args[0])
    assert "organization_id=$1 AND purpose='calendar'" in nonce_delete.args[0]
    assert nonce_delete.args[1] == ORG_A
    assert "DELETE FROM external_booking_sync" not in "\n".join(call.args[0] for call in calls)
    sync_update = next(call for call in calls if "UPDATE external_booking_sync SET" in call.args[0])
    assert "WHERE organization_id=$1" in sync_update.args[0]
    assert sync_update.args[1] == ORG_A
    # Disconnect blocks/marks queue work and fences workers; it must not erase
    # the calendar/event identity needed for approved reconciliation or cleanup.
    assert "calendar_id=" not in sync_update.args[0]
    assert "google_event_id" not in sync_update.args[0]
    assert "event_id" not in sync_update.args[0]


@pytest.mark.asyncio
async def test_retryable_refresh_error_keeps_connection_recoverable_and_sanitizes_details(
    lifecycle_env, monkeypatch,
):
    lifecycle, conn, fernet = lifecycle_env
    conn.fetchrow.return_value = _credential_row(fernet)
    sensitive_body = {"error": "temporarily_unavailable", "refresh_token": "DO_NOT_LEAK"}

    def refresh(_creds, _request):
        error = RefreshError(sensitive_body)
        error.retryable = True
        raise error

    _patch_expired_credentials(monkeypatch, refresh)

    with pytest.raises(CalendarUnavailable) as caught:
        await lifecycle.credentials(ORG_A)

    assert caught.value.code == "refresh_unavailable"
    assert "DO_NOT_LEAK" not in str(caught.value)
    assert "temporarily_unavailable" not in str(caught.value)
    assert conn.fetchval.await_count == 0
    assert not any("connection_status='revoked'" in call.args[0] for call in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_nonretryable_invalid_grant_marks_only_current_tenant_epoch_revoked(
    lifecycle_env, monkeypatch,
):
    lifecycle, conn, fernet = lifecycle_env
    conn.fetchrow.return_value = _credential_row(fernet)

    def refresh(_creds, _request):
        raise RefreshError({"error": "invalid_grant", "refresh_token": "SENSITIVE_VALUE"})

    _patch_expired_credentials(monkeypatch, refresh)

    with pytest.raises(CalendarUnavailable) as caught:
        await lifecycle.credentials(ORG_A)

    assert caught.value.code == "authorization_revoked"
    revoke = next(call for call in conn.execute.await_args_list if "connection_status='revoked'" in call.args[0])
    assert "WHERE organization_id=$1 AND connection_epoch=$2" in revoke.args[0]
    assert revoke.args[1:] == (ORG_A, EPOCH)
    assert "SENSITIVE_VALUE" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("refresh response contains secret=NEVER_LEAK"),
        RefreshError({"error": "server_error", "access_token": "NEVER_LEAK"}),
    ],
)
async def test_unclassified_refresh_exceptions_are_sanitized_and_do_not_revoke(
    lifecycle_env, monkeypatch, failure,
):
    lifecycle, conn, fernet = lifecycle_env
    conn.fetchrow.return_value = _credential_row(fernet)

    def refresh(_creds, _request):
        raise failure

    _patch_expired_credentials(monkeypatch, refresh)

    with pytest.raises(CalendarUnavailable) as caught:
        await lifecycle.credentials(ORG_A)

    assert caught.value.code == "refresh_unavailable"
    assert "NEVER_LEAK" not in str(caught.value)
    assert "server_error" not in str(caught.value)
    assert "connection_status='revoked'" not in "\n".join(call.args[0] for call in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_missing_freebusy_scope_requires_reconnect_before_refresh(lifecycle_env, monkeypatch):
    lifecycle, conn, fernet = lifecycle_env
    conn.fetchrow.return_value = _credential_row(fernet, scopes=[SCOPES[0]])
    refresh_called = []
    _patch_expired_credentials(monkeypatch, lambda *_args: refresh_called.append(True))

    with pytest.raises(CalendarUnavailable) as caught:
        await lifecycle.credentials(ORG_A)

    assert caught.value.code == "reconnect_required"
    assert refresh_called == []
    assert conn.fetchval.await_count == 0
    assert conn.execute.await_count == 2  # advisory lock and unlock only


@pytest.mark.asyncio
async def test_refresh_compare_and_swap_rejects_stale_epoch_or_credential_version(
    lifecycle_env, monkeypatch,
):
    lifecycle, conn, fernet = lifecycle_env
    conn.fetchrow.return_value = _credential_row(fernet)
    conn.fetchval.return_value = False

    def refresh(creds, _request):
        creds.token = "synthetic-refreshed-token"
        creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    _patch_expired_credentials(monkeypatch, refresh)

    with pytest.raises(CalendarUnavailable) as caught:
        await lifecycle.credentials(ORG_A)

    assert caught.value.code == "connection_changed"
    cas = conn.fetchval.await_args
    sql, bound_org, bound_epoch, bound_version, encrypted_token, _expiry, encrypted_refresh = cas.args
    assert "WHERE organization_id=$1 AND connection_epoch=$2 AND credential_version=$3" in sql
    assert "connection_status='connected' AND sync_enabled RETURNING TRUE" in sql
    assert bound_org == ORG_A
    assert bound_epoch == EPOCH
    assert bound_version == 7
    assert encrypted_token != "synthetic-refreshed-token"
    assert fernet.decrypt(encrypted_token.encode()).decode() == "synthetic-refreshed-token"
    assert encrypted_refresh != "synthetic-refresh"
    assert str(ORG_B) not in sql
