"""Read-only, tenant-scoped purge-candidate planning for manual managed bookings."""
import json
import asyncio
from uuid import uuid4

import asyncpg
import pytest

from tests.core.bookings.test_allocation_engine_postgres import create_environment
from tests.core.gdpr.test_ordinary_booking_completion_evidence import _approve, _complete

pytestmark = pytest.mark.asyncio


async def _candidate(pool, *, conversation_linked=False):
    env = await create_environment(pool)
    booking = await env.booking()
    day = '2024-01-10'
    # Manual booking retains a verified contact, with no conversation/source.
    await pool.execute('''UPDATE bookings SET data=$3::text::date,
        id_conversazione=CASE WHEN $4 THEN id_conversazione ELSE NULL END
        WHERE organization_id=$1 AND id=$2''', env.org, booking, day, conversation_linked)
    request = env.request(booking, appointment_date=day)
    await env.apply(request)
    completed, _ = await _complete(env, booking, day=day)
    await _approve(pool, env.org)
    async with pool.acquire() as conn, conn.transaction():
        # Synthetic age fixture; the installed lifecycle still protects all
        # updates to completion evidence outside this isolated database.
        await conn.execute('ALTER TABLE ordinary_booking_completion_evidence DISABLE TRIGGER ordinary_booking_completion_immutable')
        await conn.execute("UPDATE ordinary_booking_completion_evidence SET completed_at=clock_timestamp()-interval '12 months 1 day' WHERE organization_id=$1 AND booking_id=$2", env.org, booking)
        await conn.execute('ALTER TABLE ordinary_booking_completion_evidence ENABLE TRIGGER ordinary_booking_completion_immutable')
        # Anchor the synthetic observation and matching receipt to the same
        # historical timestamp, without introducing a production backfill.
        await conn.execute('''UPDATE bookings b SET completata_at=e.completed_at FROM ordinary_booking_completion_evidence e
          WHERE b.organization_id=$1 AND e.organization_id=$1 AND b.id=$2 AND e.booking_id=$2''', env.org, booking)
        await conn.execute('ALTER TABLE booking_allocation_requests DISABLE TRIGGER allocation_immutable')
        await conn.execute('''UPDATE booking_allocation_requests r SET created_at=e.completed_at
            FROM ordinary_booking_completion_evidence e WHERE r.organization_id=$1 AND e.organization_id=$1
            AND r.request_id=$3 AND e.booking_id=$2''', env.org, booking, completed.request_id)
        await conn.execute('ALTER TABLE booking_allocation_requests ENABLE TRIGGER allocation_immutable')
    return env, booking, (request.request_id, completed.request_id)


async def _plan(pool, org, booking):
    async with pool.acquire() as conn, conn.transaction(readonly=True, isolation='read_committed'):
        result = await conn.fetchval('SELECT ordinary_completed_booking_retention_plan($1,$2)', org, booking)
    return json.loads(result) if result is not None else None


