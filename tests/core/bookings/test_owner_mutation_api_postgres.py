"""Registered route integration against the actual booking transaction."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.bookings.routes import router
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.bookings.test_allocation_engine_postgres import create_environment

pytestmark = pytest.mark.asyncio


async def test_registered_owner_create_replay_authorization_and_stale_revision(pg_pool, monkeypatch):
    env = await create_environment(pg_pool)
    other = await create_environment(pg_pool)
    identity = {'organization_id': str(env.org), 'user_id': str(env.actor),
                'auth_user_id': str(env.auth), 'ruolo': 'owner', 'source': 'jwt', 'aal': 'aal2'}
    async def verified(): return dict(identity)
    app = FastAPI()
    app.include_router(router)
    app.state.booking_service = SimpleNamespace(repo=BookingRepository(pg_pool))
    app.dependency_overrides[get_current_user] = verified
    app.dependency_overrides[get_organization_context] = verified
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    booking = uuid4()
    allocation = env.request(booking)
    body = {'allocation': allocation.model_dump(mode='json'),
            'mutation': {'operation': 'create', 'nome_cliente': 'Synthetic', 'telefono': '', 'note': '',
                         'data': '2026-10-28', 'ora': '10:00', 'coperti': 1},
            'new_booking': {'contact_id': None, 'conversation_id': None}}
    endpoint = '/api/bookings/typed-mutations'
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        identity['ruolo'] = 'manager'
        assert (await client.post(endpoint, json=body)).status_code == 403
        identity['ruolo'] = 'owner'
        first = await client.post(endpoint, json=body)
        assert first.status_code == 200 and first.json()['outcome'] == 'allocated'
        replay = await client.post(endpoint, json=body)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        stale = {'allocation': env.request(booking, action='cancel', allocation_revision=0).model_dump(mode='json'),
                 'mutation': {'operation': 'cancel', 'nome_cliente': '', 'telefono': '', 'note': ''}}
        assert (await client.post(endpoint, json=stale)).status_code == 409
        identity.update(organization_id=str(other.org), user_id=str(other.actor), auth_user_id=str(other.auth))
        assert (await client.post(endpoint, json=stale)).status_code == 403
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
