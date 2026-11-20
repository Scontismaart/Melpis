"""Scoped legal hold checks for local GDPR erasure on PostgreSQL."""
import asyncio
from uuid import uuid4

import asyncpg
import pytest
from cryptography.fernet import Fernet

from src.core.gdpr.deletion import Deletion
from src.core.gdpr.operations import Operations
from tests.core.gdpr.test_deletion_a06 import approve_policies, seed_contact_data


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def synthetic_erasure(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")


async def add_hold(conn, org, scope, target, *, category="messages_content"):
    hold = uuid4()
    await conn.execute(
        """INSERT INTO gdpr_retention_holds
           (organization_id,hold_id,category,scope,scope_id,reason_code,
            authority_reference,approved_by,approved_at,review_by)
           VALUES($1,$2,$3,$4,$5,'synthetic_reason','test:authority','synthetic',
                  NOW()-INTERVAL '3 days',NOW()-INTERVAL '2 days')""",
        org, hold, category, scope, target,
    )
    return hold


async def release_hold(conn, org, hold):
    await conn.execute(
        """INSERT INTO gdpr_retention_hold_events
           (organization_id,hold_id,event_id,kind,authority_reference,approved_by,occurred_at)
           VALUES($1,$2,$3,'released','test:release','synthetic',CLOCK_TIMESTAMP())""",
        org, hold, uuid4(),
    )


async def prepare_subject(pool, org, phone="+15550002345"):
    await approve_policies(pool, org)
    person, conversation, message, _ = await seed_contact_data(pool, org, phone=phone)
    return person, conversation, message


@pytest.mark.parametrize(
    ("scope", "index", "reason"),
    [
        ("organization", None, "scoped_legal_hold"),
        ("subject", 0, "scoped_legal_hold"),
        ("conversation", 1, "scoped_legal_hold"),
        ("record", 2, "scoped_legal_hold"),
        ("record", -1, "legal_hold_scope_unverified"),
    ],
)
async def test_unreleased_hold_prevents_erasure_even_after_review_date(
    pg_pool, sample_org, scope, index, reason,
):
    org = sample_org["id"]
    target = await prepare_subject(pg_pool, org)
    evidence = None if index is None else uuid4() if index == -1 else target[index]
    hold = await add_hold(pg_pool, org, scope, evidence)
    request = uuid4()
    deletion = Deletion(pg_pool)
    operation = await deletion.request(org, "owner-hold", request, scope="contact", subject_id=target[0])
    assert operation["status"] == "blocked"
    assert reason in operation["manifest"]["blockers"]
    assert await pg_pool.fetchval(
        "SELECT content_text FROM messages WHERE organization_id=$1 AND id=$2", org, target[2],
    ) == "synthetic private message body"
    assert not await pg_pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM gdpr_erasure_barriers WHERE organization_id=$1)", org,
    )

    await release_hold(pg_pool, org, hold)
    retry = await deletion.request(org, "owner-hold", request, scope="contact", subject_id=target[0])
    assert retry["id"] == operation["id"] and retry["status"] == "partial"
    assert await pg_pool.fetchval(
        "SELECT content_text FROM messages WHERE organization_id=$1 AND id=$2", org, target[2],
    ) is None


async def test_record_hold_for_another_subject_does_not_block_scoped_erasure(pg_pool, sample_org, other_org):
    org, foreign = sample_org["id"], other_org["id"]
    person, _, message = await prepare_subject(pg_pool, org)
    _, _, other_message, _ = await seed_contact_data(pg_pool, org, phone="+15550002346")
    await add_hold(pg_pool, org, "record", other_message)
    await add_hold(pg_pool, foreign, "organization", None)

    result = await Deletion(pg_pool).request(org, "owner-hold", uuid4(), scope="contact", subject_id=person)
    assert result["status"] == "partial"
    assert await pg_pool.fetchval(
        "SELECT content_text FROM messages WHERE organization_id=$1 AND id=$2", org, message,
    ) is None
    assert await pg_pool.fetchval(
        "SELECT content_text FROM messages WHERE organization_id=$1 AND id=$2", org, other_message,
    ) == "synthetic private message body"


