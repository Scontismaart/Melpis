"""PostgreSQL integration tests for the disconnected allocation engine.

The catalog is published through its real administrative repository; all bookings
and subjects are synthetic. These tests require the project's disposable PG fixture.
"""
import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from src.core.bookings.allocation_contracts import parse_request_json
from src.core.db.repositories.booking_catalog_repo import BookingCatalogRepository
from src.core.db.repositories.booking_repo import BookingRepository
from src.core.db.repositories.booking_allocation_repo import (
    AllocationConflict, AllocationDenied, BookingAllocationRepository,
)

pytestmark = pytest.mark.asyncio
CATALOGS = json.loads((Path(__file__).parent / 'fixtures/catalogs_v2.json').read_text(encoding='utf-8'))


class Environment:
    def __init__(self, pool, org, auth, actor, catalog):
        self.pool, self.org, self.auth, self.actor = pool, org, auth, actor
        self.catalog = catalog
        self.engine = BookingAllocationRepository(pool)
        self.publisher = BookingCatalogRepository(BookingRepository(pool))
        self.revision = 0

    async def publish(self, body):
        body = copy.deepcopy(body)
        body['revision'] = self.revision + 1
        await self.publisher.write(self.org, self.actor, self.auth, action='draft_saved',
                                   expected_revision=self.revision, request_id=uuid4(), catalog=body)
        result = await self.publisher.write(self.org, self.actor, self.auth, action='published',
                                            expected_revision=self.revision + 1, request_id=uuid4())
        self.revision = result['published_revision']
        self.catalog = body

    async def booking(self):
        contact, conversation, booking = uuid4(), uuid4(), uuid4()
        async with self.pool.acquire() as c:
            await c.execute('INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)', contact, self.org, str(uuid4()))
            await c.execute('INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)', conversation, self.org, contact)
            await c.execute("""INSERT INTO bookings(id,organization_id,contact_id,id_conversazione,nome_cliente,telefono,data,ora,coperti,stato)
                VALUES($1,$2,$3,$4,'Synthetic','','2026-10-28','10:00',1,'in_attesa')""", booking, self.org, contact, str(conversation))
        return booking

    def request(self, booking, *, action='allocate', allocation_revision=0, key=None, **details):
        data = dict(request_id=str(key or uuid4()), booking_id=str(booking), action=action,
                    expected_allocation_revision=allocation_revision, expected_catalog_revision=self.revision)
        if action != 'cancel':
            data.update(service_id='standard', appointment_date='2026-10-28', appointment_time='10:00')
            if self.catalog['sector'] == 'ristorante': data['party_size'] = 1
            if self.catalog['sector'] == 'hotel_bnb':
                data.pop('appointment_date'); data.pop('appointment_time')
                data.update(arrival_date='2026-10-28', departure_date='2026-10-29')
            data.update(details)
        return parse_request_json(json.dumps(data))

    async def apply(self, request):
        return await self.engine.apply(self.org, self.auth, request)

    async def observe(self, request):
        return await self.engine.observe(self.org, self.auth, request)


async def create_environment(pool, sector='ristorante', *, configure=None):
    org, auth = uuid4(), uuid4()
    catalog = copy.deepcopy(CATALOGS[sector])
    if configure: configure(catalog)
    async with pool.acquire() as c:
        await c.execute("INSERT INTO organizations(id,name,timezone,business_profile) VALUES($1,'Synthetic','Europe/Rome',$2::jsonb)", org, json.dumps({'verticale': sector, 'timezone': 'Europe/Rome'}))
        await c.execute('INSERT INTO auth.users(id,email) VALUES($1,$2)', auth, f'{auth}@example.test')
        actor = await c.fetchval('SELECT id FROM user_profiles WHERE auth_user_id=$1', auth)
        await c.execute("INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) VALUES($1,$2,'owner',now())", org, actor)
    env = Environment(pool, org, auth, actor, catalog)
    await env.publish(catalog)
    return env


