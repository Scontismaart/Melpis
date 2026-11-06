"""Dark owner transitions preserve occupied claims; no operational wiring."""
import asyncio
from datetime import date, time

import asyncpg
import pytest
from uuid import uuid4
from src.core.bookings.booking_mutation_contract import BookingMutation, BookingMutationRejected, apply_booking_mutation
from src.core.bookings.scheduled_booking_review import run_scheduled_booking_reviews
from src.core.db.repositories.booking_allocation_repo import AllocationConflict, AllocationDenied, AllocationUnavailable, _booking_fingerprint
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.bookings.test_allocation_engine_postgres import create_environment, wait_blocked

pytestmark = pytest.mark.asyncio

@pytest.mark.parametrize('operation,status', [('confirm','confermata'),('complete','completata')])
async def test_occupied_transition_preserves_capacity_and_replays(pg_pool, operation, status):
    env = await create_environment(pg_pool, configure=lambda catalog: catalog['resources'][0].update(capacity=1))
    booking, competitor = await env.booking(), await env.booking()
    await env.apply(env.request(booking))
    before = await pg_pool.fetch('SELECT resource_id,resource_kind,exclusive,quantity,starts_at,ends_at FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id', env.org, booking)
    request = env.request(booking, action='replace', allocation_revision=1)
    command = BookingMutation(operation, '', '', '')
    result = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command)
    assert result['outcome'] == 'replaced' and result['applied_allocation_revision'] == 2
    row = await pg_pool.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)
    assert row['stato'] == status and (row['completata_at'] is not None) == (operation == 'complete')
    assert await pg_pool.fetch('SELECT resource_id,resource_kind,exclusive,quantity,starts_at,ends_at FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id', env.org, booking) == before
    allocation = await pg_pool.fetchrow('SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    assert allocation['booking_fingerprint'] == _booking_fingerprint(row) and allocation['booking_calendar_revision'] == row['calendar_revision']
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command))['replayed']
    assert (await env.apply(env.request(competitor)))['outcome'] == 'unavailable'
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0

async def test_status_transition_rejects_unauthorized_stale_and_completed_reactivation(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    request = env.request(booking, action='replace', allocation_revision=1)
    with pytest.raises(AllocationDenied):
        await apply_booking_mutation(pg_pool, env.org, uuid4(), request, mutation=BookingMutation('confirm', '', '', ''))
    await pg_pool.execute("UPDATE bookings SET stato='confermata' WHERE organization_id=$1 AND id=$2", env.org, booking)
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=BookingMutation('complete', '', '', ''))
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1

async def test_occupied_transition_rolls_back_after_outbox(pg_pool, monkeypatch):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    original = BookingRepository.enqueue_calendar_confirmation
    async def fault(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise RuntimeError('fault after outbox')
    monkeypatch.setattr(BookingRepository, 'enqueue_calendar_confirmation', fault)
    request = env.request(booking, action='replace', allocation_revision=1)
    with pytest.raises(RuntimeError, match='fault after outbox'):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=BookingMutation('confirm', '', '', '', notice=True))
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'in_attesa'
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 0

async def test_review_status_transition_rebinds_valid_preimage(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    row = await pg_pool.fetchrow("UPDATE bookings SET stato='da_verificare' WHERE organization_id=$1 AND id=$2 RETURNING *", env.org, booking)
    await pg_pool.execute('UPDATE booking_allocation_sets SET booking_fingerprint=$3,booking_calendar_revision=$4 WHERE organization_id=$1 AND booking_id=$2', env.org, booking, _booking_fingerprint(row), row['calendar_revision'])
    result = await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='replace', allocation_revision=1), mutation=BookingMutation('complete', '', '', ''))
    assert result['outcome'] == 'replaced'
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'completata'


async def test_legacy_scheduled_review_preserves_managed_allocation(pg_pool, caplog):
    env = await create_environment(pg_pool)
    managed = await env.booking()
    await env.apply(env.request(managed))
    await apply_booking_mutation(
        pg_pool, env.org, env.auth, env.request(managed, action='replace', allocation_revision=1),
        mutation=BookingMutation('confirm', '', '', ''),
    )
    target_date = date(2026, 10, 28)
    repo = BookingRepository(pg_pool)
    legacy = await repo.create_booking(
        organization_id=env.org, nome_cliente='Synthetic', data=target_date,
        ora=time(10), coperti=1, stato='confermata',
    )
    assert await repo.mark_legacy_booking_for_review(env.org, managed, target_date) is None
    assert 'Scheduled review requires typed transition' in caplog.text
    assert await repo.mark_legacy_booking_for_review(uuid4(), legacy['id'], target_date) is None
    result = await repo.mark_legacy_booking_for_review(env.org, legacy['id'], target_date)
    assert result['stato'] == 'da_verificare'
    assert await repo.mark_legacy_booking_for_review(env.org, legacy['id'], target_date) is None
    row = await repo.get_booking(env.org, managed)
    fingerprint = await pg_pool.fetchval(
        'SELECT booking_fingerprint FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2',
        env.org, managed,
    )
    assert row['stato'] == 'confermata' and fingerprint == _booking_fingerprint(row)


