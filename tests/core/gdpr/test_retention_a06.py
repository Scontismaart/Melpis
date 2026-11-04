"""PostgreSQL integration tests for GDPR-gated retention behavior."""
import hashlib
import uuid

import pytest

from src.core.retention_job import run_retention
from src.core import retention_job
from tests.core.gdpr.test_c24_schema_compatibility import legacy_pool as pg_pool


async def approve_message_retention(pool, org, *, days=1):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
                   retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
               VALUES($1,'messages_content','synthetic-retention',TRUE,$2,FALSE,
                      'test:synthetic-retention','qa-a06',NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 day')""",
            org, days,
        )


async def seed_messages(pool, org, *, count=1, status="handled", billed=False,
                        text="synthetic retained message", age_days=5):
    contact_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
            contact_id, org, "+1555" + uuid.uuid4().hex[:7],
        )
        await conn.execute(
            "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)",
            conversation_id, org, contact_id,
        )
        ids = [uuid.uuid4() for _ in range(count)]
        await conn.executemany(
            """INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,
                   content,content_text,status,handling_type,billed_at,created_at)
               VALUES($1,$2,$3,'inbound','text','{}'::jsonb,$4,$5,'human',
                   CASE WHEN $6 THEN NOW() END,NOW()-make_interval(days=>$7))""",
            [(mid, org, conversation_id, text, status, billed, age_days) for mid in ids],
        )
    return ids


@pytest.fixture(autouse=True)
def _retention_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GDPR_RETENTION_EXECUTION_ENABLED", raising=False)


@pytest.mark.asyncio
async def test_primary_data_retention_is_disabled_without_explicit_cutover(
    pg_pool, sample_org,
):
    org = sample_org["id"]
    await approve_message_retention(pg_pool, org, days=1)
    message_id = (await seed_messages(pg_pool, org))[0]

    counts = await run_retention(pg_pool)
    assert counts["messages_redacted"] == 0
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT content_text,deleted_at FROM messages WHERE id=$1", message_id)
    assert row["content_text"] == "synthetic retained message"
    assert row["deleted_at"] is None


@pytest.mark.asyncio
async def test_approved_retention_is_tenant_scoped_bounded_and_preserves_protected_records(
    pg_pool, sample_org, other_org, monkeypatch,
):
    org = sample_org["id"]
    other = other_org["id"]
    await approve_message_retention(pg_pool, org, days=1)
    eligible = await seed_messages(pg_pool, org, count=502, text="synthetic eligible content")
    billed_id = (await seed_messages(
        pg_pool, org, billed=True, text="synthetic billed content",
    ))[0]
    processing_id = (await seed_messages(
        pg_pool, org, status="processing", text="synthetic processing content",
    ))[0]
    pending_id = (await seed_messages(
        pg_pool, org, status="received_pending_ai", text="synthetic pending content",
    ))[0]
    other_id = (await seed_messages(
        pg_pool, other, text="synthetic other tenant content",
    ))[0]
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO usage_events(organization_id,event_type,quantity,metadata) "
            "VALUES($1,'synthetic.billing',4,'{}')", org,
        )
        await conn.execute(
            "INSERT INTO audit_log(organization_id,action,target_table,target_id,details) "
            "VALUES($1,'synthetic.audit','messages',$2,'{}')", org, eligible[0],
        )
        contact_id = await conn.fetchval("SELECT contact_id FROM conversations WHERE id=(SELECT conversation_id FROM messages WHERE id=$1)", eligible[0])
        await conn.execute(
            """INSERT INTO bookings(organization_id,contact_id,nome_cliente,telefono,data,ora,coperti,stato)
               VALUES($1,$2,'Synthetic Customer','+15550009999',CURRENT_DATE,TIME '13:00',2,'completata')""",
            org, contact_id,
        )

    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED", "true")
    first = await run_retention(pg_pool)
    assert first["messages_redacted"] == 500
    second = await run_retention(pg_pool)
    assert second["messages_redacted"] == 2

    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM messages WHERE id=ANY($1::uuid[]) AND content_text IS NULL", eligible) == 502
        for message_id in (billed_id, processing_id, pending_id, other_id):
            assert await conn.fetchval("SELECT content_text FROM messages WHERE id=$1", message_id) is not None
        assert await conn.fetchval("SELECT content_text FROM messages WHERE id=$1", other_id) == "synthetic other tenant content"
        assert await conn.fetchval("SELECT count(*) FROM usage_events WHERE organization_id=$1", org) == 1
        assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE organization_id=$1", org) == 1
        assert await conn.fetchval("SELECT count(*) FROM bookings WHERE organization_id=$1", org) == 1


