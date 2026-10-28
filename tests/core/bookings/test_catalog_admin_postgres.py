import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from src.core.bookings.catalog_contracts import validate_catalog_json
from src.core.db.repositories.booking_catalog_repo import BookingCatalogRepository, CatalogConflict, CatalogUnavailable
from src.core.db.repositories.booking_repo import BookingRepository

pytestmark = pytest.mark.asyncio
FIXTURES = json.loads((Path(__file__).parent / 'fixtures/catalogs_v2.json').read_text(encoding='utf-8'))

@pytest.fixture
async def catalog_env(pg_pool):
    org, actor, auth = uuid4(), uuid4(), uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations(id,name,business_profile) VALUES($1,'Synthetic',$2::jsonb)", org, json.dumps({'verticale':'ristorante'}))
        await conn.execute("INSERT INTO auth.users(id,email) VALUES($1,$2)",auth,f'{auth}@example.test')
        actor = await conn.fetchval("SELECT id FROM user_profiles WHERE auth_user_id=$1", auth)
    return pg_pool, BookingRepository(pg_pool), BookingCatalogRepository(BookingRepository(pg_pool)), org, actor, auth

def draft(sector='ristorante', revision=1):
    result=copy.deepcopy(FIXTURES[sector]); result['revision']=revision
    return result

async def write(env, action, rev, *, body=None, request=None, actor=None):
    _,_,repo,org,user,auth=env
    return await repo.write(org,actor or user,auth,action=action,expected_revision=rev,request_id=request or uuid4(),catalog=body)

@pytest.mark.parametrize('sector', FIXTURES)
async def test_five_sectors_draft_publish_snapshot_and_audit(catalog_env, sector):
    pool,_,repo,org,_,_=catalog_env
    async with pool.acquire() as c:
        await c.execute('UPDATE organizations SET business_profile=$2::jsonb WHERE id=$1',org,json.dumps({'verticale':sector}))
    saved=await write(catalog_env,'draft_saved',0,body=draft(sector))
    assert saved['revision']==1 and saved['published'] is None and saved['runtime_enabled'] is False
    published=await write(catalog_env,'published',1)
    assert published['revision']==published['published_revision']==2 and published['draft'] is None
    assert (await repo.read(org))['published']==published['published']
    async with pool.acquire() as c:
        snap=await c.fetchval('SELECT catalog FROM booking_catalog_publications WHERE organization_id=$1 AND revision=2',org)
        assert json.loads(snap)==published['published']
        assert await c.fetchval("SELECT count(*) FROM audit_log WHERE organization_id=$1 AND action LIKE 'booking_catalog.%'",org)==2

async def test_replay_is_current_state_and_id_collision(catalog_env):
    key=uuid4(); original=draft()
    await write(catalog_env,'draft_saved',0,body=original,request=key)
    await write(catalog_env,'published',1)
    replay=await write(catalog_env,'draft_saved',0,body=original,request=key)
    assert replay['replayed'] and replay['applied_revision']==1 and replay['revision']==2 and replay['published']
    other=draft(); other['services'][0]['name']='Different'
    with pytest.raises(CatalogConflict): await write(catalog_env,'draft_saved',0,body=other,request=key)
    with pytest.raises(CatalogConflict): await write(catalog_env,'draft_saved',0,body=original,request=key,actor=uuid4())

async def test_competing_cas_and_publish_save(catalog_env):
    results=await asyncio.gather(write(catalog_env,'draft_saved',0,body=draft()),write(catalog_env,'draft_saved',0,body=draft()),return_exceptions=True)
    assert sum(isinstance(x,dict) for x in results)==1
    assert sum(isinstance(x,CatalogConflict) for x in results)==1
    results=await asyncio.gather(write(catalog_env,'published',1),write(catalog_env,'draft_saved',1,body=draft(revision=2)),return_exceptions=True)
    assert sum(isinstance(x,dict) for x in results)==1
    assert sum(isinstance(x,CatalogConflict) for x in results)==1

