import copy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.bookings.catalog_api import router
from src.core.db.repositories.booking_repo import BookingRepository

pytestmark = pytest.mark.asyncio
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/catalogs_v2.json').read_text(encoding='utf-8'))['ristorante']

@pytest.fixture
async def api_env(pg_pool):
    org, user, auth = uuid4(),uuid4(),uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations(id,name,business_profile) VALUES($1,'Synthetic',$2::jsonb)",org,json.dumps({'verticale':'ristorante'}))
        await conn.execute("INSERT INTO auth.users(id,email) VALUES($1,$2)",auth,f'{auth}@example.test')
        user = await conn.fetchval("SELECT id FROM user_profiles WHERE auth_user_id=$1", auth)
    identity={'organization_id':str(org),'user_id':str(user),'auth_user_id':str(auth),'ruolo':'owner','source':'jwt','aal':'aal1'}
    async def verified(): return dict(identity)
    app=FastAPI(); app.include_router(router, prefix='/api/bookings')
    app.state.booking_service=SimpleNamespace(repo=BookingRepository(pg_pool))
    app.dependency_overrides[get_current_user]=verified
    app.dependency_overrides[get_organization_context]=verified
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        yield client, identity

async def test_role_mfa_replay_and_current_permission(api_env):
    client,identity=api_env
    key=str(uuid4()); body={'expected_revision':0,'request_id':key,'catalog':copy.deepcopy(FIXTURE)}
    saved=await client.put('/api/bookings/catalog/draft',json=body)
    assert saved.status_code==200 and saved.json()['revision']==1
    pub={'expected_revision':1,'request_id':str(uuid4())}
    assert (await client.post('/api/bookings/catalog/publish',json=pub)).status_code==403
    identity['aal']='aal2'
    published=await client.post('/api/bookings/catalog/publish',json=pub)
    assert published.status_code==200 and published.json()['published_revision']==2
    identity['ruolo']='manager'
    assert (await client.get('/api/bookings/catalog')).status_code==200
    assert (await client.put('/api/bookings/catalog/draft',json=body)).status_code==403
    assert (await client.post('/api/bookings/catalog/publish',json=pub)).status_code==403
    identity['ruolo']='staff'
    assert (await client.get('/api/bookings/catalog')).status_code==403
    identity['ruolo']='owner'; identity['source']='api_key'
    assert (await client.put('/api/bookings/catalog/draft',json=body)).status_code==401

async def test_duplicate_malformed_and_scope_override(api_env):
    client,identity=api_env
    endpoint='/api/bookings/catalog/draft'
    duplicate='{"expected_revision":0,"expected_revision":0,"request_id":"'+str(uuid4())+'","catalog":{}}'
    assert (await client.put(endpoint,content=duplicate)).status_code==422
    assert (await client.put(endpoint,content='{')).status_code==422
    body={'expected_revision':0,'request_id':str(uuid4()),'catalog':copy.deepcopy(FIXTURE)}
    body['catalog']['organization_id']=str(uuid4())
    assert (await client.put(endpoint,json=body)).status_code==422
    body['catalog'].pop('organization_id')
    assert (await client.put(endpoint,json=body,headers={'X-Organization-Id':str(uuid4())})).status_code==200
    stale={'expected_revision':0,'request_id':str(uuid4()),'catalog':copy.deepcopy(FIXTURE)}
    assert (await client.put(endpoint,json=stale)).status_code==409



async def test_receipt_replay_cannot_bypass_revoked_role_or_mfa(api_env):
    client,identity=api_env
    identity['aal']='aal2'
    draft={'expected_revision':0,'request_id':str(uuid4()),'catalog':copy.deepcopy(FIXTURE)}
    assert (await client.put('/api/bookings/catalog/draft',json=draft)).status_code==200
    publication={'expected_revision':1,'request_id':str(uuid4())}
    assert (await client.post('/api/bookings/catalog/publish',json=publication)).status_code==200
    identity['aal']='aal1'
    assert (await client.post('/api/bookings/catalog/publish',json=publication)).status_code==403
    identity['aal']='aal2'; identity['ruolo']='manager'
    assert (await client.post('/api/bookings/catalog/publish',json=publication)).status_code==403
    assert (await client.put('/api/bookings/catalog/draft',json=draft)).status_code==403
