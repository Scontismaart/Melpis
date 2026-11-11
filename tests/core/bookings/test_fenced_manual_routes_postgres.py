"""Real PostgreSQL coverage for the legacy HTTP to atomic C2.4 bridge."""
import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.bookings.booking_mutation_contract import BookingMutation, NewBookingLineage
from src.core.bookings.routes import router
from src.core.db.repositories.booking_repo import BookingRepository, ManagedBookingMutationRequired
from src.core.db.repositories.message_repo import MessageRepository
from src.core.workers.retry_worker import MultiChannelRetryWorker
from tests.core.bookings.test_allocation_engine_postgres import create_environment
from tests.core.bookings.test_booking_stays_postgres import hotel_setup

pytestmark = pytest.mark.asyncio


def envelope(request, mutation, lineage=None):
    return {'c24': {'allocation': request.model_dump(mode='json'), 'mutation': asdict(mutation),
                    'new_booking': ({k: str(v) if v is not None else None
                                     for k, v in asdict(lineage).items()} if lineage else None)}}


def headers(request):
    return {'Idempotency-Key': str(request.request_id),
            'X-Booking-Allocation-Revision': str(request.expected_allocation_revision),
            'X-Booking-Catalog-Revision': str(request.expected_catalog_revision)}


def app_for(env):
    identity = {'organization_id': str(env.org), 'auth_user_id': str(env.auth),
                'user_id': str(env.actor), 'ruolo': 'owner', 'source': 'jwt', 'aal': 'aal2'}
    async def verified():
        return dict(identity)
    app = FastAPI()
    app.include_router(router)
    app.state.booking_service = SimpleNamespace(repo=BookingRepository(env.pool))
    app.dependency_overrides[get_current_user] = verified
    app.dependency_overrides[get_organization_context] = verified
    return app, identity


@pytest.mark.parametrize('sector', ['parrucchiere', 'centro_estetico', 'studio_medico_dentista'])
async def test_fenced_http_create_update_confirm_and_replay(pg_pool, monkeypatch, sector):
    env = await create_environment(pg_pool, sector)
    await pg_pool.execute('SELECT public.booking_sector_activate_empty($1,$2)', env.org, sector)
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    app, identity = app_for(env)
    booking = uuid4()
    create = env.request(booking)
    mutation = BookingMutation('create', 'Synthetic', '3900000001', '', '2026-10-28', '10:00', 1)
    body = envelope(create, mutation, NewBookingLineage(None, None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        if sector == 'studio_medico_dentista':
            restricted = envelope(create, BookingMutation('create', 'Synthetic', '3900000001',
                'synthetic clinical note', '2026-10-28', '10:00', 1), NewBookingLineage(None, None))
            rejected = await client.post('/api/bookings', json=restricted, headers=headers(create))
            assert rejected.status_code == 422 and 'synthetic clinical note' not in rejected.text
        assert (await client.post('/api/bookings', json=body, headers=headers(create))).status_code == 200
        replay = await client.post('/api/bookings', json=body, headers=headers(create))
        assert replay.status_code == 200 and replay.json()['c24']['replayed'] is True
        changed = env.request(booking, action='replace', allocation_revision=1, appointment_time='11:00')
        change_body = envelope(changed, BookingMutation('reschedule', '', '', '', '2026-10-28', '11:00', 1))
        response = await client.put(f'/api/bookings/{booking}', json=change_body, headers=headers(changed))
        assert response.status_code == 200, response.text
        context = await client.get(f'/api/bookings/{booking}/c24-context')
        assert context.status_code == 200
        assert context.json()['allocation']['expected_allocation_revision'] == 2
        assert context.json()['allocation']['appointment_time'] == '11:00'
        confirm = env.request(booking, action='replace', allocation_revision=2, appointment_time='11:00')
        confirm_body = envelope(confirm, BookingMutation('confirm', '', '', '', notice=True))
        response = await client.post(f'/api/bookings/{booking}/confirm', json=confirm_body, headers=headers(confirm))
        assert response.status_code == 200, response.text
        assert (await client.post(f'/api/bookings/{booking}/confirm', json=confirm_body,
                                  headers=headers(confirm))).json()['c24']['replayed'] is True
        identity['organization_id'] = str(uuid4())
        foreign = await client.post(f'/api/bookings/{booking}/confirm', json=confirm_body, headers=headers(confirm))
        assert foreign.status_code in {403, 503}
        assert (await client.get(f'/api/bookings/{booking}/c24-context')).status_code in {404, 503}
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 3
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
    if sector == 'studio_medico_dentista':
        assert await pg_pool.fetchval('SELECT note FROM bookings WHERE organization_id=$1 AND id=$2', env.org, booking) == ''


@pytest.mark.parametrize('action', ['cancel', 'reject', 'mark-no-show'])
async def test_fenced_status_bridge_and_replay(pg_pool, monkeypatch, action):
    env = await create_environment(pg_pool, 'parrucchiere')
    await pg_pool.execute("SELECT public.booking_sector_activate_empty($1,'parrucchiere')", env.org)
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    app, _ = app_for(env)
    booking = uuid4()
    create = env.request(booking)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/api/bookings', json=envelope(create, BookingMutation(
            'create', 'Synthetic', '', '', '2026-10-28', '10:00', 1), NewBookingLineage(None, None)),
            headers=headers(create))).status_code == 200
        release = env.request(booking, action='cancel', allocation_revision=1)
        path = f'/api/bookings/{booking}/{action}'
        assert (await client.post(path, headers=headers(release))).status_code == 200
        assert (await client.post(path, headers=headers(release))).status_code == 200
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 0
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_allocation_requests WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 2


