"""PostgreSQL contracts for the A04 post-commit booking continuation."""
import asyncio
import json
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.core.bookings.service import BookingService
from src.core.inbound.service import InboundProcessingService

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


def _policy(authority):
    intervals = [{"start": "09:00", "end": "22:00"}]
    return {"a05": {
        "enabled": True, "version": 1, "authority": authority,
        "verticale": "ristorante", "service": "ristorante/tavolo",
        "duration_minutes": 60, "closed_dates": [],
        "weekly_opening_intervals": {
            day: intervals for day in ("monday", "tuesday", "wednesday", "thursday",
                                      "friday", "saturday", "sunday")
        },
    }}


async def _configure(pool, service, org_id, authority="internal", capacity=2):
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE organizations SET business_profile=$2::jsonb,
                timezone='Europe/Rome', subscription_status='trialing',
                trial_start=NOW()-interval '1 hour', trial_end=NOW()+interval '6 days',
                ai_accounting_blocked=FALSE
            WHERE id=$1
        """, org_id, json.dumps({"verticale": "ristorante"}))
        if authority == "internal_calendar_sync":
            # Fixture seeds an explicit owner-selected Calendar mode. The
            # booking settings endpoint cannot itself grant this authority.
            await conn.execute("""INSERT INTO booking_settings(organization_id,config)
                VALUES($1,$2::jsonb) ON CONFLICT(organization_id) DO UPDATE SET config=EXCLUDED.config""",
                org_id, json.dumps({"a05": {"enabled": False, "authority": authority}}))
    await service.aggiorna_impostazioni(
        org_id, capienze_orarie={"19:00": capacity}, config=_policy(authority),
    )


async def _message(pool, org_id, day, *, phone="+393330001111", text=None):
    contact_id, conv_id, msg_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    text = text or f"Vorrei prenotare per 2 persone il {day} alle 19:00, mi chiamo Mario Rossi"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
            contact_id, org_id, phone,
        )
        await conn.execute("""
            INSERT INTO conversations(id,organization_id,contact_id,canale)
            VALUES($1,$2,$3,'whatsapp')
        """, conv_id, org_id, contact_id)
        await conn.execute("""
            INSERT INTO messages(id,organization_id,conversation_id,direction,
                                 message_type,content,content_text,status,handling_type)
            VALUES($1,$2,$3,'inbound','text',$4::jsonb,$5,'handled','ai_handled')
        """, msg_id, org_id, conv_id, json.dumps({"from": phone}), text)
    return {"id": msg_id, "organization_id": org_id, "conversation_id": conv_id}


def _canonical(day):
    return {
        "risposta": "Richiesta ricevuta.", "richiede_umano": False, "motivo": "",
        "prenotazione": {"nome_cliente": "Mario Rossi", "data": day,
                         "ora": "19:00", "coperti": 2, "note": ""},
    }


def _processor(repo, booking):
    return InboundProcessingService(
        SimpleNamespace(use_conversation_orchestrator=False, encryption_key=""),
        repo, booking_service=booking,
    )


async def test_a05_never_falls_back_to_legacy_writers_without_private_identity(
    repo, pg_pool, sample_org,
):
    org_id = sample_org["id"]
    booking = BookingService(repo)
    await _configure(pg_pool, booking, org_id)
    day = (date.today() + timedelta(days=8)).isoformat()
    msg = await _message(pg_pool, org_id, day)
    processor = _processor(repo, booking)

    first = await processor._apply_committed_booking(org_id, msg, {}, _canonical(day))
    replay = await processor._apply_committed_booking(org_id, msg, {}, _canonical(day))
    assert first.requires_staff is replay.requires_staff is True
    assert first.persisted_state == replay.persisted_state is None
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM bookings WHERE organization_id=$1", org_id)
        outbox = await conn.fetch("SELECT * FROM booking_confirmation_outbox WHERE organization_id=$1", org_id)
        notices = await conn.fetch("""
            SELECT * FROM messages WHERE organization_id=$1 AND handling_type='booking_confirmation'
        """, org_id)
    assert rows == outbox == notices == []


async def test_missing_private_worker_does_not_enqueue_unreserved_confirmation(
    repo, pg_pool, sample_org,
):
    org_id = sample_org["id"]
    booking = BookingService(repo)
    await _configure(pg_pool, booking, org_id)
    day = (date.today() + timedelta(days=8)).isoformat()
    msg = await _message(pg_pool, org_id, day)
    result = await _processor(repo, booking)._apply_committed_booking(
        org_id, msg, {}, _canonical(day),
    )
    assert result.requires_staff is True
    assert result.persisted_state is None
    assert await pg_pool.fetchval("SELECT count(*) FROM bookings WHERE organization_id=$1", org_id) == 0
    assert await pg_pool.fetchval("SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1", org_id) == 0


async def test_optional_calendar_does_not_bypass_private_writer(
    repo, pg_pool, sample_org,
):
    org_id = sample_org["id"]
    booking = BookingService(repo)
    await _configure(pg_pool, booking, org_id, "internal_calendar_sync")
    day = (date.today() + timedelta(days=8)).isoformat()
    msg = await _message(pg_pool, org_id, day)
    result = await _processor(repo, booking)._apply_committed_booking(
        org_id, msg, {}, _canonical(day),
    )
    assert result.requires_staff is True
    assert result.persisted_state is None
    assert await pg_pool.fetchval("SELECT count(*) FROM bookings WHERE organization_id=$1", org_id) == 0


async def test_external_authority_cannot_fall_back_to_internal(
    repo, pg_pool, sample_org,
):
    org_id = sample_org["id"]
    booking = BookingService(repo)
    await _configure(pg_pool, booking, org_id, "external_authoritative")
    day = (date.today() + timedelta(days=8)).isoformat()
    msg = await _message(pg_pool, org_id, day)
    result = await _processor(repo, booking)._apply_committed_booking(
        org_id, msg, {}, _canonical(day),
    )
    assert result.requires_staff is True
    assert result.persisted_state is None
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM bookings WHERE organization_id=$1", org_id) == 0


async def test_newer_cancellation_blocks_stale_worker(repo, pg_pool, sample_org):
    org_id = sample_org["id"]
    booking = BookingService(repo)
    await _configure(pg_pool, booking, org_id)
    day = (date.today() + timedelta(days=8)).isoformat()
    msg = await _message(pg_pool, org_id, day)
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO messages(id,organization_id,conversation_id,direction,
                                 message_type,content,content_text,status,handling_type,created_at)
            VALUES($1,$2,$3,'inbound','text',$4::jsonb,'Annulla la prenotazione',
                   'handled','automation',NOW()+interval '1 second')
        """, uuid.uuid4(), org_id, msg["conversation_id"],
            json.dumps({"from": "+393330001111"}))
    result = await _processor(repo, booking)._apply_committed_booking(
        org_id, msg, {}, _canonical(day),
    )
    assert result.requires_staff is True
    assert result.persisted_state is None
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM bookings WHERE organization_id=$1", org_id) == 0


