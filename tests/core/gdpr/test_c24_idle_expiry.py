"""Synthetic idle-expiry lifecycle; no scheduler or service-role grant."""
import asyncpg
import pytest
from uuid import uuid4

from src.core.db.repositories.booking_conversation_repo import ConversationStateUnavailable, StateConflict
from src.core.gdpr.catalog import CATEGORIES
from tests.core.bookings.test_conversation_state_postgres import state_env, commit_source, apply, second_source
from tests.core.gdpr.test_conversation_lifecycle_c24 import approve_conversation
from tests.core.test_quota_reservation_lifecycle import _used

pytestmark = pytest.mark.asyncio


async def age_state(env, days):
    async with env['pool'].acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE booking_conversation_states DISABLE TRIGGER c24_state_activity')
        await conn.execute("UPDATE booking_conversation_states SET updated_at=clock_timestamp()-make_interval(days=>$3) WHERE organization_id=$1 AND conversation_id=$2", env['org'], env['conv'], days)
        await conn.execute('ALTER TABLE booking_conversation_states ENABLE TRIGGER c24_state_activity')


async def test_idle_expiry_fences_old_source_and_allows_fresh_source(state_env):
    env=state_env; pool=env['pool']; old_request=uuid4()
    await commit_source(env, env['message'], '2026-11-12', {'service_id':'standard'}, 1700000000)
    await apply(env, env['message'], request=old_request)
    assert await pool.fetchval(
        '''SELECT r.original_subject_id = c.contact_id
           FROM booking_conversation_requests r JOIN conversations c
             ON c.organization_id=r.organization_id AND c.id=r.conversation_id
           WHERE r.organization_id=$1 AND r.request_id=$2''', env['org'], old_request,
    )
    before=await _used(pool, env['org'])
    assert not await pool.fetchval('SELECT c24_expire_idle($1,$2)', env['org'], env['conv'])
    await approve_conversation(pool, env['org'])
    await age_state(env, 31)
    with pytest.raises(ConversationStateUnavailable):
        await env['repo'].read(env['org'], env['auth'], env['conv'])
    assert await pool.fetchval('SELECT c24_expire_idle($1,$2)', env['org'], env['conv'])
    assert not await pool.fetchval('SELECT c24_expire_idle($1,$2)', env['org'], env['conv'])
    assert await pool.fetchval('SELECT count(*) FROM c24_source_fences WHERE organization_id=$1', env['org']) == 1
    with pytest.raises(StateConflict, match='expired conversation generation'):
        await apply(env, env['message'], request=old_request)
    stamp=await pool.fetchval('SELECT floor(extract(epoch FROM clock_timestamp()))::bigint')
    fresh=await second_source(env, stamp)
    result=await apply(env, fresh)
    assert result['state'].revision == 1
    with pytest.raises(StateConflict, match='expired conversation generation'):
        await apply(env, env['message'], request=old_request)
    assert all(slot.source_message_id == fresh for slot in result['state'].slots)
    assert await _used(pool, env['org']) == before + 1
    subject=await pool.fetchval('SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2', env['org'],env['conv'])
    await pool.execute("UPDATE contacts SET consent_status='withdrawn' WHERE organization_id=$1 AND id=$2",env['org'],subject)
    await pool.execute("UPDATE contacts SET consent_status='granted' WHERE organization_id=$1 AND id=$2",env['org'],subject)
    with pytest.raises(PermissionError):
        await apply(env, fresh)


async def test_hold_and_service_role_block_idle_cleanup(state_env):
    env=state_env; pool=env['pool']
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'])
    await approve_conversation(pool,env['org'])
    await age_state(env,31)
    await pool.execute("""INSERT INTO gdpr_retention_holds
      (organization_id,hold_id,category,scope,scope_id,reason_code,authority_reference,approved_by,approved_at,review_by)
      VALUES($1,$2,'booking_conversation_content','conversation',$3,'litigation','case:1','qa',clock_timestamp()-interval '2 days',clock_timestamp()-interval '1 day')""",env['org'],uuid4(),env['conv'])
    assert not await pool.fetchval('SELECT c24_expire_idle($1,$2)',env['org'],env['conv'])
    assert not await pool.fetchval("SELECT has_function_privilege('service_role','c24_expire_idle(uuid,uuid)','EXECUTE')")


