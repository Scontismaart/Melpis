"""Bounded automated retention on a disposable PostgreSQL database."""
import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.gdpr.c24_retention_controller import run_c24_retention_sweep
from tests.core.bookings.test_conversation_state_postgres import state_env, commit_source, apply
from tests.core.gdpr.test_c24_idle_expiry import age_message, age_state
from tests.core.gdpr.test_conversation_lifecycle_c24 import approve_conversation
from tests.core.test_quota_reservation_lifecycle import _organization, _used

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def retention_operator(pg_pool, postgres_container, monkeypatch):
    role, password='retention_sweep_'+uuid4().hex, uuid4().hex
    async with pg_pool.acquire() as admin:
        await admin.execute(f"CREATE ROLE {role} LOGIN NOINHERIT PASSWORD '{password}'")
        await admin.execute(f'GRANT melpis_c24_retention TO {role}')
    dsn=postgres_container.get_connection_url().replace('+psycopg2','')
    operator=await asyncpg.create_pool(dsn=dsn,user=role,password=password,min_size=1,max_size=1)
    parsed = urlsplit(dsn)
    monkeypatch.setenv('GDPR_C24_OWNER_RETENTION_DSN', urlunsplit(parsed._replace(
        netloc=f'{role}:{password}@{parsed.netloc.rsplit("@", 1)[-1]}')))
    for key in ('GDPR_RETENTION_EXECUTION_ENABLED','GDPR_C24_OWNER_RETENTION_ENABLED',
                'GDPR_C24_AUTO_RETENTION_ENABLED'):
        monkeypatch.setenv(key,'true')
    try:
        yield operator
    finally:
        await operator.close()
        async with pg_pool.acquire() as admin:
            await admin.execute(f'DROP ROLE {role}')


