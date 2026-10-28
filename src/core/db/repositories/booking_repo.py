import json
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import date, time
import asyncio

import asyncpg

from src.core.db.scoping import TenantScopedRepository

class BookingRepository(TenantScopedRepository):
    """Repository specializzato per Prenotazioni, Lock di Slot (P0) e Capienze."""

    def __init__(self, pool):
        self.pool = pool
        self._locked_connection = ContextVar("booking_locked_connection", default=None)

    @asynccontextmanager
    async def _connection(self):
        owned = self._locked_connection.get()
        if owned is not None and owned[0] is asyncio.current_task():
            yield owned[1]
        else:
            async with self.pool.acquire() as conn:
                yield conn

    @asynccontextmanager
    async def slot_lock(self, organization_id, data, ora):
        """Lock consultivo transazionale su una fascia oraria (anti double-booking)."""
        fascia = int(ora[:2]) if isinstance(ora, str) else ora.hour
        canonical_date = date.fromisoformat(data).isoformat() if isinstance(data, str) else data.isoformat()
        chiave = f"{organization_id}|{canonical_date}|{fascia}"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))", str(organization_id))
                await conn.fetchval("SELECT pg_advisory_xact_lock_shared(hashtext($1))", f"booking-capacity|{organization_id}")
                await conn.fetchval("SELECT pg_advisory_xact_lock(hashtext($1))", chiave)
                token = self._locked_connection.set((asyncio.current_task(), conn))
                try:
                    yield
                finally:
                    self._locked_connection.reset(token)

    @asynccontextmanager
    async def auto_booking_lock(self, organization_id, conversation_id, data, ora):
        """Serialize current customer intent and slot admission in one transaction.

        Every inbound message insert locks the same conversation row. Holding
        FOR UPDATE while the slot is checked and the booking/outbox are written
        makes a later cancellation wait; a cancellation already persisted is
        visible before this transaction can acquire the lock. No network calls
        belong inside this context.
        """
        fascia = int(ora[:2]) if isinstance(ora, str) else ora.hour
        canonical_date = date.fromisoformat(data).isoformat() if isinstance(data, str) else data.isoformat()
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))", str(organization_id))
                locked = await conn.fetchval(
                    "SELECT id FROM conversations WHERE id=$1 AND organization_id=$2 FOR UPDATE",
                    uuid.UUID(str(conversation_id)), uuid.UUID(str(organization_id)),
                )
                if locked is None:
                    raise ValueError("Conversation does not belong to organization")
                await conn.fetchval(
                    "SELECT pg_advisory_xact_lock_shared(hashtext($1))",
                    f"booking-capacity|{organization_id}",
                )
                await conn.fetchval(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"{organization_id}|{canonical_date}|{fascia}",
                )
                token = self._locked_connection.set((asyncio.current_task(), conn))
                try:
                    yield conn
                finally:
                    self._locked_connection.reset(token)

    @asynccontextmanager
    async def booking_settings_lock(self, organization_id):
        """Validate and publish A05 policy and capacity as one unit."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                org = await conn.fetchrow(
                    "SELECT timezone,business_profile FROM organizations WHERE id=$1 FOR SHARE",
                    uuid.UUID(str(organization_id)),
                )
                if org is None:
                    raise ValueError("Organization not found")
                await conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))", str(organization_id))
                await conn.fetchval(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"booking-capacity|{organization_id}",
                )
                token = self._locked_connection.set((asyncio.current_task(), conn))
                try:
                    yield dict(org)
                finally:
                    self._locked_connection.reset(token)

    async def save_booking_settings_locked(self, organization_id, *, slot_minutes,
                                           fasce_orarie, capienze_orarie, config):
        if not isinstance(config, dict) or "catalog_v2" in config:
            raise ValueError("Use the protected catalogue endpoint")
        owned = self._locked_connection.get()
        if owned is None or owned[0] is not asyncio.current_task():
            raise RuntimeError("Booking settings write requires the settings transaction")
        conn = owned[1]
        row = await conn.fetchrow("""
            INSERT INTO booking_settings (id, organization_id, slot_minutes,
                                          fasce_orarie, capienze_orarie, config)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb)
            ON CONFLICT (organization_id) DO UPDATE SET
                slot_minutes=EXCLUDED.slot_minutes,
                fasce_orarie=EXCLUDED.fasce_orarie,
                capienze_orarie=EXCLUDED.capienze_orarie,
                config=EXCLUDED.config || CASE
                    WHEN booking_settings.config ? 'catalog_v2'
                    THEN jsonb_build_object('catalog_v2',booking_settings.config->'catalog_v2')
                    ELSE '{}'::jsonb END,
                updated_at=NOW()
            RETURNING *
        """, uuid.uuid4(), uuid.UUID(str(organization_id)), slot_minutes,
            json.dumps(fasce_orarie), json.dumps(capienze_orarie), json.dumps(config))
        result = dict(row)
        # asyncpg returns JSONB as text here; keep the settings write contract
        # identical to get_booking_settings for callers of the service/API.
        for field in ("fasce_orarie", "capienze_orarie", "config"):
            if isinstance(result.get(field), str):
                result[field] = json.loads(result[field])
        return result

    async def create_booking(self, organization_id, nome_cliente, data, ora, coperti,
                             telefono="", note="", stato="in_attesa", origine="Dashboard",
                             richiede_intervento=False, id_conversazione=None,
                             contact_id=None, richiede_deposito=False,
                             completata_at=None, tipo_evento="", source_message_id=None):
        if isinstance(data, str):
            data = date.fromisoformat(data)
        if isinstance(ora, str):
            ore, minuti = ora.split(":")
            ora = time(int(ore), int(minuti))
        async with self._connection() as conn:
            if source_message_id:
                try:
                    row = await conn.fetchrow("""
                        INSERT INTO bookings (id, organization_id, contact_id,
                                              nome_cliente, telefono, data, ora, coperti,
                                              note, stato, origine, richiede_intervento,
                                              id_conversazione, richiede_deposito, completata_at,
                                              tipo_evento, source_message_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                        ON CONFLICT (organization_id, source_message_id) WHERE source_message_id IS NOT NULL
                            DO NOTHING
                        RETURNING *
                    """, uuid.uuid4(), organization_id, contact_id,
                    nome_cliente, telefono, data, ora, coperti,
                    note, stato, origine, richiede_intervento,
                    id_conversazione, richiede_deposito, completata_at, tipo_evento, source_message_id)
                except asyncpg.exceptions.UniqueViolationError:
                    row = None
                if not row:
                    row = await conn.fetchrow("""
                        SELECT * FROM bookings WHERE organization_id = $1 AND source_message_id = $2
                    """, organization_id, source_message_id)
                return dict(row) if row else None
            else:
                row = await conn.fetchrow("""
                    INSERT INTO bookings (id, organization_id, contact_id,
                                          nome_cliente, telefono, data, ora, coperti,
                                          note, stato, origine, richiede_intervento,
                                          id_conversazione, richiede_deposito, completata_at,
                                          tipo_evento)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                    RETURNING *
                """, uuid.uuid4(), organization_id, contact_id,
                nome_cliente, telefono, data, ora, coperti,
                note, stato, origine, richiede_intervento,
                id_conversazione, richiede_deposito, completata_at, tipo_evento)
                return dict(row)

    async def insert_auto_confirmed(self, organization_id, conversation_id,
                                    inbound_message_id, source_message_id, *,
                                    nome_cliente, telefono, data, ora, coperti,
                                    channel, authority):
        """Commit a confirmed booking and its notification in the A05 lock.

        The caller must have validated customer intent, policy and capacity
        under auto_booking_lock. No provider call is performed here.
        """
        owned = self._locked_connection.get()
        if owned is None or owned[0] is not asyncio.current_task():
            raise RuntimeError("Auto booking requires the booking transaction")
        if channel not in {"whatsapp", "instagram"} or not telefono:
            raise ValueError("Unsupported notification destination")
        conn = owned[1]
        org_uuid = uuid.UUID(str(organization_id))
        conversation_uuid = uuid.UUID(str(conversation_id))
        inbound_uuid = uuid.UUID(str(inbound_message_id))
        booking = await self.create_booking(
            org_uuid, nome_cliente, data, ora, coperti,
            telefono=telefono, note="", stato="confermata",
            origine="Instagram" if channel == "instagram" else "WhatsApp",
            id_conversazione=str(conversation_uuid),
            source_message_id=str(source_message_id),
        )
        if not booking or booking.get("stato") != "confermata":
            return booking

        await self.enqueue_calendar_confirmation(conn, booking, inbound_uuid, channel)
        if authority == "internal_calendar_sync":
            await conn.execute("""
                INSERT INTO external_booking_sync
                    (id,organization_id,idempotency_key,internal_booking_id,
                     provider,sync_status)
                VALUES ($1,$2,$3,$4,'google','pending')
                ON CONFLICT (organization_id,idempotency_key) DO NOTHING
            """, uuid.uuid4(), org_uuid,
                f"google-booking:{org_uuid}:{booking['id']}", booking["id"])
        return booking

    async def has_booking_confirmation_notice(self, organization_id, booking_id, revision):
        async with self._connection() as conn:
            return bool(await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM booking_confirmation_outbox
                WHERE organization_id=$1 AND booking_id=$2 AND booking_revision=$3)""",
                uuid.UUID(str(organization_id)), uuid.UUID(str(booking_id)), revision))

    async def enqueue_calendar_confirmation(self, conn, booking, inbound_message_id, channel):
        """Existing idempotent A05 notification, also used after Calendar proof."""
        org_uuid = booking["organization_id"]
        conversation_uuid = uuid.UUID(str(booking["id_conversazione"])) if booking["id_conversazione"] else None
        inbound_uuid = uuid.UUID(str(inbound_message_id)) if inbound_message_id else None
        source_message_id = booking["source_message_id"] if inbound_message_id else f"manual:{booking['id']}:{booking['calendar_revision']}"
        data, ora, coperti = booking["data"], booking["ora"], booking["coperti"]
        telefono = booking["telefono"]
        if not telefono:
            return
        if conversation_uuid is None:
            # Destination was explicitly supplied by staff on this booking.
            from src.core.gdpr.guard import check_contact_intake
            await check_contact_intake(conn, org_uuid, telefono)
            await conn.execute("""INSERT INTO contacts(id,organization_id,phone_number)
                VALUES($1,$2,$3) ON CONFLICT(organization_id,phone_number) DO NOTHING""", uuid.uuid4(), org_uuid, telefono)
            contact_id = await conn.fetchval("SELECT id FROM contacts WHERE organization_id=$1 AND phone_number=$2", org_uuid, telefono)
            await conn.execute("""INSERT INTO conversations(id,organization_id,contact_id)
                VALUES($1,$2,$3) ON CONFLICT(organization_id,contact_id) DO NOTHING""", uuid.uuid4(), org_uuid, contact_id)
            conversation_uuid = await conn.fetchval("SELECT id FROM conversations WHERE organization_id=$1 AND contact_id=$2", org_uuid, contact_id)
        day = data.isoformat() if isinstance(data, date) else str(data)
        slot = ora.strftime("%H:%M") if isinstance(ora, time) else str(ora)
        notice = f"Prenotazione confermata in Melpis per il {day} alle {slot}, {coperti} persone."
        payload = {"to": telefono, "type": "text", "text": {"body": notice}}
        if channel == "instagram":
            payload["channel"] = "instagram"
        else:
            payload["_delivery_category"] = "service"
        key = f"booking-confirm:{org_uuid}:{source_message_id}"
        message_id = uuid.uuid4()
        outbound = await conn.fetchrow("""
            INSERT INTO messages (id, organization_id, conversation_id, wam_id,
                                  direction, message_type, content, content_text,
                                  status, handling_type, idempotency_key)
            VALUES ($1,$2,$3,NULL,'outbound','text',$4::jsonb,$5,'queued',
                    'booking_confirmation',$6)
            ON CONFLICT (organization_id,idempotency_key)
                WHERE idempotency_key IS NOT NULL DO NOTHING
            RETURNING id,content
        """, message_id, org_uuid, conversation_uuid, json.dumps(payload), notice, key)
        if outbound is None:
            outbound = await conn.fetchrow("""
                SELECT id,content FROM messages
                WHERE organization_id=$1 AND idempotency_key=$2
            """, org_uuid, key)
            stored = outbound["content"] if outbound else None
            if isinstance(stored, str):
                stored = json.loads(stored)
            if stored != payload:
                raise ValueError("Auto booking notification idempotency conflict")
        await conn.execute("""
            INSERT INTO booking_confirmation_outbox
                (organization_id,booking_id,inbound_message_id,outbound_message_id,booking_revision)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (organization_id,booking_id,booking_revision) DO NOTHING
        """, org_uuid, booking["id"], inbound_uuid, outbound["id"], booking["calendar_revision"])

    async def insert_google_pending(self, organization_id, conversation_id, inbound_message_id,
                                    source_message_id, *, nome_cliente, telefono, data, ora,
                                    coperti, channel):
        owned = self._locked_connection.get()
        if owned is None or owned[0] is not asyncio.current_task():
            raise RuntimeError("Google admission requires the booking transaction")
        conn = owned[1]
        booking = await self.create_booking(
            uuid.UUID(str(organization_id)), nome_cliente, data, ora, coperti,
            telefono=telefono, stato="in_attesa", richiede_intervento=True,
            id_conversazione=str(conversation_id), source_message_id=str(source_message_id),
            origine="Instagram" if channel == "instagram" else "WhatsApp")
        booking = dict(await conn.fetchrow("""UPDATE bookings SET calendar_confirm_pending=TRUE
            WHERE organization_id=$1 AND id=$2 AND stato='in_attesa' RETURNING *""",
            uuid.UUID(str(organization_id)), booking["id"]))
        await conn.execute("""UPDATE external_booking_sync SET calendar_inbound_message_id=$3,
            calendar_notification_channel=$4 WHERE organization_id=$1 AND internal_booking_id=$2
            AND provider='google'""", uuid.UUID(str(organization_id)), booking["id"],
            uuid.UUID(str(inbound_message_id)), channel)
        return booking

    async def request_google_confirmation(self, organization_id, booking_id):
        async with self._connection() as conn:
            from src.core.gdpr.guard import check_contact_intake
            phone = await conn.fetchval("SELECT telefono FROM bookings WHERE organization_id=$1 AND id=$2",
                                       uuid.UUID(str(organization_id)), uuid.UUID(str(booking_id)))
            if phone:
                await check_contact_intake(conn, organization_id, phone)
            row = await conn.fetchrow("""UPDATE bookings SET stato='in_attesa',calendar_confirm_pending=TRUE,
                richiede_intervento=TRUE,updated_at=NOW() WHERE organization_id=$1 AND id=$2 RETURNING *""",
                uuid.UUID(str(organization_id)), uuid.UUID(str(booking_id)))
            if row and row["richiede_deposito"]:
                raise ValueError("Authoritative Calendar confirmation with a deposit requires staff review")
            if row:
                await conn.execute("""UPDATE external_booking_sync SET calendar_notification_channel='whatsapp'
                    WHERE organization_id=$1 AND internal_booking_id=$2 AND provider='google'
                      AND calendar_inbound_message_id IS NULL""", uuid.UUID(str(organization_id)), uuid.UUID(str(booking_id)))
            return dict(row) if row else None

    async def request_calendar_sync_retry(self, organization_id, booking_id):
        from src.core.calendar.repository import CalendarQueue
        if await CalendarQueue(self.pool).retry(organization_id, booking_id):
            return await self.get_booking(organization_id, booking_id)
        return None

    async def get_booking(self, organization_id, booking_id):
        async with self._connection() as conn:
            row = await conn.fetchrow(
                """SELECT b.*,x.provider AS external_sync_provider,
                          x.sync_status AS external_sync_status,
                          CASE WHEN x.sync_status='failed' THEN 'sync_failed'
                               WHEN x.sync_status='pending_retry' THEN 'retry_pending'
                               ELSE NULL END AS external_sync_error_code
                   FROM bookings b LEFT JOIN LATERAL (
                     SELECT provider,sync_status FROM external_booking_sync e
                     WHERE e.organization_id=$1 AND e.organization_id=b.organization_id
                       AND e.internal_booking_id=b.id
                     ORDER BY e.created_at DESC LIMIT 1
                   ) x ON TRUE
                   WHERE b.organization_id=$1 AND b.id=$2""",
                organization_id, booking_id,
            )
            return dict(row) if row else None

    async def list_bookings(self, organization_id, data=None):
        if data is not None and isinstance(data, str):
            data = date.fromisoformat(data)
        async with self._connection() as conn:
            if data is not None:
                rows = await conn.fetch(
                    """SELECT b.*,x.provider AS external_sync_provider,
                              x.sync_status AS external_sync_status,
                              CASE WHEN x.sync_status='failed' THEN 'sync_failed'
                                   WHEN x.sync_status='pending_retry' THEN 'retry_pending'
                                   ELSE NULL END AS external_sync_error_code
                       FROM bookings b LEFT JOIN LATERAL (
                         SELECT provider,sync_status FROM external_booking_sync e
                         WHERE e.organization_id=$1 AND e.organization_id=b.organization_id
                           AND e.internal_booking_id=b.id
                         ORDER BY e.created_at DESC LIMIT 1
                       ) x ON TRUE
                       WHERE b.organization_id=$1 AND b.data=$2 ORDER BY b.ora""",
                    organization_id, data,
                )
            else:
                rows = await conn.fetch(
                    """SELECT b.*,x.provider AS external_sync_provider,
                              x.sync_status AS external_sync_status,
                              CASE WHEN x.sync_status='failed' THEN 'sync_failed'
                                   WHEN x.sync_status='pending_retry' THEN 'retry_pending'
                                   ELSE NULL END AS external_sync_error_code
                       FROM bookings b LEFT JOIN LATERAL (
                         SELECT provider,sync_status FROM external_booking_sync e
                         WHERE e.organization_id=$1 AND e.organization_id=b.organization_id
                           AND e.internal_booking_id=b.id
                         ORDER BY e.created_at DESC LIMIT 1
                       ) x ON TRUE
                       WHERE b.organization_id=$1 ORDER BY b.data DESC,b.ora""",
                    organization_id,
                )
            return [dict(r) for r in rows]

    async def update_booking_status(self, organization_id, booking_id, stato, expected_status=None, expected=None):
        async with self._connection() as conn:
            row = await conn.fetchrow("""
                UPDATE bookings SET stato = $3, updated_at = NOW()
                WHERE organization_id = $1 AND id = $2
                  AND ($4::text IS NULL OR stato = $4)
                  AND ($5::timestamptz IS NULL OR updated_at = $5)
                  AND ($6::date IS NULL OR data = $6)
                  AND ($7::time IS NULL OR ora = $7)
                  AND ($8::integer IS NULL OR coperti = $8)
                RETURNING *
            """, organization_id, booking_id, stato, expected_status,
                expected.get("updated_at") if expected else None,
                expected.get("data") if expected else None,
                expected.get("ora") if expected else None,
                expected.get("coperti") if expected else None)
            return dict(row) if row else None

    async def mark_booking_completed(self, organization_id, booking_id, expected):
        async with self._connection() as conn:
            row = await conn.fetchrow("""
                UPDATE bookings SET stato = 'completata', completata_at = NOW(), updated_at = NOW()
                WHERE organization_id = $1 AND id = $2
                  AND stato = $3 AND updated_at = $4
                  AND data = $5 AND ora = $6 AND coperti = $7
                RETURNING *
            """, organization_id, booking_id, expected["stato"], expected["updated_at"],
                expected["data"], expected["ora"], expected["coperti"])
            return dict(row) if row else None

    async def update_booking_details(self, organization_id, booking_id,
                                     nome_cliente, telefono, data, ora,
                                     coperti, note, stato, expected=None):
        if isinstance(data, str):
            data = date.fromisoformat(data)
        if isinstance(ora, str):
            ore, minuti = ora.split(":")
            ora = time(int(ore), int(minuti))
        async with self._connection() as conn:
            row = await conn.fetchrow("""
                UPDATE bookings SET nome_cliente = $3, telefono = $4,
                    data = $5, ora = $6, coperti = $7, note = $8,
                    stato = $9, updated_at = NOW()
                WHERE organization_id = $1 AND id = $2
                  AND ($10::timestamptz IS NULL OR updated_at = $10)
                  AND ($11::text IS NULL OR stato = $11)
                  AND ($12::date IS NULL OR data = $12)
                  AND ($13::time IS NULL OR ora = $13)
                  AND ($14::integer IS NULL OR coperti = $14)
                RETURNING *
            """, organization_id, booking_id, nome_cliente, telefono,
                data, ora, coperti, note, stato,
                expected.get("updated_at") if expected else None,
                expected.get("stato") if expected else None,
                expected.get("data") if expected else None,
                expected.get("ora") if expected else None,
                expected.get("coperti") if expected else None)
            return dict(row) if row else None

    async def update_booking_payment(self, organization_id, booking_id,
                                      payment_status, session_id=None):
        async with self.pool.acquire() as conn:
            if session_id:
                row = await conn.fetchrow("""
                    UPDATE bookings SET payment_status = $3, payment_link = $4,
                        payment_link_created_at = NOW(), updated_at = NOW()
                    WHERE organization_id = $1 AND id = $2
                    RETURNING *
                """, organization_id, booking_id, payment_status, session_id)
            else:
                row = await conn.fetchrow("""
                    UPDATE bookings SET payment_status = $3, updated_at = NOW()
                    WHERE organization_id = $1 AND id = $2
                    RETURNING *
                """, organization_id, booking_id, payment_status)
            return dict(row) if row else None

    async def list_bookings_by_stato(self, organization_id, stato):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM bookings WHERE organization_id = $1 AND stato = $2 ORDER BY data, ora",
                organization_id, stato,
            )
            return [dict(r) for r in rows]

    async def list_bookings_for_reminder(self, organization_id, target_date):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM bookings
                WHERE organization_id = $1 AND data = $2
                  AND stato = 'confermata' AND reminder_status = 'none'
                ORDER BY ora
            """, organization_id, target_date)
            return [dict(r) for r in rows]

    async def update_booking_reminder_status(self, organization_id, booking_id,
                                              reminder_status, responded_at=None):
        async with self.pool.acquire() as conn:
            if responded_at:
                row = await conn.fetchrow("""
                    UPDATE bookings SET reminder_status = $3,
                        reminder_responded_at = $4, updated_at = NOW()
                    WHERE organization_id = $1 AND id = $2
                    RETURNING *
                """, organization_id, booking_id, reminder_status, responded_at)
            else:
                row = await conn.fetchrow("""
                    UPDATE bookings SET reminder_status = $3, updated_at = NOW()
                    WHERE organization_id = $1 AND id = $2
                    RETURNING *
                """, organization_id, booking_id, reminder_status)
            return dict(row) if row else None

    async def list_pending_reminders_for_phone(self, organization_id, phone):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM bookings
                WHERE organization_id = $1 AND telefono = $2
                  AND stato = 'confermata' AND reminder_status = 'sent'
                ORDER BY data, ora
            """, organization_id, phone)
            return [dict(row) for row in rows]

    async def list_reminder_replies_for_phone(self, organization_id, phone,
                                              reminder_status):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM bookings
                WHERE organization_id = $1 AND telefono = $2
                  AND reminder_status = $3
                ORDER BY reminder_responded_at DESC NULLS LAST, data, ora
            """, organization_id, phone, reminder_status)
            return [dict(row) for row in rows]

    async def apply_reminder_reply(self, organization_id, booking_id,
                                   reminder_status, responded_at):
        """Atomically apply a reply only to an outstanding reminder."""
        async with self.pool.acquire() as conn:
            if reminder_status == "rejected":
                row = await conn.fetchrow("""
                    UPDATE bookings SET reminder_status = 'rejected',
                        reminder_responded_at = $3, stato = 'cancellata',
                        updated_at = NOW()
                    WHERE organization_id = $1 AND id = $2
                      AND stato = 'confermata' AND reminder_status = 'sent'
                    RETURNING *
                """, organization_id, booking_id, responded_at)
            else:
                row = await conn.fetchrow("""
                    UPDATE bookings SET reminder_status = $3,
                        reminder_responded_at = $4, updated_at = NOW()
                    WHERE organization_id = $1 AND id = $2
                      AND stato = 'confermata' AND reminder_status = 'sent'
                    RETURNING *
                """, organization_id, booking_id, reminder_status, responded_at)
            return dict(row) if row else None

    async def mark_reminder_sent(self, organization_id, booking_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE bookings SET reminder_status = 'sent',
                    reminder_sent_at = NOW(), updated_at = NOW()
                WHERE organization_id = $1 AND id = $2
                RETURNING *
            """, organization_id, booking_id)
            return dict(row) if row else None

    async def list_reminders_timed_out(self, organization_id, cutoff, min_date):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM bookings
                WHERE organization_id = $1
                  AND reminder_status = 'sent'
                  AND reminder_sent_at <= $2
                  AND data >= $3::date
            """, organization_id, cutoff, min_date)
            return [dict(r) for r in rows]

    async def list_bookings_da_verificare(self, organization_id, target_date):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM bookings
                WHERE organization_id = $1 AND data = $2
                  AND stato = 'confermata'
                  AND completata_at IS NULL AND no_show_at IS NULL
                ORDER BY ora
            """, organization_id, target_date)
            return [dict(r) for r in rows]

    async def upsert_booking_settings_config(self, organization_id, config):
        if not isinstance(config, dict) or "catalog_v2" in config:
            raise ValueError("Use the protected catalogue endpoint")
        async with self.booking_settings_lock(organization_id), self._connection() as conn:
            row = await conn.fetchrow("""
                INSERT INTO booking_settings (id, organization_id, config)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (organization_id) DO UPDATE
                    SET config = $3::jsonb || CASE
                        WHEN booking_settings.config ? 'catalog_v2'
                        THEN jsonb_build_object('catalog_v2',booking_settings.config->'catalog_v2')
                        ELSE '{}'::jsonb END, updated_at = NOW()
                RETURNING *
            """, uuid.uuid4(), organization_id, json.dumps(config))
            result = dict(row)
            if isinstance(result.get("config"), str):
                result["config"] = json.loads(result["config"])
            return result

    async def get_booking_settings(self, organization_id):
        async with self._connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM booking_settings WHERE organization_id = $1",
                organization_id,
            )
            if row is None:
                return None
            result = dict(row)
            if isinstance(result.get("fasce_orarie"), str):
                result["fasce_orarie"] = json.loads(result["fasce_orarie"])
            if isinstance(result.get("capienze_orarie"), str):
                result["capienze_orarie"] = json.loads(result["capienze_orarie"])
            if isinstance(result.get("config"), str):
                result["config"] = json.loads(result["config"])
            return result

    async def upsert_booking_settings(self, organization_id, fasce_orarie,
                                       capienze_orarie, slot_minutes=60):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetchval("SELECT pg_advisory_xact_lock(hashtext($1))", f"booking-capacity|{organization_id}")
                row = await conn.fetchrow("""
                INSERT INTO booking_settings (id, organization_id, slot_minutes,
                                              fasce_orarie, capienze_orarie)
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
                ON CONFLICT (organization_id) DO UPDATE
                    SET slot_minutes = $3,
                        fasce_orarie = $4::jsonb,
                        capienze_orarie = $5::jsonb,
                        updated_at = NOW()
                RETURNING *
                """, uuid.uuid4(), organization_id, slot_minutes,
                json.dumps(fasce_orarie), json.dumps(capienze_orarie))
            result = dict(row)
            if isinstance(result.get("fasce_orarie"), str):
                result["fasce_orarie"] = json.loads(result["fasce_orarie"])
            if isinstance(result.get("capienze_orarie"), str):
                result["capienze_orarie"] = json.loads(result["capienze_orarie"])
            return result

    async def check_booking_exists(self, msg_id: str, org_id: str) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM bookings WHERE organization_id = $1::uuid AND source_message_id = $2",
                org_id, str(msg_id)
            )
            return bool(row)

    async def get_booking_for_message(self, organization_id, source_message_id):
        async with self._connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM bookings WHERE organization_id = $1 AND source_message_id = $2",
                organization_id, str(source_message_id),
            )
            return dict(row) if row else None

    async def mark_booking_requires_intervention(self, organization_id, booking_id, expected=None):
        async with self._connection() as conn:
            await conn.execute("""
                UPDATE bookings SET richiede_intervento = TRUE, updated_at = NOW()
                WHERE organization_id = $1 AND id = $2
                  AND ($3::timestamptz IS NULL OR updated_at = $3)
            """, organization_id, booking_id,
                expected.get("updated_at") if expected else None)
