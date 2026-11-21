import uuid

import pytest

from src.core.conversation_store import ConversationStore
from src.core.gdpr.operations import Operations
from src.core.billing.quota_reservations import QuotaReservations
from tests.core.test_quota_reservation_lifecycle import _organization, _auth_member, _operation, _final


@pytest.mark.asyncio
async def test_simulator_histories_follow_durable_tenant_erasure(
    pg_pool, sample_org, sample_contact, other_org,
):
    org = sample_org["id"]
    foreign = other_org["id"]
    key = f"{org}:staff:session"
    foreign_key = f"{foreign}:staff:session"
    first, second = ConversationStore(), ConversationStore()

    async def populate(store, owner, cache_key):
        history, snapshot = await store.tenant_history(pg_pool, owner, cache_key)
        assert history == []
        assert await store.add_tenant_history(
            pg_pool, owner, cache_key, snapshot, "synthetic text", "response",
        )
        return snapshot

    first_snapshot = await populate(first, org, key)
    stale_snapshot = await populate(second, org, key)
    await populate(second, foreign, foreign_key)
    second.aggiungi("demo:anonymous:session", "demo", "response")
    assert first_snapshot == stale_snapshot

    operation = await Operations(pg_pool).create(
        org, "synthetic", uuid.uuid4(), kind="erase", scope="contact",
        subject_id=sample_contact["id"],
    )
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))",
            str(org),
        )
        await conn.execute(
            """INSERT INTO gdpr_erasure_barriers(
                   organization_id,operation_id,scope,subject_id,identifier_digest,key_id)
               VALUES($1,$2,'contact',$3,$4,$5)""",
            org, operation, sample_contact["id"], "a" * 64, "synthetic",
        )

    history, current = await first.tenant_history(pg_pool, org, key)
    assert not history and current != first_snapshot
    assert await second.reconcile_tenant_histories(pg_pool) == 2
    assert second.recupera_cronologia(key) == []
    assert second.recupera_cronologia(foreign_key) == [("synthetic text", "response")]
    assert second.recupera_cronologia("demo:anonymous:session") == [("demo", "response")]

    assert await second.add_tenant_history(pg_pool, org, key, current, "fresh", "new")
    assert not await second.add_tenant_history(
        pg_pool, org, key, stale_snapshot, "stale in flight", "discard",
    )
    assert second.recupera_cronologia(key) == [("fresh", "new")]

    # Losing the authority must never serve existing local text.
    with pytest.raises(AttributeError):
        await second.tenant_history(None, org, key)
    assert second.recupera_cronologia(key) == []
    assert second.recupera_cronologia(foreign_key) == [("synthetic text", "response")]


@pytest.mark.asyncio
async def test_cache_generation_metadata_follows_history_capacity(pg_pool, sample_org, other_org):
    store = ConversationStore(max_conversations=1)
    for org in (sample_org["id"], other_org["id"]):
        key = f"{org}:staff:session"
        _, snapshot = await store.tenant_history(pg_pool, org, key)
        assert await store.add_tenant_history(pg_pool, org, key, snapshot, "new", "reply")
        assert len(store._tenant_snapshots) == 1
    assert store.erase_organization(str(other_org["id"])) == 1
    assert store._tenant_snapshots == {}


@pytest.mark.asyncio
async def test_durable_replay_revalidates_exact_response_under_erasure_fence(pg_pool):
    from src.api.routes.simulator import _simulator_replay_current

    org = (await _organization(pg_pool))["id"]
    actor = await _auth_member(pg_pool, org)
    operation = _operation(org, actor_id=actor)
    quota = QuotaReservations(pg_pool)
    reserved = await quota.reserve(operation)
    ticket = reserved["ticket"]
    await quota.commit(operation, ticket, _final(operation, ticket))
    replay = await quota.reserve(operation)
    assert replay["status"] == "replay"
    payload = replay["response"]
    assert await _simulator_replay_current(pg_pool, operation, payload)
    assert not await _simulator_replay_current(pg_pool, operation, dict(payload, risposta="other"))

    subject = uuid.uuid4()
    await pg_pool.execute(
        "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,'synthetic')",
        subject, org,
    )
    erase_op = await Operations(pg_pool).create(
        org, "synthetic", uuid.uuid4(), kind="erase", scope="contact", subject_id=subject,
    )
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org),
        )
        await conn.execute(
            """INSERT INTO gdpr_erasure_barriers(
                   organization_id,operation_id,scope,subject_id,identifier_digest,key_id)
               VALUES($1,$2,'contact',$3,$4,$5)""",
            org, erase_op, subject, "b" * 64, "synthetic",
        )
    # A contact barrier alone does not relabel an unrelated account response.
    assert await _simulator_replay_current(pg_pool, operation, payload)
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org),
        )
        await conn.execute(
            """DELETE FROM simulation_requests WHERE organization_id=$1
               AND auth_user_id=$2 AND request_id=$3""",
            org, actor, operation.id,
        )
    assert not await _simulator_replay_current(pg_pool, operation, payload)
