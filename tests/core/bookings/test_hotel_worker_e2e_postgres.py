"""Private hotel worker with source provenance, civil nights and contention."""
import asyncio
import calendar
import copy
import json
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.core.bookings.catalog_contracts import validate_catalog_json
from src.core.bookings.worker_authority import WorkerBookingAuthority, WorkerProofDenied
from src.core.db.repositories.booking_catalog_repo import BookingCatalogRepository
from src.core.db.repositories.booking_conversation_repo import BookingConversationRepository
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.bookings.test_allocation_engine_postgres import CATALOGS
from tests.core.bookings.test_conversation_state_postgres import commit_source
from tests.core.bookings.test_worker_authority_postgres import _persist_ingress
from tests.core.test_quota_reservation_lifecycle import _auth_member, _inbound_message, _organization


async def setup_hotel(pool, monkeypatch):
    org = (await _organization(pool))['id']
    await pool.execute("UPDATE organizations SET timezone='Europe/Rome',business_profile=$2::jsonb WHERE id=$1", org, json.dumps({'verticale': 'hotel_bnb'}))
    auth = await _auth_member(pool, org, role='owner')
    actor = await pool.fetchval('SELECT id FROM user_profiles WHERE auth_user_id=$1', auth)
    publisher = BookingCatalogRepository(BookingRepository(pool))
    raw = copy.deepcopy(CATALOGS['hotel_bnb'])
    raw['resources'][0]['max_occupants'] = 2
    raw['services'][0]['fields'].append({'key': 'room_count', 'value_type': 'positive_integer', 'required': True})
    raw['services'][0]['fields'].append({'key': 'guest_count', 'value_type': 'positive_integer', 'required': True})
    for day in raw['availability']['days']:
        day['windows'] = [{'start_minute': 0, 'end_minute': 1440}]
    await publisher.write(org, actor, auth, action='draft_saved', expected_revision=0, request_id=uuid4(), catalog=raw)
    published = await publisher.write(org, actor, auth, action='published', expected_revision=1, request_id=uuid4())
    scoped = validate_catalog_json(json.dumps(published['published']), organization_id=org)
    await pool.execute("SELECT public.booking_sector_activate_empty($1,'hotel_bnb')", org)
    policy = {'a05': {'authority': 'internal'}, 'c24_sector_auto':
              {'enabled': True, 'version': 1, 'sector': 'hotel_bnb', 'authority': 'internal'}}
    await pool.execute('UPDATE booking_settings SET config=config || $2::jsonb WHERE organization_id=$1', org, json.dumps(policy))
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    return {'pool': pool, 'org': org, 'auth': auth, 'actor': actor, 'catalog': scoped,
            'repo': BookingConversationRepository(pool)}


def next_autumn_dst():
    today = datetime.now(timezone.utc).date()
    year = today.year if today.month < 10 else today.year + 1
    last = date(year, 10, calendar.monthrange(year, 10)[1])
    sunday = last.fromordinal(last.toordinal() - (last.weekday() + 1) % 7)
    return sunday.fromordinal(sunday.toordinal() - 1), sunday.fromordinal(sunday.toordinal() + 1)


