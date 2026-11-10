"""Real PostgreSQL ownership and required-schema check for the private worker."""
import asyncpg
import pytest
from uuid import uuid4

from src.core.bookings.private_worker_lifecycle import assert_private_booking_ready


@pytest.mark.asyncio
async def test_worker_readiness_requires_dedicated_identity(private_worker_pool, pg_pool, postgres_container):
    await assert_private_booking_ready(private_worker_pool, application_pool=pg_pool)
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


class _OneConnectionPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        class Acquire:
            def __init__(self, conn): self.conn = conn
            async def __aenter__(self): return self.conn
            async def __aexit__(self, *_): return False
        return Acquire(self.conn)
