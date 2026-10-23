"""Fenced state machine on the existing external_booking_sync outbox."""
from uuid import UUID, uuid4
from contextlib import asynccontextmanager

from src.core.db.scoping import system_scope
from src.core.calendar.connection import CalendarUnavailable


class CalendarQueue:
    def __init__(self, pool):
        self.pool = pool

    @asynccontextmanager
    async def _write(self, org_id, *, lock_organization=False):
        async with self.pool.acquire() as conn, conn.transaction():
            if lock_organization:
                # A06 erasure locks the organization before its exclusive fence.
                await conn.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR SHARE", UUID(str(org_id)))
            await conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))", str(org_id))
            yield conn

    @system_scope("bounded Calendar worker; every claim carries its tenant and connection epoch")
    async def claim(self, limit=10, *, organization_id=None, booking_id=None):
        async with self.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch("""SELECT s.* FROM external_booking_sync s
                JOIN google_calendar_credentials c ON c.organization_id=s.organization_id
                WHERE s.provider='google' AND s.calendar_connection_epoch=c.connection_epoch
                  AND c.connection_status='connected' AND c.sync_enabled
                  AND ($2::uuid IS NULL OR s.organization_id=$2)
                  AND ($3::uuid IS NULL OR s.internal_booking_id=$3)
                  AND (s.sync_status='pending'
                    OR (s.sync_status IN ('pending_retry','uncertain') AND s.updated_at<NOW()-INTERVAL '30 seconds')
                    OR (s.sync_status='processing' AND s.calendar_lease_until<clock_timestamp()))
                  AND s.retry_count<20
                ORDER BY s.updated_at LIMIT $1 FOR UPDATE OF s SKIP LOCKED""",
                min(max(int(limit), 1), 50), UUID(str(organization_id)) if organization_id else None,
                UUID(str(booking_id)) if booking_id else None)
            claims = []
            for row in rows:
                # Never wait for erasure while holding a queue row it needs.
                if not await conn.fetchval("SELECT pg_try_advisory_xact_lock_shared(hashtextextended($1::text,606))",
                                           str(row["organization_id"])):
                    continue
                recovery = row["calendar_dispatch_state"] == "possibly_sent"
                token = uuid4()
                result = await conn.fetchrow("""UPDATE external_booking_sync SET
                    sync_status='processing',calendar_fence=calendar_fence+1,
                    calendar_lease_token=$3,calendar_lease_until=clock_timestamp()+INTERVAL '45 seconds',
                    calendar_claimed_revision=CASE WHEN $4 THEN calendar_claimed_revision ELSE calendar_desired_revision END,
                    calendar_claimed_action=CASE WHEN $4 THEN calendar_claimed_action ELSE calendar_action END,
                    calendar_payload_hash=CASE WHEN $4 THEN calendar_payload_hash ELSE NULL END,
                    calendar_availability_at=NULL,
                    calendar_dispatch_state=CASE WHEN $4 THEN 'possibly_sent' ELSE 'not_sent' END,
                    retry_count=retry_count+1,last_attempt_at=NOW(),updated_at=NOW()
                    WHERE organization_id=$1 AND id=$2 RETURNING *""",
                    row["organization_id"], row["id"], token, recovery)
                claims.append({**dict(result), "recovery": recovery})
            return claims

    async def current(self, claim, *, require_revision=True):
        async with self.pool.acquire() as conn:
            return bool(await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM external_booking_sync s
                JOIN google_calendar_credentials c ON c.organization_id=s.organization_id
                JOIN bookings b ON b.organization_id=s.organization_id AND b.id=s.internal_booking_id
                JOIN booking_settings bs ON bs.organization_id=s.organization_id
                WHERE s.organization_id=$1 AND s.id=$2 AND s.calendar_lease_token=$3
                  AND s.calendar_fence=$4 AND s.calendar_lease_until>clock_timestamp() AND s.sync_status='processing'
                  AND c.organization_id=$1 AND b.organization_id=$1 AND bs.organization_id=$1
                  AND c.connection_epoch=s.calendar_connection_epoch AND c.connection_status='connected' AND c.sync_enabled
                  AND (NOT $5 OR s.calendar_claimed_action='delete' OR bs.config#>>'{a05,authority}'=
                      CASE WHEN s.calendar_authoritative THEN 'google_authoritative' ELSE 'internal_calendar_sync' END)
                  AND (NOT $5 OR b.calendar_revision=s.calendar_claimed_revision))""",
                claim["organization_id"], claim["id"], claim["calendar_lease_token"], claim["calendar_fence"], require_revision))

    async def dispatch(self, claim, payload_hash):
        # Persist BEFORE a mutation, never after. Expiry cannot turn this into not_sent.
        async with self._write(claim["organization_id"]) as conn:
            # Durable tenant mutex survives loss of the worker's advisory-lock
            # connection. An unresolved earlier HTTP write blocks another write.
            await conn.fetchrow("SELECT organization_id FROM google_calendar_credentials WHERE organization_id=$1 FOR UPDATE",
                                claim["organization_id"])
            return bool(await conn.fetchval("""UPDATE external_booking_sync s SET
                calendar_dispatch_state='possibly_sent',calendar_payload_hash=$5,updated_at=NOW()
                FROM bookings b JOIN google_calendar_credentials c ON c.organization_id=b.organization_id
                  JOIN booking_settings bs ON bs.organization_id=b.organization_id
                WHERE s.organization_id=$1 AND s.id=$2 AND s.calendar_lease_token=$3 AND s.calendar_fence=$4
                  AND s.calendar_lease_until>clock_timestamp() AND s.sync_status='processing'
                  AND b.id=s.internal_booking_id AND b.organization_id=$1 AND c.organization_id=$1 AND bs.organization_id=$1
                  AND b.calendar_revision=s.calendar_claimed_revision
                  AND c.organization_id=$1 AND c.connection_epoch=s.calendar_connection_epoch
                  AND c.connection_status='connected' AND c.sync_enabled
                  AND bs.organization_id=s.organization_id AND (s.calendar_claimed_action='delete' OR bs.config#>>'{a05,authority}'=
                      CASE WHEN s.calendar_authoritative THEN 'google_authoritative' ELSE 'internal_calendar_sync' END)
                  AND NOT EXISTS(SELECT 1 FROM external_booking_sync other
                      WHERE other.organization_id=$1 AND other.provider='google' AND other.id<>s.id
                        AND other.calendar_dispatch_state='possibly_sent')
                RETURNING TRUE""", claim["organization_id"], claim["id"], claim["calendar_lease_token"],
                claim["calendar_fence"], payload_hash))

    async def fail(self, claim, code, *, uncertain=False, blocked=False):
        async with self._write(claim["organization_id"]) as conn:
            await conn.execute("""UPDATE external_booking_sync SET sync_status=$5,sync_error=$6,
                calendar_lease_token=NULL,calendar_lease_until=NULL,updated_at=NOW()
                WHERE organization_id=$1 AND id=$2 AND calendar_lease_token=$3 AND calendar_fence=$4""",
                claim["organization_id"], claim["id"], claim["calendar_lease_token"], claim["calendar_fence"],
                "uncertain" if uncertain else ("blocked" if blocked else "pending_retry"), code)

    async def verified(self, claim, event_id, etag, finalize=None):
        """Book lock precedes queue lock, matching the booking trigger's order."""
        async with self._write(claim["organization_id"], lock_organization=True) as conn:
            # Disconnect/refresh/settings take the credential row before queue
            # rows. Share that order, then follow A05's conversation/capacity locks.
            await conn.fetchrow("SELECT organization_id FROM google_calendar_credentials WHERE organization_id=$1 FOR UPDATE",
                                claim["organization_id"])
            before = await conn.fetchrow("SELECT id_conversazione,data,ora FROM bookings WHERE organization_id=$1 AND id=$2",
                                        claim["organization_id"], claim["internal_booking_id"])
            if not before:
                return False
            if before["id_conversazione"]:
                await conn.fetchval("SELECT id FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE",
                                    claim["organization_id"], UUID(str(before["id_conversazione"])))
            await conn.execute("SELECT pg_advisory_xact_lock_shared(hashtext($1))", f"booking-capacity|{claim['organization_id']}")
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                               f"{claim['organization_id']}|{before['data'].isoformat()}|{before['ora'].hour}")
            booking = await conn.fetchrow("SELECT * FROM bookings WHERE organization_id=$1 AND id=$2 FOR UPDATE",
                                          claim["organization_id"], claim["internal_booking_id"])
            if not booking or (booking["data"], booking["ora"]) != (before["data"], before["ora"]):
                return False
            row = await conn.fetchrow("""UPDATE external_booking_sync s SET
                sync_status=CASE WHEN calendar_desired_revision=calendar_claimed_revision THEN 'succeeded' ELSE 'pending' END,
                calendar_applied_revision=calendar_claimed_revision,calendar_dispatch_state='verified',
                external_booking_id=$5,calendar_event_etag=$6,calendar_payload_hash=COALESCE($7,calendar_payload_hash),
                calendar_verified_at=clock_timestamp(),sync_error=NULL,updated_at=NOW()
                FROM google_calendar_credentials c
                WHERE s.organization_id=$1 AND s.id=$2 AND s.calendar_lease_token=$3 AND s.calendar_fence=$4
                  AND s.calendar_lease_until>clock_timestamp() AND s.sync_status='processing'
                  AND (s.calendar_claimed_action<>'authoritative' OR s.calendar_desired_revision<>s.calendar_claimed_revision
                      OR (s.calendar_availability_at>clock_timestamp()-INTERVAL '30 seconds' AND s.calendar_availability_at<=clock_timestamp()))
                  AND c.organization_id=$1 AND c.connection_epoch=s.calendar_connection_epoch
                  AND c.connection_status='connected' AND c.sync_enabled RETURNING s.*""",
                claim["organization_id"], claim["id"], claim["calendar_lease_token"], claim["calendar_fence"], event_id, etag,
                claim.get("calendar_payload_hash"))
            if not row or not booking:
                return False
            # A cancellation during HTTP is preserved. Its newer desired delete remains pending.
            await conn.execute("UPDATE bookings SET google_event_id=$3 WHERE organization_id=$1 AND id=$2",
                               claim["organization_id"], booking["id"],
                               None if claim["calendar_claimed_action"] == "delete" else event_id)
            if finalize and row["sync_status"] == "succeeded" and claim["calendar_claimed_action"] == "authoritative":
                await finalize(conn, dict(booking), dict(row))
            await conn.execute("""UPDATE google_calendar_credentials SET last_sync_at=NOW(),last_error_code=NULL
                WHERE organization_id=$1 AND connection_epoch=$2""", claim["organization_id"], claim["calendar_connection_epoch"])
            # Last statement before COMMIT: notification/FK locks may have
            # delayed us after the first proof check. Roll back every local
            # confirmation/outbox write if ownership or freshness expired.
            finished = await conn.fetchval("""UPDATE external_booking_sync s
                SET calendar_lease_token=NULL,calendar_lease_until=NULL
                WHERE s.organization_id=$1 AND s.id=$2 AND s.calendar_lease_token=$3 AND s.calendar_fence=$4
                  AND s.calendar_lease_until>clock_timestamp()
                  AND (s.calendar_claimed_action<>'authoritative'
                    OR NOT EXISTS(SELECT 1 FROM bookings b WHERE b.organization_id=$1 AND b.id=s.internal_booking_id
                        AND b.stato='confermata' AND b.calendar_revision=s.calendar_claimed_revision)
                    OR (s.calendar_availability_at>clock_timestamp()-INTERVAL '30 seconds'
                        AND s.calendar_availability_at<=clock_timestamp())) RETURNING TRUE""",
                claim["organization_id"], claim["id"], claim["calendar_lease_token"], claim["calendar_fence"])
            if not finished:
                raise CalendarUnavailable("lease_or_proof_expired")
            return True

    async def renew(self, claim):
        async with self._write(claim["organization_id"]) as conn:
            return bool(await conn.fetchval("""UPDATE external_booking_sync SET calendar_lease_until=clock_timestamp()+INTERVAL '45 seconds'
                WHERE organization_id=$1 AND id=$2 AND calendar_lease_token=$3 AND calendar_fence=$4
                  AND calendar_lease_until>clock_timestamp() AND sync_status='processing' RETURNING TRUE""",
                claim["organization_id"], claim["id"], claim["calendar_lease_token"], claim["calendar_fence"]))

    async def availability_checked(self, claim, checked_at):
        async with self._write(claim["organization_id"]) as conn:
            return bool(await conn.fetchval("""UPDATE external_booking_sync SET calendar_availability_at=$5
                WHERE organization_id=$1 AND id=$2 AND calendar_lease_token=$3 AND calendar_fence=$4
                  AND calendar_lease_until>clock_timestamp() AND sync_status='processing' RETURNING TRUE""",
                claim["organization_id"], claim["id"], claim["calendar_lease_token"], claim["calendar_fence"], checked_at))

    async def retry(self, org_id, booking_id):
        # An owner's retry never changes an uncertain dispatch into a fresh POST.
        async with self._write(org_id) as conn:
            return bool(await conn.fetchval("""UPDATE external_booking_sync s SET
                sync_status=CASE WHEN calendar_dispatch_state='possibly_sent' THEN 'uncertain' ELSE 'pending' END,
                retry_count=0,updated_at=NOW()-INTERVAL '31 seconds',
                calendar_connection_epoch=CASE WHEN calendar_dispatch_state='not_sent' THEN c.connection_epoch ELSE s.calendar_connection_epoch END,
                calendar_id=CASE WHEN calendar_dispatch_state='not_sent' THEN c.calendar_id ELSE s.calendar_id END
                FROM google_calendar_credentials c WHERE s.organization_id=$1 AND s.internal_booking_id=$2
                  AND s.provider='google' AND c.organization_id=$1
                  AND c.connection_status='connected' AND c.sync_enabled
                  AND s.sync_status IN ('blocked','pending_retry','uncertain','failed')
                  AND (s.calendar_dispatch_state='not_sent' OR s.calendar_connection_epoch=c.connection_epoch)
                RETURNING TRUE""", UUID(str(org_id)), UUID(str(booking_id))))

    async def reconnect_receipt(self, old, current_epoch, event):
        """Adopt only a correlated GET receipt after explicit owner retry.

        The service verified the old payload, epoch, ownership, modelled fields
        and ETag against Google using the current credentials. Absence is not a
        receipt. No request body or claimed receipt comes from an API client.
        """
        async with self._write(old["organization_id"], lock_organization=True) as conn:
            await conn.fetchrow("SELECT organization_id FROM google_calendar_credentials WHERE organization_id=$1 FOR UPDATE",
                                old["organization_id"])
            return bool(await conn.fetchval("""UPDATE external_booking_sync s SET
                calendar_connection_epoch=$3,calendar_dispatch_state='verified',calendar_payload_hash=NULL,
                calendar_applied_revision=calendar_claimed_revision,calendar_verified_at=clock_timestamp(),
                external_booking_id=$6,calendar_event_etag=$7,sync_status='pending',sync_error=NULL,
                calendar_fence=calendar_fence+1,calendar_lease_token=NULL,calendar_lease_until=NULL,retry_count=0,updated_at=NOW()
                FROM google_calendar_credentials c WHERE s.organization_id=$1 AND s.id=$2 AND c.organization_id=$1
                  AND c.connection_epoch=$3 AND c.connection_status='connected' AND c.sync_enabled
                  AND s.calendar_connection_epoch=$4 AND s.calendar_connection_epoch<>$3
                  AND s.calendar_fence=$5 AND s.calendar_payload_hash=$8 AND c.calendar_id=s.calendar_id
                  AND s.calendar_dispatch_state IN ('possibly_sent','verified')
                  AND s.sync_status IN ('blocked','uncertain','failed','pending_retry','succeeded')
                RETURNING TRUE""", old["organization_id"], old["id"], current_epoch, old["calendar_connection_epoch"],
                old["calendar_fence"], event["id"], event["etag"], old["calendar_payload_hash"]))
