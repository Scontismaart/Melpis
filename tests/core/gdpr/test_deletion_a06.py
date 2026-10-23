"""PostgreSQL integration tests for approved, fail-closed erasure."""
import asyncio
import uuid

import pytest
from cryptography.fernet import Fernet

from src.core.db.repositories.contact_repo import ContactRepository
from src.core.gdpr.deletion import Deletion
from src.core.gdpr.guard import ErasureBlocked
from src.core.gdpr.operations import Operations, refresh_provider_coverage


POLICY_CATEGORIES = (
    "contact_content", "transient_content", "cutover",
    "knowledge_content", "organization_profile",
)


@pytest.fixture(autouse=True)
def _synthetic_gdpr_settings(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))


async def approve_policies(pool, org, *, organization=False):
    categories = POLICY_CATEGORIES if organization else POLICY_CATEGORIES[:3]
    async with pool.acquire() as conn:
        for category in categories:
            await conn.execute(
                """INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
                       retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
                   VALUES($1,$2,'synthetic-a06',TRUE,30,FALSE,'test:synthetic','qa-a06',
                          NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 day')""",
                org, category,
            )


async def seed_contact_data(pool, org, *, phone="+15550001111", message_status="handled"):
    contact_id, conversation_id, message_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    document_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
            contact_id, org, phone,
        )
        await conn.execute(
            "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)",
            conversation_id, org, contact_id,
        )
        await conn.execute(
            """INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,
                   content,content_text,status,handling_type)
               VALUES($1,$2,$3,'inbound','text',$4::jsonb,$5,$6,'human')""",
            message_id, org, conversation_id, '{"private":"synthetic"}',
            "synthetic private message body", message_status,
        )
        await conn.execute(
            "INSERT INTO documents(id,organization_id,nome,tipo) VALUES($1,$2,'private synthetic doc','upload')",
            document_id, org,
        )
        vector = "[" + ",".join(["0"] * 384) + "]"
        await conn.execute(
            """INSERT INTO document_chunks(organization_id,document_id,chunk_index,content,embedding)
               VALUES($1,$2,0,'synthetic private knowledge',$3::vector)""",
            org, document_id, vector,
        )
    return contact_id, conversation_id, message_id, document_id


async def request_approved_deletion(pool, monkeypatch, org, requester, request_id, *, scope="contact", subject_id):
    monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")
    return await Deletion(pool).request(
        org, requester, request_id, scope=scope, subject_id=subject_id,
    )


@pytest.mark.asyncio
async def test_default_blocked_without_cutover_or_approved_policies_preserves_source(
    pg_pool, sample_org, monkeypatch,
):
    org = sample_org["id"]
    contact_id, _, message_id, _ = await seed_contact_data(pg_pool, org)
    monkeypatch.delenv("GDPR_ERASURE_EXECUTION_ENABLED", raising=False)

    result = await Deletion(pg_pool).request(org, "owner-default", uuid.uuid4(),
                                             scope="contact", subject_id=contact_id)
    assert result["status"] == "blocked"
    assert "operational_cutover_not_authorized" in result["manifest"]["blockers"]
    assert any(x.startswith("policy_required.") for x in result["manifest"]["blockers"])
    assert result["manifest"]["local_erasure"] == "not_started"
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT content_text FROM messages WHERE id=$1", message_id) == "synthetic private message body"
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM gdpr_erasure_barriers WHERE organization_id=$1)", org)
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM gdpr_operation_steps WHERE operation_id=$1 AND name='local')", result["id"])


