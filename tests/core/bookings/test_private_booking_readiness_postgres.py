"""Real PostgreSQL ownership and required-schema check for the private worker."""
from uuid import uuid4

import asyncpg
import pytest

from src.core.bookings.private_worker_lifecycle import assert_private_booking_ready


@pytest.mark.asyncio
async def test_worker_readiness_requires_dedicated_identity(private_worker_pool, pg_pool, postgres_container):
    await assert_private_booking_ready(private_worker_pool, application_pool=pg_pool)
    assert await private_worker_pool.fetchval(
        "SELECT has_function_privilege(current_user,"
        "'public.c24_attach_calendar_inbound_source(uuid,uuid,uuid)','EXECUTE')"
    )
    async with pg_pool.acquire() as app_conn:
        for role in ("anon", "authenticated", "service_role"):
            assert not await app_conn.fetchval(
                "SELECT has_function_privilege($1,"
                "'public.c24_attach_calendar_inbound_source(uuid,uuid,uuid)','EXECUTE')",
                role,
            )
    with pytest.raises(RuntimeError, match='readiness failed'):
        await assert_private_booking_ready(private_worker_pool, application_pool=private_worker_pool)
    assert await private_worker_pool.fetchval("SELECT has_table_privilege(current_user,'public.messages','INSERT')")
    assert not await private_worker_pool.fetchval("SELECT has_table_privilege(current_user,'public.organization_memberships','SELECT')")
    assert not await private_worker_pool.fetchval("SELECT has_column_privilege(current_user,'public.messages','content_text','UPDATE')")
    with pytest.raises(RuntimeError, match='readiness failed'):
        await assert_private_booking_ready(pg_pool)
    async with pg_pool.acquire() as admin:
        await admin.execute('GRANT SELECT ON public.whatsapp_accounts TO melpis_c24_booking_worker')
    with pytest.raises(RuntimeError, match='readiness failed'):
        await assert_private_booking_ready(private_worker_pool)
    async with pg_pool.acquire() as admin:
        await admin.execute('REVOKE SELECT ON public.whatsapp_accounts FROM melpis_c24_booking_worker')
    await assert_private_booking_ready(private_worker_pool)
    role, password = 'c24_wrong_owner_' + uuid4().hex, uuid4().hex
    async with pg_pool.acquire() as admin:
        await admin.execute(f'CREATE ROLE {role} LOGIN NOINHERIT PASSWORD \'{password}\'')
        try:
            conn = await asyncpg.connect(dsn=postgres_container.get_connection_url().replace('+psycopg2', ''),
                                         user=role, password=password)
            try:
                with pytest.raises(RuntimeError, match='readiness failed'):
                    await assert_private_booking_ready(_OneConnectionPool(conn))
            finally:
                await conn.close()
        finally:
            await admin.execute(f'DROP ROLE {role}')


@pytest.mark.asyncio
async def test_worker_cannot_mutate_parent_primary_key_but_can_lock_it(private_worker_pool, pg_pool, sample_org):
    org_id = sample_org['id']
    async with private_worker_pool.acquire() as worker:
        async with worker.transaction():
            assert await worker.fetchval('SELECT id FROM organizations WHERE id=$1 FOR UPDATE', org_id) == org_id
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await worker.execute('UPDATE organizations SET id=$2 WHERE id=$1', org_id, uuid4())
    async with pg_pool.acquire() as admin:
        assert await admin.fetchval('SELECT id FROM organizations WHERE id=$1', org_id) == org_id


@pytest.mark.asyncio
async def test_worker_readiness_rejects_another_database_instance(private_worker_pool, pg_pool, postgres_container):
    db_name = 'c24_identity_' + uuid4().hex
    base_dsn = postgres_container.get_connection_url().replace('+psycopg2', '')
    dsn_prefix, _ = base_dsn.rsplit('/', 1)
    async with pg_pool.acquire() as admin:
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    alternate_pool = None
    try:
        alternate_dsn = f'{dsn_prefix}/{db_name}'
        alternate_pool = await asyncpg.create_pool(dsn=alternate_dsn, min_size=1, max_size=1)
        async with alternate_pool.acquire() as alternate:
            await alternate.execute('''CREATE TABLE public.c24_database_instance_identity (
                singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), identity_id uuid NOT NULL)''')
            await alternate.execute('''INSERT INTO public.c24_database_instance_identity
                VALUES (true,$1)''', uuid4())
            await alternate.execute('GRANT SELECT ON public.c24_database_instance_identity TO melpis_c24_booking_worker')
        with pytest.raises(RuntimeError, match='readiness failed'):
            await assert_private_booking_ready(private_worker_pool, application_pool=alternate_pool)
    finally:
        if alternate_pool is not None:
            await alternate_pool.close()
        async with pg_pool.acquire() as admin:
            await admin.execute(f'DROP DATABASE "{db_name}" WITH (FORCE)')


class _OneConnectionPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        class Acquire:
            def __init__(self, conn): self.conn = conn
            async def __aenter__(self): return self.conn
            async def __aexit__(self, *_): return False
        return Acquire(self.conn)
