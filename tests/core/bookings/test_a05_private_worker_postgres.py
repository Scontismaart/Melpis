"""Connected synthetic A04 -> private A05 writer proof (no provider calls)."""
import copy
import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.billing.quota_reservations import QuotaReservations, validate_final_response
from src.core.bookings.catalog_contracts import validate_catalog_json
from src.core.bookings.conversation_state import parse_structured_response_json
from src.core.db.repositories.booking_catalog_repo import BookingCatalogRepository
from src.core.db.repositories.booking_conversation_repo import BookingConversationRepository
from src.core.db.repositories.booking_repo import BookingRepository
from src.core.bookings.service import BookingService
from src.core.bookings.worker_authority import WorkerBookingAuthority, WorkerProofDenied
from tests.core.bookings.test_worker_authority_postgres import _persist_ingress
from tests.core.test_quota_reservation_lifecycle import (
    _auth_member, _inbound_message, _inbound_operation, _organization,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.migration_limit(94)]
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
    published = await pool.fetchrow('''SELECT catalog FROM booking_catalog_publications
        WHERE organization_id=$1 ORDER BY revision DESC LIMIT 1''', org)
    catalog_payload = (published['catalog'] if isinstance(published['catalog'], str)
                       else json.dumps(published['catalog']))
    catalog = validate_catalog_json(catalog_payload, organization_id=org)
    structured = parse_structured_response_json(json.dumps({
        'risposta':'Richiesta di prenotazione ricevuta, in attesa di verifica.',
        'richiede_umano':False, 'motivo':'',
        'prenotazione': {'nome_cliente':'Mario Rossi','data':day.isoformat(),
                         'ora':'19:00','coperti':2,'note':''},
        'booking_state_proposal': {'intent':'booking','slots':[
            {'field':'service_id','value':'standard'},
            {'field':'customer_name','value':True},
            {'field':'appointment_date','value':day.isoformat()},
            {'field':'appointment_time','value':'19:00'},
            {'field':'party_size','value':2},
        ]},
    }), catalog=catalog, organization_id=org)
    final = validate_final_response(op, reservation['ticket'], structured)
    stored = await quota.commit(op, reservation['ticket'], final)
    return {'id':msg_id,'organization_id':org,'conversation_id':conversation}, stored, op


