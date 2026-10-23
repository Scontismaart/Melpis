"""Provider transport is simulated; never contact real Google accounts."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from src.core.calendar.connection import SCOPES as CALENDAR_SCOPES
from src.core.calendar.service import GoogleCalendarService
from src.core.reviews.google_service import GoogleBusinessService


@pytest.fixture(params=[GoogleCalendarService, GoogleBusinessService])
def credentials_env(request, monkeypatch):
    is_calendar = request.param is GoogleCalendarService
    org_a = UUID("11111111-1111-1111-1111-111111111111") if is_calendar else "org-a"
    org_b = UUID("22222222-2222-2222-2222-222222222222") if is_calendar else "org-b"
    unowned_org = UUID("99999999-9999-9999-9999-999999999999") if is_calendar else "unowned-org"
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_BUSINESS_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "synthetic-secret")
    conn = MagicMock()
    conn.fetchrow = AsyncMock()
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    service = request.param(SimpleNamespace(pool=pool), Fernet.generate_key())
    service._get_client_config = lambda: {
        "client_id": "synthetic-client", "client_secret": "synthetic-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    rows = {}
    for org in (org_a, org_b):
        rows[org] = {
            "access_token": service.encrypt_secret(f"access-{org}"),
            "refresh_token": service.encrypt_secret(f"refresh-{org}"),
            "token_expiry": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        if is_calendar:
            rows[org].update({
                "organization_id": org,
                "connection_status": "connected",
                "sync_enabled": True,
                "connection_epoch": UUID("33333333-3333-3333-3333-333333333333"),
                "credential_version": 2,
                "granted_scopes": list(CALENDAR_SCOPES),
            })

    async def fetch(query, org):
        assert "organization_id" in query and "$1" in query
        return rows.get(org)

    conn.fetchrow.side_effect = fetch
    if is_calendar:
        conn.fetchval = AsyncMock(return_value=True)
    return service, conn, org_a, org_b, unowned_org


@pytest.mark.asyncio
async def test_refresh_is_encrypted_and_updates_only_requested_tenant(credentials_env, monkeypatch):
    service, conn, org_a, org_b, unowned_org = credentials_env

    def refresh(creds, transport):
        assert creds.refresh_token == f"refresh-{org_a}"
        creds.token = f"refreshed-{org_a}"
        creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    credentials = await service._get_credentials(org_a)
    assert credentials.token == f"refreshed-{org_a}"
    if isinstance(service, GoogleCalendarService):
        query, org, epoch, version, encrypted, _expiry, encrypted_refresh = conn.fetchval.await_args.args
        assert "WHERE organization_id=$1 AND connection_epoch=$2 AND credential_version=$3" in query
        assert org == org_a and epoch == UUID("33333333-3333-3333-3333-333333333333") and version == 2
        assert encrypted != f"refreshed-{org_a}"
        assert service.fernet.decrypt(encrypted.encode()).decode() == f"refreshed-{org_a}"
        assert encrypted_refresh != f"refresh-{org_a}"
        assert service.fernet.decrypt(encrypted_refresh.encode()).decode() == f"refresh-{org_a}"
        assert await service._get_credentials(unowned_org) is None
        assert conn.fetchval.await_count == 1
    else:
        query, org, encrypted, _expiry = conn.execute.await_args.args
        assert "WHERE organization_id = $1" in query
        assert org == org_a
        assert encrypted != f"refreshed-{org_a}"
        assert service._decrypt(encrypted) == f"refreshed-{org_a}"
        assert await service._get_credentials(unowned_org) is None
        assert conn.execute.await_count == 1


@pytest.mark.asyncio
async def test_revoked_refresh_fails_closed_without_secret_logs(credentials_env, monkeypatch, caplog):
    service, conn, org_a, _org_b, _unowned = credentials_env

    def refresh(creds, transport):
        if isinstance(service, GoogleCalendarService):
            raise RefreshError({"error": "invalid_grant", "refresh_token": "synthetic-sensitive-token"})
        raise RefreshError("synthetic-sensitive-token")

    monkeypatch.setattr(Credentials, "refresh", refresh)
    assert await service._get_credentials(org_a) is None
    assert "synthetic-sensitive-token" not in caplog.text
    if isinstance(service, GoogleCalendarService):
        query, org, epoch = next(
            call.args for call in conn.execute.await_args_list
            if "connection_status='revoked'" in call.args[0]
        )
        assert "WHERE organization_id=$1 AND connection_epoch=$2" in query
        assert org == org_a
        assert epoch == UUID("33333333-3333-3333-3333-333333333333")
    else:
        query, org = conn.execute.await_args.args
        assert "WHERE organization_id = $1" in query
        assert org == org_a


@pytest.mark.asyncio
async def test_cross_tenant_ciphertext_key_is_not_accepted(credentials_env):
    service, conn, org_a, _org_b, _unowned = credentials_env
    from cryptography.fernet import InvalidToken

    conn.fetchrow.side_effect = None
    conn.fetchrow.return_value = {
        "access_token": Fernet(Fernet.generate_key()).encrypt(b"other-key-token").decode(),
        "refresh_token": service.encrypt_secret("synthetic-refresh"),
        "token_expiry": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    if isinstance(service, GoogleCalendarService):
        conn.fetchrow.side_effect = None
        conn.fetchrow.return_value.update({
            "organization_id": org_a,
            "connection_status": "connected",
            "sync_enabled": True,
            "connection_epoch": UUID("33333333-3333-3333-3333-333333333333"),
            "credential_version": 2,
            "granted_scopes": list(CALENDAR_SCOPES),
        })
        assert await service._get_credentials(org_a) is None
    else:
        with pytest.raises(InvalidToken):
            await service._get_credentials(org_a)
    if isinstance(service, GoogleCalendarService):
        assert not any("connection_status='revoked'" in call.args[0]
                       or "SET access_token=" in call.args[0]
                       for call in conn.execute.await_args_list)
    else:
        conn.execute.assert_not_awaited()
