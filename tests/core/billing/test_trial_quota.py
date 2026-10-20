import asyncio
import uuid

import pytest

from src.core.billing.plans import TRIAL_MESSAGES_LIMIT


pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


async def _seed_auth_profile(pg_pool):
    auth_id = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute("INSERT INTO auth.users (id, email) VALUES ($1, $2)", auth_id, f"{auth_id}@example.test")
    return auth_id


@pytest.mark.parametrize("oauth", [False, True])
async def test_both_signup_paths_start_with_seven_days_and_150_messages(repo, pg_pool, oauth):
    auth_id = await _seed_auth_profile(pg_pool)
    method = repo.get_or_create_organization_with_owner if oauth else repo.create_organization_with_owner
    created = await method(str(auth_id), "Trial Test")
    async with pg_pool.acquire() as conn:
        org = await conn.fetchrow(
            "SELECT subscription_status, trial_start, trial_end, messages_limit, messages_used_this_period "
            "FROM organizations WHERE id = $1::uuid",
            created["organization_id"],
        )
    assert org["subscription_status"] == "trialing"
    assert org["messages_limit"] == TRIAL_MESSAGES_LIMIT == 150
    assert org["messages_used_this_period"] == 0
    assert (org["trial_end"] - org["trial_start"]).days == 7


async def test_inbound_processing_claim_is_tenant_scoped_and_does_not_precharge(pg_pool):
    # Message ownership/worker fencing happens before A04 admission. Claiming
    # work does not spend the commercial reply quota; QuotaReservations owns
    # the last-unit behavior in test_quota_reservation_lifecycle.py.
    from src.whatsapp.repository import Repository as WhatsAppRepository
    message_repo = WhatsAppRepository(pool=pg_pool)
    org_ids = [uuid.uuid4(), uuid.uuid4()]
    messages = []
    async with pg_pool.acquire() as conn:
        for index, org_id in enumerate(org_ids):
            await conn.execute(
                "INSERT INTO organizations (id, name, subscription_status, trial_start, trial_end, "
                "messages_limit, messages_used_this_period) "
                "VALUES ($1, $2, 'trialing', now(), now() + interval '7 days', 150, $3)",
                org_id, f"Trial {index}", 149 if index == 0 else 0,
            )
            contact_id, conversation_id = uuid.uuid4(), uuid.uuid4()
            await conn.execute(
                "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, $3)",
                contact_id, org_id, f"test-{index}",
            )
            await conn.execute(
                "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3)",
                conversation_id, org_id, contact_id,
            )
            ids = [uuid.uuid4() for _ in range(2 if index == 0 else 1)]
            for message_id in ids:
                await conn.execute(
                    "INSERT INTO messages (id, organization_id, conversation_id, direction, message_type, content, status) "
                    "VALUES ($1, $2, $3, 'inbound', 'text', '{}'::jsonb, 'received_pending_ai')",
                    message_id, org_id, conversation_id,
                )
            messages.append(ids)

    first_org = str(org_ids[0])
    results = await asyncio.gather(*(
        message_repo.claim_message_for_processing(str(message_id), first_org)
        for message_id in messages[0]
    ))
    assert [result["status"] for result in results] == ["claimed", "claimed"]
    winner = messages[0][0]
    assert (await message_repo.claim_message_for_processing(str(winner), first_org))["status"] == "currently_processing"
    assert (await message_repo.claim_message_for_processing(str(winner), str(org_ids[1])))["status"] == "not_found"
    assert (await message_repo.claim_message_for_processing(str(messages[1][0]), str(org_ids[1])))["status"] == "claimed"

    async with pg_pool.acquire() as conn:
        usage = await conn.fetch("SELECT id, messages_used_this_period FROM organizations WHERE id = ANY($1::uuid[])", org_ids)
    assert {row["id"]: row["messages_used_this_period"] for row in usage} == {
        org_ids[0]: 149,
        org_ids[1]: 0,
    }