@pytest.mark.migration_limit(105)
async def test_a04_committed_webhook_a05_private_writer_is_atomic_and_replay_safe(
    pg_pool, private_worker_pool, monkeypatch,
):
    # Worker trigger execution continues through the dedicated writer without
    # granting direct EXECUTE rights to the role (SQL105).
    for function in ('booking_allocation_validate_worker_source', 'booking_conversation_validate_worker_source'):
        assert not await private_worker_pool.fetchval(
            "SELECT has_function_privilege(current_user,$1,'EXECUTE')", f'public.{function}()',
        )
    org, _ = await _setup(pg_pool, capacity=4)
    day = await pg_pool.fetchval("SELECT (now() AT TIME ZONE 'Europe/Rome')::date + 10")
    text = f'Vorrei prenotare un tavolo per 2 persone il {day.isoformat()} alle 19:00, mi chiamo Mario Rossi'
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED','true')
    message, canonical, _ = await _commit_synthetic_a04(pg_pool, org, text, day=day)
    async with pg_pool.acquire() as owner_conn, owner_conn.transaction():
        with pytest.raises(WorkerProofDenied, match='Private booking worker identity'):
            await WorkerBookingAuthority().claim(owner_conn, org, message['id'])
    # Mirror the real inbound flow: persist the A05 proposal into structured
    # state after A04 commit, before the private worker claims it.
    await BookingConversationRepository(pg_pool).apply_worker_source(
        org, message['conversation_id'], message['id'],
    )
    async with pg_pool.acquire() as issuer_conn, issuer_conn.transaction():
        epoch = await issuer_conn.fetchval('SELECT c24_private.c24_quota_trust_epoch()')
        assert isinstance(epoch, UUID) and epoch.int != 0
    processor = __import__('src.core.inbound.service', fromlist=['InboundProcessingService']).InboundProcessingService
    service = processor.__new__(processor)
    service.private_booking_pool = private_worker_pool
    service.private_booking_application_pool = pg_pool
    result = await service._apply_committed_booking(org, message, {}, canonical)
    replay = await service._apply_committed_booking(org, message, {}, canonical)
    assert result.requires_staff is replay.requires_staff is False
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch('SELECT id,stato,source_message_id,ora,calendar_revision FROM bookings WHERE organization_id=$1', org)
        receipt = await conn.fetchrow('''SELECT actor_kind,action,outcome FROM booking_allocation_requests
            WHERE organization_id=$1 AND booking_id=$2''', org, rows[0]['id'])
        allocations = await conn.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', org, rows[0]['id'])
        outbox = await conn.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', org, rows[0]['id'])
        allocation_set = await conn.fetchrow('''SELECT booking_calendar_revision,booking_fingerprint
            FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2''', org, rows[0]['id'])
        outbox_revision = await conn.fetchval('''SELECT booking_revision FROM booking_confirmation_outbox
            WHERE organization_id=$1 AND booking_id=$2''', org, rows[0]['id'])
        issued_payload = await conn.fetchrow('''SELECT payload,payload_hash,completed_at
            FROM c24_private.worker_capabilities WHERE organization_id=$1 AND booking_id=$2
            ORDER BY issued_at DESC LIMIT 1''', org, rows[0]['id'])
        billed = await conn.fetchval("""SELECT metadata->>'state' FROM usage_events
            WHERE organization_id=$1 AND event_type='system.quota_reservation'
            ORDER BY created_at DESC LIMIT 1""", org)
    assert len(rows) == 1 and rows[0]['stato'] == 'confermata'
    assert rows[0]['calendar_revision'] == allocation_set['booking_calendar_revision'] == outbox_revision == 1
    assert issued_payload['payload'] is None
    assert len(issued_payload['payload_hash']) == 64
    assert issued_payload['completed_at'] is not None
    assert rows[0]['source_message_id'] == str(message['id']) and rows[0]['ora'].strftime('%H:%M') == '19:00'
    assert dict(receipt) == {'actor_kind':'inbound_worker','action':'allocate','outcome':'allocated'}
    assert allocations == outbox == 1
    assert billed == 'committed'


async def test_expired_unused_worker_capability_payload_is_scrubbed_but_tombstone_remains(pg_pool):
    expired_id, active_id = uuid4(), uuid4()
    organization_id, booking_id = uuid4(), uuid4()
    expired_payload = {'customer': 'synthetic-private-detail'}
    active_payload = {'customer': 'synthetic-active-detail'}
    expired_hash = 'a' * 64
    active_hash = 'b' * 64
    await pg_pool.execute('''
        INSERT INTO c24_private.worker_capabilities
            (capability_id,organization_id,message_id,claim_id,generation,booking_id,
             payload,payload_hash,issued_at,expires_at)
        VALUES
            ($1,$3,$5,$7,1,$4,$9::jsonb,$11,now()-interval '10 minutes',
             now()-interval '5 minutes'),
            ($2,$3,$6,$8,1,$4,$10::jsonb,$12,now(),now()+interval '5 minutes')
    ''', expired_id, active_id, organization_id, booking_id, uuid4(), uuid4(), uuid4(), uuid4(),
        json.dumps(expired_payload), json.dumps(active_payload), expired_hash, active_hash)

    scrubbed = await pg_pool.fetchval(
        'SELECT c24_private.c24_worker_scrub_expired_capabilities($1)', 100,
    )
    expired = await pg_pool.fetchrow('''SELECT payload,payload_hash,completed_at,consumed_at
        FROM c24_private.worker_capabilities WHERE capability_id=$1''', expired_id)
    active = await pg_pool.fetchrow('''SELECT payload,payload_hash
        FROM c24_private.worker_capabilities WHERE capability_id=$1''', active_id)

    assert scrubbed == 1
    assert expired['payload'] is None
    assert expired['payload_hash'] == expired_hash
    assert expired['completed_at'] is None and expired['consumed_at'] is None
    active_payload_value = active['payload']
    if isinstance(active_payload_value, str):
        active_payload_value = json.loads(active_payload_value)
    assert active_payload_value == active_payload
    assert active['payload_hash'] == active_hash