@pytest.fixture
async def env(pg_pool):
    return await create_environment(pg_pool)


def flags(result):
    assert result['runtime_enabled'] is False
    assert result['confirmation_authorized'] is False


async def claims(env, booking):
    return await env.pool.fetch('SELECT resource_id,quantity,starts_at,ends_at FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id', env.org, booking)


async def test_seat_pool_concurrent_last_capacity_and_replay(env):
    a, b = await env.booking(), await env.booking()
    first, second = env.request(a, party_size=12), env.request(b, party_size=12)
    results = await asyncio.wait_for(asyncio.gather(env.apply(first), env.apply(second)), 10)
    assert sorted(r['outcome'] for r in results) == ['allocated', 'unavailable']
    for result in results: flags(result)
    winner = first if results[0]['outcome'] == 'allocated' else second
    replay = await env.apply(winner)
    assert replay['outcome'] == 'allocated' and replay['replayed'] is True
    assert await env.pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1', env.org) == 2
    assert await env.pool.fetchval('SELECT sum(quantity) FROM booking_resource_allocations WHERE organization_id=$1', env.org) == 12


async def test_same_key_conflict_and_cancel_replay_never_resurrects(env):
    booking = await env.booking()
    request = env.request(booking, party_size=3)
    original = await env.apply(request)
    assert original['outcome'] == 'allocated'
    changed = env.request(booking, key=request.request_id, party_size=4)
    with pytest.raises(AllocationConflict): await env.apply(changed)
    cancel = await env.apply(env.request(booking, action='cancel', allocation_revision=1))
    assert cancel['outcome'] == 'cancelled'
    replay = await env.apply(request)
    assert replay['outcome'] == 'allocated' and replay['replayed'] is True
    assert replay['current_state'] == 'cancelled' and replay['current_allocation_revision'] == 2
    assert not await claims(env, booking)


async def test_pool_peak_not_sum_and_late_overlap_is_detected(pg_pool):
    def configure(c):
        c['services'][0]['duration'].update(minutes=60, buffer_after=0)
        long = copy.deepcopy(c['services'][0]); long['service_id']='long'; long['duration']['minutes']=120
        c['services'].append(long)
    env = await create_environment(pg_pool, configure=configure)
    one, two, candidate, late = [await env.booking() for _ in range(4)]
    assert (await env.apply(env.request(one, party_size=6)))['outcome'] == 'allocated'
    assert (await env.apply(env.request(two, appointment_time='11:00', party_size=6)))['outcome'] == 'allocated'
    assert (await env.apply(env.request(candidate, service_id='long', party_size=6)))['outcome'] == 'allocated'
    assert (await env.apply(env.request(late, appointment_time='09:30', party_size=1)))['outcome'] == 'unavailable'
    assert not await claims(env, late)


@pytest.mark.parametrize('sector', ['parrucchiere', 'centro_estetico', 'studio_medico_dentista'])
async def test_exclusive_conflict_preserves_claims_and_no_confirmation(pg_pool, sector):
    env = await create_environment(pg_pool, sector)
    a, b = await env.booking(), await env.booking()
    results = await asyncio.wait_for(asyncio.gather(env.apply(env.request(a)), env.apply(env.request(b))), 10)
    assert sorted(r['outcome'] for r in results) == ['allocated', 'unavailable']
    for result in results: flags(result)
    assert sum([len(await claims(env, booking)) for booking in (a, b)]) == 1


