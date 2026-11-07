"""Fenced sector scheduler preserves occupied allocation and attribution."""
from uuid import uuid4

import asyncpg
import pytest

from src.core.bookings.booking_mutation_contract import BookingMutation, NewBookingLineage, apply_booking_mutation
from tests.core.bookings.test_allocation_engine_postgres import create_environment


@pytest.mark.asyncio
@pytest.mark.parametrize('sector', ['parrucchiere', 'centro_estetico'])
async def test_scheduled_review_under_sector_fence(pg_pool, sector):
    env = await create_environment(pg_pool, sector)
    await pg_pool.execute("UPDATE organizations SET subscription_status='active' WHERE id=$1", env.org)
    await pg_pool.execute('SELECT public.booking_sector_activate_empty($1,$2)', env.org, sector)
    booking = uuid4()
    create = env.request(booking, appointment_date='2024-01-11', appointment_time='10:00')
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, create,
        mutation=BookingMutation('create', 'Synthetic', '', '', '2024-01-11', '10:00', 1),
        new_booking=NewBookingLineage(None, None)))['outcome'] == 'allocated'
    confirm = env.request(booking, action='replace', allocation_revision=1,
                          appointment_date='2024-01-11', appointment_time='10:00')
    await apply_booking_mutation(pg_pool, env.org, env.auth, confirm,
                                 mutation=BookingMutation('confirm', '', '', ''))
    async with pg_pool.acquire() as conn, conn.transaction(isolation='repeatable_read'):
        await conn.execute('SET LOCAL ROLE melpis_booking_scheduler')
        with pytest.raises(asyncpg.SerializationError, match='booking admission requires read committed'):
            async with conn.transaction():
                await conn.fetchval('SELECT public.apply_scheduled_booking_review($1,$2)', env.org, booking)
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'confermata'
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('SET LOCAL ROLE melpis_booking_scheduler')
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute('SELECT public.booking_sector_mark_scheduled_review($1,$2)', env.org, booking)
        assert await conn.fetchval('SELECT public.apply_scheduled_booking_review($1,$2)', env.org, booking)
        assert not await conn.fetchval('SELECT public.apply_scheduled_booking_review($1,$2)', env.org, booking)
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'da_verificare'
    assert await pg_pool.fetchval('SELECT state FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 'active'
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) > 0
    assert await pg_pool.fetchval("SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2 AND actor_kind='scheduled_review'", env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_sector_admission_markers WHERE organization_id=$1', env.org) == 0


@pytest.mark.asyncio
async def test_scheduler_read_only_missing_booking_allows_stronger_isolation(pg_pool):
    async with pg_pool.acquire() as conn:
        async with conn.transaction(isolation='repeatable_read'):
            await conn.execute('SET LOCAL ROLE melpis_booking_scheduler')
            # An absent booking is a read-only no-op and need not create a marker.
            assert not await conn.fetchval('SELECT public.apply_scheduled_booking_review($1,$2)', uuid4(), uuid4())

@pytest.mark.asyncio
@pytest.mark.parametrize('isolation', ['repeatable_read', 'serializable'])
async def test_external_stronger_isolation_capacity_write_without_opt_in(pg_pool, isolation):
    env = await create_environment(pg_pool, 'ristorante')
    booking = await env.booking()
    async with pg_pool.acquire() as conn, conn.transaction(isolation=isolation):
        await conn.execute("UPDATE bookings SET data='2027-01-12' WHERE organization_id=$1 AND id=$2", env.org, booking)
        await conn.execute("""INSERT INTO bookings(id,organization_id,nome_cliente,telefono,data,ora,coperti,stato)
            VALUES($1,$2,'Synthetic','','2027-01-13','10:00',1,'in_attesa')""", uuid4(), env.org)
    assert str(await pg_pool.fetchval('SELECT data FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)) == '2027-01-12'