async def confirmed_request(env, arrival, departure):
    pool, org = env['pool'], env['org']
    message = await _inbound_message(pool, org, canale='whatsapp')
    conversation = await pool.fetchval('SELECT conversation_id FROM messages WHERE id=$1', message)
    current = {**env, 'message': message, 'conv': conversation}
    wam = 'hotel-proposal-' + uuid4().hex
    await pool.execute('UPDATE messages SET wam_id=$3 WHERE organization_id=$1 AND id=$2', org, message, wam)
    await _persist_ingress(pool, org, wam)
    await commit_source(current, message, f'Mi chiamo Mario Rossi. {arrival} {departure} room_count: 1 guest_count: 2',
        {'service_id':'standard', 'customer_name':True, 'arrival_date':arrival.isoformat(),
         'departure_date':departure.isoformat(), 'room_count':1, 'guest_count':2}, 1700000000, provider_id=wam)
    await env['repo'].apply_worker_source(org, conversation, message)
    prompt = await pool.fetchrow("SELECT id,content_text FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND handling_type='c24_confirmation_prompt'", org, conversation)
    assert prompt and arrival.isoformat() in prompt['content_text'] and departure.isoformat() in prompt['content_text']
    await pool.execute("UPDATE messages SET status='sent',wam_id=$3,sent_at=clock_timestamp()-interval '2 minutes' WHERE organization_id=$1 AND id=$2", org, prompt['id'], 'hotel-prompt-' + uuid4().hex)
    reply, reply_wam = uuid4(), 'hotel-confirmation-' + uuid4().hex
    stamp = await pool.fetchval("SELECT floor(extract(epoch from clock_timestamp()-interval '1 minute'))::bigint")
    text = f"CONFIRM {prompt['id']}"
    payload = {'id':reply_wam, 'type':'text', 'timestamp':str(stamp), 'text':{'body':text}}
    await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id)
        VALUES($1,$2,$3,'inbound','text',$4::jsonb,'processing',$5,$6)""", reply, org, conversation, json.dumps(payload), text, reply_wam)
    await _persist_ingress(pool, org, reply_wam)
    assert (await env['repo'].apply_worker_source(org, conversation, reply))['details_confirmed_current']
    return message, conversation, reply


@pytest.mark.asyncio
async def test_hotel_worker_dst_contention_replay_and_tenant_scope(pg_pool, private_worker_pool, monkeypatch):
    env = await setup_hotel(pg_pool, monkeypatch)
    arrival, departure = next_autumn_dst()
    one = await confirmed_request(env, arrival, departure)
    two = await confirmed_request(env, arrival, departure)
    authority = WorkerBookingAuthority()
    with pytest.raises(WorkerProofDenied):
        await authority.process_confirmation(private_worker_pool, uuid4(), one[1], one[2])
    outcomes = await asyncio.gather(*(WorkerBookingAuthority().process_confirmation(private_worker_pool, env['org'], item[1], item[2]) for item in (one, two)), return_exceptions=True)
    winners = [value for value in outcomes if not isinstance(value, BaseException)]
    assert len(winners) == 1, [type(value).__name__ for value in outcomes]
    booking = winners[0]
    row = await pg_pool.fetchrow('SELECT * FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env['org'], booking)
    assert (row['arrival_date'], row['departure_date']) == (arrival, departure)
    allocation = await pg_pool.fetchrow('SELECT starts_at,ends_at FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env['org'], booking)
    assert (allocation['ends_at'] - allocation['starts_at']).total_seconds() == 49 * 3600
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', env['org']) == 1
    assert await pg_pool.fetchval('SELECT coperti FROM bookings WHERE organization_id=$1 AND id=$2', env['org'], booking) == 2
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env['org'], booking) == 1
    assert await authority.process_next_batch(private_worker_pool) == 0
    winning = one if outcomes[0] == booking else two
    await pg_pool.execute("UPDATE booking_worker_claims SET lease_until=clock_timestamp()-interval '1 second' WHERE organization_id=$1 AND message_id=$2", env['org'], winning[0])
    assert await authority.process_confirmation(private_worker_pool, env['org'], winning[1], winning[2]) == booking
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env['org'], booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env['org'], booking) == 1


@pytest.mark.asyncio
async def test_hotel_private_worker_batch_executes_persisted_confirmation(pg_pool, private_worker_pool, monkeypatch):
    env = await setup_hotel(pg_pool, monkeypatch)
    arrival, departure = next_autumn_dst()
    await confirmed_request(env, arrival, departure)
    worker = WorkerBookingAuthority()
    assert await worker.process_next_batch(private_worker_pool, limit=10) == 1
    booking = await pg_pool.fetchrow('SELECT id,stato FROM bookings WHERE organization_id=$1', env['org'])
    assert booking and booking['stato'] == 'confermata'
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env['org'], booking['id']) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env['org'], booking['id']) == 1
    assert await worker.process_next_batch(private_worker_pool, limit=10) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env['org'], booking['id']) == 1
    notice = await pg_pool.fetchval('''SELECT m.content_text FROM booking_confirmation_outbox o JOIN messages m ON m.id=o.outbound_message_id
        WHERE o.organization_id=$1 AND o.booking_id=$2''', env['org'], booking['id'])
    assert arrival.isoformat() in notice and departure.isoformat() in notice


@pytest.mark.asyncio
async def test_hotel_worker_rejects_revoked_conversation(pg_pool, private_worker_pool, monkeypatch):
    env = await setup_hotel(pg_pool, monkeypatch)
    arrival, departure = next_autumn_dst()
    source, conversation, reply = await confirmed_request(env, arrival, departure)
    await pg_pool.execute("UPDATE conversations SET ticket_status='PENDING_STAFF' WHERE organization_id=$1 AND id=$2", env['org'], conversation)
    with pytest.raises(WorkerProofDenied):
        await WorkerBookingAuthority().process_confirmation(private_worker_pool, env['org'], conversation, reply)
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', env['org']) == 0

@pytest.mark.asyncio
@pytest.mark.parametrize('revocation', ['customer_cancel', 'withdrawn_consent'])
async def test_hotel_worker_rejects_new_cancellation_or_withdrawn_consent(pg_pool, private_worker_pool, monkeypatch, revocation):
    env = await setup_hotel(pg_pool, monkeypatch)
    arrival, departure = next_autumn_dst()
    source, conversation, reply = await confirmed_request(env, arrival, departure)
    if revocation == 'customer_cancel':
        wam = 'hotel-cancel-' + uuid4().hex
        stamp = await pg_pool.fetchval('SELECT floor(extract(epoch from clock_timestamp()))::bigint')
        payload = {'id':wam, 'type':'text', 'timestamp':str(stamp), 'text':{'body':'Annulla'}}
        await pg_pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id)
            VALUES($1,$2,$3,'inbound','text',$4::jsonb,'processing','Annulla',$5)""",
            uuid4(), env['org'], conversation, json.dumps(payload), wam)
    else:
        await pg_pool.execute("""UPDATE contacts SET consent_status='withdrawn' WHERE organization_id=$1
            AND id=(SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2)""", env['org'], conversation)
    with pytest.raises(WorkerProofDenied):
        await WorkerBookingAuthority().process_confirmation(private_worker_pool, env['org'], conversation, reply)
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', env['org']) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1', env['org']) == 0


