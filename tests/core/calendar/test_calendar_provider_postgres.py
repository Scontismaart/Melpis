"""Real PostgreSQL state machine with a deterministic Google HTTP adapter.

No Google SDK request is executed: only the transport boundary is substituted.
"""
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import httplib2
import pytest
import asyncpg
from cryptography.fernet import Fernet
from googleapiclient.errors import HttpError

from src.core.bookings.service import BookingService
from src.core.calendar.service import GoogleCalendarService
from src.core.db.repositories.booking_repo import BookingRepository
from src.core.db.repositories.message_repo import MessageRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


def _http(status):
    return HttpError(httplib2.Response({"status": str(status)}), b'{"error":{"errors":[{"reason":"authError"}]}}')


class Request:
    def __init__(self, method, call):
        self.method, self.call, self.headers = method, call, {}

    def execute(self, **kwargs):
        assert kwargs == {"num_retries": 0}
        return self.call(self)


class Google:
    def __init__(self):
        self.rows, self.calls = {}, []
        self.timeout = False
        self.read_error = None
        self.availability = "free"

    def events(self):
        return self

    def freebusy(self):
        return self

    def get(self, *, calendarId, eventId):
        def call(_):
            self.calls.append("GET")
            if self.read_error:
                raise self.read_error
            if eventId not in self.rows:
                raise _http(404)
            return deepcopy(self.rows[eventId])
        return Request("GET", call)

    def insert(self, *, calendarId, body):
        def call(_):
            self.calls.append("POST")
            assert body["id"] not in self.rows
            self.rows[body["id"]] = {**deepcopy(body), "etag": "v1", "status": "confirmed"}
            if self.timeout:
                raise TimeoutError("synthetic lost response")
            return deepcopy(self.rows[body["id"]])
        return Request("POST", call)

    def update(self, *, calendarId, eventId, body):
        def call(request):
            self.calls.append("PUT")
            assert request.headers["If-Match"] == self.rows[eventId]["etag"]
            self.rows[eventId] = {**deepcopy(body), "etag": "v2", "status": "confirmed"}
            return deepcopy(self.rows[eventId])
        return Request("PUT", call)

    def delete(self, *, calendarId, eventId):
        def call(request):
            self.calls.append("DELETE")
            assert request.headers["If-Match"] == self.rows[eventId]["etag"]
            self.rows[eventId]["status"] = "cancelled"
            return {}
        return Request("DELETE", call)

    def query(self, *, body):
        def call(_):
            self.calls.append("FREEBUSY")
            if self.availability == "unknown":
                return {"calendars": {}}
            busy = [{"start": body["timeMin"], "end": body["timeMax"]}] if self.availability == "busy" else []
            return {"kind": "calendar#freeBusy", "timeMin": body["timeMin"], "timeMax": body["timeMax"],
                    "calendars": {item["id"]: {"busy": busy} for item in body["items"]}}
        return Request("FREEBUSY", call)

    def list(self, **kwargs):
        return Request("LIST", lambda _: {"kind": "calendar#events", "timeZone": "Europe/Rome",
                                          "items": deepcopy(list(self.rows.values()))})


async def setup(pool, org, monkeypatch, mode="internal_calendar_sync", *, create_booking=True):
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_WORKER_ENABLED", "true")
    key = Fernet.generate_key()
    service = GoogleCalendarService(SimpleNamespace(pool=pool), key)
    google = Google()
    service._build_service = AsyncMock(return_value=google)
    async with pool.acquire() as conn:
        await conn.execute("""UPDATE organizations SET timezone='Europe/Rome' WHERE id=$1""", org)
        await conn.execute("""INSERT INTO booking_settings(organization_id,config,capienze_orarie)
            VALUES($1,$2::jsonb,'{"19:00":10,"20:00":10}')""", org,
            json.dumps({"a05": {"enabled": False, "authority": mode}}))
        await conn.execute("""INSERT INTO google_calendar_credentials(organization_id,access_token,refresh_token,
            token_expiry,connection_status,sync_enabled,granted_scopes,calendar_id,last_verified_at)
            VALUES($1,$2,$3,$4,'connected',TRUE,$5::jsonb,'stage@example.test',clock_timestamp())""", org,
            service.encrypt_secret("synthetic-access"), service.encrypt_secret("synthetic-refresh"),
            datetime.now(timezone.utc) + timedelta(hours=1), json.dumps([
                "https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.events.freebusy"]))
    booking_repo = BookingRepository(pool)
    if not create_booking:
        return service, google, booking_repo, None
    local_today = datetime.now(ZoneInfo("Europe/Rome")).date()
    booking = await booking_repo.create_booking(org, "Synthetic Guest", (local_today + timedelta(days=5)).isoformat(),
                                                "19:00", 2, telefono="+393330009999")
    return service, google, booking_repo, booking


