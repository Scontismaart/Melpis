"""Fenced sector calendar confirmation never starts unsupported provider work."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.calendar.lifecycle import CalendarLifecycle
from tests.core.bookings.test_allocation_engine_postgres import create_environment

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('recovery', [False, True])
async def test_fenced_confirmation_stops_before_provider(pg_pool, recovery):
    env = await create_environment(pg_pool, 'parrucchiere')
    booking_id = uuid4()
    await pg_pool.execute("SELECT public.booking_sector_activate_empty($1,'parrucchiere')", env.org)
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('SELECT public.booking_sector_mark_worker_insert($1,$2)', env.org, booking_id)
        await conn.execute("""INSERT INTO bookings
            (id,organization_id,nome_cliente,data,ora,coperti,stato,calendar_confirm_pending)
            VALUES($1,$2,'Synthetic',CURRENT_DATE+5,'10:00',1,'in_attesa',TRUE)""", booking_id, env.org)
    before = await pg_pool.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking_id)
    queue = SimpleNamespace(current=AsyncMock(return_value=True), fail=AsyncMock(),
                            verified=AsyncMock(), dispatch=AsyncMock())
    provider = AsyncMock()
    service = SimpleNamespace(repo=SimpleNamespace(pool=pg_pool), queue=queue,
                              connection=SimpleNamespace(row=AsyncMock()), _build_service=provider)
    claim = {'organization_id': env.org, 'internal_booking_id': booking_id,
             'calendar_claimed_action': 'authoritative', 'recovery': recovery}
    await CalendarLifecycle(service).run(claim)
    queue.fail.assert_awaited_once_with(claim, 'sector_authority_unsupported',
                                        uncertain=recovery, blocked=not recovery)
    provider.assert_not_awaited()
    service.connection.row.assert_not_awaited()
    queue.dispatch.assert_not_awaited()
    queue.verified.assert_not_awaited()
    assert await pg_pool.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking_id) == before
