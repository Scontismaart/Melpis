"""Bounded export expiry and explicitly approved, tenant-scoped retention.

Founder-approved product limits are not operational execution authority.
Effective tenant records and explicit operational cutover remain necessary.
"""
import logging
import os
import time

from src.core.db.scoping import system_scope
from src.core.gdpr.c24_schema import conversation_schema
from src.core.gdpr.operations import event
from src.core.gdpr.product_policy import retention_days_allowed

logger = logging.getLogger(__name__)

_EXPORT_EXPIRY_BATCH = 100
_EXPORT_EXPIRY_MAX_BATCHES = 10
_EXPORT_EXPIRY_BUDGET_SECONDS = 20
_WORKER_CAPABILITY_SCRUB_BATCH = 100
_WORKER_CAPABILITY_SCRUB_MAX_BATCHES = 10
_WORKER_CAPABILITY_SCRUB_BUDGET_SECONDS = 20


@system_scope('scheduled expiry of encrypted time-limited GDPR snapshots only')
async def _expired_exports(pool, *, deadline=None):
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            '''SELECT id,organization_id FROM gdpr_operations WHERE kind='export'
               AND expires_at<=NOW() AND status NOT IN ('expired','revoked')
               ORDER BY expires_at,id LIMIT $1 FOR UPDATE SKIP LOCKED''',
            _EXPORT_EXPIRY_BATCH,
        )
        expired = 0
        for row in rows:
            if deadline is not None and time.monotonic() >= deadline:
                break
            await conn.execute('DELETE FROM gdpr_export_chunks WHERE organization_id=$1 AND operation_id=$2', row['organization_id'], row['id'])
            await conn.execute(
                "UPDATE gdpr_operations SET status='expired',updated_at=NOW() WHERE organization_id=$1 AND id=$2", row['organization_id'], row['id'],
            )
            await event(conn, row['organization_id'], row['id'], 'export.expired')
            expired += 1
        return expired


@system_scope('aggregate-only monitoring of overdue encrypted export snapshots')
async def _oldest_overdue_export_seconds(pool):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            '''SELECT FLOOR(EXTRACT(EPOCH FROM (NOW()-expires_at)))::bigint
               FROM gdpr_operations WHERE kind='export'
                 AND expires_at<=NOW() AND status NOT IN ('expired','revoked')
               ORDER BY expires_at,id LIMIT 1''',
        )


async def expire_export_snapshots(pool):
    """Independent short-interval artifact cleanup, not primary-data retention.

    Bound each transaction and total work per run. Locked download rows can
    be skipped without preventing other expired snapshots from progressing.
    The oldest remaining due age is telemetry, not a completion claim.
    """
    deadline = time.monotonic() + _EXPORT_EXPIRY_BUDGET_SECONDS
    total = 0
    for _ in range(_EXPORT_EXPIRY_MAX_BATCHES):
        if time.monotonic() >= deadline:
            break
        expired = await _expired_exports(pool, deadline=deadline)
        total += expired
        if expired < _EXPORT_EXPIRY_BATCH:
            break
    return {'exports_expired': total,
            'oldest_overdue_seconds': await _oldest_overdue_export_seconds(pool)}


@system_scope('enumerates controller policy records; every content mutation binds the returned tenant')
async def _retention_policies(pool):
    async with pool.acquire() as conn:
        return await conn.fetch(
            '''SELECT DISTINCT ON(organization_id,category) organization_id,category,version,
                      retention_days,legal_hold,erasure_allowed,valid_until>NOW() AS valid
               FROM gdpr_policies WHERE approved_at<=NOW()
               AND category IN ('messages_content','simulator_responses','booking_conversation_content')
               ORDER BY organization_id,category,approved_at DESC,version DESC''',
        )


@system_scope('bounded removal of expired unused capability payloads and aggregate-only backlog monitoring')
async def scrub_expired_worker_capability_payloads(pool):
    deadline = time.monotonic() + _WORKER_CAPABILITY_SCRUB_BUDGET_SECONDS
    async with pool.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT session_user='postgres' AND current_user='postgres'"
        )
        available = await conn.fetchval(
            "SELECT to_regprocedure('c24_private.c24_worker_scrub_expired_capabilities(integer)') IS NOT NULL"
        )
        if owner is not True or available is not True:
            logger.warning('gdpr_retention worker_capability_payload_scrub=unavailable')
            return None
        total = 0
        for _ in range(_WORKER_CAPABILITY_SCRUB_MAX_BATCHES):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            scrubbed = await conn.fetchval(
                'SELECT c24_private.c24_worker_scrub_expired_capabilities($1)',
                _WORKER_CAPABILITY_SCRUB_BATCH, timeout=remaining,
            )
            total += scrubbed
            if scrubbed < _WORKER_CAPABILITY_SCRUB_BATCH:
                break
        oldest = await conn.fetchval(
            '''SELECT FLOOR(EXTRACT(EPOCH FROM (statement_timestamp()-MIN(expires_at))))::bigint
               FROM c24_private.worker_capabilities
               WHERE payload IS NOT NULL AND consumed_at IS NULL AND completed_at IS NULL
                 AND expires_at<=statement_timestamp()''',
        )
    return {'worker_capability_payloads_scrubbed': total, 'oldest_overdue_seconds': oldest}