async def approve_messages(pool, org, days=90):
    await pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
        retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'messages_content','synthetic-sweep',true,$2,false,'test:sweep','qa',
        clock_timestamp()-interval '1 minute',clock_timestamp()+interval '1 day')""",org,days)


async def completed_inbox(pool, org, *, age_days=91):
    inbox=uuid4()
    await pool.execute("""INSERT INTO meta_webhook_inbox(id,organization_id,channel,event_key,payload,
        trace_id,status) VALUES($1,$2,'whatsapp',$3,'{\"synthetic\":\"content\"}'::jsonb,'qa','completed')""",
        inbox,org,str(uuid4()))
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE meta_webhook_inbox DISABLE TRIGGER c24_inbox_lifecycle_guard')
        await conn.execute("UPDATE meta_webhook_inbox SET c24_completed_at=clock_timestamp()-make_interval(days=>$3) WHERE organization_id=$1 AND id=$2",org,inbox,age_days)
        await conn.execute('ALTER TABLE meta_webhook_inbox ENABLE TRIGGER c24_inbox_lifecycle_guard')
    return inbox


async def shorten_sweep_deadline(pool, function_name):
    # Exercise the production deadline branches against the same database
    # body using a shorter interval only inside this disposable fixture.
    definition = await pool.fetchval('SELECT pg_get_functiondef($1::text::regprocedure)', function_name)
    assert definition.count("interval '12 seconds'") == 1
    await pool.execute(definition.replace("interval '12 seconds'", "interval '2 seconds'"))


async def test_scheduler_uses_dedicated_connection_and_distributed_lock(pg_pool, retention_operator):
    from src.core.jobs.base import compute_advisory_lock_id
    from src.core.scheduler import _run_c24_retention_sweep_async

    org = (await _organization(pg_pool))['id']
    await approve_messages(pg_pool, org)
    inbox = await completed_inbox(pg_pool, org)
    dsn = os.environ['GDPR_C24_OWNER_RETENTION_DSN']
    lock_id = compute_advisory_lock_id('c24_retention_sweep')
    async with retention_operator.acquire() as blocker:
        await blocker.execute('SELECT pg_advisory_lock($1)', lock_id)
        try:
            await asyncio.wait_for(_run_c24_retention_sweep_async(dsn), timeout=10)
            assert await pg_pool.fetchval('SELECT c24_payload_purged_at IS NULL FROM meta_webhook_inbox WHERE id=$1', inbox)
        finally:
            await blocker.execute('SELECT pg_advisory_unlock($1)', lock_id)

    # The actual scheduler must make progress while holding its separate
    # advisory-lock connection; a pool that cannot lend a second one hangs.
    await asyncio.wait_for(_run_c24_retention_sweep_async(dsn), timeout=10)
    assert await pg_pool.fetchval("SELECT c24_payload_purged_at IS NOT NULL AND payload='{}'::jsonb FROM meta_webhook_inbox WHERE id=$1", inbox)
    await asyncio.wait_for(_run_c24_retention_sweep_async(dsn), timeout=10)


@pytest.mark.parametrize('failed_scan', ['retention', 'hotel_erasure'])
async def test_independent_sweeps_preserve_other_cursor_on_timeout(pg_pool, retention_operator, failed_scan):
    from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence

    org = (await _organization(pg_pool))['id']
    await approve_messages(pg_pool, org)
    inbox = await completed_inbox(pg_pool, org)
    env, _, booking = await _complete_with_old_evidence(pg_pool)
    await pg_pool.execute(f"""CREATE FUNCTION test_sweep_cursor_timeout() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic cursor timeout' USING ERRCODE='57014'; END $$;
        CREATE TRIGGER test_sweep_cursor_timeout BEFORE UPDATE ON c24_retention_sweep_cursor
        FOR EACH ROW WHEN (OLD.scan_name='{failed_scan}') EXECUTE FUNCTION test_sweep_cursor_timeout();""")

    # A transient failure must remain visible, but cannot roll back or prevent
    # the other independent policy category's successful transaction.
    with pytest.raises(asyncpg.QueryCanceledError, match='synthetic cursor timeout'):
        await run_c24_retention_sweep(retention_operator)
    assert await pg_pool.fetchval("SELECT c24_payload_purged_at IS NOT NULL FROM meta_webhook_inbox WHERE id=$1", inbox) == (failed_scan != 'retention')
    assert await pg_pool.fetchval("SELECT state='cancelled' FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2", env.org, booking) == (failed_scan != 'hotel_erasure')
    assert await pg_pool.fetchval("SELECT phase=0 FROM c24_retention_sweep_cursor WHERE scan_name=$1", failed_scan)
    other_scan = 'hotel_erasure' if failed_scan == 'retention' else 'retention'
    assert await pg_pool.fetchval("SELECT phase=1 FROM c24_retention_sweep_cursor WHERE scan_name=$1", other_scan)

    await pg_pool.execute('DROP TRIGGER test_sweep_cursor_timeout ON c24_retention_sweep_cursor')
    await run_c24_retention_sweep(retention_operator)
    assert await pg_pool.fetchval("SELECT c24_payload_purged_at IS NOT NULL FROM meta_webhook_inbox WHERE id=$1", inbox)
    assert await pg_pool.fetchval("SELECT NOT EXISTS (SELECT 1 FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2 AND state='active')", env.org, booking)


async def test_automatic_sweep_expires_state_without_consuming_receipt_or_usage(state_env, retention_operator):
    env=state_env; request=uuid4()
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'],request=request)
    await approve_conversation(env['pool'],env['org'])
    await age_state(env,31)
    used=await _used(env['pool'],env['org'])
    assert await run_c24_retention_sweep(retention_operator)=={'enabled':True,'batch_clear':True}
    assert not await env['pool'].fetchval('SELECT EXISTS(SELECT 1 FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2)',env['org'],env['conv'])
    assert await env['pool'].fetchval('SELECT EXISTS(SELECT 1 FROM booking_conversation_requests WHERE organization_id=$1 AND request_id=$2)',env['org'],request)
    assert await _used(env['pool'],env['org'])==used


async def test_sweep_defers_held_tenant_without_starving_another(pg_pool, retention_operator):
    held=(await _organization(pg_pool))['id']; eligible=(await _organization(pg_pool))['id']
    for org in (held,eligible):
        await approve_messages(pg_pool,org)
    blocked=await completed_inbox(pg_pool,held); clean=await completed_inbox(pg_pool,eligible)
    await pg_pool.execute("""INSERT INTO gdpr_retention_holds(organization_id,hold_id,category,scope,
        reason_code,authority_reference,approved_by,approved_at,review_by)
        VALUES($1,$2,'messages_content','organization','litigation','case:synthetic','qa',
        clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day')""",held,uuid4())
    result=await run_c24_retention_sweep(retention_operator)
    assert result=={'enabled':True,'batch_clear':False}
    assert await pg_pool.fetchval("SELECT payload<>'{}'::jsonb FROM meta_webhook_inbox WHERE organization_id=$1 AND id=$2",held,blocked)
    assert await pg_pool.fetchval("SELECT payload='{}'::jsonb AND c24_payload_purged_at IS NOT NULL FROM meta_webhook_inbox WHERE organization_id=$1 AND id=$2",eligible,clean)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError,match='cannot be restored'):
        await pg_pool.execute("UPDATE meta_webhook_inbox SET payload='{\"synthetic\":\"replay\"}'::jsonb WHERE organization_id=$1 AND id=$2",eligible,clean)
    assert not await pg_pool.fetchval('SELECT public.c24_purge_inbox($1,$2)',held,clean)


