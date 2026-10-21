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
                                 message_type,content,content_text,status)
            VALUES($1,$2,$3,'inbound','text',$4::jsonb,$5,'handled')
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


async def test_internal_auto_confirmation_is_durable_without_google(
    repo, pg_pool, sample_org,
):
    org_id = sample_org["id"]
    booking = BookingService(repo)
    await _configure(pg_pool, booking, org_id)
    day = (date.today() + timedelta(days=8)).isoformat()
    msg = await _message(pg_pool, org_id, day)
    processor = _processor(repo, booking)

    assert await processor._apply_committed_booking(org_id, msg, {}, _canonical(day)) is False
    assert await processor._apply_committed_booking(org_id, msg, {}, _canonical(day)) is False
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM bookings WHERE organization_id=$1", org_id)
        outbox = await conn.fetch("SELECT * FROM booking_confirmation_outbox WHERE organization_id=$1", org_id)
        notices = await conn.fetch("""
            SELECT * FROM messages WHERE organization_id=$1 AND handling_type='booking_confirmation'
        """, org_id)
    assert len(rows) == len(outbox) == len(notices) == 1
    assert rows[0]["stato"] == "confermata"
    assert outbox[0]["status"] == "waiting_for_reply"


async def test_optional_calendar_failure_preserves_internal_confirmation(
    repo, pg_pool, sample_org,
):
    org_id = sample_org["id"]
    booking = BookingService(repo)
    await _configure(pg_pool, booking, org_id, "internal_calendar_sync")
    day = (date.today() + timedelta(days=8)).isoformat()
    msg = await _message(pg_pool, org_id, day)
    assert await _processor(repo, booking)._apply_committed_booking(
        org_id, msg, {}, _canonical(day),
    ) is False
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT b.stato,s.provider,s.sync_status FROM bookings b
            JOIN external_booking_sync s ON s.internal_booking_id=b.id
             AND s.organization_id=b.organization_id WHERE b.organization_id=$1
        """, org_id)
    assert dict(row) == {"stato": "confermata", "provider": "google", "sync_status": "pending"}


async def test_external_authority_cannot_fall_back_to_internal(
    repo, pg_pool, sample_org,
):
    org_id = sample_org["id"]
    booking = BookingService(repo)
    await _configure(pg_pool, booking, org_id, "external_authoritative")
    day = (date.today() + timedelta(days=8)).isoformat()
    msg = await _message(pg_pool, org_id, day)
    assert await _processor(repo, booking)._apply_committed_booking(
        org_id, msg, {}, _canonical(day),
    ) is True
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
                                 message_type,content,content_text,status,created_at)
            VALUES($1,$2,$3,'inbound','text',$4::jsonb,'Annulla la prenotazione',
                   'handled',NOW()+interval '1 second')
        """, uuid.uuid4(), org_id, msg["conversation_id"],
            json.dumps({"from": "+393330001111"}))
    assert await _processor(repo, booking)._apply_committed_booking(
        org_id, msg, {}, _canonical(day),
    ) is True
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM bookings WHERE organization_id=$1", org_id) == 0


async def test_competing_last_seat_is_serialized(repo, pg_pool, sample_org):
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
    assert sorted(results) == [False, True]
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM bookings WHERE organization_id=$1", org_id) == 1
