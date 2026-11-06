"""Tests for the explicitly enabled owner lifecycle controller."""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.gdpr.c24_retention_controller import run_c24_retention_targets


def pool_with_results(results):
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=results)
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


@pytest.mark.asyncio
async def test_controller_is_disabled_without_explicit_owner_cutover(monkeypatch):
    monkeypatch.delenv("GDPR_C24_OWNER_RETENTION_ENABLED", raising=False)
    pool, _ = pool_with_results([])
    assert await run_c24_retention_targets(pool, message_targets=[(uuid4(), uuid4())]) == {
        "messages_purged": 0, "conversations_expired": 0, "receipts_cleaned": 0,
    }
    pool.acquire.assert_not_called()


@pytest.mark.asyncio
async def test_controller_delegates_targeted_mutations_to_owner_functions(monkeypatch):
    monkeypatch.setenv("GDPR_C24_OWNER_RETENTION_ENABLED", "true")
    pool, conn = pool_with_results([True, True, True, False, True, True])
    org, message, conversation, receipt = (uuid4() for _ in range(4))
    assert await run_c24_retention_targets(pool, message_targets=[(org, message)],
                                           conversation_targets=[(org, conversation)],
                                           receipt_targets=[(org, receipt)]) == {
        "messages_purged": 1, "conversations_expired": 0, "receipts_cleaned": 1,
    }
    for index, kind, record in [(1, "message", message), (3, "conversation", conversation), (5, "receipt", receipt)]:
        assert conn.fetchval.await_args_list[index].args == (
            "SELECT public.c24_run_retention_target($1::text,$2::uuid,$3::uuid)", kind, org, record)
    assert all(conn.fetchval.await_args_list[index].args == ("SELECT public.c24_retention_operator_allowed()",)
               for index in (0, 2, 4))
    assert conn.transaction.call_count == 3
    conn.transaction.assert_called_with(isolation="read_committed")


@pytest.mark.asyncio
async def test_controller_bounds_targets_before_any_mutation(monkeypatch):
    monkeypatch.setenv("GDPR_C24_OWNER_RETENTION_ENABLED", "true")
    pool, _ = pool_with_results([])
    with pytest.raises(ValueError, match="target_limit"):
        await run_c24_retention_targets(pool, message_targets=[(uuid4(), uuid4())] * 500, receipt_targets=[(uuid4(), uuid4())])
    pool.acquire.assert_not_called()


def test_manifest_validates_complete_batch_before_execution(tmp_path):
    import json

    from src.core.gdpr.c24_retention_controller import read_target_manifest

    path = tmp_path / "targets.json"
    org, receipt = uuid4(), uuid4()
    path.write_text(json.dumps({"receipt_targets": [[str(org), str(receipt)]]}))
    assert read_target_manifest(path) == {
        "message_targets": [], "conversation_targets": [], "receipt_targets": [(org, receipt)],
    }
    path.write_text(json.dumps({"message_targets": [[str(org), str(receipt)]],
                                "receipt_targets": [[str(org), "invalid"]]}))
    with pytest.raises(ValueError):
        read_target_manifest(path)


@pytest.mark.asyncio
async def test_manifest_runner_disabled_before_opening_file_or_connecting(monkeypatch, tmp_path):
    from src.core.gdpr.c24_retention_controller import _run_manifest

    monkeypatch.delenv("GDPR_C24_OWNER_RETENTION_ENABLED", raising=False)
    with pytest.raises(SystemExit, match="disabled"):
        await _run_manifest(tmp_path / "missing.json")


@pytest.mark.asyncio
async def test_controller_rejects_broad_credentials(monkeypatch):
    monkeypatch.setenv("GDPR_C24_OWNER_RETENTION_ENABLED", "true")
    pool, conn = pool_with_results([False])
    with pytest.raises(PermissionError, match="operator_required"):
        await run_c24_retention_targets(pool, message_targets=[(uuid4(), uuid4())])
    assert conn.fetchval.await_count == 1

@pytest.mark.asyncio
async def test_retention_operator_database_role_and_dispatch(pg_pool, other_org):
    """Exercise real grants, role checks and tenant-bound no-op dispatch."""
    import asyncpg

    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            assert not await conn.fetchval("SELECT public.c24_retention_operator_allowed()")
            await conn.execute("GRANT melpis_c24_retention TO CURRENT_USER")
            await conn.execute("SET LOCAL ROLE melpis_c24_retention")
            assert await conn.fetchval("SELECT public.c24_retention_operator_allowed()")
            assert not await conn.fetchval(
                "SELECT has_table_privilege(current_user, 'public.messages', 'SELECT')")
            for kind in ("message", "conversation", "receipt"):
                assert not await conn.fetchval(
                    "SELECT public.c24_run_retention_target($1,$2,$3)",
                    kind, other_org["id"], uuid4())
            await conn.execute("RESET ROLE")
            await conn.execute("SET LOCAL ROLE service_role")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.fetchval("SELECT public.c24_retention_operator_allowed()")

from tests.core.bookings.test_conversation_state_postgres import state_env, commit_source, apply
from tests.core.gdpr.test_conversation_lifecycle_c24 import approve_conversation
from tests.core.gdpr.test_c24_idle_expiry import age_state


@pytest.mark.asyncio
async def test_operator_dispatches_eligible_targets(state_env, other_org):
    env = state_env
    pool = env['pool']
    request = uuid4()
    await commit_source(env, env['message'], '2026-11-12', {'service_id': 'standard'}, 1700000000)
    await apply(env, env['message'], request=request)
    await approve_conversation(pool, env['org'])
    await age_state(env, 31)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE booking_conversation_requests DISABLE TRIGGER conversation_immutable')
        await conn.execute("UPDATE booking_conversation_requests SET c24_concluded_at=clock_timestamp()-interval '91 days' WHERE organization_id=$1 AND request_id=$2", env['org'], request)
        await conn.execute('ALTER TABLE booking_conversation_requests ENABLE TRIGGER conversation_immutable')
    await pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
        retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'technical_receipts','test',true,90,false,'test:receipt','qa',
        clock_timestamp()-interval '1 minute',clock_timestamp()+interval '1 day')""", env['org'])
    await pool.execute("""UPDATE messages SET status='handled',handling_type='ai',
        created_at=clock_timestamp()-interval '91 days' WHERE organization_id=$1 AND id=$2""",
        env['org'], env['message'])
    await pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
        retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'messages_content','test',true,90,false,'test:source','qa',
        clock_timestamp()-interval '1 minute',clock_timestamp()+interval '1 day')""", env['org'])
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('GRANT melpis_c24_retention TO CURRENT_USER')
        await conn.execute('SET LOCAL ROLE melpis_c24_retention')
        targets = [('conversation', env['conv']), ('receipt', request), ('message', env['message'])]
        for kind, target in targets:
            assert not await conn.fetchval('SELECT public.c24_run_retention_target($1,$2,$3)', kind, other_org['id'], target)
            assert await conn.fetchval('SELECT public.c24_run_retention_target($1,$2,$3)', kind, env['org'], target)
            assert not await conn.fetchval('SELECT public.c24_run_retention_target($1,$2,$3)', kind, env['org'], target)
    message = await pool.fetchrow(
        "SELECT content_purged_at,content_text,ai_reply_cache,billed_at FROM messages WHERE organization_id=$1 AND id=$2",
        env['org'], env['message'])
    assert message['content_purged_at'] and message['billed_at']
    assert message['content_text'] is None and message['ai_reply_cache'] is None
