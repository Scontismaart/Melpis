"""Replay safeguards for linked copies of a purged synthetic source."""

import asyncio
import hashlib
import hmac
import json
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import asyncpg
import pytest
from cryptography.fernet import Fernet

from src.core.gdpr.c24_retention_controller import run_c24_retention_sweep
from src.core.gdpr.restore_checkpoint import (
    RestoreBlocked,
    bind_retained_receipt_prune_checkpoint,
    retain_receipt_prune_checkpoint,
)
from tests.core.bookings.test_allocation_engine_postgres import wait_blocked
from tests.core.bookings.test_conversation_state_postgres import (
    apply,
    commit_source,
    second_source,
)
from tests.core.gdpr.test_c24_idle_expiry import age_message
from tests.core.gdpr.test_c24_retention_sweep import approve_messages
from tests.core.gdpr.test_conversation_lifecycle_c24 import (
    approve_conversation,
    approve_technical_receipts,
    subject,
)

pytest_plugins = (
    "tests.core.bookings.test_conversation_state_postgres",
    "tests.core.gdpr.test_c24_retention_sweep",
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


async def age_receipt(pool, org, request):
    async with pool.acquire() as conn, conn.transaction():
        # Model an authentic older conclusion in the synthetic pre-upgrade DB.
        await conn.execute("ALTER TABLE booking_conversation_requests DISABLE TRIGGER conversation_immutable")
        conclusion = await conn.fetchval(
            """UPDATE booking_conversation_requests SET c24_concluded_at=clock_timestamp()-interval '91 days'
               WHERE organization_id=$1 AND request_id=$2 RETURNING c24_concluded_at""",
            org, request,
        )
        await conn.execute("ALTER TABLE booking_conversation_requests ENABLE TRIGGER conversation_immutable")
    return conclusion


async def historical_fence(env, *, age_source=True):
    pool, org, source = (env[key] for key in ("pool", "org", "message"))
    request = uuid4()
    await commit_source(env, source, "10:00", {"appointment_time": "10:00"}, 1700000000)
    await apply(env, source, request=request)
    conclusion = await age_receipt(pool, org, request)
    await pool.execute(
        "UPDATE messages SET status='handled',handling_type='ai' WHERE organization_id=$1 AND id=$2",
        org, source,
    )
    if age_source:
        await age_message(pool, org, source, 91)
    return request, conclusion


async def upgrade_fence_retention(pool):
    directory = Path("src/core/db/migrations")
    for version in (98, 100, 101, 102, 103, 104):
        migration, = directory.glob(f"{version:03d}_*.sql")
        await pool.execute(migration.read_text(encoding="utf-8"))


def signed_prune_checkpoint(org, prunes, *, key=None, sequence=1):
    key = key or secrets.token_bytes(32)
    fields = ("request_id", "source_message_id", "conversation_id", "subject_id")
    payload = {
        "version": "c24-restore-v3", "organization_id": str(org), "backup_id": "synthetic-retention-backup",
        "sequence": sequence, "complete": True, "erasures": [], "stay_purges": [],
        "receipt_prunes": [dict(zip(fields, map(str, prune), strict=True)) for prune in prunes],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    checkpoint = {"payload": payload, "signature": hmac.new(key, canonical, hashlib.sha256).hexdigest()}
    return checkpoint, key, hashlib.sha256(canonical).hexdigest()


async def bind_prunes(pool, org, checkpoint, key, digest):
    with TemporaryDirectory(prefix="synthetic-receipt-checkpoint-") as directory:
        path = Path(directory).resolve() / "checkpoint.enc"
        encryption_key = Fernet.generate_key()
        trust = dict(verification_key=key, backup_id="synthetic-retention-backup",
                     minimum_sequence=1, checkpoint_sha256=digest, encryption_key=encryption_key)
        retain_receipt_prune_checkpoint(path, org, checkpoint, **trust)
        async with pool.acquire() as conn, conn.transaction():
            return await bind_retained_receipt_prune_checkpoint(conn, org, path, **trust)


@pytest.mark.asyncio
@pytest.mark.migration_limit(97)
async def test_verified_fence_and_derived_audit_follow_receipt_deadline(state_env):
    env = state_env
    pool, org, conversation, source = (env[key] for key in ("pool", "org", "conv", "message"))
    request, conclusion = await historical_fence(env)
    await upgrade_fence_retention(pool)
    await approve_messages(pool, org)
    await approve_technical_receipts(pool, org)
    cleanup = "SELECT c24_cleanup_suppression($1,$2)"
    assert not await pool.fetchval(cleanup, org, conversation)
    assert await pool.fetchval("SELECT c24_cleanup_receipt($1,$2)", org, request)
    assert not await pool.fetchval(cleanup, org, conversation)
    assert await pool.fetchval("SELECT c24_purge_source($1,$2)", org, source)
    assert not await pool.fetchval(cleanup, org, conversation)
    person = await subject(env)
    checkpoint, key, digest = signed_prune_checkpoint(org, [(request, source, conversation, person)])
    with pytest.raises(RestoreBlocked, match="Authenticated external"):
        await bind_prunes(pool, org, checkpoint, key, "0" * 64)
    assert await bind_prunes(pool, org, checkpoint, key, digest) == 1
    assert await bind_prunes(pool, org, checkpoint, key, digest) == 1
    omitted = signed_prune_checkpoint(org, [], key=key, sequence=2)
    with pytest.raises(RestoreBlocked, match="omits prior commitments"):
        await bind_prunes(pool, org, *omitted)

    async def hold_source(hold_id):
        await pool.execute(
            """INSERT INTO gdpr_retention_holds(organization_id,hold_id,category,scope,scope_id,reason_code,
                  authority_reference,approved_by,approved_at,review_by)
               VALUES($1,$2,'technical_receipts','record',$3,'litigation','test:expiry','qa',
                  clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day')""",
            org, hold_id, source,
        )

    async def release(hold_id):
        await pool.execute(
            """INSERT INTO gdpr_retention_hold_events(organization_id,hold_id,event_id,kind,
                  authority_reference,approved_by,occurred_at)
               VALUES($1,$2,$3,'released','test:expiry','qa',clock_timestamp())""",
            org, hold_id, uuid4(),
        )

    hold = uuid4()
    await hold_source(hold)
    assert not await pool.fetchval(cleanup, org, conversation)
    await release(hold)
    assert await pool.fetchval(cleanup, org, conversation)
    assert not await pool.fetchval(cleanup, org, conversation)
    audit_id, audit_conclusion, occurred_at = await pool.fetchrow(
        """SELECT audit_id,c24_concluded_at,occurred_at FROM c24_suppression_lifecycle_audit
           WHERE organization_id=$1 AND source_record_id=$2""",
        org, request,
    )
    assert audit_conclusion == conclusion and occurred_at > conclusion
    audit_cleanup = "SELECT c24_cleanup_suppression_audit($1,$2)"
    audit_hold = uuid4()
    await hold_source(audit_hold)
    assert not await pool.fetchval(audit_cleanup, org, audit_id)
    await release(audit_hold)
    assert await pool.fetchval(audit_cleanup, org, audit_id)
    assert not await pool.fetchval(audit_cleanup, org, audit_id)

    await approve_conversation(pool, org)
    assert await pool.fetchval("SELECT c24_expire_idle($1,$2)", org, conversation)
    timestamp = await pool.fetchval("SELECT floor(extract(epoch FROM clock_timestamp()))::bigint")
    new_source = await second_source(env, timestamp)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="Retired request identity"):
        await apply(env, new_source, request=request)
    fresh_request = uuid4()
    assert (await apply(env, new_source, request=fresh_request))["applied_revision"] == 1


@pytest.mark.asyncio
@pytest.mark.migration_limit(97)
@pytest.mark.parametrize("held_sibling", [False, True])
async def test_sweep_reaches_verified_fence_and_its_audit(state_env, retention_operator, held_sibling):
    env = state_env
    pool, org, source = (env[key] for key in ("pool", "org", "message"))
    request, _ = await historical_fence(env, age_source=not held_sibling)
    if held_sibling:
        other_source = await second_source(env, 1700000001)
        other_request = uuid4()
        await apply(env, other_source, request=other_request, revision=1)
        await age_receipt(pool, org, other_request)
        await pool.execute(
            "UPDATE messages SET status='handled',handling_type='ai' WHERE organization_id=$1 AND id=$2",
            org, other_source,
        )
        await age_message(pool, org, other_source, 91)
        await age_message(pool, org, source, 91)
    await upgrade_fence_retention(pool)
    await approve_messages(pool, org)
    await approve_technical_receipts(pool, org)
    if held_sibling:
        await pool.execute(
            """INSERT INTO gdpr_retention_holds(organization_id,hold_id,category,scope,scope_id,reason_code,
                  authority_reference,approved_by,approved_at,review_by)
               VALUES($1,$2,'technical_receipts','record',$3,'litigation','test:expiry','qa',
                  clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day')""",
            org, uuid4(), other_source,
        )
    assert await run_c24_retention_sweep(retention_operator) == {"enabled": True, "batch_clear": False}
    person = await subject(env)
    tuples = [(request, source, env["conv"], person)]
    if held_sibling:
        tuples.append((other_request, other_source, env["conv"], person))
    checkpoint = signed_prune_checkpoint(org, tuples)
    assert await bind_prunes(pool, org, *checkpoint) == len(tuples)
    assert await run_c24_retention_sweep(retention_operator) == {"enabled": True, "batch_clear": not held_sibling}
    assert await run_c24_retention_sweep(retention_operator) == {"enabled": True, "batch_clear": True}
    assert await pool.fetchval(
        "SELECT content_purged_at IS NOT NULL FROM messages WHERE organization_id=$1 AND id=$2", org, source,
    )
    assert await pool.fetchval(
        "SELECT EXISTS(SELECT FROM c24_receipt_cleanup_audit WHERE organization_id=$1 AND request_id=$2)",
        org, request,
    )
    if held_sibling:
        assert await pool.fetchval(
            "SELECT EXISTS(SELECT FROM c24_source_fences WHERE organization_id=$1 AND request_id=$2)",
            org, other_request,
        )
        assert await pool.fetchval(
            "SELECT EXISTS(SELECT FROM c24_receipt_cleanup_audit WHERE organization_id=$1 AND request_id=$2)",
            org, other_request,
        )
    assert not await pool.fetchval(
        """SELECT EXISTS(SELECT FROM c24_source_fences WHERE organization_id=$1 AND request_id=$2)
           OR EXISTS(SELECT FROM c24_suppression_lifecycle_audit WHERE organization_id=$1 AND source_record_id=$2)""",
        org, request,
    )
