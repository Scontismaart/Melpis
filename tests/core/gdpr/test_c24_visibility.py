"""Ordinary message views require immutable receipt and subject evidence."""
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet

from src.api.routes.dashboard import recupera_eventi_dashboard
from src.core.db.repositories.conversation_repo import ConversationRepository
from src.core.db.repositories.message_repo import MessageRepository
from src.core.gdpr.exporter import Exporter
from src.core.gdpr.journal_publication import _canonical
from src.core.gdpr.legacy_message_reconciliation import inventory_quarantined_messages
from src.core.gdpr.operations import OperationUnavailable
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from tests.core.bookings.test_conversation_state_postgres import state_env
from tests.core.gdpr.test_exports_a06 import export_bytes

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


async def test_missing_conflicting_and_future_lineage_is_quarantined_and_reviewable(state_env, monkeypatch):
    env = state_env
    pool, org, conv = env['pool'], env['org'], env['conv']
    subject = await pool.fetchval(
        "SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2", org, conv,
    )
    recent, no_subject, mismatch, future, unknown_receipt = (uuid4() for _ in range(5))
    texts = {
        recent: 'verified synthetic source',
        no_subject: 'synthetic no subject',
        mismatch: 'synthetic conflicting subject',
        future: 'synthetic future timestamp',
        unknown_receipt: 'synthetic missing timestamp',
    }
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany("""INSERT INTO messages(id,organization_id,conversation_id,direction,
            message_type,content,content_text,status,handling_type)
            VALUES($1,$2,$3,'inbound','text','{}'::jsonb,$4,'handled','ai_handled')""",
            [(message, org, conv, content) for message, content in texts.items()])
        await conn.execute('ALTER TABLE messages DISABLE TRIGGER booking_message_original_subject')
        await conn.execute('ALTER TABLE messages DISABLE TRIGGER c24_source_received')
        await conn.execute("""UPDATE messages SET c24_original_subject_id=NULL
            WHERE organization_id=$1 AND id=$2""", org, no_subject)
        await conn.execute("""UPDATE messages SET c24_original_subject_id=$3
            WHERE organization_id=$1 AND id=$2""", org, mismatch, uuid4())
        await conn.execute("""UPDATE messages SET c24_received_at=clock_timestamp()+interval '1 day'
            WHERE organization_id=$1 AND id=$2""", org, future)
        await conn.execute("""UPDATE messages SET c24_received_at=NULL
            WHERE organization_id=$1 AND id=$2""", org, unknown_receipt)
        await conn.execute('ALTER TABLE messages ENABLE TRIGGER c24_source_received')
        await conn.execute('ALTER TABLE messages ENABLE TRIGGER booking_message_original_subject')
    blocked = {no_subject, mismatch, future, unknown_receipt}
    conv_repo, msg_repo = ConversationRepository(pool), MessageRepository(pool)
    for rows in (
        await conv_repo.list_conversation_messages(org, conv),
        await msg_repo.get_messages_by_org(org),
    ):
        ids = {row['id'] for row in rows}
        assert recent in ids
        assert not ids.intersection(blocked)
    assert (await msg_repo.get_message_org_scoped(org, recent))['id'] == recent
    for message in blocked:
        assert await msg_repo.get_message_org_scoped(org, message) is None
    events = await recupera_eventi_dashboard(pool, str(org))
    event_texts = {event.testo_originale for event in events}
    assert texts[recent] in event_texts and event_texts.isdisjoint(texts[msg] for msg in blocked)

    # Supabase can expose these relations directly to tenant JWTs. Verify
    # that this route has the same restrictions as the application APIs.
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany("""INSERT INTO message_feedback(organization_id,message_id,conversation_id,source,value)
            VALUES($1,$2,$3,'customer_emoji','up')""", [(org, message, conv) for message in texts])
        await conn.execute("""CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('request.jwt.claim.sub',true),'')::uuid $$""")
        await conn.execute('GRANT USAGE ON SCHEMA public,auth TO authenticated')
        await conn.execute('''GRANT SELECT ON organization_memberships,user_profiles,contacts,conversations,
            messages,event_log,message_feedback TO authenticated''')
        await conn.execute('SET LOCAL ROLE authenticated')
        await conn.execute("SELECT set_config('request.jwt.claim.sub',$1,true)", str(env['auth']))
        message_ids = [row['id'] for row in await conn.fetch(
            'SELECT id FROM messages WHERE organization_id=$1 AND id=ANY($2::uuid[])', org, list(texts))]
        event_ids = {row['source_id'] for row in await conn.fetch(
            "SELECT source_id FROM event_log WHERE organization_id=$1 AND source_table='messages'", org)}
        feedback_ids = {row['message_id'] for row in await conn.fetch(
            'SELECT message_id FROM message_feedback WHERE organization_id=$1', org)}
        assert message_ids == [recent]
        assert recent in event_ids and event_ids.isdisjoint(blocked)
        assert feedback_ids == {recent}

    # The restricted owner queue is tenant-bound, bounded, traceable and reads
    # only opaque IDs/reason codes. Its review date is supplied by the controller.
    review = dict(case_id=uuid4(), review_by=datetime.now(timezone.utc)+timedelta(days=7),
                  authority_reference="test:synthetic-review")
    async with pool.acquire() as conn:
        with pytest.raises(RestoreBlocked, match="read-only owner"):
            await inventory_quarantined_messages(conn, org, **review)
        async with conn.transaction(readonly=True):
            page = await inventory_quarantined_messages(conn, org, limit=2, **review)
            second = await inventory_quarantined_messages(conn, org, limit=2, after=UUID(page['next']), **review)
            records = {UUID(record['message_id']): record['reasons']
                       for record in page['records']+second['records']}
            assert set(records) == blocked and second['next'] is None
            assert records[no_subject] == ['subject_missing']
            assert records[mismatch] == ['subject_conflict']
            assert records[future] == ['future_receipt']
            assert records[unknown_receipt] == ['receipt_missing']
            assert page['review_by'] == review['review_by'].isoformat()
            assert page['review_overdue'] is False and page['deletion_authorized'] is False
            claimed = page.pop('report_sha256')
            assert claimed == sha256(_canonical(page)).hexdigest()
            overdue = await inventory_quarantined_messages(conn, org, **dict(review,
                review_by=datetime.now(timezone.utc)-timedelta(days=1)))
            assert overdue['review_overdue'] is True and overdue['deletion_authorized'] is False
            unrelated = await inventory_quarantined_messages(conn, uuid4(), **review)
            assert unrelated['records'] == []
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    exporter = Exporter(pool)
    request_id = uuid4()
    snapshot = await exporter.prepare(org, env['auth'], request_id=request_id)
    document = json.loads(await export_bytes(exporter, org, snapshot['export_id'], env['auth']))
    assert {UUID(msg['id']) for msg in document['messages']}.isdisjoint(blocked)
    category = next(item for item in document['manifest']['categories'] if item['category']=='messages')
    assert category['status'] == 'partial' and category['quarantined_records'] == len(blocked)
    feedback = {UUID(row['message_id']) for row in document['message_feedback']}
    assert recent in feedback and feedback.isdisjoint(blocked)
    event_sources = {UUID(row['source_id']) for row in document['event_log']
                     if row['source_table'] == 'messages'}
    assert recent in event_sources and event_sources.isdisjoint(blocked)
    categories = {row['category']: row for row in document['manifest']['categories']}
    for name in ('message_feedback', 'event_log'):
        assert categories[name]['status'] == 'partial'
        assert categories[name]['quarantined_records'] >= len(blocked)
    export_id = UUID(snapshot['export_id'])
    await pool.execute("""UPDATE gdpr_operations
        SET manifest=jsonb_set(manifest,'{version}','"a06-v1"'::jsonb)
        WHERE organization_id=$1 AND id=$2""", org, export_id)
    with pytest.raises(OperationUnavailable, match='new request identifier'):
        await exporter.prepare(org, env['auth'], request_id=request_id)
    with pytest.raises(LookupError, match='Export unavailable'):
        await exporter.preflight(org, export_id, env['auth'])
    with pytest.raises(OperationUnavailable, match='Export unavailable'):
        await anext(exporter.stream(org, export_id, env['auth']))
    contact = await exporter.prepare(org, env['auth'], scope='contact', subject_id=subject, request_id=uuid4())
    contact_doc = json.loads(await export_bytes(exporter, org, contact['export_id'], env['auth']))
    assert {UUID(msg['id']) for msg in contact_doc['messages']}.isdisjoint(blocked)
    assert await pool.fetchval("SELECT count(*) FROM messages WHERE organization_id=$1 AND id=ANY($2::uuid[])",
                               org, list(blocked)) == len(blocked)
