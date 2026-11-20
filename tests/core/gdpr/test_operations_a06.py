"""PostgreSQL-backed GDPR operation and provider-fencing contracts."""
import asyncio
import hashlib
import uuid
from pathlib import Path

import pytest
import asyncpg

from src.core.gdpr.operations import OperationUnavailable, Operations, RequestConflict


async def make_operation(pool, org_id, requester="owner-a", request_id=None):
    request_id = request_id or uuid.uuid4()
    oid = await Operations(pool).create(
        org_id, requester, request_id, kind="erase", scope="organization",
    )
    return oid, request_id


async def authorize_stage(pool, org, oid, provider="provider.crm"):
    """Model the already committed local erasure and its inventoried provider."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE gdpr_operations SET status='partial' WHERE organization_id=$1 AND id=$2",
            org, oid,
        )
        await conn.execute(
            """INSERT INTO gdpr_operation_steps(organization_id,operation_id,name,status)
               VALUES($1,$2,'local','confirmed'),($1,$2,$3,'pending')""",
            org, oid, provider,
        )


@pytest.mark.asyncio
async def test_create_is_idempotent_and_request_hash_conflicts_are_rejected(pg_pool, sample_org):
    ops = Operations(pg_pool)
    request_id = uuid.uuid4()
    first = await ops.create(
        sample_org["id"], "owner-a", request_id,
        kind="export", scope="organization",
    )
    second = await ops.create(
        sample_org["id"], "owner-a", request_id,
        kind="export", scope="organization",
    )

    assert first == second
    with pytest.raises(RequestConflict):
        await ops.create(
            sample_org["id"], "owner-a", request_id,
            kind="erase", scope="organization",
        )


@pytest.mark.asyncio
async def test_get_is_bound_to_organization_and_requester(pg_pool, sample_org, other_org):
    oid, _ = await make_operation(pg_pool, sample_org["id"])
    ops = Operations(pg_pool)

    with pytest.raises(LookupError):
        await ops.get(other_org["id"], oid, "owner-a")
    with pytest.raises(LookupError):
        await ops.get(sample_org["id"], oid, "owner-b")


@pytest.mark.asyncio
async def test_provider_claim_has_one_concurrent_winner(pg_pool, sample_org):
    oid, _ = await make_operation(pg_pool, sample_org["id"])
    ops = Operations(pg_pool)
    await authorize_stage(pg_pool, sample_org["id"], oid)
    claims = await asyncio.gather(*(
        ops.claim_provider(sample_org["id"], oid, "provider.crm")
        for _ in range(20)
    ))

    assert sum(claim is not None for claim in claims) == 1
    state = await ops.get(sample_org["id"], oid, "owner-a")
    step = next(s for s in state["steps"] if s["name"] == "provider.crm")
    assert step["status"] == "running"
    assert step["attempts"] == 1


@pytest.mark.asyncio
async def test_provider_claim_requires_preexisting_inventory_stage(pg_pool, sample_org):
    org = sample_org["id"]
    oid, _ = await make_operation(pg_pool, org)
    await authorize_stage(pg_pool, org, oid)
    assert await Operations(pg_pool).claim_provider(org, oid, "provider.other") is None
    state = await Operations(pg_pool).get(org, oid, "owner-a")
    assert {step["name"] for step in state["steps"]} == {"local", "provider.crm"}


@pytest.mark.asyncio
async def test_provider_claim_requires_confirmed_local_stage(pg_pool, sample_org):
    org = sample_org["id"]
    oid, _ = await make_operation(pg_pool, org)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """UPDATE gdpr_operations SET status='partial'
               WHERE organization_id=$1 AND id=$2""", org, oid,
        )
        await conn.execute(
            """INSERT INTO gdpr_operation_steps(organization_id,operation_id,name,status)
               VALUES($1,$2,'provider.crm','pending')""", org, oid,
        )
    with pytest.raises(OperationUnavailable):
        await Operations(pg_pool).claim_provider(org, oid, "provider.crm")


@pytest.mark.asyncio
async def test_inventory_review_is_not_a_dispatchable_stage(pg_pool, sample_org):
    org = sample_org["id"]
    oid, _ = await make_operation(pg_pool, org)
    await authorize_stage(pg_pool, org, oid, provider="provider.inventory_review")
    assert await Operations(pg_pool).claim_provider(org, oid, "provider.inventory_review") is None


@pytest.mark.asyncio
async def test_expired_claim_is_fenced_uncertain_and_verified_reconciliation_persists(
    pg_pool, sample_org,
):
    org = sample_org["id"]
    oid, _ = await make_operation(pg_pool, org)
    await authorize_stage(pg_pool, org, oid)
    ops = Operations(pg_pool)
    fence = await ops.claim_provider(org, oid, "provider.crm")
    assert fence is not None

    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE gdpr_operation_steps SET lease_until=NOW()-INTERVAL '1 second' "
            "WHERE organization_id=$1 AND operation_id=$2 AND name=$3",
            org, oid, "provider.crm",
        )
    assert await ops.claim_provider(org, oid, "provider.crm") is None

    uncertain = await Operations(pg_pool).get(org, oid, "owner-a")
    step = next(s for s in uncertain["steps"] if s["name"] == "provider.crm")
    assert step["status"] == "uncertain"
    assert step["fence"] == fence + 1
    with pytest.raises(RequestConflict):
        await ops.provider_result(org, oid, "provider.crm", fence, receipt={
            "http_status": 200,
        })

    receipt = {
        "operation_id": str(oid),
        "organization_id": str(org),
        "provider": "provider.crm",
        "status": "deleted",
        "evidence_digest": hashlib.sha256(b"synthetic verified receipt").hexdigest(),
    }
    with pytest.raises(ValueError):
        await ops.reconcile_provider(org, oid, "provider.crm", receipt={"status": "deleted"})
    await ops.reconcile_provider(org, oid, "provider.crm", receipt=receipt)

    persisted = await Operations(pg_pool).get(org, oid, "owner-a")
    step = next(s for s in persisted["steps"] if s["name"] == "provider.crm")
    assert step["status"] == "confirmed"
    assert step["fence"] == fence + 2


@pytest.mark.asyncio
async def test_bare_provider_success_is_not_deletion_confirmation(pg_pool, sample_org):
    org = sample_org["id"]
    oid, _ = await make_operation(pg_pool, org)
    await authorize_stage(pg_pool, org, oid)
    ops = Operations(pg_pool)
    fence = await ops.claim_provider(org, oid, "provider.crm")

    await ops.provider_result(org, oid, "provider.crm", fence, receipt=None)
    assert not await org_step_confirmed(pg_pool, org, oid)


@pytest.mark.asyncio
async def test_migration_replay_acl_and_immutable_evidence_contract(pg_pool, sample_org):
    org = sample_org["id"]
    oid, _ = await make_operation(pg_pool, org)
    migration = Path("src/core/db/migrations/063_gdpr_operations.sql").read_text(encoding="utf-8")
    async with pg_pool.acquire() as conn:
        await conn.execute(migration)
        await conn.execute(migration)

        assert await conn.fetchval("SELECT has_table_privilege('anon','gdpr_operations','SELECT')") is False
        assert await conn.fetchval("SELECT has_table_privilege('authenticated','gdpr_export_chunks','INSERT')") is False
        assert await conn.fetchval("SELECT has_table_privilege('service_role','gdpr_operations','SELECT')") is True
        assert await conn.fetchval("SELECT has_table_privilege('service_role','gdpr_export_chunks','DELETE')") is True
        assert await conn.fetchval("SELECT has_table_privilege('service_role','gdpr_policies','INSERT')") is False
        assert await conn.fetchval("SELECT has_table_privilege('service_role','gdpr_operation_events','UPDATE')") is False

        event_id = await conn.fetchval(
            "SELECT id FROM gdpr_operation_events WHERE organization_id=$1 AND operation_id=$2 LIMIT 1",
            org, oid,
        )
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                "UPDATE gdpr_operation_events SET details='{}'::jsonb WHERE id=$1", event_id,
            )
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute("DELETE FROM gdpr_operation_events WHERE id=$1", event_id)


async def org_step_confirmed(pool, org, oid):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT status='confirmed' FROM gdpr_operation_steps "
            "WHERE organization_id=$1 AND operation_id=$2 AND name='provider.crm'",
            org, oid,
        )
