"""Single Calendar writer: fences, explicit uncertainty and verified finalization."""
import asyncio
from contextlib import suppress
import json
from uuid import UUID

from googleapiclient.errors import HttpError

from src.core.calendar.availability import AvailabilityStatus, aware_utc
from src.core.calendar.connection import CalendarUnavailable, authority_from


class CalendarLifecycle:
    def __init__(self, service):
        self.service, self.queue = service, service.queue

    async def heartbeat(self, claim):
        while True:
            await asyncio.sleep(10)
            if not await self.queue.renew(claim):
                return

    async def process(self, limit, **scope):
        claims = await self.queue.claim(limit, **scope)
        for claim in claims:
            heartbeat = asyncio.create_task(self.heartbeat(claim))
            try:
                # One network cycle per tenant, including optional and authoritative writers.
                async with self.service.repo.pool.acquire() as conn:
                    key = f"calendar-worker:{claim['organization_id']}"
                    locked = await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1,605))", key)
                    if not locked:
                        await self.queue.fail(claim, "worker_busy", uncertain=claim["recovery"])
                        continue
                    try:
                        await self.run(claim)
                    finally:
                        if not conn.is_closed():
                            await asyncio.shield(conn.execute("SELECT pg_advisory_unlock(hashtextextended($1,605))", key))
            except asyncio.CancelledError:
                # A potentially dispatched request is left durable for GET-only recovery.
                raise
            except HttpError as exc:
                reasons = set()
                try:
                    payload = json.loads(exc.content)
                    reasons = {item.get("reason") for item in payload.get("error", {}).get("errors", [])}
                except (ValueError, TypeError, AttributeError):
                    pass
                unauthorized = exc.resp.status == 401 or (exc.resp.status == 403 and
                    bool(reasons & {"insufficientPermissions", "forbidden", "authError"}))
                if unauthorized:
                    await self.service.connection.mark_error(claim["organization_id"], claim["calendar_connection_epoch"],
                                                             "authorization_unavailable", reconnect=True)
                await self.queue.fail(claim, "authorization_unavailable" if unauthorized else "provider_unavailable",
                    uncertain=claim.get("dispatched", claim["recovery"]), blocked=unauthorized)
            except Exception:
                await self.queue.fail(claim, "provider_unavailable", uncertain=claim.get("dispatched", claim["recovery"]))
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
        return len(claims)

    async def run(self, claim):
        s = self.service
        if not await self.queue.current(claim, require_revision=not claim["recovery"]):
            await self.queue.fail(claim, "context_changed", uncertain=claim["recovery"], blocked=not claim["recovery"])
            return
        async with s.repo.pool.acquire() as conn:
            booking = await conn.fetchrow("SELECT * FROM bookings WHERE organization_id=$1 AND id=$2",
                                          claim["organization_id"], claim["internal_booking_id"])
        connection = await s.connection.row(claim["organization_id"])
        try:
            provider = await s._build_service(claim["organization_id"], claim["calendar_connection_epoch"])
        except CalendarUnavailable as exc:
            await self.queue.fail(claim, exc.code, uncertain=claim["recovery"], blocked=not claim["recovery"])
            return
        event = await s.read_event(provider, claim)
        action = claim["calendar_claimed_action"]
        if event is not None and not s.owned(event, claim):
            await self.queue.fail(claim, "event_ownership_conflict", uncertain=claim["recovery"], blocked=not claim["recovery"])
            return
        if claim["recovery"]:
            if action == "delete" and (event is None or event.get("status") == "cancelled"):
                await self.queue.verified(claim, None, None)
                return
            if not event or not s.matches(event, claim, claim["calendar_payload_hash"]):
                # 404 does not prove that the old request will never arrive.
                await self.queue.fail(claim, "outcome_unverified", uncertain=True)
                return
            if action == "authoritative" and not await self.verify_slot(provider, claim, connection, event):
                return
            await self.queue.verified(claim, event["id"], event["etag"], self.finalize)
            return
        if action == "delete":
            if event is None or event.get("status") == "cancelled":
                await self.queue.verified(claim, None, None)
                return
            if not event.get("etag"):
                await self.queue.fail(claim, "event_etag_missing", blocked=True)
                return
            request = provider.events().delete(calendarId=claim["calendar_id"], eventId=event["id"])
            request.headers["If-Match"] = event["etag"]
            if not await self.queue.dispatch(claim, "delete"):
                return
            claim["dispatched"] = True
            await s.execute(request)
            observed = await s.read_event(provider, claim)
            if observed and observed.get("status") != "cancelled":
                await self.queue.fail(claim, "delete_unverified", uncertain=True)
                return
            await self.queue.verified(claim, None, None)
            return
        body = await s.event_body(dict(booking), claim)
        digest = body["extendedProperties"]["private"]["payload_hash"]
        claim["calendar_payload_hash"] = digest
        if action == "authoritative" and not await self.verify_slot(provider, claim, connection, event, body):
            return
        if event and event.get("status") == "cancelled":
            # Stable IDs are tombstoned. Never resurrect a cancellation on retry.
            await self.queue.fail(claim, "event_cancelled", blocked=True)
            return
        if not event or not s.matches(event, claim, digest):
            if event and not event.get("etag"):
                await self.queue.fail(claim, "event_etag_missing", blocked=True)
                return
            if event:
                request = provider.events().update(calendarId=claim["calendar_id"], eventId=event["id"], body=body)
                request.headers["If-Match"] = event["etag"]
            else:
                request = provider.events().insert(calendarId=claim["calendar_id"], body=body)
            if not await self.queue.dispatch(claim, digest):
                return
            claim["dispatched"] = True
            try:
                await s.execute(request)
            except HttpError as exc:
                if exc.resp.status not in (409, 412):
                    raise
                # Even a conflict is reconciled by reading the exact desired version.
            event = await s.read_event(provider, claim)
        if not event or not s.matches(event, claim, digest):
            await self.queue.fail(claim, "mutation_unverified", uncertain=claim.get("dispatched", False), blocked=True)
            return
        if action == "authoritative" and not await self.verify_slot(provider, claim, connection, event, body, allow_superseded=True):
            return
        await self.queue.verified(claim, event["id"], event["etag"], self.finalize)

    async def verify_slot(self, provider, claim, connection, event, body=None, *, allow_superseded=False):
        candidate = body or event
        start, end = aware_utc(candidate["start"]["dateTime"]), aware_utc(candidate["end"]["dateTime"])
        # A GET recovery can encounter our own event: exclude only that exact owned event.
        result = await self.service.availability_excluding_event(
            claim["organization_id"], start, end, connection=connection, service=provider,
            event_id=event["id"] if event else None,
        )
        if result.status != AvailabilityStatus.FREE or result.checked_at is None:
            await self.queue.fail(claim, "slot_busy" if result.status == AvailabilityStatus.BUSY else "availability_unknown",
                                  uncertain=claim["recovery"], blocked=not claim["recovery"])
            return False
        if not await self.queue.availability_checked(claim, result.checked_at):
            return False
        return await self.queue.current(claim, require_revision=not (claim["recovery"] or allow_superseded))

    async def finalize(self, conn, booking, sync):
        """No network: atomically revalidate intent, local capacity, policy and proof."""
        if (not booking["calendar_confirm_pending"] or booking["stato"] != "in_attesa"
                or booking["calendar_revision"] != sync["calendar_applied_revision"]):
            return
        org = booking["organization_id"]
        config = await conn.fetchrow("SELECT config,capienze_orarie FROM booking_settings WHERE organization_id=$1", org)
        if authority_from(dict(config) if config else None) != "google_authoritative":
            return
        capacities = config["capienze_orarie"] or {}
        if isinstance(capacities, str):
            capacities = json.loads(capacities)
        capacity = capacities.get(f"{booking['ora'].hour:02d}:00", 40)
        used = await conn.fetchval("""SELECT COALESCE(SUM(coperti),0) FROM bookings WHERE organization_id=$1
            AND data=$2 AND EXTRACT(HOUR FROM ora)=$3 AND id<>$4
            AND stato IN ('in_attesa','confermata','da_verificare','completata')""", org, booking["data"], booking["ora"].hour, booking["id"])
        valid = type(capacity) is int and int(used) + booking["coperti"] <= capacity
        organization = await conn.fetchrow("SELECT timezone,business_profile FROM organizations WHERE id=$1 FOR SHARE", org)
        expected = self.service.event_body_for(booking, sync, organization["timezone"])
        valid = valid and expected["extendedProperties"]["private"]["payload_hash"] == sync["calendar_payload_hash"]
        if sync["calendar_inbound_message_id"]:
            latest = await conn.fetchval("""SELECT id FROM messages WHERE organization_id=$1 AND conversation_id=$2
                AND direction='inbound' ORDER BY created_at DESC,id DESC LIMIT 1""", org, UUID(str(booking["id_conversazione"])))
            from src.core.bookings.auto_policy import parse_and_validate_auto_booking
            root = json.loads(config["config"]) if isinstance(config["config"], str) else config["config"]
            profile = organization["business_profile"] or {}
            if isinstance(profile, str):
                profile = json.loads(profile)
            decision = parse_and_validate_auto_booking({"config": root, "capienze_orarie": capacities}, organization["timezone"],
                {"service": "ristorante/tavolo", "date": booking["data"].isoformat(), "time": booking["ora"].strftime("%H:%M"),
                 "party_size": booking["coperti"], "duration_minutes": 60})
            valid = valid and latest == sync["calendar_inbound_message_id"] and decision.accepted and profile.get("verticale") == "ristorante"
        if not valid:
            # Remove an event obtained for a superseded intent. Never confirm it.
            await conn.execute("""UPDATE bookings SET stato='da_verificare',calendar_confirm_pending=FALSE,
                richiede_intervento=TRUE,updated_at=NOW() WHERE organization_id=$1 AND id=$2""", org, booking["id"])
            await conn.execute("""UPDATE external_booking_sync SET calendar_action='delete',sync_status='pending',
                sync_error='confirmation_revalidation_failed' WHERE organization_id=$1 AND id=$2""", org, sync["id"])
            return
        updated = dict(await conn.fetchrow("""UPDATE bookings SET stato='confermata',calendar_confirm_pending=FALSE,
            richiede_intervento=FALSE,updated_at=NOW() WHERE organization_id=$1 AND id=$2 RETURNING *""", org, booking["id"]))
        if sync["calendar_inbound_message_id"] or sync["calendar_notification_channel"]:
            from src.core.db.repositories.booking_repo import BookingRepository
            await BookingRepository(self.service.repo.pool).enqueue_calendar_confirmation(
                conn, updated, sync["calendar_inbound_message_id"], sync["calendar_notification_channel"])
