"""Legacy cancellation must serialize with allocation and preserve managed claims."""
import asyncio
from uuid import uuid4

import pytest
import asyncpg

from src.core.db.repositories.booking_repo import BookingRepository, ManagedBookingMutationRequired
from tests.core.bookings.test_allocation_engine_postgres import create_environment, wait_blocked

pytestmark = pytest.mark.asyncio


async def test_legacy_cancel_observes_concurrent_managed_allocation(pg_pool, monkeypatch):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    allocated, release = asyncio.Event(), asyncio.Event()
    original = env.engine._receipt
    allocator_pid = None

    async def pause(conn, *args):
        nonlocal allocator_pid
        await original(conn, *args)
        allocator_pid = conn.get_server_pid()
        allocated.set()
        await release.wait()

    monkeypatch.setattr(env.engine, '_receipt', pause)
    admission = asyncio.create_task(env.apply(env.request(booking)))
    cancel = None
    try:
        await asyncio.wait_for(allocated.wait(), 5)
        cancel = asyncio.create_task(BookingRepository(pg_pool).cancel_legacy_booking(env.org, booking))
        await wait_blocked(pg_pool, allocator_pid)
    finally:
        release.set()
        results = await asyncio.gather(admission, *([cancel] if cancel else []), return_exceptions=True)
    assert results[0]['outcome'] == 'allocated'
    assert isinstance(results[1], ManagedBookingMutationRequired)
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'in_attesa'
    assert await BookingRepository(pg_pool).cancel_legacy_booking(uuid4(), booking) is None


@pytest.mark.parametrize('operation', ['reject', 'confirm', 'complete', 'update'])
async def test_legacy_writers_observe_concurrent_allocation(pg_pool, monkeypatch, operation):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    allocated, release = asyncio.Event(), asyncio.Event()
    original = env.engine._receipt
    allocator_pid = None

    async def pause(conn, *args):
        nonlocal allocator_pid
        await original(conn, *args)
        allocator_pid = conn.get_server_pid()
        allocated.set()
        await release.wait()

    monkeypatch.setattr(env.engine, '_receipt', pause)
    repo = BookingRepository(pg_pool)
    before = await repo.get_booking(env.org, booking)
    async def mutate():
        if operation == 'reject':
            return await repo.update_booking_status(env.org, booking, 'rifiutata')
        if operation == 'confirm':
            return await repo.update_booking_status(env.org, booking, 'confermata', expected=before)
        if operation == 'complete':
            return await repo.mark_booking_completed(env.org, booking, before)
        return await repo.update_booking_details(env.org, booking, nome_cliente=before['nome_cliente'],
            telefono=before['telefono'], data=before['data'], ora=before['ora'], coperti=2,
            note=before['note'], stato=before['stato'], expected=before)
    admission = asyncio.create_task(env.apply(env.request(booking)))
    writer = None
    try:
        await asyncio.wait_for(allocated.wait(), 5)
        writer = asyncio.create_task(mutate())
        await wait_blocked(pg_pool, allocator_pid)
    finally:
        release.set()
        results = await asyncio.gather(admission, *([writer] if writer else []), return_exceptions=True)
    assert results[0]['outcome'] == 'allocated'
    assert isinstance(results[1], ManagedBookingMutationRequired)
    after = await repo.get_booking(env.org, booking)
    assert (after['stato'], after['data'], after['ora'], after['coperti']) == (
        before['stato'], before['data'], before['ora'], before['coperti'])


@pytest.mark.parametrize('operation', ['cancel', 'no_show', 'reject', 'confirm', 'complete', 'update', 'google_confirm'])
async def test_repeatable_read_stale_snapshot_fails_closed(pg_pool, operation):
    """A caller's old snapshot must not hide an allocation committed later."""
    env = await create_environment(pg_pool)
    booking = await env.booking()
    repo = BookingRepository(pg_pool)
    async with pg_pool.acquire() as conn, conn.transaction(isolation='repeatable_read'):
        before = dict(await conn.fetchrow(
            'SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking))
        assert not await conn.fetchval(
            'SELECT EXISTS(SELECT 1 FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2)',
            env.org, booking)
        # This commits on a separate connection after the old snapshot is fixed.
        assert (await env.apply(env.request(booking)))['outcome'] == 'allocated'
        token = repo._locked_connection.set((asyncio.current_task(), conn))
        try:
            with pytest.raises(asyncpg.InterfaceError, match='different isolation level'):
                if operation == 'cancel':
                    await repo.cancel_legacy_booking(env.org, booking)
                elif operation == 'no_show':
                    await repo.mark_legacy_booking_no_show(env.org, booking)
                elif operation in ('reject', 'confirm'):
                    await repo.update_booking_status(env.org, booking,
                        'rifiutata' if operation == 'reject' else 'confermata', expected=before)
                elif operation == 'complete':
                    await repo.mark_booking_completed(env.org, booking, before)
                elif operation == 'google_confirm':
                    await repo.request_google_confirmation(env.org, booking)
                else:
                    await repo.update_booking_details(env.org, booking, nome_cliente=before['nome_cliente'],
                        telefono=before['telefono'], data=before['data'], ora=before['ora'], coperti=2,
                        note=before['note'], stato=before['stato'], expected=before)
        finally:
            repo._locked_connection.reset(token)
    after = await repo.get_booking(env.org, booking)
    assert (after['stato'], after['data'], after['ora'], after['coperti'], after['updated_at']) == (
        before['stato'], before['data'], before['ora'], before['coperti'], before['updated_at'])
    allocation = await pg_pool.fetchrow(
        'SELECT state,allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2',
        env.org, booking)
    assert allocation['state'] == 'active' and allocation['allocation_revision'] == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2',
        env.org, booking) > 0