@pytest.mark.parametrize('lock_kind', ['tenant', 'row'])
async def test_sweep_advances_past_contended_target(pg_pool, retention_operator, lock_kind):
    blocked_org, clean_org = sorted([(await _organization(pg_pool))['id'], (await _organization(pg_pool))['id']])
    for org in (blocked_org, clean_org):
        await approve_messages(pg_pool, org)
    blocked = await completed_inbox(pg_pool, blocked_org)
    clean = await completed_inbox(pg_pool, clean_org)

    async with pg_pool.acquire() as blocker, blocker.transaction():
        if lock_kind == 'tenant':
            await blocker.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(blocked_org))
        else:
            await blocker.fetchval('SELECT id FROM meta_webhook_inbox WHERE organization_id=$1 AND id=$2 FOR UPDATE', blocked_org, blocked)
        result = await asyncio.wait_for(run_c24_retention_sweep(retention_operator), timeout=5)
        assert result == {'enabled': True, 'batch_clear': False}
        assert await pg_pool.fetchval("SELECT c24_payload_purged_at IS NULL FROM meta_webhook_inbox WHERE id=$1", blocked)
        assert await pg_pool.fetchval("SELECT c24_payload_purged_at IS NOT NULL FROM meta_webhook_inbox WHERE id=$1", clean)
        cursor = await pg_pool.fetchrow("SELECT phase,last_organization_id,last_record_id FROM c24_retention_sweep_cursor WHERE scan_name='retention'")
        assert tuple(cursor) == (1, clean_org, clean)

    assert await run_c24_retention_sweep(retention_operator) == {'enabled': True, 'batch_clear': True}
    assert await pg_pool.fetchval("SELECT c24_payload_purged_at IS NOT NULL FROM meta_webhook_inbox WHERE id=$1", blocked)


async def test_sweep_yields_and_resumes_under_sustained_contention(pg_pool, retention_operator):
    org = (await _organization(pg_pool))['id']
    await approve_messages(pg_pool, org)
    inboxes = sorted([await completed_inbox(pg_pool, org) for _ in range(20)])
    await shorten_sweep_deadline(pg_pool, 'public.c24_retention_sweep()')
    async with pg_pool.acquire() as blocker, blocker.transaction():
        await blocker.fetch('SELECT id FROM meta_webhook_inbox WHERE organization_id=$1 FOR UPDATE', org)
        assert await asyncio.wait_for(run_c24_retention_sweep(retention_operator), timeout=5) == {
            'enabled': True, 'batch_clear': False,
        }
        cursor = await pg_pool.fetchrow("SELECT phase,last_organization_id,last_record_id FROM c24_retention_sweep_cursor WHERE scan_name='retention'")
        assert cursor['phase'] == 1 and cursor['last_organization_id'] == org
        assert cursor['last_record_id'] in inboxes[:-1]
        assert await pg_pool.fetchval("SELECT count(*) FROM meta_webhook_inbox WHERE organization_id=$1 AND c24_payload_purged_at IS NOT NULL", org) == 0

    assert await run_c24_retention_sweep(retention_operator) == {'enabled': True, 'batch_clear': True}
    assert await pg_pool.fetchval("SELECT c24_payload_purged_at IS NOT NULL FROM meta_webhook_inbox WHERE id=$1", inboxes[-1])
    assert await run_c24_retention_sweep(retention_operator) == {'enabled': True, 'batch_clear': True}
    assert await pg_pool.fetchval("SELECT count(*) FROM meta_webhook_inbox WHERE organization_id=$1 AND c24_payload_purged_at IS NOT NULL", org) == len(inboxes)


@pytest.mark.parametrize('condition', [asyncpg.DeadlockDetectedError, asyncpg.InsufficientPrivilegeError, asyncpg.CheckViolationError, asyncpg.QueryCanceledError])
async def test_sweep_defers_only_expected_target_failures(pg_pool, retention_operator, condition):
    first_org, second_org = sorted([(await _organization(pg_pool))['id'], (await _organization(pg_pool))['id']])
    for org in (first_org, second_org):
        await approve_messages(pg_pool, org)
    first = await completed_inbox(pg_pool, first_org)
    second = await completed_inbox(pg_pool, second_org)
    # Raise a specific SQLSTATE inside a synthetic target transaction to test
    # classification, independently of PostgreSQL's deadlock victim selection.
    await pg_pool.execute(f"""CREATE FUNCTION test_sweep_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic target classification' USING ERRCODE='{condition.sqlstate}'; END $$;
        CREATE TRIGGER test_sweep_failure BEFORE UPDATE ON meta_webhook_inbox
        FOR EACH ROW WHEN (OLD.id='{second}'::uuid) EXECUTE FUNCTION test_sweep_failure();""")
    if condition is asyncpg.DeadlockDetectedError:
        assert await run_c24_retention_sweep(retention_operator) == {'enabled': True, 'batch_clear': False}
        assert await pg_pool.fetchval("SELECT c24_payload_purged_at IS NOT NULL FROM meta_webhook_inbox WHERE id=$1", first)
        assert await pg_pool.fetchval("SELECT last_record_id FROM c24_retention_sweep_cursor WHERE scan_name='retention'") == second
    else:
        with pytest.raises(condition, match='synthetic target classification'):
            await run_c24_retention_sweep(retention_operator)
        assert await pg_pool.fetchval("SELECT c24_payload_purged_at IS NULL FROM meta_webhook_inbox WHERE id=$1", first)
        assert await pg_pool.fetchval("SELECT phase FROM c24_retention_sweep_cursor WHERE scan_name='retention'") == 0
    assert await pg_pool.fetchval("SELECT c24_payload_purged_at IS NULL FROM meta_webhook_inbox WHERE id=$1", second)