@pytest.mark.parametrize("category", ["contact_content", "booking_conversation_content", "simulator_responses"])
async def test_unverified_shared_copy_blocks_even_with_a_known_other_subject_hold(pg_pool, sample_org, category):
    org = sample_org["id"]
    person, _, message = await prepare_subject(pg_pool, org)
    other_person, _, _, _ = await seed_contact_data(pg_pool, org, phone="+15550002346")
    if category == "contact_content":
        # Raw envelopes can contain multiple subjects and diagnostics without
        # a provable relationship to either person.
        await pg_pool.execute(
            """INSERT INTO meta_webhook_inbox(id,organization_id,channel,event_key,trace_id,status,payload)
               VALUES($1,$2,'whatsapp','synthetic-held-envelope','synthetic-trace','completed',
                      '{"entry":[{"synthetic":"mixed"}]}'::jsonb)""", uuid4(), org,
        )
    await add_hold(pg_pool, org, "subject", other_person, category=category)

    result = await Deletion(pg_pool).request(org, "owner-hold", uuid4(), scope="contact", subject_id=person)
    assert result["status"] == "blocked"
    assert "legal_hold_scope_unverified" in result["manifest"]["blockers"]
    assert await pg_pool.fetchval(
        "SELECT content_text FROM messages WHERE organization_id=$1 AND id=$2", org, message,
    ) == "synthetic private message body"


async def test_current_hold_is_seen_after_waiting_for_tenant_lock(pg_pool, sample_org):
    org = sample_org["id"]
    person, _, _ = await prepare_subject(pg_pool, org)
    oid = await Operations(pg_pool).create(
        org, "owner-hold", uuid4(), kind="erase", scope="contact", subject_id=person,
    )
    async with pg_pool.acquire() as writer, writer.transaction():
        await add_hold(writer, org, "subject", person)
        task = asyncio.create_task(Deletion(pg_pool).advance(org, oid, "owner-hold"))

        async def wait_for_block():
            async with pg_pool.acquire() as observer:
                while not await observer.fetchval(
                    """SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                       WHERE $1=ANY(pg_blocking_pids(pid)))""", writer.get_server_pid(),
                ):
                    await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_block(), timeout=5)
    await asyncio.wait_for(task, timeout=10)
    result = await Operations(pg_pool).get(org, oid, "owner-hold")
    assert result["status"] == "blocked"
    assert "scoped_legal_hold" in result["manifest"]["blockers"]


async def test_scoped_hold_function_acl_and_tenant_scope(pg_pool, sample_org, other_org):
    org = sample_org["id"]
    person, _, _ = await prepare_subject(pg_pool, org)
    oid = await Operations(pg_pool).create(
        org, "owner-hold", uuid4(), kind="erase", scope="contact", subject_id=person,
    )
    await add_hold(pg_pool, org, "subject", person)
    async with pg_pool.acquire() as conn, conn.transaction():
        for role in ("anon", "authenticated"):
            assert not await conn.fetchval(
                "SELECT has_function_privilege($1,'public.gdpr_erasure_hold_check(uuid,uuid)','EXECUTE')",
                role,
            )
        assert not await conn.fetchval(
            "SELECT has_table_privilege('service_role','public.gdpr_retention_holds','SELECT')"
        )
        await conn.execute("SET LOCAL ROLE service_role")
        assert await conn.fetchval("SELECT public.gdpr_erasure_hold_check($1,$2)", org, oid) == "held"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval("SELECT public.gdpr_erasure_hold_check($1,$2)", other_org["id"], oid)


@pytest.mark.migration_limit(104)
async def test_schema_with_private_holds_but_without_checked_lookup_defers_erasure(pg_pool, sample_org):
    org = sample_org["id"]
    person, _, message = await prepare_subject(pg_pool, org)
    result = await Deletion(pg_pool).request(org, "owner-hold", uuid4(), scope="contact", subject_id=person)
    assert result["status"] == "blocked"
    assert "legal_hold_check_unavailable" in result["manifest"]["blockers"]
    assert await pg_pool.fetchval(
        "SELECT content_text FROM messages WHERE organization_id=$1 AND id=$2", org, message,
    ) == "synthetic private message body"
