"""Server-only Calendar credentials and explicit owner-selected authority."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timezone
import json
import os
import re
from uuid import UUID, uuid4

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/calendar.events.freebusy"]
MODES = {"internal", "internal_calendar_sync", "google_authoritative"}
_CALENDAR_ID = re.compile(r"^[A-Za-z0-9._@#-]{1,254}$")


class CalendarUnavailable(RuntimeError):
    def __init__(self, code="provider_unavailable"):
        self.code = code
        super().__init__(code)


def resources_for(row):
    values = row.get("resource_calendar_ids") or []
    if isinstance(values, str):
        values = json.loads(values)
    selected = row.get("calendar_id") or "primary"
    values = list(dict.fromkeys([selected, *values]))
    if not 1 <= len(values) <= 10 or any(not isinstance(v, str) or not _CALENDAR_ID.fullmatch(v) for v in values):
        raise ValueError("Invalid Calendar resources")
    return tuple(values)


def authority_from(settings):
    config = (settings or {}).get("config") or {}
    if isinstance(config, str):
        config = json.loads(config)
    value = (config.get("a05") or {}).get("authority", "internal")
    return value if isinstance(value, str) else "unsupported"


@asynccontextmanager
async def connection_lock(pool, org_id):
    """Cross-worker serialization. Never an application-wide credential lock."""
    async with pool.acquire() as conn:
        key = f"calendar-credentials:{org_id}"
        await conn.execute("SELECT pg_advisory_lock_shared(hashtextextended($1::text,606))", str(org_id))
        try:
            await conn.execute("SELECT pg_advisory_lock(hashtextextended($1,604))", key)
            try:
                yield conn
            finally:
                if not conn.is_closed():
                    await asyncio.shield(conn.execute("SELECT pg_advisory_unlock(hashtextextended($1,604))", key))
        finally:
            if not conn.is_closed():
                await asyncio.shield(conn.execute("SELECT pg_advisory_unlock_shared(hashtextextended($1::text,606))", str(org_id)))


class CalendarConnection:
    def __init__(self, pool, fernet):
        self.pool, self.fernet = pool, fernet

    async def row(self, org_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM google_calendar_credentials WHERE organization_id=$1", UUID(str(org_id)))
            return dict(row) if row else None

    async def mode(self, org_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT config FROM booking_settings WHERE organization_id=$1", UUID(str(org_id)))
            return authority_from(dict(row) if row else None)

    async def begin_oauth(self, org_id, nonce):
        org_id = UUID(str(org_id))
        async with connection_lock(self.pool, org_id) as conn, conn.transaction():
            generation = uuid4()
            await conn.execute("""INSERT INTO google_calendar_credentials
                (organization_id,connection_status,sync_enabled,oauth_generation,oauth_sync_enabled)
                VALUES($1,'disconnected',FALSE,$2,TRUE)
                ON CONFLICT(organization_id) DO UPDATE SET oauth_generation=$2,
                    oauth_sync_enabled=CASE WHEN google_calendar_credentials.connection_status='disconnected'
                        THEN TRUE ELSE google_calendar_credentials.sync_enabled END,updated_at=NOW()""", org_id, generation)
            await conn.execute("DELETE FROM oauth_nonces WHERE organization_id=$1 AND purpose='calendar'", org_id)
            await conn.execute("""INSERT INTO oauth_nonces(nonce,organization_id,created_at,purpose,calendar_generation)
                VALUES($1,$2,NOW(),'calendar',$3)""", nonce, org_id, generation)

    async def consume_nonce(self, org_id, nonce):
        async with connection_lock(self.pool, org_id) as conn, conn.transaction():
            return await conn.fetchrow("""DELETE FROM oauth_nonces n USING google_calendar_credentials c
                WHERE n.organization_id=$1 AND c.organization_id=$1 AND n.nonce=$2 AND n.purpose='calendar'
                  AND c.organization_id=n.organization_id AND c.oauth_generation=n.calendar_generation
                RETURNING n.created_at,n.calendar_generation""", UUID(str(org_id)), nonce)

    async def save_oauth(self, org_id, generation, creds):
        if not isinstance(creds.token, str) or not creds.token or not isinstance(creds.refresh_token, str) or not creds.refresh_token:
            raise CalendarUnavailable("reconnect_required")
        expiry = creds.expiry
        if expiry is None:
            raise CalendarUnavailable("invalid_token_response")
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        async with connection_lock(self.pool, org_id) as conn:
            return bool(await conn.fetchval("""UPDATE google_calendar_credentials
                SET access_token=$3,refresh_token=$4,token_expiry=$5,connection_epoch=$6,
                    connection_status='connected',granted_scopes=$7::jsonb,credential_version=credential_version+1,
                    sync_enabled=oauth_sync_enabled,last_error_code=NULL,last_verified_at=NULL,updated_at=NOW()
                WHERE organization_id=$1 AND oauth_generation=$2 RETURNING TRUE""",
                UUID(str(org_id)), generation,
                self.fernet.encrypt(creds.token.encode()).decode(),
                self.fernet.encrypt(creds.refresh_token.encode()).decode(), expiry, uuid4(), json.dumps(SCOPES)))

    async def disconnect(self, org_id):
        # Invalidate callbacks, refresh writes and worker epochs atomically.
        # Keep event IDs and queue evidence: disconnect is not remote deletion.
        async with connection_lock(self.pool, org_id) as conn, conn.transaction():
            previous = await conn.fetchrow("SELECT * FROM google_calendar_credentials WHERE organization_id=$1 FOR UPDATE", UUID(str(org_id)))
            await conn.execute("""UPDATE google_calendar_credentials SET access_token=NULL,refresh_token=NULL,
                token_expiry=NULL,sync_enabled=FALSE,connection_status='disconnected',
                connection_epoch=$2,oauth_generation=$3,credential_version=credential_version+1,
                last_error_code=NULL,last_verified_at=NULL,updated_at=NOW() WHERE organization_id=$1""",
                UUID(str(org_id)), uuid4(), uuid4())
            await conn.execute("DELETE FROM oauth_nonces WHERE organization_id=$1 AND purpose='calendar'", UUID(str(org_id)))
            await conn.execute("""UPDATE external_booking_sync SET
                sync_status=CASE WHEN calendar_dispatch_state='possibly_sent' THEN 'uncertain' ELSE 'blocked' END,
                sync_error='disconnected',calendar_fence=calendar_fence+1,
                calendar_lease_token=NULL,calendar_lease_until=NULL,updated_at=NOW()
                WHERE organization_id=$1 AND provider='google' AND sync_status NOT IN ('succeeded','cancelled')""", UUID(str(org_id)))
            return dict(previous) if previous else None

    async def mark_error(self, org_id, epoch, code, *, revoked=False, reconnect=False):
        async with connection_lock(self.pool, org_id) as conn:
            await conn.execute("""UPDATE google_calendar_credentials SET last_error_code=$3,
                connection_status=CASE WHEN $4 THEN 'revoked' WHEN $5 THEN 'reconnect_required' ELSE connection_status END,
                updated_at=NOW() WHERE organization_id=$1 AND connection_epoch=$2""",
                UUID(str(org_id)), UUID(str(epoch)), code, revoked, reconnect)

    async def credentials(self, org_id, expected_epoch=None):
        async with connection_lock(self.pool, org_id) as conn:
            row = await conn.fetchrow("SELECT * FROM google_calendar_credentials WHERE organization_id=$1", UUID(str(org_id)))
            if not row or row["connection_status"] != "connected" or not row["sync_enabled"]:
                raise CalendarUnavailable("not_connected" if not row else row["connection_status"])
            if expected_epoch is not None and str(row["connection_epoch"]) != str(expected_epoch):
                raise CalendarUnavailable("connection_changed")
            scopes = row["granted_scopes"]
            if isinstance(scopes, str):
                scopes = json.loads(scopes)
            if not isinstance(scopes, list) or not set(SCOPES).issubset(scopes):
                raise CalendarUnavailable("reconnect_required")
            try:
                cfg = {"client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"]}
                expiry = row["token_expiry"]
                if not expiry:
                    raise ValueError
                expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
                creds = Credentials(token=self.fernet.decrypt(row["access_token"].encode()).decode(),
                                    refresh_token=self.fernet.decrypt(row["refresh_token"].encode()).decode(),
                                    token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES, expiry=expiry, **cfg)
            except Exception:
                raise CalendarUnavailable("credential_unavailable") from None
            if not creds.refresh_token:
                raise CalendarUnavailable("reconnect_required")
            if creds.expired:
                request = GoogleAuthRequest()

                def bounded_request(*args, **kwargs):
                    kwargs["timeout"] = 10
                    return request(*args, **kwargs)

                try:
                    await asyncio.to_thread(creds.refresh, bounded_request)
                except RefreshError as exc:
                    details = next((value for value in exc.args if isinstance(value, dict)), {})
                    if getattr(exc, "retryable", False) or details.get("error") != "invalid_grant":
                        raise CalendarUnavailable("refresh_unavailable") from None
                    await conn.execute("""UPDATE google_calendar_credentials SET connection_status='revoked',
                        last_error_code='authorization_revoked',updated_at=NOW()
                        WHERE organization_id=$1 AND connection_epoch=$2""", UUID(str(org_id)), row["connection_epoch"])
                    raise CalendarUnavailable("authorization_revoked") from None
                except Exception:
                    raise CalendarUnavailable("refresh_unavailable") from None
                refreshed_expiry = creds.expiry
                if not isinstance(creds.token, str) or not creds.token or refreshed_expiry is None:
                    raise CalendarUnavailable("invalid_token_response")
                if refreshed_expiry.tzinfo is None:
                    refreshed_expiry = refreshed_expiry.replace(tzinfo=timezone.utc)
                updated = await conn.fetchval("""UPDATE google_calendar_credentials SET access_token=$4,
                    token_expiry=$5,refresh_token=$6,credential_version=credential_version+1,updated_at=NOW()
                    WHERE organization_id=$1 AND connection_epoch=$2 AND credential_version=$3
                      AND connection_status='connected' AND sync_enabled RETURNING TRUE""",
                    UUID(str(org_id)), row["connection_epoch"], row["credential_version"],
                    self.fernet.encrypt(creds.token.encode()).decode(), refreshed_expiry,
                    self.fernet.encrypt(creds.refresh_token.encode()).decode())
                if not updated:
                    raise CalendarUnavailable("connection_changed")
            return creds, dict(row)
