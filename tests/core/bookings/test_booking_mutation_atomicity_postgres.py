"""Integration checks for the dormant typed transaction protocol."""
from dataclasses import replace
import asyncio
from datetime import date
from uuid import UUID, uuid4
import pytest

from src.core.bookings.booking_mutation_contract import (
    BookingMutation, BookingMutationRejected, NewBookingLineage, apply_booking_mutation,
)
from src.core.db.repositories.booking_allocation_repo import AllocationConflict, AllocationDenied, AllocationUnavailable
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.bookings.test_allocation_engine_postgres import create_environment

pytestmark = pytest.mark.asyncio


def edit(*, notice=False):
    return BookingMutation('edit_contact', 'Synthetic', '3900000000', 'changed', notice=notice)


async def test_unlinked_manual_booking_cannot_recreate_erased_phone(pg_pool):
    from src.core.gdpr.guard import ErasureBlocked, identifier_fingerprint
    env = await create_environment(pg_pool)
    phone = '3900000004'
    subject, operation, booking = uuid4(), uuid4(), uuid4()
    digest, key_id = identifier_fingerprint(env.org, phone)
    await pg_pool.execute("INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)", subject, env.org, 'erased:synthetic')
    await pg_pool.execute('''INSERT INTO gdpr_operations
        (id,organization_id,requester,request_id,request_hash,kind,scope,status,subject_id)
        VALUES($1,$2,'synthetic',$3,$4,'erase','contact','partial',$5)''', operation, env.org, uuid4(), 'e'*64, subject)
    await pg_pool.execute('''INSERT INTO gdpr_erasure_barriers
        (organization_id,operation_id,scope,subject_id,identifier_digest,key_id)
        VALUES($1,$2,'contact',$3,$4,$5)''', env.org, operation, subject, digest, key_id)
    command = BookingMutation('create', 'Synthetic', phone, '', '2026-10-28', '10:00', 1)
    with pytest.raises(AllocationDenied):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking),
                                     mutation=command, new_booking=NewBookingLineage(None, None))
    # A different valid subject must not be a way to reintroduce the erased
    # phone either. Here the deterministic intake fingerprint is the rejecting
    # boundary, rather than the conservative unlinked-subject barrier above.
    other_contact = uuid4()
    await pg_pool.execute("INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)", other_contact, env.org, '+15550008881')
    with pytest.raises(ErasureBlocked):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking),
                                     mutation=command, new_booking=NewBookingLineage(other_contact, None))
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1', env.org) == 0


