"""Lifecycle for the explicitly enabled private booking worker."""
import asyncio
import logging

from src.core.db.scoping import system_scope

logger = logging.getLogger(__name__)


@system_scope('read-only verification of database-owned worker schema metadata; no tenant records')
async def assert_private_booking_ready(pool):
    """Require the private lifecycle owner and the worker's database objects."""
    if pool is None:
        raise RuntimeError('Private booking worker requires its database')
    try:
        async with pool.acquire() as conn:
            ready = await conn.fetchval("""SELECT
                current_user NOT IN ('service_role','anon','authenticated')
                AND current_user = pg_get_userbyid(o.relowner)
                AND current_user = pg_get_userbyid(c.relowner)
                AND current_user = pg_get_userbyid(f.relowner)
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
                FROM pg_class o, pg_class c, pg_class f
                WHERE o.oid=to_regclass('public.inbound_worker_operations')
                  AND c.oid=to_regclass('public.booking_worker_claims')
                  AND f.oid=to_regclass('public.booking_sector_admission_fences')""")
    except Exception:
        raise RuntimeError('Private booking worker database readiness failed') from None
    if ready is not True:
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
