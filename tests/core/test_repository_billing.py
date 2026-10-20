import uuid
import pytest
from datetime import datetime, timezone

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


async def test_get_organization_billing_defaults(repo, sample_org):
    result = await repo.get_organization_billing(sample_org["id"])
    assert result["subscription_status"] == "incomplete"
    assert result["messages_used_this_period"] == 0
    assert result["messages_limit"] is None
    assert result["plan"] is None


async def test_update_organization_billing(repo, sample_org):
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test123",
        "subscription_id": "sub_test123",
        "subscription_status": "trialing",
        "plan": "starter",
    })
    result = await repo.get_organization_billing(sample_org["id"])
    assert result["stripe_customer_id"] == "cus_test123"
    assert result["subscription_status"] == "trialing"
    assert result["plan"] == "starter"


async def test_set_subscription_status(repo, sample_org):
    await repo.set_subscription_status(sample_org["id"], "active")
    result = await repo.get_organization_billing(sample_org["id"])
    assert result["subscription_status"] == "active"


async def test_increment_message_usage_is_fenced(repo, sample_org):
    with pytest.raises(RuntimeError, match="atomic A04 commit"):
        await repo.increment_message_usage(sample_org["id"])
    result = await repo.get_organization_billing(sample_org["id"])
    assert result["messages_used_this_period"] == 0


async def test_reset_message_usage(repo, sample_org):
    await repo.pool.execute(
        "UPDATE organizations SET messages_used_this_period=2 WHERE id=$1", sample_org["id"]
    )
    period_start = datetime.now(timezone.utc)
    period_end = datetime.now(timezone.utc)
    await repo.reset_message_usage(sample_org["id"], period_start, period_end)
    result = await repo.get_organization_billing(sample_org["id"])
    assert result["messages_used_this_period"] == 0
    assert result["current_period_start"] is not None


async def test_process_stripe_event_new(repo, sample_org):
    result = await repo.process_stripe_event("evt_test_001", sample_org["id"])
    assert result is True


async def test_process_stripe_event_duplicate(repo, sample_org):
    await repo.process_stripe_event("evt_test_002", sample_org["id"])
    result = await repo.process_stripe_event("evt_test_002", sample_org["id"])
    assert result is False


async def test_update_plan_limits_sets_correct_values(repo, sample_org):
    await repo.update_plan_limits(sample_org["id"], "pro")
    result = await repo.get_organization_billing(sample_org["id"])
    assert result["plan"] == "pro"
    assert result["messages_limit"] == 2000
    assert result["users_limit"] == 3


async def test_update_plan_limits_business_unlimited(repo, sample_org):
    await repo.update_plan_limits(sample_org["id"], "business")
    result = await repo.get_organization_billing(sample_org["id"])
    assert result["plan"] == "business"
    assert result["messages_limit"] == 10000


async def test_increment_message_usage_cross_tenant_writer_is_fenced(repo, sample_org, other_org):
    for org in (sample_org, other_org):
        with pytest.raises(RuntimeError, match="atomic A04 commit"):
            await repo.increment_message_usage(org["id"])
    counts = await repo.pool.fetch(
        "SELECT messages_used_this_period FROM organizations WHERE id=ANY($1::uuid[])",
        [sample_org["id"], other_org["id"]],
    )
    assert [row["messages_used_this_period"] for row in counts] == [0, 0]


async def test_all_repository_increment_entry_points_are_fenced(repo, sample_org):
    from src.core.db.repositories.billing_repo import BillingRepository
    from src.whatsapp.repository import Repository as WhatsAppRepository

    billing = BillingRepository(repo.pool)
    whatsapp = WhatsAppRepository(repo.pool)
    writers = (
        lambda: billing.increment_message_usage(sample_org["id"]),
        lambda: billing._increment_message_usage(None, sample_org["id"]),
        lambda: whatsapp.increment_message_usage(sample_org["id"]),
        lambda: whatsapp._increment_message_usage(None, sample_org["id"]),
        lambda: whatsapp.billing_repo.increment_message_usage(sample_org["id"]),
    )
    for write in writers:
        with pytest.raises(RuntimeError, match="atomic A04 commit"):
            await write()
    assert await repo.pool.fetchval(
        "SELECT messages_used_this_period FROM organizations WHERE id=$1", sample_org["id"],
    ) == 0


async def test_get_organization_by_stripe_customer(repo, sample_org):
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_find_001",
    })
    result = await repo.get_organization_by_stripe_customer("cus_find_001")
    assert result is not None
    assert result["id"] == sample_org["id"]


async def test_get_organization_by_stripe_customer_not_found(repo):
    result = await repo.get_organization_by_stripe_customer("cus_nonexistent")
    assert result is None