async def test_all_writes_roll_back_after_outbox(pg_pool, monkeypatch):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await pg_pool.execute("UPDATE bookings SET stato='confermata' WHERE organization_id=$1 AND id=$2", env.org, booking)
    original = BookingRepository.enqueue_calendar_confirmation
    async def fault(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise RuntimeError('fault after outbox')
    monkeypatch.setattr(BookingRepository, 'enqueue_calendar_confirmation', fault)
    with pytest.raises(RuntimeError, match='fault after outbox'):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking), mutation=edit(notice=True))
    assert await pg_pool.fetchval('SELECT telefono FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == ''
    for table in ('booking_allocation_sets', 'booking_resource_allocations', 'booking_allocation_requests', 'booking_confirmation_outbox'):
        assert await pg_pool.fetchval(f'SELECT count(*) FROM {table} WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    assert await pg_pool.fetchval("SELECT count(*) FROM messages WHERE organization_id=$1 AND handling_type='booking_confirmation'", env.org) == 0


async def test_replay_matches_complete_command_and_legacy_cannot_replay(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    request = env.request(booking)
    first = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit())
    assert first['outcome'] == 'allocated'
    replay = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit())
    assert replay['replayed'] is True
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=replace(edit(), note='different'))
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=replace(edit(), notice=True))
    with pytest.raises(AllocationConflict):
        await env.apply(request)
    assert await pg_pool.fetchval('SELECT note FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'changed'
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1


async def test_unauthorized_no_mutation(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    with pytest.raises(AllocationDenied):
        await apply_booking_mutation(pg_pool, env.org, uuid4(), env.request(booking), mutation=edit())
    assert await pg_pool.fetchval('SELECT telefono FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == ''
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1', env.org) == 0


async def test_new_booking_commit_and_replay(pg_pool):
    env = await create_environment(pg_pool)
    booking = uuid4()
    request = env.request(booking)
    mutation = BookingMutation('create', 'Synthetic', '3900000001', '', '2026-10-28', '10:00', 1)
    lineage = NewBookingLineage(None, None)
    first = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=mutation, new_booking=lineage)
    assert first['outcome'] == 'allocated'
    replay = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=mutation, new_booking=lineage)
    assert replay['replayed'] is True
    for table in ('bookings', 'booking_allocation_sets', 'booking_allocation_requests'):
        field = 'id' if table == 'bookings' else 'booking_id'
        assert await pg_pool.fetchval(f'SELECT count(*) FROM {table} WHERE organization_id=$1 AND {field}=$2', env.org, booking) == 1


@pytest.mark.parametrize('operation', ['invented', 'force_release', 'set_consent', 'clinical_decision'])
async def test_unsupported_status_fails_closed(operation):
    with pytest.raises(BookingMutationRejected):
        BookingMutation(operation, '', '', '')


async def test_foreign_tenant_lineage_and_erasure_barrier_fail_closed(pg_pool):
    env = await create_environment(pg_pool)
    other = await create_environment(pg_pool)
    foreign = await other.booking()
    foreign_contact = await pg_pool.fetchval(
        'SELECT contact_id FROM bookings WHERE organization_id=$1 AND id=$2', other.org, foreign)
    booking = uuid4()
    request = env.request(booking)
    command = BookingMutation('create', 'Synthetic', '3900000002', '', '2026-10-28', '10:00', 1)
    with pytest.raises(AllocationDenied):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command,
                                     new_booking=NewBookingLineage(UUID(str(foreign_contact)), None))
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 0
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2',
        env.org, request.request_id) == 0

    own = await env.booking()
    operation = uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute("""INSERT INTO gdpr_operations
            (id,organization_id,requester,request_id,request_hash,kind,scope,status)
            VALUES($1,$2,'synthetic',$3,$4,'erase','organization','running')""",
            operation, env.org, uuid4(), 'd' * 64)
        await conn.execute("""INSERT INTO gdpr_erasure_barriers
            (organization_id,operation_id,scope) VALUES($1,$2,'organization')""", env.org, operation)
    with pytest.raises(AllocationDenied):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(own), mutation=edit())
    assert await pg_pool.fetchval(
        'SELECT telefono FROM bookings WHERE organization_id=$1 AND id=$2', env.org, own) == ''
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1', env.org) == 0


async def test_revoked_lifecycle_and_schedule_semantic_mismatch_have_no_writes(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await pg_pool.execute('UPDATE auth_access_lifecycle SET disabled_at=now() WHERE auth_user_id=$1', env.auth)
    with pytest.raises(AllocationDenied):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking), mutation=edit())
    assert await pg_pool.fetchval(
        'SELECT telefono FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == ''
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1', env.org) == 0

    # A trusted typed command cannot diverge from the independently supplied
    # allocation schedule. The rejected create must leave no parent or receipt.
    fresh = await create_environment(pg_pool)
    new_id = uuid4()
    mismatched = fresh.request(new_id).model_copy(update={'appointment_date': date(2026, 10, 29)})
    command = BookingMutation('create', 'Synthetic', '3900000003', '', '2026-10-28', '10:00', 1)
    with pytest.raises(BookingMutationRejected, match='Allocation and booking differ'):
        await apply_booking_mutation(pg_pool, fresh.org, fresh.auth, mismatched,
                                     mutation=command, new_booking=NewBookingLineage(None, None))
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', fresh.org, new_id) == 0
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2',
        fresh.org, mismatched.request_id) == 0


async def test_concurrent_identical_command_key_commits_once_and_replays(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    request = env.request(booking)
    results = await asyncio.gather(*(
        apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit())
        for _ in range(2)))
    assert sorted(result['replayed'] for result in results) == [False, True]
    assert all(result['outcome'] == 'allocated' for result in results)
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2',
        env.org, request.request_id) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2',
        env.org, booking) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2',
        env.org, booking) == 1


