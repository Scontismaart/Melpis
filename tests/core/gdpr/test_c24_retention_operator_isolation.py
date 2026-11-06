"""Operator login and migration isolation regressions (dedicated test DB only)."""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest


@pytest.mark.asyncio
async def test_dedicated_login_must_set_operator_role(pg_pool, postgres_container):
    # Random cluster-wide role; always remove it, including on assertion failure.
    role = 'c24_test_' + uuid4().hex
    password = uuid4().hex
    async with pg_pool.acquire() as admin:
        await admin.execute(f"CREATE ROLE {role} LOGIN NOINHERIT PASSWORD '{password}'")
        try:
            await admin.execute(f'GRANT melpis_c24_retention TO {role}')
            dsn = postgres_container.get_connection_url().replace('+psycopg2', '')
            login = await asyncpg.connect(dsn=dsn, user=role, password=password)
            try:
                assert await login.fetchval('SELECT session_user') == role
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await login.fetchval('SELECT public.c24_retention_operator_allowed()')
                await login.execute('SET ROLE melpis_c24_retention')
                assert await login.fetchval('SELECT session_user') == role
                assert await login.fetchval('SELECT current_user') == 'melpis_c24_retention'
                assert await login.fetchval('SELECT public.c24_retention_operator_allowed()')
                assert not await login.fetchval(
                    "SELECT public.c24_run_retention_target('message',$1,$2)", uuid4(), uuid4())
                await login.execute('RESET ROLE')
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await login.fetchval('SELECT public.c24_retention_operator_allowed()')
            finally:
                await login.close()
        finally:
            await admin.execute(f'DROP ROLE {role}')


@pytest.mark.asyncio
@pytest.mark.parametrize('unsafe_change', [
    'ALTER ROLE melpis_c24_retention LOGIN',
    'GRANT service_role TO melpis_c24_retention',
    'GRANT melpis_c24_retention TO service_role',
])
async def test_migration_rejects_preexisting_unsafe_role(pg_pool, unsafe_change):
    sql = Path('src/core/db/migrations/083_c24_retention_operator.sql').read_text(encoding='utf-8')
    async with pg_pool.acquire() as conn:
        outer = conn.transaction()
        await outer.start()
        try:
            await conn.execute(unsafe_change)
            with pytest.raises(asyncpg.RaiseError, match='Retention role must be isolated'):
                async with conn.transaction():
                    await conn.execute(sql)
        finally:
            # Revert the role change even if the migration unexpectedly succeeds.
            await outer.rollback()
