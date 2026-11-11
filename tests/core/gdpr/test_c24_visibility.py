"""Ordinary message views use the immutable server receipt time."""
from uuid import uuid4

import pytest

from src.api.routes.dashboard import recupera_eventi_dashboard
from src.core.db.repositories.conversation_repo import ConversationRepository
from src.core.db.repositories.message_repo import MessageRepository
from tests.core.bookings.test_conversation_state_postgres import state_env

pytestmark = pytest.mark.asyncio


async def test_ordinary_views_hide_old_or_unverifiable_content(state_env):
    env=state_env; pool=env['pool']; org=env['org']; conv=env['conv']
    recent, old, unknown = uuid4(), uuid4(), uuid4()
    for message, text in ((recent,'recent synthetic'),(old,'old synthetic'),(unknown,'unknown synthetic')):
        await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,
            content,content_text,status,handling_type) VALUES($1,$2,$3,'inbound','text','{}'::jsonb,$4,'handled','ai')""",
            message,org,conv,text)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE messages DISABLE TRIGGER c24_source_received')
        await conn.execute("UPDATE messages SET c24_received_at=clock_timestamp()-interval '61 days',created_at=clock_timestamp() WHERE organization_id=$1 AND id=$2",org,old)
        await conn.execute('UPDATE messages SET c24_received_at=NULL WHERE organization_id=$1 AND id=$2',org,unknown)
        await conn.execute('ALTER TABLE messages ENABLE TRIGGER c24_source_received')

    conv_repo=ConversationRepository(pool); msg_repo=MessageRepository(pool)
    visible=await conv_repo.list_conversation_messages(org,conv,limit=50)
    ids={row['id'] for row in visible}
    assert recent in ids and old not in ids and unknown not in ids
    assert await msg_repo.get_message_org_scoped(org,old) is None
    assert await msg_repo.get_message_org_scoped(org,unknown) is None
    assert (await msg_repo.get_message_org_scoped(org,recent))['id']==recent
    by_org={row['id'] for row in await msg_repo.get_messages_by_org(org)}
    assert recent in by_org and old not in by_org and unknown not in by_org
    events=await recupera_eventi_dashboard(pool,str(org))
    texts={event.testo_originale for event in events}
    assert 'recent synthetic' in texts
    assert 'old synthetic' not in texts and 'unknown synthetic' not in texts