@pytest.mark.asyncio
async def test_approved_org_erasure_is_local_partial_and_preserves_root_billing_audit_and_counters(
    pg_pool, sample_org, monkeypatch,
):
    org = sample_org["id"]
    await approve_policies(pg_pool, org, organization=True)
    contact_id, _, message_id, document_id = await seed_contact_data(pg_pool, org)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET business_profile=$2::jsonb, "
            "messages_used_this_period=42 WHERE id=$1", org, '{"private":"synthetic profile"}',
        )
        await conn.execute(
            "INSERT INTO usage_events(organization_id,event_type,quantity,metadata) VALUES($1,'synthetic.billing',9,'{}')",
            org,
        )
        await conn.execute(
            "INSERT INTO audit_log(organization_id,action,target_table,target_id,details) "
            "VALUES($1,'synthetic.audit','contacts',$2,'{}')", org, contact_id,
        )
        for status in ("completata", "cancellata"):
            await conn.execute(
                """INSERT INTO bookings(organization_id,contact_id,nome_cliente,telefono,data,ora,coperti,
                       note,stato) VALUES($1,$2,'Synthetic Customer','+15550001111',CURRENT_DATE,
                       TIME '12:00',2,'synthetic private booking',$3)""", org, contact_id, status,
            )

    result = await request_approved_deletion(
        pg_pool, monkeypatch, org, "owner-approved-org", uuid.uuid4(),
        scope="organization", subject_id=None,
    )
    assert result["status"] == "partial"
    assert result["manifest"]["external_copies"] == "pending_verified_receipts"
    assert "billing" in result["manifest"]["retained"]
    assert "audit" in result["manifest"]["retained"]
    provider_states = {step['name']: step['status'] for step in result['steps'] if step['name'].startswith('provider.')}
    assert provider_states.pop('provider.inventory_review') == 'held'
    assert provider_states and all(status == 'pending' for status in provider_states.values())

    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM organizations WHERE id=$1", org) == 1
        assert await conn.fetchval("SELECT messages_used_this_period FROM organizations WHERE id=$1", org) == 42
        assert await conn.fetchval("SELECT count(*) FROM usage_events WHERE organization_id=$1", org) == 1
        assert await conn.fetchval("SELECT count(*) FROM audit_log WHERE organization_id=$1", org) == 1
        assert await conn.fetchval("SELECT count(*) FROM documents WHERE organization_id=$1", org) == 0
        assert await conn.fetchval("SELECT count(*) FROM document_chunks WHERE organization_id=$1", org) == 0
        assert await conn.fetchval("SELECT content_text IS NULL FROM messages WHERE id=$1", message_id)
        assert await conn.fetchval("SELECT phone_number LIKE 'erased:%' FROM contacts WHERE id=$1", contact_id)
        assert await conn.fetchval("SELECT bool_and(nome_cliente='REDACTED' AND telefono='') FROM bookings WHERE organization_id=$1", org)
        assert await conn.fetchval("SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1", org) == 1


@pytest.mark.asyncio
async def test_contact_erasure_is_idempotent_and_tombstone_blocks_recreated_identity(
    pg_pool, sample_org, other_org, monkeypatch,
):
    org = sample_org["id"]
    await approve_policies(pg_pool, org)
    target = await seed_contact_data(pg_pool, org, phone="+15550002222")
    unrelated = await seed_contact_data(pg_pool, org, phone="+15550003333")
    outside = await seed_contact_data(pg_pool, other_org["id"], phone="+15550004444")
    request_id = uuid.uuid4()

    first = await request_approved_deletion(
        pg_pool, monkeypatch, org, "owner-replay", request_id,
        scope="contact", subject_id=target[0],
    )
    again = await request_approved_deletion(
        pg_pool, monkeypatch, org, "owner-replay", request_id,
        scope="contact", subject_id=target[0],
    )
    assert first["id"] == again["id"]
    assert first["status"] == again["status"] == "partial"

    async with pg_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT attempts FROM gdpr_operation_steps WHERE organization_id=$1 AND operation_id=$2 AND name='local'",
            org, first["id"],
        ) == 1
        assert await conn.fetchval("SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1", org) == 1
        assert await conn.fetchval("SELECT content_text FROM messages WHERE id=$1", target[2]) is None
        assert await conn.fetchval("SELECT content_text FROM messages WHERE id=$1", unrelated[2]) == "synthetic private message body"
        assert await conn.fetchval("SELECT content_text FROM messages WHERE id=$1", outside[2]) == "synthetic private message body"
        assert await conn.fetchval("SELECT count(*) FROM documents WHERE organization_id=$1", org) == 2
    with pytest.raises(ErasureBlocked):
        await ContactRepository(pg_pool).get_or_create_contact(org, "+15550002222")


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["unresolved_provider", "reserved_quota", "processing_message", "active_booking"])
async def test_inflight_work_blocks_erasure_without_mutation(pg_pool, sample_org, monkeypatch, blocker):
    org = sample_org["id"]
    await approve_policies(pg_pool, org)
    contact_id, _, message_id, _ = await seed_contact_data(
        pg_pool, org, phone="+15550005555", message_status="handled",
    )
    async with pg_pool.acquire() as conn:
        if blocker in {"unresolved_provider", "reserved_quota"}:
            event_type, state = (
                ("system.provider_attempt", "unresolved") if blocker == "unresolved_provider"
                else ("system.quota_reservation", "reserved")
            )
            await conn.execute(
                "INSERT INTO usage_events(organization_id,event_type,metadata) VALUES($1,$2,$3::jsonb)",
                org, event_type, '{"accounting_status":"' + state + '","state":"' + state + '"}',
            )
        elif blocker == "processing_message":
            await conn.execute("UPDATE messages SET status='processing' WHERE id=$1", message_id)
        elif blocker == "active_booking":
            await conn.execute(
                """INSERT INTO bookings(organization_id,contact_id,nome_cliente,telefono,data,ora,coperti,stato)
                   VALUES($1,$2,'Synthetic Customer','+15550005555',CURRENT_DATE,TIME '12:00',2,'confermata')""",
                org, contact_id,
            )

    result = await request_approved_deletion(
        pg_pool, monkeypatch, org, "owner-inflight-" + blocker, uuid.uuid4(),
        scope="contact", subject_id=contact_id,
    )
    assert result["status"] == "blocked"
    assert result["manifest"]["local_erasure"] == "not_started"
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT content_text FROM messages WHERE id=$1", message_id) == "synthetic private message body"
        assert await conn.fetchval("SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1", org) == 0