async def test_concurrent_changed_command_same_key_has_one_winner_and_no_duplicate(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    request = env.request(booking)
    first, second = edit(), replace(edit(), note='other command')
    results = await asyncio.gather(
        apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=first),
        apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=second),
        return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, AllocationConflict) for result in results) == 1
    winner = first if isinstance(results[0], dict) else second
    assert await pg_pool.fetchval(
        'SELECT note FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == winner.note
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2',
        env.org, request.request_id) == 1
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2',
        env.org, booking) == 1

async def test_active_contact_edit_replaces_revision_preserving_claims_and_replays(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    before_booking = await pg_pool.fetchrow('''SELECT data,ora,coperti,stato,calendar_revision
        FROM bookings WHERE organization_id=$1 AND id=$2''', env.org, booking)
    prior_calendar_revision = before_booking['calendar_revision']
    before_claims = await pg_pool.fetch('''SELECT resource_id,resource_kind,exclusive,quantity,
        starts_at,ends_at,allocation_revision,catalog_revision,state
        FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2
        ORDER BY resource_id''', env.org, booking)
    request = env.request(booking, action='replace', allocation_revision=1)
    result = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit())
    assert result['outcome'] == 'replaced' and result['applied_allocation_revision'] == 2
    from src.core.db.repositories.booking_allocation_repo import _booking_fingerprint
    post = await pg_pool.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)
    assert post['calendar_revision'] == prior_calendar_revision + 1
    assert tuple(post[k] for k in ('data', 'ora', 'coperti', 'stato')) == tuple(
        before_booking[k] for k in ('data', 'ora', 'coperti', 'stato'))
    allocation = await pg_pool.fetchrow('SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    assert allocation['booking_fingerprint'] == _booking_fingerprint(post)
    assert allocation['booking_calendar_revision'] == post['calendar_revision']
    assert allocation['catalog_revision'] == request.expected_catalog_revision
    after_claims = await pg_pool.fetch('''SELECT resource_id,resource_kind,exclusive,quantity,
        starts_at,ends_at,allocation_revision,catalog_revision,state
        FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2
        ORDER BY resource_id''', env.org, booking)
    assert len(after_claims) == len(before_claims)
    for before, after in zip(before_claims, after_claims):
        assert (after['resource_id'], after['resource_kind'], after['exclusive'], after['quantity'],
                after['starts_at'], after['ends_at'], after['catalog_revision'], after['state']) == (
            before['resource_id'], before['resource_kind'], before['exclusive'], before['quantity'],
            before['starts_at'], before['ends_at'], before['catalog_revision'], before['state'])
        assert after['allocation_revision'] == 2
        assert after['catalog_revision'] == allocation['catalog_revision']
        assert after['state'] == 'active'
    assert await pg_pool.fetchval('''SELECT count(*) FROM booking_confirmation_outbox
        WHERE organization_id=$1 AND booking_id=$2''', env.org, booking) == 0
    assert await pg_pool.fetchval('''SELECT count(*) FROM messages
        WHERE organization_id=$1 AND handling_type='booking_confirmation' ''', env.org) == 0
    replay = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit())
    assert replay['replayed']
    assert await pg_pool.fetchval('SELECT calendar_revision FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == post['calendar_revision']
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 1


async def test_active_contact_edit_rejects_stale_and_changed_preimages(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    request = env.request(booking, action='replace', allocation_revision=1)
    for changed in (request.model_copy(update={'service_id': 'other'}), request.model_copy(update={'appointment_date': date(2026, 10, 29)}), request.model_copy(update={'party_size': 2})):
        with pytest.raises((BookingMutationRejected, AllocationConflict, AllocationUnavailable)):
            await apply_booking_mutation(pg_pool, env.org, env.auth, changed, mutation=edit())
    await pg_pool.execute("UPDATE bookings SET stato='confermata' WHERE organization_id=$1 AND id=$2", env.org, booking)
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit())
    assert await pg_pool.fetchval('SELECT telefono FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == ''
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1


async def test_active_contact_edit_rejects_stale_booking_calendar_revision(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    await pg_pool.execute('''UPDATE booking_allocation_sets SET booking_calendar_revision=0
        WHERE organization_id=$1 AND booking_id=$2''', env.org, booking)
    request = env.request(booking, action='replace', allocation_revision=1)
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit())
    assert await pg_pool.fetchval('SELECT telefono FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == ''
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 0


async def test_generic_allocation_replace_fails_closed_after_unsealed_contact_edit(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    await pg_pool.execute('''UPDATE bookings SET telefono='3900000099'
        WHERE organization_id=$1 AND id=$2''', env.org, booking)
    request = env.request(booking, action='replace', allocation_revision=1)
    result = await env.apply(request)
    assert result['outcome'] == 'unknown'
    assert result['applied_allocation_revision'] is None
    assert await pg_pool.fetchval('SELECT telefono FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == '3900000099'
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1


async def test_forged_contact_preimage_cannot_bypass_fingerprint_staleness(pg_pool):
    from src.core.db.repositories.booking_allocation_repo import BookingAllocationRepository

    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    request = env.request(booking, action='replace', allocation_revision=1)
    raw_fingerprint = await pg_pool.fetchval('''SELECT booking_fingerprint FROM booking_allocation_sets
        WHERE organization_id=$1 AND booking_id=$2''', env.org, booking)
    async with pg_pool.acquire() as conn, conn.transaction():
        for forged_preimage in (object(), raw_fingerprint):
            engine = BookingAllocationRepository(pg_pool)
            with pytest.raises(AllocationDenied, match='Contact preimage unavailable'):
                await engine._apply_on_connection(
                    conn, env.org, env.auth, request, contact_preimage=forged_preimage)
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 0


async def test_active_contact_edit_concurrent_revision_has_one_winner(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    requests = [env.request(booking, action='replace', allocation_revision=1) for _ in range(2)]
    results = await asyncio.gather(*(apply_booking_mutation(pg_pool, env.org, env.auth, r, mutation=edit()) for r in requests), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, AllocationConflict) for r in results) == 1
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 2


async def test_active_contact_edit_rolls_back_after_replacement(pg_pool, monkeypatch):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await pg_pool.execute("UPDATE bookings SET stato='confermata' WHERE organization_id=$1 AND id=$2", env.org, booking)
    await env.apply(env.request(booking))
    original = BookingRepository.enqueue_calendar_confirmation
    async def fault(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise RuntimeError('fault after outbox')
    monkeypatch.setattr(BookingRepository, 'enqueue_calendar_confirmation', fault)
    request = env.request(booking, action='replace', allocation_revision=1)
    with pytest.raises(RuntimeError, match='fault after outbox'):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=edit(notice=True))
    assert await pg_pool.fetchval('SELECT telefono FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == ''
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 0

async def test_dark_reschedule_replay_and_changed_command(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    request = env.request(booking, action='replace', allocation_revision=1, appointment_time='11:00')
    command = BookingMutation('reschedule', '', '', '', '2026-10-28', '11:00', 1)
    result = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command)
    assert result['outcome'] == 'replaced' and result['applied_allocation_revision'] == 2
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command))['replayed']
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=replace(command, coperti=2))
    assert await pg_pool.fetchval('SELECT ora FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == __import__('datetime').time(11)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0


@pytest.mark.parametrize('operation,status', [('cancel', 'cancellata'), ('reject', 'rifiutata'), ('no_show', 'no_show')])
async def test_dark_status_releases_and_replays(pg_pool, operation, status):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    request = env.request(booking, action='cancel', allocation_revision=1)
    command = BookingMutation(operation, '', '', '')
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command))['outcome'] == 'cancelled'
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command))['replayed']
    row = await pg_pool.fetchrow('SELECT stato,no_show_at FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)
    assert row['stato'] == status and (row['no_show_at'] is not None) == (operation == 'no_show')
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0


async def test_dark_status_failure_rolls_back(pg_pool, monkeypatch):
    from src.core.db.repositories.booking_allocation_repo import BookingAllocationRepository
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    original = BookingAllocationRepository._receipt
    async def fault(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('receipt fault')
    monkeypatch.setattr(BookingAllocationRepository, '_receipt', staticmethod(fault))
    with pytest.raises(RuntimeError, match='receipt fault'):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='cancel', allocation_revision=1), mutation=BookingMutation('cancel', '', '', ''))
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'in_attesa'
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) > 0