@pytest.mark.parametrize('arrival,departure,hours', [('2026-03-28','2026-03-30',47), ('2026-10-24','2026-10-26',49)])
async def test_hotel_intermediate_nights_adjacency_and_dst(pg_pool, arrival, departure, hours):
    def configure(c):
        for day in c['availability']['days']: day['windows'] = [{'start_minute':0,'end_minute':1440}]
    env = await create_environment(pg_pool, 'hotel_bnb', configure=configure)
    a, b, c = [await env.booking() for _ in range(3)]
    assert (await env.apply(env.request(a, arrival_date=arrival, departure_date=departure)))['outcome'] == 'allocated'
    row = (await claims(env, a))[0]
    assert (row['ends_at'] - row['starts_at']).total_seconds() == hours * 3600
    from datetime import date, timedelta
    middle = (date.fromisoformat(arrival) + timedelta(days=1)).isoformat()
    assert (await env.apply(env.request(b, arrival_date=middle, departure_date=departure)))['outcome'] == 'unavailable'
    after = (date.fromisoformat(departure) + timedelta(days=1)).isoformat()
    assert (await env.apply(env.request(c, arrival_date=departure, departure_date=after)))['outcome'] == 'allocated'


async def test_cross_tenant_booking_denied_and_actor_revocation(env, pg_pool):
    other = await create_environment(pg_pool)
    foreign = await other.booking()
    with pytest.raises(AllocationDenied): await env.apply(env.request(foreign))
    own = await env.booking()
    await pg_pool.execute('DELETE FROM organization_memberships WHERE organization_id=$1 AND user_id=$2', env.org, env.actor)
    with pytest.raises(AllocationDenied): await env.apply(env.request(own))
    assert not await claims(env, foreign)


async def test_parent_drift_keeps_claims_and_reports_unknown(env):
    a, b = await env.booking(), await env.booking()
    assert (await env.apply(env.request(a, party_size=12)))['outcome'] == 'allocated'
    before = await claims(env, a)
    await env.pool.execute("UPDATE bookings SET ora='11:00' WHERE id=$1 AND organization_id=$2", a, env.org)
    observed = await env.observe(env.request(b, party_size=1))
    assert observed['outcome'] == 'unknown'
    assert await claims(env, a) == before
    assert not await claims(env, b)


async def test_medical_clinical_fields_are_not_request_data(pg_pool):
    env = await create_environment(pg_pool, 'studio_medico_dentista')
    booking = await env.booking()
    with pytest.raises(ValueError): env.request(booking, symptoms='clinical text')
    result = await env.apply(env.request(booking))
    assert result['outcome'] == 'allocated'
    flags(result)
    assert 'consent' not in result and 'clinical_authorized' not in result

async def wait_blocked(pool, blocker):
    async def probe():
        while True:
            if await pool.fetchval('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1=ANY(pg_blocking_pids(pid)))', blocker):
                return
            await asyncio.sleep(0)
    await asyncio.wait_for(probe(), 5)


async def test_resource_waiter_respects_other_tenant_and_commits_once(env, pg_pool):
    other = await create_environment(pg_pool)
    a, b = await env.booking(), await other.booking()
    async with pg_pool.acquire() as holder:
        tx = holder.transaction(); await tx.start()
        try:
            await holder.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,725))", f'allocation-resource|{env.org}|primary')
            waiting = asyncio.create_task(env.apply(env.request(a, party_size=12)))
            await wait_blocked(pg_pool, holder.get_server_pid())
            independent = await asyncio.wait_for(other.apply(other.request(b, party_size=12)), 5)
            assert independent['outcome'] == 'allocated'
            assert not waiting.done()
        finally:
            await tx.rollback()
    assert (await asyncio.wait_for(waiting, 5))['outcome'] == 'allocated'


async def test_replace_conflict_keeps_old_claims_and_cas(env):
    a, b = await env.booking(), await env.booking()
    assert (await env.apply(env.request(a, party_size=6)))['outcome'] == 'allocated'
    assert (await env.apply(env.request(b, party_size=6, appointment_time='12:00')))['outcome'] == 'allocated'
    before = await claims(env, a)
    replace = env.request(a, action='replace', allocation_revision=1, party_size=7, appointment_time='12:00')
    assert (await env.apply(replace))['outcome'] == 'unavailable'
    assert await claims(env, a) == before
    results = await asyncio.gather(
        env.apply(env.request(a, action='cancel', allocation_revision=1)),
        env.apply(env.request(a, action='replace', allocation_revision=1, party_size=1, appointment_time='14:00')),
        return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, AllocationConflict) for r in results) == 1