async def ledger(pool, org, booking):
    async with pool.acquire() as conn:
        return dict(await conn.fetchrow("SELECT * FROM external_booking_sync WHERE organization_id=$1 AND internal_booking_id=$2",
                                        org, booking["id"]))


async def test_optional_create_update_delete_are_verified_once(pg_pool, sample_org, monkeypatch):
    org = sample_org["id"]
    service, google, repo, booking = await setup(pg_pool, org, monkeypatch)
    await service.process_pending()
    await service.process_pending()
    assert google.calls.count("POST") == 1
    assert (await ledger(pg_pool, org, booking))["sync_status"] == "succeeded"
    async with pg_pool.acquire() as conn:
        await conn.execute("UPDATE bookings SET ora='20:00' WHERE organization_id=$1 AND id=$2", org, booking["id"])
    await service.process_pending()
    assert google.calls.count("PUT") == 1
    await repo.update_booking_status(org, booking["id"], "cancellata")
    await service.process_pending()
    assert google.calls.count("DELETE") == 1
    assert (await repo.get_booking(org, booking["id"]))["google_event_id"] is None


@pytest.mark.parametrize("availability", ["busy", "unknown"])
async def test_authoritative_denial_never_confirms_or_notifies(pg_pool, sample_org, monkeypatch, availability):
    org = sample_org["id"]
    service, google, repo, booking = await setup(pg_pool, org, monkeypatch, "google_authoritative")
    google.availability = availability
    result = await BookingService(booking_repo=repo, calendar_service=service).confirm(org, booking["id"])
    assert result["stato"] == "in_attesa" and result["calendar_confirm_pending"]
    assert "POST" not in google.calls
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1", org) == 0


async def test_authoritative_manual_confirmation_has_durable_revision_scoped_notice(pg_pool, sample_org, monkeypatch):
    org = sample_org["id"]
    service, google, repo, booking = await setup(pg_pool, org, monkeypatch, "google_authoritative")
    result = await BookingService(booking_repo=repo, calendar_service=service).confirm(org, booking["id"])
    assert result["stato"] == "confermata" and not result["calendar_confirm_pending"]
    async with pg_pool.acquire() as conn:
        notice = await conn.fetchrow("SELECT * FROM booking_confirmation_outbox WHERE organization_id=$1", org)
        assert notice["inbound_message_id"] is None
        assert notice["booking_revision"] == result["calendar_revision"]
    messages = MessageRepository(pg_pool)
    assert await messages.enqueue_ready_booking_confirmations() == 1
    async with messages.booking_confirmation_delivery_guard(org, notice["outbound_message_id"]) as allowed:
        assert allowed
    async with pg_pool.acquire() as conn:
        await conn.execute("UPDATE bookings SET ora='20:00',stato='in_attesa' WHERE organization_id=$1 AND id=$2", org, booking["id"])
    async with messages.booking_confirmation_delivery_guard(org, notice["outbound_message_id"]) as allowed:
        assert not allowed
    result = await BookingService(booking_repo=repo, calendar_service=service).confirm(org, booking["id"])
    assert result["stato"] == "confermata"
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1", org) == 2
    await service.process_pending()
    assert google.calls.count("POST") == 1


async def test_timeout_is_reconciled_by_get_without_second_post(pg_pool, sample_org, monkeypatch):
    org = sample_org["id"]
    service, google, _repo, booking = await setup(pg_pool, org, monkeypatch)
    google.timeout = True
    await service.process_pending()
    assert (await ledger(pg_pool, org, booking))["sync_status"] == "uncertain"
    assert await service.queue.retry(org, booking["id"])
    await service.process_pending()
    assert google.calls.count("POST") == 1
    assert (await ledger(pg_pool, org, booking))["sync_status"] == "succeeded"


async def test_cancel_during_remote_create_cannot_confirm_or_resurrect(pg_pool, sample_org, monkeypatch):
    org = sample_org["id"]
    service, google, repo, booking = await setup(pg_pool, org, monkeypatch, "google_authoritative")
    async with repo.slot_lock(org, booking["data"], booking["ora"]):
        await repo.request_google_confirmation(org, booking["id"])
    execute = service.execute
    async def cancel_after_post(request):
        value = await execute(request)
        if request.method == "POST":
            await repo.update_booking_status(org, booking["id"], "cancellata")
        return value
    service.execute = cancel_after_post
    await service.process_pending()
    assert (await repo.get_booking(org, booking["id"]))["stato"] == "cancellata"
    await service.process_pending()
    assert google.calls.count("POST") == google.calls.count("DELETE") == 1
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1", org) == 0