@pytest.mark.asyncio
async def test_local_mutation_crash_rolls_back_barrier_and_retry_commits_cleanly(
    pg_pool, sample_org, monkeypatch,
):
    org = sample_org["id"]
    await approve_policies(pg_pool, org)
    contact_id, _, message_id, _ = await seed_contact_data(pg_pool, org, phone="+15550006666")
    request_id, requester = uuid.uuid4(), "owner-crash-retry"
    monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")

    async with pg_pool.acquire() as conn:
        await conn.execute("""CREATE FUNCTION qa_a06_crash_on_redaction() RETURNS trigger AS $$
            BEGIN IF NEW.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'synthetic crash'; END IF;
            RETURN NEW; END; $$ LANGUAGE plpgsql""")
        await conn.execute("CREATE TRIGGER qa_a06_crash BEFORE UPDATE ON messages "
                           "FOR EACH ROW EXECUTE FUNCTION qa_a06_crash_on_redaction()")
    try:
        with pytest.raises(Exception, match="synthetic crash"):
            await Deletion(pg_pool).request(
                org, requester, request_id, scope="contact", subject_id=contact_id,
            )
    finally:
        async with pg_pool.acquire() as conn:
            await conn.execute("DROP TRIGGER IF EXISTS qa_a06_crash ON messages")
            await conn.execute("DROP FUNCTION IF EXISTS qa_a06_crash_on_redaction()")

    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT content_text FROM messages WHERE id=$1", message_id) == "synthetic private message body"
        assert await conn.fetchval("SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1", org) == 0
        operation_id = await conn.fetchval(
            "SELECT id FROM gdpr_operations WHERE organization_id=$1 AND requester=$2 AND request_id=$3",
            org, requester, request_id,
        )
    retried = await Deletion(pg_pool).request(
        org, requester, request_id, scope="contact", subject_id=contact_id,
    )
    assert retried["id"] == operation_id and retried["status"] == "partial"
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1", org) == 1
        assert await conn.fetchval("SELECT content_text IS NULL FROM messages WHERE id=$1", message_id)