async def test_scheduled_review_observes_concurrently_committed_allocation(pg_pool, monkeypatch):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await pg_pool.execute("UPDATE bookings SET stato='confermata' WHERE organization_id=$1 AND id=$2", env.org, booking)
    allocated = asyncio.Event()
    release = asyncio.Event()
    original = env.engine._receipt
    allocator_pid = None

    async def pause_after_receipt(conn, *args):
        nonlocal allocator_pid
        await original(conn, *args)
        allocator_pid = conn.get_server_pid()
        allocated.set()
        await release.wait()

    monkeypatch.setattr(env.engine, '_receipt', pause_after_receipt)
    admission = asyncio.create_task(env.apply(env.request(booking)))
    review = None
    try:
        await asyncio.wait_for(allocated.wait(), 5)
        review = asyncio.create_task(BookingRepository(pg_pool).mark_legacy_booking_for_review(
            env.org, booking, date(2026, 10, 28),
        ))
        await wait_blocked(pg_pool, allocator_pid)
    finally:
        release.set()
        results = await asyncio.gather(admission, *([review] if review else []), return_exceptions=True)
    assert results[0]['outcome'] == 'allocated' and results[1] is None
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'confermata'


async def test_scheduled_worker_atomically_reviews_elapsed_booking_with_private_receipt(pg_pool):
    env = await create_environment(pg_pool)
    await pg_pool.execute("UPDATE organizations SET subscription_status='active' WHERE id=$1", env.org)
    booking = await env.booking()
    await env.apply(env.request(booking))
    await apply_booking_mutation(
        pg_pool, env.org, env.auth, env.request(booking, action='replace', allocation_revision=1),
        mutation=BookingMutation('confirm', '', '', ''),
    )
    original_claims = await pg_pool.fetch(
        'SELECT resource_id,quantity,starts_at,ends_at FROM booking_resource_allocations '
        'WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id', env.org, booking,
    )
    assert original_claims
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('SET LOCAL ROLE service_role')
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.fetchval('SELECT public.apply_scheduled_booking_review($1,$2)', env.org, booking)
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('SET LOCAL ROLE melpis_booking_scheduler')
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.fetchval('SELECT nome_cliente FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)
        assert await conn.fetchval('SELECT public.apply_scheduled_booking_review($1,$2)', uuid4(), booking) is False
        assert await run_scheduled_booking_reviews(conn) >= 1
        assert await conn.fetchval('SELECT public.apply_scheduled_booking_review($1,$2)', env.org, booking) is False
    row = await BookingRepository(pg_pool).get_booking(env.org, booking)
    allocation = await pg_pool.fetchrow(
        'SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking,
    )
    assert row['stato'] == 'da_verificare' and row['no_show_at'] is None
    assert allocation['state'] == 'active' and allocation['allocation_revision'] == 3
    assert allocation['booking_fingerprint'] == _booking_fingerprint(row)
    assert allocation['booking_calendar_revision'] == row['calendar_revision']
    assert await pg_pool.fetch(
        'SELECT resource_id,quantity,starts_at,ends_at FROM booking_resource_allocations '
        'WHERE organization_id=$1 AND booking_id=$2 AND allocation_revision=3 ORDER BY resource_id', env.org, booking,
    ) == original_claims
    receipts = await pg_pool.fetch(
        "SELECT actor_user_id,action,outcome,applied_allocation_revision FROM booking_allocation_requests "
        "WHERE organization_id=$1 AND booking_id=$2 AND actor_kind='scheduled_review'", env.org, booking,
    )
    assert len(receipts) == 1 and tuple(receipts[0]) == (None, 'replace', 'replaced', 3)
    for operation in ('cancel', 'reject'):
        with pytest.raises(AllocationConflict):
            await apply_booking_mutation(
                pg_pool, env.org, env.auth, env.request(booking, action='cancel', allocation_revision=3),
                mutation=BookingMutation(operation, '', '', ''),
            )
    release = env.request(booking, action='cancel', allocation_revision=3)
    no_show = BookingMutation('no_show', '', '', '')
    result = await apply_booking_mutation(pg_pool, env.org, env.auth, release, mutation=no_show)
    assert result['outcome'] == 'cancelled' and result['applied_allocation_revision'] == 4
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, release, mutation=no_show))['replayed']
    row = await BookingRepository(pg_pool).get_booking(env.org, booking)
    assert row['stato'] == 'no_show' and row['no_show_at'] is not None
    assert await pg_pool.fetchval(
        'SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2',
        env.org, booking,
    ) == 0
    assert await pg_pool.fetchval(
        'SELECT booking_fingerprint FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2',
        env.org, booking,
    ) == _booking_fingerprint(row)