async def test_legacy_writes_preserve_protected_namespace(catalog_env):
    pool,legacy,repo,org,_,_=catalog_env
    saved=await write(catalog_env,'draft_saved',0,body=draft())
    with pytest.raises(ValueError): await legacy.upsert_booking_settings_config(org,{'catalog_v2':{}})
    await legacy.upsert_booking_settings_config(org,{'other':{'enabled':True}})
    assert (await repo.read(org))['draft']==saved['draft']
    async with pool.acquire() as c:
        async with c.transaction():
            before=json.loads(await c.fetchval('SELECT config FROM booking_settings WHERE organization_id=$1',org))
            await c.execute("UPDATE booking_settings SET config='{}'::jsonb WHERE organization_id=$1",org)
            assert 'catalog_v2' not in json.loads(await c.fetchval('SELECT config FROM booking_settings WHERE organization_id=$1',org))
            await c.execute('UPDATE booking_settings SET config=$2::jsonb WHERE organization_id=$1',org,json.dumps(before))

async def test_tenant_isolation_and_corrupt_state_fails_closed(catalog_env):
    pool,_,repo,org,_,_=catalog_env
    await write(catalog_env,'draft_saved',0,body=draft())
    another=uuid4()
    async with pool.acquire() as c:
        await c.execute("INSERT INTO organizations(id,name) VALUES($1,'Other')",another)
    assert (await repo.read(another))['revision']==0
    async with pool.acquire() as c:
        await c.execute("UPDATE booking_settings SET config=jsonb_set(config,'{catalog_v2,revision}','999') WHERE organization_id=$1",org)
    with pytest.raises(CatalogUnavailable): await repo.read(org)

@pytest.mark.parametrize('mutation', ['clinical','type','sector','revision'])
async def test_invalid_draft_rejected(catalog_env,mutation):
    body=draft()
    if mutation=='clinical': body['services'][0]['fields'].append({'key':'symptoms','value_type':'text','required':True})
    elif mutation=='type': body['services'][0]['fields'][0]['value_type']='date'
    elif mutation=='sector': body['sector']='parrucchiere'
    else: body['revision']=9
    with pytest.raises(ValueError): await write(catalog_env,'draft_saved',0,body=body)
    assert (await catalog_env[2].read(catalog_env[3]))['revision']==0

async def test_publications_and_receipts_are_immutable(catalog_env):
    pool,_,_,org,_,_=catalog_env
    await write(catalog_env,'draft_saved',0,body=draft())
    await write(catalog_env,'published',1)
    async with pool.acquire() as conn:
        for table in ('booking_catalog_requests','booking_catalog_publications'):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(f'DELETE FROM {table} WHERE organization_id=$1',org)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(f'UPDATE {table} SET organization_id=organization_id WHERE organization_id=$1',org)

async def test_audit_failure_rolls_back_entire_write(catalog_env):
    pool,_,repo,org,_,_=catalog_env
    async with pool.acquire() as conn:
        await conn.execute("""CREATE FUNCTION test_catalog_audit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action LIKE 'booking_catalog.%' THEN RAISE EXCEPTION 'synthetic audit failure'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER test_catalog_audit_failure BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION test_catalog_audit_failure();""")
    with pytest.raises(asyncpg.PostgresError): await write(catalog_env,'draft_saved',0,body=draft())
    assert (await repo.read(org))['revision']==0
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT count(*) FROM booking_catalog_requests WHERE organization_id=$1',org)==0
        await conn.execute('DROP TRIGGER test_catalog_audit_failure ON audit_log; DROP FUNCTION test_catalog_audit_failure()')