@pytest.mark.asyncio
async def test_concurrent_replay_commits_one_local_step(pg_pool, sample_org, monkeypatch):
    org = sample_org["id"]
    await approve_policies(pg_pool, org)
    contact_id, _, _, _ = await seed_contact_data(pg_pool, org, phone="+15550007777")
    request_id = uuid.uuid4()
    monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")

    results = await asyncio.gather(*(
        Deletion(pg_pool).request(org, "owner-race", request_id,
                                  scope="contact", subject_id=contact_id)
        for _ in range(2)
    ))
    assert results[0]["id"] == results[1]["id"]
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT attempts FROM gdpr_operation_steps WHERE organization_id=$1 AND operation_id=$2 AND name='local'",
            org, results[0]["id"],
        ) == 1
        assert await conn.fetchval("SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1", org) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inventory", "expected_stage"),
    [
        ("instagram", "provider.meta"),
        ("google_business", "provider.google_business"),
        ("configured_services", "provider.softr"),
        ("configured_services", "provider.sentry"),
        ("configured_services", "provider.smtp"),
        ("stripe", "provider.stripe_retention_review"),
    ],
)
async def test_inventory_changes_create_pending_provider_stages(
    pg_pool, sample_org, monkeypatch, inventory, expected_stage,
):
    org = sample_org["id"]
    await approve_policies(pg_pool, org)
    contact_id, _, _, _ = await seed_contact_data(
        pg_pool, org, phone="+15550008888",
    )
    for setting in ("SOFTR_WEBHOOK_URL", "SENTRY_DSN", "SMTP_HOST"):
        monkeypatch.delenv(setting, raising=False)
    if inventory == "instagram":
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO instagram_accounts(id,organization_id,ig_user_id,access_token) "
                "VALUES($1,$2,'synthetic-ig-user','synthetic-token')",
                uuid.uuid4(), org,
            )
    elif inventory == "google_business":
        async with pg_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO google_business_credentials(organization_id,access_token,refresh_token,
                       token_expiry,account_name) VALUES($1,'synthetic-access','synthetic-refresh',
                       NOW()+INTERVAL '1 day','synthetic-account')""",
                org,
            )
    elif inventory == "configured_services":
        monkeypatch.setenv("SOFTR_WEBHOOK_URL", "https://synthetic.invalid/hook")
        monkeypatch.setenv("SENTRY_DSN", "https://synthetic.invalid/1")
        monkeypatch.setenv("SMTP_HOST", "synthetic.invalid")
    elif inventory == "stripe":
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "UPDATE organizations SET stripe_customer_id='cus_synthetic_a06' WHERE id=$1", org,
            )

    result = await request_approved_deletion(
        pg_pool, monkeypatch, org, "owner-inventory-" + inventory, uuid.uuid4(),
        scope="contact", subject_id=contact_id,
    )
    assert result["status"] == "partial"
    stages = {step["name"]: step["status"] for step in result["steps"]}
    assert stages[expected_stage] == "pending"
    assert stages["provider.inventory_review"] == "held"
    if inventory == "instagram":
        assert "provider.google_business" not in stages
    elif inventory == "google_business":
        assert "provider.meta" not in stages
    elif inventory == "configured_services":
        assert all(stages[f"provider.{name}"] == "pending" for name in ("softr", "sentry", "smtp"))


@pytest.mark.asyncio
async def test_inventory_review_cannot_be_claimed_or_overridden_by_provider_confirmations(
    pg_pool, sample_org, monkeypatch,
):
    org = sample_org["id"]
    await approve_policies(pg_pool, org)
    contact_id, _, _, _ = await seed_contact_data(
        pg_pool, org, phone="+15550009998",
    )
    monkeypatch.delenv("SOFTR_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("SOFTR_WEBHOOK_URL", "https://synthetic.invalid/hook")
    result = await request_approved_deletion(
        pg_pool, monkeypatch, org, "owner-inventory-hold", uuid.uuid4(),
        scope="contact", subject_id=contact_id,
    )
    oid = result["id"]

    assert await Operations(pg_pool).claim_provider(org, oid, "provider.inventory_review") is None
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """UPDATE gdpr_operation_steps SET status='confirmed',lease_until=NULL
               WHERE organization_id=$1 AND operation_id=$2 AND name LIKE 'provider.%'
                 AND name <> 'provider.inventory_review'""",
            org, oid,
        )
        await refresh_provider_coverage(conn, org, oid)

    final = await Operations(pg_pool).get(org, oid, "owner-inventory-hold")
    assert final["steps"]
    assert {step["name"]: step["status"] for step in final["steps"]}["provider.inventory_review"] == "held"
    assert final["manifest"]["external_copies"] == "pending_verified_receipts"
