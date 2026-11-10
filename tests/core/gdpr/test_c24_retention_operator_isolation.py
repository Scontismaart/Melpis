"""Operator login and migration isolation regressions (dedicated test DB only)."""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from tests.core.bookings.test_conversation_state_postgres import state_env


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
    'GRANT CREATE ON SCHEMA public TO melpis_c24_retention',
    'GRANT CREATE ON SCHEMA auth TO melpis_c24_retention',
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


@pytest.mark.asyncio
async def test_controller_rejects_custom_schema_create_or_public_security_definer(pg_pool, postgres_container, monkeypatch):
    import asyncpg
    from src.core.gdpr.c24_retention_controller import run_c24_retention_targets

    monkeypatch.setenv('GDPR_C24_OWNER_RETENTION_ENABLED', 'true')
    role, password = 'c24_test_' + uuid4().hex, uuid4().hex
    async with pg_pool.acquire() as admin:
        await admin.execute(f'CREATE ROLE {role} LOGIN NOINHERIT PASSWORD \'{password}\'')
        await admin.execute('CREATE SCHEMA c24_extra_security_test')
        await admin.execute('GRANT USAGE ON SCHEMA c24_extra_security_test TO PUBLIC')
        await admin.execute("CREATE FUNCTION c24_extra_security_test.reachable() RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'")
        try:
            await admin.execute(f'GRANT melpis_c24_retention TO {role}')
            dsn = postgres_container.get_connection_url().replace('+psycopg2', '')
            async with asyncpg.create_pool(dsn=dsn, user=role, password=password, min_size=1, max_size=1) as login_pool:
                await admin.execute(f'GRANT CREATE ON SCHEMA auth TO {role}')
                with pytest.raises(PermissionError, match='operator_required'):
                    await run_c24_retention_targets(login_pool, message_targets=[(uuid4(), uuid4())])
                await admin.execute(f'REVOKE CREATE ON SCHEMA auth FROM {role}')
                # Default PUBLIC EXECUTE is dangerous once this custom schema is visible.
                with pytest.raises(PermissionError, match='operator_required'):
                    await run_c24_retention_targets(login_pool, message_targets=[(uuid4(), uuid4())])
        finally:
            await admin.execute('DROP SCHEMA c24_extra_security_test CASCADE')
            await admin.execute(f'DROP ROLE {role}')


@pytest.mark.asyncio
async def test_manifest_controller_uses_dedicated_login_per_transaction(
    state_env, postgres_container, monkeypatch, tmp_path,
):
    import json
    from urllib.parse import quote, urlsplit, urlunsplit
    from src.core.gdpr.c24_retention_controller import _run_manifest
    from tests.core.bookings.test_conversation_state_postgres import commit_source, apply
    from tests.core.gdpr.test_conversation_lifecycle_c24 import approve_conversation
    from tests.core.gdpr.test_c24_idle_expiry import age_state

    env = state_env
    await commit_source(env, env['message'], '2026-11-12', {'service_id': 'standard'}, 1700000000)
    await apply(env, env['message'])
    await approve_conversation(env['pool'], env['org'])
    await age_state(env, 31)
    role = 'c24_test_' + uuid4().hex
    password = uuid4().hex
    url = urlsplit(postgres_container.get_connection_url().replace('+psycopg2', ''))
    host = url.hostname or 'localhost'
    host = f'[{host}]' if ':' in host else host
    endpoint = f'{quote(role)}:{quote(password)}@{host}' + (f':{url.port}' if url.port else '')
    dsn = urlunsplit((url.scheme, endpoint, url.path, url.query, url.fragment))
    path = tmp_path / 'targets.json'
    # Repeating the target exercises a second pool checkout and idempotent retry.
    target = [str(env['org']), str(env['conv'])]
    path.write_text(json.dumps({'conversation_targets': [target, target]}))
    monkeypatch.setenv('GDPR_C24_OWNER_RETENTION_ENABLED', 'true')
    monkeypatch.setenv('GDPR_C24_OWNER_RETENTION_DSN', dsn)
    async with env['pool'].acquire() as admin:
        await admin.execute(f"CREATE ROLE {role} LOGIN NOINHERIT PASSWORD '{password}'")
        try:
            await admin.execute(f'GRANT melpis_c24_retention TO {role}')
            login = await asyncpg.connect(dsn)
            try:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await login.fetchval('SELECT public.c24_retention_operator_allowed()')
            finally:
                await login.close()
            assert await _run_manifest(path) == {
                'messages_purged': 0, 'conversations_expired': 1, 'receipts_cleaned': 0,
                'suppressions_cleaned': 0, 'suppression_audit_rows_expired': 0,
            }
            assert not await admin.fetchval(
                'SELECT EXISTS(SELECT 1 FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2)',
                env['org'], env['conv'])
        finally:
            await admin.execute(f'DROP ROLE {role}')
