"""Apply ACL-only migration against the Staging-style direct client grants."""
from pathlib import Path

import pytest


FUNCTIONS = (
    'booking_allocation_validate_worker_source',
    'booking_conversation_validate_worker_source',
)
ROLES = ('anon', 'authenticated', 'service_role')


@pytest.mark.migration_limit(104)
async def test_worker_source_trigger_acl_revokes_direct_grants_and_preserves_triggers(pg_pool):
    migration = Path('src/core/db/migrations/105_c24_worker_trigger_execute_acl.sql').read_text(encoding='utf-8')
    async with pg_pool.acquire() as conn, conn.transaction():
        for function in FUNCTIONS:
            # Model the direct grants confirmed by the read-only Staging advisor.
            await conn.execute(f'GRANT EXECUTE ON FUNCTION public.{function}() TO anon,authenticated,service_role')
            for role in ROLES:
                assert await conn.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')',
                                           role, f'public.{function}()')

        # Idempotent: a repeated migration neither restores grants nor modifies
        # the trigger logic or trigger registration.
        for _ in range(2):
            await conn.execute(migration)
            for function in FUNCTIONS:
                for role in ROLES + ('melpis_c24_booking_worker',):
                    assert not await conn.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')',
                                                   role, f'public.{function}()')
                assert await conn.fetchval('''SELECT p.prosecdef AND p.prorettype='trigger'::regtype
                    AND 'search_path=pg_catalog, public'=ANY(p.proconfig)
                    AND EXISTS(SELECT FROM pg_trigger t WHERE t.tgfoid=p.oid AND t.tgenabled='O')
                    FROM pg_proc p WHERE p.oid=to_regprocedure($1)''', f'public.{function}()')
        # The distinct current-principal RLS helper still works for the intended
        # authenticated principal and remains unavailable to anonymous clients.
        assert await conn.fetchval("SELECT has_function_privilege('authenticated', 'public.current_auth_access_allowed()', 'EXECUTE')")
        assert not await conn.fetchval("SELECT has_function_privilege('anon', 'public.current_auth_access_allowed()', 'EXECUTE')")
