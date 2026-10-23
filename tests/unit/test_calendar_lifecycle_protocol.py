"""Faked-provider protocol checks for the Calendar outbox lifecycle."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httplib2
import pytest
from googleapiclient.errors import HttpError

from src.core.calendar.availability import AvailabilityStatus, CalendarAvailability
from src.core.calendar.lifecycle import CalendarLifecycle
from src.core.calendar.service import GoogleCalendarService, event_hash, event_id_for


ORG_ID = UUID("11111111-1111-1111-1111-111111111111")
BOOKING_ID = UUID("22222222-2222-2222-2222-222222222222")
QUEUE_ID = UUID("33333333-3333-3333-3333-333333333333")
CONNECTION_EPOCH = UUID("44444444-4444-4444-4444-444444444444")
LEASE_TOKEN = UUID("55555555-5555-5555-5555-555555555555")
EVENT_ID = event_id_for(ORG_ID, BOOKING_ID)


def _not_found():
    return HttpError(httplib2.Response({"status": "404"}), b"synthetic not found")


class _Request:
    def __init__(self, provider, method, *, event_id=None, body=None):
        self.provider, self.method, self.event_id, self.body = provider, method, event_id, body
        self.headers = {}

    def execute(self, **_kwargs):
        self.provider.calls.append((self.method, self.event_id, dict(self.headers)))
        if self.method == "GET":
            if self.provider.get_override is not None:
                result = self.provider.get_override
                if isinstance(result, Exception):
                    raise result
                return deepcopy(result)
            if self.event_id not in self.provider.event_store:
                raise _not_found()
            return deepcopy(self.provider.event_store[self.event_id])
        if self.method == "POST":
            self.provider.post_count += 1
            value = {**deepcopy(self.body), "etag": "etag-created", "status": "confirmed"}
            self.provider.event_store[self.event_id or value["id"]] = value
            if self.provider.timeout_after_post:
                raise TimeoutError("synthetic timeout after provider accepted insert")
            return deepcopy(value)
        if self.method == "PUT":
            value = {**deepcopy(self.body), "etag": "etag-updated", "status": "confirmed"}
            self.provider.event_store[self.event_id] = value
            return deepcopy(value)
        if self.method == "DELETE":
            value = self.provider.event_store[self.event_id]
            value["status"] = "cancelled"
            return {}
        raise AssertionError(f"Unexpected provider method: {self.method}")


class _Events:
    def __init__(self, provider):
        self.provider = provider

    def get(self, *, calendarId, eventId):
        assert calendarId == "stage@example.test"
        return _Request(self.provider, "GET", event_id=eventId)

    def insert(self, *, calendarId, body):
        assert calendarId == "stage@example.test"
        return _Request(self.provider, "POST", event_id=body["id"], body=body)

    def update(self, *, calendarId, eventId, body):
        assert calendarId == "stage@example.test"
        return _Request(self.provider, "PUT", event_id=eventId, body=body)

    def delete(self, *, calendarId, eventId):
        assert calendarId == "stage@example.test"
        return _Request(self.provider, "DELETE", event_id=eventId)


class _Provider:
    def __init__(self):
        self.event_store = {}
        self.calls = []
        self.post_count = 0
        self.timeout_after_post = False
        self.get_override = None

    def events(self):
        return _Events(self)


class _DBConnection:
    def __init__(self, booking, *, worker_lock=True):
        self.booking = booking
        self.worker_lock = worker_lock
        self.execute = AsyncMock()
        self.fetchrow = AsyncMock(side_effect=self._fetchrow)
        self.fetchval = AsyncMock(side_effect=self._fetchval)
        self.is_closed = MagicMock(return_value=False)

    async def _fetchrow(self, query, *_args):
        if "FROM bookings" in query:
            return self.booking
        raise AssertionError(f"Unexpected DB fetchrow: {query}")

    async def _fetchval(self, query, *_args):
        if "pg_try_advisory_lock" in query:
            return self.worker_lock
        raise AssertionError(f"Unexpected DB fetchval: {query}")


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        class _Acquire:
            async def __aenter__(inner_self):
                return self.conn

            async def __aexit__(inner_self, *_args):
                return False

        return _Acquire()


class _Queue:
    def __init__(self, claim, *, current=True, dispatch=True):
        self.claims = [claim]
        self.current_values = current if isinstance(current, list) else [current]
        self.dispatch_result = dispatch
        self.failures = []
        self.verified_rows = []
        self.dispatched_hashes = []

    async def claim(self, *_args, **_kwargs):
        return self.claims

    async def current(self, _claim, *, require_revision=True):
        self.require_revision = require_revision
        if len(self.current_values) > 1:
            return self.current_values.pop(0)
        return self.current_values[0]

    async def dispatch(self, claim, payload_hash):
        self.dispatched_hashes.append(payload_hash)
        if self.dispatch_result:
            claim["dispatch_seen"] = True
        return self.dispatch_result

    async def fail(self, claim, code, *, uncertain=False, blocked=False):
        self.failures.append({"claim": claim, "code": code,
                              "uncertain": uncertain, "blocked": blocked})

    async def verified(self, claim, event_id, etag, _finalize=None):
        self.verified_rows.append((claim, event_id, etag))
        return True

    async def renew(self, _claim):
        return True

    async def availability_checked(self, _claim, _checked_at):
        return True


class _Service(GoogleCalendarService):
    def __init__(self, claim, *, current=True, dispatch=True, booking=None):
        self.provider = _Provider()
        self.booking = booking or {
            "id": BOOKING_ID,
            "organization_id": ORG_ID,
            "data": "2026-01-15",
            "ora": "10:00:00",
            "nome_cliente": "Synthetic Guest",
            "coperti": 2,
            "note": None,
        }
        self.pool_connection = _DBConnection(self.booking)
        self.repo = SimpleNamespace(pool=_Pool(self.pool_connection))
        self.queue = _Queue(claim, current=current, dispatch=dispatch)
        self.connection = SimpleNamespace(row=AsyncMock(return_value={
            "connection_status": "connected",
            "sync_enabled": True,
            "connection_epoch": CONNECTION_EPOCH,
            "calendar_id": "stage@example.test",
            "resource_calendar_ids": ["stage@example.test"],
        }))
        self.authority_result = None
        self.body = _event_body(claim)
        self._build_service = AsyncMock(return_value=self.provider)
        self._get_org_timezone = AsyncMock(return_value="UTC")
        self.availability_excluding_event = AsyncMock(side_effect=self._availability)

    async def event_body(self, _booking, _claim):
        return deepcopy(self.body)

    async def _availability(self, *_args, **_kwargs):
        return self.authority_result or CalendarAvailability(AvailabilityStatus.FREE, checked_at=datetime.now(timezone.utc))


def _event_body(claim):
    body = {
        "id": event_id_for(claim["organization_id"], claim["internal_booking_id"]),
        "summary": "Synthetic booking",
        "description": "Synthetic fixture",
        "start": {"dateTime": "2026-01-15T10:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-01-15T11:00:00Z", "timeZone": "UTC"},
        "extendedProperties": {"private": {
            "organization_id": str(claim["organization_id"]),
            "booking_id": str(claim["internal_booking_id"]),
            "revision": str(claim["calendar_claimed_revision"]),
            "connection_epoch": str(claim["calendar_connection_epoch"]),
        }},
    }
    body["extendedProperties"]["private"]["payload_hash"] = event_hash(body)
    return body


def _claim(action="upsert", *, recovery=False):
    return {
        "id": QUEUE_ID,
        "organization_id": ORG_ID,
        "internal_booking_id": BOOKING_ID,
        "calendar_connection_epoch": CONNECTION_EPOCH,
        "calendar_lease_token": LEASE_TOKEN,
        "calendar_fence": 4,
        "calendar_claimed_revision": 9,
        "calendar_desired_revision": 9,
        "calendar_claimed_action": action,
        "calendar_action": action,
        "calendar_payload_hash": None,
        "calendar_id": "stage@example.test",
        "calendar_authoritative": action == "authoritative",
        "recovery": recovery,
    }


def _owned_event(claim, *, revision=None, etag="etag-old", payload_hash=None):
    body = _event_body(claim)
    if revision is not None:
        body["extendedProperties"]["private"]["revision"] = str(revision)
    if payload_hash is not None:
        body["extendedProperties"]["private"]["payload_hash"] = payload_hash
        body["extendedProperties"]["private"].pop("payload_hash", None)
        body["extendedProperties"]["private"]["payload_hash"] = payload_hash
    body["etag"] = etag
    body["status"] = "confirmed"
    return body


async def _run(claim, *, current=True, dispatch=True):
    service = _Service(claim, current=current, dispatch=dispatch)
    lifecycle = CalendarLifecycle(service)
    await lifecycle.run(claim)
    return lifecycle, service


@pytest.mark.asyncio
async def test_uncertain_missing_event_get_is_not_treated_as_permission_to_post():
    claim = _claim(recovery=True)
    claim["calendar_payload_hash"] = _event_body(claim)["extendedProperties"]["private"]["payload_hash"]
    lifecycle, service = await _run(claim)

    assert service.provider.calls == [("GET", EVENT_ID, {})]
    assert service.provider.post_count == 0
    assert service.queue.failures == [{
        "claim": claim, "code": "outcome_unverified", "uncertain": True, "blocked": False,
    }]
    assert service.queue.verified_rows == []
    assert service.queue.require_revision is False


@pytest.mark.asyncio
async def test_uncertain_recovery_with_exact_hash_verifies_by_get_without_post():
    claim = _claim(recovery=True)
    digest = _event_body(claim)["extendedProperties"]["private"]["payload_hash"]
    claim["calendar_payload_hash"] = digest
    service = _Service(claim)
    service.provider.event_store[EVENT_ID] = _owned_event(claim, payload_hash=digest)

    await CalendarLifecycle(service).run(claim)

    assert service.provider.calls == [("GET", EVENT_ID, {})]
    assert service.provider.post_count == 0
    assert service.queue.failures == []
    assert service.queue.verified_rows == [(claim, EVENT_ID, "etag-old")]
    assert service.queue.require_revision is False


@pytest.mark.asyncio
async def test_late_lease_or_revision_change_denies_before_provider_read():
    claim = _claim()
    _lifecycle, service = await _run(claim, current=False)

    assert service.provider.calls == []
    assert service.provider.post_count == 0
    assert service.queue.failures[0]["code"] == "context_changed"
    assert service.queue.failures[0]["blocked"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "tentative", "unknown"])
async def test_recovery_requires_explicit_confirmed_provider_status(status):
    claim = _claim("authoritative", recovery=True)
    service = _Service(claim)
    digest = service.body["extendedProperties"]["private"]["payload_hash"]
    claim["calendar_payload_hash"] = digest
    event = _owned_event(claim, payload_hash=digest)
    if status is None:
        event.pop("status")
    else:
        event["status"] = status
    service.provider.event_store[EVENT_ID] = event
    await CalendarLifecycle(service).run(claim)
    assert service.queue.verified_rows == []
    assert service.provider.post_count == 0
    assert service.queue.failures[0]["uncertain"] is True


@pytest.mark.asyncio
async def test_dispatch_cas_denial_prevents_insert_after_initial_get_404():
    claim = _claim()
    _lifecycle, service = await _run(claim, dispatch=False)

    assert service.provider.calls == [("GET", EVENT_ID, {})]
    assert service.provider.post_count == 0
    assert service.queue.dispatched_hashes == [_event_body(claim)["extendedProperties"]["private"]["payload_hash"]]
    assert service.queue.verified_rows == []
    assert service.queue.failures == []


@pytest.mark.asyncio
async def test_optional_create_requires_get_404_then_dispatches_one_insert_and_verifies():
    claim = _claim()
    _lifecycle, service = await _run(claim)

    assert [call[0] for call in service.provider.calls] == ["GET", "POST", "GET"]
    assert service.provider.post_count == 1
    assert service.queue.verified_rows == [(claim, EVENT_ID, "etag-created")]
    assert service.queue.failures == []


@pytest.mark.asyncio
async def test_optional_update_uses_exact_event_etag_and_verifies_updated_body():
    claim = _claim()
    service = _Service(claim)
    old = _owned_event(claim, revision=8, etag='"etag-before"')
    service.provider.event_store[EVENT_ID] = old
    await CalendarLifecycle(service).run(claim)

    assert [call[0] for call in service.provider.calls] == ["GET", "PUT", "GET"]
    update = service.provider.calls[1]
    assert update[1] == EVENT_ID
    assert update[2] == {"If-Match": '"etag-before"'}
    assert service.provider.post_count == 0
    assert service.queue.verified_rows == [(claim, EVENT_ID, "etag-updated")]


@pytest.mark.asyncio
async def test_optional_delete_uses_etag_and_verifies_cancelled_event():
    claim = _claim("delete")
    service = _Service(claim)
    service.provider.event_store[EVENT_ID] = _owned_event(claim, etag='"delete-etag"')
    await CalendarLifecycle(service).run(claim)

    assert [call[0] for call in service.provider.calls] == ["GET", "DELETE", "GET"]
    assert service.provider.calls[1][2] == {"If-Match": '"delete-etag"'}
    assert service.provider.post_count == 0
    assert service.queue.verified_rows == [(claim, None, None)]
    assert service.queue.failures == []


@pytest.mark.asyncio
async def test_malformed_get_response_fails_closed_without_insert():
    claim = _claim()
    service = _Service(claim)
    service.provider.get_override = []
    lifecycle = CalendarLifecycle(service)
    await lifecycle.process(1)

    assert service.provider.post_count == 0
    assert [call[0] for call in service.provider.calls] == ["GET"]
    assert service.queue.failures[0]["code"] == "provider_unavailable"


@pytest.mark.asyncio
async def test_foreign_event_ownership_conflict_fails_closed_without_update_or_insert():
    claim = _claim()
    service = _Service(claim)
    foreign = _owned_event(claim)
    foreign["extendedProperties"]["private"]["organization_id"] = str(ORG_ID.int + 1)
    service.provider.event_store[EVENT_ID] = foreign
    await CalendarLifecycle(service).run(claim)

    assert service.provider.calls == [("GET", EVENT_ID, {})]
    assert service.provider.post_count == 0
    assert service.queue.failures[0]["code"] == "event_ownership_conflict"
    assert service.queue.failures[0]["blocked"] is True


@pytest.mark.asyncio
async def test_timeout_after_insert_persists_uncertainty_and_recovery_does_not_post_again():
    claim = _claim()
    service = _Service(claim)
    service.provider.timeout_after_post = True
    lifecycle = CalendarLifecycle(service)
    await lifecycle.process(1)

    assert service.provider.post_count == 1
    assert [call[0] for call in service.provider.calls] == ["GET", "POST"]
    assert service.queue.failures[0]["code"] == "provider_unavailable"
    assert service.queue.failures[0]["uncertain"] is True
    assert service.queue.failures[0]["blocked"] is False
    assert claim.get("dispatch_seen") is True

    # A subsequent leased recovery may observe the accepted insert, but may
    # only reconcile by exact GET and must never issue a second write.
    claim["recovery"] = True
    claim["calendar_payload_hash"] = _event_body(claim)["extendedProperties"]["private"]["payload_hash"]
    service.provider.timeout_after_post = False
    service.queue.failures.clear()
    service.queue.verified_rows.clear()
    before_post_count = service.provider.post_count
    await CalendarLifecycle(service).run(claim)

    assert service.provider.post_count == before_post_count == 1
    assert [call[0] for call in service.provider.calls[-1:]] == ["GET"]
    assert service.queue.failures == []
    assert service.queue.verified_rows == [(claim, EVENT_ID, "etag-created")]


@pytest.mark.asyncio
async def test_tenant_worker_lock_denial_prevents_second_worker_provider_calls():
    claim = _claim()
    service = _Service(claim)
    service.repo.pool.conn.worker_lock = False
    await CalendarLifecycle(service).process(1)

    assert service.provider.calls == []
    assert service.queue.failures == [{
        "claim": claim, "code": "worker_busy", "uncertain": False, "blocked": False,
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected_code",
    [
        (AvailabilityStatus.BUSY, "slot_busy"),
        (AvailabilityStatus.UNKNOWN, "availability_unknown"),
    ],
)
async def test_authoritative_busy_or_unknown_never_inserts_or_finalizes(status, expected_code):
    claim = _claim("authoritative")
    service = _Service(claim)
    service.authority_result = CalendarAvailability(status, reason_code="synthetic")
    await CalendarLifecycle(service).run(claim)

    assert [call[0] for call in service.provider.calls] == ["GET"]
    assert service.provider.post_count == 0
    assert service.queue.dispatched_hashes == []
    assert service.queue.verified_rows == []
    assert service.queue.failures[0]["code"] == expected_code
    assert service.queue.failures[0]["blocked"] is True
    service.availability_excluding_event.assert_awaited_once()
