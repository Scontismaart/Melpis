"""Synthetic owner-only SQL lifecycle; external checkpoint authentication is tested separately."""
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from tests.core.gdpr.test_ordinary_booking_retention_plan import _candidate, _plan

pytestmark = pytest.mark.asyncio
_CHECKPOINT = 'a' * 64
_INVENTORY = 'b' * 64


async def _bind(conn, env, booking, plan, *, checkpoint=_CHECKPOINT, inventory=_INVENTORY):
    return await conn.fetchval('SELECT ordinary_bind_booking_retention_checkpoint($1,$2,$3::jsonb,$4,$5,$6)',
                               env.org, booking, json.dumps(plan), checkpoint, 1, inventory)


async def _purge(conn, env, booking, plan, *, checkpoint=_CHECKPOINT, inventory=_INVENTORY):
    return await conn.fetchval('SELECT ordinary_admin_purge_booking($1,$2,$3::jsonb,$4,$5,$6)',
                               env.org, booking, json.dumps(plan), checkpoint, 1, inventory)


async def test_ordinary_booking_purge_requires_exact_binding_and_preserves_accounting(pg_pool, other_org):
    env, booking, request_ids = await _candidate(pg_pool)
    plan = await _plan(pg_pool, env.org, booking)
    assert plan is not None
    ledger_ids = [uuid4(), uuid4()]
    for identity, org in zip(ledger_ids, (env.org, other_org['id'])):
        await pg_pool.execute('INSERT INTO usage_events(id,organization_id,event_type,metadata) VALUES($1,$2,$3,$4::jsonb)',
                              identity, org, 'test.booking', '{}')
    async with pg_pool.acquire() as conn, conn.transaction():
        assert not await _purge(conn, env, booking, plan)
        wrong = dict(plan, request_ids=plan['request_ids'][:-1])
        assert not await _bind(conn, env, booking, wrong)
        assert await _bind(conn, env, booking, plan)
        assert not await _purge(conn, env, booking, plan, inventory='c'*64)
        assert await _purge(conn, env, booking, plan)
    assert await pg_pool.fetchval('SELECT ordinary_verify_booking_retention_purge($1,$2,$3::jsonb)',
                                  env.org, booking, json.dumps(plan))
    fence = await pg_pool.fetchrow('SELECT purge_kind,policy_version,receipt_count FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    assert tuple(fence) == ('ordinary', 'v1', len(request_ids))
    actual_requests = await pg_pool.fetch('SELECT request_id FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    assert {row['request_id'] for row in actual_requests} == set(request_ids)
    assert await pg_pool.fetchval('SELECT count(*) FROM usage_events WHERE id=ANY($1::uuid[])', ledger_ids) == len(ledger_ids)
    async with pg_pool.acquire() as conn, conn.transaction():
        assert await _bind(conn, env, booking, plan)
        assert await _purge(conn, env, booking, plan)
    # Even owner-side accidental mutations cannot remove or alter ordinary
    # replay evidence. Hotel records retain the existing lifecycle contract.
    for statement, args in (
        ('UPDATE booking_stay_purge_fences SET purge_kind=\'stay\' WHERE organization_id=$1 AND booking_id=$2', (env.org, booking)),
        ('DELETE FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2', (env.org, booking)),
        ('UPDATE booking_stay_tombstones SET policy_version=\'changed\' WHERE organization_id=$1 AND booking_id=$2', (env.org, booking)),
        ('''INSERT INTO booking_stay_tombstones(organization_id,request_id,booking_id,original_subject_id,purpose,policy_version)
            VALUES($1,$2,$3,$4,'retention','v1')''', (env.org, uuid4(), booking, plan['subject_id'])),
        ('TRUNCATE booking_stay_tombstones', ()),
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await pg_pool.execute(statement, *args)
    await pg_pool.execute(Path('src/core/db/migrations/109_ordinary_booking_retention_purge.sql').read_text(encoding='utf-8'))
    assert await pg_pool.fetchval('SELECT ordinary_verify_booking_retention_purge($1,$2,$3::jsonb)',
                                  env.org, booking, json.dumps(plan))
    # The existing shared fence is authoritative for every booking writer.
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await pg_pool.execute('''INSERT INTO bookings(id,organization_id,contact_id,nome_cliente,telefono,data,ora,coperti,stato)
            VALUES($1,$2,$3,'Synthetic','','2024-01-10','10:00',1,'in_attesa')''', booking, env.org, plan['subject_id'])
    for role in ('anon','authenticated','service_role','melpis_c24_retention','melpis_c24_booking_worker'):
        assert not await pg_pool.fetchval("SELECT has_table_privilege($1,'ordinary_booking_purge_bindings','SELECT,INSERT,UPDATE,DELETE,TRUNCATE')", role)
        for fn in ('ordinary_bind_booking_retention_checkpoint(uuid,uuid,jsonb,text,bigint,text)',
                   'ordinary_admin_purge_booking(uuid,uuid,jsonb,text,bigint,text)',
                   'ordinary_verify_booking_retention_purge(uuid,uuid,jsonb)'):
            assert not await pg_pool.fetchval('SELECT has_function_privilege($1,$2,$3)', role, fn, 'EXECUTE')


async def test_unexecuted_binding_cannot_commit_or_be_reused(pg_pool):
    env, booking, _ = await _candidate(pg_pool)
    plan = await _plan(pg_pool, env.org, booking)
    with pytest.raises((asyncpg.ObjectNotInPrerequisiteStateError, asyncpg.ForeignKeyViolationError)):
        async with pg_pool.acquire() as conn, conn.transaction():
            assert await _bind(conn, env, booking, plan)
    assert await pg_pool.fetchval('SELECT count(*) FROM ordinary_booking_purge_bindings WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    assert await _plan(pg_pool, env.org, booking) == plan
    async with pg_pool.acquire() as conn, conn.transaction():
        assert not await _purge(conn, env, booking, plan)


async def test_conversation_binding_requires_exact_conversation_and_keeps_conversation(pg_pool):
    env, booking, requests = await _candidate(pg_pool, conversation_linked=True)
    plan = await _plan(pg_pool, env.org, booking)
    conversation = plan['conversation_id']
    sibling = uuid4()
    async with pg_pool.acquire() as conn, conn.transaction():
        assert not await _bind(conn, env, booking, dict(plan, conversation_id=str(sibling)))
        old_kind = {key: value for key, value in plan.items() if key != 'conversation_id'}
        old_kind['kind'] = 'ordinary_managed_booking_candidate_v1'
        assert not await _bind(conn, env, booking, old_kind)
        assert await _bind(conn, env, booking, plan)
        assert await _purge(conn, env, booking, plan)
    assert await pg_pool.fetchval('SELECT ordinary_verify_booking_retention_purge($1,$2,$3::jsonb)',
                                  env.org, booking, json.dumps(plan))
    assert await pg_pool.fetchval('SELECT count(*) FROM conversations WHERE organization_id=$1 AND id=$2',
                                  env.org, conversation) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2',
                                  env.org, booking) == len(requests)
    async with pg_pool.acquire() as conn, conn.transaction():
        assert await _bind(conn, env, booking, plan)
        assert await _purge(conn, env, booking, plan)
        assert not await _bind(conn, env, booking, dict(plan, conversation_id=str(sibling)))


async def test_new_dependency_after_binding_rolls_back_the_transaction(pg_pool):
    env, booking, _ = await _candidate(pg_pool)
    plan = await _plan(pg_pool, env.org, booking)
    with pytest.raises((asyncpg.ObjectNotInPrerequisiteStateError, asyncpg.ForeignKeyViolationError)):
        async with pg_pool.acquire() as conn, conn.transaction():
            assert await _bind(conn, env, booking, plan)
            await conn.execute('UPDATE bookings SET deposit_amount_minor=100 WHERE organization_id=$1 AND id=$2', env.org, booking)
            assert not await _purge(conn, env, booking, plan)
    assert await _plan(pg_pool, env.org, booking) == plan


async def test_binding_cannot_commit_with_fence_but_without_cleanup(pg_pool):
    env, booking, _ = await _candidate(pg_pool)
    plan = await _plan(pg_pool, env.org, booking)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        async with pg_pool.acquire() as conn, conn.transaction():
            assert await _bind(conn, env, booking, plan)
            await conn.execute('''INSERT INTO booking_stay_purge_fences(organization_id,booking_id,original_subject_id,
                purpose,policy_version,receipt_count,purge_kind) VALUES($1,$2,$3,'retention',$4,$5,'ordinary')''',
                env.org, booking, plan['subject_id'], plan['policy_version'], len(plan['request_ids']))
    assert await _plan(pg_pool, env.org, booking) == plan
