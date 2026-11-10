"""Bounded export expiry and explicitly approved, tenant-scoped retention.

Founder-approved product limits are not operational execution authority.
Effective tenant records and explicit operational cutover remain necessary.
"""
import logging
import os

from src.core.db.scoping import system_scope
from src.core.gdpr.c24_schema import conversation_schema
from src.core.gdpr.operations import event
from src.core.gdpr.product_policy import retention_days_allowed

logger = logging.getLogger(__name__)


@system_scope('scheduled expiry of encrypted time-limited GDPR snapshots only')
async def _expired_exports(pool):
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            '''SELECT id,organization_id FROM gdpr_operations WHERE kind='export'
               AND expires_at<=NOW() AND status NOT IN ('expired','revoked')
               ORDER BY expires_at LIMIT 100 FOR UPDATE SKIP LOCKED''',
        )
        for row in rows:
            await conn.execute('DELETE FROM gdpr_export_chunks WHERE organization_id=$1 AND operation_id=$2', row['organization_id'], row['id'])
            await conn.execute(
                "UPDATE gdpr_operations SET status='expired',updated_at=NOW() WHERE organization_id=$1 AND id=$2", row['organization_id'], row['id'],
            )
            await event(conn, row['organization_id'], row['id'], 'export.expired')
        return len(rows)


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
            c24_policy = await conn.fetchrow('''SELECT erasure_allowed,legal_hold,retention_days,
                    valid_until>NOW() AS valid FROM gdpr_policies
                WHERE organization_id=$1 AND category='booking_conversation_content'
                  AND approved_at<=NOW() ORDER BY approved_at DESC,version DESC LIMIT 1''', org)
            c24_allowed = bool(c24_policy and c24_policy['valid']
                               and c24_policy['erasure_allowed'] and not c24_policy['legal_hold']
                               and retention_days_allowed('booking_conversation_content', c24_policy['retention_days']))
            if schema == 'ready' and policy['category'] == 'booking_conversation_content':
                # Only the typed proposal is expired here. The final response,
                # commercial receipt and counter are governed independently.
                # Removing a source proposal invokes SQL070's bounded trigger,
                # atomically purging derived snapshots/receipts and fencing replay.
                await conn.execute('''WITH expired AS (
                    SELECT id FROM messages WHERE organization_id=$1
                      AND created_at<NOW()-make_interval(days=>$2)
                      AND ai_reply_cache ? 'booking_state_proposal'
                      AND status IN ('handled','sent','delivered','read','failed')
                    ORDER BY created_at,id LIMIT 500 FOR UPDATE SKIP LOCKED)
                    UPDATE messages SET ai_reply_cache=ai_reply_cache-'booking_state_proposal'
                    WHERE organization_id=$1 AND id IN (SELECT id FROM expired)''', org, days)
                await conn.execute('''WITH expired AS (
                    SELECT organization_id,auth_user_id,request_id FROM simulation_requests
                    WHERE organization_id=$1 AND response ? 'booking_state_proposal'
                      AND status='completed' AND completed_at<NOW()-make_interval(days=>$2)
                    ORDER BY completed_at,request_id LIMIT 500 FOR UPDATE SKIP LOCKED)
                    UPDATE simulation_requests SET response=response-'booking_state_proposal'
                    WHERE organization_id=$1 AND (organization_id,auth_user_id,request_id)
                      IN (SELECT organization_id,auth_user_id,request_id FROM expired)''', org, days)
            elif schema == 'absent' and policy['category'] == 'messages_content':
                rows = await conn.fetch(
                    '''SELECT id FROM messages WHERE organization_id=$1
                       AND created_at<NOW()-make_interval(days=>$2)
                       AND status IN ('handled','sent','delivered','read','failed')
                       AND deleted_at IS NULL AND billed_at IS NULL
                       ORDER BY created_at,id LIMIT 500 FOR UPDATE SKIP LOCKED''', org, days,
                )
                ids = [r['id'] for r in rows]
                if ids:
                    await conn.execute(
                        '''UPDATE messages SET content='{}',content_text=NULL,ai_reply_cache=NULL,
                               error_details=NULL,error_title=NULL,deleted_at=NOW()
                           WHERE organization_id=$1 AND id=ANY($2::uuid[])''', org, ids,
                    )
                    await conn.execute(
                        '''UPDATE event_log SET testo_originale='',risposta_ai='',dettagli='{}'
                           WHERE organization_id=$1 AND source_id=ANY($2::uuid[])''', org, ids,
                    )
                counts['messages_redacted'] += len(ids)
            elif schema == 'absent' and policy['category'] == 'simulator_responses':
                result = await conn.execute(
                    '''DELETE FROM simulation_requests WHERE organization_id=$1
                       AND (organization_id,auth_user_id,request_id) IN (
                         SELECT organization_id,auth_user_id,request_id FROM simulation_requests
                         WHERE organization_id=$1 AND status IN ('completed','failed')
                           AND completed_at<NOW()-make_interval(days=>$2)
                         ORDER BY completed_at LIMIT 500 FOR UPDATE SKIP LOCKED)''', org, days,
                )
                counts['simulator_purged'] += int(result.split()[-1])
            elif policy['category'] == 'messages_content':
                rows = await conn.fetch(
                    '''SELECT id FROM messages WHERE organization_id=$1
                       AND created_at<NOW()-make_interval(days=>$2)
                       AND status IN ('handled','sent','delivered','read','failed')
                       AND deleted_at IS NULL AND billed_at IS NULL
                       AND ($3 OR (NOT COALESCE(ai_reply_cache ? 'booking_state_proposal',false)
                         AND NOT EXISTS(SELECT 1 FROM booking_conversation_states s
                             WHERE s.organization_id=$1 AND s.conversation_id=messages.conversation_id)))
                       ORDER BY created_at,id LIMIT 500 FOR UPDATE SKIP LOCKED''', org, days, c24_allowed,
                )
                ids = [r['id'] for r in rows]
                if ids:
                    await conn.execute(
                        '''UPDATE messages SET content='{}',content_text=NULL,ai_reply_cache=NULL,
                               error_details=NULL,error_title=NULL,deleted_at=NOW()
                           WHERE organization_id=$1 AND id=ANY($2::uuid[])''', org, ids,
                    )
                    await conn.execute(
                        '''UPDATE event_log SET testo_originale='',risposta_ai='',dettagli='{}'
                           WHERE organization_id=$1 AND source_id=ANY($2::uuid[])''', org, ids,
                    )
                counts['messages_redacted'] += len(ids)
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
    logger.info('gdpr_retention exports_expired=%d messages_redacted=%d simulator_purged=%d', *counts.values())
    return counts