@pytest.mark.parametrize('caller_timeout, expected_cap', [('25ms', '25ms'), ('0', '250ms')])
async def test_sweep_respects_and_restores_caller_lock_timeout(pg_pool, retention_operator, caller_timeout, expected_cap):
    org = (await _organization(pg_pool))['id']
    await approve_messages(pg_pool, org)
    inbox = await completed_inbox(pg_pool, org)
    await pg_pool.execute(f"""CREATE FUNCTION test_sweep_timeout() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('lock_timeout')<>'{expected_cap}' THEN RAISE EXCEPTION 'unexpected cap'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER test_sweep_timeout BEFORE UPDATE ON meta_webhook_inbox
        FOR EACH ROW WHEN (OLD.id='{inbox}'::uuid) EXECUTE FUNCTION test_sweep_timeout();""")
    async with retention_operator.acquire() as conn, conn.transaction():
        await conn.execute('SET LOCAL ROLE melpis_c24_retention')
        await conn.fetchval("SELECT set_config('lock_timeout',$1,true)", caller_timeout)
        assert await conn.fetchval("SELECT c24_run_retention_target('sweep',$1,$1)", UUID(int=0))
        assert await conn.fetchval("SELECT current_setting('lock_timeout')") == caller_timeout


async def test_unverified_inbox_completion_is_never_inferred(pg_pool, retention_operator):
    org=(await _organization(pg_pool))['id']; await approve_messages(pg_pool,org)
    inbox=await completed_inbox(pg_pool,org)
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE meta_webhook_inbox DISABLE TRIGGER c24_inbox_lifecycle_guard')
        await conn.execute("UPDATE meta_webhook_inbox SET c24_completed_at=NULL,completed_at=clock_timestamp()-interval '1 year' WHERE organization_id=$1 AND id=$2",org,inbox)
        await conn.execute('ALTER TABLE meta_webhook_inbox ENABLE TRIGGER c24_inbox_lifecycle_guard')
    await run_c24_retention_sweep(retention_operator)
    assert await pg_pool.fetchval("SELECT payload<>'{}'::jsonb AND c24_payload_purged_at IS NULL FROM meta_webhook_inbox WHERE organization_id=$1 AND id=$2",org,inbox)


