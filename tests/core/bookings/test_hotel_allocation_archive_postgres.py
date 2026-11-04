"""Owner-only hotel occupancy archive stays dormant until explicitly invoked."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from src.core.bookings.booking_mutation_contract import BookingMutation, apply_booking_mutation
from tests.core.bookings.test_booking_stay_lifecycle_postgres import _approve_retention, _create_stay
from tests.core.bookings.test_booking_stays_postgres import hotel_setup

pytestmark = pytest.mark.asyncio


async def _complete_with_old_evidence(pool):
    env, lineage = await hotel_setup(pool)
    booking = await _create_stay(env, lineage, arrival='2024-01-10', departure='2024-01-11')
    await apply_booking_mutation(pool, env.org, env.auth,
        env.request(booking, action='replace', allocation_revision=1,
                    arrival_date='2024-01-10', departure_date='2024-01-11'),
        mutation=BookingMutation('complete', '', '', ''))
    # Synthetic owner-only fixture simulates an evidence record observed in the
    # past; production evidence is protected by the immutable trigger.
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE booking_stay_completion_evidence DISABLE TRIGGER completion_evidence_immutable')
        await conn.execute("UPDATE booking_stay_completion_evidence SET completed_at='2024-01-12 12:00Z' WHERE organization_id=$1 AND booking_id=$2", env.org, booking)
        await conn.execute('ALTER TABLE booking_stay_completion_evidence ENABLE TRIGGER completion_evidence_immutable')
    await _approve_retention(pool, env.org)
    return env, lineage, booking


async def _archive(pool, env, booking):
    return await pool.fetchval('SELECT public.archive_completed_hotel_allocation($1,$2)', env.org, booking)


async def test_archive_releases_occupancy_preserving_history_and_is_idempotent(pg_pool):
    env, _, booking = await _complete_with_old_evidence(pg_pool)
    assert await _archive(pg_pool, env, booking) is True
    assert await _archive(pg_pool, env, booking) is False
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'completata'
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 2
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    assert await pg_pool.fetchval('SELECT state FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 'cancelled'


async def test_archive_rejects_noncompleted_stay(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = await _create_stay(env, lineage, arrival='2024-01-10', departure='2024-01-11')
    await _approve_retention(pg_pool, env.org)
    with pytest.raises(Exception, match='archive evidence unavailable'):
        await _archive(pg_pool, env, booking)


async def test_archive_rejects_future_checkout(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    arrival = (datetime.now(timezone.utc).date() + timedelta(days=60)).isoformat()
    departure = (datetime.now(timezone.utc).date() + timedelta(days=61)).isoformat()
    booking = await _create_stay(env, lineage, arrival=arrival, departure=departure)
    await apply_booking_mutation(pg_pool, env.org, env.auth,
        env.request(booking, action='replace', allocation_revision=1, arrival_date=arrival, departure_date=departure),
        mutation=BookingMutation('complete', '', '', ''))
    await _approve_retention(pg_pool, env.org)
    with pytest.raises(Exception, match='archive evidence unavailable'):
        await _archive(pg_pool, env, booking)


async def test_archive_rejects_unresolved_work(pg_pool):
    env, lineage, booking = await _complete_with_old_evidence(pg_pool)
    await pg_pool.execute("UPDATE messages SET status='processing' WHERE organization_id=$1 AND id=$2", env.org, lineage.source_message_id)
    with pytest.raises(Exception, match='Unresolved'):
        await _archive(pg_pool, env, booking)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) > 0


async def test_archive_concurrent_calls_are_serialized(pg_pool):
    env, _, booking = await _complete_with_old_evidence(pg_pool)
    results = await asyncio.gather(_archive(pg_pool, env, booking), _archive(pg_pool, env, booking))
    assert sorted(results) == [False, True]


async def test_archive_cannot_be_called_by_backend_role(pg_pool):
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('SET LOCAL ROLE service_role')
        with pytest.raises(Exception, match='permission denied'):
            await conn.fetchval('SELECT public.archive_completed_hotel_allocation($1,$2)', uuid4(), uuid4())


async def test_archive_rejects_stale_transaction_snapshot(pg_pool):
    with pytest.raises(asyncpg.InvalidTransactionStateError, match='read committed'):
        async with pg_pool.acquire() as conn, conn.transaction(isolation='repeatable_read'):
            await conn.fetchval('SELECT public.archive_completed_hotel_allocation($1,$2)', uuid4(), uuid4())
