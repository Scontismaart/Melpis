"""PostgreSQL integration coverage for A04 simulator reservations.

The old repository pre-charge methods are fenced. Simulator admission, response
replay and commercial usage now share the same atomic quota protocol as other
answer-capable paths.
"""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest

from src.core.billing.quota_reservations import QuotaConflict, QuotaOperation, QuotaReservations, validate_final_response
from src.core.db.repositories.message_repo import MessageRepository
from src.core.db.scoping import TENANT_SCOPED_TABLES
from src.models.schemas import RispostaOutput


async def _organization(pool, *, used=0, limit=150):
    org_id = uuid4()
    async with pool.acquire() as conn:
        now = await conn.fetchval("SELECT clock_timestamp()")
        await conn.execute(
            """INSERT INTO organizations
                   (id,name,subscription_status,trial_start,trial_end,current_period_start,
                    current_period_end,messages_limit,messages_used_this_period,ai_accounting_blocked)
                 VALUES($1,'Simulator A04 test','trialing',$2,$3,$2,$4,$5,$6,FALSE)""",
            org_id, now, now + timedelta(days=7), now + timedelta(days=30), limit, used,
        )
        auth_id = uuid4()
        await conn.execute(
            "INSERT INTO auth.users(id,email) VALUES($1,$2)", auth_id, f"{auth_id}@quota-test.invalid",
        )
        profile_id = await conn.fetchval(
            "SELECT id FROM user_profiles WHERE auth_user_id=$1", auth_id,
        )
        await conn.execute(
            "INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at) "
            "VALUES($1,$2,'owner',now())", org_id, profile_id,
        )
    return org_id, auth_id


def _operation(org_id, actor_id, key="simulator request"):
    source_id = uuid4()
    return QuotaOperation(
        organization_id=org_id,
        source_id=source_id,
        channel="simulator",
        payload_hash=hashlib.sha256(key.encode()).hexdigest(),
        auth_user_id=actor_id,
    )


def _final(operation, ticket, text="Risposta testata"):
    return validate_final_response(
        operation,
        ticket,
        RispostaOutput(risposta=text, richiede_umano=False, motivo="", categoria="generico"),
    )


@pytest.mark.asyncio
async def test_simulator_reservation_commits_once_and_replays_durable_response(pg_pool):
    org_id, actor_id = await _organization(pg_pool)
    operation = _operation(org_id, actor_id)
    service = QuotaReservations(pg_pool)

    reservation = await service.reserve(operation)
    assert reservation["status"] == "reserved"
    ticket = reservation["ticket"]
    assert (await service.reserve(operation))["status"] == "in_progress"

    final = _final(operation, ticket)
    expected = json.loads(final.serialized)
    committed = await service.commit(operation, ticket, final)
    assert committed == expected
    assert await pg_pool.fetchval(
        "SELECT messages_used_this_period FROM organizations WHERE id=$1", org_id,
    ) == 1

    replay = await service.reserve(operation)
    assert replay == {"status": "replay", "response": expected}
    assert await service.commit(operation, ticket, final) == expected
    assert await pg_pool.fetchval(
        "SELECT messages_used_this_period FROM organizations WHERE id=$1", org_id,
    ) == 1
    persisted = await pg_pool.fetchrow(
        "SELECT status,response FROM simulation_requests WHERE organization_id=$1 "
        "AND auth_user_id=$2 AND request_id=$3",
        org_id, actor_id, operation.id,
    )
    value = json.loads(persisted["response"]) if isinstance(persisted["response"], str) else persisted["response"]
    assert persisted["status"] == "completed"
    assert value == expected


@pytest.mark.asyncio
async def test_simulator_reservations_share_last_unit_and_do_not_precharge(pg_pool):
    org_id, actor_id = await _organization(pg_pool, used=149)
    first = _operation(org_id, actor_id, "first synthetic request")
    second = _operation(org_id, actor_id, "second synthetic request")
    service = QuotaReservations(pg_pool)

    held = await service.reserve(first)
    assert held["status"] == "reserved"
    assert await service.reserve(second) == {"status": "quota_exceeded"}
    assert await pg_pool.fetchval(
        "SELECT messages_used_this_period FROM organizations WHERE id=$1", org_id,
    ) == 149

    await service.commit(first, held["ticket"], _final(first, held["ticket"]))
    assert await pg_pool.fetchval(
        "SELECT messages_used_this_period FROM organizations WHERE id=$1", org_id,
    ) == 150
    assert await service.reserve(second) == {"status": "quota_exceeded"}


@pytest.mark.asyncio
async def test_expired_simulator_claim_stays_held_for_reconciliation(pg_pool):
    org_id, actor_id = await _organization(pg_pool)
    operation = _operation(org_id, actor_id)
    service = QuotaReservations(pg_pool)
    reservation = await service.reserve(operation)
    ticket = reservation["ticket"]
    await pg_pool.execute(
        "UPDATE usage_events SET metadata=jsonb_set(jsonb_set(metadata,'{lease_start}',"
        "to_jsonb((now()-interval '20 seconds')::text)),'{lease_end}',"
        "to_jsonb((now()-interval '10 seconds')::text)) "
        "WHERE organization_id=$1 AND id=$2",
        org_id, operation.id,
    )

    assert await service.reserve(operation) == {"status": "reconciliation_required"}
    assert await pg_pool.fetchval(
        "SELECT messages_used_this_period FROM organizations WHERE id=$1", org_id,
    ) == 0
    with pytest.raises(QuotaConflict):
        await service.commit(operation, ticket, _final(operation, ticket))


@pytest.mark.asyncio
async def test_legacy_simulator_write_entry_points_are_fenced():
    repository = MessageRepository(None)
    org_id, actor_id, request_id, claim = uuid4(), uuid4(), uuid4(), uuid4()
    calls = (
        repository.reserve_simulation_request(org_id, actor_id, request_id, "a" * 64),
        repository.complete_simulation_request(org_id, actor_id, request_id, "a" * 64, claim, {"risposta": "x"}),
        repository.fail_simulation_request(org_id, actor_id, request_id, "a" * 64, claim),
    )
    for call in calls:
        with pytest.raises(RuntimeError, match="Legacy simulator"):
            await call
    assert "simulation_requests" in TENANT_SCOPED_TABLES
