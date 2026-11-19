"""Replay safeguards for linked copies of a purged synthetic source."""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from tests.core.bookings.test_allocation_engine_postgres import wait_blocked
from tests.core.bookings.test_conversation_state_postgres import (
    apply,
    commit_source,
    second_source,
)
from tests.core.gdpr.test_c24_idle_expiry import age_message
from tests.core.gdpr.test_c24_retention_sweep import approve_messages
from tests.core.gdpr.test_conversation_lifecycle_c24 import (
    subject,
)

pytest_plugins = (
    "tests.core.bookings.test_conversation_state_postgres",
)


@pytest.mark.asyncio
async def test_purged_unapplied_source_cannot_acquire_new_linked_copies(state_env):
    env = state_env
    pool, org, conversation, source = (env[key] for key in ("pool", "org", "conv", "message"))
    await commit_source(env, source, "10:00", {"appointment_time": "10:00"}, 1700000000)
    fresh = await second_source(env, 1700000001)
    await apply(env, fresh)
    await pool.execute(
        "UPDATE messages SET status='handled',handling_type='ai' WHERE organization_id=$1 AND id IN ($2,$3)",
        org, source, fresh,
    )
    await approve_messages(pool, org)
    await age_message(pool, org, source, 91)
    person = await subject(env)
    insert = """INSERT INTO simulation_requests(organization_id,auth_user_id,request_id,payload_hash,
                  status,response,completed_at,c24_conversation_id,c24_source_message_id,c24_subject_id)
               VALUES($1,$2,$3,repeat('0',64),'completed','{"risposta":"synthetic"}',now(),$4,$5,$6)"""
    writer = None
    try:
        async with pool.acquire() as holder, holder.transaction():
            assert await holder.fetchval("SELECT c24_purge_source($1,$2)", org, source)
            writer = asyncio.create_task(pool.execute(insert, org, env["auth"], uuid4(), conversation, source, person))
            await wait_blocked(pool, holder.get_server_pid())
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="Purged canonical response"):
            await asyncio.wait_for(writer, 5)
    finally:
        if writer is not None and not writer.done():
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
    assert not await pool.fetchval(
        "SELECT EXISTS(SELECT FROM c24_source_fences WHERE organization_id=$1 AND source_message_id=$2)",
        org, source,
    )
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="Purged source cannot conclude"):
        await pool.execute(
            """INSERT INTO booking_conversation_requests(organization_id,request_id,conversation_id,
                  source_message_id,actor_user_id,action,payload_hash,applied_revision,canonical_response_hash)
               VALUES($1,$2,$3,$4,$5,'proposal',repeat('0',64),2,repeat('0',64))""",
            org, uuid4(), conversation, source, env["actor"],
        )
    assert await pool.fetchval(
        """SELECT response='{}'::jsonb FROM simulation_requests
           WHERE organization_id=$1 AND c24_source_message_id=$2""",
        org, source,
    )
    assert not await pool.fetchval(
        "SELECT EXISTS(SELECT FROM booking_conversation_requests WHERE organization_id=$1 AND source_message_id=$2)",
        org, source,
    )
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="delivery identity is immutable"):
        await pool.execute(
            "UPDATE messages SET wam_id=NULL WHERE organization_id=$1 AND id=$2",
            org, source,
        )

    other_subject = uuid4()
    await pool.execute(
        "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
        other_subject, org, "+" + str(other_subject.int)[:12],
    )
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="subject lineage unverified"):
        await pool.execute(insert, org, env["auth"], uuid4(), conversation, fresh, other_subject)
