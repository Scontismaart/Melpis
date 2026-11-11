"""Synthetic sector checks for the private, owner-authorized C2.4 path.

These checks do not claim a runtime worker or conversation-to-writer bridge.
Observation uses an existing synthetic booking; a separate typed command creates
(or, for hotel, creates a source-proven stay). No provider is called.
"""
from dataclasses import asdict
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import pytest

from src.core.bookings.booking_mutation_contract import (
    BookingMutation, NewBookingLineage, apply_booking_mutation,
)
from src.core.bookings.conversation_state import new_state
from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.bookings.owner_mutation_api import router
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.bookings.test_allocation_engine_postgres import create_environment
from tests.core.bookings.test_booking_stays_postgres import hotel_setup
from tests.core.bookings.test_conversation_state import catalog, apply, proposal

pytestmark = pytest.mark.asyncio

SECTORS = ('ristorante', 'parrucchiere', 'centro_estetico', 'hotel_bnb', 'studio_medico_dentista')


def _future_booking_date():
    day = date.today() + timedelta(days=7)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


def _request_for_command(env, booking_id, command, sector):
    if sector == 'hotel_bnb':
        return env.request(booking_id, arrival_date=command.arrival_date,
                           departure_date=command.departure_date, room_count=command.coperti)
    return env.request(booking_id, appointment_date=command.data,
                       appointment_time=command.ora, party_size=command.coperti)


@pytest.mark.parametrize('sector', SECTORS)
async def test_sector_dark_catalog_observation_and_owner_mutation(pg_pool, sector):
    # State reduction is deliberately independent: no production bridge claims
    # that this proposal itself authorizes the private mutation below.
    org, cat = catalog(sector)
    state = apply(new_state(org, uuid4(), cat), cat, proposal(service_id='standard'))
    assert state.derived.resource_ids == ('primary',)
    assert not state.runtime_enabled and not state.confirmation_authorized

    if sector == 'hotel_bnb':
        env, lineage = await hotel_setup(pg_pool)
        arrival = _future_booking_date()
        departure = arrival + timedelta(days=1)
        command = BookingMutation('create_stay', 'Synthetic', '', '', arrival.isoformat(), '14:00', 1,
                                  arrival_date=arrival.isoformat(), departure_date=departure.isoformat())
    else:
        env = await create_environment(pg_pool, sector)
        lineage = NewBookingLineage(None, None)
        command = BookingMutation('create', 'Synthetic', '3900000001', '', _future_booking_date().isoformat(), '10:00', 1)

    probe = await env.booking()
    observation = await env.observe(_request_for_command(env, probe, command, sector))
    assert observation['outcome'] == 'available'
    assert not observation['runtime_enabled'] and not observation['confirmation_authorized']
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1', env.org) == 0

    booking = uuid4()
    request = _request_for_command(env, booking, command, sector)
    first = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command, new_booking=lineage)
    replay = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command, new_booking=lineage)
    assert first['outcome'] == 'allocated' and replay['replayed'] is True
    for table in ('booking_allocation_sets', 'booking_resource_allocations', 'booking_allocation_requests'):
        assert await pg_pool.fetchval(f'SELECT count(*) FROM {table} WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 1
    # Creation remains unconfirmed, hence no notification outbox is appropriate.
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0


@pytest.mark.parametrize('sector', SECTORS)
async def test_five_sector_owner_api_postgres(pg_pool, monkeypatch, sector):
    """Exercise the gated HTTP bridge and real transaction with synthetic data."""
    if sector == 'hotel_bnb':
        env, lineage = await hotel_setup(pg_pool)
        arrival = _future_booking_date()
        departure = arrival + timedelta(days=1)
        command = BookingMutation('create_stay', 'Synthetic', '', '', arrival.isoformat(), '14:00', 1,
                                  arrival_date=arrival.isoformat(), departure_date=departure.isoformat())
    else:
        env = await create_environment(pg_pool, sector)
        lineage = NewBookingLineage(None, None)
        command = BookingMutation('create', 'Synthetic', '3900000001', '', _future_booking_date().isoformat(), '10:00', 1)

    identity = {'organization_id': str(env.org), 'auth_user_id': str(env.auth),
                'user_id': str(env.actor), 'ruolo': 'owner', 'source': 'jwt', 'aal': 'aal2'}
    async def verified():
        return dict(identity)

    app = FastAPI()
    app.include_router(router, prefix='/api/bookings')
    app.state.booking_service = SimpleNamespace(repo=BookingRepository(pg_pool))
    app.dependency_overrides[get_current_user] = verified
    app.dependency_overrides[get_organization_context] = verified
    booking = uuid4()
    body = {'allocation': _request_for_command(env, booking, command, sector).model_dump(mode='json'),
            'mutation': asdict(command),
            'new_booking': {k: str(v) if v is not None else None for k, v in asdict(lineage).items()}}

    monkeypatch.delenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        disabled = await client.post('/api/bookings/typed-mutations', json=body)
        assert disabled.status_code == 404
        assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 0
        monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
        if sector == 'studio_medico_dentista':
            rejected = await client.post('/api/bookings/typed-mutations', json={
                **body, 'mutation': {**body['mutation'], 'note': 'Synthetic prohibited free text'},
            })
            assert rejected.status_code == 422
            assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2',
                                          env.org, booking) == 0
        response = await client.post('/api/bookings/typed-mutations', json=body)
        assert response.status_code == 200, response.text
        assert response.json()['outcome'] == 'allocated'
        if sector == 'ristorante':
            replay = await client.post('/api/bookings/typed-mutations', json=body)
            assert replay.status_code == 200 and replay.json()['replayed'] is True

    for table in ('booking_allocation_sets', 'booking_resource_allocations', 'booking_allocation_requests'):
        assert await pg_pool.fetchval(f'SELECT count(*) FROM {table} WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    row = await pg_pool.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking)
    assert row is not None
    if sector == 'studio_medico_dentista':
        assert not row['note']
    if sector == 'hotel_bnb':
        stay = await pg_pool.fetchrow('SELECT * FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
        assert stay['original_subject_id'] == lineage.contact_id
        assert str(stay['arrival_date']) == arrival.isoformat() and str(stay['departure_date']) == departure.isoformat()
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
