"""PostgreSQL contracts for migration 064 Calendar queue lifecycle.

These tests exercise trigger and queue SQL with synthetic rows only. They do
not construct a Google provider or send requests to Google.
"""

import asyncio
from uuid import uuid4

import pytest

from src.core.calendar.repository import CalendarQueue

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


async def _set_authority(pool, organization_id, authority):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO booking_settings(organization_id,config)
               VALUES($1,jsonb_build_object('a05',jsonb_build_object('authority',$2::text)))
               ON CONFLICT(organization_id) DO UPDATE SET config=EXCLUDED.config""",
            organization_id,
            authority,
        )


async def _connect_calendar(pool, organization_id, *, epoch=None):
    epoch = epoch or uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO google_calendar_credentials
               (organization_id,connection_epoch,connection_status,sync_enabled,calendar_id)
               VALUES($1,$2,'connected',TRUE,'primary')""",
            organization_id,
            epoch,
        )
    return epoch


async def _booking(pool, organization_id, *, status="in_attesa"):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO bookings
               (organization_id,nome_cliente,telefono,data,ora,coperti,stato)
               VALUES($1,'Calendar lifecycle test','',CURRENT_DATE+5,'19:00',2,$2)
               RETURNING id""",
            organization_id,
            status,
        )


async def _ledger(pool, organization_id, booking_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT * FROM external_booking_sync
               WHERE organization_id=$1 AND internal_booking_id=$2 AND provider='google'""",
            organization_id,
            booking_id,
        )


async def test_optional_mode_reuses_existing_external_booking_sync_ledger(
    pg_pool, sample_org,
):
    org_id = sample_org["id"]
    await _set_authority(pg_pool, org_id, "internal_calendar_sync")
    await _connect_calendar(pg_pool, org_id)
    booking_id = await _booking(pg_pool, org_id)
    first = await _ledger(pg_pool, org_id, booking_id)
    assert first["sync_status"] == "pending"
    assert first["calendar_action"] == "upsert"

    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE bookings SET note='changed' WHERE organization_id=$1 AND id=$2",
            org_id,
            booking_id,
        )
        row = await conn.fetchrow(
            """SELECT s.*,b.calendar_revision FROM external_booking_sync s
               JOIN bookings b ON b.organization_id=s.organization_id AND b.id=s.internal_booking_id
               WHERE s.organization_id=$1 AND s.internal_booking_id=$2 AND s.provider='google'""",
            org_id,
            booking_id,
        )
        count = await conn.fetchval(
            "SELECT count(*) FROM external_booking_sync WHERE organization_id=$1 AND internal_booking_id=$2 AND provider='google'",
            org_id,
            booking_id,
        )
    assert count == 1
    assert row["id"] == first["id"]
    assert row["calendar_desired_revision"] == row["calendar_revision"]


async def test_internal_mode_without_google_does_not_enqueue_calendar(
    pg_pool, sample_org,
):
    org_id = sample_org["id"]
    await _set_authority(pg_pool, org_id, "internal")
    booking_id = await _booking(pg_pool, org_id)
    assert await _ledger(pg_pool, org_id, booking_id) is None


@pytest.mark.parametrize("operation", ["insert", "update"])
async def test_google_authoritative_rejects_direct_confirmed_insert_and_update(
    pg_pool, sample_org, operation,
):
    org_id = sample_org["id"]
    await _set_authority(pg_pool, org_id, "google_authoritative")
    await _connect_calendar(pg_pool, org_id)

    async with pg_pool.acquire() as conn:
        if operation == "insert":
            query = """INSERT INTO bookings
                (organization_id,nome_cliente,telefono,data,ora,coperti,stato)
                VALUES($1,'Direct insert','',CURRENT_DATE+5,'19:00',2,'confermata')"""
            args = (org_id,)
        else:
            booking_id = await conn.fetchval(
                """INSERT INTO bookings
                   (organization_id,nome_cliente,telefono,data,ora,coperti,stato)
                   VALUES($1,'Direct update','',CURRENT_DATE+5,'19:00',2,'in_attesa') RETURNING id""",
                org_id,
            )
            query = "UPDATE bookings SET stato='confermata' WHERE organization_id=$1 AND id=$2"
            args = (org_id, booking_id)

        with pytest.raises(Exception, match="Calendar authority requires verified finalization"):
            await conn.execute(query, *args)

    if operation == "update":
        async with pg_pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2", org_id, booking_id
            ) == "in_attesa"


