"""Fenced HTTP writers require one tenant-scoped typed operation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.bookings import routes
from src.core.db.repositories.booking_repo import BookingRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def env(monkeypatch):
    identity = {'organization_id': str(uuid4()), 'auth_user_id': str(uuid4()),
                'user_id': str(uuid4()), 'ruolo': 'owner', 'source': 'jwt', 'aal': 'aal2'}
    async def verified():
        return dict(identity)
    app = FastAPI()
    app.include_router(routes.router)
    repo = BookingRepository(object())
    repo.get_booking = AsyncMock(return_value={'id': str(uuid4()), 'stato': 'in_attesa'})
    service = SimpleNamespace(repo=repo, create_booking=AsyncMock(), update_booking=AsyncMock(),
                              confirm=AsyncMock(), mark_completed=AsyncMock(), cancel=AsyncMock(),
                              reject=AsyncMock(), mark_no_show=AsyncMock())
    app.state.booking_service = service
    app.dependency_overrides[get_current_user] = verified
    app.dependency_overrides[get_organization_context] = verified
    apply = AsyncMock(return_value={'replayed': False, 'outcome': 'allocated'})
    audit = AsyncMock()
    monkeypatch.setattr(routes, 'apply_booking_mutation', apply)
    monkeypatch.setattr(routes, '_audit_booking', audit)
    monkeypatch.setattr(routes, '_sector_fenced', AsyncMock(return_value=True))
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client, identity, service, apply, audit


def command(booking, key, *, operation='create', allocation_revision=0):
    request = {'request_id': str(key), 'booking_id': str(booking),
               'action': 'allocate' if operation == 'create' else 'replace',
               'expected_allocation_revision': allocation_revision, 'expected_catalog_revision': 2,
               'service_id': 'standard', 'appointment_date': '2026-10-28', 'appointment_time': '10:00'}
    mutation = {'operation': operation, 'nome_cliente': 'Synthetic' if operation == 'create' else '',
                'telefono': '', 'note': ''}
    if operation == 'confirm':
        mutation['notice'] = True
    lineage = {'contact_id': None, 'conversation_id': None} if operation == 'create' else None
    if operation in {'create', 'reschedule'}:
        mutation.update(data='2026-10-28', ora='10:00', coperti=1)
    return {'c24': {'allocation': request, 'mutation': mutation, 'new_booking': lineage}}


def headers(key, allocation=0):
    return {'Idempotency-Key': str(key), 'X-Booking-Allocation-Revision': str(allocation),
            'X-Booking-Catalog-Revision': '2'}


async def test_create_binds_headers_tenant_and_replay(env):
    client, identity, service, apply, audit = env
    booking, key = uuid4(), uuid4()
    payload = command(booking, key)
    response = await client.post('/api/bookings', json=payload, headers=headers(key))
    assert response.status_code == 200
    args, kwargs = apply.await_args
    assert args[1:3] == (identity['organization_id'], identity['auth_user_id'])
    assert args[3].booking_id == booking and args[3].request_id == key
    assert kwargs['mutation'].operation == 'create'
    service.create_booking.assert_not_awaited()
    apply.return_value = {'replayed': True, 'outcome': 'allocated'}
    assert (await client.post('/api/bookings', json=payload, headers=headers(key))).status_code == 200
    assert audit.await_count == 1


@pytest.mark.parametrize('method,path,operation', [
    ('PUT', '', 'reschedule'), ('POST', '/confirm', 'confirm'), ('POST', '/mark-completed', 'complete'),
])
async def test_managed_routes_bind_path_role_and_revision(env, method, path, operation):
    client, identity, service, apply, audit = env
    booking, key = uuid4(), uuid4()
    payload = command(booking, key, operation=operation, allocation_revision=1)
    url = f'/api/bookings/{booking}{path}'
    assert (await client.request(method, url, json=payload, headers=headers(key, 1))).status_code == 200
    assert apply.await_args.args[3].booking_id == booking
    assert apply.await_args.kwargs['mutation'].operation == operation
    assert (await client.request(method, url, json=payload, headers=headers(key, 2))).status_code == 422
    assert (await client.request(method, f'/api/bookings/{uuid4()}{path}', json=payload, headers=headers(key, 1))).status_code == 422
    identity['ruolo'] = 'manager'
    assert (await client.request(method, url, json=payload, headers=headers(key, 1))).status_code == 403
    identity['ruolo'], identity['aal'] = 'owner', 'aal1'
    assert (await client.request(method, url, json=payload, headers=headers(key, 1))).status_code == 403
    assert apply.await_count == 1
    assert audit.await_count == 1
    service.update_booking.assert_not_awaited()
    service.confirm.assert_not_awaited()
    service.mark_completed.assert_not_awaited()


@pytest.mark.parametrize('path', ['/reject', '/cancel', '/mark-no-show'])
async def test_status_routes_cannot_fall_back_without_required_headers(env, path):
    client, _, service, apply, audit = env
    response = await client.post(f'/api/bookings/{uuid4()}{path}')
    assert response.status_code == 422
    apply.assert_not_awaited()
    audit.assert_not_awaited()
    service.reject.assert_not_awaited()
    service.cancel.assert_not_awaited()
    service.mark_no_show.assert_not_awaited()


async def test_missing_envelope_disabled_flag_and_duplicate_json_fail_closed(env, monkeypatch):
    client, _, service, apply, _ = env
    key, booking = uuid4(), uuid4()
    assert (await client.post('/api/bookings', json={'nome_cliente': 'Synthetic'})).status_code == 422
    body = command(booking, key)
    body['organization_id'] = str(uuid4())
    assert (await client.post('/api/bookings', json=body, headers=headers(key))).status_code == 422
    duplicate = '{"c24":{},"c24":{}}'
    assert (await client.post('/api/bookings', content=duplicate, headers={**headers(key), 'Content-Type': 'application/json'})).status_code == 422
    monkeypatch.delenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED')
    assert (await client.post('/api/bookings', json=command(booking, key), headers=headers(key))).status_code == 409
    apply.assert_not_awaited()
    service.create_booking.assert_not_awaited()