async def test_migration_backfills_payloads_for_preexisting_completed_capabilities(pg_pool):
    capability_id, organization_id = uuid4(), uuid4()
    payload_hash = 'c' * 64
    await pg_pool.execute('''
        INSERT INTO c24_private.worker_capabilities
            (capability_id,organization_id,message_id,claim_id,generation,booking_id,
             payload,payload_hash,issued_at,expires_at,completed_at)
        VALUES ($1,$2,$3,$4,1,$5,$6::jsonb,$7,now()-interval '10 minutes',
                now()-interval '5 minutes',now()-interval '6 minutes')
    ''', capability_id, organization_id, uuid4(), uuid4(), uuid4(),
        json.dumps({'customer': 'synthetic-legacy-detail'}), payload_hash)

    migration = Path(__file__).parents[3] / 'src/core/db/migrations/094_worker_capability_payload_retention.sql'
    await pg_pool.execute(migration.read_text(encoding='utf-8'))
    row = await pg_pool.fetchrow('''SELECT payload,payload_hash,completed_at
        FROM c24_private.worker_capabilities WHERE capability_id=$1''', capability_id)

    assert row['payload'] is None
    assert row['payload_hash'] == payload_hash
    assert row['completed_at'] is not None


async def test_private_worker_after_insert_guard_rolls_back_tampered_final_booking(
    pg_pool, private_worker_pool, monkeypatch,
):
    """The capability guard validates the post-normalization row atomically."""
    org, _ = await _setup(pg_pool, capacity=4)
    day = await pg_pool.fetchval("SELECT (now() AT TIME ZONE 'Europe/Rome')::date + 10")
    text = f'Vorrei prenotare un tavolo per 2 persone il {day.isoformat()} alle 19:00, mi chiamo Mario Rossi'
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    message, canonical, _ = await _commit_synthetic_a04(pg_pool, org, text, day=day)
    await BookingConversationRepository(pg_pool).apply_worker_source(
        org, message['conversation_id'], message['id'],
    )

    # Simulate a late BEFORE trigger mutation after the booking revision trigger.
    # The AFTER capability guard must compare the final row and abort every write.
    await pg_pool.execute("""
        CREATE OR REPLACE FUNCTION public.test_c24_tamper_booking_before_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF session_user='melpis_c24_booking_worker' THEN
            NEW.nome_cliente := NEW.nome_cliente || ' tampered';
          END IF;
          RETURN NEW;
        END;
        $$;
        DROP TRIGGER IF EXISTS zzz_test_c24_tamper_booking ON public.bookings;
        CREATE TRIGGER zzz_test_c24_tamper_booking BEFORE INSERT ON public.bookings
        FOR EACH ROW EXECUTE FUNCTION public.test_c24_tamper_booking_before_guard();
    """)
    try:
        from src.core.bookings.worker_authority import WorkerBookingAuthority
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match='outside the issued booking capability'):
            await WorkerBookingAuthority().process_a05_booking(
                private_worker_pool, org, message['id'], canonical, application_pool=pg_pool,
            )
        async with pg_pool.acquire() as conn:
            for table in ('bookings','booking_allocation_requests','booking_allocation_sets',
                          'booking_resource_allocations','booking_confirmation_outbox'):
                assert await conn.fetchval(
                    f'SELECT count(*) FROM public.{table} WHERE organization_id=$1', org,
                ) == 0
    finally:
        await pg_pool.execute('DROP TRIGGER IF EXISTS zzz_test_c24_tamper_booking ON public.bookings')
        await pg_pool.execute('DROP FUNCTION IF EXISTS public.test_c24_tamper_booking_before_guard()')


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
        await WorkerBookingAuthority().process_a05_booking(
            private_worker_pool, org, message['id'], canonical, application_pool=pg_pool,
        )
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', org) == 0
    assert await pg_pool.fetchval("SELECT count(*) FROM usage_events WHERE organization_id=$1 AND event_type='system.quota_reservation' AND metadata->>'state'='committed'", org) == 1
