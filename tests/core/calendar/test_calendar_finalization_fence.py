"""Final transaction CAS must roll back confirmation after its lease expires."""

import asyncio
from datetime import datetime, timezone
import uuid

import pytest

from src.core.calendar.connection import CalendarUnavailable
from src.core.calendar.lifecycle import CalendarLifecycle
from src.core.calendar.repository import CalendarQueue
from tests.core.calendar.test_calendar_provider_postgres import setup

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


async def _wait_until_confirmation_insert_waits(pool, timeout=4.0):
    """Wait until the verified transaction is blocked on the conversation FK."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with pool.acquire() as observer:
        while loop.time() < deadline:
            blocked = await observer.fetchval(
                """SELECT EXISTS(
                       SELECT 1 FROM pg_stat_activity
                       WHERE pid <> pg_backend_pid() AND state='active'
                         AND wait_event_type='Lock' AND query ILIKE '%INSERT INTO messages%'
                   )"""
            )
            if blocked:
                return
            await asyncio.sleep(0.02)
    raise AssertionError("Calendar finalization did not reach the message foreign-key wait")


@pytest.mark.asyncio
async def test_expired_lease_during_final_notice_insert_rolls_back_confirmation(
    pg_pool, sample_org, monkeypatch,
):
    org_id = sample_org["id"]
    service, _google, repo, _ = await setup(
        pg_pool, org_id, monkeypatch, "google_authoritative", create_booking=False,
    )
    contact_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    phone = "+15550008882"
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
            contact_id, org_id, phone,
        )
        await conn.execute(
            "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)",
            conversation_id, org_id, contact_id,
        )

    booking = await repo.create_booking(
        org_id, "Synthetic manual guest", "2099-05-10", "19:00", 2,
        telefono=phone,  # Existing contact, but deliberately no conversation link.
    )
    async with repo.slot_lock(org_id, booking["data"], booking["ora"]):
        await repo.request_google_confirmation(org_id, booking["id"])
    booking = await repo.get_booking(org_id, booking["id"])

    queue = CalendarQueue(pg_pool)
    [claim] = await queue.claim(organization_id=org_id, booking_id=booking["id"])
    assert claim["calendar_claimed_action"] == "authoritative"
    event_body = await service.event_body(booking, claim)
    payload_hash = event_body["extendedProperties"]["private"]["payload_hash"]
    assert await queue.dispatch(claim, payload_hash)
    assert await queue.availability_checked(claim, datetime.now(timezone.utc))
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """UPDATE external_booking_sync SET calendar_lease_until=clock_timestamp()+INTERVAL '2 seconds'
               WHERE organization_id=$1 AND id=$2 AND calendar_lease_token=$3 AND calendar_fence=$4""",
            org_id, claim["id"], claim["calendar_lease_token"], claim["calendar_fence"],
        )
        before = await conn.fetchrow(
            """SELECT b.stato,b.calendar_confirm_pending,b.google_event_id,b.calendar_revision,
                      s.sync_status,s.calendar_dispatch_state,s.calendar_applied_revision,
                      s.calendar_lease_token,s.calendar_verified_at
               FROM bookings b JOIN external_booking_sync s
                 ON s.organization_id=b.organization_id AND s.internal_booking_id=b.id AND s.provider='google'
               WHERE b.organization_id=$1 AND b.id=$2""",
            org_id, booking["id"],
        )
        counts_before = await conn.fetchrow(
            """SELECT
                 (SELECT count(*) FROM contacts WHERE organization_id=$1) AS contacts,
                 (SELECT count(*) FROM conversations WHERE organization_id=$1) AS conversations,
                 (SELECT count(*) FROM messages WHERE organization_id=$1) AS messages,
                 (SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1) AS outbox""",
            org_id,
        )

    async with pg_pool.acquire() as blocker:
        async with blocker.transaction():
            await blocker.fetchrow(
                "SELECT id FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE",
                org_id, conversation_id,
            )
            verified_task = asyncio.create_task(
                queue.verified(
                    claim, event_body["id"], "synthetic-etag",
                    CalendarLifecycle(service).finalize,
                )
            )
            await _wait_until_confirmation_insert_waits(pg_pool)
            await asyncio.sleep(2.2)
            # Exiting the transaction releases this lock and lets the message
            # FK check proceed to the final wall-clock lease CAS. Do not update
            # the queue row here.

    with pytest.raises(CalendarUnavailable, match="lease_or_proof_expired"):
        await asyncio.wait_for(verified_task, timeout=5)

    async with pg_pool.acquire() as conn:
        after = await conn.fetchrow(
            """SELECT b.stato,b.calendar_confirm_pending,b.google_event_id,b.calendar_revision,
                      s.sync_status,s.calendar_dispatch_state,s.calendar_applied_revision,
                      s.calendar_lease_token,s.calendar_verified_at
               FROM bookings b JOIN external_booking_sync s
                 ON s.organization_id=b.organization_id AND s.internal_booking_id=b.id AND s.provider='google'
               WHERE b.organization_id=$1 AND b.id=$2""",
            org_id, booking["id"],
        )
        counts_after = await conn.fetchrow(
            """SELECT
                 (SELECT count(*) FROM contacts WHERE organization_id=$1) AS contacts,
                 (SELECT count(*) FROM conversations WHERE organization_id=$1) AS conversations,
                 (SELECT count(*) FROM messages WHERE organization_id=$1) AS messages,
                 (SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1) AS outbox""",
            org_id,
        )
    assert after == before
    assert counts_after == counts_before
    assert after["stato"] == "in_attesa"
    assert after["calendar_confirm_pending"] is True
    assert after["google_event_id"] is None
    assert after["sync_status"] == "processing"
    assert after["calendar_dispatch_state"] == "possibly_sent"
    assert after["calendar_lease_token"] == claim["calendar_lease_token"]
    assert after["calendar_verified_at"] is None
