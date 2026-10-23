"""Scheduler pool regression: a calendar worker needs several connections.

All provider traffic is replaced with an in-memory adapter. The PostgreSQL
test runs only under the disposable PostgreSQL fixture used by CI.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from cryptography.fernet import Fernet
from googleapiclient.errors import HttpError
import httplib2
import pytest

from src.core.calendar.service import GoogleCalendarService
from src.core.db.repositories.booking_repo import BookingRepository


class _Request:
    def __init__(self, call):
        self.call = call

    def execute(self, *, num_retries):
        assert num_retries == 0
        return self.call()


class _GoogleEvents:
    def __init__(self):
        self.rows = {}
        self.calls = []

    def get(self, *, calendarId, eventId):
        def execute():
            self.calls.append("GET")
            if eventId not in self.rows:
                raise HttpError(httplib2.Response({"status": "404"}), b"{}")
            return deepcopy(self.rows[eventId])
        return _Request(execute)

    def insert(self, *, calendarId, body):
        def execute():
            self.calls.append("POST")
            assert body["id"] not in self.rows
            self.rows[body["id"]] = {**deepcopy(body), "etag": '"synthetic-v1"', "status": "confirmed"}
            return deepcopy(self.rows[body["id"]])
        return _Request(execute)


class _Google:
    def __init__(self):
        self._events = _GoogleEvents()

    def events(self):
        return self._events


@pytest.mark.asyncio
async def test_scheduler_calendar_job_requests_four_connection_pool(monkeypatch):
    """The scheduled entry point keeps the larger pool requirement explicit."""
    from src.core import scheduler

    invoked = []

    async def pooled(job, *, pool_max_size):
        invoked.append((job, pool_max_size))

    def run_stub(coro):
        try:
            coro.send(None)
        except StopIteration:
            pass

    monkeypatch.setattr(scheduler, "_con_pool_esimero", pooled)
    monkeypatch.setattr(scheduler, "google_calendar_enabled", lambda: True)
    monkeypatch.setattr(scheduler.asyncio, "run", run_stub)

    scheduler._run_calendar_sync()

    assert invoked == [(scheduler._calendar_sync_job, 4)]


@pytest.mark.asyncio
@pytest.mark.usefixtures("reset_db")
async def test_scheduler_calendar_sync_completes_with_four_connection_pool(
    pg_pool, postgres_container, sample_org, monkeypatch
):
    """The advisory lock, tenant lock, queue SQL and provider cycle all finish."""
    from src.core import scheduler

    org_id = sample_org["id"]
    key = Fernet.generate_key()
    seed_service = GoogleCalendarService(SimpleNamespace(pool=pg_pool), key)
    google = _Google()
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_WORKER_ENABLED", "true")
    monkeypatch.setenv("ENCRYPTION_KEY", key.decode())
    monkeypatch.setenv("DATABASE_URL", postgres_container.get_connection_url().replace("+psycopg2", ""))
    async with pg_pool.acquire() as conn:
        await conn.execute("UPDATE organizations SET timezone='Europe/Rome' WHERE id=$1", org_id)
        await conn.execute(
            """INSERT INTO booking_settings(organization_id,config,capienze_orarie)
               VALUES($1,$2::jsonb,'{"19:00":10}')""",
            org_id,
            json.dumps({"a05": {"enabled": False, "authority": "internal_calendar_sync"}}),
        )
        await conn.execute(
            """INSERT INTO google_calendar_credentials(organization_id,access_token,refresh_token,
                   token_expiry,connection_status,sync_enabled,granted_scopes,calendar_id)
               VALUES($1,$2,$3,$4,'connected',TRUE,$5::jsonb,'stage@example.test')""",
            org_id,
            seed_service.encrypt_secret("synthetic-access"),
            seed_service.encrypt_secret("synthetic-refresh"),
            datetime.now(timezone.utc) + timedelta(hours=1),
            json.dumps([
                "https://www.googleapis.com/auth/calendar.events",
                "https://www.googleapis.com/auth/calendar.events.freebusy",
            ]),
        )
    booking_repo = BookingRepository(pg_pool)
    booking = await booking_repo.create_booking(
        org_id,
        "Synthetic Scheduler Guest",
        (date.today() + timedelta(days=5)).isoformat(),
        "19:00",
        2,
        telefono="+393330009998",
    )

    created_pools = []
    real_create_pool = scheduler.asyncpg.create_pool

    async def create_pool_spy(*, dsn, min_size, max_size, command_timeout):
        assert dsn == postgres_container.get_connection_url().replace("+psycopg2", "")
        assert max_size == 4
        pool = await real_create_pool(
            dsn=dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=command_timeout,
        )
        created_pools.append(pool)
        return pool

    monkeypatch.setattr(scheduler.asyncpg, "create_pool", create_pool_spy)

    def service_factory(repo, encryption_key):
        service = GoogleCalendarService(repo, encryption_key)
        service._build_service = AsyncMock(return_value=google)
        return service

    monkeypatch.setattr(scheduler, "google_calendar_enabled", lambda: True)
    monkeypatch.setattr("src.core.calendar.GoogleCalendarService", service_factory)

    await __import__("asyncio").wait_for(
        scheduler._con_pool_esimero(scheduler._calendar_sync_job, pool_max_size=4),
        timeout=10,
    )

    assert len(created_pools) == 1
    assert created_pools[0]._closed
    assert google._events.calls.count("POST") == 1
    assert "GET" in google._events.calls
    async with pg_pool.acquire() as conn:
        receipt = await conn.fetchrow(
            """SELECT sync_status,external_booking_id FROM external_booking_sync
               WHERE organization_id=$1 AND internal_booking_id=$2 AND provider='google'""",
            org_id,
            booking["id"],
        )
    assert receipt["sync_status"] == "succeeded"
    assert receipt["external_booking_id"] == next(iter(google._events.rows))