@pytest.mark.asyncio
async def test_legacy_runner_stops_on_partial_c24_schema(pg_pool, sample_org, monkeypatch):
    org = sample_org["id"]
    await approve_message_retention(pg_pool, org)
    message = (await seed_messages(pg_pool, org))[0]
    async with pg_pool.acquire() as conn:
        await conn.execute('ALTER TABLE messages ADD COLUMN c24_original_subject_id uuid')
    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED", "true")

    assert (await run_retention(pg_pool))["messages_redacted"] == 0
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT content_text FROM messages WHERE id=$1", message)


@pytest.mark.asyncio
async def test_export_snapshot_expiry_cleans_at_most_one_hundred_chunks(pg_pool, sample_org):
    org = sample_org["id"]
    ids = [uuid.uuid4() for _ in range(101)]
    async with pg_pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,
                   kind,scope,status,manifest,expires_at)
               VALUES($1,$2,'retention-qa',$3,$4,'export','organization','ready','{}',
                      NOW()-INTERVAL '1 second')""",
            [(oid, org, uuid.uuid4(), hashlib.sha256(oid.bytes).hexdigest()) for oid in ids],
        )
        await conn.executemany(
            """INSERT INTO gdpr_export_chunks(organization_id,operation_id,sequence,ciphertext,digest)
               VALUES($1,$2,0,$3,$4)""",
            [(org, oid, b"synthetic-encrypted-chunk", hashlib.sha256(b"synthetic-encrypted-chunk").hexdigest())
             for oid in ids],
        )

    counts = await run_retention(pg_pool)
    assert counts["exports_expired"] == 100
    assert counts["messages_redacted"] == 0
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM gdpr_export_chunks WHERE organization_id=$1", org) == 1
        assert await conn.fetchval("SELECT count(*) FROM gdpr_operations WHERE organization_id=$1 AND status='expired'", org) == 100
        assert await conn.fetchval("SELECT count(*) FROM gdpr_operations WHERE organization_id=$1 AND status='ready'", org) == 1


async def insert_message_policy(pool, org, *, version, days=1, hold=False, valid=True,
                                approved_offset="0 seconds"):
    valid_until = "NOW()+INTERVAL '1 day'" if valid else "NOW()-INTERVAL '1 second'"
    approved_at = (
        f"NOW()+INTERVAL '{approved_offset}'" if valid else "NOW()-INTERVAL '2 seconds'"
    )
    async with pool.acquire() as conn:
        await conn.execute(
            f"""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
                    retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
                VALUES($1,'messages_content',$2,TRUE,$4,$3,'test:synthetic-retention','qa-a06',
                       {approved_at},{valid_until})""",
            org, version, hold, days,
        )


@pytest.mark.asyncio
async def test_retention_rechecks_latest_policy_after_enumeration_race(
    pg_pool, sample_org, monkeypatch,
):
    org = sample_org["id"]
    await approve_message_retention(pg_pool, org, days=1)
    message_id = (await seed_messages(pg_pool, org))[0]
    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED", "true")
    original = retention_job._retention_policies

    async def stale_rows_then_new_hold(pool):
        policies = await original(pool)
        await insert_message_policy(
            pool, org, version="synthetic-new-hold", hold=True, approved_offset="0 seconds",
        )
        return policies

    monkeypatch.setattr(retention_job, "_retention_policies", stale_rows_then_new_hold)
    counts = await run_retention(pg_pool)
    assert counts["messages_redacted"] == 0
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT content_text,deleted_at FROM messages WHERE id=$1", message_id)
    assert row["content_text"] == "synthetic retained message"
    assert row["deleted_at"] is None


@pytest.mark.asyncio
async def test_expired_newer_policy_does_not_fall_back_to_older_approval(
    pg_pool, sample_org, monkeypatch,
):
    org = sample_org["id"]
    await approve_message_retention(pg_pool, org, days=1)
    await insert_message_policy(
        pg_pool, org, version="synthetic-new-expired", valid=False, approved_offset="0 seconds",
    )
    message_id = (await seed_messages(pg_pool, org))[0]
    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED", "true")

    counts = await run_retention(pg_pool)
    assert counts["messages_redacted"] == 0
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT content_text,deleted_at FROM messages WHERE id=$1", message_id)
    assert row["content_text"] == "synthetic retained message"
    assert row["deleted_at"] is None


@pytest.mark.asyncio
async def test_product_cap_allows_90_days_rejects_91_and_remains_tenant_scoped(
    pg_pool, sample_org, other_org, monkeypatch,
):
    org_a = sample_org["id"]
    org_b = other_org["id"]
    await approve_message_retention(pg_pool, org_a, days=90)
    await approve_message_retention(pg_pool, org_b, days=91)
    message_a = (await seed_messages(
        pg_pool, org_a, age_days=120, text="synthetic tenant A old message",
    ))[0]
    message_b = (await seed_messages(
        pg_pool, org_b, age_days=120, text="synthetic tenant B old message",
    ))[0]
    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED", "true")

    counts = await run_retention(pg_pool)

    assert counts["messages_redacted"] == 1
    async with pg_pool.acquire() as conn:
        row_a = await conn.fetchrow(
            "SELECT content_text,deleted_at FROM messages WHERE id=$1", message_a,
        )
        row_b = await conn.fetchrow(
            "SELECT content_text,deleted_at FROM messages WHERE id=$1", message_b,
        )
    assert row_a["content_text"] is None
    assert row_a["deleted_at"] is not None
    assert row_b["content_text"] == "synthetic tenant B old message"
    assert row_b["deleted_at"] is None


@pytest.mark.asyncio
async def test_latest_over_cap_policy_inserted_after_enumeration_prevents_redaction(
    pg_pool, sample_org, monkeypatch,
):
    org = sample_org["id"]
    await approve_message_retention(pg_pool, org, days=1)
    message_id = (await seed_messages(
        pg_pool, org, age_days=120,
    ))[0]
    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED", "true")
    original = retention_job._retention_policies

    async def stale_rows_then_over_cap_policy(pool):
        policies = await original(pool)
        await insert_message_policy(
            pool, org, version="synthetic-new-over-cap", days=91,
            approved_offset="0 seconds",
        )
        return policies

    monkeypatch.setattr(retention_job, "_retention_policies", stale_rows_then_over_cap_policy)
    counts = await run_retention(pg_pool)

    assert counts["messages_redacted"] == 0
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT content_text,deleted_at FROM messages WHERE id=$1", message_id,
        )
    assert row["content_text"] == "synthetic retained message"
    assert row["deleted_at"] is None


@pytest.mark.asyncio
async def test_unmapped_simulator_schedule_does_not_delete_completed_response(
    pg_pool, sample_org, monkeypatch,
):
    org, user, request = sample_org["id"], uuid.uuid4(), uuid.uuid4()
    await pg_pool.execute("""INSERT INTO simulation_requests(
        organization_id,auth_user_id,request_id,payload_hash,status,response,completed_at,created_at)
        VALUES($1,$2,$3,$4,'completed','{"synthetic":"retained"}',
               NOW()-INTERVAL '120 days',NOW()-INTERVAL '120 days')""",
        org, user, request, hashlib.sha256(b"synthetic-response").hexdigest())
    await pg_pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,
        erasure_allowed,retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'simulator_responses','synthetic-unmapped',TRUE,1,FALSE,
               'test:synthetic','qa',NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 day')""", org)
    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED", "true")

    counts = await run_retention(pg_pool)

    assert counts["simulator_purged"] == 0
    assert await pg_pool.fetchval("""SELECT response->>'synthetic' FROM simulation_requests
        WHERE organization_id=$1 AND auth_user_id=$2 AND request_id=$3""", org, user, request) == "retained"
