"""PG16 integration contract for the inactive quota reservation lifecycle.

Uses only synthetic rows in pg_pool's disposable schema. This module never
uses reset_db, because that fixture truncates the shared quota trust marker.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.core.billing.quota_reservations import (
    QuotaConflict,
    QuotaOperation,
    QuotaReservations,
    validate_final_response,
)
from src.core.billing.suspension import AIGenerationBlocked
from src.models.schemas import RispostaOutput


async def _organization(pool, *, used=0, limit=150, status="trialing", period_end=None):
    async with pool.acquire() as conn:
        now = await conn.fetchval("SELECT clock_timestamp()")
        anchor = now - timedelta(minutes=1)
        trial_end = period_end or anchor + timedelta(days=7)
        current_end = anchor + timedelta(days=30)
        org_id = uuid4()
        subscription_id = "sub_test123" if status == "active" else None
        await conn.execute(
            """INSERT INTO public.organizations
                   (id,name,subscription_status,subscription_id,trial_start,trial_end,
                    current_period_start,current_period_end,messages_limit,
                    messages_used_this_period,ai_accounting_blocked)
                 VALUES ($1,'Synthetic quota reservation',$2,$3,$4,$5,$4,$6,$7,$8,FALSE)""",
            org_id, status, subscription_id, anchor, trial_end, current_end, limit, used,
        )
        return {"id": org_id, "anchor": anchor, "trial_end": trial_end,
                "current_end": current_end, "now": now}


async def _auth_member(pool, org_id, *, role="staff"):
    auth_user_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO auth.users (id,email) VALUES ($1,$2)",
            auth_user_id, f"{auth_user_id}@quota-test.invalid",
        )
        profile_id = await conn.fetchval(
            "SELECT id FROM user_profiles WHERE auth_user_id=$1", auth_user_id,
        )
        await conn.execute(
            "INSERT INTO organization_memberships (organization_id,user_id,ruolo) VALUES ($1,$2,$3)",
            org_id, profile_id, role,
        )
    return auth_user_id


def _operation(org_id, *, source_id=None, actor_id, channel="simulator", content="same payload"):
    return QuotaOperation(
        organization_id=org_id,
        source_id=source_id or uuid4(),
        channel=channel,
        payload_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        auth_user_id=None if channel == "inbound" else actor_id or uuid4(),
    )


def _final(operation, ticket, text="Siamo aperti oggi."):
    return validate_final_response(
        operation,
        ticket,
        RispostaOutput(risposta=text, richiede_umano=False, motivo="", categoria="generico"),
    )


async def _used(pool, org_id):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT messages_used_this_period FROM organizations WHERE id=$1", org_id,
        )


async def _reservation_metadata(pool, org_id, operation_id):
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT metadata FROM usage_events WHERE organization_id=$1 AND id=$2",
            org_id, operation_id,
        )
        return json.loads(value) if isinstance(value, str) else value


async def _inbound_message(
    pool, org_id, *, direction="inbound", status="processing", deleted_at=None,
    sent_at=None, replied_at=None, billed_at=None, ai_reply_cache=None,
    quota_exceeded_at=None,
):
    contact_id, conversation_id, message_id = uuid4(), uuid4(), uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts (id,organization_id,phone_number) VALUES ($1,$2,$3)",
            contact_id, org_id, f"+39{contact_id.int % 10**10:010d}",
        )
        await conn.execute(
            "INSERT INTO conversations (id,organization_id,contact_id) VALUES ($1,$2,$3)",
            conversation_id, org_id, contact_id,
        )
        await conn.execute(
            """INSERT INTO messages
                   (id,organization_id,conversation_id,direction,message_type,content,status)
                 VALUES ($1,$2,$3,$4,'text','{}'::jsonb,$5)""",
            message_id, org_id, conversation_id, direction, status,
        )
        await conn.execute(
            """UPDATE messages SET deleted_at=$2,sent_at=$3,replied_at=$4,billed_at=$5,
                       ai_reply_cache=$6::jsonb,quota_exceeded_at=$7 WHERE id=$1""",
            message_id, deleted_at, sent_at, replied_at, billed_at,
            json.dumps(ai_reply_cache) if ai_reply_cache is not None else None, quota_exceeded_at,
        )
    return message_id


@pytest.mark.asyncio
async def test_unique_concurrent_reservations_preserve_last_unit_and_commit_once(pg_pool):
    # The stored trial limit is deliberately inconsistent; the protocol must
    # apply its hard 150 cap without changing the stored billing field.
    org = await _organization(pg_pool, used=149, limit=500)
    service = QuotaReservations(pg_pool)
    operations = [
        _operation(org["id"], source_id=uuid4(), actor_id=await _auth_member(pg_pool, org["id"]),
                   content=f"request {i}")
        for i in range(20)
    ]

    results = await asyncio.gather(*(service.reserve(operation) for operation in operations))
    admitted = [(operation, result) for operation, result in zip(operations, results)
                if result["status"] == "reserved"]
    assert len(admitted) == 1
    assert sum(result["status"] == "quota_exceeded" for result in results) == 19
    assert await _used(pg_pool, org["id"]) == 149

    operation, reservation = admitted[0]
    response = await service.commit(operation, reservation["ticket"], _final(operation, reservation["ticket"]))
    assert response["risposta"] == "Siamo aperti oggi."
    assert await _used(pg_pool, org["id"]) == 150
    assert await pg_pool.fetchval(
        "SELECT messages_limit FROM organizations WHERE id=$1", org["id"],
    ) == 500

    replayed = await asyncio.gather(*(service.reserve(operation) for _ in range(20)))
    assert all(result == {"status": "replay", "response": response} for result in replayed)
    assert await _used(pg_pool, org["id"]) == 150
    assert await service.release(operation, reservation["ticket"]) is False
    assert await _used(pg_pool, org["id"]) == 150

    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET messages_used_this_period=151 WHERE id=$1", org["id"],
        )
    with pytest.raises(AIGenerationBlocked):
        await service.reserve(_operation(org["id"], actor_id=await _auth_member(pg_pool, org["id"])))


@pytest.mark.asyncio
async def test_paid_upgrade_increases_admission_without_resetting_consumed_usage(pg_pool):
    org = await _organization(pg_pool, status="active", limit=1)
    service = QuotaReservations(pg_pool)
    first_actor = await _auth_member(pg_pool, org["id"])
    second_actor = await _auth_member(pg_pool, org["id"])
    first = _operation(org["id"], actor_id=first_actor, content="upgrade first")
    second = _operation(org["id"], actor_id=second_actor, content="upgrade second")

    first_reservation = await service.reserve(first)
    assert first_reservation["status"] == "reserved"
    assert (await service.reserve(second))["status"] == "quota_exceeded"
    async with pg_pool.acquire() as conn:
        await conn.execute("UPDATE organizations SET messages_limit=2 WHERE id=$1", org["id"])
    second_reservation = await service.reserve(second)
    assert second_reservation["status"] == "reserved"

    await service.commit(first, first_reservation["ticket"], _final(first, first_reservation["ticket"]))
    await service.commit(second, second_reservation["ticket"], _final(second, second_reservation["ticket"]))
    assert await _used(pg_pool, org["id"]) == 2
    assert await pg_pool.fetchval(
        "SELECT messages_limit FROM organizations WHERE id=$1", org["id"],
    ) == 2


@pytest.mark.asyncio
async def test_paid_period_renewal_fences_pending_but_replays_old_commit_without_rebilling(pg_pool):
    org = await _organization(pg_pool, status="active")
    service = QuotaReservations(pg_pool)
    committed_actor = await _auth_member(pg_pool, org["id"])
    pending_actor = await _auth_member(pg_pool, org["id"])
    committed_op = _operation(org["id"], actor_id=committed_actor, content="old committed")
    pending_op = _operation(org["id"], actor_id=pending_actor, content="old pending")

    committed_reservation = await service.reserve(committed_op)
    committed_final = _final(committed_op, committed_reservation["ticket"])
    original_response = await service.commit(
        committed_op, committed_reservation["ticket"], committed_final,
    )
    pending_reservation = await service.reserve(pending_op)
    assert pending_reservation["status"] == "reserved"

    async with pg_pool.acquire() as conn:
        new_start = await conn.fetchval("SELECT clock_timestamp()")
        await conn.execute(
            "UPDATE organizations SET current_period_start=$2,current_period_end=$3,"
            "messages_used_this_period=0 WHERE id=$1",
            org["id"], new_start, new_start + timedelta(days=30),
        )

    with pytest.raises(AIGenerationBlocked):
        await service.commit(
            pending_op, pending_reservation["ticket"],
            _final(pending_op, pending_reservation["ticket"]),
        )
    assert await service.reserve(committed_op) == {"status": "replay", "response": original_response}
    assert await _used(pg_pool, org["id"]) == 0

    new_actor = await _auth_member(pg_pool, org["id"])
    new_op = _operation(org["id"], actor_id=new_actor, content="new billing period")
    new_reservation = await service.reserve(new_op)
    assert new_reservation["status"] == "reserved"
    await service.commit(new_op, new_reservation["ticket"], _final(new_op, new_reservation["ticket"]))
    assert await _used(pg_pool, org["id"]) == 1


@pytest.mark.asyncio
async def test_inbound_requires_unbilled_processing_message_and_commits_cache_with_counter(pg_pool):
    org = await _organization(pg_pool, used=149)
    service = QuotaReservations(pg_pool)
    message_id = await _inbound_message(pg_pool, org["id"])
    operation = _operation(org["id"], source_id=message_id, channel="inbound", actor_id=None)

    reserved = await service.reserve(operation)
    assert reserved["status"] == "reserved"
    final = _final(operation, reserved["ticket"], "Risposta inbound autorizzata.")
    committed = await service.commit(operation, reserved["ticket"], final)
    assert committed["risposta"] == "Risposta inbound autorizzata."
    assert await _used(pg_pool, org["id"]) == 150
    stored = await pg_pool.fetchrow(
        "SELECT status,response FROM simulation_requests WHERE organization_id=$1 AND request_id=$2",
        org["id"], operation.id,
    )
    message = await pg_pool.fetchrow(
        "SELECT status,ai_reply_cache,ai_reply_generated_at,billed_at FROM messages WHERE id=$1",
        message_id,
    )
    stored_response = json.loads(stored["response"]) if isinstance(stored["response"], str) else stored["response"]
    cached_response = json.loads(message["ai_reply_cache"]) if isinstance(message["ai_reply_cache"], str) else message["ai_reply_cache"]
    assert stored["status"] == "completed" and stored_response == committed
    assert cached_response == committed
    assert message["billed_at"] is not None and message["ai_reply_generated_at"] is not None
    first_billed_at = message["billed_at"]

    assert await service.reserve(operation) == {"status": "replay", "response": committed}
    assert await service.commit(operation, reserved["ticket"], final) == committed
    assert await _used(pg_pool, org["id"]) == 150
    assert await pg_pool.fetchval("SELECT billed_at FROM messages WHERE id=$1", message_id) == first_billed_at


@pytest.mark.asyncio
async def test_inbound_rejects_wrong_tenant_direction_deleted_sent_replied_and_legacy_marks(pg_pool):
    org = await _organization(pg_pool)
    other_org = await _organization(pg_pool)
    service = QuotaReservations(pg_pool)
    now = datetime.now(timezone.utc)
    messages = [
        (org["id"], await _inbound_message(pg_pool, org["id"], direction="outbound")),
        (org["id"], await _inbound_message(pg_pool, org["id"], deleted_at=now)),
        (org["id"], await _inbound_message(pg_pool, org["id"], sent_at=now)),
        (org["id"], await _inbound_message(pg_pool, org["id"], replied_at=now)),
        (org["id"], await _inbound_message(pg_pool, org["id"], billed_at=now)),
        (org["id"], await _inbound_message(pg_pool, org["id"], ai_reply_cache={"legacy": True})),
        (org["id"], await _inbound_message(pg_pool, org["id"], quota_exceeded_at=now)),
    ]
    wrong_tenant_message = await _inbound_message(pg_pool, org["id"])
    for organization_id, message_id in [*messages, (other_org["id"], wrong_tenant_message)]:
        operation = _operation(
            organization_id, source_id=message_id, channel="inbound", actor_id=None,
        )
        with pytest.raises(QuotaConflict):
            await service.reserve(operation)


@pytest.mark.parametrize("fault_point", ["counter", "ledger"])
@pytest.mark.asyncio
async def test_inbound_commit_failure_rolls_back_response_cache_and_counter_then_release_retry(pg_pool, fault_point):
    import asyncpg

    org = await _organization(pg_pool)
    service = QuotaReservations(pg_pool)
    message_id = await _inbound_message(pg_pool, org["id"])
    operation = _operation(org["id"], source_id=message_id, channel="inbound", actor_id=None)
    reservation = await service.reserve(operation)
    final = _final(operation, reservation["ticket"], "Transazione atomica.")
    suffix = uuid4().hex
    trigger_name = f"quota_inject_{suffix}"
    function_name = f"quota_inject_fn_{suffix}"
    table = "organizations" if fault_point == "counter" else "usage_events"
    update_column = "messages_used_this_period" if fault_point == "counter" else "metadata"
    target_id = org["id"] if fault_point == "counter" else operation.id
    async with pg_pool.acquire() as conn:
        await conn.execute(
            f"""CREATE FUNCTION public.{function_name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.id = '{target_id}'::uuid THEN
                        RAISE EXCEPTION 'synthetic quota counter failure';
                    END IF;
                    RETURN NEW;
                END $$"""
        )
        await conn.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE UPDATE OF {update_column} "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION public.{function_name}()"
        )

    try:
        with pytest.raises(asyncpg.PostgresError, match="synthetic quota counter failure"):
            await service.commit(operation, reservation["ticket"], final)
        assert await _used(pg_pool, org["id"]) == 0
        message = await pg_pool.fetchrow(
            "SELECT ai_reply_cache,ai_reply_generated_at,billed_at FROM messages WHERE id=$1",
            message_id,
        )
        assert all(message[field] is None for field in
                   ("ai_reply_cache", "ai_reply_generated_at", "billed_at"))
        request = await pg_pool.fetchrow(
            "SELECT status,response FROM simulation_requests WHERE organization_id=$1 AND request_id=$2",
            org["id"], operation.id,
        )
        assert request["status"] == "reserved" and request["response"] is None
        assert (await _reservation_metadata(pg_pool, org["id"], operation.id))["state"] == "reserved"
    finally:
        async with pg_pool.acquire() as conn:
            await conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table}")
            await conn.execute(f"DROP FUNCTION IF EXISTS public.{function_name}()")

    assert await service.release(operation, reservation["ticket"]) is True
    retry = await service.reserve(operation)
    assert retry["status"] == "reserved"
    assert retry["ticket"].claim_id != reservation["ticket"].claim_id
    retry_final = _final(operation, retry["ticket"], "Transazione atomica.")
    retry_response = await service.commit(operation, retry["ticket"], retry_final)
    assert retry_response["risposta"] == "Transazione atomica."
    assert await _used(pg_pool, org["id"]) == 1
    assert (await pg_pool.fetchrow(
        "SELECT billed_at,ai_reply_cache FROM messages WHERE id=$1", message_id,
    ))["billed_at"] is not None


@pytest.mark.asyncio
async def test_concurrent_commit_replay_increments_once(pg_pool):
    org = await _organization(pg_pool)
    service = QuotaReservations(pg_pool)
    operation = _operation(org["id"], actor_id=await _auth_member(pg_pool, org["id"]))
    reservation = await service.reserve(operation)
    final = _final(operation, reservation["ticket"])

    responses = await asyncio.gather(*(
        service.commit(operation, reservation["ticket"], final) for _ in range(20)
    ))
    assert all(response == responses[0] for response in responses)
    assert await _used(pg_pool, org["id"]) == 1
    assert (await _reservation_metadata(pg_pool, org["id"], operation.id))["state"] == "committed"


@pytest.mark.asyncio
async def test_commit_release_race_has_one_fenced_terminal_state(pg_pool):
    org = await _organization(pg_pool)
    service = QuotaReservations(pg_pool)
    operation = _operation(org["id"], actor_id=await _auth_member(pg_pool, org["id"]))
    reservation = await service.reserve(operation)
    ticket = reservation["ticket"]
    final = _final(operation, ticket)

    committed, released = await asyncio.gather(
        service.commit(operation, ticket, final),
        service.release(operation, ticket),
        return_exceptions=True,
    )
    used = await _used(pg_pool, org["id"])
    state = (await _reservation_metadata(pg_pool, org["id"], operation.id))["state"]
    if state == "committed":
        assert used == 1
        assert committed == json.loads(final.serialized)
        assert released is False
        assert await service.commit(operation, ticket, final) == committed
    else:
        assert state == "released"
        assert used == 0
        assert isinstance(committed, QuotaConflict)
        assert released is True
        with pytest.raises(QuotaConflict):
            await service.commit(operation, ticket, final)


@pytest.mark.asyncio
async def test_same_operation_claim_conflicts_scope_and_cached_response_tamper(pg_pool):
    org = await _organization(pg_pool)
    other_org = await _organization(pg_pool)
    service = QuotaReservations(pg_pool)
    source_id = uuid4()
    actor_id = await _auth_member(pg_pool, org["id"])
    operation = _operation(org["id"], source_id=source_id, actor_id=actor_id)

    results = await asyncio.gather(*(service.reserve(operation) for _ in range(20)))
    admitted = [result for result in results if result["status"] == "reserved"]
    assert len(admitted) == 1
    assert sum(result["status"] == "in_progress" for result in results) == 19
    ticket = admitted[0]["ticket"]
    final = _final(operation, ticket)

    conflict = _operation(org["id"], source_id=source_id, actor_id=actor_id, content="changed payload")
    with pytest.raises(QuotaConflict):
        await service.reserve(conflict)

    wrong_actor = _operation(org["id"], source_id=source_id,
                             actor_id=await _auth_member(pg_pool, org["id"]))
    wrong_org = _operation(other_org["id"], source_id=source_id, actor_id=actor_id)
    for wrong_operation in (wrong_actor, wrong_org):
        with pytest.raises(QuotaConflict):
            await service.commit(wrong_operation, ticket, final)

    await service.commit(operation, ticket, final)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """UPDATE simulation_requests SET response='{"risposta":"tampered"}'::jsonb
                 WHERE organization_id=$1 AND request_id=$2""",
            org["id"], operation.id,
        )
    with pytest.raises(QuotaConflict):
        await service.reserve(operation)
    assert await _used(pg_pool, org["id"]) == 1
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM simulation_requests WHERE organization_id=$1 AND request_id=$2",
            org["id"], operation.id,
        )
    with pytest.raises(QuotaConflict):
        await service.reserve(operation)
    with pytest.raises(QuotaConflict):
        await service.commit(operation, ticket, final)
    assert await _used(pg_pool, org["id"]) == 1


@pytest.mark.asyncio
async def test_release_fences_old_claim_retry_commits_once_and_never_refunds(pg_pool):
    org = await _organization(pg_pool, used=149)
    service = QuotaReservations(pg_pool)
    operation = _operation(org["id"], actor_id=await _auth_member(pg_pool, org["id"]))
    first = await service.reserve(operation)
    old_ticket = first["ticket"]

    assert await service.release(operation, old_ticket) is True
    assert await service.release(operation, old_ticket) is True
    assert await _used(pg_pool, org["id"]) == 149
    with pytest.raises(QuotaConflict):
        await service.commit(operation, old_ticket, _final(operation, old_ticket))

    retry = await service.reserve(operation)
    assert retry["status"] == "reserved"
    assert retry["ticket"].claim_id != old_ticket.claim_id
    stale_final = _final(operation, old_ticket, "Risposta verificata.")
    with pytest.raises(QuotaConflict):
        await service.commit(operation, retry["ticket"], stale_final)
    assert await _used(pg_pool, org["id"]) == 149
    retry_final = _final(operation, retry["ticket"], "Risposta verificata.")
    await service.commit(operation, retry["ticket"], retry_final)
    assert await _used(pg_pool, org["id"]) == 150
    # A delivery failure occurs after persistence and therefore cannot refund.
    assert await service.release(operation, retry["ticket"]) is False
    assert await _used(pg_pool, org["id"]) == 150


@pytest.mark.asyncio
async def test_revocation_and_membership_removal_block_commit_and_renew_but_allow_fenced_release(pg_pool):
    org = await _organization(pg_pool)
    service = QuotaReservations(pg_pool)

    revoked_actor = await _auth_member(pg_pool, org["id"])
    revoked_op = _operation(org["id"], actor_id=revoked_actor, content="revoked user")
    revoked_reservation = await service.reserve(revoked_op)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE auth_access_lifecycle SET disabled_at=clock_timestamp() WHERE auth_user_id=$1",
            revoked_actor,
        )
    with pytest.raises(AIGenerationBlocked):
        await service.commit(revoked_op, revoked_reservation["ticket"],
                             _final(revoked_op, revoked_reservation["ticket"]))
    with pytest.raises(AIGenerationBlocked):
        await service.renew(revoked_op, revoked_reservation["ticket"])
    assert await service.release(revoked_op, revoked_reservation["ticket"]) is True

    removed_actor = await _auth_member(pg_pool, org["id"])
    removed_op = _operation(org["id"], actor_id=removed_actor, content="removed member")
    removed_reservation = await service.reserve(removed_op)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM organization_memberships om USING user_profiles up "
            "WHERE om.user_id=up.id AND up.auth_user_id=$1 AND om.organization_id=$2",
            removed_actor, org["id"],
        )
    with pytest.raises(AIGenerationBlocked):
        await service.commit(removed_op, removed_reservation["ticket"],
                             _final(removed_op, removed_reservation["ticket"]))
    with pytest.raises(AIGenerationBlocked):
        await service.renew(removed_op, removed_reservation["ticket"])
    assert await service.release(removed_op, removed_reservation["ticket"]) is True


@pytest.mark.asyncio
async def test_expired_reservations_keep_capacity_and_commit_rechecks_entitlement_period(pg_pool):
    org = await _organization(pg_pool, used=149)
    service = QuotaReservations(pg_pool)
    operation = _operation(org["id"], actor_id=await _auth_member(pg_pool, org["id"]))
    await service.reserve(operation)
    metadata = await _reservation_metadata(pg_pool, org["id"], operation.id)
    now = datetime.now(timezone.utc)
    metadata["lease_start"] = (now - timedelta(seconds=60)).isoformat()
    metadata["lease_end"] = (now - timedelta(seconds=30)).isoformat()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE usage_events SET metadata=$3::jsonb WHERE organization_id=$1 AND id=$2",
            org["id"], operation.id, json.dumps(metadata),
        )

    assert (await service.reserve(operation))["status"] == "reconciliation_required"
    blocked = await service.reserve(_operation(
        org["id"], source_id=uuid4(), actor_id=await _auth_member(pg_pool, org["id"]),
    ))
    assert blocked["status"] == "quota_exceeded"
    # Release is an explicit DB-fenced proof of no persisted response. A newly
    # issued internal ticket represents the expired stored claim for this check.
    expired_ticket = service._ticket(operation, metadata)
    assert await service.release(operation, expired_ticket) is True
    with pytest.raises(QuotaConflict):
        await service.commit(operation, expired_ticket, _final(operation, expired_ticket))
    retried = await service.reserve(operation)
    assert retried["status"] == "reserved"
    assert retried["ticket"].claim_id != expired_ticket.claim_id

    # A shorter original billing deadline survives later period extension.
    short_end = datetime.now(timezone.utc) + timedelta(minutes=2)
    limited = await _organization(pg_pool, period_end=short_end)
    limited_operation = _operation(limited["id"], actor_id=await _auth_member(pg_pool, limited["id"]))
    limited_reservation = await service.reserve(limited_operation)
    limited_final = _final(limited_operation, limited_reservation["ticket"])
    original_end = (await _reservation_metadata(pg_pool, limited["id"], limited_operation.id))["period_end"]
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET trial_end=trial_end + interval '1 day' WHERE id=$1", limited["id"],
        )
    renewed = await service.renew(limited_operation, limited_reservation["ticket"])
    assert renewed.lease_end <= datetime.fromisoformat(original_end)
    # Response proof binds the claim and period, not the mutable lease deadline.
    await service.commit(limited_operation, renewed, limited_final)

    # Entitlement changes between reserve and commit/renew always deny access.
    cases = [
        ("trial_end", lambda case_org: datetime.now(timezone.utc) - timedelta(seconds=1)),
        ("subscription_status", lambda case_org: "canceled"),
        ("ai_accounting_blocked", lambda case_org: True),
        ("trial_start", lambda case_org: case_org["anchor"] - timedelta(seconds=1)),
    ]
    for index, (column, value_for_org) in enumerate(cases):
        case_org = await _organization(pg_pool)
        case_op = _operation(case_org["id"], actor_id=await _auth_member(pg_pool, case_org["id"]),
                             content=f"revalidate {index}")
        reservation = await service.reserve(case_op)
        value = value_for_org(case_org)
        async with pg_pool.acquire() as conn:
            await conn.execute(
                f"UPDATE organizations SET {column}=$2 WHERE id=$1", case_org["id"], value,
            )
        with pytest.raises(AIGenerationBlocked):
            await service.commit(case_op, reservation["ticket"], _final(case_op, reservation["ticket"]))
        with pytest.raises(AIGenerationBlocked):
            await service.renew(case_op, reservation["ticket"])

    paid = await _organization(pg_pool, status="active")
    paid_op = _operation(paid["id"], actor_id=await _auth_member(pg_pool, paid["id"]),
                         content="paid period")
    paid_reservation = await service.reserve(paid_op)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET subscription_id='sub_changed123' WHERE id=$1", paid["id"],
        )
    with pytest.raises(AIGenerationBlocked):
        await service.commit(paid_op, paid_reservation["ticket"],
                             _final(paid_op, paid_reservation["ticket"]))
    with pytest.raises(AIGenerationBlocked):
        await service.renew(paid_op, paid_reservation["ticket"])
