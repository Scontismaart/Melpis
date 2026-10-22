import json
import uuid

from src.core.db.scoping import TenantScopedRepository


class ContactRepository(TenantScopedRepository):
    """Repository specializzato per il dominio Contatti, Preferenze e Consensi GDPR."""

    def __init__(self, pool):
        self.pool = pool

    async def get_or_create_contact(self, org_id, phone):
        from src.core.gdpr.guard import check_contact_intake

        async with self.pool.acquire() as conn, conn.transaction():
            await check_contact_intake(conn, org_id, phone)
            row = await conn.fetchrow("""
                INSERT INTO contacts (id, organization_id, phone_number)
                VALUES ($1, $2, $3)
                ON CONFLICT (organization_id, phone_number) DO UPDATE
                    SET updated_at = NOW()
                RETURNING *
            """, uuid.uuid4(), org_id, phone)
            return dict(row)

    async def get_contact_prefs(self, org_id, phone):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM contacts
                WHERE organization_id = $1 AND phone_number = $2 AND deleted_at IS NULL
            """, org_id, phone)
            return dict(row) if row else None

    async def has_recent_whatsapp_inbound(self, org_id, phone: str) -> bool:
        """Only an actual inbound WhatsApp message opens the 24-hour test window."""
        async with self.scoped_conn(org_id) as conn:
            return bool(await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM messages AS m
                    JOIN conversations AS cv
                      ON cv.id = m.conversation_id
                     AND cv.organization_id = $1::uuid
                    JOIN contacts AS ct
                      ON ct.id = cv.contact_id
                     AND ct.organization_id = $1::uuid
                    WHERE m.organization_id = $1::uuid
                      AND ct.phone_number = $2
                      AND cv.canale = 'whatsapp'
                      AND m.direction = 'inbound'
                      AND m.created_at BETWEEN NOW() - INTERVAL '24 hours' AND NOW()
                      AND m.deleted_at IS NULL
                      AND cv.deleted_at IS NULL
                      AND ct.deleted_at IS NULL
                )
            """, org_id, phone))

    async def record_consent_event(self, contact_id, event_type, method,
                                     triggering_message_id=None, matched_text=None, *,
                                     organization_id):
        async with self.scoped_conn(organization_id) as conn, conn.transaction():
                # The contact lock serializes webhook retries even before the
                # partial unique message index is applied.
                contact = await conn.fetchrow("""
                    SELECT id FROM contacts
                    WHERE id = $1::uuid AND organization_id = $2::uuid
                      AND deleted_at IS NULL FOR UPDATE
                """, contact_id, organization_id)
                if not contact:
                    raise ValueError("Contact not found in organization")

                if triggering_message_id is not None:
                    existing = await conn.fetchrow("""
                        SELECT log.* FROM contact_consent_log AS log
                        JOIN contacts AS c ON c.id = log.contact_id
                        WHERE log.triggering_message_id = $1::uuid
                          AND c.id = $2::uuid AND c.organization_id = $3::uuid
                    """, triggering_message_id, contact_id, organization_id)
                    if existing:
                        return dict(existing)

                row = await conn.fetchrow("""
                    INSERT INTO contact_consent_log (id, contact_id, event_type, method,
                                                      triggering_message_id, matched_text)
                    SELECT $1, c.id, $3, $4, $5, $6
                    FROM contacts c
                    WHERE c.id = $2::uuid AND c.organization_id = $7::uuid
                    RETURNING *
                """, uuid.uuid4(), contact_id, event_type, method,
                    triggering_message_id, matched_text, organization_id)
                new_status = "granted" if event_type == "opt_in" else "withdrawn"
                await conn.execute("""
                    UPDATE contacts SET consent_status = $1,
                        marketing_opt_out = $2, consent_updated_at = NOW()
                    WHERE id = $3::uuid AND organization_id = $4::uuid
                """, new_status, event_type == "opt_out", contact_id, organization_id)
                await conn.execute("""
                    INSERT INTO audit_log
                        (id, organization_id, action, target_table, target_id, details)
                    VALUES ($1, $2::uuid, $3, 'contacts', $4::uuid, $5::jsonb)
                """, uuid.uuid4(), organization_id, f"consent.{event_type}",
                    contact_id, json.dumps({
                        "method": method,
                        "triggering_message_id": str(triggering_message_id)
                        if triggering_message_id else None,
                    }))
                return dict(row)

    async def get_contact_consent(self, contact_id, organization_id) -> str | None:
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow(
                "SELECT consent_status FROM contacts WHERE id = $1 AND organization_id = $2::uuid AND deleted_at IS NULL",
                contact_id, organization_id,
            )
            return row["consent_status"] if row else None

    async def mark_ai_disclosure_sent(self, contact_id: uuid.UUID, organization_id) -> bool:
        """Atomicamente segna il contatto come destinatario della disclosure AI.
        Ritorna True solo per il chiamante che vince la race (primo UPDATE);
        False se la disclosure era gia' stata segnata o il contatto non esiste."""
        async with self.scoped_conn(organization_id) as conn:
            row = await conn.fetchrow(
                """UPDATE contacts SET ai_disclosure_sent_at = NOW()
                   WHERE id = $1::uuid AND organization_id = $2::uuid AND ai_disclosure_sent_at IS NULL
                   RETURNING id""",
                contact_id, organization_id,
            )
            return row is not None

    async def get_contacts_by_org(self, organization_id: uuid.UUID | str) -> list[dict]:
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM contacts WHERE organization_id = $1 AND deleted_at IS NULL ORDER BY created_at DESC",
                organization_id,
            )
            return [dict(r) for r in rows]
