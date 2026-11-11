"""Connected synthetic A04 -> private A05 writer proof (no provider calls)."""
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from src.core.billing.quota_reservations import QuotaReservations, validate_final_response
from src.core.db.repositories.booking_catalog_repo import BookingCatalogRepository
from src.core.db.repositories.booking_repo import BookingRepository
from src.core.bookings.service import BookingService
from src.core.bookings.worker_authority import WorkerBookingAuthority, WorkerProofDenied
from src.models.schemas import RispostaOutput, DatiPrenotazione
from tests.core.bookings.test_worker_authority_postgres import _persist_ingress
from tests.core.test_quota_reservation_lifecycle import (
    _auth_member, _inbound_message, _inbound_operation, _organization,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.migration_limit(91)]
CATALOGS = json.loads((Path(__file__).parent / 'fixtures/catalogs_v2.json').read_text(encoding='utf-8'))


async def _setup(pool, *, capacity=2):
    org_row = await _organization(pool)
    org = org_row['id']
    auth = await _auth_member(pool, org, role='owner')
    actor = await pool.fetchval('SELECT id FROM user_profiles WHERE auth_user_id=$1', auth)
    await pool.execute("""UPDATE organizations SET timezone='Europe/Rome',business_profile=$2::jsonb,
        subscription_status='trialing',trial_start=now()-interval '1 hour',
        trial_end=now()+interval '6 days',ai_accounting_blocked=false WHERE id=$1""",
        org, json.dumps({'verticale':'ristorante'}))
    booking_repo = BookingRepository(pool)
    catalog_repo = BookingCatalogRepository(booking_repo)
    catalog = copy.deepcopy(CATALOGS['ristorante'])
    for weekday in catalog['availability']['days']:
        weekday['windows'] = [{'start_minute':540,'end_minute':1320}]
    await catalog_repo.write(org, actor, auth, action='draft_saved', expected_revision=0,
                             request_id=uuid4(), catalog=catalog)
    await catalog_repo.write(org, actor, auth, action='published', expected_revision=1, request_id=uuid4())
    intervals = [{'start':'09:00','end':'22:00'}]
    a05 = {'enabled':True,'version':1,'authority':'internal','verticale':'ristorante',
        'service':'ristorante/tavolo','duration_minutes':60,'closed_dates':[],
        'weekly_opening_intervals':{day:intervals for day in
            ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')}}
    await BookingService(booking_repo=booking_repo).aggiorna_impostazioni(
        org, capienze_orarie={'19:00':capacity}, config={'a05':a05})
    return org, booking_repo


async def _commit_synthetic_a04(pool, org, text, *, day=None):
    day = day or await pool.fetchval("SELECT (now() AT TIME ZONE 'Europe/Rome')::date + 10")
    msg_id = await _inbound_message(pool, org, canale='whatsapp')
    conversation = await pool.fetchval('SELECT conversation_id FROM messages WHERE organization_id=$1 AND id=$2', org, msg_id)
    phone = await pool.fetchval('SELECT phone_number FROM contacts WHERE organization_id=$1 AND id=(SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2)', org, conversation)
    wam = 'synthetic-a05-' + uuid4().hex
    stamp = await pool.fetchval('SELECT floor(extract(epoch from clock_timestamp()))::bigint')
    body = {'id':wam,'type':'text','timestamp':str(stamp),'from':phone,'text':{'body':text}}
    await pool.execute('''UPDATE messages SET wam_id=$3,content=$4::jsonb,content_text=$5,status='processing'
        WHERE organization_id=$1 AND id=$2''', org, msg_id, wam, json.dumps(body), text)
    await _persist_ingress(pool, org, wam)
    op = _inbound_operation(org, msg_id, text=text)
    quota = QuotaReservations(pool)
    reservation = await quota.reserve(op)
    assert reservation['status'] == 'reserved'
    final = validate_final_response(op, reservation['ticket'], RispostaOutput(
        risposta='Richiesta di prenotazione ricevuta, in attesa di verifica.',
        richiede_umano=False, motivo='', prenotazione=DatiPrenotazione(
            nome_cliente='Mario Rossi', data=day.isoformat(), ora='19:00', coperti=2, note='')))
    stored = await quota.commit(op, reservation['ticket'], final)
    return {'id':msg_id,'organization_id':org,'conversation_id':conversation}, stored, op


async def test_a04_committed_webhook_a05_private_writer_is_atomic_and_replay_safe(
    pg_pool, private_worker_pool, monkeypatch,
):
    org, _ = await _setup(pg_pool, capacity=4)
    day = await pg_pool.fetchval("SELECT (now() AT TIME ZONE 'Europe/Rome')::date + 10")
    text = f'Vorrei prenotare un tavolo per 2 persone il {day.isoformat()} alle 19:00, mi chiamo Mario Rossi'
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED','true')
    message, canonical, _ = await _commit_synthetic_a04(pg_pool, org, text, day=day)
    async with pg_pool.acquire() as owner_conn, owner_conn.transaction():
        with pytest.raises(WorkerProofDenied, match='Private booking worker identity'):
            await WorkerBookingAuthority().claim(owner_conn, org, message['id'])
    processor = __import__('src.core.inbound.service', fromlist=['InboundProcessingService']).InboundProcessingService
    service = processor.__new__(processor)
    service.private_booking_pool = private_worker_pool
    result = await service._apply_committed_booking(org, message, {}, canonical)
    replay = await service._apply_committed_booking(org, message, {}, canonical)
    assert result.requires_staff is replay.requires_staff is False
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch('SELECT id,stato,source_message_id,ora FROM bookings WHERE organization_id=$1', org)
        receipt = await conn.fetchrow('''SELECT actor_kind,action,outcome FROM booking_allocation_requests
            WHERE organization_id=$1 AND booking_id=$2''', org, rows[0]['id'])
        allocations = await conn.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', org, rows[0]['id'])
        outbox = await conn.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', org, rows[0]['id'])
        billed = await conn.fetchval("""SELECT metadata->>'state' FROM usage_events
            WHERE organization_id=$1 AND event_type='system.quota_reservation'
            ORDER BY created_at DESC LIMIT 1""", org)
    assert len(rows) == 1 and rows[0]['stato'] == 'confermata'
    assert rows[0]['source_message_id'] == str(message['id']) and rows[0]['ora'].strftime('%H:%M') == '19:00'
    assert dict(receipt) == {'actor_kind':'inbound_worker','action':'allocate','outcome':'allocated'}
    assert allocations == outbox == 1
    assert billed == 'committed'


async def test_a05_private_writer_fails_closed_without_private_identity_or_policy(pg_pool, private_worker_pool, monkeypatch):
    org, _ = await _setup(pg_pool)
    day = await pg_pool.fetchval("SELECT (now() AT TIME ZONE 'Europe/Rome')::date + 10")
    text = f'Vorrei prenotare un tavolo per 2 persone il {day.isoformat()} alle 19:00, mi chiamo Mario Rossi'
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED','true')
    message, canonical, _ = await _commit_synthetic_a04(pg_pool, org, text, day=day)
    # Revoke the deterministic auto-confirm policy after A04. The committed
    # response remains billed, but no booking/allocation/outbox is fabricated.
    await pg_pool.execute("UPDATE booking_settings SET config=jsonb_set(config,'{a05,enabled}','false') WHERE organization_id=$1", org)
    with pytest.raises(WorkerProofDenied):
        await WorkerBookingAuthority().process_a05_booking(private_worker_pool, org, message['id'], canonical)
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', org) == 0
    assert await pg_pool.fetchval("SELECT count(*) FROM usage_events WHERE organization_id=$1 AND event_type='system.quota_reservation' AND metadata->>'state'='committed'", org) == 1