async def test_message_purge_uses_server_time_and_only_related_envelopes(state_env):
    env=state_env; pool=env['pool']; org=env['org']; source=env['message']
    await commit_source(env,source,'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,source)
    await pool.execute("UPDATE messages SET status='handled',handling_type='ai',created_at=clock_timestamp()-interval '1 year' WHERE organization_id=$1 AND id=$2",org,source)
    await approve_messages(pool,org)
    # Caller-writable created_at cannot make a new message eligible.
    assert not await pool.fetchval('SELECT public.c24_purge_source($1,$2)',org,source)
    await age_message(pool,org,source,91)
    # An unrelated fresh envelope must not keep every old message forever.
    unrelated=await completed_inbox(pool,org,age_days=0)
    assert await pool.fetchval('SELECT public.c24_purge_source($1,$2)',org,source)
    assert await pool.fetchval("SELECT payload<>'{}'::jsonb FROM meta_webhook_inbox WHERE organization_id=$1 AND id=$2",org,unrelated)
    assert await pool.fetchval('SELECT content_purged_at IS NOT NULL FROM messages WHERE organization_id=$1 AND id=$2',org,source)


async def test_related_raw_envelope_blocks_source_until_scrubbed(state_env):
    import json
    env=state_env; pool=env['pool']; org=env['org']; source=env['message']
    await commit_source(env,source,'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,source)
    await pool.execute("UPDATE messages SET status='handled',handling_type='ai' WHERE organization_id=$1 AND id=$2",org,source)
    await approve_messages(pool,org); await age_message(pool,org,source,91)
    inbox=await completed_inbox(pool,org)
    wam=await pool.fetchval('SELECT wam_id FROM messages WHERE organization_id=$1 AND id=$2',org,source)
    payload={'entry':[{'changes':[{'value':{'messages':[{'id':wam}]}}]}]}
    await pool.execute('UPDATE meta_webhook_inbox SET payload=$3::jsonb WHERE organization_id=$1 AND id=$2',org,inbox,json.dumps(payload))
    assert not await pool.fetchval('SELECT public.c24_purge_source($1,$2)',org,source)
    assert await pool.fetchval('SELECT public.c24_purge_inbox($1,$2)',org,inbox)
    assert await pool.fetchval('SELECT public.c24_purge_source($1,$2)',org,source)


async def test_outbound_source_purge_uses_immutable_server_receipt_time(state_env):
    env = state_env; pool = env['pool']; org = env['org']; source = env['message']
    await commit_source(env, source, '2026-11-12', {'service_id': 'standard'}, 1700000000)
    await apply(env, source)
    stamped = await pool.fetchval(
        'SELECT c24_received_at FROM messages WHERE organization_id=$1 AND id=$2', org, source,
    )
    assert stamped is not None
    # Reclassify the synthetic source to represent a completed outbound receipt;
    # caller-controlled created_at remains deliberately irrelevant.
    await pool.execute("""UPDATE messages SET direction='outbound',status='sent',
        created_at=clock_timestamp()-interval '2 years'
        WHERE organization_id=$1 AND id=$2""", org, source)
    await approve_messages(pool, org)
    assert not await pool.fetchval('SELECT public.c24_purge_source($1,$2)', org, source)
    await age_message(pool, org, source, 91)
    aged_stamp = await pool.fetchval(
        'SELECT c24_received_at FROM messages WHERE organization_id=$1 AND id=$2', org, source,
    )
    assert await pool.fetchval('SELECT public.c24_purge_source($1,$2)', org, source)
    row = await pool.fetchrow(
        'SELECT content_purged_at,content_text,c24_received_at FROM messages WHERE organization_id=$1 AND id=$2',
        org, source,
    )
    assert row['content_purged_at'] is not None and row['content_text'] is None
    assert row['c24_received_at'] == aged_stamp and aged_stamp < stamped


async def test_hotel_erasure_cursor_archives_then_purges_only_after_civil_retention(
        pg_pool, retention_operator):
    from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence

    env, lineage, booking = await _complete_with_old_evidence(pg_pool)
    before = await pg_pool.fetchval(
        "SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2",
        env.org, booking,
    )
    assert before > 0

    # First bounded phase releases occupancy, but preserves the stay and its
    # evidence. The second phase is cursor-addressable and performs the purge.
    result = await run_c24_retention_sweep(retention_operator)
    assert result == {"enabled": True, "batch_clear": True}
    assert await pg_pool.fetchval(
        "SELECT state='cancelled' FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2",
        env.org, booking,
    )
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2",
        env.org, booking,
    ) == 1
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2",
        env.org, booking,
    ) == 0

    result = await run_c24_retention_sweep(retention_operator)
    assert result == {"enabled": True, "batch_clear": True}
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2",
        env.org, booking,
    ) == 0
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM booking_stay_purge_audit WHERE organization_id=$1 AND booking_id=$2 AND purpose='retention'",
        env.org, booking,
    ) == 1
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2",
        env.org, booking,
    ) == 2
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM messages WHERE organization_id=$1 AND id=$2",
        env.org, lineage.source_message_id,
    ) == 1


async def test_hotel_sweep_advances_past_contended_tenant(pg_pool, retention_operator):
    from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence

    stays = [await _complete_with_old_evidence(pg_pool), await _complete_with_old_evidence(pg_pool)]
    (blocked, _, blocked_booking), (clean, _, clean_booking) = sorted(stays, key=lambda stay: stay[0].org)
    async with pg_pool.acquire() as blocker, blocker.transaction():
        await blocker.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(blocked.org))
        assert await asyncio.wait_for(run_c24_retention_sweep(retention_operator), timeout=5) == {
            'enabled': True, 'batch_clear': False,
        }
        assert await pg_pool.fetchval("SELECT state='active' FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2", blocked.org, blocked_booking)
        assert await pg_pool.fetchval("SELECT state='cancelled' FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2", clean.org, clean_booking)
        cursor = await pg_pool.fetchrow("SELECT phase,last_organization_id,last_record_id FROM c24_retention_sweep_cursor WHERE scan_name='hotel_erasure'")
        assert tuple(cursor) == (1, clean.org, clean_booking)

    # The next phase may purge the unblocked stay before the cursor wraps;
    # a later pass must still archive the originally contended stay.
    for _ in range(3):
        await run_c24_retention_sweep(retention_operator)
    assert not await pg_pool.fetchval("SELECT EXISTS(SELECT 1 FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2 AND state='active')", blocked.org, blocked_booking)