async def test_receipt_record_hold_preserves_receipt_but_not_expired_state(state_env):
    env=state_env; pool=env['pool']; request=uuid4()
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'],request=request)
    await approve_conversation(pool,env['org'])
    await age_state(env,31)
    await pool.execute("""INSERT INTO gdpr_retention_holds
      (organization_id,hold_id,category,scope,scope_id,reason_code,authority_reference,approved_by,approved_at,review_by)
      VALUES($1,$2,'technical_receipts','record',$3,'litigation','case:receipt','qa',clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day')""",env['org'],uuid4(),request)

    assert await pool.fetchval('SELECT c24_expire_idle($1,$2)',env['org'],env['conv'])
    assert not await pool.fetchval('SELECT EXISTS(SELECT 1 FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2)',env['org'],env['conv'])
    assert await pool.fetchval('SELECT EXISTS(SELECT 1 FROM booking_conversation_requests WHERE organization_id=$1 AND conversation_id=$2 AND request_id=$3)',env['org'],env['conv'],request)


async def test_source_deletion_cannot_cascade_to_receipt(state_env):
    env = state_env
    pool = env['pool']
    request = uuid4()
    await commit_source(env, env['message'], '2026-11-12', {'service_id': 'standard'}, 1700000000)
    await apply(env, env['message'], request=request)
    await pool.execute('DELETE FROM messages WHERE organization_id=$1 AND id=$2', env['org'], env['message'])
    assert await pool.fetchval(
        'SELECT EXISTS(SELECT 1 FROM booking_conversation_requests WHERE organization_id=$1 AND request_id=$2)',
        env['org'], request,
    )
    subject = await pool.fetchval(
        'SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2',
        env['org'], env['conv'],
    )
    projection = next(category.contact_query for category in CATEGORIES
                      if category.name == 'booking_conversation_requests')
    receipts = await pool.fetch(projection, env['org'], subject)
    assert [receipt['request_id'] for receipt in receipts] == [request]
    assert not await pool.fetch(projection, env['org'], uuid4())
    assert not await pool.fetch(projection, uuid4(), subject)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await pool.execute(
            'DELETE FROM booking_conversation_requests WHERE organization_id=$1 AND request_id=$2',
            env['org'], request,
        )


async def test_delayed_provider_message_cannot_restart_expired_state(state_env):
    env=state_env; pool=env['pool']
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'])
    await approve_conversation(pool,env['org'])
    await age_state(env,31)
    assert await pool.fetchval('SELECT c24_expire_idle($1,$2)',env['org'],env['conv'])
    # Receipt is new but its provider timestamp precedes the idle cutoff.
    late=await second_source(env,1700000001)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await apply(env,late)
    assert (await env['repo'].read(env['org'],env['auth'],env['conv']))['state'] is None

async def test_expiry_and_new_turn_serialize_on_conversation(state_env):
    import asyncio
    env=state_env; pool=env['pool']
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'])
    await approve_conversation(pool,env['org'])
    await age_state(env,31)
    stamp=await pool.fetchval('SELECT floor(extract(epoch FROM clock_timestamp()))::bigint')
    fresh=await second_source(env,stamp)
    async with pool.acquire() as conn:
        tx=conn.transaction(); await tx.start()
        await conn.fetchval('SELECT id FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE',env['org'],env['conv'])
        expiry=asyncio.create_task(pool.fetchval('SELECT c24_expire_idle($1,$2)',env['org'],env['conv']))
        writer=asyncio.create_task(apply(env,fresh))
        await asyncio.sleep(0.1)
        await tx.commit()
    expired,result=await asyncio.gather(expiry,writer,return_exceptions=True)
    assert expired is True
    if isinstance(result,ConversationStateUnavailable):
        result=await apply(env,fresh)
    assert isinstance(result,dict)
    assert all(slot.source_message_id == fresh for slot in result['state'].slots)