async def test_catalog_acl_and_rls_deny_clients(catalog_env):
    pool,_,_,org,_,_=catalog_env
    await write(catalog_env,'draft_saved',0,body=draft())
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT has_table_privilege('authenticated','booking_settings','SELECT')")
        for role in ('anon','authenticated'):
            for table in ('booking_settings','booking_catalog_requests','booking_catalog_publications'):
                assert not await conn.fetchval('SELECT has_table_privilege($1,$2,\'UPDATE\')',role,table)
                assert not await conn.fetchval('SELECT has_any_column_privilege($1,$2,\'UPDATE\')',role,table)
        # A future permissive regrant/policy must not bypass restrictive RLS.
        async with conn.transaction():
            await conn.execute('GRANT USAGE ON SCHEMA public,auth TO authenticated; GRANT SELECT ON user_profiles,organization_memberships TO authenticated; GRANT UPDATE ON booking_settings TO authenticated')
            await conn.execute('CREATE POLICY test_catalog_allow ON booking_settings FOR UPDATE TO authenticated USING(true) WITH CHECK(true)')
            await conn.execute('SET LOCAL ROLE authenticated')
            status=await conn.execute("UPDATE booking_settings SET config='{}'::jsonb WHERE organization_id=$1",org)
            assert status=='UPDATE 0'

async def test_erasure_barrier_blocks_new_catalog_writes(catalog_env):
    pool,_,repo,org,_,_=catalog_env
    operation=uuid4()
    async with pool.acquire() as conn:
        await conn.execute("""INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,kind,scope,status)
            VALUES($1,$2,'synthetic',$3,$4,'erase','organization','running')""",operation,org,uuid4(),'0'*64)
        await conn.execute("INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope) VALUES($1,$2,'organization')",org,operation)
    with pytest.raises(asyncpg.PostgresError): await write(catalog_env,'draft_saved',0,body=draft())
    assert (await repo.read(org))['revision']==0



async def test_nonempty_evidence_cannot_be_truncated(catalog_env):
    pool,_,_,org,_,_=catalog_env
    await write(catalog_env,'draft_saved',0,body=draft())
    await write(catalog_env,'published',1)
    async with pool.acquire() as conn:
        for table in ('booking_catalog_requests','booking_catalog_publications'):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(f'TRUNCATE {table}')
            assert await conn.fetchval(f'SELECT count(*) FROM {table} WHERE organization_id=$1',org)>0
            assert not await conn.fetchval("SELECT has_table_privilege('service_role',$1,'TRUNCATE')",table)

MIGRATION = (Path(__file__).parents[3] / 'src/core/db/migrations/068_booking_catalog_admin.sql').read_text(encoding='utf-8')

@pytest.mark.parametrize('grant_sql', [
    'GRANT UPDATE(config) ON booking_settings TO {role}',
    'GRANT SELECT ON booking_catalog_requests TO {role}',
])
async def test_inherited_client_grants_abort_migration_atomically(catalog_env, grant_sql):
    pool,_,_,org,_,_=catalog_env
    await write(catalog_env,'draft_saved',0,body=draft())
    role='catalog_test_'+uuid4().hex
    async with pool.acquire() as conn:
        try:
            await conn.execute(f'CREATE ROLE {role}; GRANT {role} TO authenticated')
            await conn.execute(grant_sql.format(role=role))
            async with conn.transaction():
                with pytest.raises(asyncpg.PostgresError):
                    async with conn.transaction(): await conn.execute(MIGRATION)
                assert await conn.fetchval('SELECT pg_has_role(\'authenticated\',$1,\'MEMBER\')',role)
                assert await conn.fetchval('SELECT count(*) FROM booking_catalog_requests WHERE organization_id=$1',org)==1
                if 'UPDATE' in grant_sql:
                    assert await conn.fetchval("SELECT has_column_privilege($1,'booking_settings','config','UPDATE')",role)
                else:
                    assert await conn.fetchval("SELECT has_table_privilege($1,'booking_catalog_requests','SELECT')",role)
        finally:
            await conn.execute(f'REVOKE {role} FROM authenticated; DROP OWNED BY {role}; DROP ROLE {role}')

async def test_service_role_forced_truncate_cannot_erase_evidence(catalog_env):
    pool,_,_,org,_,_=catalog_env
    await write(catalog_env,'draft_saved',0,body=draft())
    await write(catalog_env,'published',1)
    async with pool.acquire() as conn:
        for table in ('booking_catalog_requests','booking_catalog_publications'):
            async with conn.transaction():
                await conn.execute(f'GRANT TRUNCATE ON {table} TO service_role')
                await conn.execute('SET LOCAL ROLE service_role')
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with conn.transaction(): await conn.execute(f'TRUNCATE {table}')
                await conn.execute('RESET ROLE')
                await conn.execute(f'REVOKE TRUNCATE ON {table} FROM service_role')
            assert await conn.fetchval(f'SELECT count(*) FROM {table} WHERE organization_id=$1',org)>0

