import json
import uuid

import asyncpg
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def org_id(pg_pool):
    oid = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'Test Org')", oid)
    return oid


@pytest_asyncio.fixture
async def contact_id(org_id, repo):
    async def _make(phone="391234567890"):
        return (await repo.get_or_create_contact(org_id, phone))["id"]
    return _make


@pytest_asyncio.fixture
async def conv_id(org_id, contact_id, repo):
    async def _make(cid=None):
        cid = cid or await contact_id()
        return (await repo.get_or_create_conversation(org_id, cid))["id"]
    return _make


@pytest_asyncio.fixture
async def msg_id(org_id, conv_id, repo):
    async def _make(cvid=None, created_at=None):
        cvid = cvid or await conv_id()
        content = {"type": "text", "text": {"body": "Test"}}
        if created_at:
            async with repo.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    INSERT INTO messages (id, organization_id, conversation_id, wam_id,
                        direction, message_type, content, content_text, status, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10)
                    RETURNING *
                """, uuid.uuid4(), org_id, cvid, f"wam_{uuid.uuid4().hex[:8]}",
                    "outbound", "text", json.dumps(content), "Test", "sent", created_at)
                return row["id"]
        msg = await repo.upsert_message(
            uuid.uuid4(), org_id, cvid, f"wam_{uuid.uuid4().hex[:8]}",
            "outbound", "text", content, "Test", "sent",
        )
        return msg["id"]
    return _make


async def _soft_delete_message(repo, mid):
    # Repository.soft_delete_message e' stato rimosso (dead code cross-tenant):
    # il setup usa lo stesso SQL inline, semanticamente identico.
    async with repo.pool.acquire() as conn:
        await conn.execute("UPDATE messages SET deleted_at = NOW() WHERE id = $1", mid)


@pytest.mark.asyncio
async def test_soft_delete_message_excluded(repo, org_id, msg_id):
    mid = await msg_id()
    await _soft_delete_message(repo, mid)
    async with repo.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM messages WHERE id = $1", mid)
    assert row["deleted_at"] is not None


@pytest.mark.asyncio
async def test_soft_deleted_messages_not_claimed(repo, org_id, msg_id, conv_id):
    cvid = await conv_id()
    mid = await msg_id(cvid)
    async with repo.pool.acquire() as conn:
        await conn.execute(
            "UPDATE messages SET direction = 'inbound', status = 'received_pending_ai' WHERE id = $1 AND organization_id=$2",
            mid,
            org_id,
        )
    await _soft_delete_message(repo, mid)
    async with repo.pool.acquire() as conn:
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match='resurrection'):
            await conn.execute('UPDATE messages SET deleted_at=NULL WHERE id=$1 AND organization_id=$2', mid, org_id)
        assert await conn.fetchval('SELECT deleted_at IS NOT NULL FROM messages WHERE id=$1 AND organization_id=$2', mid, org_id)
    claimed = await repo.claim_inbound_messages(limit=10)
    assert mid not in [c["id"] for c in claimed]
