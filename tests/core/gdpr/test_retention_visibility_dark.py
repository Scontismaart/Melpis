"""Retention reads are independent of the destructive retention controller."""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core import retention_job
from src.core.db.repositories.conversation_repo import ConversationRepository
from src.core.db.repositories.message_repo import MessageRepository


@pytest.mark.asyncio
async def test_legacy_execution_flag_alone_cannot_delete(monkeypatch):
    expire = AsyncMock(return_value=2)
    policies = AsyncMock(return_value=[{'organization_id': uuid4(), 'category': 'messages_content',
        'version': 'test', 'retention_days': 60, 'legal_hold': False, 'erasure_allowed': True, 'valid': True}])
    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.fetchval = AsyncMock(return_value=True)  # SQL074/C2 lifecycle owns this schema.
    connection.transaction.return_value.__aenter__ = AsyncMock()
    connection.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    class Pool:
        def acquire(self):
            class Acquire:
                async def __aenter__(self): return connection
                async def __aexit__(self, *args): return False
            return Acquire()
    monkeypatch.setattr(retention_job, '_expired_exports', expire)
    monkeypatch.setattr(retention_job, '_retention_policies', policies)
    monkeypatch.setattr(retention_job, 'conversation_schema', AsyncMock(return_value='ready'))
    monkeypatch.setenv('GDPR_RETENTION_EXECUTION_ENABLED', 'true')
    result = await retention_job.run_retention(Pool())
    assert result == {'exports_expired': 2, 'messages_redacted': 0, 'simulator_purged': 0}
    policies.assert_awaited_once()
    assert all('DELETE' not in call.args[0].upper() and 'UPDATE' not in call.args[0].upper()
               for call in connection.execute.await_args_list)


@pytest.mark.asyncio
async def test_inbox_hides_old_content_but_keeps_recent_and_tenant_scope(pg_pool, sample_org, other_org):
    async def seed(org, text, days):
        contact, conversation, message = uuid4(), uuid4(), uuid4()
        async with pg_pool.acquire() as conn:
            await conn.execute('INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)',
                               contact, org, '+1555' + uuid4().hex[:7])
            await conn.execute('INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)',
                               conversation, org, contact)
            has_c24_retention = await conn.fetchval(
                "SELECT to_regclass('public.c24_idle_watermarks') IS NOT NULL",
            )
            if days > 60 and has_c24_retention:
                # Model a pre-existing historical row with its immutable
                # provider-receipt time. The session-local trigger bypass is
                # limited to this synthetic fixture; the recent rows below
                # exercise the production insert trigger normally.
                async with conn.transaction():
                    await conn.execute('SET LOCAL session_replication_role=replica')
                    await conn.execute('''INSERT INTO messages(id,organization_id,conversation_id,direction,
                        message_type,content,content_text,status,handling_type,created_at,c24_received_at)
                        VALUES($1,$2,$3,'inbound','text','{}'::jsonb,$4,'handled','human',
                               NOW()-make_interval(days=>$5),
                               NOW()-make_interval(days=>$5))''', message, org, conversation, text, days)
                return message
            await conn.execute('''INSERT INTO messages(id,organization_id,conversation_id,direction,
                message_type,content,content_text,status,handling_type,created_at)
                VALUES($1,$2,$3,'inbound','text','{}'::jsonb,$4,'handled','human',
                       NOW()-make_interval(days=>$5))''', message, org, conversation, text, days)
        return message
    org, other = sample_org['id'], other_org['id']
    recent = await seed(org, 'recent', 59)
    old = await seed(org, 'old', 61)
    foreign = await seed(other, 'foreign', 59)
    async with pg_pool.acquire() as conn:
        old_conv = await conn.fetchval('SELECT conversation_id FROM messages WHERE id=$1', old)
        recent_conv = await conn.fetchval('SELECT conversation_id FROM messages WHERE id=$1', recent)
    repo = ConversationRepository(pg_pool)
    assert not await repo.list_conversation_messages(str(org), str(old_conv))
    assert [r['id'] for r in await repo.list_conversation_messages(str(org), str(recent_conv))] == [recent]
    assert not await repo.list_conversation_messages(str(other), str(recent_conv))
    ids = {r['id'] for r in await MessageRepository(pg_pool).get_messages_by_org(org)}
    assert recent in ids and old not in ids and foreign not in ids
