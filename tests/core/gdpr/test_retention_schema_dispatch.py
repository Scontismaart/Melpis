"""Retention dispatch must fail closed when the lifecycle schema is unsafe."""
import pytest

from src.core.retention_job import run_retention
from tests.core.gdpr.test_c24_schema_compatibility import legacy_pool
from tests.core.gdpr.test_retention_a06 import approve_message_retention, seed_messages
from tests.core.test_quota_reservation_lifecycle import _organization

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('partial', [False, True])
async def test_legacy_and_partial_dispatch(legacy_pool, monkeypatch, partial):
    org = (await _organization(legacy_pool))['id']
    await approve_message_retention(legacy_pool, org)
    message = (await seed_messages(legacy_pool, org))[0]
    if partial:
        await legacy_pool.execute('ALTER TABLE messages ADD COLUMN c24_original_subject_id uuid')
    monkeypatch.setenv('GDPR_RETENTION_EXECUTION_ENABLED', 'true')
    result = await run_retention(legacy_pool)
    assert result['messages_redacted'] == (0 if partial else 1)
    assert await legacy_pool.fetchval(
        'SELECT deleted_at IS NOT NULL FROM messages WHERE organization_id=$1 AND id=$2', org, message
    ) is not partial


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
