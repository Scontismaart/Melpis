import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from src.core.db.scoping import TenantScopedRepository, system_scope

STATUS_RANK = {
    "queued": 0,
    "processing": 0,
    "sending_ambiguous": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    "failed": 4,
}


def apply_status_update(current_status: str, new_status: str) -> bool:
    if new_status == "failed":
        return True
    if new_status == "sending_ambiguous" and current_status in ("queued", "processing"):
        # Pre-mark Send-Then-Mark: mossa laterale consentita prima della chiamata Meta.
        return True
    return STATUS_RANK.get(new_status, 0) > STATUS_RANK.get(current_status, 0)


class MessageRepository(TenantScopedRepository):
    """Repository specializzato per Code Messaggi, Claim atomici (P0), Retry e Deduplica."""

    def __init__(self, pool):
        self.pool = pool

    # ── Upsert & Status Updates ───────────────────────────────────

    async def upsert_message(self, id, organization_id, conversation_id, wam_id, direction,
                              message_type, content, content_text, status, handling_type=None,
                              idempotency_key=None, conn=None):
        if conn is None:
            async with self.scoped_conn(organization_id) as conn:
                if direction == "inbound":
                    async with conn.transaction():
                        return await self._upsert_message(conn, id, organization_id, conversation_id,
                            wam_id, direction, message_type, content, content_text, status,
                            handling_type, idempotency_key)
                return await self._upsert_message(conn, id, organization_id, conversation_id,
                    wam_id, direction, message_type, content, content_text, status,
                    handling_type, idempotency_key)
        return await self._upsert_message(conn, id, organization_id, conversation_id,
            wam_id, direction, message_type, content, content_text, status,
            handling_type, idempotency_key)

    async def _upsert_message(self, conn, id, organization_id, conversation_id, wam_id, direction,
                               message_type, content, content_text, status, handling_type=None,
                               idempotency_key=None):
        if direction == "inbound":
            # Shared ordering fence with A05 booking finalization. Callers that
            # pass a connection must hold an enclosing transaction.
            if hasattr(conn, "is_in_transaction") and not conn.is_in_transaction():
                raise RuntimeError("Inbound insertion requires a transaction")
            locked = await conn.fetchval(
                "SELECT id FROM conversations WHERE id=$1 AND organization_id=$2 FOR UPDATE",
                conversation_id, organization_id,
            )
            if locked is None:
                raise ValueError("Inbound conversation does not belong to organization")
        if idempotency_key:
            row = await conn.fetchrow("""
                INSERT INTO messages (id, organization_id, conversation_id, wam_id,
                                      direction, message_type, content, content_text,
                                      status, handling_type, idempotency_key)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11)
                ON CONFLICT (organization_id, idempotency_key) WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                RETURNING *
            """, id, organization_id, conversation_id, wam_id, direction, message_type,
                json.dumps(content), content_text, status, handling_type, idempotency_key)
            if row:
                return dict(row)
            row = await conn.fetchrow(
                "SELECT * FROM messages WHERE organization_id = $1 AND idempotency_key = $2",
                organization_id, idempotency_key,
            )
            return dict(row)
        row = await conn.fetchrow("""
            INSERT INTO messages (id, organization_id, conversation_id, wam_id,
                                  direction, message_type, content, content_text, status, handling_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10)
            ON CONFLICT (wam_id) WHERE wam_id IS NOT NULL DO NOTHING
            RETURNING *
        """, id, organization_id, conversation_id, wam_id, direction, message_type,
            json.dumps(content), content_text, status, handling_type)
        if row:
            return dict(row)
        row = await conn.fetchrow(
            "SELECT * FROM messages WHERE wam_id = $1 AND organization_id = $2",
            wam_id, organization_id,
        )
        return dict(row) if row else None

    async def update_message_status(self, message_id, new_status, wam_id=None, error_code=None,
                                      error_title=None, error_details=None, biz_opaque_callback_data=None,
                                      *, organization_id):
        async with self.scoped_conn(organization_id) as conn:
            current = await conn.fetchrow(
                "SELECT status FROM messages WHERE id = $1 AND organization_id = $2::uuid",
                message_id, organization_id,
            )
            if not current:
                return None
            if not apply_status_update(current["status"], new_status):
                return dict(current)
            row = await conn.fetchrow(
                """UPDATE messages
                   SET status = $3,
                       wam_id = COALESCE(NULLIF($4, ''), wam_id),
                       error_code = COALESCE(NULLIF($5, ''), error_code),
                       error_title = COALESCE(NULLIF($6, ''), error_title),
                       error_details = COALESCE($7::jsonb, error_details),
                       sent_at = CASE WHEN $3 = 'sent' THEN NOW() ELSE sent_at END,
                       delivered_at = CASE WHEN $3 = 'delivered' THEN NOW() ELSE delivered_at END,
                       read_at = CASE WHEN $3 = 'read' THEN NOW() ELSE read_at END,
                       updated_at = NOW()
                   WHERE id = $1 AND organization_id = $2::uuid
                   RETURNING *""",
                message_id, organization_id, new_status,
                wam_id or None, error_code or None, error_title or None,
                json.dumps(error_details) if error_details else None,
            )
            return dict(row) if row else None

    async def update_message_status_by_wam_id(self, wam_id, new_status, error_code=None,
                                                error_title=None, error_details=None,
                                                *, organization_id):
        async with self.scoped_conn(organization_id) as conn:
            current = await conn.fetchrow(
                "SELECT id, status FROM messages WHERE wam_id = $1 AND organization_id = $2::uuid",
                wam_id, organization_id,
            )
            if not current:
                return None
            return await self.update_message_status(
                current["id"], new_status, wam_id=wam_id,
                error_code=error_code, error_title=error_title, error_details=error_details,
                organization_id=organization_id,
            )

    # ── Claim Atomici & Concorrenza P0 ────────────────────────────

    async def record_processing_failure(self, message_id, organization_id, handling_type):
        async with self.scoped_conn(organization_id) as conn:
            await conn.execute("""
                UPDATE messages SET handling_type = $3, status = 'received_pending_ai',
                    processing_at = NULL, claimed_at = NULL, heartbeat_at = NULL, updated_at = NOW()
                WHERE id = $1 AND organization_id = $2 AND direction = 'inbound'
                  AND replied_at IS NULL
            """, message_id, organization_id, handling_type)

    async def claim_outbound_delivery(self, message_id, *, organization_id):
        """One sender only. An ambiguous request is NEVER reclaimed by a timer."""
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow("""
                UPDATE messages SET status = 'sending_ambiguous', updated_at = NOW()
                WHERE id = $1 AND organization_id = $2::uuid
                  AND direction = 'outbound' AND status IN ('queued', 'failed')
                  AND wam_id IS NULL AND deleted_at IS NULL
                RETURNING *
            """, message_id, organization_id)
            return dict(row) if row else None

    @system_scope("worker queue: claim globale SKIP LOCKED, solo background job fidati")
    async def claim_inbound_messages(self, limit=10):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch("""
                    UPDATE messages SET status = 'processing', claimed_at = NOW(),
                        heartbeat_at = NOW()
                    WHERE id IN (
                        SELECT id FROM messages
                        WHERE direction = 'inbound' AND status = 'received_pending_ai'
                        AND deleted_at IS NULL AND replied_at IS NULL
                        ORDER BY created_at
                        LIMIT $1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING *,
                        (SELECT c.canale FROM conversations c
                         WHERE c.id = messages.conversation_id) AS canale
                """, limit)
                return [dict(r) for r in rows]

    async def claim_message_and_check_quota(self, msg_id: str, org_id: str) -> dict:
        raise RuntimeError("Legacy pre-charge is disabled; use claim_message_for_processing and A04")

    async def claim_message_for_processing(self, msg_id: str, org_id: str) -> dict:
        """Claim an inbound message without charging the AI answer counter.

        A04 reserves only after the worker has selected an AI final-answer path.
        Rows carrying an old pre-charge without a cached response are fenced for
        reconciliation instead of being silently adopted by the new protocol.
        """
        async with self.scoped_conn(org_id) as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """SELECT id, direction, status, billed_at, ai_reply_cache, sent_at, replied_at,
                              quota_exceeded_at, processing_at, deleted_at
                         FROM messages
                        WHERE id=$1::uuid AND organization_id=$2::uuid FOR UPDATE""",
                    msg_id, org_id,
                )
                if not row or row["deleted_at"] is not None:
                    return {"status": "not_found"}
                if row["direction"] != "inbound":
                    return {"status": "invalid_direction"}
                if row["sent_at"] is not None or row["replied_at"] is not None:
                    return {"status": "already_sent"}
                if row["quota_exceeded_at"] is not None:
                    return {"status": "quota_exceeded"}
                cache_val = row["ai_reply_cache"]
                if isinstance(cache_val, str):
                    try:
                        cache_val = json.loads(cache_val)
                    except Exception:
                        cache_val = {"text": cache_val, "richiede_umano": False}
                if row["billed_at"] is not None and cache_val is None:
                    return {"status": "legacy_charge_pending"}
                if row["status"] not in {"received_pending_ai", "processing"}:
                    return {"status": "invalid_state"}
                if (row["processing_at"] is not None and cache_val is None
                        and (datetime.now(timezone.utc) - row["processing_at"]).total_seconds() < 30):
                    return {"status": "currently_processing"}
                if cache_val is None:
                    await conn.execute(
                        """UPDATE messages
                              SET status='processing', claimed_at=COALESCE(claimed_at, now()),
                                  heartbeat_at=now(), processing_at=now(), updated_at=now()
                            WHERE id=$1::uuid AND organization_id=$2::uuid
                              AND direction='inbound'
                              AND status IN ('received_pending_ai','processing')""",
                        msg_id, org_id,
                    )
                return {"status": "claimed", "ai_reply_cache": cache_val}

    async def try_mark_replied(self, message_id, handling_type: str | None = None, *,
                               organization_id):
        """Atomically marks a message as replied+handled."""
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow("""
                UPDATE messages SET replied_at = NOW(), status = 'handled',
                    handling_type = COALESCE($2, handling_type),
                    updated_at = NOW()
                WHERE id = $1 AND organization_id = $3::uuid AND replied_at IS NULL
                RETURNING *
            """, message_id, handling_type, organization_id)
            return dict(row) if row else None

    async def update_heartbeat(self, message_id, organization_id):
        """Periodic heartbeat — tells the reaper this claim is still alive."""
        async with self.scoped_conn(organization_id) as conn:
            await conn.execute(
                "UPDATE messages SET heartbeat_at = NOW() WHERE id = $1 AND organization_id = $2::uuid",
                message_id, organization_id,
            )

    @system_scope("worker queue: claim globale SKIP LOCKED, solo background job fidati")
    async def claim_delivery_attempts(self, limit=10):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch("""
                    SELECT a.*, m.organization_id
                    FROM message_delivery_attempts a
                    JOIN messages m ON m.id = a.message_id
                    WHERE a.status = 'pending' AND a.next_retry_at <= NOW()
                    ORDER BY a.next_retry_at
                    LIMIT $1
                    FOR UPDATE OF a SKIP LOCKED
                """, limit)
                if rows:
                    ids = [r["id"] for r in rows]
                    await conn.execute("""
                        UPDATE message_delivery_attempts a
                        SET status = 'processing', claimed_at = NOW()
                        FROM messages m
                        WHERE m.id = a.message_id AND a.id = ANY($1)
                    """, ids)
                return [dict(r) for r in rows]

    @system_scope("tabella indiretta (via messages), solo worker")
    async def insert_delivery_attempt(self, message_id, next_retry_at):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO message_delivery_attempts (id, message_id, next_retry_at)
                SELECT $1, m.id, $3
                FROM messages m
                WHERE m.id = $2
                RETURNING *
            """, uuid.uuid4(), message_id, next_retry_at)
            return dict(row)

    @system_scope("A05 booking confirmation outbox: enqueue only after canonical reply")
    async def enqueue_ready_booking_confirmations(self, limit=20):
        """Schedule durable confirmations after the corresponding AI reply.

        Booking and outbound content were already committed atomically. This
        short transaction only makes the existing retry worker deliver them.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch("""
                    SELECT o.id,o.outbound_message_id
                    FROM booking_confirmation_outbox o
                    LEFT JOIN messages inbound ON inbound.id=o.inbound_message_id
                      AND inbound.organization_id=o.organization_id
                    JOIN messages outbound ON outbound.id=o.outbound_message_id
                      AND outbound.organization_id=o.organization_id
                    JOIN bookings b ON b.id=o.booking_id
                      AND b.organization_id=o.organization_id
                    WHERE o.status='waiting_for_reply'
                      AND (o.inbound_message_id IS NULL OR inbound.replied_at IS NOT NULL)
                      AND b.stato='confermata'
                      AND b.calendar_revision=o.booking_revision
                      AND outbound.status='queued'
                    ORDER BY o.created_at LIMIT $1
                    FOR UPDATE OF o SKIP LOCKED
                """, limit)
                for row in rows:
                    await conn.execute("""
                        INSERT INTO message_delivery_attempts
                            (id,message_id,next_retry_at)
                        VALUES ($1,$2,NOW())
                        ON CONFLICT DO NOTHING
                    """, uuid.uuid4(), row["outbound_message_id"])
                    await conn.execute("""
                        UPDATE booking_confirmation_outbox
                        SET status='scheduled',scheduled_at=NOW() WHERE id=$1
                    """, row["id"])
                return len(rows)

    @asynccontextmanager
    async def booking_confirmation_delivery_guard(self, organization_id, message_id):
        """Serialize an A05 confirmation send with booking cancellation.

        The booking row stays locked through the provider handoff. A
        cancellation that committed first suppresses the notice; one that
        follows observes a confirmation that was sent while still valid.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                state = await conn.fetchval("""
                    SELECT b.stato FROM booking_confirmation_outbox o
                    JOIN bookings b ON b.id=o.booking_id
                      AND b.organization_id=o.organization_id
                    WHERE o.organization_id=$1 AND b.organization_id=$1 AND o.outbound_message_id=$2
                      AND b.calendar_revision=o.booking_revision
                    FOR UPDATE OF b
                """, uuid.UUID(str(organization_id)), uuid.UUID(str(message_id)))
                yield state == "confermata"

    @system_scope("tabella indiretta (via messages), solo worker")
    async def update_delivery_attempt(self, attempt_id, status, error_details=None):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE message_delivery_attempts a
                SET status = $2, error_details = $3::jsonb
                FROM messages m
                WHERE m.id = a.message_id AND a.id = $1
                RETURNING a.*
            """, attempt_id, status, json.dumps(error_details) if error_details else None)
            return dict(row) if row else None

    @system_scope("retry worker: org letta dal payload e riusata a valle")
    async def reconstruct_payload_for_retry(self, message_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM messages WHERE id = $1", message_id
            )
            if not row:
                return None
            result = dict(row)
            if isinstance(result.get("content"), str):
                result["content"] = json.loads(result["content"])
            return result

    @system_scope("retention/reaper globale: manutenzione cross-tenant programmata")
    async def reap_stale_claims(self, timeout_minutes=15, dead_letter_threshold=3):
        """Libera i claim rimasti bloccati oltre timeout_minutes."""
        async with self.pool.acquire() as conn:
            dead = await conn.fetch("""
                UPDATE messages SET status = 'dead', claimed_at = NULL
                WHERE status = 'processing'
                AND (
                    (heartbeat_at IS NOT NULL AND heartbeat_at < NOW() - ($1 || ' minutes')::INTERVAL)
                    OR
                    (heartbeat_at IS NULL AND claimed_at < NOW() - ($1 || ' minutes')::INTERVAL)
                )
                AND dead_letter_count >= $2
                RETURNING *
            """, str(timeout_minutes), dead_letter_threshold)
            msgs = await conn.fetch("""
                UPDATE messages SET status = 'received_pending_ai', claimed_at = NULL,
                    heartbeat_at = NULL, dead_letter_count = dead_letter_count + 1
                WHERE status = 'processing'
                AND (
                    (heartbeat_at IS NOT NULL AND heartbeat_at < NOW() - ($1 || ' minutes')::INTERVAL)
                    OR
                    (heartbeat_at IS NULL AND claimed_at < NOW() - ($1 || ' minutes')::INTERVAL)
                )
                RETURNING *
            """, str(timeout_minutes))
            attempts = await conn.fetch("""
                UPDATE message_delivery_attempts a SET status = 'pending', claimed_at = NULL
                WHERE a.status = 'processing'
                  AND a.claimed_at < NOW() - ($1 || ' minutes')::INTERVAL
                  AND EXISTS (SELECT 1 FROM messages m WHERE m.id = a.message_id)
                RETURNING *
            """, str(timeout_minutes))
            return [dict(r) for r in dead] + [dict(r) for r in msgs] + [dict(r) for r in attempts]

    @system_scope("retention/reaper globale: manutenzione cross-tenant programmata")
    async def delete_expired_messages(self, retention_days: int = 60) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE messages SET deleted_at = NOW()
                WHERE deleted_at IS NULL
                AND created_at < NOW() - ($1 || ' days')::INTERVAL
            """, str(retention_days))
            return int(result.split()[-1]) if result else 0

    @system_scope("retention/reaper globale: manutenzione cross-tenant programmata")
    async def purge_soft_deleted_messages(self, grace_days: int = 30) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM messages
                WHERE deleted_at IS NOT NULL
                AND deleted_at < NOW() - ($1 || ' days')::INTERVAL
            """, str(grace_days))
            return int(result.split()[-1]) if result else 0

    async def get_outbound_dedup(self, organization_id, message_id) -> dict | None:
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow("""
                SELECT response_text FROM outbound_dedup
                WHERE message_id = $2 AND organization_id = $1
            """, organization_id, message_id)
            return dict(row) if row else None

    async def save_outbound_dedup(self, message_id: uuid.UUID, org_id: uuid.UUID, response_text: str):
        async with self.scoped_conn(org_id) as conn:
            await conn.execute("""
                INSERT INTO outbound_dedup (message_id, organization_id, response_text)
                VALUES ($1, $2, $3)
                ON CONFLICT (message_id) DO UPDATE
                    SET response_text = EXCLUDED.response_text
                    WHERE outbound_dedup.organization_id = EXCLUDED.organization_id
            """, message_id, org_id, response_text)

    async def get_last_ai_outbound_message(self, organization_id,
                                           conversation_id) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM messages
                WHERE organization_id = $1::uuid
                  AND conversation_id = $2::uuid
                  AND direction = 'outbound'
                  AND handling_type = 'ai_handled'
                  AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
            """, organization_id, conversation_id)
            return dict(row) if row else None

    async def get_message_org_scoped(self, organization_id, message_id) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM messages
                WHERE id = $2::uuid AND organization_id = $1::uuid
                  AND deleted_at IS NULL
            """, organization_id, message_id)
            return dict(row) if row else None

    async def registra_feedback(self, organization_id, message_id, conversation_id,
                                source: str, value: str,
                                created_by_user_id=None) -> dict:
        async with self.scoped_conn(organization_id) as conn:
            if source == "customer_emoji":
                row = await conn.fetchrow("""
                    INSERT INTO message_feedback
                        (id, organization_id, message_id, conversation_id, source, value)
                    VALUES ($1, $2::uuid, $3::uuid, $4::uuid, 'customer_emoji', $5)
                    ON CONFLICT (message_id) WHERE source = 'customer_emoji'
                        DO UPDATE SET value = EXCLUDED.value
                        WHERE message_feedback.organization_id = EXCLUDED.organization_id
                    RETURNING *
                """, uuid.uuid4(), organization_id, message_id, conversation_id, value)
            else:
                row = await conn.fetchrow("""
                    INSERT INTO message_feedback
                        (id, organization_id, message_id, conversation_id, source,
                         value, created_by_user_id)
                    VALUES ($1, $2::uuid, $3::uuid, $4::uuid, 'staff_ui', $5, $6::uuid)
                    ON CONFLICT (message_id, created_by_user_id) WHERE source = 'staff_ui'
                        DO UPDATE SET value = EXCLUDED.value
                        WHERE message_feedback.organization_id = EXCLUDED.organization_id
                    RETURNING *
                """, uuid.uuid4(), organization_id, message_id, conversation_id,
                    value, created_by_user_id)
            return dict(row)

    async def check_idempotency(self, org_id: str, idempotency_key: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM messages WHERE organization_id = $1::uuid AND idempotency_key = $2",
                org_id, idempotency_key
            )
            return dict(row) if row else None

    async def check_booking_exists(self, msg_id: str, org_id: str) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM bookings WHERE organization_id = $1::uuid AND source_message_id = $2",
                org_id, str(msg_id)
            )
            return bool(row)

    async def save_ai_reply(self, msg_id: str, reply: dict | str, richiede_umano: bool = False,
                            motivo: str = "", *, organization_id) -> None:
        raise RuntimeError("Direct AI reply writes are disabled; use the atomic A04 commit")

    async def mark_message_sent(self, msg_id: str, meta_message_id: str, organization_id) -> None:
        async with self.scoped_conn(organization_id) as conn:
            await conn.execute(
                "UPDATE messages SET sent_at = now(), meta_message_id = $2 WHERE id = $1::uuid AND organization_id = $3::uuid",
                msg_id, str(meta_message_id), organization_id
            )

    async def get_messages_by_org(self, organization_id: uuid.UUID | str) -> list[dict]:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM messages WHERE organization_id = $1 AND deleted_at IS NULL ORDER BY created_at DESC",
                organization_id,
            )
            return [dict(r) for r in rows]

    async def reserve_simulation_request(
        self,
        organization_id: uuid.UUID | str,
        auth_user_id: uuid.UUID | str,
        request_id: uuid.UUID | str,
        payload_hash: str,
    ) -> dict:
        raise RuntimeError("Legacy simulator pre-charge is disabled; use QuotaReservations")

    async def complete_simulation_request(
        self,
        organization_id: uuid.UUID | str,
        auth_user_id: uuid.UUID | str,
        request_id: uuid.UUID | str,
        payload_hash: str,
        claim_token: uuid.UUID | str,
        response: dict,
    ) -> bool:
        raise RuntimeError("Legacy simulator completion is disabled; use QuotaReservations")

    async def fail_simulation_request(
        self,
        organization_id: uuid.UUID | str,
        auth_user_id: uuid.UUID | str,
        request_id: uuid.UUID | str,
        payload_hash: str,
        claim_token: uuid.UUID | str,
    ) -> bool:
        raise RuntimeError("Legacy simulator failure write is disabled; use QuotaReservations")

    async def get_simulation_requests_by_org(self, organization_id: uuid.UUID | str) -> list[dict]:
        org_id = uuid.UUID(str(organization_id))
        async with self.scoped_conn(org_id) as conn:
            rows = await conn.fetch(
                """
                SELECT auth_user_id, request_id, payload_hash, status, response,
                       created_at, completed_at
                FROM simulation_requests
                WHERE organization_id = $1::uuid
                ORDER BY created_at
                """,
                org_id,
            )
            return [dict(row) for row in rows]

    @system_scope("retention reaper globale: scadenza risposte cache del simulatore")
    async def purge_simulation_requests(self, retention_days: int = 30) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM simulation_requests
                WHERE created_at < NOW() - ($1 || ' days')::INTERVAL
                """,
                str(retention_days),
            )
            return int(result.split()[-1]) if result else 0