async def test_fenced_legacy_contact_writer_rejected(pg_pool):
    env = await create_environment(pg_pool, 'parrucchiere')
    await pg_pool.execute("SELECT public.booking_sector_activate_empty($1,'parrucchiere')", env.org)
    repo = BookingRepository(pg_pool)
    with pytest.raises(ManagedBookingMutationRequired):
        await repo.update_booking_details(env.org, uuid4(), 'Synthetic', '', '2026-10-28', '10:00', 1, '', 'in_attesa')


async def test_legacy_contact_writer_serializes_activation(pg_pool):
    env = await create_environment(pg_pool, 'parrucchiere')
    repo = BookingRepository(pg_pool)
    async with repo._legacy_mutation_connection(env.org, uuid4()) as conn:
        pending = asyncio.create_task(pg_pool.execute("SELECT public.booking_sector_activate_empty($1,'parrucchiere')", env.org))
        await asyncio.sleep(0.05)
        assert not pending.done()
    await asyncio.wait_for(pending, 5)
    with pytest.raises(ManagedBookingMutationRequired):
        async with repo._legacy_mutation_connection(env.org, uuid4()):
            pass


async def test_concurrent_fenced_creates_do_not_duplicate_resource(pg_pool, monkeypatch):
    env = await create_environment(pg_pool, 'parrucchiere')
    await pg_pool.execute("SELECT public.booking_sector_activate_empty($1,'parrucchiere')", env.org)
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    app, _ = app_for(env)
    requests = [env.request(uuid4()), env.request(uuid4())]
    mutation = BookingMutation('create', 'Synthetic', '', '', '2026-10-28', '10:00', 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        responses = await asyncio.gather(*(client.post('/api/bookings',
            json=envelope(req, mutation, NewBookingLineage(None, None)), headers=headers(req)) for req in requests))
    assert sum(response.status_code == 200 for response in responses) == 1
    assert all(response.status_code in {200, 422, 503} for response in responses)
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', env.org) == 1
    assert await pg_pool.fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1', env.org) == 1


async def test_non_opted_in_manual_route_keeps_legacy_contract(pg_pool, monkeypatch):
    from src.core.bookings.service import BookingService
    env = await create_environment(pg_pool, 'parrucchiere')
    app, identity = app_for(env)
    app.state.booking_service = BookingService(repo=BookingRepository(pg_pool))
    identity['ruolo'], identity['aal'] = 'manager', 'aal1'
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/bookings', json={'nome_cliente': 'Synthetic',
            'telefono': '', 'note': '', 'data': '2026-10-28', 'ora': '10:00', 'coperti': 1})
    assert response.status_code == 200, response.text
    assert await pg_pool.fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', env.org) == 1


