"""C2.4 dark state persistence tests; requires isolated PostgreSQL and 070."""
import asyncio
import copy
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from src.core.billing.quota_reservations import QuotaReservations, validate_final_response
from src.core.bookings.catalog_contracts import validate_catalog_json
from src.core.bookings.conversation_state import parse_structured_response_json
from src.core.db.repositories.booking_catalog_repo import BookingCatalogRepository
from src.core.db.repositories.booking_repo import BookingRepository
from src.core.db.repositories.booking_conversation_repo import BookingConversationRepository, ConversationStateDenied, ConversationStateUnavailable
from src.core.bookings.conversation_state import StateConflict, render_confirmation_prompt
from tests.core.test_quota_reservation_lifecycle import (
    _auth_member, _inbound_message, _inbound_operation, _organization, _used,
)

pytestmark=pytest.mark.asyncio
CATALOG=json.loads((Path(__file__).parent / 'fixtures/catalogs_v2.json').read_text(encoding='utf-8'))['ristorante']


@pytest.fixture
async def state_env(pg_pool):
    org=await _organization(pg_pool); oid=org['id']
    await pg_pool.execute("UPDATE organizations SET timezone='Europe/Rome',business_profile=$2::jsonb WHERE id=$1",oid,json.dumps({'verticale':'ristorante'}))
    auth=await _auth_member(pg_pool,oid,role='owner')
    actor=await pg_pool.fetchval('SELECT id FROM user_profiles WHERE auth_user_id=$1',auth)
    catalog_repo=BookingCatalogRepository(BookingRepository(pg_pool))
    await catalog_repo.write(oid,actor,auth,action='draft_saved',expected_revision=0,request_id=uuid4(),catalog=copy.deepcopy(CATALOG))
    published=await catalog_repo.write(oid,actor,auth,action='published',expected_revision=1,request_id=uuid4())
    scoped=validate_catalog_json(json.dumps(published['published']),organization_id=oid)
    message=await _inbound_message(pg_pool,oid,canale='whatsapp')
    conv=await pg_pool.fetchval('SELECT conversation_id FROM messages WHERE id=$1',message)
    return {'pool':pg_pool,'org':oid,'auth':auth,'actor':actor,'catalog':scoped,'conv':conv,
            'message':message,'repo':BookingConversationRepository(pg_pool)}


async def commit_source(env, message, text, fields, timestamp):
    pool,oid=env['pool'],env['org']; wam=f'synthetic-{uuid4()}'
    await pool.execute("""UPDATE messages SET wam_id=$3,content_text=$4,content=$5::jsonb
        WHERE organization_id=$1 AND id=$2""",oid,message,wam,text,
        json.dumps({'id':wam,'timestamp':str(timestamp),'type':'text','text':{'body':text}}))
    op=_inbound_operation(oid,message,text=text)
    quota=QuotaReservations(pool); reservation=await quota.reserve(op)
    assert reservation['status']=='reserved'
    payload={'risposta':'Richiesta ricevuta.','richiede_umano':False,'motivo':'',
             'booking_state_proposal':{'intent':'booking','slots':[{'field':k,'value':v} for k,v in fields.items()]}}
    response=parse_structured_response_json(json.dumps(payload),catalog=env['catalog'],organization_id=oid)
    saved=await quota.commit(op,reservation['ticket'],validate_final_response(op,reservation['ticket'],response))
    assert saved['booking_state_proposal']['intent'] == 'booking'
    assert saved['booking_state_proposal']['slots'] == payload['booking_state_proposal']['slots']
    return op


async def apply(env, source, *, request=None, revision=0, catalog_revision=2):
    return await env['repo'].apply_from_a04(env['org'],env['auth'],env['conv'],source,
                                            request_id=request or uuid4(),expected_revision=revision,expected_catalog_revision=catalog_revision)