async def test_hotel_sweep_yields_after_successful_target_then_resumes(pg_pool, retention_operator):
    from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence

    stays = [await _complete_with_old_evidence(pg_pool), await _complete_with_old_evidence(pg_pool)]
    (first, _, first_booking), (second, _, second_booking) = sorted(stays, key=lambda stay: stay[0].org)
    await shorten_sweep_deadline(pg_pool, 'public.c24_hotel_retention_sweep()')
    await pg_pool.execute(f"""CREATE FUNCTION test_slow_hotel_target() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_sleep(2.1); RETURN NEW; END $$;
        CREATE TRIGGER test_slow_hotel_target BEFORE UPDATE ON booking_allocation_sets
        FOR EACH ROW WHEN (OLD.booking_id='{first_booking}'::uuid) EXECUTE FUNCTION test_slow_hotel_target();""")
    assert await asyncio.wait_for(run_c24_retention_sweep(retention_operator), timeout=5) == {
        'enabled': True, 'batch_clear': False,
    }
    assert await pg_pool.fetchval("SELECT state='cancelled' FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2", first.org, first_booking)
    assert await pg_pool.fetchval("SELECT state='active' FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2", second.org, second_booking)
    cursor = await pg_pool.fetchrow("SELECT phase,last_organization_id,last_record_id FROM c24_retention_sweep_cursor WHERE scan_name='hotel_erasure'")
    assert tuple(cursor) == (1, first.org, first_booking)

    await pg_pool.execute('DROP TRIGGER test_slow_hotel_target ON booking_allocation_sets')
    await run_c24_retention_sweep(retention_operator)
    assert not await pg_pool.fetchval("SELECT EXISTS(SELECT 1 FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2 AND state='active')", second.org, second_booking)


async def test_hotel_erasure_cursor_defers_a_retention_hold(pg_pool, retention_operator):
    from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence

    env, lineage, booking = await _complete_with_old_evidence(pg_pool)
    hold = uuid4()
    await pg_pool.execute("""INSERT INTO gdpr_retention_holds
        (organization_id,hold_id,category,scope,scope_id,reason_code,authority_reference,
         approved_by,approved_at,review_by)
        VALUES($1,$2,'booking_stay_content','subject',$3,'legal_review','case:synthetic','qa',
               clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day')""",
        env.org, hold, lineage.contact_id)

    # The hold prevents even attempting archival; occupancy and evidence remain.
    assert await run_c24_retention_sweep(retention_operator) == {
        "enabled": True, "batch_clear": True,
    }
    assert await pg_pool.fetchval(
        "SELECT state='active' FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2",
        env.org, booking,
    )
    assert await pg_pool.fetchval(
        "SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2",
        env.org, booking,
    ) == 1


async def test_sweep_clears_retained_content_of_eligible_soft_deleted_message(
    state_env, retention_operator,
):
    pool, org, source = state_env["pool"], state_env["org"], state_env["message"]
    await commit_source(state_env, source, "2026-11-12", {"service_id": "standard"}, 1700000000)
    await pool.execute(
        """UPDATE messages SET status='handled', handling_type='ai_handled', deleted_at=clock_timestamp()
           WHERE organization_id=$1 AND id=$2""",
        org, source,
    )
    await approve_messages(pool, org)
    await pool.execute(
        """UPDATE event_log SET testo_originale='synthetic text', risposta_ai='synthetic reply'
           WHERE organization_id=$1 AND source_id=$2""",
        org, source,
    )
    assert not await pool.fetchval("SELECT public.c24_purge_source($1,$2)", org, source)
    await age_message(pool, org, source, 91)
    hold_id = uuid4()
    await pool.execute(
        """INSERT INTO gdpr_retention_holds (organization_id,hold_id,category,scope,scope_id,
             reason_code,authority_reference,approved_by,approved_at,review_by)
           VALUES ($1,$2,'messages_content','record',$3,'litigation','test:soft-delete','qa',
             now()-interval '1 hour',now()+interval '1 day')""",
        org, hold_id, source,
    )
    assert not await pool.fetchval("SELECT public.c24_purge_source($1,$2)", org, source)
    await pool.execute(
        """INSERT INTO gdpr_retention_hold_events (organization_id,hold_id,event_id,kind,
             authority_reference,approved_by,occurred_at)
           VALUES ($1,$2,$3,'released','test:soft-delete','qa',clock_timestamp())""",
        org, hold_id, uuid4(),
    )
    assert await run_c24_retention_sweep(retention_operator) == {"enabled": True, "batch_clear": True}
    assert await pool.fetchval(
        """SELECT content_purged_at IS NOT NULL AND content='{}'::jsonb AND content_text IS NULL
                  AND ai_reply_cache IS NULL AND deleted_at IS NOT NULL
           FROM messages WHERE organization_id=$1 AND id=$2""",
        org, source,
    )
    assert await pool.fetchval(
        """SELECT testo_originale='' AND risposta_ai=''
           FROM event_log WHERE organization_id=$1 AND source_id=$2""",
        org, source,
    )
    assert not await pool.fetchval("SELECT public.c24_purge_source($1,$2)", org, source)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="cannot be restored"):
        await pool.execute(
            "UPDATE messages SET content_text='synthetic replay' WHERE organization_id=$1 AND id=$2",
            org, source,
        )


