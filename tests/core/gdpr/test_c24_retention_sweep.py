"""Bounded automated retention on a disposable PostgreSQL database."""
from uuid import uuid4

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
