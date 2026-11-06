"""Legacy cancellation must serialize with allocation and preserve managed claims."""
import asyncio
from uuid import uuid4

import pytest

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
