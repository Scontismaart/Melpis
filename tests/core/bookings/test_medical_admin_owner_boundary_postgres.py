"""Medical administrative owner API never persists rejected note text."""
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.bookings.booking_mutation_contract import BookingMutation, NewBookingLineage
from src.core.bookings.owner_mutation_api import router
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.bookings.test_allocation_engine_postgres import create_environment


@pytest.mark.asyncio
async def test_owner_medical_administration_rejects_note_and_persists_empty_note(pg_pool, monkeypatch):
    env = await create_environment(pg_pool, 'studio_medico_dentista')
    identity = {'organization_id': str(env.org), 'auth_user_id': str(env.auth),
                'user_id': str(env.actor), 'ruolo': 'owner', 'source': 'jwt', 'aal': 'aal2'}
    async def verified():
        return dict(identity)
    app = FastAPI()
    app.include_router(router, prefix='/api/bookings')
    app.state.booking_service = SimpleNamespace(repo=BookingRepository(pg_pool))
    app.dependency_overrides[get_current_user] = verified
    app.dependency_overrides[get_organization_context] = verified
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    booking = uuid4()
    mutation = BookingMutation('create', 'Synthetic', '3900000001', '', '2026-10-28', '10:00', 1)
    lineage = NewBookingLineage(None, None)
    body = {'allocation': env.request(booking).model_dump(mode='json'), 'mutation': asdict(mutation),
            'new_booking': asdict(lineage)}
    restricted = 'synthetic restricted clinical note'
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        rejected = await client.post('/api/bookings/typed-mutations', json={**body,
            'mutation': {**body['mutation'], 'note': restricted}})
        assert rejected.status_code == 422
        assert restricted not in rejected.text
        assert not await pg_pool.fetchval('SELECT EXISTS(SELECT 1 FROM bookings WHERE organization_id=$1 AND id=$2)', env.org, booking)
        accepted = await client.post('/api/bookings/typed-mutations', json=body)
        assert accepted.status_code == 200 and accepted.json()['outcome'] == 'allocated'
    assert not await pg_pool.fetchval('SELECT note FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
