"""Synthetic, read-only orphan proposal evidence; no purge or quota mutation."""
import json
from uuid import uuid4

import pytest

from src.core.gdpr.orphan_proposal import plan_orphan_proposal_expiry
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from tests.core.bookings.test_conversation_state_postgres import state_env, commit_source, apply, second_source
from tests.core.gdpr.test_c24_idle_expiry import age_message
from tests.core.gdpr.test_conversation_lifecycle_c24 import approve_conversation
from tests.core.test_quota_reservation_lifecycle import _used

pytestmark = pytest.mark.asyncio


async def _known_orphan(env):
    operation = await commit_source(env, env['message'], '2026-11-12', {'service_id':'standard'}, 1700000000)
    await env['pool'].execute("UPDATE messages SET status='handled',handling_type='ai_handled',replied_at=clock_timestamp() WHERE organization_id=$1 AND id=$2",
                              env['org'], env['message'])
    await approve_conversation(env['pool'], env['org'])
    await age_message(env['pool'], env['org'], env['message'], 31)
    return operation


async def _plan(env, org=None):
    async with env['pool'].acquire() as conn, conn.transaction(readonly=True, isolation='read_committed'):
        return await plan_orphan_proposal_expiry(conn, org or env['org'], env['message'])


async def test_known_orphan_proposal_plan_preserves_source_and_charge(state_env, other_org):
    env = state_env
    operation = await _known_orphan(env)
    used = await _used(env['pool'], env['org'])
    plan = await _plan(env)
    assert plan['kind'] == 'known_source_orphan_proposal_candidate_v1'
    assert plan['organization_id'] == str(env['org'])
    assert plan['conversation_id'] == str(env['conv'])
    assert plan['source_message_id'] == str(env['message'])
    assert plan['quota_operation_id'] == str(operation.id)
    assert plan['policy_version'] == 'synthetic-c24'
    assert plan['deletion_authorized'] is False
    assert 'response' not in plan and 'proposal' not in plan
    assert await _plan(env) == plan
    assert await _plan(env, other_org['id']) is None
    assert await env['pool'].fetchval("SELECT ai_reply_cache ? 'booking_state_proposal' FROM messages WHERE organization_id=$1 AND id=$2", env['org'], env['message'])
    assert await env['pool'].fetchval("SELECT response ? 'booking_state_proposal' FROM simulation_requests WHERE organization_id=$1 AND request_id=$2", env['org'], operation.id)
    assert await _used(env['pool'], env['org']) == used
    async with env['pool'].acquire() as conn, conn.transaction():
        with pytest.raises(RestoreBlocked, match='read-only'):
            await plan_orphan_proposal_expiry(conn, env['org'], env['message'])


@pytest.mark.parametrize('defect', ['unknown_received', 'unbilled', 'hash_mismatch', 'newer_activity', 'held'])
async def test_unverified_or_held_orphan_is_not_a_candidate(state_env, defect):
    env = state_env
    operation = await _known_orphan(env)
    pool, org, message = env['pool'], env['org'], env['message']
    if defect == 'unknown_received':
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute('ALTER TABLE messages DISABLE TRIGGER c24_source_received')
            await conn.execute('UPDATE messages SET c24_received_at=NULL WHERE organization_id=$1 AND id=$2', org, message)
            await conn.execute('ALTER TABLE messages ENABLE TRIGGER c24_source_received')
    elif defect == 'unbilled':
        await pool.execute('UPDATE messages SET billed_at=NULL WHERE organization_id=$1 AND id=$2', org, message)
    elif defect == 'hash_mismatch':
        await pool.execute("UPDATE messages SET ai_reply_cache=jsonb_set(ai_reply_cache,'{booking_state_proposal,intent}', '\"cancel\"'::jsonb) WHERE organization_id=$1 AND id=$2", org, message)
    elif defect == 'newer_activity':
        await second_source(env, 1700000001)
    elif defect == 'held':
        await pool.execute('''INSERT INTO gdpr_retention_holds(organization_id,hold_id,category,scope,scope_id,
            reason_code,authority_reference,approved_by,approved_at,review_by)
          VALUES($1,$2,'booking_conversation_content','record',$3,'litigation','test:orphan','qa',
              clock_timestamp(),clock_timestamp()+interval '1 day')''', org, uuid4(), operation.id)
    assert await _plan(env) is None
    assert await pool.fetchval('SELECT count(*) FROM usage_events WHERE organization_id=$1 AND id=$2', org, operation.id) == 1


async def test_existing_applied_state_is_not_orphan(state_env):
    env = state_env
    await commit_source(env, env['message'], '2026-11-12', {'service_id':'standard'}, 1700000000)
    await apply(env, env['message'])
    await env['pool'].execute("UPDATE messages SET status='handled',handling_type='ai_handled',replied_at=clock_timestamp() WHERE organization_id=$1 AND id=$2",
                              env['org'], env['message'])
    await approve_conversation(env['pool'], env['org'])
    await age_message(env['pool'], env['org'], env['message'], 31)
    assert await _plan(env) is None
