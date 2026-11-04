"""Synthetic sector checks for the private, owner-authorized C2.4 path.

These checks do not claim a runtime worker or conversation-to-writer bridge.
Observation uses an existing synthetic booking; a separate typed command creates
(or, for hotel, creates a source-proven stay). No provider is called.
"""
from uuid import uuid4

import pytest

from src.core.bookings.booking_mutation_contract import (
    BookingMutation, NewBookingLineage, apply_booking_mutation,
)
from src.core.bookings.conversation_state import new_state
from tests.core.bookings.test_allocation_engine_postgres import create_environment
from tests.core.bookings.test_booking_stays_postgres import hotel_setup
from tests.core.bookings.test_conversation_state import catalog, apply, proposal

pytestmark = pytest.mark.asyncio

SECTORS = ('ristorante', 'parrucchiere', 'centro_estetico', 'hotel_bnb', 'studio_medico_dentista')


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
        command = BookingMutation('create_stay', 'Synthetic', '', '', '2026-10-28', '14:00', 1,
                                  arrival_date='2026-10-28', departure_date='2026-10-29')
    else:
        env = await create_environment(pg_pool, sector)
        lineage = NewBookingLineage(None, None)
        command = BookingMutation('create', 'Synthetic', '3900000001', '', '2026-10-28', '10:00', 1)

    probe = await env.booking()
    observation = await env.observe(env.request(probe))
    assert observation['outcome'] == 'available'
    assert not observation['runtime_enabled'] and not observation['confirmation_authorized']
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1', env.org) == 0

    booking = uuid4()
    request = env.request(booking)
    first = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command, new_booking=lineage)
    replay = await apply_booking_mutation(pg_pool, env.org, env.auth, request, mutation=command, new_booking=lineage)
    assert first['outcome'] == 'allocated' and replay['replayed'] is True
    for table in ('booking_allocation_sets', 'booking_resource_allocations', 'booking_allocation_requests'):
        assert await pg_pool.fetchval(f'SELECT count(*) FROM {table} WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 1
    # Creation remains unconfirmed, hence no notification outbox is appropriate.
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
