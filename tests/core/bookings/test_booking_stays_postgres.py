"""Synthetic PostgreSQL checks for dark source-proven stays."""
from uuid import uuid4
import pytest
from src.core.bookings.booking_mutation_contract import BookingMutation, NewBookingLineage, apply_booking_mutation
from src.core.db.repositories.booking_allocation_repo import AllocationDenied
from tests.core.bookings.test_allocation_engine_postgres import create_environment

pytestmark = pytest.mark.asyncio

async def hotel_setup(pool):
    def configure(catalog):
        for day in catalog['availability']['days']:
            day['windows'] = [{'start_minute': 0, 'end_minute': 1440}]
    env = await create_environment(pool, 'hotel_bnb', configure=configure)
    contact, conversation, source = uuid4(), uuid4(), uuid4()
    await pool.execute('INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)', contact, env.org, str(uuid4()))
    await pool.execute('INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)', conversation, env.org, contact)
    await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,content_text,status,handling_type)
        VALUES($1,$2,$3,'inbound','text','{}'::jsonb,'Synthetic hotel request','handled','manual')""", source, env.org, conversation)
    return env, NewBookingLineage(contact, conversation, source)

async def test_source_proven_stay_create_and_replay(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    request = env.request(booking)
    command = BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1,
                              arrival_date='2026-10-28', departure_date='2026-10-29')
    first = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command, new_booking=lineage)
    assert first['outcome'] == 'allocated'
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command, new_booking=lineage))['replayed']
    stay = await pg_pool.fetchrow('SELECT * FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    assert stay['original_subject_id'] == lineage.contact_id and stay['revision'] == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1

async def test_stay_rejects_missing_source(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    command = BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1,
                              arrival_date='2026-10-28', departure_date='2026-10-29')
    with pytest.raises(AllocationDenied):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking), mutation=command,
                                     new_booking=NewBookingLineage(lineage.contact_id, lineage.conversation_id))
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 0

async def test_stay_reschedule_replaces_nights_and_replay(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    create = BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1,
                             arrival_date='2026-10-28', departure_date='2026-10-29')
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking), mutation=create, new_booking=lineage)
    request = env.request(booking, action='replace', allocation_revision=1, arrival_date='2026-10-29', departure_date='2026-10-30')
    edit = BookingMutation('reschedule_stay', '', '', '', '2026-10-29', '14:00', 1,
                           arrival_date='2026-10-29', departure_date='2026-10-30', expected_stay_revision=1)
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit))['outcome'] == 'replaced'
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit))['replayed']
    stay = await pg_pool.fetchrow('SELECT * FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    assert str(stay['arrival_date']) == '2026-10-29' and str(stay['departure_date']) == '2026-10-30' and stay['revision'] == 2
    allocation = await pg_pool.fetchrow('SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    assert (allocation['arrival_date'], allocation['departure_date']) == (stay['arrival_date'], stay['departure_date'])

async def test_stay_cannot_be_transferred_to_another_subject(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    command = BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1,
                              arrival_date='2026-10-28', departure_date='2026-10-29')
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking), mutation=command, new_booking=lineage)
    other = uuid4()
    await pg_pool.execute('INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)', other, env.org, str(uuid4()))
    with pytest.raises(Exception, match='lineage.*(reassigned|immutable)'):
        await pg_pool.execute('UPDATE conversations SET contact_id=$3 WHERE organization_id=$1 AND id=$2', env.org, lineage.conversation_id, other)
    assert await pg_pool.fetchval('SELECT original_subject_id FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == lineage.contact_id

async def test_stay_reschedule_rejects_alternate_published_service(pg_pool):
    import copy
    def configure(catalog):
        for day in catalog['availability']['days']:
            day['windows'] = [{'start_minute': 0, 'end_minute': 1440}]
        alternate = copy.deepcopy(catalog['services'][0])
        alternate['service_id'] = 'alternate'
        catalog['services'].append(alternate)
    env = await create_environment(pg_pool, 'hotel_bnb', configure=configure)
    contact, conversation, source, booking = uuid4(), uuid4(), uuid4(), uuid4()
    await pg_pool.execute('INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)', contact, env.org, str(uuid4()))
    await pg_pool.execute('INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)', conversation, env.org, contact)
    await pg_pool.execute("INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,content_text,status,handling_type) VALUES($1,$2,$3,'inbound','text','{}'::jsonb,'Synthetic hotel request','handled','manual')", source, env.org, conversation)
    lineage = NewBookingLineage(contact, conversation, source)
    command = BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1, arrival_date='2026-10-28', departure_date='2026-10-29')
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking), mutation=command, new_booking=lineage)
    edit = BookingMutation('reschedule_stay', '', '', '', '2026-10-29', '14:00', 1, arrival_date='2026-10-29', departure_date='2026-10-30', expected_stay_revision=1)
    request = env.request(booking, action='replace', allocation_revision=1, service_id='alternate', arrival_date='2026-10-29', departure_date='2026-10-30')
    from src.core.db.repositories.booking_allocation_repo import AllocationConflict
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit)
    assert await pg_pool.fetchval('SELECT revision FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1

@pytest.mark.parametrize('operation', ['cancel', 'reject', 'no_show'])
async def test_stay_release_deletes_claims_but_preserves_subject_lineage(pg_pool, operation):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    create = BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1,
                             arrival_date='2026-10-28', departure_date='2026-10-29')
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking), mutation=create, new_booking=lineage)
    release = env.request(booking, action='cancel', allocation_revision=1)
    result = await apply_booking_mutation(pg_pool, env.org, env.auth, release, mutation=BookingMutation(operation, '', '', ''))
    assert result['outcome'] == 'cancelled'
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    assert await pg_pool.fetchval('SELECT original_subject_id FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == lineage.contact_id

async def test_admin_purge_denies_unapproved_policy_and_backend_privileges(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    command = BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1, arrival_date='2026-10-28', departure_date='2026-10-29')
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking), mutation=command, new_booking=lineage)
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='cancel', allocation_revision=1), mutation=BookingMutation('cancel', '', '', ''))
    assert not await pg_pool.fetchval("SELECT has_function_privilege('service_role','public.booking_stay_admin_purge(uuid,uuid,text,uuid,text)','EXECUTE')")
    assert not await pg_pool.fetchval("SELECT has_table_privilege('service_role','public.booking_stay_purge_markers','INSERT')")
    assert not await pg_pool.fetchval("SELECT has_table_privilege('service_role','public.booking_stays','DELETE')")
    with pytest.raises(Exception, match='Approved current policy required'):
        await pg_pool.fetchval("SELECT public.booking_stay_admin_purge($1,$2,'retention',NULL,'missing')", env.org, booking)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 2

async def test_owner_retention_purges_only_one_stay_and_fences_replay(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    command = BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1, arrival_date='2026-10-28', departure_date='2026-10-29')
    request = env.request(booking)
    await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command, new_booking=lineage)
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='cancel', allocation_revision=1), mutation=BookingMutation('cancel', '', '', ''))
    await pg_pool.execute("UPDATE bookings SET updated_at=now()-interval '2 days' WHERE organization_id=$1 AND id=$2", env.org, booking)
    await pg_pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'booking_stay_content','synthetic-v1',true,1,false,'synthetic','synthetic',now()-interval '1 day',now()+interval '1 day')""", env.org)
    await pg_pool.execute("UPDATE messages SET status='handled' WHERE organization_id=$1 AND id=$2", env.org, lineage.source_message_id)
    with pytest.raises(Exception, match='Completed archived stay retention evidence required'):
        await pg_pool.fetchval("SELECT public.booking_stay_admin_purge($1,$2,'retention',NULL,'synthetic-v1')", env.org, booking)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 2
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 1

