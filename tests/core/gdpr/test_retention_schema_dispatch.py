"""Retention dispatch must fail closed when the lifecycle schema is unsafe."""
import json

import pytest

from src.core.retention_job import run_retention
from src.core.gdpr.c24_schema import conversation_schema
from tests.core.gdpr.test_c24_schema_compatibility import legacy_pool
from tests.core.gdpr.test_retention_a06 import approve_message_retention, seed_messages
from tests.core.test_quota_reservation_lifecycle import _organization

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('partial', [False, True])
async def test_legacy_and_partial_dispatch_without_verified_message_provenance(
    legacy_pool, monkeypatch, partial,
):
    org = (await _organization(legacy_pool))['id']
    await approve_message_retention(legacy_pool, org)
    message = (await seed_messages(legacy_pool, org))[0]
    if partial:
        await legacy_pool.execute('ALTER TABLE messages ADD COLUMN c24_original_subject_id uuid')
    monkeypatch.setenv('GDPR_RETENTION_EXECUTION_ENABLED', 'true')
    result = await run_retention(legacy_pool)
    assert result['messages_redacted'] == 0
    row = await legacy_pool.fetchrow(
        'SELECT content_text,deleted_at FROM messages WHERE organization_id=$1 AND id=$2', org, message,
    )
    assert row['content_text'] == 'synthetic retained message'
    assert row['deleted_at'] is None


async def test_sql074_defers_primary_retention_to_owner(pg_pool, monkeypatch):
    org = (await _organization(pg_pool))['id']
    await approve_message_retention(pg_pool, org)
    message = (await seed_messages(pg_pool, org))[0]
    monkeypatch.setenv('GDPR_RETENTION_EXECUTION_ENABLED', 'true')
    result = await run_retention(pg_pool)
    assert result['messages_redacted'] == 0
    assert not await pg_pool.fetchval(
        'SELECT deleted_at IS NOT NULL FROM messages WHERE organization_id=$1 AND id=$2', org, message
    )


@pytest.mark.migration_limit(73)
async def test_pre_controller_booking_retention_requires_verified_receipt(
    pg_pool, sample_org, monkeypatch,
):
    org = sample_org['id']
    message = (await seed_messages(pg_pool, org, age_days=5))[0]
    async with pg_pool.acquire() as conn:
        assert await conversation_schema(conn) == 'ready'
        assert await conn.fetchval("SELECT to_regclass('public.gdpr_retention_holds') IS NULL")
        assert not await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_attribute
            WHERE attrelid='public.messages'::regclass AND attname='c24_received_at'
              AND NOT attisdropped)""")
        await conn.execute(
            """UPDATE messages SET ai_reply_cache=jsonb_build_object(
                   'booking_state_proposal','{"synthetic":true}'::jsonb) WHERE id=$1""",
            message,
        )
        await conn.execute(
            """INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
                   retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
               VALUES($1,'booking_conversation_content','synthetic-booking-retention',TRUE,1,FALSE,
                      'test:synthetic-booking-retention','qa-retention',NOW()-INTERVAL '1 minute',
                      NOW()+INTERVAL '1 day')""",
            org,
        )

    monkeypatch.setenv('GDPR_RETENTION_EXECUTION_ENABLED', 'true')
    counts = await run_retention(pg_pool)

    assert counts['messages_redacted'] == 0
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT ai_reply_cache,content_text,deleted_at FROM messages WHERE organization_id=$1 AND id=$2',
            org, message,
        )
        assert json.loads(row['ai_reply_cache']) == {'booking_state_proposal': {'synthetic': True}}
        assert row['content_text'] == 'synthetic retained message'
        assert row['deleted_at'] is None
        assert await conn.fetchval(
            'SELECT count(*) FROM booking_conversation_revocations WHERE organization_id=$1', org,
        ) == 0