async def test_cancellation_during_processing_advances_desired_delete_revision(
    pg_pool, sample_org,
):
    org_id = sample_org["id"]
    await _set_authority(pg_pool, org_id, "internal_calendar_sync")
    await _connect_calendar(pg_pool, org_id)
    booking_id = await _booking(pg_pool, org_id)
    queue = CalendarQueue(pg_pool)
    [claim] = await queue.claim(booking_id=booking_id)

    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE bookings SET stato='cancellata' WHERE organization_id=$1 AND id=$2",
            org_id,
            booking_id,
        )
        row = await conn.fetchrow(
            "SELECT * FROM external_booking_sync WHERE organization_id=$1 AND id=$2",
            org_id,
            claim["id"],
        )
    assert row["sync_status"] == "processing"
    assert row["calendar_claimed_action"] == "upsert"
    assert row["calendar_action"] == "delete"
    assert row["calendar_desired_revision"] > row["calendar_claimed_revision"]


async def test_expired_possibly_sent_claim_recovers_same_revision_and_hash_and_fences_old_claim(
    pg_pool, sample_org,
):
    org_id = sample_org["id"]
    await _set_authority(pg_pool, org_id, "internal_calendar_sync")
    await _connect_calendar(pg_pool, org_id)
    booking_id = await _booking(pg_pool, org_id)
    queue = CalendarQueue(pg_pool)
    [original] = await queue.claim(booking_id=booking_id)
    assert await queue.dispatch(original, "stable-payload-hash") is True

    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE bookings SET note='new desired revision' WHERE organization_id=$1 AND id=$2",
            org_id,
            booking_id,
        )
        await conn.execute(
            """UPDATE external_booking_sync SET calendar_lease_until=NOW()-INTERVAL '1 second'
               WHERE organization_id=$1 AND id=$2""",
            org_id,
            original["id"],
        )

    [recovered] = await queue.claim(booking_id=booking_id)
    assert recovered["recovery"] is True
    assert recovered["calendar_claimed_revision"] == original["calendar_claimed_revision"]
    assert recovered["calendar_claimed_action"] == original["calendar_claimed_action"]
    assert recovered["calendar_payload_hash"] == "stable-payload-hash"
    assert recovered["calendar_dispatch_state"] == "possibly_sent"
    assert recovered["calendar_fence"] > original["calendar_fence"]
    assert recovered["calendar_lease_token"] != original["calendar_lease_token"]
    assert await queue.verified(original, "event-old", "etag-old") is False

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM external_booking_sync WHERE organization_id=$1 AND id=$2",
            org_id,
            original["id"],
        )
    assert row["calendar_lease_token"] == recovered["calendar_lease_token"]
    assert row["calendar_payload_hash"] == "stable-payload-hash"
    assert row["sync_status"] == "processing"


async def test_calendar_queue_compare_and_set_denies_cross_tenant_claim(
    pg_pool, sample_org, other_org,
):
    org_id = sample_org["id"]
    other_id = other_org["id"]
    await _set_authority(pg_pool, org_id, "internal_calendar_sync")
    await _connect_calendar(pg_pool, org_id)
    booking_id = await _booking(pg_pool, org_id)
    queue = CalendarQueue(pg_pool)
    [claim] = await queue.claim(booking_id=booking_id)

    foreign_claim = {**claim, "organization_id": other_id}
    assert await queue.current(foreign_claim) is False
    assert await queue.dispatch(foreign_claim, "cross-tenant-payload") is False
    assert await queue.verified(foreign_claim, "cross-tenant-event", "etag") is False

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT sync_status,calendar_dispatch_state FROM external_booking_sync WHERE organization_id=$1 AND id=$2",
            org_id,
            claim["id"],
        )
    assert dict(row) == {"sync_status": "processing", "calendar_dispatch_state": "not_sent"}