async def test_reapply_caps_broad_service_role_grants(catalog_env):
    pool,_,_,_,_,_=catalog_env
    async with pool.acquire() as conn:
        for table in ('booking_catalog_requests','booking_catalog_publications'):
            await conn.execute(f'GRANT ALL ON {table} TO service_role')
        await conn.execute(MIGRATION)
        for table in ('booking_catalog_requests','booking_catalog_publications'):
            for privilege in ('SELECT','INSERT'):
                assert await conn.fetchval('SELECT has_table_privilege($1,$2,$3)','service_role',table,privilege)
            for privilege in ('UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
                assert not await conn.fetchval('SELECT has_table_privilege($1,$2,$3)','service_role',table,privilege)

async def test_owner_may_truncate_empty_evidence(catalog_env):
    pool,_,_,_,_,_=catalog_env
    async with pool.acquire() as conn:
        await conn.execute('TRUNCATE booking_catalog_requests,booking_catalog_publications')

async def wait_until_blocked(pool, blocker_pid):
    async def observe():
        while True:
            async with pool.acquire() as conn:
                if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1=ANY(pg_blocking_pids(pid)))', blocker_pid):
                    return
            await asyncio.sleep(0)
    await asyncio.wait_for(observe(), timeout=5)

async def test_publication_blocks_legacy_writer_and_preserves_both(catalog_env):
    pool,legacy,repo,org,_,_=catalog_env
    await write(catalog_env,'draft_saved',0,body=draft())
    async with pool.acquire() as holder:
        tx=holder.transaction(); await tx.start()
        try:
            await holder.fetchrow('SELECT id FROM booking_settings WHERE organization_id=$1 FOR UPDATE',org)
            publication=asyncio.create_task(write(catalog_env,'published',1))
            await wait_until_blocked(pool,holder.get_server_pid())
            legacy_task=asyncio.create_task(legacy.upsert_booking_settings_config(org,{'other':{'updated':True}}))
            # Publisher holds the capacity lock and waits for holder; legacy must wait for publisher.
            async def blocked_chain():
                while True:
                    async with pool.acquire() as conn:
                        found=await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity waiter
                            JOIN pg_stat_activity publisher ON publisher.pid=ANY(pg_blocking_pids(waiter.pid))
                            WHERE $1=ANY(pg_blocking_pids(publisher.pid)))""",holder.get_server_pid())
                    if found: return
                    await asyncio.sleep(0)
            await asyncio.wait_for(blocked_chain(),5)
        finally:
            await tx.rollback()
        await asyncio.wait_for(asyncio.gather(publication,legacy_task),5)
    state=await repo.read(org); settings=await legacy.get_booking_settings(org)
    assert state['published_revision']==2 and settings['config']['other']=={'updated':True}

async def test_publication_waits_for_legacy_settings_transaction(catalog_env):
    pool,legacy,repo,org,_,_=catalog_env
    await write(catalog_env,'draft_saved',0,body=draft())
    async with legacy.booking_settings_lock(org):
        async with legacy._connection() as conn:
            pid=conn.get_server_pid()
            publication=asyncio.create_task(write(catalog_env,'published',1))
            await wait_until_blocked(pool,pid)
            settings=await legacy.get_booking_settings(org)
            await legacy.save_booking_settings_locked(org,slot_minutes=settings['slot_minutes'],
                fasce_orarie=settings['fasce_orarie'],capienze_orarie=settings['capienze_orarie'],
                config={'other':{'updated':True}})
    await asyncio.wait_for(publication,5)
    assert (await repo.read(org))['published_revision']==2
    assert (await legacy.get_booking_settings(org))['config']['other']=={'updated':True}
