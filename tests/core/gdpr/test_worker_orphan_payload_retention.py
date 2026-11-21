"""Retention of expired private capabilities without usable worker authority."""
import uuid

import pytest

from src.core.retention_job import scrub_expired_worker_capability_payloads


async def _insert_orphan(conn, org, *, expired=True):
    return await conn.fetchval(
        """INSERT INTO c24_private.worker_capabilities
            (organization_id,message_id,claim_id,generation,booking_id,payload,payload_hash,
             issued_at,expires_at,consumed_at,consumed_backend_pid,consumed_transaction_id)
           VALUES ($1,gen_random_uuid(),gen_random_uuid(),1,gen_random_uuid(),
             '{"synthetic":"payload"}'::jsonb,repeat('a',64),
             clock_timestamp()-interval '2 minutes',
             CASE WHEN $2 THEN clock_timestamp()-interval '1 minute'
                  ELSE clock_timestamp()+interval '2 minutes' END,
             clock_timestamp()-interval '2 minutes',pg_backend_pid(),pg_current_xact_id())
           RETURNING capability_id""",
        org, expired,
    )


@pytest.mark.asyncio
async def test_expired_consumed_orphan_payload_scrubs_with_lineage_intact(pg_pool):
    org = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        capability = await _insert_orphan(conn, org)
        active = await _insert_orphan(conn, org, expired=False)
        original = await conn.fetchval(
            """SELECT to_jsonb(c)-'payload' FROM c24_private.worker_capabilities c
               WHERE organization_id=$1 AND capability_id=$2""", org, capability,
        )
    try:
        async with pg_pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """SELECT capability_id FROM c24_private.worker_capabilities
                   WHERE organization_id=$1 AND capability_id=$2 FOR UPDATE""", org, capability,
            )
            result = await scrub_expired_worker_capability_payloads(pg_pool)
            assert result['worker_capability_payloads_scrubbed'] == 0
            assert result['oldest_overdue_seconds'] >= 60

        assert await scrub_expired_worker_capability_payloads(pg_pool) == {
            'worker_capability_payloads_scrubbed': 1, 'oldest_overdue_seconds': None,
        }
        assert await scrub_expired_worker_capability_payloads(pg_pool) == {
            'worker_capability_payloads_scrubbed': 0, 'oldest_overdue_seconds': None,
        }
        async with pg_pool.acquire() as conn:
            assert await conn.fetchval(
                """SELECT payload IS NULL FROM c24_private.worker_capabilities
                   WHERE organization_id=$1 AND capability_id=$2""", org, capability,
            )
            assert await conn.fetchval(
                """SELECT to_jsonb(c)-'payload' FROM c24_private.worker_capabilities c
                   WHERE organization_id=$1 AND capability_id=$2""", org, capability,
            ) == original
            assert await conn.fetchval(
                """SELECT payload IS NOT NULL FROM c24_private.worker_capabilities
                   WHERE organization_id=$1 AND capability_id=$2""", org, active,
            )
    finally:
        await pg_pool.execute(
            """DELETE FROM c24_private.worker_capabilities
               WHERE organization_id=$1 AND capability_id=ANY($2::uuid[])""",
            org, [capability, active],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('exact_id', [True, False])
async def test_scrub_protects_matching_context_without_committing_incomplete_work(pg_pool, exact_id):
    org = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        try:
            capability = await _insert_orphan(conn, org)
            # The owner fixture represents a partially constructed transaction;
            # never commit an unfinished context or disable its constraint.
            await conn.execute(
                """INSERT INTO c24_private.worker_context
                    (backend_pid,transaction_id,organization_id,message_id,conversation_id,
                     claim_id,generation,capability_id,lease_until)
                   SELECT consumed_backend_pid,consumed_transaction_id,organization_id,message_id,
                     gen_random_uuid(),claim_id,generation,$3,
                     clock_timestamp()+interval '1 minute'
                   FROM c24_private.worker_capabilities
                   WHERE organization_id=$1 AND capability_id=$2""",
                org, capability, capability if exact_id else uuid.uuid4(),
            )
            assert await conn.fetchval(
                'SELECT c24_private.c24_worker_scrub_expired_capabilities(100)',
            ) == 0
            assert await conn.fetchval(
                """SELECT payload IS NOT NULL FROM c24_private.worker_capabilities
                   WHERE organization_id=$1 AND capability_id=$2""", org, capability,
            )
        finally:
            await transaction.rollback()
