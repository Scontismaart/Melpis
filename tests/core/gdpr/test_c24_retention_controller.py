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
    pool, conn = pool_with_results([True, False, True])
    org, message, conversation, receipt = (uuid4() for _ in range(4))
    assert await run_c24_retention_targets(pool, message_targets=[(org, message)],
                                           conversation_targets=[(org, conversation)],
                                           receipt_targets=[(org, receipt)]) == {
        "messages_purged": 1, "conversations_expired": 0, "receipts_cleaned": 1,
    }
    assert conn.fetchval.await_args_list[0].args == (
        "SELECT public.c24_purge_source($1::uuid,$2::uuid)", org, message)
    assert conn.fetchval.await_args_list[1].args == (
        "SELECT public.c24_expire_idle($1::uuid,$2::uuid)", org, conversation)
    assert conn.fetchval.await_args_list[2].args == (
        "SELECT public.c24_cleanup_receipt($1::uuid,$2::uuid)", org, receipt)
    assert conn.transaction.call_count == 3
    conn.transaction.assert_called_with(isolation="read_committed")


@pytest.mark.asyncio
async def test_controller_bounds_targets_before_any_mutation(monkeypatch):
    monkeypatch.setenv("GDPR_C24_OWNER_RETENTION_ENABLED", "true")
    pool, _ = pool_with_results([])
    with pytest.raises(ValueError, match="target_limit"):
        await run_c24_retention_targets(pool, message_targets=[(uuid4(), uuid4())] * 500, receipt_targets=[(uuid4(), uuid4())])
    pool.acquire.assert_not_called()
