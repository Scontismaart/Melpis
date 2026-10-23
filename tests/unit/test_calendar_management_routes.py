"""Calendar management route contracts using only synthetic storage/provider state."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.calendar import routes
from src.core.calendar.connection import SCOPES


ORG = "11111111-1111-4111-8111-111111111111"


def _credential_row(**overrides):
    row = {
        "organization_id": UUID(ORG),
        "connection_status": "connected",
        "sync_enabled": True,
        "granted_scopes": list(SCOPES),
        "access_token": "encrypted-access-placeholder",
        "refresh_token": "encrypted-refresh-placeholder",
        "calendar_id": "primary",
        "resource_calendar_ids": [],
        "last_error_code": None,
        "last_verified_at": "2026-10-22T12:00:00Z",
        "last_sync_at": None,
        "connection_epoch": UUID("22222222-2222-4222-8222-222222222222"),
    }
    row.update(overrides)
    return row


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class _Pool:
    """Tiny asyncpg-shaped fake that records writes and returns org-scoped rows."""

    def __init__(self, credentials=None, config=None):
        self.credentials = credentials
        self.config = config or {"a05": {"enabled": False, "authority": "internal"}}
        self.write_queries = []
        self.read_queries = []
        self.acquire_count = 0

    def acquire(self):
        self.acquire_count += 1

        async def fetchrow(query, *_args):
            self.read_queries.append(query)
            if "google_calendar_credentials" in query:
                return self.credentials
            if "booking_settings" in query:
                return {"config": self.config, "capienze_orarie": {}}
            if "external_booking_sync" in query:
                return {"pending_count": 0, "failed_count": 0, "uncertain_count": 0}
            if "FROM organizations" in query:
                return {"timezone": "Europe/Rome", "business_profile": {}}
            return None

        async def fetchval(query, *_args):
            self.read_queries.append(query)
            if "external_booking_sync" in query:
                return False
            return None

        async def execute(query, *args):
            self.write_queries.append((query, args))
            if "INSERT INTO booking_settings" in query:
                self.config = json.loads(args[1])
            return "OK"

        conn = SimpleNamespace(
            fetchrow=fetchrow,
            fetchval=fetchval,
            execute=execute,
            transaction=lambda: _Transaction(),
            is_closed=lambda: False,
        )
        return _Acquire(conn)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _test_app(pool, service=None):
    app = FastAPI()
    app.state.pool = pool
    app.state.calendar_service = service
    app.include_router(routes.router)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flag", "connection_status", "expected_status", "expected_mode"),
    [
        ("false", "connected", "disabled", "google_authoritative"),
        ("true", "revoked", "revoked", "google_authoritative"),
        ("true", "reconnect_required", "reconnect_required", "google_authoritative"),
    ],
)
async def test_get_status_is_read_only_reports_flag_and_preserves_stored_authority(
    monkeypatch, flag, connection_status, expected_status, expected_mode
):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", flag)
    pool = _Pool(
        _credential_row(connection_status=connection_status),
        {"a05": {"enabled": False, "authority": expected_mode}},
    )

    payload = await routes.calendar_status(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pool))),
        {"organization_id": ORG},
    )

    assert payload["enabled"] is (flag == "true")
    assert payload["integration_status"] == expected_status
    assert payload["mode"] == expected_mode
    assert pool.write_queries == []
    assert len(pool.read_queries) == 3


@pytest.mark.asyncio
async def test_get_status_redacts_provider_failure_and_missing_scope_is_not_connected(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    row = _credential_row(
        last_error_code="provider response contained private-token-value",
        granted_scopes=[SCOPES[0]],
    )
    pool = _Pool(row)
    payload = await routes.calendar_status(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pool))),
        {"organization_id": ORG},
    )
    assert payload["connected"] is False
    assert payload["integration_status"] == "sync_failed"
    assert payload["error_code"] == "provider_unavailable"
    assert "private-token-value" not in repr(payload)
    assert pool.write_queries == []


def test_status_role_and_manual_settings_mfa_gates_use_real_dependencies(install_test_identity, monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "false")
    pool = _Pool(_credential_row())
    service = SimpleNamespace(
        connection=SimpleNamespace(row=AsyncMock(return_value=_credential_row())),
        availability=AsyncMock(side_effect=AssertionError("disabled feature reached provider")),
        repo=SimpleNamespace(pool=pool),
    )
    app = _test_app(pool, service)
    install_test_identity(app, "manager-session", default_org_id=ORG, ruolo="manager")

    with TestClient(app) as client:
        assert client.get("/api/calendar/status").status_code == 401
        headers = {"Authorization": "Bearer manager-session"}
        status = client.get("/api/calendar/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["integration_status"] == "disabled"
        assert client.patch("/api/calendar/settings", headers=headers, json={"mode": "internal"}).status_code == 403
        assert client.post("/api/calendar/check", headers=headers).status_code == 403
    service.availability.assert_not_awaited()
    assert pool.write_queries == []


def test_owner_mfa_is_required_before_disabled_manual_check_and_owner_gets_safe_status(
    install_test_identity, monkeypatch
):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "false")
    pool = _Pool(_credential_row())
    service = SimpleNamespace(
        connection=SimpleNamespace(row=AsyncMock(return_value=_credential_row())),
        availability=AsyncMock(side_effect=AssertionError("disabled feature reached provider")),
        repo=SimpleNamespace(pool=pool),
    )
    app = _test_app(pool, service)
    install_test_identity(app, "owner-session", default_org_id=ORG)

    with TestClient(app) as client:
        base = {"Authorization": "Bearer owner-session"}
        low_aal = {"Authorization": "Bearer owner-session-aal1"}
        assert client.get("/api/calendar/status", headers=low_aal).status_code == 200
        rejected = client.post("/api/calendar/check", headers=low_aal)
        assert rejected.status_code == 403
        assert rejected.headers["x-mfa-required"] == "true"
        assert client.post("/api/calendar/check", headers=base).status_code == 503
        assert client.patch("/api/calendar/settings", headers=low_aal, json={"mode": "internal"}).status_code == 403
        saved = client.patch("/api/calendar/settings", headers=base, json={"mode": "internal"})
        assert saved.status_code == 200, saved.text
        assert saved.json()["mode"] == "internal"
    service.availability.assert_not_awaited()
    assert pool.config["a05"]["enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        {"calendar_id": "bad/calendar"},
        {"resource_calendar_ids": ["valid@example.com", "bad calendar"]},
    ],
)
async def test_invalid_calendar_ids_are_rejected_before_provider_or_transaction(monkeypatch, settings):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    pool = _Pool(_credential_row())
    service = SimpleNamespace(
        connection=SimpleNamespace(row=AsyncMock(return_value=_credential_row())),
        availability=AsyncMock(side_effect=AssertionError("invalid resource reached provider")),
        repo=SimpleNamespace(pool=pool),
    )
    app = _test_app(pool, service)
    from src.core.calendar.management import save_calendar_settings

    with pytest.raises(Exception) as exc:
        await save_calendar_settings(service, ORG, routes.CalendarSettingsInput(**settings))
    assert getattr(exc.value, "status_code", None) == 400
    service.availability.assert_not_awaited()
    assert pool.write_queries == []


@pytest.mark.asyncio
async def test_unknown_availability_probe_blocks_authority_change_without_database_writes(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    pool = _Pool(_credential_row())
    service = SimpleNamespace(
        connection=SimpleNamespace(row=AsyncMock(return_value=_credential_row())),
        availability=AsyncMock(return_value=SimpleNamespace(verified=False, free=False)),
        repo=SimpleNamespace(pool=pool),
    )
    from src.core.calendar.management import save_calendar_settings

    with pytest.raises(Exception) as exc:
        await save_calendar_settings(
            service, ORG, routes.CalendarSettingsInput(mode="google_authoritative")
        )
    assert getattr(exc.value, "status_code", None) == 409
    service.availability.assert_awaited_once()
    assert pool.write_queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, expected", [
    ("internal_calendar_sync", "sync_failed"),
    ("google_authoritative", "authoritative_unavailable"),
])
async def test_manual_check_cannot_report_old_success_when_current_probe_is_unknown(monkeypatch, mode, expected):
    from src.core.calendar.availability import AvailabilityStatus, CalendarAvailability

    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    pool = _Pool(_credential_row(), {"a05": {"enabled": False, "authority": mode}})
    service = SimpleNamespace(availability=AsyncMock(return_value=CalendarAvailability(
        AvailabilityStatus.UNKNOWN, reason_code="provider_unavailable")))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pool, calendar_service=service)))
    payload = await routes.calendar_check(request, {"organization_id": ORG})
    assert payload["integration_status"] == expected
    assert payload["error_code"] == "provider_unavailable"
    assert payload["mode"] == mode


@pytest.mark.asyncio
async def test_authority_mode_change_does_not_auto_enable_a05(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    pool = _Pool(config={"a05": {"enabled": False, "authority": "google_authoritative"}})
    service = SimpleNamespace(
        connection=SimpleNamespace(row=AsyncMock(return_value=None)),
        availability=AsyncMock(),
        repo=SimpleNamespace(pool=pool),
    )
    from src.core.calendar.management import save_calendar_settings

    await save_calendar_settings(service, ORG, routes.CalendarSettingsInput(mode="internal"))

    assert pool.config["a05"] == {"enabled": False, "authority": "internal"}
    assert any("INSERT INTO booking_settings" in query for query, _args in pool.write_queries)