async def test_committed_a04_apply_and_replay_do_not_charge_again(state_env):
    env=state_env; source=env['message']; request=uuid4()
    await commit_source(env,source,'2026-11-12',{'service_id':'standard','appointment_date':'2026-11-12'},1700000000)
    before=await _used(env['pool'],env['org'])
    assert before==1
    first=await apply(env,source,request=request)
    replay=await apply(env,source,request=request)
    assert first['applied_revision']==1 and not first['replayed']
    assert replay['replayed'] and replay['applied_revision']==1
    assert replay['state']==first['state']
    assert await _used(env['pool'],env['org'])==before
    assert (await env['repo'].read(env['org'],env['auth'],env['conv']))['state']==first['state']
    assert first['state'].runtime_enabled is False and not first['state'].confirmation_authorized


async def test_read_is_observational_and_does_not_consume_quota(state_env):
    env=state_env; before=await _used(env['pool'],env['org'])
    assert (await env['repo'].read(env['org'],env['auth'],env['conv']))['state'] is None
    assert await _used(env['pool'],env['org'])==before==0

async def second_source(env, timestamp, text='11:00'):
    message=uuid4()
    await env['pool'].execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,status)
        VALUES($1,$2,$3,'inbound','text','{}'::jsonb,'processing')""",message,env['org'],env['conv'])
    await commit_source(env,message,text,{'appointment_time':text},timestamp)
    return message


async def test_two_writers_with_same_expected_revision_only_one_commits(state_env):
    env=state_env
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard','appointment_date':'2026-11-12'},1700000000)
    later=await second_source(env,1700000001)
    async with env['pool'].acquire() as holder:
        tx=holder.transaction(); await tx.start()
        try:
            await holder.fetchrow('SELECT id FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE',env['org'],env['conv'])
            tasks=[asyncio.create_task(apply(env,source)) for source in (env['message'],later)]
            async def both_blocked():
                while True:
                    count=await env['pool'].fetchval('WITH RECURSIVE blocked(pid) AS (SELECT pid FROM pg_stat_activity WHERE $1=ANY(pg_blocking_pids(pid)) UNION SELECT a.pid FROM pg_stat_activity a JOIN blocked b ON b.pid=ANY(pg_blocking_pids(a.pid))) SELECT count(*) FROM blocked',holder.get_server_pid())
                    if count>=2: return
                    await asyncio.sleep(0)
            await asyncio.wait_for(both_blocked(),5)
        finally:
            await tx.rollback()
    results=await asyncio.wait_for(asyncio.gather(*tasks,return_exceptions=True),10)
    assert sum(isinstance(result,dict) for result in results)==1
    assert sum(isinstance(result,StateConflict) for result in results)==1
    state=(await env['repo'].read(env['org'],env['auth'],env['conv']))['state']
    assert state.revision==1
    assert await _used(env['pool'],env['org'])==2

async def test_foreign_conversation_or_principal_cannot_read_or_apply(state_env):
    env=state_env; foreign_org=await _organization(env['pool']); foreign_auth=await _auth_member(env['pool'],foreign_org['id'],role='owner')
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard','appointment_date':'2026-11-12'},1700000000)
    with pytest.raises(PermissionError):
        await env['repo'].read(env['org'],foreign_auth,env['conv'])
    with pytest.raises(PermissionError):
        await env['repo'].apply_from_a04(foreign_org['id'],foreign_auth,env['conv'],env['message'],request_id=uuid4(),expected_revision=0,expected_catalog_revision=2)
    assert (await env['repo'].read(env['org'],env['auth'],env['conv']))['state'] is None
    assert await _used(env['pool'],env['org'])==1


async def test_revoked_membership_rejects_existing_evidence(state_env):
    env=state_env
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await env['pool'].execute('DELETE FROM organization_memberships WHERE organization_id=$1 AND user_id=$2',env['org'],env['actor'])
    with pytest.raises(PermissionError): await apply(env,env['message'])
    with pytest.raises(PermissionError): await env['repo'].read(env['org'],env['auth'],env['conv'])
    assert await _used(env['pool'],env['org'])==1


async def test_canonical_a04_tamper_is_rejected_without_state(state_env):
    env=state_env
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await env['pool'].execute("UPDATE messages SET ai_reply_cache=jsonb_set(ai_reply_cache,'{booking_state_proposal,intent}', '\"cancel\"'::jsonb) WHERE organization_id=$1 AND id=$2",env['org'],env['message'])
    with pytest.raises(ConversationStateUnavailable): await apply(env,env['message'])
    assert (await env['repo'].read(env['org'],env['auth'],env['conv']))['state'] is None
    assert await _used(env['pool'],env['org'])==1

@pytest.mark.parametrize('timestamp',[1700000000,1699999999])
async def test_equal_or_older_provider_timestamp_fails_closed(state_env,timestamp):
    env=state_env
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard','appointment_date':'2026-11-12'},1700000000)
    original=(await apply(env,env['message']))['state']
    late=await second_source(env,timestamp)
    with pytest.raises(StateConflict): await apply(env,late,revision=1)
    assert (await env['repo'].read(env['org'],env['auth'],env['conv']))['state']==original
    assert await _used(env['pool'],env['org'])==2

@pytest.mark.parametrize('scope',['organization','contact'])
async def test_erasure_barrier_blocks_apply_and_read(state_env,scope):
    env=state_env
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    subject=await env['pool'].fetchval('SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2',env['org'],env['conv']) if scope=='contact' else None
    operation=uuid4()
    await env['pool'].execute("""INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,kind,scope,status,subject_id)
        VALUES($1,$2,'synthetic',$3,$4,'erase',$5,'running',$6)""",operation,env['org'],uuid4(),'a'*64,scope,subject)
    await env['pool'].execute("""INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope,subject_id,identifier_digest,key_id)
        VALUES($1,$2,$3,$4,$5,$6)""",env['org'],operation,scope,subject,'b'*64 if subject else None,'test' if subject else None)
    with pytest.raises(PermissionError): await apply(env,env['message'])
    with pytest.raises(PermissionError): await env['repo'].read(env['org'],env['auth'],env['conv'])
    assert await _used(env['pool'],env['org'])==1


async def test_disabled_lifecycle_rejects_access(state_env):
    env=state_env
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await env['pool'].execute('UPDATE auth_access_lifecycle SET disabled_at=now() WHERE auth_user_id=$1',env['auth'])
    with pytest.raises(PermissionError): await apply(env,env['message'])
    with pytest.raises(PermissionError): await env['repo'].read(env['org'],env['auth'],env['conv'])
    assert await _used(env['pool'],env['org'])==1

async def test_confirmation_requires_live_sent_prompt_and_explicit_later_reply(state_env):
    env=state_env; pool=env['pool']; oid=env['org']
    await commit_source(env,env['message'],'2026-11-12 10:00',{
        'service_id':'standard','customer_name':True,'appointment_date':'2026-11-12',
        'appointment_time':'10:00','party_size':2},1700000000)
    state=(await apply(env,env['message']))['state']
    assert not state.missing_fields
    prompt,reply=uuid4(),uuid4(); prompt_wam=f'synthetic-prompt-{uuid4()}'; reply_wam=f'synthetic-reply-{uuid4()}'
    body=render_confirmation_prompt(state,prompt)
    await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id,sent_at)
        VALUES($1,$2,$3,'outbound','text','{}'::jsonb,'sent',$4,$5,clock_timestamp()-interval '120 seconds')""",prompt,oid,env['conv'],body,prompt_wam)
    stamp=await pool.fetchval("SELECT floor(extract(epoch from clock_timestamp()-interval '60 seconds'))::bigint")
    text=f'CONFIRM {prompt}'
    await pool.execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id)
        VALUES($1,$2,$3,'inbound','text',$4::jsonb,'processing',$5,$6)""",reply,oid,env['conv'],json.dumps({'id':reply_wam,'type':'text','timestamp':str(stamp),'text':{'body':text}}),text,reply_wam)
    request=uuid4(); before=await _used(pool,oid)
    with pytest.raises(ConversationStateDenied):
        await env['repo'].confirm(oid,env['auth'],env['conv'],reply,uuid4(),request_id=uuid4(),expected_revision=1,expected_catalog_revision=2)
    assert (await env['repo'].read(oid,env['auth'],env['conv']))['state'] == state
    result=await env['repo'].confirm(oid,env['auth'],env['conv'],reply,prompt,request_id=request,expected_revision=1,expected_catalog_revision=2)
    assert result['state'].details_confirmed and result['applied_revision']==2
    assert result['confirmation_authorized'] is False and result['runtime_enabled'] is False
    retry=await env['repo'].confirm(oid,env['auth'],env['conv'],reply,prompt,request_id=request,expected_revision=1,expected_catalog_revision=2)
    assert retry['replayed'] and retry['state']==result['state']
    assert await _used(pool,oid)==before==1
    catalog_repo=BookingCatalogRepository(BookingRepository(pool))
    draft=copy.deepcopy(CATALOG); draft['revision']=3
    await catalog_repo.write(oid,env['actor'],env['auth'],action='draft_saved',expected_revision=2,request_id=uuid4(),catalog=draft)
    await catalog_repo.write(oid,env['actor'],env['auth'],action='published',expected_revision=3,request_id=uuid4())
    stale=await env['repo'].read(oid,env['auth'],env['conv'])
    assert stale['state'].details_confirmed and stale['catalog_stale']
    assert stale['current_catalog_revision']==4 and stale['details_confirmed_current'] is False
    await pool.execute('UPDATE messages SET deleted_at=now() WHERE organization_id=$1 AND id=$2',oid,prompt)
    assert await pool.fetchval('SELECT count(*) FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2',oid,env['conv']) == 0
    assert await pool.fetchval('SELECT count(*) FROM booking_conversation_requests WHERE organization_id=$1 AND conversation_id=$2',oid,env['conv']) == 0
    assert (await env['repo'].read(oid,env['auth'],env['conv']))['state'] is None
    with pytest.raises(ConversationStateDenied):
        await env['repo'].confirm(oid,env['auth'],env['conv'],reply,prompt,request_id=uuid4(),expected_revision=0,expected_catalog_revision=4)


async def test_wrong_catalog_revision_does_not_persist_or_charge_again(state_env):
    env=state_env
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    with pytest.raises((StateConflict,ConversationStateUnavailable)):
        await apply(env,env['message'],catalog_revision=3)
    assert (await env['repo'].read(env['org'],env['auth'],env['conv']))['state'] is None
    assert await _used(env['pool'],env['org'])==1


async def test_state_tables_deny_client_roles_and_receipts_are_immutable(state_env):
    pool=state_env['pool']
    for table in ('booking_conversation_states','booking_conversation_requests'):
        for role in ('anon','authenticated'):
            for privilege in ('SELECT','INSERT','UPDATE','DELETE'):
                assert not await pool.fetchval('SELECT has_table_privilege($1,$2,$3)',role,table,privilege)
    for privilege in ('UPDATE','DELETE','TRUNCATE'):
        assert not await pool.fetchval("SELECT has_table_privilege('service_role','booking_conversation_requests',$1)",privilege)

MIGRATION = Path('src/core/db/migrations/070_booking_conversation_state_dark.sql')


async def test_070_replay_preserves_preexisting_acl_and_backend_limits(state_env):
    pool=state_env['pool']; sql=MIGRATION.read_text(encoding='utf-8')
    async with pool.acquire() as conn:
        before=await conn.fetchval("SELECT relacl::text FROM pg_class WHERE oid='bookings'::regclass")
        await conn.execute(sql)
        assert await conn.fetchval("SELECT relacl::text FROM pg_class WHERE oid='bookings'::regclass")==before
        for table in ('booking_conversation_states','booking_conversation_requests'):
            assert await conn.fetchval('SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass',table)
            assert await conn.fetchval("SELECT has_table_privilege('service_role',$1,'SELECT')",table)
            assert not await conn.fetchval("SELECT has_table_privilege('service_role',$1,'TRUNCATE')",table)


@pytest.mark.parametrize('role',['anon','authenticated'])
@pytest.mark.parametrize('statement',['SELECT * FROM booking_conversation_states','INSERT INTO booking_conversation_states DEFAULT VALUES','TRUNCATE booking_conversation_states'])
async def test_actual_client_operations_denied(state_env,role,statement):
    import asyncpg
    async with state_env['pool'].acquire() as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute('GRANT USAGE ON SCHEMA public TO anon,authenticated')
                await conn.execute(f'SET LOCAL ROLE {role}')
                await conn.execute(statement)


@pytest.mark.parametrize('role,privilege,admin',[
    ('authenticated','SELECT',False),('authenticated','SELECT',True),
    ('service_role','TRUNCATE',False),('service_role','TRUNCATE',True),
])
async def test_070_rejects_indirect_set_or_admin_role_grants_atomically(state_env,role,privilege,admin):
    import asyncpg
    pool=state_env['pool']; sql=MIGRATION.read_text(encoding='utf-8'); helper='state_acl_'+uuid4().hex
    async with pool.acquire() as conn:
        tx=conn.transaction(); await tx.start()
        try:
            await conn.execute(f'CREATE ROLE {helper}')
            opts='ADMIN TRUE, INHERIT FALSE, SET FALSE' if admin else 'INHERIT FALSE, SET TRUE'
            await conn.execute(f'GRANT {helper} TO {role} WITH {opts}')
            await conn.execute(f'GRANT {privilege} ON booking_conversation_states TO {helper}')
            before=await conn.fetchval("SELECT relacl::text FROM pg_class WHERE oid='bookings'::regclass")
            with pytest.raises(asyncpg.PostgresError,match='privilege requires review'):
                async with conn.transaction(): await conn.execute(sql)
            assert await conn.fetchval("SELECT relacl::text FROM pg_class WHERE oid='bookings'::regclass")==before
            assert await conn.fetchval('SELECT has_table_privilege($1,$2,$3)',helper,'booking_conversation_states',privilege)
        finally: await tx.rollback()


async def test_backend_can_insert_read_and_update_scoped_state(state_env):
    from src.core.bookings.conversation_state import new_state,state_digest
    env=state_env; pool=env['pool']
    state=new_state(env['org'],UUID(str(env['conv'])),env['catalog']).model_copy(update={'revision':1,'last_source_message_id':env['message']})
    async with pool.acquire() as conn:
        tx=conn.transaction(); await tx.start()
        try:
            await conn.execute('ALTER ROLE service_role BYPASSRLS; GRANT SELECT ON conversations,contacts,messages,gdpr_erasure_barriers,booking_catalog_publications TO service_role')
            await conn.execute('SET LOCAL ROLE service_role')
            await conn.execute('''INSERT INTO booking_conversation_states
                (organization_id,conversation_id,revision,catalog_revision,source_message_id,provider_timestamp,snapshot,snapshot_hash,subject_id)
                VALUES($1,$2,1,2,$3,1700000000,$4::jsonb,$5,
                  (SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2))''',env['org'],env['conv'],env['message'],state.model_dump_json(),state_digest(state))
            assert await conn.fetchval('SELECT revision FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2',env['org'],env['conv'])==1
            await conn.execute('UPDATE booking_conversation_states SET updated_at=now() WHERE organization_id=$1 AND conversation_id=$2',env['org'],env['conv'])
        finally: await tx.rollback()

@pytest.mark.parametrize('foreign_kind',['source','prompt'])
async def test_composite_message_fk_rejects_other_conversation(state_env,foreign_kind):
    import asyncpg
    env=state_env; pool=env['pool']; oid=env['org']
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'])
    other=await _inbound_message(pool,oid,canale='whatsapp')
    own_source=uuid4()
    await pool.execute("INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,status) VALUES($1,$2,$3,'inbound','text','{}'::jsonb,'processing')",own_source,oid,env['conv'])
    async with pool.acquire() as conn:
        tx=conn.transaction(); await tx.start()
        try:
            # Isolate the declarative composite FK from the separate scope guard.
            await conn.execute('ALTER TABLE booking_conversation_requests DISABLE TRIGGER conversation_scope_guard')
            source=other if foreign_kind=='source' else own_source
            prompt=other if foreign_kind=='prompt' else None
            action='confirmation' if prompt else 'proposal'
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                async with conn.transaction():
                    await conn.execute('''INSERT INTO booking_conversation_requests
                        (organization_id,request_id,conversation_id,source_message_id,actor_user_id,action,payload_hash,applied_revision,canonical_response_hash,prompt_message_id,prompt_body_hash)
                        VALUES($1,$2,$3,$4,$5,$6,$7,2,$8,$9,$10)''',oid,uuid4(),env['conv'],source,env['actor'],action,'a'*64,'b'*64 if not prompt else None,prompt,'c'*64 if prompt else None)
        finally: await tx.rollback()

async def test_source_from_different_conversation_cannot_create_state(state_env):
    env=state_env
    foreign=await _inbound_message(env['pool'],env['org'],canale='whatsapp')
    await commit_source(env,foreign,'2026-11-12',{'service_id':'standard'},1700000000)
    with pytest.raises(ConversationStateDenied): await apply(env,foreign)
    assert (await env['repo'].read(env['org'],env['auth'],env['conv']))['state'] is None
    assert await _used(env['pool'],env['org'])==1

async def test_worker_identity_cannot_impersonate_owner(state_env):
    env=state_env
    op=await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    with pytest.raises(PermissionError):
        await env['repo'].apply_from_a04(env['org'],op.actor_id,env['conv'],env['message'],request_id=uuid4(),expected_revision=0,expected_catalog_revision=2)
    assert (await env['repo'].read(env['org'],env['auth'],env['conv']))['state'] is None
    assert await _used(env['pool'],env['org'])==1


async def test_withdrawn_consent_denies_read_and_historical_replay(state_env):
    env=state_env; request=uuid4()
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'],request=request)
    subject=await env['pool'].fetchval('SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2',env['org'],env['conv'])
    await env['pool'].execute("UPDATE contacts SET consent_status='withdrawn' WHERE organization_id=$1 AND id=$2",env['org'],subject)
    with pytest.raises(PermissionError): await env['repo'].read(env['org'],env['auth'],env['conv'])
    with pytest.raises(PermissionError): await apply(env,env['message'],request=request)
    await env['pool'].execute("UPDATE contacts SET consent_status='granted' WHERE organization_id=$1 AND id=$2",env['org'],subject)
    assert (await env['repo'].read(env['org'],env['auth'],env['conv']))['state'] is None
    with pytest.raises(ConversationStateDenied): await apply(env,env['message'],request=request)
    assert await _used(env['pool'],env['org'])==1


async def test_catalog_change_marks_existing_state_stale_and_not_currently_confirmed(state_env):
    env=state_env
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    old=(await apply(env,env['message']))['state']
    catalog_repo=BookingCatalogRepository(BookingRepository(env['pool']))
    body=copy.deepcopy(CATALOG); body['revision']=3
    await catalog_repo.write(env['org'],env['actor'],env['auth'],action='draft_saved',expected_revision=2,request_id=uuid4(),catalog=body)
    await catalog_repo.write(env['org'],env['actor'],env['auth'],action='published',expected_revision=3,request_id=uuid4())
    result=await env['repo'].read(env['org'],env['auth'],env['conv'])
    assert result['state']==old and result['current_catalog_revision']==4
    assert result['catalog_stale'] is True and result['details_confirmed_current'] is False
    assert await _used(env['pool'],env['org'])==1


async def test_a04_validator_rejects_unsealed_tamper_and_accepts_guardrail_trim(state_env):
    from pydantic import BaseModel, ValidationError
    from src.core.bookings.conversation_state import StructuredConversationResponse
    env=state_env; pool=env['pool']; oid=env['org']; source=env['message']
    text='2026-11-12'; wam=f'synthetic-{uuid4()}'
    await pool.execute('UPDATE messages SET wam_id=$3,content_text=$4,content=$5::jsonb WHERE organization_id=$1 AND id=$2',oid,source,wam,text,json.dumps({'id':wam,'type':'text','timestamp':'1700000000','text':{'body':text}}))
    operation=_inbound_operation(oid,source,text=text); quota=QuotaReservations(pool)
    reservation=await quota.reserve(operation); ticket=reservation['ticket']
    raw={'risposta':'**Richiesta ricevuta.**','richiede_umano':False,'motivo':'',
         'booking_state_proposal':{'intent':'booking','slots':[{'field':'service_id','value':'standard'}]}}
    valid=parse_structured_response_json(json.dumps(raw),catalog=env['catalog'],organization_id=oid)
    with pytest.raises(ValidationError):
        StructuredConversationResponse(risposta='OK',richiede_umano=False,motivo='',booking_state_proposal=valid.booking_state_proposal,booking_catalog_revision=2)
    with pytest.raises(ValidationError): valid.booking_catalog_revision=3
    with pytest.raises(ValueError): valid.model_copy(update={'booking_catalog_revision':3})
    bad_proposal=valid.booking_state_proposal.model_copy(update={'intent':'cancel'})
    with pytest.raises(ValueError): valid.model_copy(update={'booking_state_proposal':bad_proposal})
    bypass=BaseModel.model_copy(valid,update={'booking_state_proposal':bad_proposal})
    with pytest.raises(ValueError): validate_final_response(operation,ticket,bypass)
    from src.core.billing.suspension import AIGenerationBlocked
    blocked=valid.model_copy(update={'risposta':''})
    with pytest.raises(AIGenerationBlocked): validate_final_response(operation,ticket,blocked)
    assert await _used(pool,oid)==0
    final=validate_final_response(operation,ticket,valid)
    saved=await quota.commit(operation,ticket,final)
    assert saved['booking_state_proposal']['intent']=='booking'
    assert saved['risposta']=='Richiesta ricevuta.'
    assert await _used(pool,oid)==1


async def test_removing_proposal_cache_revokes_state_and_replay(state_env):
    env=state_env; pool=env['pool']; org=env['org']; conv=env['conv']
    await commit_source(env,env['message'],'2026-11-12',{'service_id':'standard'},1700000000)
    await apply(env,env['message'])
    await pool.execute("UPDATE messages SET ai_reply_cache=ai_reply_cache - 'booking_state_proposal' WHERE organization_id=$1 AND id=$2",org,env['message'])
    assert await pool.fetchval('SELECT count(*) FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2',org,conv)==0
    assert await pool.fetchval('SELECT count(*) FROM booking_conversation_requests WHERE organization_id=$1 AND conversation_id=$2',org,conv)==0
    assert await pool.fetchval('SELECT reason FROM booking_conversation_revocations WHERE organization_id=$1 AND conversation_id=$2',org,conv)=='proposal_removed'
    assert (await env['repo'].read(org,env['auth'],conv))['state'] is None
    with pytest.raises(ConversationStateDenied): await apply(env,env['message'])
