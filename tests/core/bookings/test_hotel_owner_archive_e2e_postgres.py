"""Synthetic owner HTTP hotel stay through checkout retention archive.

The archive is an owner-only database operation, not a public HTTP endpoint.
The historical completion timestamp is simulated solely to test retention.
"""
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.bookings.booking_mutation_contract import BookingMutation
from src.core.bookings.owner_mutation_api import router
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.bookings.test_booking_stay_lifecycle_postgres import _approve_retention
from tests.core.bookings.test_booking_stays_postgres import hotel_setup


@pytest.mark.asyncio
async def test_owner_hotel_stay_reschedule_checkout_and_archive(pg_pool, monkeypatch):
    env, lineage = await hotel_setup(pg_pool)
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

    def body(request, mutation, source=None):
        return {'allocation': request.model_dump(mode='json'), 'mutation': asdict(mutation),
                'new_booking': ({k: str(v) if v is not None else None
                                 for k, v in asdict(source).items()} if source else None)}

    create = body(env.request(booking, arrival_date='2024-01-10', departure_date='2024-01-11'),
                  BookingMutation('create_stay', 'Synthetic', '', '', '2024-01-10', '14:00', 1,
                                  arrival_date='2024-01-10', departure_date='2024-01-11'), lineage)
    move = body(env.request(booking, action='replace', allocation_revision=1,
                            arrival_date='2024-01-11', departure_date='2024-01-13'),
                BookingMutation('reschedule_stay', '', '', '', '2024-01-11', '14:00', 1,
                                arrival_date='2024-01-11', departure_date='2024-01-13',
                                expected_stay_revision=1))
    complete = body(env.request(booking, action='replace', allocation_revision=2,
                                arrival_date='2024-01-11', departure_date='2024-01-13'),
                    BookingMutation('complete', '', '', ''))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        for command, expected in ((create, 'allocated'), (move, 'replaced'), (complete, 'replaced')):
            response = await client.post('/api/bookings/typed-mutations', json=command)
            assert response.status_code == 200, response.text
            assert response.json()['outcome'] == expected
            replay = await client.post('/api/bookings/typed-mutations', json=command)
            assert replay.status_code == 200, replay.text
            assert replay.json()['replayed'] is True

    stay = await pg_pool.fetchrow('SELECT * FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
    assert stay['original_subject_id'] == lineage.contact_id
    assert (str(stay['arrival_date']), str(stay['departure_date']), stay['revision']) == ('2024-01-11', '2024-01-13', 2)
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 3
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stay_completion_evidence WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) > 0

    # Simulate elapsed retention time; production evidence is immutable.
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE booking_stay_completion_evidence DISABLE TRIGGER completion_evidence_immutable')
        await conn.execute("UPDATE booking_stay_completion_evidence SET completed_at='2024-01-14 12:00Z' WHERE organization_id=$1 AND booking_id=$2", env.org, booking)
        await conn.execute('ALTER TABLE booking_stay_completion_evidence ENABLE TRIGGER completion_evidence_immutable')
    await _approve_retention(pg_pool, env.org)
    assert await pg_pool.fetchval('SELECT public.archive_completed_hotel_allocation($1,$2)', env.org, booking) is True
    assert await pg_pool.fetchval('SELECT public.archive_completed_hotel_allocation($1,$2)', env.org, booking) is False
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    assert await pg_pool.fetchval('SELECT state FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 'cancelled'
    assert await pg_pool.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 'completata'
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 3