async def test_unrelated_record_hold_does_not_block_state_expiry(state_env):
    env=state_env; pool=env['pool']
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'])
    await approve_conversation(pool,env['org'])
    await age_state(env,31)
    await pool.execute("""INSERT INTO gdpr_retention_holds
      (organization_id,hold_id,category,scope,scope_id,reason_code,authority_reference,approved_by,approved_at,review_by)
      VALUES($1,$2,'booking_conversation_content','record',$3,'litigation','case:2','qa',clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day')""",env['org'],uuid4(),uuid4())
    assert await pool.fetchval('SELECT c24_expire_idle($1,$2)',env['org'],env['conv'])

@pytest.mark.parametrize('provenance_field', ['slots', 'superseded', 'confirmation'])
async def test_old_provenance_cannot_be_restored_after_expiry(state_env, provenance_field):
    import json
    env=state_env; pool=env['pool']
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'])
    await approve_conversation(pool,env['org'])
    await age_state(env,31)
    assert await pool.fetchval('SELECT c24_expire_idle($1,$2)',env['org'],env['conv'])
    stamp=await pool.fetchval('SELECT floor(extract(epoch FROM clock_timestamp()))::bigint')
    fresh=await second_source(env,stamp)
    await apply(env,fresh)
    later=await second_source(env,stamp+1)
    body=await pool.fetchval('SELECT snapshot FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2',env['org'],env['conv'])
    snapshot=json.loads(body) if isinstance(body,str) else dict(body)
    snapshot['revision']=2
    snapshot['last_source_message_id']=str(later)
    if provenance_field == 'slots':
        snapshot['slots'][0]['source_message_id']=str(env['message'])
    elif provenance_field == 'superseded':
        snapshot['superseded']=[{'source_message_id':str(env['message'])}]
    else:
        snapshot['confirmation']={'source_message_id':str(env['message'])}
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError,match='provenance'):
        await pool.execute('''UPDATE booking_conversation_states SET source_message_id=$3,
          provider_timestamp=$4,revision=2,snapshot=$5::jsonb WHERE organization_id=$1 AND conversation_id=$2''',
          env['org'],env['conv'],later,stamp+1,json.dumps(snapshot))

async def test_purged_old_source_preserves_fresh_materialized_state(state_env):
    env=state_env; pool=env['pool']; request=uuid4()
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'],request=request)
    newer=await second_source(env,1700000001)
    before=(await apply(env,newer,revision=1))['state']
    billed=await _used(pool,env['org'])
    # Owner-only synthetic marker: this is not an activated cleanup controller.
    await pool.execute("""UPDATE messages SET content_purged_at=clock_timestamp(),content_purge_cause='retention',
       content='{}'::jsonb,content_text=NULL,ai_reply_cache=NULL,error_details=NULL,error_title=NULL
       WHERE organization_id=$1 AND id=$2""",env['org'],env['message'])
    await pool.execute("UPDATE simulation_requests SET response='{}'::jsonb WHERE organization_id=$1 AND c24_source_message_id=$2",env['org'],env['message'])
    state=(await env['repo'].read(env['org'],env['auth'],env['conv']))['state']
    assert state == before
    assert await _used(pool,env['org']) == billed
    from src.core.billing.quota_reservations import QuotaReservations, QuotaConflict
    from tests.core.test_quota_reservation_lifecycle import _inbound_operation
    with pytest.raises(QuotaConflict):
        await QuotaReservations(pool).reserve(_inbound_operation(env['org'],env['message'],text='2026-11-12'))
    assert await _used(pool,env['org']) == billed
    newer2=await second_source(env,1700000002,text='12:00')
    refreshed=await apply(env,newer2,revision=2)
    assert any(slot.source_message_id == env['message'] for slot in refreshed['state'].slots)
    with pytest.raises(PermissionError):
        await apply(env,env['message'],request=request)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await pool.execute("UPDATE messages SET content_text='restored' WHERE organization_id=$1 AND id=$2",env['org'],env['message'])

