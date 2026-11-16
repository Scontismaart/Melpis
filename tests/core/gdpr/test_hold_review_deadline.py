from uuid import uuid4

import asyncpg
import pytest


@pytest.mark.asyncio
async def test_review_requires_deadline_and_release_remains_final(pg_pool, sample_org):
    org_id, hold_id = sample_org["id"], uuid4()
    await pg_pool.execute(
        """INSERT INTO gdpr_retention_holds (
               organization_id, hold_id, category, scope, reason_code,
               authority_reference, approved_by, approved_at, review_by
           ) VALUES ($1,$2,'messages_content','organization','litigation',
                     'test:deadline','qa',now()-interval '1 hour',now()+interval '1 week')""",
        org_id, hold_id,
    )
    insert = """INSERT INTO gdpr_retention_hold_events (
                    organization_id, hold_id, event_id, kind, authority_reference,
                    approved_by, occurred_at, next_review_by
                )
                SELECT $1,$2,$3,$4,'test:deadline','qa',clock_timestamp(),
                       CASE WHEN $5::boolean THEN clock_timestamp()+interval '1 week' END"""
    with pytest.raises(asyncpg.CheckViolationError, match="gdpr_hold_review_deadline_required"):
        await pg_pool.execute(insert, org_id, hold_id, uuid4(), "reviewed", False)
    await pg_pool.execute(insert, org_id, hold_id, uuid4(), "reviewed", True)
    await pg_pool.execute(insert, org_id, hold_id, uuid4(), "released", False)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="already superseded"):
        await pg_pool.execute(insert, org_id, hold_id, uuid4(), "reviewed", True)
