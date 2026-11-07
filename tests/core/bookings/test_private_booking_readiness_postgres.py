"""Real PostgreSQL ownership and required-schema check for the private worker."""
import asyncpg
import pytest
from uuid import uuid4

from src.core.bookings.private_worker_lifecycle import assert_private_booking_ready


@pytest.mark.asyncio
async def test_worker_readiness_accepts_owner_identity_and_rejects_other_login(pg_pool, postgres_container):
    await assert_private_booking_ready(pg_pool)
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