async def test_purged_confirmation_prompt_requires_new_confirmation(state_env):
    import json
    from src.core.bookings.conversation_state import render_confirmation_prompt
    env=state_env; pool=env['pool']; org=env['org']
    await commit_source(env,env['message'],'2026-11-12 10:00',{
        'service_id':'standard','customer_name':True,'appointment_date':'2026-11-12',
        'appointment_time':'10:00','party_size':2},1700000000)
    state=(await apply(env,env['message']))['state']
    prompt,reply=uuid4(),uuid4(); body=render_confirmation_prompt(state,prompt)
    await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id,sent_at)
        VALUES($1,$2,$3,'outbound','text','{}'::jsonb,'sent',$4,$5,clock_timestamp()-interval '120 seconds')""",prompt,org,env['conv'],body,str(uuid4()))
    stamp=await pool.fetchval("SELECT floor(extract(epoch from clock_timestamp()-interval '60 seconds'))::bigint")
    text=f'CONFIRM {prompt}'; wam=str(uuid4())
    await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id)
        VALUES($1,$2,$3,'inbound','text',$4::jsonb,'processing',$5,$6)""",reply,org,env['conv'],json.dumps({'id':wam,'type':'text','timestamp':str(stamp),'text':{'body':text}}),text,wam)
    confirmed=await env['repo'].confirm(org,env['auth'],env['conv'],reply,prompt,request_id=uuid4(),expected_revision=1,expected_catalog_revision=2)
    assert confirmed['state'].details_confirmed
    await pool.execute("""UPDATE messages SET content_purged_at=clock_timestamp(),content_purge_cause='retention',
       content='{}'::jsonb,content_text=NULL,ai_reply_cache=NULL,error_details=NULL,error_title=NULL
       WHERE organization_id=$1 AND id=$2""",org,prompt)
    result=await env['repo'].read(org,env['auth'],env['conv'])
    assert not result['state'].details_confirmed and not result['details_confirmed_current']