@pytest.mark.asyncio
async def test_hotel_expired_worker_claim_cannot_mint(pg_pool, private_worker_pool, monkeypatch):
    env = await setup_hotel(pg_pool, monkeypatch)
    arrival, departure = next_autumn_dst()
    source, conversation, reply = await confirmed_request(env, arrival, departure)
    authority = WorkerBookingAuthority()
    async with private_worker_pool.acquire() as conn, conn.transaction():
        claim = await authority.claim(conn, env['org'], source)
        await conn.execute("""UPDATE booking_worker_claims SET lease_until=clock_timestamp()-interval '1 second'
            WHERE organization_id=$1 AND message_id=$2""", env['org'], source)
        with pytest.raises(WorkerProofDenied, match='claim expired'):
            await authority.mint(conn, claim, conversation, expected_state_revision=2, expected_catalog_revision=2)
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', env['org']) == 0

@pytest.mark.asyncio
async def test_hotel_missing_guest_count_does_not_prompt_or_book(pg_pool, monkeypatch):
    env = await setup_hotel(pg_pool, monkeypatch)
    arrival, departure = next_autumn_dst()
    message = await _inbound_message(pg_pool, env['org'], canale='whatsapp')
    conversation = await pg_pool.fetchval('SELECT conversation_id FROM messages WHERE id=$1', message)
    current = {**env, 'message': message, 'conv': conversation}
    wam = 'hotel-missing-guests-' + uuid4().hex
    await pg_pool.execute('UPDATE messages SET wam_id=$3 WHERE organization_id=$1 AND id=$2', env['org'], message, wam)
    await _persist_ingress(pg_pool, env['org'], wam)
    await commit_source(current, message, f'Mi chiamo Mario Rossi. {arrival} {departure} room_count: 1',
        {'service_id':'standard', 'customer_name':True, 'arrival_date':arrival.isoformat(),
         'departure_date':departure.isoformat(), 'room_count':1}, 1700000000, provider_id=wam)
    result = await env['repo'].apply_worker_source(env['org'], conversation, message)
    assert 'guest_count' in result['state'].missing_fields
    assert await pg_pool.fetchval("SELECT count(*) FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND handling_type='c24_confirmation_prompt'", env['org'], conversation) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', env['org']) == 0

@pytest.mark.asyncio
async def test_hotel_over_capacity_does_not_prompt_or_book(pg_pool, monkeypatch):
    env = await setup_hotel(pg_pool, monkeypatch)
    arrival, departure = next_autumn_dst()
    message = await _inbound_message(pg_pool, env['org'], canale='whatsapp')
    conversation = await pg_pool.fetchval('SELECT conversation_id FROM messages WHERE id=$1', message)
    current = {**env, 'message': message, 'conv': conversation}
    wam = 'hotel-overcapacity-' + uuid4().hex
    await pg_pool.execute('UPDATE messages SET wam_id=$3 WHERE organization_id=$1 AND id=$2', env['org'], message, wam)
    await _persist_ingress(pg_pool, env['org'], wam)
    await commit_source(current, message, f'Mi chiamo Mario Rossi. {arrival} {departure} room_count: 1 guest_count: 100',
        {'service_id':'standard', 'customer_name':True, 'arrival_date':arrival.isoformat(),
         'departure_date':departure.isoformat(), 'room_count':1, 'guest_count':100}, 1700000000, provider_id=wam)
    result = await env['repo'].apply_worker_source(env['org'], conversation, message)
    assert not result['state'].details_confirmed
    assert await pg_pool.fetchval("SELECT count(*) FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND handling_type='c24_confirmation_prompt'", env['org'], conversation) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', env['org']) == 0