async def test_hotel_http_confirmation_outbox_replay_cancel_and_guard(pg_pool, monkeypatch):
    env, lineage = await hotel_setup(pg_pool)
    await pg_pool.execute("SELECT public.booking_sector_activate_empty($1,'hotel_bnb')", env.org)
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    app, _ = app_for(env)
    booking = uuid4()
    create = env.request(booking)
    mutation = BookingMutation('create_stay', 'Synthetic', '3900000001', '', '2026-10-28', '14:00', 1,
                               arrival_date='2026-10-28', departure_date='2026-10-29')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/bookings', json=envelope(create, mutation, lineage), headers=headers(create))
        assert response.status_code == 200, response.text
        confirm = env.request(booking, action='replace', allocation_revision=1)
        body = envelope(confirm, BookingMutation('confirm', '', '', '', notice=True))
        response = await client.post(f'/api/bookings/{booking}/confirm', json=body, headers=headers(confirm))
        assert response.status_code == 200, response.text
        assert (await client.post(f'/api/bookings/{booking}/confirm', json=body,
                                  headers=headers(confirm))).json()['c24']['replayed']
        outbox = await pg_pool.fetchrow('''SELECT o.outbound_message_id,m.content_text,m.status
            FROM booking_confirmation_outbox o JOIN messages m ON m.organization_id=o.organization_id
            AND m.id=o.outbound_message_id WHERE o.organization_id=$1 AND o.booking_id=$2''', env.org, booking)
        assert outbox['status'] == 'queued'
        assert 'arrivo 2026-10-28' in outbox['content_text'] and 'partenza 2026-10-29' in outbox['content_text']
        assert await pg_pool.fetchval('SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id=$2', env.org, booking) == 1
        messages = MessageRepository(pg_pool)
        assert await messages.enqueue_ready_booking_confirmations() == 1
        attempt = await pg_pool.fetchrow('SELECT * FROM message_delivery_attempts WHERE message_id=$1', outbox['outbound_message_id'])
        import src.whatsapp.config as wa_config
        import src.whatsapp.client as wa_client
        monkeypatch.setattr(wa_config, 'load_tenant_config', AsyncMock(return_value=SimpleNamespace(phone_number_id='test', access_token='test')))
        monkeypatch.setattr(wa_client, 'MetaClient', lambda _: SimpleNamespace(close=AsyncMock()))
        async def timeout_after_claim(**kwargs):
            await pg_pool.execute("UPDATE messages SET status='sending_ambiguous' WHERE organization_id=$1 AND id=$2",
                                  env.org, kwargs['message_id'])
            raise TimeoutError('synthetic provider timeout')
        sender = SimpleNamespace(attempt_delivery=AsyncMock(side_effect=timeout_after_claim))
        worker = MultiChannelRetryWorker(SimpleNamespace(max_retry_attempts=3), messages, service=sender)
        await worker._process_one(dict(attempt))
        assert await pg_pool.fetchval('SELECT status FROM messages WHERE organization_id=$1 AND id=$2', env.org, outbox['outbound_message_id']) == 'sending_ambiguous'
        await worker._process_one(dict(attempt))
        sender.attempt_delivery.assert_awaited_once()
        cancel = env.request(booking, action='cancel', allocation_revision=2)
        assert (await client.post(f'/api/bookings/{booking}/cancel', headers=headers(cancel))).status_code == 200
    async with messages.booking_confirmation_delivery_guard(env.org, outbox['outbound_message_id']) as allowed:
        assert allowed is False
    await worker._process_one(dict(attempt))
    sender.attempt_delivery.assert_awaited_once()
    assert await pg_pool.fetchval('SELECT status FROM messages WHERE organization_id=$1 AND id=$2', env.org, outbox['outbound_message_id']) == 'sending_ambiguous'