async def test_dark_reschedule_last_capacity_race_preserves_loser(pg_pool):
    env = await create_environment(pg_pool, configure=lambda catalog: catalog['resources'][0].update(capacity=1))
    first, second = await env.booking(), await env.booking()
    await pg_pool.execute("UPDATE bookings SET ora='12:00' WHERE organization_id=$1 AND id=$2", env.org, second)
    await env.apply(env.request(first))
    await env.apply(env.request(second, appointment_time='12:00'))
    requests = [env.request(b, action='replace', allocation_revision=1, appointment_time='14:00') for b in (first, second)]
    command = BookingMutation('reschedule', '', '', '', '2026-10-28', '14:00', 1)
    outcomes = await asyncio.gather(*(apply_booking_mutation(pg_pool, env.org, env.auth, r, mutation=command) for r in requests), return_exceptions=True)
    assert sum(isinstance(x, dict) and x['outcome'] == 'replaced' for x in outcomes) == 1, repr(outcomes)
    assert sum(isinstance(x, BookingMutationRejected) for x in outcomes) == 1
    winner = first if isinstance(outcomes[0], dict) else second
    loser = second if winner == first else first
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, loser) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, loser) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, requests[0 if loser == first else 1].request_id) == 0

async def test_dark_reschedule_stale_preimage_has_no_mutation(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    await pg_pool.execute("UPDATE bookings SET stato='confermata' WHERE organization_id=$1 AND id=$2", env.org, booking)
    request = env.request(booking, action='replace', allocation_revision=1, appointment_time='11:00')
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=BookingMutation('reschedule', '', '', '', '2026-10-28', '11:00', 1))
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 0


