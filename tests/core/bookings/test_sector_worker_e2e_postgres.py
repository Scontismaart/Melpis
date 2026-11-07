"""Synthetic opt-in hair/spa confirmation and fenced local allocation."""
import asyncio
import copy
import json
from datetime import date, timedelta
from uuid import uuid4

import pytest

from src.core.bookings.catalog_contracts import validate_catalog_json
from src.core.bookings.worker_authority import WorkerBookingAuthority
from src.core.db.repositories.booking_catalog_repo import BookingCatalogRepository
from src.core.db.repositories.booking_conversation_repo import BookingConversationRepository
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.bookings.test_allocation_engine_postgres import CATALOGS, wait_blocked
from tests.core.bookings.test_conversation_state_postgres import commit_source
from tests.core.bookings.test_worker_authority_postgres import _persist_ingress
from tests.core.test_quota_reservation_lifecycle import _auth_member, _inbound_message, _organization


@pytest.mark.asyncio
@pytest.mark.parametrize('sector', ['parrucchiere', 'centro_estetico'])
async def test_private_worker_sector_confirmation_creates_fenced_booking(pg_pool, monkeypatch, sector):
    pool = pg_pool
    org = (await _organization(pool))['id']
    await pool.execute("UPDATE organizations SET timezone='Europe/Rome',business_profile=$2::jsonb WHERE id=$1",
                       org, json.dumps({'verticale': sector}))
    auth = await _auth_member(pool, org, role='owner')
    actor = await pool.fetchval('SELECT id FROM user_profiles WHERE auth_user_id=$1', auth)
    publisher = BookingCatalogRepository(BookingRepository(pool))
    await publisher.write(org, actor, auth, action='draft_saved', expected_revision=0, request_id=uuid4(),
                          catalog=copy.deepcopy(CATALOGS[sector]))
    published = await publisher.write(org, actor, auth, action='published', expected_revision=1, request_id=uuid4())
    scoped = validate_catalog_json(json.dumps(published['published']), organization_id=org)
    await pool.execute('SELECT public.booking_sector_activate_empty($1,$2)', org, sector)
    policy = {'a05': {'authority': 'internal'}, 'c24_sector_auto':
              {'enabled': True, 'version': 1, 'sector': sector, 'authority': 'internal'}}
    await pool.execute('UPDATE booking_settings SET config=config || $2::jsonb WHERE organization_id=$1', org, json.dumps(policy))
    message = await _inbound_message(pool, org, canale='whatsapp')
    conversation = await pool.fetchval('SELECT conversation_id FROM messages WHERE id=$1', message)
    env = {'pool': pool, 'org': org, 'auth': auth, 'actor': actor, 'catalog': scoped,
           'conv': conversation, 'message': message, 'repo': BookingConversationRepository(pool)}
    wam = 'sector-proposal-' + uuid4().hex
    await pool.execute('UPDATE messages SET wam_id=$3 WHERE organization_id=$1 AND id=$2', org, message, wam)
    await _persist_ingress(pool, org, wam)
    appointment = date.today() + timedelta(days=7)
    while appointment.weekday() == 6:
        appointment += timedelta(days=1)
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    await commit_source(env, message, f'Mi chiamo Mario Rossi. {appointment} 10:00',
        {'service_id': 'standard', 'customer_name': True, 'appointment_date': appointment.isoformat(),
         'appointment_time': '10:00'}, 1700000000, provider_id=wam)
    result = await env['repo'].apply_worker_source(org, conversation, message)
    assert not result['replayed']
    prompt = await pool.fetchval("SELECT id FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND handling_type='c24_confirmation_prompt'", org, conversation)
    assert prompt is not None
    await pool.execute("UPDATE messages SET status='sent',wam_id=$3,sent_at=clock_timestamp()-interval '2 minutes' WHERE organization_id=$1 AND id=$2", org, prompt, 'sector-prompt-' + uuid4().hex)
    reply, reply_wam = uuid4(), 'sector-confirmation-' + uuid4().hex
    stamp = await pool.fetchval("SELECT floor(extract(epoch from clock_timestamp()-interval '1 minute'))::bigint")
    text = f'CONFIRM {prompt}'
    payload = {'id': reply_wam, 'type': 'text', 'timestamp': str(stamp), 'text': {'body': text}}
    await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id)
        VALUES($1,$2,$3,'inbound','text',$4::jsonb,'processing',$5,$6)""",
        reply, org, conversation, json.dumps(payload), text, reply_wam)
    await _persist_ingress(pool, org, reply_wam)
    assert (await env['repo'].apply_worker_source(org, conversation, reply))['details_confirmed_current']
    authority = WorkerBookingAuthority()
    entered, release = asyncio.Event(), asyncio.Event()
    worker_pid = None
    original_marker = WorkerBookingAuthority._mark_sector_admission

    async def hold_fenced_marker(worker_authority, conn, *args):
        nonlocal worker_pid
        await original_marker(worker_authority, conn, *args)
        worker_pid = conn.get_server_pid()
        entered.set()
        await release.wait()

    monkeypatch.setattr(WorkerBookingAuthority, '_mark_sector_admission', hold_fenced_marker)
    legacy = BookingRepository(pool)

    async def legacy_admission():
        async with legacy.slot_lock(org, appointment, '10:00'):
            return await legacy.create_booking(org, 'Synthetic legacy', appointment, '10:00', 1)

    worker = asyncio.create_task(WorkerBookingAuthority().process_next_batch(pool))
    competing = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        competing = asyncio.create_task(legacy_admission())
        await wait_blocked(pool, worker_pid)
    finally:
        release.set()
    processed, legacy_result = await asyncio.wait_for(asyncio.gather(worker, competing, return_exceptions=True), 10)
    assert processed == 1, repr(processed)
    assert isinstance(legacy_result, Exception) and 'guarded admission' in str(legacy_result)
    booking = await pool.fetchval('SELECT id FROM bookings WHERE organization_id=$1', org)
    assert booking is not None
    assert await authority.process_next_batch(pool) == 0
    assert await pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', org, booking) == 'confermata'
    assert await pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', org, booking) == 1
    assert await pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', org, booking) == 1
    with pytest.raises(Exception, match='guarded admission'):
        await pool.execute("INSERT INTO bookings(id,organization_id,nome_cliente,telefono,data,ora,coperti,stato) VALUES($1,$2,'Synthetic','',$3,'10:00',1,'in_attesa')", uuid4(), org, appointment)
