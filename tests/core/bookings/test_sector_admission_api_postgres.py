"""Real PostgreSQL authorization, revision, replay and history checks."""
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
from src.core.db.repositories.booking_catalog_repo import BookingCatalogRepository
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.test_quota_reservation_lifecycle import _auth_member, _organization

pytestmark = pytest.mark.asyncio
CATALOG = json.loads((Path(__file__).parent / 'fixtures/catalogs_v2.json').read_text(encoding='utf-8'))['parrucchiere']


async def make_api(pool, *, historic=False):
    org = (await _organization(pool))['id']
    await pool.execute("UPDATE organizations SET business_profile=$2::jsonb WHERE id=$1", org,
                       json.dumps({'verticale': 'parrucchiere'}))
    auth = await _auth_member(pool, org, role='owner')
    actor = await pool.fetchval('SELECT id FROM user_profiles WHERE auth_user_id=$1', auth)
    repo = BookingRepository(pool)
    publisher = BookingCatalogRepository(repo)
    draft = copy.deepcopy(CATALOG)
    await publisher.write(org, actor, auth, action='draft_saved', expected_revision=0,
                          request_id=uuid4(), catalog=draft)
    published = await publisher.write(org, actor, auth, action='published', expected_revision=1,
                                      request_id=uuid4())
    if historic:
        contact, conversation, booking = uuid4(), uuid4(), uuid4()
        await pool.execute('INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)', contact, org, str(uuid4()))
        await pool.execute('INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)', conversation, org, contact)
        await pool.execute("""INSERT INTO bookings(id,organization_id,contact_id,id_conversazione,nome_cliente,telefono,data,ora,coperti,stato)
            VALUES($1,$2,$3,$4,'Synthetic','','2026-11-12','10:00',1,'in_attesa')""", booking, org, contact, str(conversation))
    identity = {'organization_id': str(org), 'user_id': str(actor), 'auth_user_id': str(auth),
                'ruolo': 'owner', 'source': 'jwt', 'aal': 'aal2'}

    async def current_user(): return dict(identity)
    async def organization_context(): return dict(identity)

    app = FastAPI()
    app.include_router(router, prefix='/api/bookings')
    app.state.booking_service = SimpleNamespace(repo=repo)
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_organization_context] = organization_context
    client = AsyncClient(transport=ASGITransport(app=app), base_url='http://test')
    return client, identity, org, published['published_revision']


async def test_owner_mfa_opt_in_status_revision_and_idempotent_replay(pg_pool):
    client, identity, org, revision = await make_api(pg_pool)
    async with client:
        endpoint = '/api/bookings/catalog/sector-admission'
        status = await client.get(endpoint)
        assert status.status_code == 200
        assert status.json() == {'enabled': False, 'eligible': True, 'published_revision': revision}
        request_id = str(uuid4())
        body = {'request_id': request_id, 'expected_published_revision': revision, 'enabled': True}
        first = await client.put(endpoint, json=body)
        assert first.status_code == 200 and first.json()['enabled'] is True and not first.json()['replayed']
        replay = await client.put(endpoint, json=body)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert await pg_pool.fetchval('SELECT count(*) FROM booking_sector_admission_requests WHERE organization_id=$1', org) == 1
        assert (await client.get(endpoint)).json()['enabled'] is True
        body['enabled'] = False
        assert (await client.put(endpoint, json=body)).status_code == 409
        stale = {'request_id': str(uuid4()), 'expected_published_revision': revision - 1, 'enabled': False}
        assert (await client.put(endpoint, json=stale)).status_code == 409
        identity['aal'] = 'aal1'
        assert (await client.put(endpoint, json={'request_id': str(uuid4()), 'expected_published_revision': revision, 'enabled': False})).status_code == 403
        identity['aal'] = 'aal2'; identity['ruolo'] = 'manager'
        assert (await client.get(endpoint)).status_code == 403
        assert (await client.put(endpoint, json={'request_id': str(uuid4()), 'expected_published_revision': revision, 'enabled': False})).status_code == 403


async def test_owner_cannot_enable_for_tenant_with_historical_booking(pg_pool):
    client, _, org, revision = await make_api(pg_pool, historic=True)
    async with client:
        response = await client.put('/api/bookings/catalog/sector-admission', json={
            'request_id': str(uuid4()), 'expected_published_revision': revision, 'enabled': True,
        })
        assert response.status_code == 409
        assert not await pg_pool.fetchval('SELECT EXISTS(SELECT 1 FROM booking_sector_admission_fences WHERE organization_id=$1)', org)
        assert not await pg_pool.fetchval('SELECT EXISTS(SELECT 1 FROM booking_settings WHERE organization_id=$1 AND config ? \'c24_sector_auto\')', org)
