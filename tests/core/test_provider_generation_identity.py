"""PostgreSQL evidence and billing behavior for observed OpenRouter IDs."""

import json
import asyncio
import time
import uuid

import httpx
import pytest

from src.core.ai_providers import PROVIDERS
from src.core.db.repositories.billing_repo import BillingRepository
from src.core.llm_client_policy import LLMClientPolicy
from src.core.observed_llm import ObservedChatLLM, ProviderAccountingError
from tests.core.test_provider_accounting import _events, _open_test_context, _tenant


@pytest.mark.asyncio
async def test_body_identity_allows_lookup_without_usage_or_a04_consumption(
    pg_pool, sample_org, other_org, monkeypatch,
):
    """Generation identity is lookup evidence, never verified usage or quota."""
    org_id = sample_org["id"]
    actor = await _tenant(pg_pool, org_id)
    context = await _open_test_context(
        pg_pool, org_id, task_type="document_qa", actor_auth_user_id=actor,
    )
    generation_id = "gen-body-only-identity-001"
    posts = []

    def transport(request):
        posts.append(request.method)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(json.dumps({
                "id": generation_id,
                "request_id": "req-body-only-identity-001",
                "model": "xingchen-agi/xing4.0-29b-a4b:free",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                # No usage: the identity is useful for lookup, but metrics are unverified.
            }).encode()),
            headers={"content-type": "application/json"},
        )

    llm = ObservedChatLLM(
        policy=LLMClientPolicy(
            provider=PROVIDERS["openrouter"],
            model="openrouter/xingchen-agi/xing4.0-29b-a4b:free",
            api_key="offline-test-only", base_url=None,
        ),
        session=context,
        async_transport=httpx.MockTransport(transport),
    )
    try:
        with pytest.raises(ProviderAccountingError):
            await llm.acall("synthetic recovery identity test")
        assert posts == ["POST"]

        event = (await _events(pg_pool, org_id))[0]
        assert event["metadata"]["provider_generation_id"] == generation_id
        assert event["metadata"]["accounting_status"] == "unresolved"
        assert event["metadata"].get("prompt_tokens") is None
        assert event["metadata"].get("completion_tokens") is None
        assert event["metadata"].get("estimated_cost_eur") is None

        async with pg_pool.acquire() as conn:
            quota_reservation = await conn.fetchrow(
                "SELECT event_type, quantity, metadata FROM usage_events "
                "WHERE organization_id=$1 AND id=$2",
                org_id, context._quota_operation.id,
            )
            await conn.execute(
                "UPDATE provider_attempt_runtime SET lease_expires_at=now()-interval '1 second' "
                "WHERE organization_id=$1 AND attempt_id=$2", org_id, event["id"],
            )
            blocked = await conn.fetchval(
                "SELECT ai_accounting_blocked FROM organizations WHERE id=$1", org_id,
            )
            used = await conn.fetchval(
                "SELECT messages_used_this_period FROM organizations WHERE id=$1", org_id,
            )

        # A04 reservation remains pending; unresolved provider accounting blocks use.
        assert blocked is True
        assert used == 0
        assert quota_reservation["event_type"] == "system.quota_reservation"
        assert quota_reservation["quantity"] == 0
        reservation_metadata = quota_reservation["metadata"]
        if isinstance(reservation_metadata, str):
            reservation_metadata = json.loads(reservation_metadata)
        assert reservation_metadata["state"] == "reserved"
        repository = BillingRepository(pg_pool)
        result = await repository.recover_provider_attempt(
            org_id, event["id"], actor_auth_user_id=actor,
            aal="aal2", auth_time=int(time.time()),
        )
        assert result["state"] == "lookup_required"
        assert result["generation_id"] == generation_id
        assert posts == ["POST"]
        async with pg_pool.acquire() as conn:
            reservation_metadata = await conn.fetchval(
                "SELECT metadata FROM usage_events WHERE organization_id=$1 AND id=$2",
                org_id, context._quota_operation.id,
            )
        if isinstance(reservation_metadata, str):
            reservation_metadata = json.loads(reservation_metadata)
        assert reservation_metadata["state"] == "reserved"

        # Cross-tenant recovery cannot use an otherwise valid generation identity.
        with pytest.raises(PermissionError):
            await repository.recover_provider_attempt(
                other_org["id"], event["id"], actor_auth_user_id=actor,
                aal="aal2", auth_time=int(time.time()),
            )
    finally:
        await context.close()


@pytest.mark.asyncio
async def test_tenant_identity_and_concurrent_recovery_claims_are_isolated(
    pg_pool, sample_org, other_org,
):
    org_ids = (sample_org["id"], other_org["id"])
    actors = [await _tenant(pg_pool, org_id) for org_id in org_ids]
    contexts = [
        await _open_test_context(
            pg_pool, org_id, task_type="document_qa", actor_auth_user_id=actor,
        )
        for org_id, actor in zip(org_ids, actors)
    ]
    generation_ids = ("gen-tenant-one-identity-001", "gen-tenant-two-identity-002")
    post_counts = [0, 0]

    def make_llm(index):
        def transport(_request):
            post_counts[index] += 1
            return httpx.Response(
                200,
                stream=httpx.ByteStream(json.dumps({
                    "id": generation_ids[index],
                    "request_id": f"req-tenant-identity-{index + 1:03d}",
                    "model": "xingchen-agi/xing4.0-29b-a4b:free",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                }).encode()),
                headers={"content-type": "application/json"},
            )

        return ObservedChatLLM(
            policy=LLMClientPolicy(
                provider=PROVIDERS["openrouter"],
                model="openrouter/xingchen-agi/xing4.0-29b-a4b:free",
                api_key="offline-test-only", base_url=None,
            ),
            session=contexts[index],
            async_transport=httpx.MockTransport(transport),
        )

    try:
        results = await asyncio.gather(*(
            make_llm(i).acall("synthetic tenant isolation") for i in range(2)
        ), return_exceptions=True)
        assert all(isinstance(result, ProviderAccountingError) for result in results)
        assert post_counts == [1, 1]

        events = [
            (await _events(pg_pool, org_id))[0]
            for org_id in org_ids
        ]
        assert [event["metadata"]["provider_generation_id"] for event in events] == list(generation_ids)
        assert [event["metadata"]["accounting_status"] for event in events] == [
            "unresolved", "unresolved",
        ]

        async with pg_pool.acquire() as conn:
            for org_id, event, context in zip(org_ids, events, contexts):
                await conn.execute(
                    "UPDATE provider_attempt_runtime SET lease_expires_at=now()-interval '1 second' "
                    "WHERE organization_id=$1 AND attempt_id=$2", org_id, event["id"],
                )
                reservation = await conn.fetchrow(
                    "SELECT event_type, metadata FROM usage_events "
                    "WHERE organization_id=$1 AND id=$2", org_id,
                    context._quota_operation.id,
                )
                metadata = reservation["metadata"]
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                assert reservation["event_type"] == "system.quota_reservation"
                assert metadata["state"] == "reserved"

        repository = BillingRepository(pg_pool)
        claims = await asyncio.gather(*(
            repository.recover_provider_attempt(
                org_ids[0], events[0]["id"], actor_auth_user_id=actors[0],
                aal="aal2", auth_time=int(time.time()),
            )
            for _ in range(2)
        ))
        assert sorted(claim["state"] for claim in claims) == ["lookup_required", "worker_active"]
        assert all(claim.get("generation_id") in {None, generation_ids[0]} for claim in claims)
        assert post_counts == [1, 1]
    finally:
        for context in contexts:
            await context.close()
