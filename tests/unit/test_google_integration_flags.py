"""Runtime Google gates must prevent external activity while disabled."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from src.core.calendar import routes as calendar_routes
from src.core.calendar.service import GoogleCalendarService
from src.core.reviews import google_routes as business_routes
from src.core.reviews.google_service import GoogleBusinessService


FERNET_KEY = "GT4pFJ9wm5vlxRS2MSmSF3tjbThnKnon-sgG5TVYILE="


def _set_disabled(monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return None


class _ConfiguredPool:
    """Contains valid, enabled credentials if a disabled service touches storage."""

    def __init__(self):
        self.row = None
        self.acquire_calls = 0
        self.fetch = AsyncMock(return_value=[{"id": "org-1"}])
        self.execute = AsyncMock()
        self.read_queries = []

    def acquire(self):
        self.acquire_calls += 1
        async def fetchrow(query, *_args):
            self.read_queries.append(query)
            if "external_booking_sync" in query:
                return {"pending_count": 0, "failed_count": 0, "uncertain_count": 0}
            return self.row
        async def fetchval(query, *_args):
            if "SELECT timezone" in query:
                return "UTC"
            return None
        connection = SimpleNamespace(fetchrow=AsyncMock(side_effect=fetchrow),
                                     fetchval=AsyncMock(side_effect=fetchval), execute=AsyncMock())
        return _Acquire(connection)


def _request(pool):
    return SimpleNamespace(
        query_params={
            "state": "11111111-1111-1111-1111-111111111111:" + "a" * 32,
            "code": "unused-code",
        },
        app=SimpleNamespace(state=SimpleNamespace(pool=pool, repo=MagicMock())),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("flag_value", [None, "false"])
async def test_calendar_routes_block_oauth_status_and_activation(
    monkeypatch, flag_value
):
    _set_disabled(monkeypatch, "GOOGLE_CALENDAR_ENABLED", flag_value)
    pool = _ConfiguredPool()
    request = _request(pool)
    user = {"organization_id": "11111111-1111-1111-1111-111111111111"}
    make_flow = MagicMock(side_effect=AssertionError("OAuth must not start"))
    monkeypatch.setattr(calendar_routes, "_make_flow", make_flow)

    for operation in (
        lambda: calendar_routes.calendar_auth(request, user=user, mfa={}),
        lambda: calendar_routes.calendar_settings(
            calendar_routes.CalendarSettingsInput(mode="google_authoritative"),
            request,
            user=user,
            mfa={},
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await operation()
        assert exc.value.status_code == 503

    # Read-only GET remains available while the feature is off so the UI can
    # report a disabled integration state without attempting provider traffic.
    status = await calendar_routes.calendar_status(request, user=user)
    assert status["enabled"] is False
    assert status["integration_status"] == "disabled"

    callback = await calendar_routes.calendar_oauth2callback(request)
    assert callback.headers["location"].endswith("calendar=error&reason=disabled")
    make_flow.assert_not_called()
    assert pool.acquire_calls == 1
    assert len(pool.read_queries) == 3
    assert pool.execute.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("flag_value", [None, "false"])
async def test_business_routes_block_oauth_status_sync_and_activation(
    monkeypatch, flag_value
):
    _set_disabled(monkeypatch, "GOOGLE_BUSINESS_ENABLED", flag_value)
    pool = _ConfiguredPool()
    request = _request(pool)
    user = {"organization_id": "org-1"}
    make_flow = MagicMock(side_effect=AssertionError("OAuth must not start"))
    monkeypatch.setattr(business_routes, "_make_flow", make_flow)

    for operation in (
        business_routes.google_reviews_auth(request, user=user, mfa={}),
        business_routes.google_reviews_status(request, user=user),
        business_routes.google_reviews_sync(request, user=user),
        business_routes.google_reviews_settings(
            business_routes.GoogleReviewsSettingsInput(
                account_name="accounts/valid",
                location_name="locations/valid",
            ),
            request,
            user=user,
            mfa={},
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await operation
        assert exc.value.status_code == 503

    callback = await business_routes.google_reviews_oauth2callback(request)
    assert callback.headers["location"].endswith("reviews_google=error&reason=disabled")
    make_flow.assert_not_called()
    assert pool.acquire_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("flag_value", [None, "false"])
async def test_calendar_service_blocks_refresh_and_all_google_operations(
    monkeypatch, flag_value
):
    _set_disabled(monkeypatch, "GOOGLE_CALENDAR_ENABLED", flag_value)
    pool = _ConfiguredPool()
    service = GoogleCalendarService(
        repo=SimpleNamespace(pool=pool), encryption_key=FERNET_KEY
    )
    pool.row = {
        "access_token": service.encrypt_secret("valid-access-token"),
        "refresh_token": service.encrypt_secret("valid-refresh-token"),
        "token_expiry": datetime.now(timezone.utc) - timedelta(minutes=5),
        "sync_enabled": True,
        "calendar_id": "primary",
    }
    build_service = AsyncMock(side_effect=AssertionError("Google API must not build"))
    monkeypatch.setattr(service, "_build_service", build_service)

    assert await service._get_credentials("org-1") is None
    booking = {"id": UUID("22222222-2222-2222-2222-222222222222"), "organization_id": UUID("11111111-1111-1111-1111-111111111111")}
    assert await service.create_event(booking, booking["organization_id"]) is None
    assert await service.update_event(booking, booking["organization_id"]) is None
    assert await service.delete_event(booking, booking["organization_id"]) is None
    assert (await service.get_busy_intervals(booking["organization_id"], "2026-09-15")).status.value == "disabled_or_unconfigured"

    create_event = AsyncMock(side_effect=AssertionError("event must not sync"))
    monkeypatch.setattr(service, "create_event", create_event)
    await service.sync_booking_state(booking, booking["organization_id"])

    build_service.assert_not_awaited()
    create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("flag_value", [None, "false"])
async def test_business_service_blocks_refresh_discovery_and_review_api(
    monkeypatch, flag_value
):
    _set_disabled(monkeypatch, "GOOGLE_BUSINESS_ENABLED", flag_value)
    pool = _ConfiguredPool()
    repo = SimpleNamespace(
        pool=pool,
        get_onboarding_profile=AsyncMock(return_value={}),
        create_review=AsyncMock(),
    )
    service = GoogleBusinessService(repo=repo, encryption_key=FERNET_KEY)
    pool.row = {
        "access_token": service.encrypt_secret("valid-access-token"),
        "refresh_token": service.encrypt_secret("valid-refresh-token"),
        "token_expiry": datetime.now(timezone.utc) - timedelta(minutes=5),
        "account_name": "accounts/valid",
        "location_name": "locations/valid",
        "sync_enabled": True,
    }
    build_service = AsyncMock(side_effect=AssertionError("discovery must not run"))
    list_reviews = AsyncMock(side_effect=AssertionError("review API must not run"))
    monkeypatch.setattr(service, "_build_service", build_service)
    monkeypatch.setattr(service, "_list_reviews", list_reviews)

    assert await service._get_credentials("org-1") is None
    assert await service.fetch_reviews("org-1") == {
        "nuove": 0,
        "fallimenti": 0,
        "parziale": False,
    }

    assert pool.acquire_calls == 0
    build_service.assert_not_awaited()
    list_reviews.assert_not_awaited()
    repo.get_onboarding_profile.assert_not_awaited()
    repo.create_review.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("flag_value", [None, "false"])
async def test_business_low_level_review_call_is_also_gated(monkeypatch, flag_value):
    _set_disabled(monkeypatch, "GOOGLE_BUSINESS_ENABLED", flag_value)
    service = GoogleBusinessService(repo=MagicMock(), encryption_key=FERNET_KEY)
    google_api = MagicMock()

    assert (
        await service._list_reviews(google_api, "accounts/valid", "locations/valid")
        == []
    )
    google_api.accounts.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("flag_value", [None, "false"])
async def test_calendar_scheduler_stops_before_pool_or_tenant_scan(
    monkeypatch, flag_value
):
    from src.core import scheduler

    _set_disabled(monkeypatch, "GOOGLE_CALENDAR_ENABLED", flag_value)
    pooled_job = MagicMock(side_effect=AssertionError("pool must not open"))
    monkeypatch.setattr(scheduler, "_con_pool_esimero", pooled_job)

    scheduler._run_calendar_sync()
    pooled_job.assert_not_called()

    pool = _ConfiguredPool()
    await scheduler._calendar_sync_job(pool)
    pool.fetch.assert_not_awaited()
    pool.execute.assert_not_awaited()


def test_make_flow_rejects_inconsistent_calendar_redirect_uri(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://melpis.it/api/calendar/callback")

    with pytest.raises(
        ValueError,
        match="atteso percorso '/api/calendar/oauth2callback'",
    ):
        calendar_routes._make_flow()


def test_make_flow_accepts_staging_localhost_oauth_callback(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:4187/api/calendar/oauth2callback")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id.test")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret.test")
    from_client_config = MagicMock(return_value=object())
    monkeypatch.setattr(calendar_routes.Flow, "from_client_config", from_client_config)
    monkeypatch.setattr(calendar_routes, "google_calendar_enabled", lambda: True)

    calendar_routes._make_flow()

    assert from_client_config.call_args.kwargs["redirect_uri"] == (
        "http://localhost:4187/api/calendar/oauth2callback"
    )


def test_make_flow_rejects_loopback_http_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:4187/api/calendar/oauth2callback")
    monkeypatch.setattr(calendar_routes, "google_calendar_enabled", lambda: True)

    with pytest.raises(ValueError, match="HTTP esclusivamente su localhost:4187 fuori da Production"):
        calendar_routes._make_flow()


def test_make_flow_rejects_loopback_http_without_explicit_environment(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:4187/api/calendar/oauth2callback")
    monkeypatch.setattr(calendar_routes, "google_calendar_enabled", lambda: True)

    with pytest.raises(ValueError, match="HTTP esclusivamente su localhost:4187 fuori da Production"):
        calendar_routes._make_flow()


def test_make_flow_rejects_conflicting_environment_values(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:4187/api/calendar/oauth2callback")
    monkeypatch.setattr(calendar_routes, "google_calendar_enabled", lambda: True)

    with pytest.raises(ValueError, match="HTTP esclusivamente su localhost:4187 fuori da Production"):
        calendar_routes._make_flow()
