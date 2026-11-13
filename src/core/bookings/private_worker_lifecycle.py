"""Lifecycle for the explicitly enabled private booking worker."""
import asyncio
import logging

from src.core.db.scoping import system_scope

logger = logging.getLogger(__name__)

_WORKER_ROLE = 'melpis_c24_booking_worker'


async def create_private_booking_pool(dsn, *, min_size=1, max_size=2):
    """Open a pool with the separately provisioned, narrowly granted worker login."""
    import asyncpg

    if not isinstance(dsn, str) or not dsn.strip():
        raise RuntimeError('Private booking worker database readiness failed')

    return await asyncpg.create_pool(
        dsn=dsn, min_size=min_size, max_size=max_size, command_timeout=30,
        server_settings={'search_path': 'pg_catalog, c24_private, pg_temp'},
    )


@system_scope('read-only verification of database-owned worker schema metadata; no tenant records')
async def assert_private_booking_ready(pool, *, application_pool=None):
    """Require the dedicated, non-owner C2.4 database login and schema grants."""
    if pool is None:
        raise RuntimeError('Private booking worker requires its database')
    try:
        async with pool.acquire() as conn:
            contract_ok = await conn.fetchval("""WITH expected_worker_functions(oid) AS (
                VALUES
                    (to_regprocedure('c24_private.c24_worker_readiness()')),
                    (to_regprocedure('c24_private.c24_quota_trust_epoch()')),
                    (to_regprocedure('c24_private.c24_worker_next_jobs(uuid,integer)')),
                    (to_regprocedure('c24_private.c24_worker_begin_job(uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_renew_job(uuid,uuid,uuid,bigint,integer)')),
                    (to_regprocedure('c24_private.c24_worker_finish_job()')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows(uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows_message(uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_mark_sector_insert(uuid,uuid)')),
                    (to_regprocedure('c24_private.c24_worker_attach_calendar_source(uuid,uuid,uuid)')),
                    (to_regprocedure('c24_private.c24_worker_consume_capability(uuid)')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows_claim_source(uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows_operation(uuid,uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows_simulation_request(uuid,uuid,uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows_request(uuid,uuid,uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows_conversation(uuid,uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows_contact(uuid,uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows_inbox(uuid,uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows_booking(uuid,uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_scope_allows_bound_booking(uuid,uuid,text)')),
                    (to_regprocedure('c24_private.c24_worker_claim_source_allows(uuid,text,text,text)')),
                    (to_regprocedure('c24_private.c24_worker_claim_booking_insert_allows(uuid,uuid,text,text,text)')),
                    (to_regprocedure('c24_private.c24_worker_outbound_booking_message_allows(uuid,uuid,text,text,text)')),
                    (to_regprocedure('c24_private.c24_worker_gdpr_guard(text,text,jsonb,jsonb)'))
            )
            SELECT
                session_user='melpis_c24_booking_worker'
                AND current_user=session_user
                AND EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname=session_user
                    AND r.rolcanlogin AND NOT r.rolinherit AND NOT r.rolsuper
                    AND NOT r.rolcreatedb AND NOT r.rolcreaterole
                    AND NOT r.rolreplication AND NOT r.rolbypassrls)
                AND NOT EXISTS (SELECT 1 FROM pg_auth_members m
                    WHERE m.member=session_user::regrole)
                AND NOT EXISTS (SELECT 1 FROM pg_auth_members m
                    JOIN pg_roles member ON member.oid=m.member
                    JOIN pg_roles grantor ON grantor.oid=m.grantor
                    WHERE m.roleid=session_user::regrole AND NOT (
                        member.rolname='postgres'
                        AND grantor.rolname IN ('postgres','supabase_admin')
                        AND m.admin_option AND NOT m.set_option AND NOT m.inherit_option))
                AND NOT pg_has_role('service_role',session_user,'MEMBER')
                AND NOT pg_has_role('anon',session_user,'MEMBER')
                AND NOT pg_has_role('authenticated',session_user,'MEMBER')
                AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
                      AND n.nspname<>'c24_private' AND c.relkind IN ('r','p','v','m','f','S')
                      AND (has_table_privilege(session_user,c.oid,
                            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                        OR (c.relkind='S' AND has_sequence_privilege(session_user,c.oid,'USAGE,SELECT,UPDATE'))))
                AND (SELECT count(*) FROM expected_worker_functions WHERE oid IS NOT NULL)
                    = (SELECT count(*) FROM expected_worker_functions)
                AND NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
                      AND p.prosecdef AND has_function_privilege(session_user,p.oid,'EXECUTE')
                      AND NOT EXISTS (SELECT 1 FROM expected_worker_functions f WHERE f.oid=p.oid))
                AND has_schema_privilege(session_user,'c24_private','USAGE')
                AND NOT has_schema_privilege(session_user,'c24_private','CREATE')
                AND NOT has_table_privilege(session_user,'c24_private.worker_context',
                    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                AND has_function_privilege(session_user,
                    'c24_private.c24_worker_readiness()','EXECUTE')
                AND has_function_privilege(session_user,
                    'c24_private.c24_quota_trust_epoch()','EXECUTE')
                AND has_function_privilege(session_user,
                    'c24_private.c24_worker_begin_job(uuid,text)','EXECUTE')
                AND has_function_privilege(session_user,
                    'c24_private.c24_worker_renew_job(uuid,uuid,uuid,bigint,integer)','EXECUTE')
                AND has_function_privilege(session_user,
                    'c24_private.c24_worker_finish_job()','EXECUTE')""")
            marker = await conn.fetchrow('SELECT identity_id,database_oid FROM c24_private.c24_worker_readiness()')
            ready = contract_ok is True and marker is not None
    except Exception:
        raise RuntimeError('Private booking worker database readiness failed') from None
    if ready is not True:
        raise RuntimeError('Private booking worker database readiness failed')
    if application_pool is not None:
        try:
            async with pool.acquire() as worker_conn, application_pool.acquire() as app_conn:
                worker_login = await worker_conn.fetchval('SELECT session_user')
                application_login = await app_conn.fetchval('SELECT session_user')
                application_is_capability_issuer = await app_conn.fetchval("""SELECT
                    session_user='postgres' AND current_user='postgres'
                    AND has_function_privilege(current_user,
                        'c24_private.c24_worker_issue_capability(uuid,uuid,uuid,bigint,uuid,jsonb,text,integer)',
                        'EXECUTE')""")
                worker_identity = {
                    'identity_id': str(marker['identity_id']),
                    'database_oid': str(marker['database_oid']),
                }
                application_identity = await app_conn.fetchrow('''SELECT i.identity_id::text AS identity_id,
                    d.oid::text AS database_oid FROM public.c24_database_instance_identity i
                    JOIN pg_database d ON d.datname=current_database() WHERE i.singleton=true''')
        except Exception:
            raise RuntimeError('Private booking worker database readiness failed') from None
        if (not worker_login or worker_login == application_login
                or application_is_capability_issuer is not True or not worker_identity
                or not application_identity
                or worker_identity['identity_id'] != application_identity['identity_id']
                or worker_identity['database_oid'] != application_identity['database_oid']):
            raise RuntimeError('Private booking worker database readiness failed')


async def run_private_booking_worker(pool, *, application_pool=None):
    from src.core.bookings.worker_authority import WorkerBookingAuthority

    if application_pool is None:
        raise RuntimeError('Private booking capability issuer unavailable')
    authority = WorkerBookingAuthority()
    while True:
        try:
            processed = await authority.process_next_batch(pool, application_pool=application_pool)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('Private booking worker failed error_type=%s', type(exc).__name__)
            processed = 0
        await asyncio.sleep(0.1 if processed else 1.0)
