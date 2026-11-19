"""Synthetic opt-in booking confirmation and guarded local allocation."""
import asyncio
import copy
import json
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.core.bookings import worker_authority
from src.core.bookings.allocation_contracts import AllocationRequest
from src.core.bookings.booking_mutation_contract import (
    BookingMutation,
    apply_booking_mutation,
)
from src.core.bookings.catalog_contracts import validate_catalog_json
from src.core.bookings.worker_authority import WorkerBookingAuthority, WorkerProofDenied
from src.core.db.repositories.booking_allocation_repo import BookingAllocationRepository
from src.core.db.repositories.booking_catalog_repo import BookingCatalogRepository
from src.core.db.repositories.booking_conversation_repo import (
    BookingConversationRepository,
)
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.bookings.test_allocation_engine_postgres import CATALOGS, wait_blocked
from tests.core.bookings.test_conversation_state_postgres import commit_source
from tests.core.bookings.test_fenced_manual_routes_postgres import (
    app_for,
    envelope,
    headers,
)
from tests.core.bookings.test_worker_authority_postgres import _persist_ingress
from tests.core.test_quota_reservation_lifecycle import (
    _auth_member,
    _inbound_message,
    _organization,
)


@pytest.mark.asyncio
@pytest.mark.parametrize('sector', ['parrucchiere', 'centro_estetico', 'studio_medico_dentista'])
async def test_private_worker_sector_confirmation_creates_fenced_booking(pg_pool, private_worker_pool, monkeypatch, sector):
    pool = pg_pool
    org = (await _organization(pool))['id']
    await pool.execute("UPDATE organizations SET timezone='Europe/Rome',business_profile=$2::jsonb WHERE id=$1",
                       org, json.dumps({'verticale': sector}))
    auth = await _auth_member(pool, org, role='owner')
    actor = await pool.fetchval('SELECT id FROM user_profiles WHERE auth_user_id=$1', auth)
    publisher = BookingCatalogRepository(BookingRepository(pool))
    catalog = copy.deepcopy(CATALOGS[sector])
    if sector == 'centro_estetico':
        catalog['resources'] = [
            {'resource_id': 'operator', 'kind': 'operator', 'capacity': 1, 'exclusive': True},
            {'resource_id': 'cabin', 'kind': 'space', 'capacity': 1, 'exclusive': True},
            {'resource_id': 'laser', 'kind': 'equipment', 'capacity': 1, 'exclusive': True},
        ]
        catalog['services'][0]['duration'].update(
            minutes=47, buffer_before=7, buffer_after=13,
        )
        catalog['services'][0]['resources'] = [
            {'resource_id': resource_id, 'quantity': 1}
            for resource_id in ('operator', 'cabin', 'laser')
        ]
    await publisher.write(org, actor, auth, action='draft_saved', expected_revision=0, request_id=uuid4(),
                          catalog=catalog)
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
    await commit_source(env, message, f'Mi chiamo Mario Rossi, vorrei un appuntamento amministrativo il {appointment} alle 10:00',
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

    worker = asyncio.create_task(WorkerBookingAuthority().process_next_batch(
        private_worker_pool, application_pool=pool,
    ))
    competing = None
    try:
        await asyncio.wait_for(entered.wait(), 30)
        competing = asyncio.create_task(legacy_admission())
        await wait_blocked(pool, worker_pid)
    finally:
        release.set()
    processed, legacy_result = await asyncio.wait_for(asyncio.gather(worker, competing, return_exceptions=True), 10)
    assert processed == 1, repr(processed)
    assert isinstance(legacy_result, Exception) and 'guarded admission' in str(legacy_result)
    booking = await pool.fetchval('SELECT id FROM bookings WHERE organization_id=$1', org)
    assert booking is not None
    assert await authority.process_next_batch(private_worker_pool, application_pool=pool) == 0
    assert await pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', org, booking) == 'confermata'
    claims = await pool.fetch('''SELECT resource_id,resource_kind,quantity,starts_at,ends_at
        FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id''', org, booking)
    if sector == 'centro_estetico':
        assert [(row['resource_id'], row['resource_kind'], row['quantity']) for row in claims] == [
            ('cabin', 'space', 1), ('laser', 'equipment', 1), ('operator', 'operator', 1),
        ]
        local_zone = ZoneInfo('Europe/Rome')
        expected_start = datetime.combine(appointment, time(9, 53), tzinfo=local_zone).astimezone(timezone.utc)
        expected_end = datetime.combine(appointment, time(11, 0), tzinfo=local_zone).astimezone(timezone.utc)
        assert {(row['starts_at'], row['ends_at']) for row in claims} == {
            (expected_start, expected_end),
        }
    else:
        assert len(claims) == 1
    assert await pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', org, booking) == 1
    with pytest.raises(Exception, match='guarded admission'):
        await pool.execute("INSERT INTO bookings(id,organization_id,nome_cliente,telefono,data,ora,coperti,stato) VALUES($1,$2,'Synthetic','',$3,'10:00',1,'in_attesa')", uuid4(), org, appointment)


@pytest.mark.asyncio
@pytest.mark.parametrize(('sector', 'scenario'), [
    ('ristorante', None),
    ('parrucchiere', None),
    ('centro_estetico', None),
    ('parrucchiere', 'before_proof'),
    ('parrucchiere', 'after_proof'),
    ('parrucchiere', 'cancel_winner'),
])
async def test_two_confirmed_sector_workers_compete_for_same_resource(
    pg_pool, private_worker_pool, monkeypatch, sector, scenario,
):
    pool = pg_pool
    org = (await _organization(pool))['id']
    await pool.execute("UPDATE organizations SET timezone='Europe/Rome',business_profile=$2::jsonb WHERE id=$1",
                       org, json.dumps({'verticale': sector}))
    auth = await _auth_member(pool, org, role='owner')
    actor = await pool.fetchval('SELECT id FROM user_profiles WHERE auth_user_id=$1', auth)
    publisher = BookingCatalogRepository(BookingRepository(pool))
    await publisher.write(org, actor, auth, action='draft_saved', expected_revision=0,
                          request_id=uuid4(), catalog=copy.deepcopy(CATALOGS[sector]))
    published = await publisher.write(org, actor, auth, action='published', expected_revision=1, request_id=uuid4())
    scoped = validate_catalog_json(json.dumps(published['published']), organization_id=org)
    if sector == 'ristorante':
        policy = {'a05': {
            'enabled': True, 'version': 1, 'authority': 'internal', 'verticale': sector,
            'service': 'ristorante/tavolo', 'duration_minutes': 60, 'closed_dates': [],
            'weekly_opening_intervals': {
                'sunday': [],
                **{weekday: [{'start': '09:00', 'end': '18:00'}]
                   for weekday in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday')},
            },
        }}
        await pool.execute('''UPDATE booking_settings SET config=config || $2::jsonb,
            capienze_orarie='{"10:00":2,"11:00":2}'::jsonb WHERE organization_id=$1''', org, json.dumps(policy))
    else:
        await pool.execute('SELECT public.booking_sector_activate_empty($1,$2)', org, sector)
        policy = {'a05': {'authority': 'internal'}, 'c24_sector_auto':
                  {'enabled': True, 'version': 1, 'sector': sector, 'authority': 'internal'}}
        await pool.execute('UPDATE booking_settings SET config=config || $2::jsonb WHERE organization_id=$1',
                           org, json.dumps(policy))
    appointment = date.today() + timedelta(days=7)
    while appointment.weekday() == 6:
        appointment += timedelta(days=1)
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    replies = []
    for _ in range(2):
        message = await _inbound_message(pool, org, canale='whatsapp')
        conversation = await pool.fetchval('SELECT conversation_id FROM messages WHERE id=$1', message)
        env = {'pool': pool, 'org': org, 'auth': auth, 'actor': actor, 'catalog': scoped,
               'conv': conversation, 'message': message, 'repo': BookingConversationRepository(pool)}
        wam = 'race-proposal-' + uuid4().hex
        await pool.execute('UPDATE messages SET wam_id=$3 WHERE organization_id=$1 AND id=$2', org, message, wam)
        await _persist_ingress(pool, org, wam)
        text = f'Mi chiamo Mario Rossi, vorrei un appuntamento il {appointment} alle 10:00'
        fields = {'service_id': 'standard', 'customer_name': True, 'appointment_date': appointment.isoformat(),
                  'appointment_time': '10:00'}
        if sector == 'ristorante':
            text = f'Mi chiamo Mario Rossi, vorrei un tavolo per 2 persone il {appointment} alle 10:00'
            fields['party_size'] = 2
        await commit_source(env, message, text, fields, 1700000000, provider_id=wam)
        await env['repo'].apply_worker_source(org, conversation, message)
        prompt = await pool.fetchval("SELECT id FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND handling_type='c24_confirmation_prompt'", org, conversation)
        assert prompt is not None
        await pool.execute("UPDATE messages SET status='sent',wam_id=$3,sent_at=clock_timestamp()-interval '2 minutes' WHERE organization_id=$1 AND id=$2", org, prompt, 'race-prompt-' + uuid4().hex)
        reply, reply_wam = uuid4(), 'race-confirmation-' + uuid4().hex
        stamp = await pool.fetchval("SELECT floor(extract(epoch from clock_timestamp()-interval '1 minute'))::bigint")
        text = f'CONFIRM {prompt}'
        payload = {'id': reply_wam, 'type': 'text', 'timestamp': str(stamp), 'text': {'body': text}}
        await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id)
            VALUES($1,$2,$3,'inbound','text',$4::jsonb,'processing',$5,$6)""",
            reply, org, conversation, json.dumps(payload), text, reply_wam)
        await _persist_ingress(pool, org, reply_wam)
        assert (await env['repo'].apply_worker_source(org, conversation, reply))['details_confirmed_current']
        replies.append((conversation, reply))

    entered, release = asyncio.Event(), asyncio.Event()
    second_entered = asyncio.Event()
    original_marker = WorkerBookingAuthority._mark_sector_admission
    original_create = WorkerBookingAuthority.create_booking
    worker_pid = None
    second_pid = None
    second = None

    async def record_second(authority, conn, capability):
        nonlocal second_pid
        assert capability._claim.claim_id not in active_heartbeats
        if asyncio.current_task() is second:
            second_pid = conn.get_server_pid()
            second_entered.set()
        return await original_create(authority, conn, capability)

    async def hold_first(authority, conn, *args):
        nonlocal worker_pid
        await original_marker(authority, conn, *args)
        if worker_pid is None:
            worker_pid = conn.get_server_pid()
            entered.set()
            await release.wait()

    original_handoff = WorkerBookingAuthority._handoff_unavailable
    original_heartbeat = worker_authority._lease_heartbeat
    original_availability = BookingAllocationRepository._availability
    active_heartbeats = set()
    handoff_connection = None
    losing_capability = None

    async def track_heartbeat(pool, claim, stop_event):
        active_heartbeats.add(claim.claim_id)
        try:
            await original_heartbeat(pool, claim, stop_event)
        finally:
            active_heartbeats.remove(claim.claim_id)

    async def expire_capability(conn, capability_id):
        # Shift both timestamps to model passage of time without violating the
        # capability duration constraint or also expiring its claim.
        assert await conn.fetchval('''UPDATE c24_private.worker_capabilities
            SET issued_at=statement_timestamp()-interval '2 seconds',
                expires_at=statement_timestamp()-interval '1 second'
            WHERE capability_id=$1 RETURNING true''', capability_id)

    async def check_handoff(authority, conn, claim, proof, capability_id):
        nonlocal handoff_connection, losing_capability
        assert claim.claim_id not in active_heartbeats
        assert await conn.fetchval('''SELECT lease_until>clock_timestamp()
            FROM booking_worker_claims WHERE organization_id=$1 AND message_id=$2
              AND claim_id=$3''', org, claim.message_id, claim.claim_id)
        handoff_connection = conn
        losing_capability = capability_id
        if scenario == 'before_proof':
            await expire_capability(conn, capability_id)
        if scenario == 'cancel_winner':
            winning_id = await first
            cancellation = AllocationRequest(
                request_id=uuid4(), booking_id=winning_id, action='cancel',
                expected_allocation_revision=1, expected_catalog_revision=scoped.catalog.revision,
            )
            result = await apply_booking_mutation(
                pool, org, auth, cancellation, mutation=BookingMutation('cancel', '', '', ''),
            )
            assert result['outcome'] == 'cancelled'
        return await original_handoff(authority, conn, claim, proof, capability_id)

    async def check_availability(repo, conn, *args, **kwargs):
        result = await original_availability(repo, conn, *args, **kwargs)
        if scenario == 'after_proof' and conn is handoff_connection:
            assert result == 'unavailable'
            await expire_capability(conn, losing_capability)
        return result

    monkeypatch.setattr(worker_authority, '_lease_heartbeat', track_heartbeat)
    monkeypatch.setattr(WorkerBookingAuthority, '_handoff_unavailable', check_handoff)
    monkeypatch.setattr(BookingAllocationRepository, '_availability', check_availability)
    monkeypatch.setattr(WorkerBookingAuthority, '_mark_sector_admission', hold_first)
    monkeypatch.setattr(WorkerBookingAuthority, 'create_booking', record_second)
    first = asyncio.create_task(WorkerBookingAuthority().process_confirmation(
        private_worker_pool, org, *replies[0], application_pool=pool))
    try:
        async def first_holds_lock():
            while not entered.is_set():
                if first.done():
                    raise AssertionError(f'first worker ended before holding its lock: {first.exception()!r}')
                await asyncio.sleep(0.01)
        await asyncio.wait_for(first_holds_lock(), 30)
        second = asyncio.create_task(WorkerBookingAuthority().process_confirmation(
            private_worker_pool, org, *replies[1], application_pool=pool))
        await asyncio.wait_for(second_entered.wait(), 30)
        async def second_blocked_by_first():
            while not await pool.fetchval('SELECT $1=ANY(pg_blocking_pids($2))', worker_pid, second_pid):
                if second.done():
                    raise AssertionError(f'second worker ended before blocking: {second.exception()!r}')
                await asyncio.sleep(0.01)
        await asyncio.wait_for(second_blocked_by_first(), 10)
    except BaseException:
        release.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task is not None),
                             return_exceptions=True)
        raise
    release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 15)
    assert results[0] is not None and not isinstance(results[0], Exception), repr(results)
    winner = results[0]
    loser_conversation, loser_confirmation = replies[1]
    assert await pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', org) == 1
    assert await pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1', org) == 1
    if scenario == 'cancel_winner':
        assert isinstance(results[1], WorkerProofDenied), repr(results)
        assert str(results[1]) == 'Slot unavailability no longer verified'
        assert await pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2',
                                   org, winner) == 'cancellata'
        assert await pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1', org) == 0
        generation = await pool.fetchval('''UPDATE booking_worker_claims
            SET lease_until=clock_timestamp()-interval '1 second'
            WHERE organization_id=$1 AND conversation_id=$2 AND NOT handoff_queued RETURNING generation''',
            org, loser_conversation)
        assert generation is not None
        assert await pool.fetchval('SELECT ticket_status FROM conversations WHERE organization_id=$1 AND id=$2',
                                   org, loser_conversation) == 'AI_ACTIVE'
        retry = WorkerBookingAuthority()
        replacement = await retry.process_confirmation(
            private_worker_pool, org, loser_conversation, loser_confirmation, application_pool=pool,
        )
        assert replacement is not None and replacement != winner
        assert await retry.process_confirmation(
            private_worker_pool, org, loser_conversation, loser_confirmation, application_pool=pool,
        ) == replacement
        assert await pool.fetchval('''SELECT generation FROM booking_worker_claims
            WHERE organization_id=$1 AND conversation_id=$2''', org, loser_conversation) == generation + 1
        assert await pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1', org) == 1
        assert await pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2',
                                   org, replacement) == 1
        return
    assert await pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1', org) == 1
    assert await pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1', org) == 1
    if scenario:
        reason = ('Handoff capability unavailable' if scenario == 'before_proof'
                  else 'Handoff capability expired before transition')
        assert isinstance(results[1], WorkerProofDenied), repr(results)
        assert str(results[1]) == reason
        assert await pool.fetchval('''SELECT lease_until>clock_timestamp() AND NOT handoff_queued
            FROM booking_worker_claims WHERE organization_id=$1 AND conversation_id=$2''',
            org, loser_conversation)
        assert await pool.fetchval('SELECT ticket_status FROM conversations WHERE organization_id=$1 AND id=$2',
                                   org, loser_conversation) == 'AI_ACTIVE'
        return
    assert results[1] is None, repr(results)
    assert await pool.fetchval('SELECT ticket_status FROM conversations WHERE organization_id=$1 AND id=$2',
                               org, loser_conversation) == 'PENDING_STAFF'
    assert await pool.fetchval('SELECT ticket_status FROM conversations WHERE organization_id=$1 AND id=$2',
                               org, replies[0][0]) == 'AI_ACTIVE'
    assert await pool.fetchval('''SELECT handoff_queued FROM booking_worker_claims
        WHERE organization_id=$1 AND conversation_id=$2''', org, loser_conversation)
    revoked = await pool.fetchrow('''SELECT cap.capability_id, cap.payload IS NULL AS scrubbed,
            cap.expires_at<=clock_timestamp() AS expired
        FROM c24_private.worker_capabilities AS cap JOIN booking_worker_claims AS claim
          ON claim.organization_id=cap.organization_id AND claim.message_id=cap.message_id
         AND claim.claim_id=cap.claim_id AND claim.generation=cap.generation
        WHERE claim.organization_id=$1 AND claim.conversation_id=$2''', org, loser_conversation)
    assert revoked['scrubbed'] and revoked['expired']
    async with private_worker_pool.acquire() as private:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await private.fetchval('SELECT c24_private.c24_worker_consume_capability($1)', revoked['capability_id'])

    # Staff may put the conversation back into AI mode. The expired lease and
    # original confirmation must not reopen the already handed-off proposal.
    await pool.execute("""UPDATE conversations SET ticket_status='AI_ACTIVE'
        WHERE organization_id=$1 AND id=$2""", org, loser_conversation)
    await pool.execute("""UPDATE booking_worker_claims SET lease_until=clock_timestamp()-interval '1 second'
        WHERE organization_id=$1 AND conversation_id=$2""", org, loser_conversation)
    retry = WorkerBookingAuthority()
    assert await retry._confirmed_jobs(private_worker_pool, 10) == []
    with pytest.raises(WorkerProofDenied):
        await retry.process_confirmation(private_worker_pool, uuid4(), loser_conversation, loser_confirmation,
                                         application_pool=pool)
    with pytest.raises(WorkerProofDenied):
        await retry.process_confirmation(private_worker_pool, org, loser_conversation, loser_confirmation,
                                         application_pool=pool)
    assert await pool.fetchval('SELECT ticket_status FROM conversations WHERE organization_id=$1 AND id=$2',
                               org, loser_conversation) == 'AI_ACTIVE'
    assert await pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1', org) == 1

    # A later WhatsApp request can update conversational evidence. The
    # existing owner mutation path is required to change the booking itself.
    winner_conversation = replies[0][0]
    timestamp = await pool.fetchval('''SELECT provider_timestamp FROM booking_conversation_states
        WHERE organization_id=$1 AND conversation_id=$2''', org, winner_conversation)
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    staff_app, identity = app_for(SimpleNamespace(pool=pool, org=org, auth=auth, actor=actor))
    async with AsyncClient(transport=ASGITransport(app=staff_app), base_url='http://test') as client:
        status = await client.get('/api/bookings/c24-status')
        assert status.json() == {'enabled': True, 'fenced': sector != 'ristorante'}
        path = f'/api/bookings/{winner}/c24-context'
        identity['ruolo'] = 'manager'
        assert (await client.get(path)).status_code == 403
        identity['ruolo'] = 'owner'
        foreign_org = (await _organization(pool))['id']
        identity['organization_id'] = str(foreign_org)
        assert (await client.get(path)).status_code == 404
        identity['organization_id'] = str(org)
    for offset, (intent, text, fields) in enumerate((
        ('modify', 'Vorrei spostare la prenotazione alle 11:00', {'appointment_time': '11:00'}),
        ('cancel', 'Vorrei cancellare la prenotazione', {}),
    ), 1):
        source, wam = uuid4(), f'sector-{intent}-' + uuid4().hex
        await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,
            content,status,wam_id) VALUES($1,$2,$3,'inbound','text','{}'::jsonb,'processing',$4)""",
            source, org, winner_conversation, wam)
        await _persist_ingress(pool, org, wam)
        await commit_source(env, source, text, fields, timestamp + offset, provider_id=wam, intent=intent)
        applied = await env['repo'].apply_worker_source(org, winner_conversation, source)
        state = applied['state']
        assert state.intent == intent and state.last_source_message_id == source
        assert not applied['details_confirmed_current'] and not applied['confirmation_authorized']
        assert (await env['repo'].apply_worker_source(org, winner_conversation, source))['replayed']
        proposed_time = next(slot for slot in state.slots if slot.field == 'appointment_time')
        assert proposed_time.value == '11:00'
        if intent == 'modify':
            modified_by = source
            assert proposed_time.origin == 'customer_provided'
        assert proposed_time.source_message_id == modified_by
        assert await retry._confirmed_jobs(private_worker_pool, 10) == []
        assert await pool.fetchval("""SELECT count(*) FROM messages WHERE organization_id=$1 AND conversation_id=$2
            AND handling_type='c24_confirmation_prompt'""", org, winner_conversation) == 1
        assert await pool.fetchrow('SELECT stato,ora FROM bookings WHERE organization_id=$1 AND id=$2',
                                   org, winner) == (('confermata', time(10)) if intent == 'modify'
                                                    else ('in_attesa', time(11)))
        assert await pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1', org) == offset
        assert await pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1', org) == offset

        # An authenticated owner independently accepts the customer's request
        # through the normal staff API, with current revisions and replay key.
        async with AsyncClient(transport=ASGITransport(app=staff_app), base_url='http://test') as client:
            context = await client.get(f'/api/bookings/{winner}/c24-context')
            assert context.status_code == 200, context.text
            current = context.json()['allocation']
            command = dict(request_id=uuid4(), booking_id=winner,
                           expected_catalog_revision=current['expected_catalog_revision'],
                           expected_allocation_revision=current['expected_allocation_revision'])
            if intent == 'modify':
                changed = AllocationRequest(**command, action='replace', service_id=current['service_id'],
                    appointment_date=appointment, appointment_time=time(11),
                    party_size=current['party_size'] if sector == 'ristorante' else None)
                mutation = BookingMutation('reschedule', '', '', '', appointment.isoformat(), '11:00',
                                           current['party_size'], notice=True)
                for _ in range(2):
                    response = await client.put(f'/api/bookings/{winner}', json=envelope(changed, mutation),
                                                headers=headers(changed))
                    assert response.status_code == 200, response.text
                assert await pool.fetchrow('SELECT stato,ora FROM bookings WHERE organization_id=$1 AND id=$2',
                                           org, winner) == ('in_attesa', time(11))
            else:
                cancellation = AllocationRequest(**command, action='cancel')
                for _ in range(2):
                    response = await client.post(f'/api/bookings/{winner}/cancel', headers=headers(cancellation))
                    assert response.status_code == 200, response.text
                assert await pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2',
                                           org, winner) == 'cancellata'
        assert await pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', org) == 1
        assert await pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1',
                                   org) == offset + 1
        assert await pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1',
                                   org) == (1 if intent == 'modify' else 0)
        assert await pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1', org) == 2
        assert await pool.fetchval("""SELECT count(*) FROM booking_confirmation_outbox o
            JOIN messages m ON m.organization_id=o.organization_id AND m.id=o.outbound_message_id
            WHERE o.organization_id=$1 AND o.booking_id=$2 AND m.handling_type='booking_reconfirmation'""",
            org, winner) == 1
