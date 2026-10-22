"""Durable Meta ingress. No provider calls or LLM generation at acceptance time."""
import hashlib
import json
import uuid

from src.core.db.scoping import TenantScopedRepository, system_scope


class WebhookInboxRepository(TenantScopedRepository):
    def __init__(self, pool):
        self.pool = pool

    async def enqueue(self, organization_id, channel, payload, trace_id):
        from src.core.gdpr.guard import ErasureBlocked, filter_webhook_payload

        async with self.scoped_conn(organization_id) as conn, conn.transaction():
            try:
                payload = await filter_webhook_payload(conn, organization_id, payload)
            except ErasureBlocked:
                return  # Acknowledge without persisting a suppressed subject.
            if payload is None:
                return
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            key = hashlib.sha256(serialized.encode()).hexdigest()
            await conn.execute("""
                INSERT INTO meta_webhook_inbox
                    (id, organization_id, channel, event_key, payload, trace_id)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                ON CONFLICT (organization_id, channel, event_key) DO NOTHING
            """, uuid.uuid4(), organization_id, channel, key, serialized, str(trace_id)[:128])

    @system_scope("trusted worker: global durable inbox SKIP LOCKED claim, tenant-bound processing")
    async def claim(self, limit=10):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                WITH candidates AS (
                    SELECT id FROM meta_webhook_inbox
                    WHERE (status = 'pending' AND next_attempt_at <= NOW())
                       OR (status = 'processing' AND lease_until < NOW())
                    ORDER BY next_attempt_at LIMIT $1 FOR UPDATE SKIP LOCKED
                )
                UPDATE meta_webhook_inbox w
                SET status = 'processing', attempts = attempts + 1,
                    lease_token = $2, lease_until = NOW() + INTERVAL '2 minutes'
                FROM candidates c WHERE w.id = c.id RETURNING w.*
            """, max(1, min(limit, 100)), uuid.uuid4())

    async def fail(self, row, error_type):
        async with self.scoped_conn(row["organization_id"]) as conn:
            await conn.execute("""
                UPDATE meta_webhook_inbox SET
                    status = CASE WHEN attempts >= 10 THEN 'dead_letter' ELSE 'pending' END,
                    next_attempt_at = NOW() + LEAST(attempts * attempts * 10, 3600) * INTERVAL '1 second',
                    lease_token = NULL, lease_until = NULL, last_error = $4
                WHERE id = $1 AND organization_id = $2 AND lease_token = $3
            """, row["id"], row["organization_id"], row["lease_token"], error_type[:100])

    @system_scope("retention: purge completed raw payloads after seven days; retain unresolved inbox")
    async def purge_completed(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM meta_webhook_inbox
                WHERE status = 'completed' AND completed_at < NOW() - INTERVAL '7 days'
            """)
