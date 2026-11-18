"""Synthetic PostgreSQL regressions for exact C2.4 fence provenance."""

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from tests.core.bookings.test_conversation_state_postgres import apply, commit_source, state_env


async def _apply_receipt(env):
    request = uuid4()
    source = env["message"]
    await commit_source(env, source, "2026-11-12", {"service_id": "standard"}, 1700000000)
    await apply(env, source, request=request)
    return request


async def _receipt_clock(pool, org, request):
    return await pool.fetchval(
        """
        SELECT c24_concluded_at FROM booking_conversation_requests
        WHERE organization_id=$1 AND request_id=$2
        """,
        org, request,
    )


async def _fence_clock(pool, org, conversation):
    return await pool.fetchval(
        """
        SELECT c24_concluded_at FROM c24_source_fences
        WHERE organization_id=$1 AND conversation_id=$2
        """,
        org, conversation,
    )


@pytest.mark.asyncio
@pytest.mark.migration_limit(97)
@pytest.mark.parametrize("history", ["exact", "unrecorded", "unmatched_source", "unmatched_request", "unmatched_tenant"])
async def test_migration_copies_only_verified_historical_conclusion(state_env, other_org, history):
    env = state_env
    pool, org, conversation = env["pool"], env["org"], env["conv"]
    request = await _apply_receipt(env)
    original_conclusion = await _receipt_clock(pool, org, request)
    assert original_conclusion is not None
    if history == "unrecorded":
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("ALTER TABLE booking_conversation_requests DISABLE TRIGGER conversation_immutable")
            await conn.execute(
                """UPDATE booking_conversation_requests SET c24_concluded_at=NULL
                   WHERE organization_id=$1 AND request_id=$2""",
                org, request,
            )
            await conn.execute("ALTER TABLE booking_conversation_requests ENABLE TRIGGER conversation_immutable")
    if history in {"unmatched_source", "unmatched_request"}:
        field = {"unmatched_source": "source_message_id", "unmatched_request": "request_id"}[history]
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("ALTER TABLE c24_source_fences DISABLE TRIGGER c24_immutable")
            await conn.execute(
                f"UPDATE c24_source_fences SET {field}=$3 WHERE organization_id=$1 AND request_id=$2",
                org, request, uuid4(),
            )
            await conn.execute("ALTER TABLE c24_source_fences ENABLE TRIGGER c24_immutable")

    if history == "unmatched_tenant":
        tenant, target_conversation, target_subject = other_org["id"], uuid4(), uuid4()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO contacts (organization_id,id,phone_number) VALUES ($1,$2,$3)",
                tenant, target_subject, f"synthetic-{target_subject}",
            )
            await conn.execute(
                "INSERT INTO conversations (organization_id,id,contact_id) VALUES ($1,$2,$3)",
                tenant, target_conversation, target_subject,
            )
            await conn.execute("ALTER TABLE c24_source_fences DISABLE TRIGGER c24_immutable")
            await conn.execute(
                """UPDATE c24_source_fences SET organization_id=$3, conversation_id=$4, subject_id=$5
                   WHERE organization_id=$1 AND request_id=$2""",
                org, request, tenant, target_conversation, target_subject,
            )
            await conn.execute("ALTER TABLE c24_source_fences ENABLE TRIGGER c24_immutable")
        org, conversation = tenant, target_conversation

    migration = Path("src/core/db/migrations/100_c24_source_fence_conclusion.sql").read_text(encoding="utf-8")
    await pool.execute(migration)
    expected = original_conclusion if history == "exact" else None
    assert await pool.fetchval("SELECT count(*) FROM c24_source_fences WHERE organization_id=$1", org) == 1
    assert await _fence_clock(pool, org, conversation) == expected


@pytest.mark.asyncio
async def test_future_request_fence_copies_server_conclusion_and_preserves_gate(state_env):
    env = state_env
    pool, org, conversation = env["pool"], env["org"], env["conv"]
    request = await _apply_receipt(env)

    conclusion = await _receipt_clock(pool, org, request)
    assert conclusion is not None
    assert await _fence_clock(pool, org, conversation) == conclusion
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="immutable"):
        await pool.execute(
            "UPDATE c24_source_fences SET c24_concluded_at=NULL WHERE organization_id=$1 AND request_id=$2",
            org, request,
        )
    assert not await pool.fetchval("SELECT c24_cleanup_suppression($1,$2)", org, conversation)
    assert await _fence_clock(pool, org, conversation) == conclusion
