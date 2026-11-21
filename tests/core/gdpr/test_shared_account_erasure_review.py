"""Independent controller review only, with real shared account relationships."""

from uuid import UUID, uuid4

import pytest

from src.core.gdpr.account_erasure_review import review_account_erasure
from src.core.gdpr.deletion import Deletion
from src.core.gdpr.restore_checkpoint import RestoreBlocked


@pytest.mark.asyncio
async def test_account_review_is_exact_owner_only_and_reports_shared_dependencies(pg_pool, sample_org, other_org):
    org, other = sample_org["id"], other_org["id"]
    auth, contact = uuid4(), uuid4()
    await pg_pool.execute("INSERT INTO auth.users(id,email) VALUES($1,$2)", auth, f"{auth}@test.invalid")
    profile = await pg_pool.fetchval("SELECT id FROM user_profiles WHERE auth_user_id=$1", auth)
    await pg_pool.executemany(
        "INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) VALUES($1,$2,$3,now())",
        [(org, profile, "owner"), (other, profile, "staff")],
    )
    await pg_pool.execute("INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
                          contact, other, str(contact))
    await pg_pool.execute("""INSERT INTO conversations(id,organization_id,contact_id,assigned_to)
        VALUES($1,$2,$3,$4)""", uuid4(), other, contact, profile)
    await pg_pool.execute("""INSERT INTO audit_log(organization_id,user_id,action)
        VALUES($1,$2,'synthetic')""", other, profile)
    request = await Deletion(pg_pool).request(org, auth, uuid4(), scope="account")
    oid = UUID(str(request["id"]))
    assert request["status"] == "blocked"
    async with pg_pool.acquire() as conn:
        with pytest.raises(RestoreBlocked, match="owner snapshot"):
            await review_account_erasure(conn, org, oid, auth)
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            result = await review_account_erasure(conn, org, oid, auth)
            assert result["state"] == "controller_review_required"
            assert result["deletion_authorized"] is False
            assert set(result) == {"state", "blockers", "deletion_authorized"}
            assert str(other) not in str(result)
            assert {"shared_tenant_memberships_review_required", "tenant_owner_transfer_required",
                    "conversation_assignment_disposition_required", "audit_retention_disposition_required",
                    "auth_provider_identity_and_receipt_required"} <= set(result["blockers"])
            for args in ((other, oid, auth), (org, oid, uuid4()), (org, uuid4(), auth)):
                with pytest.raises(RestoreBlocked, match="scope unavailable"):
                    await review_account_erasure(conn, *args)
            await conn.execute("SET LOCAL ROLE service_role")
            with pytest.raises(RestoreBlocked, match="owner snapshot"):
                await review_account_erasure(conn, org, oid, auth)
    assert await pg_pool.fetchval("SELECT EXISTS(SELECT FROM auth.users WHERE id=$1)", auth)
    assert await pg_pool.fetchval("SELECT count(*) FROM organization_memberships WHERE user_id=$1", profile) == 2
    assert await pg_pool.fetchval("SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1", org) == 0