async def run_retention(pool):
    expired = await _expired_exports(pool)
    counts = {'exports_expired': expired, 'messages_redacted': 0, 'simulator_purged': 0}
    if os.getenv('GDPR_RETENTION_EXECUTION_ENABLED') != 'true':
        logger.info('gdpr_retention primary_data=disabled exports_expired=%d', expired)
        return counts
    for policy in await _retention_policies(pool):
        if (not policy['valid'] or policy['legal_hold'] or not policy['erasure_allowed']
                or not retention_days_allowed(policy['category'], policy['retention_days'])):
            continue
        org, days = policy['organization_id'], policy['retention_days']
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(org))
            schema = await conversation_schema(conn)
            # SQL074 transfers lifecycle authority to its owner workflow. A
            # partial SQL070 install is equally unsafe for either runner.
            newer = await conn.fetchval("""SELECT
                to_regclass('public.gdpr_retention_holds') IS NOT NULL OR
                to_regclass('public.gdpr_retention_hold_events') IS NOT NULL OR
                to_regclass('public.c24_source_fences') IS NOT NULL OR
                EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.messages'::regclass
                  AND attname IN ('c24_received_at','content_purged_at','content_purge_cause')
                  AND NOT attisdropped)""")
            if schema == 'partial' or newer:
                logger.warning('gdpr_retention primary_data=blocked c24_lifecycle_controller_required')
                return counts
            if schema == 'absent' and policy['category'] == 'booking_conversation_content':
                continue
            current = await conn.fetchrow(
                '''SELECT version,retention_days,erasure_allowed,legal_hold,valid_until>NOW() AS valid
                   FROM gdpr_policies WHERE organization_id=$1 AND category=$2 AND approved_at<=NOW()
                   ORDER BY approved_at DESC,version DESC LIMIT 1''', org, policy['category'],
            )
            if (not current or not current['valid'] or current['legal_hold'] or not current['erasure_allowed']
                    or current['version'] != policy['version']
                    or not retention_days_allowed(policy['category'], current['retention_days'])):
                continue
            days = current['retention_days']
            if policy['category'] in {'messages_content', 'booking_conversation_content'}:
                reason = (
                    'legacy_message_provenance_required'
                    if schema == 'absent' and policy['category'] == 'messages_content'
                    else 'booking_message_provenance_required'
                    if policy['category'] == 'booking_conversation_content'
                    else 'c24_retention_controller_required'
                )
                logger.warning(
                    'gdpr_retention primary_data=blocked reason=%s organization_id=%s',
                    reason, org,
                )
                # The generic worker has no verified per-message receipt and
                # lineage for this schema. Do not substitute historical
                # messages.created_at or a provider timestamp for that evidence.
                # Export expiry above remains independent and already ran.
                continue
            c24_policy = await conn.fetchrow('''SELECT erasure_allowed,legal_hold,retention_days,
                    valid_until>NOW() AS valid FROM gdpr_policies
                WHERE organization_id=$1 AND category='booking_conversation_content'
                  AND approved_at<=NOW() ORDER BY approved_at DESC,version DESC LIMIT 1''', org)
            c24_allowed = bool(c24_policy and c24_policy['valid']
                               and c24_policy['erasure_allowed'] and not c24_policy['legal_hold']
                               and retention_days_allowed('booking_conversation_content', c24_policy['retention_days']))
            if schema == 'absent' and policy['category'] == 'simulator_responses':
                result = await conn.execute(
                    '''DELETE FROM simulation_requests WHERE organization_id=$1
                       AND (organization_id,auth_user_id,request_id) IN (
                         SELECT organization_id,auth_user_id,request_id FROM simulation_requests
                         WHERE organization_id=$1 AND status IN ('completed','failed')
                           AND completed_at<NOW()-make_interval(days=>$2)
                         ORDER BY completed_at LIMIT 500 FOR UPDATE SKIP LOCKED)''', org, days,
                )
                counts['simulator_purged'] += int(result.split()[-1])
            elif policy['category'] == 'simulator_responses':
                result = await conn.execute(
                    '''DELETE FROM simulation_requests WHERE organization_id=$1
                       AND (organization_id,auth_user_id,request_id) IN (
                         SELECT organization_id,auth_user_id,request_id FROM simulation_requests
                         WHERE organization_id=$1 AND status IN ('completed','failed')
                           AND completed_at<NOW()-make_interval(days=>$2)
                           AND ($3 OR NOT COALESCE(response ? 'booking_state_proposal',false))
                         ORDER BY completed_at LIMIT 500 FOR UPDATE SKIP LOCKED)''', org, days, c24_allowed,
                )
                counts['simulator_purged'] += int(result.split()[-1])
    logger.info(
        'gdpr_retention exports_expired=%d messages_redacted=%d simulator_purged=%d',
        *counts.values(),
    )
    return counts
