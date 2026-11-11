import uuid
from src.core.db.scoping import TenantScopedRepository, system_scope


class ConversationRepository(TenantScopedRepository):
    """Repository specializzato per Conversazioni, Storico Chat e Gestione Ticket HITL."""

    def __init__(self, pool):
        self.pool = pool

    async def get_or_create_conversation(self, org_id, contact_id, canale: str = "whatsapp"):
        """canale: origine della conversazione. L'identita' del contatto
        (contacts.phone_number) e' gia' channel-agnostic (numero WA o IG id),
        quindi una conversazione nuova nasce col canale del primo messaggio."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO conversations (id, organization_id, contact_id, canale)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (organization_id, contact_id) DO UPDATE
                    SET last_message_at = NOW()
                RETURNING *
            """, uuid.uuid4(), org_id, contact_id, canale)
            return dict(row)

    @system_scope("retention job globale: pulizia conversazioni orfane senza messaggi")
    async def cleanup_empty_conversations(self) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE conversations SET deleted_at = NOW()
                WHERE deleted_at IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM messages
                    WHERE messages.conversation_id = conversations.id
                    AND messages.deleted_at IS NULL
                )
            """)
            return int(result.split()[-1]) if result else 0

    async def get_conversations_by_org(self, organization_id: uuid.UUID | str) -> list[dict]:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM conversations WHERE organization_id = $1 AND deleted_at IS NULL ORDER BY created_at DESC",
                organization_id,
            )
            return [dict(r) for r in rows]

    # ── HITL: Ticket State Machine & Inbox ─────────────────────────

    async def list_tickets(self, org_id: str, status: str | None = None, priorita: str | None = None, limit: int | None = None, offset: int = 0) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """WITH enriched AS (
                       SELECT c.*, u.nome AS assigned_nome, u.email AS assigned_email,
                              ct.phone_number AS phone_number,
                              o.sla_minutes,
                              (c.pending_staff_at + (o.sla_minutes || ' minutes')::interval) AS sla_due_at,
                              (c.pending_staff_at + (o.sla_minutes || ' minutes')::interval) < NOW() AS is_overdue,
                              COALESCE(el.priorita,
                                       CASE WHEN c.ticket_status IN ('PENDING_STAFF', 'CLAIMED') THEN 'alta'
                                            ELSE 'media' END) AS priorita,
                              lm.content_text AS last_message_preview,
                              (c.ticket_status <> 'RESOLVED' AND EXISTS (
                                  SELECT 1 FROM messages failed
                                  WHERE failed.organization_id = $1::uuid
                                    AND failed.conversation_id = c.id
                                    AND failed.deleted_at IS NULL
                                    AND failed.handling_type = 'escalation_failed'
                                    AND (c.resolved_at IS NULL OR failed.created_at > c.resolved_at)
                              )) AS escalation_failed
                       FROM conversations c
                       LEFT JOIN user_profiles u ON u.id = c.assigned_to
                       LEFT JOIN contacts ct ON ct.id = c.contact_id
                                                AND ct.organization_id = $1::uuid
                       JOIN organizations o ON o.id = c.organization_id
                       LEFT JOIN LATERAL (
                           SELECT e.priorita
                           FROM event_log e
                           WHERE e.organization_id = $1::uuid
                             AND e.dettagli->>'conversation_id' = c.id::text
                           ORDER BY CASE e.priorita WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END,
                                    e.created_at DESC
                           LIMIT 1
                       ) el ON TRUE
                       LEFT JOIN LATERAL (
                           SELECT m.content_text
                           FROM messages m
                           WHERE m.organization_id = $1::uuid
                             AND m.conversation_id = c.id AND m.deleted_at IS NULL
                             AND (CASE WHEN to_regclass('public.c24_idle_watermarks') IS NULL THEN m.created_at
                                  ELSE (to_jsonb(m)->>'c24_received_at')::timestamptz END) > NOW() - INTERVAL '60 days'
                           ORDER BY m.created_at DESC
                           LIMIT 1
                       ) lm ON TRUE
                       WHERE c.organization_id = $1::uuid
                         AND c.deleted_at IS NULL
                   )
                   SELECT * FROM enriched
                   WHERE ($2::text IS NULL OR ticket_status = $2)
                     AND ($3::text IS NULL OR priorita = $3)
                   ORDER BY pending_staff_at ASC NULLS LAST,
                            claimed_at ASC NULLS LAST,
                            created_at ASC
                   LIMIT $4::int OFFSET $5::int""",
                org_id, status, priorita, limit, offset
            )
            return [dict(r) for r in rows]

    async def get_conversation(self, conversation_id: str, organization_id) -> dict | None:
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow(
                """WITH enriched AS (
                       SELECT c.*, u.nome AS assigned_nome, u.email AS assigned_email,
                              ct.phone_number AS phone_number,
                              o.sla_minutes,
                              (c.pending_staff_at + (o.sla_minutes || ' minutes')::interval) AS sla_due_at,
                              (c.pending_staff_at + (o.sla_minutes || ' minutes')::interval) < NOW() AS is_overdue,
                              COALESCE(el.priorita,
                                       CASE WHEN c.ticket_status IN ('PENDING_STAFF', 'CLAIMED') THEN 'alta'
                                            ELSE 'media' END) AS priorita,
                              lm.content_text AS last_message_preview,
                              (c.ticket_status <> 'RESOLVED' AND EXISTS (
                                  SELECT 1 FROM messages failed
                                  WHERE failed.organization_id = $2::uuid
                                    AND failed.conversation_id = c.id
                                    AND failed.deleted_at IS NULL
                                    AND failed.handling_type = 'escalation_failed'
                                    AND (c.resolved_at IS NULL OR failed.created_at > c.resolved_at)
                              )) AS escalation_failed
                       FROM conversations c
                       LEFT JOIN user_profiles u ON u.id = c.assigned_to
                       LEFT JOIN contacts ct ON ct.id = c.contact_id
                                                AND ct.organization_id = $2::uuid
                       JOIN organizations o ON o.id = c.organization_id
                       LEFT JOIN LATERAL (
                           SELECT e.priorita
                           FROM event_log e
                           WHERE e.organization_id = $2::uuid
                             AND e.dettagli->>'conversation_id' = c.id::text
                           ORDER BY CASE e.priorita WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END,
                                    e.created_at DESC
                           LIMIT 1
                       ) el ON TRUE
                       LEFT JOIN LATERAL (
                           SELECT m.content_text
                           FROM messages m
                           WHERE m.organization_id = $2::uuid
                             AND m.conversation_id = c.id AND m.deleted_at IS NULL
                             AND (CASE WHEN to_regclass('public.c24_idle_watermarks') IS NULL THEN m.created_at
                                  ELSE (to_jsonb(m)->>'c24_received_at')::timestamptz END) > NOW() - INTERVAL '60 days'
                           ORDER BY m.created_at DESC
                           LIMIT 1
                       ) lm ON TRUE
                       WHERE c.organization_id = $2::uuid AND c.id = $1::uuid AND c.deleted_at IS NULL
                   )
                   SELECT * FROM enriched""",
                conversation_id, organization_id
            )
            return dict(row) if row else None

    async def list_conversation_messages(
        self, org_id: str, conversation_id: str, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        """Storico messaggi di una conversazione per l'inbox HITL."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT m.id, m.direction, m.message_type, m.content_text, m.status,
                          m.handling_type, m.created_at,
                          fc.feedback_customer,
                          COALESCE(fs.up, 0) AS feedback_staff_up,
                          COALESCE(fs.down, 0) AS feedback_staff_down,
                          COUNT(*) OVER() AS total
                   FROM messages m
                   LEFT JOIN LATERAL (
                       SELECT value AS feedback_customer
                       FROM message_feedback mf
                       WHERE mf.message_id = m.id
                         AND mf.organization_id = $2::uuid
                         AND mf.source = 'customer_emoji'
                       LIMIT 1
                   ) fc ON TRUE
                   LEFT JOIN LATERAL (
                       SELECT COUNT(*) FILTER (WHERE value = 'up') AS up,
                              COUNT(*) FILTER (WHERE value = 'down') AS down
                       FROM message_feedback mf
                       WHERE mf.message_id = m.id
                         AND mf.organization_id = $2::uuid
                         AND mf.source = 'staff_ui'
                   ) fs ON TRUE
                   WHERE m.conversation_id = $1::uuid
                     AND m.organization_id = $2::uuid
                     AND m.deleted_at IS NULL
                     AND (CASE WHEN to_regclass('public.c24_idle_watermarks') IS NULL THEN m.created_at
                          ELSE (to_jsonb(m)->>'c24_received_at')::timestamptz END) > NOW() - INTERVAL '60 days'
                   ORDER BY m.created_at ASC
                   LIMIT $3 OFFSET $4""",
                conversation_id, org_id, limit, offset
            )
            return [dict(r) for r in rows]

    async def escalate_to_human(self, conversation_id: str, organization_id) -> dict | None:
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow(
                """UPDATE conversations
                   SET ticket_status = 'PENDING_STAFF',
                       pending_staff_at = NOW(),
                       updated_at = NOW(),
                       version = version + 1
                   WHERE id = $1::uuid
                     AND organization_id = $2::uuid
                     AND ticket_status NOT IN ('PENDING_STAFF', 'CLAIMED', 'RESOLVED')
                     AND deleted_at IS NULL
                   RETURNING *""",
                conversation_id, organization_id
            )
            return dict(row) if row else None

    async def claim_ticket(self, conversation_id: str, staff_user_id: str, expected_version: int,
                           organization_id) -> dict | None:
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow(
                """UPDATE conversations
                   SET ticket_status = 'CLAIMED',
                       assigned_to = $2::uuid,
                       claimed_at = NOW(),
                       updated_at = NOW(),
                       version = version + 1
                   WHERE id = $1::uuid
                     AND organization_id = $4::uuid
                     AND version = $3
                     AND ticket_status = 'PENDING_STAFF'
                     AND deleted_at IS NULL
                   RETURNING *""",
                conversation_id, staff_user_id, expected_version, organization_id
            )
            return dict(row) if row else None

    async def release_ticket(self, conversation_id: str, staff_user_id: str,
                             organization_id) -> dict | None:
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow(
                """UPDATE conversations
                   SET ticket_status = 'PENDING_STAFF',
                       assigned_to = NULL,
                       claimed_at = NULL,
                       updated_at = NOW(),
                       version = version + 1
                   WHERE id = $1::uuid
                     AND organization_id = $3::uuid
                     AND assigned_to = $2::uuid
                     AND ticket_status = 'CLAIMED'
                     AND deleted_at IS NULL
                   RETURNING *""",
                conversation_id, staff_user_id, organization_id
            )
            return dict(row) if row else None

    async def resolve_ticket(self, conversation_id: str, staff_user_id: str,
                             organization_id) -> dict | None:
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow(
                """UPDATE conversations
                   SET ticket_status = 'RESOLVED',
                       assigned_to = NULL,
                       resolved_at = NOW(),
                       updated_at = NOW(),
                       version = version + 1
                   WHERE id = $1::uuid
                     AND organization_id = $3::uuid
                     AND assigned_to = $2::uuid
                     AND ticket_status = 'CLAIMED'
                     AND deleted_at IS NULL
                   RETURNING *""",
                conversation_id, staff_user_id, organization_id
            )
            return dict(row) if row else None

    async def assign_ticket(self, conversation_id: str, staff_user_id: str, expected_version: int,
                            organization_id) -> dict | None:
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow(
                """UPDATE conversations
                   SET ticket_status = 'CLAIMED',
                       assigned_to = $2::uuid,
                       claimed_at = NOW(),
                       updated_at = NOW(),
                       version = version + 1
                   WHERE id = $1::uuid
                     AND organization_id = $4::uuid
                     AND version = $3
                     AND ticket_status IN ('PENDING_STAFF', 'CLAIMED')
                     AND deleted_at IS NULL
                   RETURNING *""",
                conversation_id, staff_user_id, expected_version, organization_id
            )
            return dict(row) if row else None

    async def set_conversation_ai_active(self, conversation_id: str, organization_id) -> dict | None:
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow(
                """UPDATE conversations
                   SET ticket_status = 'AI_ACTIVE',
                       assigned_to = NULL,
                       updated_at = NOW(),
                       version = version + 1
                   WHERE id = $1::uuid AND organization_id = $2::uuid AND deleted_at IS NULL
                   RETURNING *""",
                conversation_id, organization_id
            )
            return dict(row) if row else None
