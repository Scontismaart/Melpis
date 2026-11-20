"""Forward-only completion evidence for managed minute bookings; no purge authority."""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from src.core.bookings.booking_mutation_contract import BookingMutation, apply_booking_mutation
from tests.core.bookings.test_allocation_engine_postgres import create_environment

pytestmark = pytest.mark.asyncio


async def _managed_booking(pool, sector='ristorante', *, day='2024-01-10'):
    env = await create_environment(pool, sector)
    booking = await env.booking()
    await pool.execute('UPDATE bookings SET data=$3::text::date WHERE organization_id=$1 AND id=$2', env.org, booking, day)
    await env.apply(env.request(booking, appointment_date=day))
    return env, booking


async def _complete(env, booking, day='2024-01-10'):
    command = BookingMutation('complete', '', '', '')
    request = env.request(booking, action='replace', allocation_revision=1, appointment_date=day)
    result = await apply_booking_mutation(env.pool, env.org, env.auth, request, mutation=command)
    assert result['outcome'] == 'replaced'
    return request, command


@pytest.mark.parametrize('sector', ['ristorante', 'parrucchiere', 'centro_estetico', 'studio_medico_dentista'])
async def test_managed_minute_completion_captures_same_transaction_evidence(pg_pool, sector):
    env, booking = await _managed_booking(pg_pool, sector)
    assert not await pg_pool.fetchval('SELECT EXISTS(SELECT 1 FROM ordinary_booking_completion_evidence WHERE organization_id=$1 AND booking_id=$2)', env.org, booking)
    request, command = await _complete(env, booking)
    evidence = await pg_pool.fetchrow('''SELECT e.*,b.completata_at,b.contact_id,b.id_conversazione
        FROM ordinary_booking_completion_evidence e JOIN bookings b ON b.organization_id=e.organization_id AND b.id=e.booking_id
        WHERE e.organization_id=$1 AND e.booking_id=$2''', env.org, booking)
    assert evidence['completed_at'] == evidence['completata_at']
    assert evidence['subject_id'] == evidence['contact_id']
    assert str(evidence['conversation_id']) == evidence['id_conversazione']
    assert evidence['timezone'] == 'Europe/Rome'
    assert evidence['completed_at'] > await pg_pool.fetchval("SELECT clock_timestamp()-interval '1 minute'")
    replay = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command)
    assert replay['replayed'] is True
    assert await pg_pool.fetchval('SELECT completed_at FROM ordinary_booking_completion_evidence WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == evidence['completed_at']
    # Evidence merely starts the retention clock; no policy or deletion is inferred.
    assert not await pg_pool.fetchval('SELECT ordinary_completed_booking_retention_eligible($1,$2)', env.org, booking)
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 1


async def test_owner_can_reinitialize_empty_table_but_not_truncate_completion_evidence(pg_pool):
    await pg_pool.execute('TRUNCATE ordinary_booking_completion_evidence')
    env, booking = await _managed_booking(pg_pool)
    await _complete(env, booking)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await pg_pool.execute('TRUNCATE ordinary_booking_completion_evidence')
    assert await pg_pool.fetchval('SELECT count(*) FROM ordinary_booking_completion_evidence WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1


async def test_untyped_or_future_completion_does_not_create_evidence(pg_pool):
    env, booking = await _managed_booking(pg_pool)
    await pg_pool.execute("UPDATE bookings SET stato='completata',completata_at=now() WHERE organization_id=$1 AND id=$2", env.org, booking)
    assert not await pg_pool.fetchval('SELECT EXISTS(SELECT 1 FROM ordinary_booking_completion_evidence WHERE organization_id=$1 AND booking_id=$2)', env.org, booking)
    future = await pg_pool.fetchval("SELECT (current_date+90)::text")
    env, booking = await _managed_booking(pg_pool, day=future)
    await _complete(env, booking, day=future)
    assert not await pg_pool.fetchval('SELECT EXISTS(SELECT 1 FROM ordinary_booking_completion_evidence WHERE organization_id=$1 AND booking_id=$2)', env.org, booking)


@pytest.mark.migration_limit(105)
async def test_migration_does_not_backfill_historical_completion(pg_pool):
    env, booking = await _managed_booking(pg_pool)
    await _complete(env, booking)
    migration = Path('src/core/db/migrations/106_ordinary_booking_completion_evidence.sql')
    sql = migration.read_text(encoding='utf-8')
    await pg_pool.execute(sql)
    await pg_pool.execute(sql)
    assert not await pg_pool.fetchval('SELECT EXISTS(SELECT 1 FROM ordinary_booking_completion_evidence WHERE organization_id=$1 AND booking_id=$2)', env.org, booking)


async def _approve(pool, org, months=12, version='v1'):
    await pool.execute('''INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,retention_calendar_months,
        legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'completed_bookings_and_stays',$2,true,$3,false,'test:ordinary-booking','qa',
               clock_timestamp(),clock_timestamp()+interval '1 day')''', org, version, months)


async def test_retention_eligibility_is_calendar_based_scope_and_hold_checked(pg_pool, other_org):
    env, booking = await _managed_booking(pg_pool)
    await _complete(env, booking)
    await _approve(pg_pool, env.org)
    assert not await pg_pool.fetchval('SELECT ordinary_completed_booking_retention_eligible($1,$2)', env.org, booking)
    # Model a historical observed completion only inside this isolated fixture.
    # The real evidence trigger remains immutable to every application role.
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE ordinary_booking_completion_evidence DISABLE TRIGGER ordinary_booking_completion_immutable')
        await conn.execute("UPDATE ordinary_booking_completion_evidence SET completed_at=clock_timestamp()-interval '12 months 1 day' WHERE organization_id=$1 AND booking_id=$2", env.org, booking)
        await conn.execute('ALTER TABLE ordinary_booking_completion_evidence ENABLE TRIGGER ordinary_booking_completion_immutable')
        await conn.execute('''UPDATE bookings b SET completata_at=e.completed_at FROM ordinary_booking_completion_evidence e
          WHERE b.organization_id=$1 AND e.organization_id=$1 AND b.id=$2 AND e.booking_id=$2''', env.org, booking)
    assert await pg_pool.fetchval('SELECT ordinary_completed_booking_retention_eligible($1,$2)', env.org, booking)
    assert not await pg_pool.fetchval('SELECT ordinary_completed_booking_retention_eligible($1,$2)', other_org['id'], booking)
    subject = await pg_pool.fetchval('SELECT subject_id FROM ordinary_booking_completion_evidence WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    hold = uuid4()
    await pg_pool.execute('''INSERT INTO gdpr_retention_holds(organization_id,hold_id,category,scope,scope_id,
        reason_code,authority_reference,approved_by,approved_at,review_by)
        VALUES($1,$2,'completed_bookings_and_stays','subject',$3,'litigation','test:synthetic','qa',
               clock_timestamp(),clock_timestamp()+interval '1 day')''', env.org, hold, subject)
    assert not await pg_pool.fetchval('SELECT ordinary_completed_booking_retention_eligible($1,$2)', env.org, booking)
    await pg_pool.execute('''INSERT INTO gdpr_retention_hold_events(organization_id,hold_id,event_id,kind,
         authority_reference,approved_by,occurred_at) VALUES($1,$2,$3,'released','synthetic:release','qa',clock_timestamp())''', env.org, hold, uuid4())
    assert await pg_pool.fetchval('SELECT ordinary_completed_booking_retention_eligible($1,$2)', env.org, booking)
    # Calendar month schedules cannot exceed the approved maximum.
    with pytest.raises(asyncpg.CheckViolationError):
        await _approve(pg_pool, env.org, 13, 'v2')
    await pg_pool.execute('''INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
        retention_calendar_months,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'completed_bookings_and_stays','v3',false,12,false,'test:ordinary-booking','qa',
               clock_timestamp(),clock_timestamp()+interval '1 day')''', env.org)
    assert not await pg_pool.fetchval('SELECT ordinary_completed_booking_retention_eligible($1,$2)', env.org, booking)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await pg_pool.execute('UPDATE ordinary_booking_completion_evidence SET completed_at=now() WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    for role in ('anon', 'authenticated', 'service_role', 'melpis_c24_retention'):
        assert not await pg_pool.fetchval("SELECT has_table_privilege($1,'ordinary_booking_completion_evidence','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')", role)
        assert not await pg_pool.fetchval("SELECT has_any_column_privilege($1,'ordinary_booking_completion_evidence','SELECT,INSERT,UPDATE,REFERENCES')", role)
        assert not await pg_pool.fetchval("SELECT has_function_privilege($1,'capture_ordinary_booking_completion()','EXECUTE')", role)
        assert not await pg_pool.fetchval("SELECT has_function_privilege($1,'ordinary_completed_booking_retention_eligible(uuid,uuid)','EXECUTE')", role)
