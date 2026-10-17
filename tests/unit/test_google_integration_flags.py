"""Runtime Google gates must prevent external activity while disabled."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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

    def acquire(self):
        self.acquire_calls += 1
        connection = SimpleNamespace(
            fetchrow=AsyncMock(return_value=self.row),
            execute=AsyncMock(),
        )
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
    user = {"organization_id": "org-1"}
    make_flow = MagicMock(side_effect=AssertionError("OAuth must not start"))
    monkeypatch.setattr(calendar_routes, "_make_flow", make_flow)

    for operation in (
        calendar_routes.calendar_auth(request, user=user, mfa={}),
        calendar_routes.calendar_status(request, user=user),
        calendar_routes.calendar_settings(
            calendar_routes.CalendarSettingsInput(sync_enabled=True),
            request,
            user=user,
            mfa={},
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await operation
        assert exc.value.status_code == 503

    callback = await calendar_routes.calendar_oauth2callback(request)
    assert callback.headers["location"].endswith("calendar=error&reason=disabled")
    make_flow.assert_not_called()
    assert pool.acquire_calls == 0


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
    assert await service.create_event({}, "org-1") is None
    assert await service.update_event({}, "org-1") is None
    assert await service.delete_event({}, "org-1") is None
    assert await service.get_busy_intervals("org-1", "2026-09-15") == []

    create_event = AsyncMock(side_effect=AssertionError("event must not sync"))
    monkeypatch.setattr(service, "create_event", create_event)
    await service.sync_booking_state({"stato": "confermata"}, "org-1")

    assert pool.acquire_calls == 0
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