async def test_revoked_access_is_durable_and_never_changes_authority(pg_pool, sample_org, monkeypatch):
    org = sample_org["id"]
    service, google, repo, booking = await setup(pg_pool, org, monkeypatch, "google_authoritative")
    google.read_error = _http(401)
    await BookingService(booking_repo=repo, calendar_service=service).confirm(org, booking["id"])
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT connection_status FROM google_calendar_credentials WHERE organization_id=$1", org) == "reconnect_required"
        assert await conn.fetchval("SELECT config#>>'{a05,authority}' FROM booking_settings WHERE organization_id=$1", org) == "google_authoritative"
    assert await service.process_pending() == 0
    assert "POST" not in google.calls


async def test_timezone_change_during_provider_write_invalidates_confirmation(pg_pool, sample_org, monkeypatch):
    org = sample_org["id"]
    service, google, repo, booking = await setup(pg_pool, org, monkeypatch, "google_authoritative")
    execute = service.execute
    async def change_timezone(request):
        value = await execute(request)
        if request.method == "POST":
            async with pg_pool.acquire() as conn:
                await conn.execute("UPDATE organizations SET timezone='UTC' WHERE id=$1", org)
        return value
    service.execute = change_timezone
    result = await BookingService(booking_repo=repo, calendar_service=service).confirm(org, booking["id"])
    assert result["stato"] == "da_verificare"
    assert not result["calendar_confirm_pending"]
    await service.process_pending()
    assert google.calls.count("DELETE") == 1
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1", org) == 0


async def test_inbound_google_authority_defers_confirmation_without_rerunning_ai(
    pg_pool, private_worker_pool, monkeypatch,
):
    from tests.core.test_a04_review_regressions import _enable_private_a05, _worker

    case = await _worker(pg_pool, monkeypatch)
    org = case.org["id"]
    service, google, _, _ = await setup(pg_pool, org, monkeypatch, "google_authoritative", create_booking=False)
    await _enable_private_a05(case, pg_pool, private_worker_pool, monkeypatch,
                              authority="google_authoritative")
    case.service.booking_service.calendar_service = service
    case.service.legacy_pipeline.booking_service.calendar_service = service
    initial = await case.service.process_message(case.msg)
    assert initial.action == "handled"
    assert google.calls == []
    async with pg_pool.acquire() as conn:
        before = await conn.fetchrow("SELECT * FROM bookings WHERE organization_id=$1", org)
        assert before["stato"] == "in_attesa" and before["calendar_confirm_pending"]
        assert await conn.fetchval("SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1", org) == 0
    await service.process_pending()
    async with pg_pool.acquire() as conn:
        after = await conn.fetchrow("SELECT * FROM bookings WHERE organization_id=$1", org)
        assert after["stato"] == "confermata" and not after["calendar_confirm_pending"]
        notice = await conn.fetchrow("SELECT * FROM booking_confirmation_outbox WHERE organization_id=$1", org)
        assert notice["inbound_message_id"] == case.msg["id"]
        assert await conn.fetchval("SELECT count(*) FROM usage_events WHERE organization_id=$1 AND event_type='system.provider_attempt'", org) == 0
    replay = await case.service.process_message(case.msg)
    assert replay.action == "handled"
    assert google.calls.count("POST") == 1
    assert case.generate.await_count == 1


