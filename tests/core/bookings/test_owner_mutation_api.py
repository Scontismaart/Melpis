from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.bookings import owner_mutation_api as api
from src.core.db.repositories.booking_repo import BookingRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def env(monkeypatch):
    identity = {'organization_id': str(uuid4()), 'auth_user_id': str(uuid4()),
                'user_id': str(uuid4()), 'ruolo': 'owner', 'source': 'jwt', 'aal': 'aal2'}
    async def verified(): return dict(identity)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/bookings')
    app.state.booking_service = SimpleNamespace(repo=BookingRepository(object()))
    app.dependency_overrides[get_current_user] = verified
    app.dependency_overrides[get_organization_context] = verified
    calls = []
    async def apply(*args, **kwargs):
        calls.append((args, kwargs))
        return {'outcome': 'allocated', 'replayed': False}
    monkeypatch.setattr(api, 'apply_booking_mutation', apply)
    body = {'allocation': {'request_id': str(uuid4()), 'booking_id': str(uuid4()),
                           'action': 'allocate', 'expected_allocation_revision': 0,
                           'expected_catalog_revision': 1, 'service_id': 'cut',
                           'appointment_date': '2026-10-28', 'appointment_time': '10:00', 'party_size': 1},
            'mutation': {'operation': 'create', 'nome_cliente': 'Synthetic', 'telefono': '', 'note': '',
                         'data': '2026-10-28', 'ora': '10:00', 'coperti': 1},
            'new_booking': {'contact_id': None, 'conversation_id': None}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client, identity, calls, body


async def test_disabled_and_owner_mfa_gate(env, monkeypatch):
    client, identity, calls, body = env
    monkeypatch.delenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', raising=False)
    assert (await client.post('/api/bookings/typed-mutations', json=body)).status_code == 404
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    identity['aal'] = 'aal1'
    assert (await client.post('/api/bookings/typed-mutations', json=body)).status_code == 403
    identity['aal'] = 'aal2'; identity['ruolo'] = 'manager'
    assert (await client.post('/api/bookings/typed-mutations', json=body)).status_code == 403
    identity['ruolo'] = 'owner'; identity['source'] = 'api_key'
    assert (await client.post('/api/bookings/typed-mutations', json=body)).status_code == 401
    assert not calls


async def test_typed_dispatch_and_reject_scope_or_invalid_input(env, monkeypatch):
    client, identity, calls, body = env
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    response = await client.post('/api/bookings/typed-mutations', json=body, headers={'X-Organization-Id': str(uuid4())})
    assert response.status_code == 200
    args, kwargs = calls[0]
    assert args[1:3] == (identity['organization_id'], identity['auth_user_id'])
    assert args[3].request_id == __import__('uuid').UUID(body['allocation']['request_id'])
    assert args[3].expected_allocation_revision == 0
    assert kwargs['mutation'].operation == 'create'
    body['organization_id'] = str(uuid4())
    assert (await client.post('/api/bookings/typed-mutations', json=body)).status_code == 422
    assert (await client.post('/api/bookings/typed-mutations', content='{"allocation":{},"allocation":{}}')).status_code == 422
    assert len(calls) == 1