async def test_owner_source_cleanup_holds_fences_and_billing(state_env):
    from src.core.billing.quota_reservations import QuotaReservations, QuotaConflict
    from tests.core.test_quota_reservation_lifecycle import _inbound_operation
    env=state_env; pool=env['pool']; org=env['org']; source=env['message']
    await commit_source(env,source,'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,source)
    fresh=await second_source(env,1700000001)
    state=(await apply(env,fresh,revision=1))['state']; usage=await _used(pool,org)
    await pool.execute("UPDATE messages SET status='handled',handling_type='ai' WHERE organization_id=$1 AND id=$2",org,fresh)
    await pool.execute("UPDATE messages SET status='handled',handling_type='ai',created_at=clock_timestamp()-interval '91 days' WHERE organization_id=$1 AND id=$2",org,source)
    await pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
      VALUES($1,'messages_content','qa-90',TRUE,90,FALSE,'test:qa','qa',clock_timestamp()-interval '1 hour',clock_timestamp()+interval '1 day')""",org)
    hold=uuid4()
    await pool.execute("""INSERT INTO gdpr_retention_holds(organization_id,hold_id,category,scope,scope_id,reason_code,authority_reference,approved_by,approved_at,review_by)
      VALUES($1,$2,'messages_content','record',$3,'litigation','case:3','qa',clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day')""",org,hold,source)
    await pool.execute("INSERT INTO outbound_dedup(message_id,organization_id,response_text) VALUES($1,$2,'response')",source,org)
    await pool.execute("INSERT INTO message_delivery_attempts(id,message_id,status,error_details) VALUES($1,$2,'sent','{\"diagnostic\":\"private\"}')",uuid4(),source)
    await pool.execute("INSERT INTO event_log(organization_id,source_table,source_id,tipo_evento,priorita,testo_originale,risposta_ai,dettagli) VALUES($1,'messages',$2,'messaggio','media','private','private','{\"private\":true}')",org,source)
    assert not await pool.fetchval('SELECT c24_purge_source($1,$2)',org,source)
    await pool.execute("""INSERT INTO gdpr_retention_hold_events(organization_id,hold_id,event_id,kind,authority_reference,approved_by,occurred_at)
      VALUES($1,$2,$3,'released','case:3','qa',clock_timestamp())""",org,hold,uuid4())
    assert await pool.fetchval('SELECT c24_purge_source($1,$2)',org,source)
    assert not await pool.fetchval('SELECT c24_purge_source($1,$2)',org,source)
    row=await pool.fetchrow('SELECT content_purged_at,content_text,ai_reply_cache,billed_at FROM messages WHERE organization_id=$1 AND id=$2',org,source)
    assert row['content_purged_at'] and row['content_text'] is None and row['ai_reply_cache'] is None and row['billed_at']
    assert await _used(pool,org)==usage
    assert (await env['repo'].read(org,env['auth'],env['conv']))['state']==state
    with pytest.raises(QuotaConflict):
        await QuotaReservations(pool).reserve(_inbound_operation(org,source,text='2026-11-12'))
    assert await pool.fetchval('SELECT response_text IS NULL FROM outbound_dedup WHERE organization_id=$1 AND message_id=$2',org,source)
    assert await pool.fetchval('SELECT error_details IS NULL FROM message_delivery_attempts WHERE message_id=$1',source)
    assert await pool.fetchval("SELECT bool_and(testo_originale='' AND risposta_ai='' AND dettagli='{}'::jsonb) FROM event_log WHERE organization_id=$1 AND source_id=$2",org,source)
    assert not await pool.fetchval("SELECT has_function_privilege('service_role','c24_purge_source(uuid,uuid)','EXECUTE')")
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await pool.execute("UPDATE outbound_dedup SET response_text='restored' WHERE message_id=$1",source)

async def test_receipt_cleanup_requires_policy_and_respects_record_hold(state_env):
    env=state_env; pool=env['pool']; request=uuid4(); hold=uuid4()
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'],request=request)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE booking_conversation_requests DISABLE TRIGGER conversation_immutable')
        await conn.execute("UPDATE booking_conversation_requests SET c24_concluded_at=clock_timestamp()-interval '91 days' WHERE organization_id=$1 AND request_id=$2",env['org'],request)
        await conn.execute('ALTER TABLE booking_conversation_requests ENABLE TRIGGER conversation_immutable')
    assert not await pool.fetchval('SELECT c24_cleanup_receipt($1,$2)',env['org'],request)
    await pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
      retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
      VALUES($1,'technical_receipts','test',true,90,false,'test:receipt','qa',
       clock_timestamp()-interval '1 minute',clock_timestamp()+interval '1 day')""",env['org'])
    await pool.execute("""INSERT INTO gdpr_retention_holds
      (organization_id,hold_id,category,scope,scope_id,reason_code,authority_reference,approved_by,approved_at,review_by)
      VALUES($1,$2,'technical_receipts','record',$3,'litigation','case:receipt','qa',
       clock_timestamp()-interval '2 days',clock_timestamp()-interval '1 day')""",env['org'],hold,request)
    assert not await pool.fetchval('SELECT c24_cleanup_receipt($1,$2)',env['org'],request)
    assert not await pool.fetchval('SELECT c24_cleanup_receipt($1,$2)',uuid4(),request)
    assert not await pool.fetchval("SELECT has_function_privilege('service_role','c24_cleanup_receipt(uuid,uuid)','EXECUTE')")
    await pool.execute("""INSERT INTO gdpr_retention_hold_events
      (organization_id,hold_id,event_id,kind,authority_reference,approved_by,occurred_at)
      VALUES($1,$2,$3,'released','case:release','qa',clock_timestamp())""",env['org'],hold,uuid4())
    assert await pool.fetchval('SELECT c24_cleanup_receipt($1,$2)',env['org'],request)
    assert not await pool.fetchval('SELECT c24_cleanup_receipt($1,$2)',env['org'],request)
    assert await pool.fetchval('SELECT count(*) FROM c24_receipt_cleanup_audit WHERE organization_id=$1 AND request_id=$2',env['org'],request)==1
    assert await pool.fetchval('SELECT count(*) FROM c24_source_fences WHERE organization_id=$1 AND request_id=$2',env['org'],request)==1



async def test_receipt_cleanup_rejects_stale_transaction_snapshot(state_env):
    async with state_env['pool'].acquire() as conn:
        async with conn.transaction(isolation='repeatable_read'):
            with pytest.raises(asyncpg.InvalidTransactionStateError, match='read committed'):
                await conn.fetchval('SELECT c24_cleanup_receipt($1,$2)', state_env['org'], uuid4())

async def test_receipt_erasure_policy_hold_and_release(state_env, monkeypatch):
    from src.core.gdpr.deletion import Deletion
    from tests.core.gdpr.test_conversation_lifecycle_c24 import populated, subject, approve_conversation
    from tests.core.gdpr.test_deletion_a06 import approve_policies
    env=state_env; pool=env['pool']; org=env['org']; request=await populated(env)
    contact=await subject(env)
    await pool.execute("UPDATE messages SET status='handled',handling_type='ai' WHERE organization_id=$1 AND id=$2",org,env['message'])
    await approve_policies(pool,org)
    await approve_conversation(pool,org)
    monkeypatch.setenv('GDPR_ERASURE_EXECUTION_ENABLED','true')
    result=await Deletion(pool).request(org,'test-receipt',uuid4(),scope='contact',subject_id=contact)
    assert 'policy_required.technical_receipts' in result['manifest']['blockers']
    await pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
      retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
      VALUES($1,'technical_receipts','erase-test',true,90,false,'test:receipt','qa',
       clock_timestamp()-interval '1 minute',clock_timestamp()+interval '1 day')""",org)
    hold=uuid4()
    await pool.execute("""INSERT INTO gdpr_retention_holds
      (organization_id,hold_id,category,scope,scope_id,reason_code,authority_reference,approved_by,approved_at,review_by)
      VALUES($1,$2,'technical_receipts','record',$3,'litigation','case:receipt','qa',
       clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day')""",org,hold,request)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('SET LOCAL ROLE service_role')
        assert await conn.fetchval('SELECT c24_receipts_held($1,$2)',org,contact)
        assert not await conn.fetchval("SELECT has_table_privilege(current_user,'booking_conversation_requests','DELETE')")
    result=await Deletion(pool).request(org,'test-receipt',uuid4(),scope='contact',subject_id=contact)
    assert 'technical_receipts_held' in result['manifest']['blockers']
    assert await pool.fetchval('SELECT count(*) FROM booking_conversation_requests WHERE organization_id=$1 AND request_id=$2',org,request)==1
    await pool.execute("""INSERT INTO gdpr_retention_hold_events
      (organization_id,hold_id,event_id,kind,authority_reference,approved_by,occurred_at)
      VALUES($1,$2,$3,'released','case:release','qa',clock_timestamp())""",org,hold,uuid4())
    from src.core.gdpr.operations import Operations
    operation = await Operations(pool).create(
        org, 'test-forged-barrier', uuid4(), kind='erase', scope='contact', subject_id=contact,
    )
    # Model a backend able to insert barriers despite RLS, as in a
    # deployment whose service role has BYPASSRLS.
    await pool.execute('''CREATE POLICY synthetic_barrier_insert ON gdpr_erasure_barriers
        FOR INSERT TO service_role WITH CHECK (true)''')
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match='lifecycle owner'):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute('SET LOCAL ROLE service_role')
            await conn.execute('''INSERT INTO gdpr_erasure_barriers
               (organization_id,operation_id,scope,subject_id,identifier_digest,key_id)
               VALUES($1,$2,'contact',$3,$4,'synthetic')''', org, operation, contact, '0'*64)
    result=await Deletion(pool).request(org,'test-receipt',uuid4(),scope='contact',subject_id=contact)
    assert result['status']=='partial'
    assert await pool.fetchval('SELECT count(*) FROM booking_conversation_requests WHERE organization_id=$1 AND request_id=$2',org,request)==0
    assert await pool.fetchval('SELECT count(*) FROM c24_receipt_cleanup_audit WHERE organization_id=$1 AND request_id=$2',org,request)==1
    assert await pool.fetchval('SELECT count(*) FROM c24_source_fences WHERE organization_id=$1 AND request_id=$2',org,request)==1