async def test_concurrent_calendar_claims_have_one_worker_owner(pg_pool, sample_org):
    org_id = sample_org["id"]
    await _set_authority(pg_pool, org_id, "internal_calendar_sync")
    await _connect_calendar(pg_pool, org_id)
    booking_id = await _booking(pg_pool, org_id)
    queue = CalendarQueue(pg_pool)

    first, second = await asyncio.gather(
        queue.claim(limit=1, booking_id=booking_id),
        queue.claim(limit=1, booking_id=booking_id),
    )
    claims = first + second
    assert len(claims) == 1
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT sync_status,calendar_fence FROM external_booking_sync WHERE organization_id=$1 AND internal_booking_id=$2",
            org_id,
            booking_id,
        )
    assert row["sync_status"] == "processing"
    assert row["calendar_fence"] == 1


async def test_optional_to_internal_then_cancel_queues_delete_on_same_ledger(
    pg_pool, sample_org,
):
    org_id = sample_org["id"]
    await _set_authority(pg_pool, org_id, "internal_calendar_sync")
    await _connect_calendar(pg_pool, org_id)
    booking_id = await _booking(pg_pool, org_id)
    original = await _ledger(pg_pool, org_id, booking_id)
    assert original["calendar_action"] == "upsert"

    await _set_authority(pg_pool, org_id, "internal")
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE bookings SET stato='cancellata' WHERE organization_id=$1 AND id=$2",
            org_id,
            booking_id,
        )
        row = await conn.fetchrow(
            """SELECT s.*,b.calendar_revision FROM external_booking_sync s
               JOIN bookings b ON b.organization_id=s.organization_id AND b.id=s.internal_booking_id
               WHERE s.organization_id=$1 AND s.internal_booking_id=$2 AND s.provider='google'""",
            org_id,
            booking_id,
        )
        count = await conn.fetchval(
            "SELECT count(*) FROM external_booking_sync WHERE organization_id=$1 AND internal_booking_id=$2 AND provider='google'",
            org_id,
            booking_id,
        )
    assert count == 1
    assert row["id"] == original["id"]
    assert row["calendar_action"] == "delete"
    assert row["calendar_desired_revision"] == row["calendar_revision"]
    assert row["sync_status"] == "pending"


async def test_tenant_write_mutex_survives_worker_connection_loss(pg_pool, sample_org):
    org = sample_org["id"]
    await _set_authority(pg_pool, org, "internal_calendar_sync")
    await _connect_calendar(pg_pool, org)
    first_id, second_id = await _booking(pg_pool, org), await _booking(pg_pool, org)
    queue = CalendarQueue(pg_pool)
    [first] = await queue.claim(organization_id=org, booking_id=first_id)
    [second] = await queue.claim(organization_id=org, booking_id=second_id)
    results = await asyncio.gather(queue.dispatch(first, "first"), queue.dispatch(second, "second"))
    assert sorted(results) == [False, True]
    winner, loser = (first, second) if results[0] else (second, first)
    # Session locks are intentionally absent: the durable marker alone blocks
    # another HTTP write until the first outcome has a verified receipt.
    assert not await queue.dispatch(loser, "still-blocked")
    assert await queue.verified(winner, "observed-event", "verified-etag")
    assert await queue.dispatch(loser, "now-safe")


async def test_waiting_transaction_cannot_finalize_after_wall_clock_lease_expiry(pg_pool, sample_org):
    org = sample_org["id"]
    await _set_authority(pg_pool, org, "internal_calendar_sync")
    await _connect_calendar(pg_pool, org)
    booking_id = await _booking(pg_pool, org)
    queue = CalendarQueue(pg_pool)
    [claim] = await queue.claim(organization_id=org, booking_id=booking_id)
    assert await queue.dispatch(claim, "dispatched")
    async with pg_pool.acquire() as blocker, blocker.transaction():
        await blocker.fetchrow("SELECT id FROM bookings WHERE organization_id=$1 AND id=$2 FOR UPDATE", org, booking_id)
        pending = asyncio.create_task(queue.verified(claim, "too-late-event", "etag"))
        await asyncio.sleep(0.08)
        assert not pending.done()
        # Later than the waiting transaction's NOW(), already past on the wall
        # clock: using transaction time would incorrectly accept this lease.
        await blocker.execute("""UPDATE external_booking_sync SET calendar_lease_until=clock_timestamp()-INTERVAL '1 millisecond'
            WHERE organization_id=$1 AND id=$2""", org, claim["id"])
    assert await asyncio.wait_for(pending, timeout=3) is False