async def test_completed_stay_records_immutable_transactional_evidence(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    await apply_booking_mutation(
        pg_pool, env.org, env.auth, env.request(booking),
        mutation=BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1,
                                 arrival_date='2026-10-28', departure_date='2026-10-29'),
        new_booking=lineage,
    )
    request = env.request(booking, action='replace', allocation_revision=1)
    command = BookingMutation('complete', '', '', '')
    await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command)
    evidence = await pg_pool.fetchrow('''SELECT e.*,b.completata_at,a.timezone AS allocation_timezone
        FROM booking_stay_completion_evidence e
        JOIN bookings b ON b.organization_id=e.organization_id AND b.id=e.booking_id
        JOIN booking_allocation_sets a ON a.organization_id=e.organization_id AND a.booking_id=e.booking_id
        WHERE e.organization_id=$1 AND e.booking_id=$2''', env.org, booking)
    assert evidence['completed_at'] >= evidence['completata_at']
    assert evidence['timezone'] == evidence['allocation_timezone']
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command))['replayed']
    with pytest.raises(Exception, match='immutable'):
        await pg_pool.execute('UPDATE booking_stay_completion_evidence SET timezone=\'UTC\' WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    with pytest.raises(Exception, match='immutable'):
        await pg_pool.execute('DELETE FROM booking_stay_completion_evidence WHERE organization_id=$1 AND booking_id=$2', env.org, booking)


async def test_legacy_completed_stay_cannot_mint_evidence(pg_pool):
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    await apply_booking_mutation(
        pg_pool, env.org, env.auth, env.request(booking),
        mutation=BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1,
                                 arrival_date='2026-10-28', departure_date='2026-10-29'),
        new_booking=lineage,
    )
    await pg_pool.execute("UPDATE bookings SET stato='completata',completata_at=now()-interval '3 years' WHERE organization_id=$1 AND id=$2", env.org, booking)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stay_completion_evidence WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0

async def test_completion_evidence_erased_by_approved_scoped_barrier(pg_pool):
    from src.core.gdpr.operations import Operations
    env, lineage = await hotel_setup(pg_pool)
    booking = uuid4()
    await apply_booking_mutation(
        pg_pool, env.org, env.auth, env.request(booking),
        mutation=BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1,
                                 arrival_date='2026-10-28', departure_date='2026-10-29'),
        new_booking=lineage,
    )
    await apply_booking_mutation(pg_pool, env.org, env.auth,
        env.request(booking, action='replace', allocation_revision=1),
        mutation=BookingMutation('complete', '', '', ''))
    for category in ('booking_stay_content', 'contact_content', 'cutover'):
        await pg_pool.execute('''INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
            retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
            VALUES($1,$2,'v1',true,NULL,false,'test:stay','qa',now()-interval '1 day',now()+interval '1 day')''',
            env.org, category)
    operation = await Operations(pg_pool).create(env.org, 'synthetic-admin', uuid4(),
        kind='erase', scope='contact', subject_id=lineage.contact_id)
    await pg_pool.execute('''INSERT INTO gdpr_erasure_barriers
        (organization_id,operation_id,scope,subject_id,identifier_digest,key_id)
        VALUES($1,$2,'contact',$3,$4,'test')''', env.org, operation, lineage.contact_id, 'a' * 64)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stay_completion_evidence WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
