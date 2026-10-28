"""Exercise booking credential authorization against the real PostgreSQL repository."""
import json

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes.integrations import router
from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.db.repositories.external_booking_repo import ExternalBookingRepository

URL = '/api/v1/integrations/booking'
SECRET = 'synthetic-api-secret'


@pytest.mark.asyncio
async def test_booking_credentials_authorization_and_tenant_scope(pg_pool, sample_org, other_org, monkeypatch):
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
    org_a, org_b = sample_org['id'], other_org['id']
    app = FastAPI()
    app.include_router(router)
    app.state.pool = pg_pool
    identity = {'organization_id': str(org_a), 'ruolo': 'staff', 'source': 'jwt', 'aal': 'aal2'}

    async def verified_user():
        return dict(identity)

    async def organization_context():
        return dict(identity)

    app.dependency_overrides[get_current_user] = verified_user
    app.dependency_overrides[get_organization_context] = organization_context
    repository = ExternalBookingRepository(pg_pool)
    await repository.save_credentials(org_b, 'fake', {'api_key': 'tenant-b-secret'},
                                      {'mode': 'shadow', 'medical_dpa_signed': False})
    before_b = dict(await pg_pool.fetchrow('SELECT * FROM external_booking_credentials WHERE organization_id=$1', org_b))
    payload = {'provider': 'fake', 'credentials': {'api_key': SECRET}, 'mode': 'authoritative',
               'config': {'medical_dpa_signed': True}, 'organization_id': str(org_b)}

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        for role, aal in [('staff', 'aal2'), ('manager', 'aal2'), ('owner', 'aal1')]:
            identity.update(ruolo=role, aal=aal)
            response = await client.post(URL, json=payload)
            assert response.status_code == 403
            assert await pg_pool.fetchval('SELECT count(*) FROM external_booking_credentials WHERE organization_id=$1', org_a) == 0

        identity.update(ruolo='owner', aal='aal2')
        for _ in range(2):
            response = await client.post(URL, json=payload)
            assert response.status_code == 200
            assert SECRET not in response.text
        assert await pg_pool.fetchval('SELECT count(*) FROM external_booking_credentials WHERE organization_id=$1', org_a) == 1
        row = await pg_pool.fetchrow('SELECT * FROM external_booking_credentials WHERE organization_id=$1', org_a)
        config = json.loads(row['config']) if isinstance(row['config'], str) else row['config']
        assert config['medical_dpa_signed'] is True
        assert config['mode'] == 'authoritative'
        assert SECRET not in row['credentials_encrypted']
        assert (await repository.get_credentials(org_a))['credentials']['api_key'] == SECRET
        assert dict(await pg_pool.fetchrow('SELECT * FROM external_booking_credentials WHERE organization_id=$1', org_b)) == before_b

        status = await client.get(URL + '/status')
        assert status.status_code == 200
        assert status.json()['medical_dpa_signed'] is True
        assert SECRET not in status.text
        assert row['credentials_encrypted'] not in status.text
        assert 'credentials_encrypted' not in status.text

        identity.update(ruolo='manager', aal='aal1')
        patched = await client.patch(URL + '/mode', json={
            'mode': 'shadow', 'config': {'medical_dpa_signed': False}, 'organization_id': str(org_b)})
        assert patched.status_code == 200
        after = await repository.get_credentials(org_a)
        assert after['config']['mode'] == 'shadow'
        assert after['config']['medical_dpa_signed'] is True
        assert dict(await pg_pool.fetchrow('SELECT * FROM external_booking_credentials WHERE organization_id=$1', org_b)) == before_b
