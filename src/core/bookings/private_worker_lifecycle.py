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
        server_settings={'search_path': 'public, extensions'},
    )


@system_scope('read-only verification of database-owned worker schema metadata; no tenant records')
async def assert_private_booking_ready(pool, *, application_pool=None):
    """Require the dedicated, non-owner C2.4 database login and schema grants."""
    if pool is None:
        raise RuntimeError('Private booking worker requires its database')
    try:
        async with pool.acquire() as conn:
            identity_ok = await conn.fetchval("""SELECT
                current_user = 'melpis_c24_booking_worker'
                AND session_user = current_user
                AND NOT EXISTS (SELECT 1 FROM pg_auth_members m
                    WHERE m.member=current_user::regrole OR m.roleid=current_user::regrole)
                AND NOT EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname=current_user
                    AND (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication))
                AND EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname=current_user
                    AND r.rolcanlogin AND NOT r.rolinherit AND NOT r.rolsuper
                    AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication
                    AND r.rolbypassrls)
                AND NOT EXISTS (SELECT 1 FROM pg_namespace n
                    WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
                      AND has_schema_privilege(current_user,n.oid,'CREATE'))""")
            tables_ok = await conn.fetchval("""SELECT NOT EXISTS (
                SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname='public' AND t.relkind IN ('r','p','v','m','f')
                  AND (has_table_privilege(current_user,t.oid,'SELECT')
                    OR has_table_privilege(current_user,t.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
                  AND t.relname NOT IN (
                    'organizations','bookings','booking_stays','booking_settings','messages',
                    'conversations','contacts','booking_catalog_publications','booking_catalog_requests',
                    'booking_conversation_states','booking_conversation_requests','booking_conversation_revocations',
                    'booking_worker_claims','inbound_worker_operations','inbound_worker_receipts','meta_webhook_inbox',
                    'booking_sector_admission_fences','booking_allocation_sets','booking_resource_allocations',
                    'booking_allocation_requests','booking_confirmation_outbox','booking_stay_purge_fences',
                    'booking_stay_tombstones','gdpr_erasure_barriers','usage_events','simulation_requests',
                    'message_delivery_attempts'))""")
            functions_ok = await conn.fetchval("""SELECT NOT EXISTS (
                SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.prosecdef
                  AND has_function_privilege(current_user,p.oid,'EXECUTE')
                  AND p.oid<>to_regprocedure('public.booking_sector_mark_worker_insert(uuid,uuid)'))""")
            schema_ok = await conn.fetchval("""SELECT
                current_user <> pg_get_userbyid(o.relowner)
                AND current_user <> pg_get_userbyid(c.relowner)
                AND current_user <> pg_get_userbyid(f.relowner)
                AND has_table_privilege(current_user,'public.inbound_worker_operations','SELECT,INSERT,UPDATE')
                AND has_table_privilege(current_user,'public.booking_worker_claims','SELECT,INSERT,UPDATE')
                AND has_table_privilege(current_user,'public.booking_sector_admission_fences','SELECT')
                AND has_function_privilege(current_user,'public.booking_sector_mark_worker_insert(uuid,uuid)','EXECUTE')
                AND to_regclass('public.inbound_worker_receipts') IS NOT NULL
                AND to_regclass('public.booking_conversation_states') IS NOT NULL
                AND to_regclass('public.booking_conversation_requests') IS NOT NULL
                AND to_regclass('public.booking_allocation_requests') IS NOT NULL
                AND to_regclass('public.booking_confirmation_outbox') IS NOT NULL
                AND to_regclass('public.booking_stays') IS NOT NULL
                AND to_regclass('public.booking_sector_admission_epochs') IS NOT NULL
                AND to_regclass('public.booking_sector_admission_markers') IS NOT NULL
                AND to_regprocedure('public.booking_sector_mark_worker_insert(uuid,uuid)') IS NOT NULL
                AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=c.oid
                    AND attname='generation' AND NOT attisdropped)
                AND EXISTS (SELECT 1 FROM pg_attribute
                    WHERE attrelid=to_regclass('public.booking_allocation_requests')
                    AND attname='service_operation_id' AND NOT attisdropped)
                AND EXISTS (SELECT 1 FROM pg_attribute
                    WHERE attrelid=to_regclass('public.booking_conversation_requests')
                    AND attname='service_operation_id' AND NOT attisdropped)
                FROM pg_class o
                JOIN pg_class c ON c.oid=to_regclass('public.booking_worker_claims')
                JOIN pg_class f ON f.oid=to_regclass('public.booking_sector_admission_fences')
                WHERE o.oid=to_regclass('public.inbound_worker_operations')""")
            ready = identity_ok is True and tables_ok is True and functions_ok is True and schema_ok is True
    except Exception:
        raise RuntimeError('Private booking worker database readiness failed') from None
    if ready is not True:
        raise RuntimeError('Private booking worker database readiness failed')
    if application_pool is not None:
        try:
            async with pool.acquire() as worker_conn, application_pool.acquire() as app_conn:
                worker_login = await worker_conn.fetchval('SELECT session_user')
                application_login = await app_conn.fetchval('SELECT session_user')
        except Exception:
            raise RuntimeError('Private booking worker database readiness failed') from None
        if not worker_login or worker_login == application_login:
            raise RuntimeError('Private booking worker database readiness failed')


async def run_private_booking_worker(pool):
    from src.core.bookings.worker_authority import WorkerBookingAuthority

    authority = WorkerBookingAuthority()
    while True:
        try:
            processed = await authority.process_next_batch(pool)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('Private booking worker failed error_type=%s', type(exc).__name__)
            processed = 0
        await asyncio.sleep(0.1 if processed else 1.0)