@pytest.mark.parametrize('authority,allowed', [('internal_calendar_sync', True),('google_authoritative', False),('external_authoritative', False)])
async def test_calendar_authority_is_local_or_fail_closed(pg_pool, authority, allowed):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    await BookingRepository(pg_pool).upsert_booking_settings_config(env.org, {'a05': {'authority': authority}})
    request = env.request(booking, action='replace', allocation_revision=1)
    if allowed:
        assert (await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=BookingMutation('confirm', '', '', '')))['outcome'] == 'replaced'
    else:
        with pytest.raises(AllocationUnavailable):
            await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=BookingMutation('confirm', '', '', ''))
        assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'in_attesa'

async def test_completed_booking_does_not_reactivate_to_confirmed(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='replace', allocation_revision=1), mutation=BookingMutation('complete', '', '', ''))
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='replace', allocation_revision=2), mutation=BookingMutation('confirm', '', '', ''))
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'completata'

@pytest.mark.parametrize('released_as', ['cancel','reject'])
async def test_coherent_release_reactivates_with_fresh_capacity(pg_pool, released_as):
    env = await create_environment(pg_pool, configure=lambda catalog: catalog['resources'][0].update(capacity=1))
    booking, competitor = await env.booking(), await env.booking()
    await env.apply(env.request(booking))
    release = env.request(booking, action='cancel', allocation_revision=1)
    await apply_booking_mutation(pg_pool, env.org, env.auth, release, mutation=BookingMutation(released_as, '', '', ''))
    assert (await env.apply(env.request(competitor)))['outcome'] == 'allocated'
    request = env.request(booking, action='allocate', allocation_revision=2)
    with pytest.raises(BookingMutationRejected):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=BookingMutation('reactivate', '', '', ''))
    assert await pg_pool.fetchval('SELECT state FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 'cancelled'
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(competitor, action='cancel', allocation_revision=1), mutation=BookingMutation('cancel', '', '', ''))
    result = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=BookingMutation('reactivate', '', '', ''))
    assert result['outcome'] == 'allocated' and result['applied_allocation_revision'] == 3
    assert (await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=BookingMutation('reactivate', '', '', '')))['replayed']
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'in_attesa'

async def test_unbound_legacy_cancel_cannot_reactivate(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    await pg_pool.execute("UPDATE bookings SET stato='cancellata' WHERE organization_id=$1 AND id=$2", env.org, booking)
    await env.apply(env.request(booking, action='cancel', allocation_revision=1))
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='allocate', allocation_revision=2), mutation=BookingMutation('reactivate', '', '', ''))
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0

async def test_no_show_cannot_reactivate(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='cancel', allocation_revision=1), mutation=BookingMutation('no_show', '', '', ''))
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='allocate', allocation_revision=2), mutation=BookingMutation('reactivate', '', '', ''))

async def test_occupied_transition_denied_after_erasure_barrier(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    operation = uuid4()
    await pg_pool.execute("""INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,kind,scope,status)
        VALUES($1,$2,'synthetic',$3,$4,'erase','organization','running')""", operation, env.org, uuid4(), 'e'*64)
    await pg_pool.execute("INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope) VALUES($1,$2,'organization')", env.org, operation)
    with pytest.raises(AllocationDenied):
        await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='replace', allocation_revision=1), mutation=BookingMutation('complete', '', '', ''))
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'in_attesa'

async def test_occupied_transition_rejects_changed_catalog_revision(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    request = env.request(booking, action='replace', allocation_revision=1)
    await env.publish(env.catalog)
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=BookingMutation('complete', '', '', ''))
    assert await pg_pool.fetchval('SELECT allocation_revision FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1


async def test_another_authorized_manager_cannot_replay_actor_bound_completed_command(pg_pool):
    env = await create_environment(pg_pool)
    manager = await create_environment(pg_pool)
    await pg_pool.execute("INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) VALUES($1,$2,'manager',now())", env.org, manager.actor)
    booking = await env.booking()
    await env.apply(env.request(booking))
    request = env.request(booking, action='replace', allocation_revision=1)
    command = BookingMutation('complete', '', '', '')
    await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command)
    before = await pg_pool.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)
    with pytest.raises(AllocationConflict):
        await apply_booking_mutation(pg_pool, env.org, manager.auth, request, mutation=command)
    assert await pg_pool.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == before
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND request_id=$2', env.org, request.request_id) == 1


async def test_release_fingerprint_is_the_actual_canceled_postimage(pg_pool):
    env = await create_environment(pg_pool)
    booking = await env.booking()
    await env.apply(env.request(booking))
    await apply_booking_mutation(pg_pool, env.org, env.auth, env.request(booking, action='cancel', allocation_revision=1), mutation=BookingMutation('cancel', '', '', ''))
    row = await pg_pool.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)
    allocation = await pg_pool.fetchrow('SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    assert allocation['state'] == 'cancelled' and allocation['booking_fingerprint'] == _booking_fingerprint(row)
    assert allocation['booking_calendar_revision'] == row['calendar_revision']
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