async def test_plan_is_specific_repeatable_and_read_only(pg_pool, other_org):
    from src.core.gdpr.restore_checkpoint import RestoreBlocked
    from src.core.gdpr.source_checkpoint import capture_due_ordinary_bookings

    env, booking, request_ids = await _candidate(pg_pool)
    subject = await pg_pool.fetchval('SELECT contact_id FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)
    plan = await _plan(pg_pool, env.org, booking)
    assert plan['kind'] == 'ordinary_managed_booking_candidate_v1'
    assert plan['organization_id'] == str(env.org)
    assert plan['booking_id'] == str(booking)
    assert plan['subject_id'] == str(subject)
    assert plan['policy_version'] == 'v1'
    assert plan['allocation_revision'] == 2
    assert plan['request_ids'] == sorted(str(value) for value in request_ids)
    assert plan['deletion_authorized'] is False
    assert await _plan(pg_pool, env.org, booking) == plan
    assert await _plan(pg_pool, other_org['id'], booking) is None
    async with pg_pool.acquire() as conn:
        with pytest.raises(RestoreBlocked, match="read-only"):
            await capture_due_ordinary_bookings(conn, env.org)
        async with conn.transaction(readonly=True):
            assert await capture_due_ordinary_bookings(conn, env.org, limit=1) == ((booking,), None)
            assert await capture_due_ordinary_bookings(conn, env.org, after=booking) == ((), None)
            assert await capture_due_ordinary_bookings(conn, other_org['id']) == ((), None)
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 2
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    with pytest.raises(asyncpg.InvalidTransactionStateError):
        await pg_pool.fetchval('SELECT ordinary_completed_booking_retention_plan($1,$2)', env.org, booking)
    for role in ('anon', 'authenticated', 'service_role', 'melpis_c24_retention', 'melpis_c24_booking_worker'):
        assert not await pg_pool.fetchval("SELECT has_function_privilege($1,'ordinary_completed_booking_retention_plan(uuid,uuid)','EXECUTE')", role)


@pytest.mark.parametrize('blocked_field, value', [
    ('deposit_amount_minor', 100), ('source_message_id', 'synthetic-source'),
    ('google_event_id', 'synthetic-event'), ('reminder_status', 'sent'),
])
async def test_plan_refuses_known_financial_or_external_dependencies(pg_pool, blocked_field, value):
    env, booking, _ = await _candidate(pg_pool)
    await pg_pool.execute(f'UPDATE bookings SET {blocked_field}=$3 WHERE organization_id=$1 AND id=$2', env.org, booking, value)
    assert await _plan(pg_pool, env.org, booking) is None


async def test_plan_refuses_a_technical_receipt_hold(pg_pool):
    env, booking, requests = await _candidate(pg_pool)
    await pg_pool.execute('''INSERT INTO gdpr_retention_holds(organization_id,hold_id,category,scope,scope_id,
        reason_code,authority_reference,approved_by,approved_at,review_by)
        VALUES($1,$2,'technical_receipts','record',$3,'litigation','test:receipt-hold','qa',
               clock_timestamp(),clock_timestamp()+interval '1 day')''', env.org, uuid4(), requests[0])
    assert await _plan(pg_pool, env.org, booking) is None


async def test_conversation_candidate_requires_exact_verified_lineage(pg_pool, other_org):
    env, booking, requests = await _candidate(pg_pool, conversation_linked=True)
    plan = await _plan(pg_pool, env.org, booking)
    evidence = await pg_pool.fetchrow('''SELECT subject_id,conversation_id
        FROM ordinary_booking_completion_evidence WHERE organization_id=$1 AND booking_id=$2''', env.org, booking)
    assert plan['kind'] == 'ordinary_managed_booking_conversation_candidate_v1'
    assert plan['conversation_id'] == str(evidence['conversation_id'])
    assert plan['subject_id'] == str(evidence['subject_id'])
    assert plan['request_ids'] == sorted(map(str, requests))
    assert plan['deletion_authorized'] is False
    assert await _plan(pg_pool, other_org['id'], booking) is None
    sibling, different_subject = uuid4(), uuid4()
    await pg_pool.execute('INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)',
                          different_subject, env.org, str(uuid4()))
    await pg_pool.execute('''INSERT INTO conversations(id,organization_id,contact_id)
        VALUES($1,$2,$3)''', sibling, env.org, different_subject)
    await pg_pool.execute('''UPDATE bookings SET id_conversazione=$3
        WHERE organization_id=$1 AND id=$2''', env.org, booking, str(sibling))
    assert await _plan(pg_pool, env.org, booking) is None


async def test_conversation_candidate_respects_hold_and_quarantine(pg_pool):
    env, booking, _ = await _candidate(pg_pool, conversation_linked=True)
    plan = await _plan(pg_pool, env.org, booking)
    await pg_pool.execute('''INSERT INTO gdpr_retention_holds(organization_id,hold_id,category,scope,scope_id,
        reason_code,authority_reference,approved_by,approved_at,review_by)
        VALUES($1,$2,'technical_receipts','conversation',$3,'litigation','test:conversation-hold','qa',
               clock_timestamp(),clock_timestamp()+interval '1 day')''', env.org, uuid4(), plan['conversation_id'])
    assert await _plan(pg_pool, env.org, booking) is None
    other, other_booking, _ = await _candidate(pg_pool, conversation_linked=True)
    assert await _plan(pg_pool, other.org, other_booking) is not None
    await pg_pool.execute('''INSERT INTO booking_provenance_quarantine
        (organization_id,booking_id,review_reference,reason_code,reviewed_at,review_due_at)
        VALUES($1,$2,'synthetic-review','historical_provenance_unverified',
               clock_timestamp()-interval '1 minute',clock_timestamp()+interval '30 days')''', other.org, other_booking)
    assert await _plan(pg_pool, other.org, other_booking) is None


async def test_plan_refuses_linked_accounting_and_never_mutates_it(pg_pool, other_org):
    env, booking, _ = await _candidate(pg_pool)
    metadata = json.dumps({'booking_id': str(booking)})
    foreign = uuid4()
    await pg_pool.execute('INSERT INTO usage_events(id,organization_id,event_type,metadata) VALUES($1,$2,$3,$4::jsonb)',
                          foreign, other_org['id'], 'test.booking', metadata)
    assert await _plan(pg_pool, env.org, booking) is not None
    linked = uuid4()
    await pg_pool.execute('INSERT INTO usage_events(id,organization_id,event_type,metadata) VALUES($1,$2,$3,$4::jsonb)',
                          linked, env.org, 'test.booking', metadata)
    assert await _plan(pg_pool, env.org, booking) is None
    assert await pg_pool.fetchval('SELECT count(*) FROM usage_events WHERE id=ANY($1::uuid[])', [foreign, linked]) == 2


async def test_plan_defers_contended_tenant_without_waiting(pg_pool):
    env, booking, _ = await _candidate(pg_pool)
    other, other_booking, _ = await _candidate(pg_pool)
    async with pg_pool.acquire() as locked, locked.transaction():
        await locked.execute('SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))', str(env.org))
        assert await asyncio.wait_for(_plan(pg_pool, env.org, booking), timeout=5) is None
        assert await asyncio.wait_for(_plan(pg_pool, other.org, other_booking), timeout=5) is not None
    assert await _plan(pg_pool, env.org, booking) is not None