@pytest.mark.migration_limit(97)
async def test_suppression_retention_defers_sweep_and_explicit_targets(state_env, retention_operator):
    from src.core.gdpr.c24_retention_controller import run_c24_retention_targets

    env=state_env; pool=env['pool']; org=env['org']; conv=env['conv']
    contact=await pool.fetchval('SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2',org,conv)
    await pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
        retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'technical_receipts','test',true,90,false,'test:receipt','qa',
        clock_timestamp()-interval '1 minute',clock_timestamp()+interval '1 day')""",org)
    await pool.execute("""INSERT INTO c24_idle_watermarks(organization_id,conversation_id,subject_id,
        generation,cutoff,provider_timestamp,expired_at) VALUES($1,$2,$3,1,
        clock_timestamp()-interval '91 days',1700000000,clock_timestamp()-interval '91 days')""",org,conv,contact)
    audit=await pool.fetchval("""INSERT INTO c24_suppression_lifecycle_audit(organization_id,subject_id,
        conversation_id,record_kind,record_digest,action,occurred_at) VALUES($1,$2,$3,
        'idle_watermark',repeat('a',32),'retention',clock_timestamp()-interval '91 days') RETURNING audit_id""",org,contact,conv)
    target=await run_c24_retention_targets(retention_operator,suppression_targets=[(org,conv)],
        suppression_audit_targets=[(org,audit)])
    assert target['suppressions_cleaned']==0 and target['suppression_audit_rows_expired']==0
    assert await run_c24_retention_sweep(retention_operator)=={'enabled':True,'batch_clear':False}
    assert await pool.fetchval('SELECT EXISTS(SELECT 1 FROM c24_idle_watermarks WHERE organization_id=$1 AND conversation_id=$2)',org,conv)
    assert await pool.fetchval('SELECT EXISTS(SELECT 1 FROM c24_suppression_lifecycle_audit WHERE organization_id=$1 AND audit_id=$2)',org,audit)
    # More than a batch of distinct deferred audit rows must advance the cursor;
    # after wrap, another tenant's eligible work is reached.
    for n in range(125):
        await pool.execute("""INSERT INTO c24_suppression_lifecycle_audit(organization_id,subject_id,
            conversation_id,record_kind,record_digest,action,occurred_at) VALUES($1,$2,$3,
            'idle_watermark',md5($4::text),'retention',clock_timestamp()-interval '91 days')""",org,contact,conv,str(n))
    # Begin at the start of audit phase: 126 rows exceed a 100 target batch.
    await pool.execute("""UPDATE c24_retention_sweep_cursor SET phase=6,
        last_organization_id='00000000-0000-0000-0000-000000000000',
        last_record_id='00000000-0000-0000-0000-000000000000' WHERE scan_name='retention'""")
    assert await run_c24_retention_sweep(retention_operator)=={'enabled':True,'batch_clear':False}
    cursor=await pool.fetchrow("SELECT phase,last_organization_id,last_record_id FROM c24_retention_sweep_cursor WHERE scan_name='retention'")
    assert cursor['phase']==6 and cursor['last_organization_id']==org
    pending=await pool.fetchval("SELECT count(*) FROM c24_suppression_lifecycle_audit WHERE organization_id=$1 AND audit_id>$2",org,cursor['last_record_id'])
    assert pending==26
    other=(await _organization(pool))['id']
    await approve_messages(pool,other)
    inbox=await completed_inbox(pool,other)
    # Drain the remaining 26 records, then wrap back to earlier phases.
    assert await run_c24_retention_sweep(retention_operator)=={'enabled':True,'batch_clear':False}
    assert not await pool.fetchval("SELECT c24_payload_purged_at IS NOT NULL FROM meta_webhook_inbox WHERE organization_id=$1 AND id=$2",other,inbox)
    await run_c24_retention_sweep(retention_operator)
    assert await pool.fetchval("SELECT c24_payload_purged_at IS NOT NULL FROM meta_webhook_inbox WHERE organization_id=$1 AND id=$2",other,inbox)
    assert await pool.fetchval('SELECT count(*) FROM c24_suppression_lifecycle_audit WHERE organization_id=$1',org)==126


async def test_sweep_progresses_past_newly_due_inbox_and_deferred_suppression(
    state_env, retention_operator,
):
    env = state_env
    pool, org, source, conversation = (
        env["pool"], env["org"], env["message"], env["conv"],
    )
    incoming = (await _organization(pool))["id"]
    await approve_messages(pool, org)
    await approve_messages(pool, incoming, days=1)
    await commit_source(env, source, "2026-11-12", {"service_id": "standard"}, 1700000000)
    await apply(env, source)
    await pool.execute(
        "UPDATE messages SET status='handled', handling_type='ai_handled' WHERE organization_id=$1 AND id=$2",
        org, source,
    )
    await age_message(pool, org, source, 91)

    contact = await pool.fetchval(
        "SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2",
        org, conversation,
    )
    await pool.execute(
        """INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
          retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
          VALUES($1,'technical_receipts','test',true,90,false,'test:sweep','qa',
          clock_timestamp()-interval '1 minute',clock_timestamp()+interval '1 day')""", org,
    )
    await pool.execute(
        """INSERT INTO c24_suppression_lifecycle_audit(organization_id,subject_id,
           conversation_id,record_kind,record_digest,action,occurred_at)
           SELECT $1,$2,$3,'idle_watermark',md5(n::text),'retention',
             clock_timestamp()-interval '91 days' FROM generate_series(1,126) n""",
        org, contact, conversation,
    )
    # Simulate envelopes becoming due since the scan started yesterday.
    # The synthetic administrator controls time solely to avoid waiting a full
    # retention window. Restore the guard before the dedicated operator runs.
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """UPDATE c24_retention_sweep_cursor SET scan_cutoff_at=clock_timestamp()-interval '1 day'
               WHERE scan_name='retention'""",
        )
        await conn.execute("ALTER TABLE meta_webhook_inbox DISABLE TRIGGER c24_inbox_lifecycle_guard")
        await conn.execute(
            """INSERT INTO meta_webhook_inbox(id,organization_id,channel,event_key,payload,
              trace_id,status,c24_completed_at)
              SELECT gen_random_uuid(),$1,'whatsapp','sweep-progress-'||n,
               '{"synthetic":"content"}'::jsonb,'qa','completed',
               clock_timestamp()-CASE WHEN n<=101 THEN interval '3 days' ELSE interval '36 hours' END
              FROM generate_series(1,211) n""", incoming,
        )
        await conn.execute("ALTER TABLE meta_webhook_inbox ENABLE TRIGGER c24_inbox_lifecycle_guard")

    assert await run_c24_retention_sweep(retention_operator) == {
        "enabled": True, "batch_clear": False,
    }
    cursor_query = """
        SELECT phase,last_organization_id,last_record_id,scan_cutoff_at
        FROM c24_retention_sweep_cursor WHERE scan_name='retention'
    """
    cursor = await pool.fetchrow(cursor_query)
    assert cursor["phase"] == 1 and cursor["last_organization_id"] == incoming
    migration = (
        Path(__file__).resolve().parents[3]
        / "src/core/db/migrations/098_c24_retention_sweep_progress.sql"
    )
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(migration.read_text(encoding="utf-8"))
    assert await pool.fetchrow(cursor_query) == cursor
    assert await run_c24_retention_sweep(retention_operator) == {
        "enabled": True, "batch_clear": False,
    }
    # The second batch reaches the other tenant's message while 110 envelopes
    # that became due after the scan started wait for the next horizon.
    assert await pool.fetchval(
        "SELECT content_purged_at IS NOT NULL FROM messages WHERE organization_id=$1 AND id=$2",
        org, source,
    )
    assert await pool.fetchval(
        """SELECT count(*) FROM meta_webhook_inbox WHERE organization_id=$1
           AND c24_payload_purged_at IS NULL""", incoming,
    ) == 110
    assert await pool.fetchval(
        "SELECT count(*) FROM c24_suppression_lifecycle_audit WHERE organization_id=$1", org,
    ) == 126
    assert await run_c24_retention_sweep(retention_operator) == {
        "enabled": True, "batch_clear": False,
    }
    assert await pool.fetchval(
        """SELECT count(*) FROM meta_webhook_inbox WHERE organization_id=$1
           AND c24_payload_purged_at IS NULL""", incoming,
    ) == 10
    await run_c24_retention_sweep(retention_operator)
    assert not await pool.fetchval(
        """SELECT EXISTS(SELECT 1 FROM meta_webhook_inbox WHERE organization_id=$1
           AND c24_payload_purged_at IS NULL)""", incoming,
    )