async def test_competing_proposals_without_private_identity_are_both_handed_off(repo, pg_pool, sample_org):
    org_id = sample_org["id"]
    booking = BookingService(repo)
    await _configure(pg_pool, booking, org_id)
    day = (date.today() + timedelta(days=8)).isoformat()
    first = await _message(pg_pool, org_id, day, phone="+393330000001")
    second = await _message(pg_pool, org_id, day, phone="+393330000002")
    processor = _processor(repo, booking)
    results = await asyncio.gather(
        processor._apply_committed_booking(org_id, first, {}, _canonical(day)),
        processor._apply_committed_booking(org_id, second, {}, _canonical(day)),
    )
    assert all(result.requires_staff for result in results)
    assert await pg_pool.fetchval("SELECT count(*) FROM bookings WHERE organization_id=$1", org_id) == 0


async def test_confirmation_outbox_requires_same_tenant(pg_pool):
    from asyncpg import ForeignKeyViolationError

    day = date.today() + timedelta(days=1)
    async with pg_pool.acquire() as conn, conn.transaction():
        org_a, org_b = uuid.uuid4(), uuid.uuid4()
        await conn.execute("INSERT INTO organizations(id,name) VALUES($1,'Synthetic A'),($2,'Synthetic B')", org_a, org_b)

        async def rows(org):
            booking, conversation, contact, inbound, outbound = (uuid.uuid4() for _ in range(5))
            await conn.execute("INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)", contact, org, str(contact))
            await conn.execute("INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)", conversation, org, contact)
            await conn.execute("""
                INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,content_text,status,handling_type)
                VALUES ($1,$3,$4,'inbound','text','{}','Synthetic inbound','handled','ai_handled'),
                       ($2,$3,$4,'outbound','text','{}','Synthetic outbound','handled','ai_handled')
            """, inbound, outbound, org, conversation)
            await conn.execute("""
                INSERT INTO bookings(id,organization_id,contact_id,id_conversazione,nome_cliente,telefono,data,ora,coperti,stato)
                VALUES($1,$2,$3,$4,'Synthetic','',$5,'10:00',1,'in_attesa')
            """, booking, org, contact, str(conversation), day)
            return booking, inbound, outbound

        booking_a, inbound_a, outbound_a = await rows(org_a)
        booking_b, inbound_b, outbound_b = await rows(org_b)
        insert = """
            INSERT INTO booking_confirmation_outbox(organization_id,booking_id,inbound_message_id,outbound_message_id)
            VALUES($1,$2,$3,$4)
        """
        for values, constraint in (
            ((booking_b, inbound_a, outbound_a), "booking_confirmation_outbox_booking_scope"),
            ((booking_a, inbound_b, outbound_a), "booking_confirmation_outbox_inbound_scope"),
            ((booking_a, inbound_a, outbound_b), "booking_confirmation_outbox_outbound_scope"),
        ):
            with pytest.raises(ForeignKeyViolationError) as error:
                async with conn.transaction():
                    await conn.execute(insert, org_a, *values)
            assert error.value.constraint_name == constraint

        await conn.execute(insert, org_a, booking_a, inbound_a, outbound_a)
        with pytest.raises(ForeignKeyViolationError):
            async with conn.transaction():
                await conn.execute("DELETE FROM messages WHERE id=$1", inbound_a)
        await conn.execute("DELETE FROM booking_confirmation_outbox WHERE booking_id=$1", booking_a)
        await conn.execute("DELETE FROM messages WHERE id=$1", inbound_a)
        await conn.execute(insert, org_a, booking_a, None, outbound_a)