@pytest.mark.parametrize('operation', ['cancel', 'reject', 'no_show'])
async def test_status_command_cannot_smuggle_contact_changes_or_notice(operation):
    with pytest.raises(ValueError):
        BookingMutation(operation, 'Synthetic', '', '')
    with pytest.raises(ValueError):
        BookingMutation(operation, '', '', '', notice=True)


@pytest.mark.parametrize('target_change', [{'service_id': 'alternative'}, {'room_count': 2}])
async def test_reschedule_cannot_change_service_or_room_authority(pg_pool, target_change):
    from copy import deepcopy
    def configure(catalog):
        alternative = deepcopy(catalog['services'][0])
        alternative['service_id'] = 'alternative'
        catalog['services'].append(alternative)
    env = await create_environment(pg_pool, configure=configure)
    booking = await env.booking()
    await env.apply(env.request(booking))
    request = env.request(booking, action='replace', allocation_revision=1, appointment_time='11:00', **target_change)
    before = await pg_pool.fetch('SELECT * FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id', env.org, booking)
    with pytest.raises(BookingMutationRejected, match='cannot change service or room'):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request,
            mutation=BookingMutation('reschedule', '', '', '', '2026-10-28', '11:00', 1))
    assert await pg_pool.fetchval('SELECT ora FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == __import__('datetime').time(10)
    assert await pg_pool.fetch('SELECT * FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id', env.org, booking) == before
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 0


async def test_legacy_command_digest_ignores_absent_new_stay_fields():
    import hashlib
    import json
    contact, conversation = uuid4(), uuid4()
    command = BookingMutation('create', 'Synthetic', '', '', '2026-10-28', '10:00', 1)
    lineage = NewBookingLineage(contact, conversation)
    legacy = {'version': 1, 'mutation': {'operation': 'create', 'nome_cliente': 'Synthetic',
        'telefono': '', 'note': '', 'data': '2026-10-28', 'ora': '10:00',
        'coperti': 1, 'notice': False}, 'lineage': {'contact_id': contact, 'conversation_id': conversation}}
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    assert command.digest(lineage) == expected
