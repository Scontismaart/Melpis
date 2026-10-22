"""Bounded export expiry and explicitly approved, tenant-scoped retention.

Legacy constants are not legal approval. Primary-data retention is disabled
until C05 and cutover approve a versioned policy for each category.
"""
import logging
import os

from src.core.db.scoping import system_scope
from src.core.gdpr.operations import event

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
               AND category IN ('messages_content','simulator_responses')
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
                or policy['retention_days'] is None):
            continue
        org, days = policy['organization_id'], policy['retention_days']
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(org))
            current = await conn.fetchrow(
                '''SELECT version,retention_days,erasure_allowed,legal_hold,valid_until>NOW() AS valid
                   FROM gdpr_policies WHERE organization_id=$1 AND category=$2 AND approved_at<=NOW()
                   ORDER BY approved_at DESC,version DESC LIMIT 1''', org, policy['category'],
            )
            if (not current or not current['valid'] or current['legal_hold'] or not current['erasure_allowed']
                    or current['version'] != policy['version'] or current['retention_days'] is None):
                continue
            days = current['retention_days']
            if policy['category'] == 'messages_content':
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
            elif policy['category'] == 'simulator_responses':
                result = await conn.execute(
                    '''DELETE FROM simulation_requests WHERE organization_id=$1
                       AND (organization_id,auth_user_id,request_id) IN (
                         SELECT organization_id,auth_user_id,request_id FROM simulation_requests
                         WHERE organization_id=$1 AND status IN ('completed','failed')
                           AND completed_at<NOW()-make_interval(days=>$2)
                         ORDER BY completed_at LIMIT 500 FOR UPDATE SKIP LOCKED)''', org, days,
                )
                counts['simulator_purged'] += int(result.split()[-1])
    logger.info('gdpr_retention exports_expired=%d messages_redacted=%d simulator_purged=%d', *counts.values())
    return counts
