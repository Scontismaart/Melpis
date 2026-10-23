"""Google transport. Booking effects belong to the fenced durable lifecycle."""
import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import httplib2

from src.core.google_feature_flags import google_calendar_enabled
from src.core.calendar.availability import AvailabilityStatus, CalendarAvailability, aware_utc, local_instant, parse_freebusy
from src.core.calendar.connection import CalendarConnection, CalendarUnavailable, resources_for, connection_lock
from src.core.calendar.repository import CalendarQueue
from src.core.calendar.connection import SCOPES as SCOPES

logger = logging.getLogger(__name__)
DEFAULT_SLOT_MINUTES = 60


def event_id_for(org_id, booking_id):
    return hashlib.sha256(f"melpis-calendar-v1:{org_id}:{booking_id}".encode()).hexdigest()


def event_hash(body):
    """Hash semantic fields, excluding Google's added fields and formatting."""
    private = dict(body.get("extendedProperties", {}).get("private", {}))
    private.pop("payload_hash", None)
    normalized = {"summary": body["summary"], "description": body["description"], "private": private,
                  "start": aware_utc(body["start"]["dateTime"]).isoformat(),
                  "end": aware_utc(body["end"]["dateTime"]).isoformat()}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class GoogleCalendarService:
    def __init__(self, repo, encryption_key):
        self.repo = repo
        if not encryption_key:
            raise ValueError("ENCRYPTION_KEY required for Calendar credentials")
        self.fernet = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)
        self.connection = CalendarConnection(repo.pool, self.fernet)
        self.queue = CalendarQueue(repo.pool)

    async def assert_schema(self):
        async with self.repo.pool.acquire() as conn:
            ready = await conn.fetchval("""SELECT
                EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public'
                    AND table_name='external_booking_sync' AND column_name='calendar_lease_token')
                AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public'
                    AND table_name='google_calendar_credentials' AND column_name='connection_epoch')
                AND EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.bookings'::regclass
                    AND tgname='calendar_queue_booking' AND tgenabled='O')
                AND EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.bookings'::regclass
                    AND tgname='calendar_track_booking' AND tgenabled='O')""")
        if not ready:
            raise CalendarUnavailable("calendar_schema_064_required")

    def encrypt_secret(self, value):
        return self.fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value):
        return self.fernet.decrypt(value.encode()).decode()

    def _get_client_config(self):
        if not google_calendar_enabled():
            raise CalendarUnavailable("disabled")
        if not os.getenv("GOOGLE_CLIENT_ID") or not os.getenv("GOOGLE_CLIENT_SECRET"):
            raise CalendarUnavailable("configuration_missing")
        return {"client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "token_uri": "https://oauth2.googleapis.com/token"}

    async def _get_credentials(self, org_id):
        if not google_calendar_enabled():
            return None
        try:
            return (await self.connection.credentials(org_id))[0]
        except CalendarUnavailable:
            return None

    async def _get_calendar_id(self, org_id):
        row = await self.connection.row(org_id)
        return row["calendar_id"] if row else "primary"

    async def _get_org_timezone(self, org_id):
        async with self.repo.pool.acquire() as conn:
            value = await conn.fetchval("SELECT timezone FROM organizations WHERE id=$1", UUID(str(org_id)))
        ZoneInfo(value)
        return value

    async def _build_service(self, org_id, expected_epoch=None):
        if not google_calendar_enabled():
            raise CalendarUnavailable("disabled")
        creds, _ = await self.connection.credentials(org_id, expected_epoch)
        http = AuthorizedHttp(creds, http=httplib2.Http(timeout=10), max_refresh_attempts=0)
        return await asyncio.to_thread(build, "calendar", "v3", http=http, static_discovery=True, cache_discovery=False)

    @staticmethod
    async def execute(request):
        return await asyncio.to_thread(request.execute, num_retries=0)

    async def availability(self, org_id, starts_at, ends_at, *, connection=None, service=None):
        if not google_calendar_enabled():
            return CalendarAvailability(AvailabilityStatus.DISABLED, reason_code="disabled")
        row = None
        try:
            if starts_at.utcoffset() is None or ends_at.utcoffset() is None or starts_at >= ends_at:
                raise ValueError("Invalid availability window")
            row = connection or await self.connection.row(org_id)
            if not row or row["connection_status"] != "connected" or not row["sync_enabled"]:
                return CalendarAvailability(AvailabilityStatus.DISABLED, reason_code="not_connected")
            resources = resources_for(row)
            service = service or await self._build_service(org_id, row["connection_epoch"])
            body = {"timeMin": starts_at.isoformat(), "timeMax": ends_at.isoformat(),
                    "items": [{"id": resource} for resource in resources]}
            result = parse_freebusy(await self.execute(service.freebusy().query(body=body)), resources,
                                    starts_at, ends_at, connection_epoch=str(row["connection_epoch"]))
            async with connection_lock(self.repo.pool, org_id) as conn:
                current = await conn.fetchval("""UPDATE google_calendar_credentials SET last_verified_at=
                    CASE WHEN $3 THEN NOW() ELSE last_verified_at END,last_error_code=$4
                    WHERE organization_id=$1 AND connection_epoch=$2 AND connection_status='connected'
                      AND sync_enabled RETURNING TRUE""", UUID(str(org_id)), row["connection_epoch"], result.verified,
                    None if result.verified else result.reason_code)
            return result if current else CalendarAvailability(AvailabilityStatus.UNKNOWN, reason_code="connection_changed")
        except Exception:
            logger.warning("calendar=availability_unknown organization_id=%s", org_id)
            if row and row.get("connection_epoch"):
                try:
                    await self.connection.mark_error(org_id, row["connection_epoch"], "provider_unavailable")
                except Exception:
                    pass  # No success claim when health persistence is also unavailable.
            return CalendarAvailability(AvailabilityStatus.UNKNOWN, reason_code="provider_unavailable")

    async def get_busy_intervals(self, org_id, data):
        """Typed result for a local day; never [] on error."""
        if not google_calendar_enabled():
            return CalendarAvailability(AvailabilityStatus.DISABLED, reason_code="disabled")
        try:
            name = await self._get_org_timezone(org_id)
            start = local_instant(data, "00:00:00", name)
            next_day = (datetime.fromisoformat(str(data)).date() + timedelta(days=1)).isoformat()
            end = local_instant(next_day, "00:00:00", name)
            return await self.availability(org_id, start, end)
        except Exception:
            return CalendarAvailability(AvailabilityStatus.UNKNOWN, reason_code="invalid_timezone_or_date")

    async def availability_excluding_event(self, org_id, starts_at, ends_at, *, connection, service, event_id=None):
        if not event_id:
            return await self.availability(org_id, starts_at, ends_at, connection=connection, service=service)
        # Free/busy cannot exclude our own event. Re-query the selected calendar
        # as expanded instances, and require valid free/busy for every other resource.
        try:
            from datetime import date, time
            from src.core.calendar.availability import parse_freebusy
            resources = resources_for(connection)
            selected = resources[0]
            intervals, seen_tokens, token = [], set(), None
            for _ in range(20):
                payload = await self.execute(service.events().list(calendarId=selected,
                    timeMin=starts_at.isoformat(), timeMax=ends_at.isoformat(), singleEvents=True,
                    showDeleted=False, maxResults=250, pageToken=token))
                if (not isinstance(payload, dict) or payload.get("kind") != "calendar#events"
                        or not isinstance(payload.get("items"), list)):
                    raise ValueError
                calendar_zone = ZoneInfo(payload["timeZone"])
                for event in payload["items"]:
                    if not isinstance(event, dict) or not event.get("id"):
                        raise ValueError
                    if event["id"] == event_id:
                        continue
                    if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                        continue
                    if event.get("status") not in {"confirmed", "tentative"}:
                        raise ValueError
                    def instant(value):
                        if "dateTime" in value:
                            return aware_utc(value["dateTime"])
                        return datetime.combine(date.fromisoformat(value["date"]), time(), calendar_zone).astimezone(timezone.utc)
                    start, end = instant(event["start"]), instant(event["end"])
                    if start >= end:
                        raise ValueError
                    intervals.append({"start": start.isoformat(), "end": end.isoformat()})
                token = payload.get("nextPageToken")
                if token is None:
                    break
                if not isinstance(token, str) or not token or token in seen_tokens:
                    raise ValueError
                seen_tokens.add(token)
            else:
                raise ValueError
            others = resources[1:]
            calendars = {selected: {"busy": intervals}}
            if others:
                body = {"timeMin": starts_at.isoformat(), "timeMax": ends_at.isoformat(),
                        "items": [{"id": resource} for resource in others]}
                other_payload = await self.execute(service.freebusy().query(body=body))
                verified = parse_freebusy(other_payload, others, starts_at, ends_at)
                if not verified.verified:
                    raise ValueError
                calendars.update(other_payload["calendars"])
            synthetic = {"kind": "calendar#freeBusy", "timeMin": starts_at.isoformat(),
                         "timeMax": ends_at.isoformat(), "calendars": calendars}
            return parse_freebusy(synthetic, resources, starts_at, ends_at,
                                  connection_epoch=str(connection["connection_epoch"]))
        except Exception:
            return CalendarAvailability(AvailabilityStatus.UNKNOWN, reason_code="event_availability_unverified")

    async def event_body(self, booking, claim):
        tz = await self._get_org_timezone(claim["organization_id"])
        return self.event_body_for(booking, claim, tz)

    @staticmethod
    def event_body_for(booking, claim, tz):
        start = local_instant(booking["data"], booking["ora"], tz)
        body = {"id": event_id_for(claim["organization_id"], booking["id"]),
                "summary": f"{booking['nome_cliente']} — {booking['coperti']} coperti",
                "description": f"Booking ID: {booking['id']}\nNote: {booking.get('note') or ''}",
                "start": {"dateTime": start.isoformat(), "timeZone": tz},
                "end": {"dateTime": (start + timedelta(minutes=60)).isoformat(), "timeZone": tz},
                "extendedProperties": {"private": {"organization_id": str(claim["organization_id"]),
                    "booking_id": str(booking["id"]), "revision": str(claim["calendar_claimed_revision"]),
                    "connection_epoch": str(claim["calendar_connection_epoch"])}}}
        body["extendedProperties"]["private"]["payload_hash"] = event_hash(body)
        return body

    @staticmethod
    def owned(event, claim):
        private = event.get("extendedProperties", {}).get("private", {})
        return (event.get("id") == event_id_for(claim["organization_id"], claim["internal_booking_id"])
                and private.get("organization_id") == str(claim["organization_id"])
                and private.get("booking_id") == str(claim["internal_booking_id"]))

    @classmethod
    def matches(cls, event, claim, payload_hash):
        try:
            private = event["extendedProperties"]["private"]
            return (cls.owned(event, claim) and event.get("status") == "confirmed"
                    and private.get("revision") == str(claim["calendar_claimed_revision"])
                    and private.get("connection_epoch") == str(claim["calendar_connection_epoch"])
                    and private.get("payload_hash") == payload_hash == event_hash(event)
                    and isinstance(event.get("etag"), str) and bool(event["etag"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    async def read_event(self, service, claim):
        try:
            return await self.execute(service.events().get(calendarId=claim["calendar_id"],
                eventId=event_id_for(claim["organization_id"], claim["internal_booking_id"])))
        except HttpError as exc:
            if exc.resp.status in (404, 410):
                return None
            raise

    async def process_pending(self, limit=10, *, organization_id=None, booking_id=None):
        if not google_calendar_enabled() or os.getenv("GOOGLE_CALENDAR_WORKER_ENABLED", "false").lower() != "true":
            return 0
        from src.core.calendar.lifecycle import CalendarLifecycle
        return await CalendarLifecycle(self).process(limit, organization_id=organization_id, booking_id=booking_id)

    async def sync_booking_state(self, booking, org_id):
        if not google_calendar_enabled():
            return None
        if not booking or (booking.get("organization_id") is not None and str(booking["organization_id"]) != str(org_id)):
            return None
        await self.process_pending(1, organization_id=org_id, booking_id=booking["id"])
        async with self.repo.pool.acquire() as conn:
            return await conn.fetchval("""SELECT b.google_event_id FROM bookings b JOIN external_booking_sync s
                ON s.internal_booking_id=b.id AND s.organization_id=$1
                WHERE b.organization_id=$1 AND b.id=$2 AND s.provider='google'
                  AND s.sync_status='succeeded' AND s.calendar_applied_revision=b.calendar_revision""",
                                       UUID(str(org_id)), UUID(str(booking["id"])))

    async def request_booking_retry(self, org_id, booking_id):
        if not google_calendar_enabled():
            return False
        if await self.queue.retry(org_id, booking_id):
            return True
        current = await self.connection.row(org_id)
        if not current or current["connection_status"] != "connected" or not current["sync_enabled"]:
            return False
        async with self.repo.pool.acquire() as conn:
            old = await conn.fetchrow("""SELECT * FROM external_booking_sync WHERE organization_id=$1
                AND internal_booking_id=$2 AND provider='google'""", UUID(str(org_id)), UUID(str(booking_id)))
        if (not old or not old["calendar_payload_hash"] or old["calendar_claimed_action"] == "delete"
                or old["calendar_id"] != current["calendar_id"] or old["calendar_connection_epoch"] == current["connection_epoch"]):
            return False
        try:
            provider = await self._build_service(org_id, current["connection_epoch"])
            observed = await self.read_event(provider, dict(old))
        except Exception:
            logger.warning("calendar=reconnect_receipt_unverified organization_id=%s", org_id)
            return False
        if not observed or not self.matches(observed, old, old["calendar_payload_hash"]):
            return False
        return await self.queue.reconnect_receipt(dict(old), current["connection_epoch"], observed)

    create_event = sync_booking_state
    update_event = sync_booking_state
    delete_event = sync_booking_state
