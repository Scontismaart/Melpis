"""Lease-based ingress worker; dedup and all ingestion writes share one transaction."""
import json
import logging
from contextlib import asynccontextmanager

from src.core.db.repositories.webhook_inbox_repo import WebhookInboxRepository
from src.core.db.inbound_worker_provenance import private_booking_worker_enabled, record_receipt

logger = logging.getLogger(__name__)


class TransactionPool:
    """Bind legacy repository acquisitions to the ingestion transaction.

    This object is local to one job. It never replaces the application's pool.
    Nested repository transactions become savepoints on the same connection.
    """
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection

    async def fetchrow(self, sql, *args, **kwargs):
        from src.whatsapp.idempotency import WEBHOOK_IDEMPOTENCY_SQL

        if sql != WEBHOOK_IDEMPOTENCY_SQL:
            raise ValueError("TransactionPool accepts only the webhook idempotency query")
        return await self.connection.fetchrow(sql, *args, **kwargs)


class WebhookInboxWorker:
    def __init__(self, wrepo, igrepo=None):
        self.pool = wrepo.pool
        self.inbox = WebhookInboxRepository(self.pool)

    async def process_next_batch(self, limit=10):
        rows = await self.inbox.claim(limit)
        for row in rows:
            try:
                await self._process_one(row)
            except Exception as exc:
                # Never log raw messages, credentials or provider payloads.
                logger.error("webhook_ingestion_failed organization_id=%s message_id=%s trace_id=%s error_type=%s",
                             row["organization_id"], row["id"], row["trace_id"], type(exc).__name__)
                await self.inbox.fail(row, type(exc).__name__)
        return len(rows)

    async def _process_one(self, row):
        from src.whatsapp.repository import Repository
        from src.instagram.repository import InstagramRepository

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                current = await conn.fetchrow("""
                    SELECT id FROM meta_webhook_inbox
                    WHERE id = $1 AND organization_id = $2 AND lease_token = $3
                      AND status = 'processing' FOR UPDATE
                """, row["id"], row["organization_id"], row["lease_token"])
                if not current:
                    return
                pool = TransactionPool(conn)
                repo = Repository(pool)
                payload = row["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                await self._ingest(repo, InstagramRepository(pool), row, payload)
                await conn.execute("""
                    UPDATE meta_webhook_inbox SET status = 'completed', completed_at = NOW(),
                        lease_token = NULL, lease_until = NULL, last_error = NULL
                    WHERE id = $1 AND organization_id = $2 AND lease_token = $3
                """, row["id"], row["organization_id"], row["lease_token"])

    async def _ingest(self, repo, igrepo, row, payload):
        org_id = row["organization_id"]
        if row["channel"] == "instagram":
            from src.instagram.models import InstagramWebhook
            from src.instagram.router import _handle_dm
            webhook = InstagramWebhook.model_validate(payload)
            for entry in webhook.entry:
                for event in entry.messaging:
                    await _handle_dm(repo, igrepo, entry.id, event,
                                     trace_id=row["trace_id"], expected_org_id=org_id)
        else:
            from src.whatsapp.models import IngoingWebhook
            from src.whatsapp.router import _handle_inbound_message, _handle_status_update
            webhook = IngoingWebhook.model_validate(payload)
            for entry in webhook.entry:
                for change in entry.changes:
                    value = change.value
                    if row["channel"] == "whatsapp_template":
                        await repo.update_template_status(
                            organization_id=org_id, name=value.message_template_name,
                            language=value.message_template_language, status=value.message_template_status,
                            rejected_reason=getattr(value, "reason", None))
                        continue
                    for status in value.statuses or []:
                        await _handle_status_update(repo, org_id, status)
                    for msg in value.messages or []:
                        await _handle_inbound_message(repo, org_id, msg, value.contacts, trace_id=row["trace_id"])
                        if private_booking_worker_enabled():
                            await record_receipt(repo.pool.connection, row, msg.id)