async def test_receipt_insert_failure_rolls_back_all_claims(env):
    booking = await env.booking()
    async with env.pool.acquire() as c:
        await c.execute("""CREATE FUNCTION test_allocation_receipt_failure() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN RAISE EXCEPTION 'synthetic failure before commit'; END $$;
          CREATE TRIGGER test_allocation_receipt_failure BEFORE INSERT ON booking_allocation_requests
          FOR EACH ROW EXECUTE FUNCTION test_allocation_receipt_failure()""")
    request = env.request(booking)
    try:
        with pytest.raises(Exception, match='synthetic failure before commit'):
            await env.apply(request)
        assert not await claims(env, booking)
        assert await env.pool.fetchval('SELECT count(*) FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
        assert await env.pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 0
    finally:
        async with env.pool.acquire() as c:
            await c.execute('DROP TRIGGER test_allocation_receipt_failure ON booking_allocation_requests; DROP FUNCTION test_allocation_receipt_failure()')
    assert (await env.apply(request))['outcome'] == 'allocated'
    assert (await env.apply(request))['replayed'] is True


async def test_catalog_publisher_wins_before_waiter_rechecks_revision(env):
    booking = await env.booking()
    stale = env.request(booking)
    draft = copy.deepcopy(env.catalog); draft['revision'] = env.revision + 1
    await env.publisher.write(env.org, env.actor, env.auth, action='draft_saved',
                              expected_revision=env.revision, request_id=uuid4(), catalog=draft)
    async with env.pool.acquire() as holder:
        tx = holder.transaction(); await tx.start()
        try:
            await holder.fetchrow('SELECT id FROM booking_settings WHERE organization_id=$1 FOR UPDATE', env.org)
            publisher = asyncio.create_task(env.publisher.write(
                env.org, env.actor, env.auth, action='published',
                expected_revision=env.revision + 1, request_id=uuid4()))
            await wait_blocked(env.pool, holder.get_server_pid())
            waiter = asyncio.create_task(env.apply(stale))
            async def chain():
                while True:
                    found = await env.pool.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity w
                        JOIN pg_stat_activity p ON p.pid=ANY(pg_blocking_pids(w.pid))
                        WHERE $1=ANY(pg_blocking_pids(p.pid)))""", holder.get_server_pid())
                    if found: return
                    await asyncio.sleep(0)
            await asyncio.wait_for(chain(), 5)
        finally:
            await tx.rollback()
    publication = await asyncio.wait_for(publisher, 5)
    assert publication['published_revision'] == stale.expected_catalog_revision + 2
    with pytest.raises(AllocationConflict): await asyncio.wait_for(waiter, 5)
    assert not await claims(env, booking)


@pytest.mark.parametrize('kind', ['membership', 'lifecycle'])
async def test_revocation_committed_before_waiter_resumes_is_denied(env, kind):
    booking = await env.booking()
    async with env.pool.acquire() as holder:
        tx = holder.transaction(); await tx.start()
        try:
            if kind == 'membership':
                await holder.execute('SELECT 1 FROM organization_memberships WHERE organization_id=$1 AND user_id=$2 FOR UPDATE', env.org, env.actor)
            else:
                await holder.execute('SELECT 1 FROM auth_access_lifecycle WHERE auth_user_id=$1 FOR UPDATE', env.auth)
            waiter = asyncio.create_task(env.apply(env.request(booking)))
            await wait_blocked(env.pool, holder.get_server_pid())
            if kind == 'membership':
                await holder.execute('DELETE FROM organization_memberships WHERE organization_id=$1 AND user_id=$2', env.org, env.actor)
            else:
                await holder.execute('UPDATE auth_access_lifecycle SET disabled_at=now() WHERE auth_user_id=$1', env.auth)
            await tx.commit()
        except BaseException:
            await tx.rollback()
            raise
    with pytest.raises(AllocationDenied): await asyncio.wait_for(waiter, 5)
    assert not await claims(env, booking)

async def test_spa_three_resource_conflict_is_atomic_and_lock_order(pg_pool):
    def configure(c):
        c['resources'] = [dict(resource_id=name, kind=kind, capacity=1, exclusive=True)
                          for name, kind in [('operator','operator'), ('cabin','space'), ('laser','equipment'), ('other','operator')]]
        c['services'][0]['resources'] = [dict(resource_id=x,quantity=1) for x in ('operator','cabin','laser')]
        alt = copy.deepcopy(c['services'][0]); alt['service_id']='alternate'
        alt['resources'] = [dict(resource_id=x,quantity=1) for x in ('laser','cabin','other')]
        c['services'].append(alt)
    env = await create_environment(pg_pool, 'centro_estetico', configure=configure)
    a, b = await env.booking(), await env.booking()
    results = await asyncio.wait_for(asyncio.gather(env.apply(env.request(a)), env.apply(env.request(b, service_id='alternate'))), 10)
    assert sorted(r['outcome'] for r in results) == ['allocated','unavailable']
    sizes = [len(await claims(env, booking)) for booking in (a,b)]
    assert sorted(sizes) == [0,3]


async def test_barber_different_operators_and_buffer(pg_pool):
    def configure(c):
        c['resources'].append(dict(resource_id='second', kind='operator', capacity=1, exclusive=True))
        alternative = copy.deepcopy(c['services'][0]); alternative['service_id']='second'
        alternative['resources']=[dict(resource_id='second', quantity=1)]
        c['services'].append(alternative)
    env = await create_environment(pg_pool, 'parrucchiere', configure=configure)
    a, b, c = [await env.booking() for _ in range(3)]
    assert (await env.apply(env.request(a)))['outcome'] == 'allocated'
    assert (await env.apply(env.request(b, service_id='second')))['outcome'] == 'allocated'
    assert (await env.apply(env.request(c, appointment_time='10:45')))['outcome'] == 'unavailable'


async def test_old_catalog_claim_counts_after_capacity_lowered_and_replay(env):
    first, second = await env.booking(), await env.booking()
    original = env.request(first, party_size=6)
    assert (await env.apply(original))['outcome'] == 'allocated'
    updated = copy.deepcopy(env.catalog); updated['resources'][0]['capacity']=6
    await env.publish(updated)
    replay = await env.apply(original)
    assert replay['replayed'] and replay['outcome'] == 'allocated'
    assert replay['current_catalog_revision'] == env.revision
    assert (await env.apply(env.request(second, party_size=1)))['outcome'] == 'unavailable'
    assert not await claims(env, second)


async def test_unknown_external_authority_has_no_claims(env):
    booking = await env.booking()
    await BookingRepository(env.pool).upsert_booking_settings_config(env.org, {'a05': {'authority':'google_authoritative'}})
    result = await env.observe(env.request(booking))
    assert result['outcome'] == 'unknown'
    flags(result)
    assert not await claims(env, booking)


async def test_contact_erasure_barrier_rejects_conversation_lineage(env):
    booking = await env.booking()
    async with env.pool.acquire() as c:
        subject = await c.fetchval('SELECT contact_id FROM bookings WHERE id=$1', booking)
        await c.execute('UPDATE bookings SET contact_id=NULL WHERE id=$1', booking)
        operation = uuid4()
        await c.execute("""INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,kind,scope,status,subject_id)
            VALUES($1,$2,'synthetic',$3,$4,'erase','contact','running',$5)""", operation, env.org, uuid4(), 'a'*64, subject)
        await c.execute("""INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope,subject_id,identifier_digest,key_id)
            VALUES($1,$2,'contact',$3,$4,'test')""", env.org, operation, subject, 'c'*64)
    with pytest.raises(AllocationDenied): await env.apply(env.request(booking))
    assert not await claims(env, booking)

async def test_same_key_concurrent_waiter_replays_once(env):
    booking = await env.booking()
    request = env.request(booking)
    async with env.pool.acquire() as holder:
        tx = holder.transaction(); await tx.start()
        try:
            await holder.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,723))', f'allocation-request|{env.org}|{request.request_id}')
            first = asyncio.create_task(env.apply(request))
            await wait_blocked(env.pool, holder.get_server_pid())
            second = asyncio.create_task(env.apply(request))
        finally:
            await tx.rollback()
    results = await asyncio.wait_for(asyncio.gather(first, second), 10)
    assert [r['outcome'] for r in results] == ['allocated','allocated']
    assert sorted(r['replayed'] for r in results) == [False, True]
    assert await env.pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 1
    assert len(await claims(env, booking)) == 1


async def test_organization_erasure_barrier_inserted_before_waiter_resumes(env):
    booking = await env.booking()
    async with env.pool.acquire() as holder:
        tx = holder.transaction(); await tx.start()
        try:
            await holder.execute('SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))', str(env.org))
            waiter = asyncio.create_task(env.apply(env.request(booking)))
            await wait_blocked(env.pool, holder.get_server_pid())
            operation = uuid4()
            await holder.execute("""INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,kind,scope,status)
                VALUES($1,$2,'synthetic',$3,$4,'erase','organization','running')""", operation, env.org, uuid4(), 'a'*64)
            await holder.execute("INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope) VALUES($1,$2,'organization')", env.org, operation)
            await tx.commit()
        except BaseException:
            await tx.rollback()
            raise
    with pytest.raises(AllocationDenied): await asyncio.wait_for(waiter, 5)
    assert not await claims(env, booking)

async def test_restaurant_table_and_seats_both_required(pg_pool):
    def configure(c):
        c['resources'].append(dict(resource_id='table_one',kind='table',capacity=1,exclusive=True))
        c['services'][0]['resources'].append(dict(resource_id='table_one',quantity=1))
    env = await create_environment(pg_pool, configure=configure)
    a,b = await env.booking(),await env.booking()
    assert (await env.apply(env.request(a,party_size=2)))['outcome'] == 'allocated'
    assert len(await claims(env,a)) == 2
    assert (await env.apply(env.request(b,party_size=2,appointment_time='10:30')))['outcome'] == 'unavailable'
    assert not await claims(env,b)


async def test_resource_repurpose_does_not_erase_old_claim(env):
    a,b = await env.booking(),await env.booking()
    assert (await env.apply(env.request(a,party_size=2)))['outcome'] == 'allocated'
    before = await claims(env,a)
    changed = copy.deepcopy(env.catalog)
    changed['resources'][0].update(kind='table',capacity=1,exclusive=True)
    await env.publish(changed)
    result = await env.observe(env.request(b,party_size=1))
    assert result['outcome'] == 'unknown'
    assert await claims(env,a) == before

async def test_backend_killed_after_claims_before_receipt_rolls_back(env):
    """Terminate the engine's real backend while its transaction contains claims."""
    import asyncpg

    booking = await env.booking()
    request = env.request(booking)
    lock_key = f'test-kill-allocation|{env.org}'
    async with env.pool.acquire() as setup:
        await setup.execute("""CREATE FUNCTION test_allocation_crash_gate() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN PERFORM pg_advisory_xact_lock(hashtextextended('test-kill-allocation|' || NEW.organization_id::text, 991));
            RETURN NEW; END $$;
            CREATE TRIGGER test_allocation_crash_gate BEFORE INSERT ON booking_allocation_requests
            FOR EACH ROW EXECUTE FUNCTION test_allocation_crash_gate()""")
    try:
        async with env.pool.acquire() as holder:
            tx = holder.transaction(); await tx.start()
            try:
                await holder.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,991))', lock_key)
                task = asyncio.create_task(env.apply(request))
                await wait_blocked(env.pool, holder.get_server_pid())
                pid = await env.pool.fetchval("""SELECT pid FROM pg_stat_activity
                    WHERE $1=ANY(pg_blocking_pids(pid)) AND pid<>$1""", holder.get_server_pid())
                assert pid is not None
                assert await env.pool.fetchval('SELECT pg_terminate_backend($1)', pid)
                with pytest.raises((asyncpg.PostgresConnectionError, asyncpg.InterfaceError, ConnectionError)):
                    await asyncio.wait_for(task, 5)
            finally:
                await tx.rollback()
        assert not await claims(env, booking)
        assert await env.pool.fetchval('SELECT count(*) FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
        assert await env.pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 0
    finally:
        async with env.pool.acquire() as cleanup:
            await cleanup.execute('DROP TRIGGER test_allocation_crash_gate ON booking_allocation_requests; DROP FUNCTION test_allocation_crash_gate()')
    assert (await env.apply(request))['outcome'] == 'allocated'
    assert len(await claims(env, booking)) == 1


async def test_authority_change_cannot_release_claims_or_replay_admission(env):
    from src.core.db.repositories.booking_allocation_repo import AllocationUnavailable
    booking = await env.booking()
    original = env.request(booking)
    assert (await env.apply(original))['outcome'] == 'allocated'
    before = await claims(env, booking)
    await BookingRepository(env.pool).upsert_booking_settings_config(
        env.org, {'a05': {'authority': 'google_authoritative'}})
    result = await env.apply(env.request(booking, action='cancel', allocation_revision=1))
    assert result['outcome'] == 'unknown'
    assert result['applied_allocation_revision'] is None
    assert result['current_state'] == 'active'
    assert await claims(env, booking) == before
    with pytest.raises(AllocationUnavailable):
        await env.apply(original)


@pytest.mark.parametrize('state', ['cancellata', 'rifiutata', 'no_show', 'completata'])
async def test_terminal_booking_cannot_acquire_capacity(env, state):
    booking = await env.booking()
    await env.pool.execute('UPDATE bookings SET stato=$2 WHERE id=$1', booking, state)
    result = await env.apply(env.request(booking))
    assert result['outcome'] == 'unknown'
    assert result['applied_allocation_revision'] is None
    assert not await claims(env, booking)


@pytest.mark.parametrize('column', ['completata_at', 'no_show_at'])
async def test_completion_marker_drift_retains_capacity_unknown(env, column):
    booking, candidate = await env.booking(), await env.booking()
    assert (await env.apply(env.request(booking)))['outcome'] == 'allocated'
    before = await claims(env, booking)
    await env.pool.execute(f'UPDATE bookings SET {column}=now() WHERE id=$1', booking)
    assert (await env.apply(env.request(candidate)))['outcome'] == 'unknown'
    assert (await env.apply(env.request(booking, action='replace', allocation_revision=1)))['outcome'] == 'unknown'
    assert await claims(env, booking) == before


async def test_committed_result_lost_then_retry_is_historical_replay(env):
    booking = await env.booking()
    request = env.request(booking, party_size=3)
    # A committed operation whose response is lost must be safe to retry.
    assert (await env.apply(request))['outcome'] == 'allocated'
    receipt = await env.pool.fetchrow('SELECT actor_user_id,payload_hash,outcome FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id)
    before = await claims(env, booking)
    retry = await env.apply(request)
    assert retry['replayed'] is True and retry['outcome'] == 'allocated'
    assert retry['applied_allocation_revision'] == 1
    assert await claims(env, booking) == before
    assert await env.pool.fetchrow('SELECT actor_user_id,payload_hash,outcome FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == receipt
    assert await env.pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 1
