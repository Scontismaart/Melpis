"""Admission activation respects organization and legacy-writer lock ordering."""
import asyncio
from uuid import uuid4

import asyncpg
import pytest

from tests.core.bookings.test_allocation_engine_postgres import create_environment, wait_blocked


@pytest.mark.asyncio
async def test_activation_does_not_wait_for_worker_organization_share(pg_pool):
    env = await create_environment(pg_pool, 'parrucchiere')
    async with pg_pool.acquire() as worker, worker.transaction():
        await worker.fetchrow('SELECT id FROM organizations WHERE id=$1 FOR SHARE', env.org)
        # Activation must not wait for this compatible worker lock while holding
        # the admission lock; that was the deadlock-causing inverse order.
        await asyncio.wait_for(pg_pool.execute(
            "SELECT public.booking_sector_activate_empty($1,'parrucchiere')", env.org), 5)
        await asyncio.wait_for(worker.fetchval(
            'SELECT pg_advisory_xact_lock(hashtextextended($1::text,884))', str(env.org)), 5)
    assert await pg_pool.fetchval('SELECT EXISTS(SELECT 1 FROM booking_sector_admission_fences WHERE organization_id=$1)', env.org)


@pytest.mark.asyncio
async def test_activation_rechecks_legacy_insert_after_admission_lock_wait(pg_pool):
    env = await create_environment(pg_pool, 'parrucchiere')
    async with pg_pool.acquire() as legacy:
        transaction = legacy.transaction()
        await transaction.start()
        try:
            await legacy.execute("""INSERT INTO bookings(id,organization_id,nome_cliente,telefono,data,ora,coperti,stato)
                VALUES($1,$2,'Synthetic','','2027-01-11','10:00',1,'in_attesa')""", uuid4(), env.org)
            activation = asyncio.create_task(pg_pool.execute(
                "SELECT public.booking_sector_activate_empty($1,'parrucchiere')", env.org))
            await wait_blocked(pg_pool, legacy.get_server_pid())
            await transaction.commit()
        except BaseException:
            await transaction.rollback()
            raise
    with pytest.raises(Exception, match='tenant must have no bookings'):
        await asyncio.wait_for(activation, 5)
    assert not await pg_pool.fetchval('SELECT EXISTS(SELECT 1 FROM booking_sector_admission_fences WHERE organization_id=$1)', env.org)


@pytest.mark.asyncio
async def test_activation_rejects_stale_repeatable_read_snapshot(pg_pool):
    env = await create_environment(pg_pool, 'parrucchiere')
    async with pg_pool.acquire() as conn, conn.transaction(isolation='repeatable_read'):
        with pytest.raises(Exception, match='requires read committed'):
            async with conn.transaction():
                await conn.execute("SELECT public.booking_sector_activate_empty($1,'parrucchiere')", env.org)

@pytest.mark.asyncio
@pytest.mark.parametrize('isolation', ['repeatable_read', 'serializable'])
async def test_stale_legacy_insert_cannot_cross_activation(pg_pool, isolation):
    env = await create_environment(pg_pool, 'parrucchiere')
    booking = uuid4()
    async with pg_pool.acquire() as legacy:
        transaction = legacy.transaction(isolation=isolation)
        await transaction.start()
        try:
            # Establish the snapshot before activation commits.
            assert not await legacy.fetchval('SELECT EXISTS(SELECT 1 FROM booking_sector_admission_fences WHERE organization_id=$1)', env.org)
            await pg_pool.execute("SELECT public.booking_sector_activate_empty($1,'parrucchiere')", env.org)
            with pytest.raises(asyncpg.SerializationError):
                await legacy.execute("""INSERT INTO bookings(id,organization_id,nome_cliente,telefono,data,ora,coperti,stato)
                    VALUES($1,$2,'Synthetic','','2027-01-11','10:00',1,'in_attesa')""", booking, env.org)
        finally:
            await transaction.rollback()
    assert not await pg_pool.fetchval('SELECT EXISTS(SELECT 1 FROM bookings WHERE organization_id=$1 AND id=$2)', env.org, booking)


@pytest.mark.asyncio
@pytest.mark.parametrize('isolation', ['repeatable_read', 'serializable'])
@pytest.mark.parametrize('operation', ['capacity_update', 'insert'])
async def test_unfenced_booking_admission_remains_allowed_at_stronger_isolation(pg_pool, isolation, operation):
    env = await create_environment(pg_pool, 'ristorante')
    booking = await env.booking()
    async with pg_pool.acquire() as conn, conn.transaction(isolation=isolation):
        if operation == 'capacity_update':
            await conn.execute("UPDATE bookings SET data='2026-10-29' WHERE organization_id=$1 AND id=$2", env.org, booking)
        else:
            await conn.execute("""INSERT INTO bookings(id,organization_id,nome_cliente,telefono,data,ora,coperti,stato)
                VALUES($1,$2,'Synthetic','','2026-10-29','10:00',1,'in_attesa')""", uuid4(), env.org)
    assert await pg_pool.fetchval("SELECT count(*) FROM bookings WHERE organization_id=$1 AND data='2026-10-29'", env.org) == 1

@pytest.mark.asyncio
async def test_sector_admission_private_tables_have_rls_and_no_client_privileges(pg_pool):
    for table in ('booking_sector_admission_epochs', 'booking_sector_admission_fences', 'booking_sector_admission_markers'):
        assert await pg_pool.fetchval('SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass', f'public.{table}')
        assert await pg_pool.fetchval('SELECT count(*) FROM pg_policies WHERE schemaname=$1 AND tablename=$2', 'public', table) == 0
        for role in ('anon', 'authenticated', 'service_role'):
            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                assert not await pg_pool.fetchval('SELECT has_table_privilege($1,$2,$3)', role, f'public.{table}', privilege)