@pytest.mark.parametrize("credential_state", ["missing", "disconnected"])
async def test_inbound_google_authority_without_ready_credentials_stays_pending(
    pg_pool, private_worker_pool, monkeypatch, credential_state,
):
    from tests.core.test_a04_review_regressions import _enable_private_a05, _worker

    case = await _worker(pg_pool, monkeypatch)
    org = case.org["id"]
    await setup(pg_pool, org, monkeypatch, "google_authoritative", create_booking=False)
    await _enable_private_a05(case, pg_pool, private_worker_pool, monkeypatch,
                              authority="google_authoritative")
    if credential_state == "missing":
        await pg_pool.execute("DELETE FROM google_calendar_credentials WHERE organization_id=$1", org)
    else:
        await pg_pool.execute("""UPDATE google_calendar_credentials
            SET connection_status='revoked',sync_enabled=false WHERE organization_id=$1""", org)

    first = await case.service.process_message(case.msg)
    assert first.action == "handled"
    assert not await pg_pool.fetchval("""SELECT has_function_privilege('service_role',
        'public.c24_attach_calendar_inbound_source(uuid,uuid,uuid)', 'EXECUTE')""")
    async with pg_pool.acquire() as conn:
        booking = await conn.fetchrow("SELECT * FROM bookings WHERE organization_id=$1", org)
        assert booking["stato"] == "in_attesa" and booking["calendar_confirm_pending"]
        assert booking["richiede_intervento"]
        sync = await conn.fetchrow("""SELECT sync_status,calendar_action,calendar_authoritative
            FROM external_booking_sync WHERE organization_id=$1 AND internal_booking_id=$2
              AND provider='google'""", org, booking["id"])
        assert dict(sync) == {
            "sync_status": "blocked", "calendar_action": "authoritative", "calendar_authoritative": True,
        }
        assert await conn.fetchval("SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1", org) == 0
        assert await conn.fetchval("""SELECT count(*) FROM usage_events WHERE organization_id=$1
            AND event_type='system.quota_reservation' AND metadata->>'state'='committed'""", org) == 1

    async with private_worker_pool.acquire() as worker:
        assert not await worker.fetchval("SELECT has_table_privilege(current_user,'public.google_calendar_credentials','SELECT')")
        assert not await worker.fetchval("SELECT has_table_privilege(current_user,'public.external_booking_sync','UPDATE')")
        assert await worker.fetchval(
            "SELECT public.c24_attach_calendar_inbound_source($1,$2,$3)",
            org, booking["id"], case.msg["id"],
        ) is True
        assert await worker.fetchval(
            "SELECT public.c24_attach_calendar_inbound_source($1,$2,$3)",
            org, booking["id"], uuid4(),
        ) is False
        assert await worker.fetchval(
            "SELECT public.c24_attach_calendar_inbound_source($1,$2,$3)",
            uuid4(), booking["id"], case.msg["id"],
        ) is False
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await worker.fetch("SELECT * FROM public.google_calendar_credentials WHERE organization_id=$1", org)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await worker.execute("UPDATE public.external_booking_sync SET calendar_id=NULL WHERE organization_id=$1", org)

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await pg_pool.fetchval(
            "SELECT public.c24_attach_calendar_inbound_source($1,$2,$3)",
            org, booking["id"], case.msg["id"],
        )

    replay = await case.service.process_message(case.msg)
    assert replay.action == "handled"
    assert case.generate.await_count == 1
    assert await pg_pool.fetchval("SELECT count(*) FROM bookings WHERE organization_id=$1", org) == 1
    assert await pg_pool.fetchval("SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1", org) == 0
    await pg_pool.execute("UPDATE bookings SET stato='cancellata',calendar_confirm_pending=false WHERE organization_id=$1", org)
    async with private_worker_pool.acquire() as worker:
        assert await worker.fetchval(
            "SELECT public.c24_attach_calendar_inbound_source($1,$2,$3)",
            org, booking["id"], case.msg["id"],
        ) is False


@pytest.mark.parametrize("lost_response", [False, True])
async def test_explicit_reconnect_retry_requires_correlated_get_and_reuses_event(pg_pool, sample_org, monkeypatch, lost_response):
    org = sample_org["id"]
    service, google, _, booking = await setup(pg_pool, org, monkeypatch)
    google.timeout = lost_response
    await service.process_pending()
    old = await ledger(pg_pool, org, booking)
    await service.connection.disconnect(org)
    nonce = uuid4().hex
    await service.connection.begin_oauth(org, nonce)
    consumed = await service.connection.consume_nonce(org, nonce)
    assert await service.connection.save_oauth(org, consumed["calendar_generation"], SimpleNamespace(
        token="synthetic-new-access", refresh_token="synthetic-new-refresh",
        expiry=datetime.now(timezone.utc) + timedelta(hours=1)))
    assert await service.request_booking_retry(org, booking["id"])
    google.timeout = False
    await service.process_pending()
    current = await ledger(pg_pool, org, booking)
    assert current["calendar_connection_epoch"] != old["calendar_connection_epoch"]
    assert current["sync_status"] == "succeeded"
    assert google.calls.count("POST") == google.calls.count("PUT") == 1


async def test_reconnect_404_cannot_clear_uncertain_write(pg_pool, sample_org, monkeypatch):
    org = sample_org["id"]
    service, google, _, booking = await setup(pg_pool, org, monkeypatch)
    google.timeout = True
    await service.process_pending()
    await service.connection.disconnect(org)
    nonce = uuid4().hex
    await service.connection.begin_oauth(org, nonce)
    consumed = await service.connection.consume_nonce(org, nonce)
    assert await service.connection.save_oauth(org, consumed["calendar_generation"], SimpleNamespace(
        token="synthetic-new-access", refresh_token="synthetic-new-refresh",
        expiry=datetime.now(timezone.utc) + timedelta(hours=1)))
    google.rows.clear()  # Could mean another account, not proof of no old write.
    assert not await service.request_booking_retry(org, booking["id"])
    assert (await ledger(pg_pool, org, booking))["calendar_dispatch_state"] == "possibly_sent"
    assert google.calls.count("POST") == 1
