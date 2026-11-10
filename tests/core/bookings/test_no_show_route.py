from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.bookings import routes
from src.core.db.repositories.booking_repo import BookingRepository, ManagedBookingMutationRequired

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
    repo.get_booking = AsyncMock(return_value={'id': str(uuid4()), 'stato': 'no_show'})
    service = SimpleNamespace(repo=repo, mark_no_show=AsyncMock(), cancel=AsyncMock(), reject=AsyncMock(),
                              confirm=AsyncMock(), mark_completed=AsyncMock(), update_booking=AsyncMock())
    app.state.booking_service = service
    app.dependency_overrides[get_current_user] = verified
    app.dependency_overrides[get_organization_context] = verified
    apply = AsyncMock(return_value={'replayed': False})
    audit = AsyncMock()
    monkeypatch.setattr(routes, 'apply_booking_mutation', apply)
    monkeypatch.setattr(routes, '_audit_booking', audit)
    monkeypatch.setenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', 'true')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client, identity, service, apply, audit


async def test_managed_no_show_requires_owner_mfa_and_complete_headers(env):
    client, identity, service, apply, audit = env
    path = f'/api/bookings/{uuid4()}/mark-no-show'
    headers = {'Idempotency-Key': str(uuid4()), 'X-Booking-Allocation-Revision': '1',
               'X-Booking-Catalog-Revision': '2'}
    identity['ruolo'] = 'manager'
    assert (await client.post(path, headers=headers)).status_code == 403
    identity['ruolo'] = 'owner'
    identity['aal'] = 'aal1'
    assert (await client.post(path, headers=headers)).status_code == 403
    identity['aal'] = 'aal2'
    assert (await client.post(path, headers={'X-Booking-Allocation-Revision': '1',
                                             'X-Booking-Catalog-Revision': '2'})).status_code == 422
    assert (await client.post(path, headers={**headers, 'X-Booking-Catalog-Revision': '0'})).status_code == 422
    apply.assert_not_awaited()
    service.mark_no_show.assert_not_awaited()
    audit.assert_not_awaited()


async def test_managed_no_show_dispatch_and_replay(env):
    client, identity, service, apply, audit = env
    booking_id, key = uuid4(), uuid4()
    headers = {'Idempotency-Key': str(key), 'X-Booking-Allocation-Revision': '1',
               'X-Booking-Catalog-Revision': '2'}
    path = f'/api/bookings/{booking_id}/mark-no-show'
    assert (await client.post(path, headers=headers)).status_code == 200
    args, kwargs = apply.await_args
    assert args[1:3] == (identity['organization_id'], identity['auth_user_id'])
    assert args[3].request_id == key and args[3].booking_id == booking_id
    assert args[3].expected_allocation_revision == 1 and args[3].expected_catalog_revision == 2
    assert kwargs['mutation'].operation == 'no_show'
    apply.return_value = {'replayed': True}
    assert (await client.post(path, headers=headers)).status_code == 200
    assert audit.await_count == 1
    service.mark_no_show.assert_not_awaited()


async def test_legacy_managed_booking_fails_closed(env):
    client, _, service, apply, audit = env
    service.mark_no_show.side_effect = ManagedBookingMutationRequired()
    assert (await client.post(f'/api/bookings/{uuid4()}/mark-no-show')).status_code == 409
    apply.assert_not_awaited()
    audit.assert_not_awaited()


async def test_cancel_typed_gate_dispatch_and_replay(env):
    client, identity, service, apply, audit = env
    booking_id, key = uuid4(), uuid4()
    path = f'/api/bookings/{booking_id}/cancel'
    headers = {'Idempotency-Key': str(key), 'X-Booking-Allocation-Revision': '1',
               'X-Booking-Catalog-Revision': '2'}
    identity['ruolo'] = 'manager'
    assert (await client.post(path, headers=headers)).status_code == 403
    identity['ruolo'] = 'owner'
    identity['aal'] = 'aal1'
    assert (await client.post(path, headers=headers)).status_code == 403
    identity['aal'] = 'aal2'
    assert (await client.post(path, headers=headers)).status_code == 200
    args, kwargs = apply.await_args
    assert args[1:3] == (identity['organization_id'], identity['auth_user_id'])
    assert args[3].booking_id == booking_id and args[3].request_id == key
    assert kwargs['mutation'].operation == 'cancel'
    apply.return_value = {'replayed': True}
    assert (await client.post(path, headers=headers)).status_code == 200
    assert audit.await_count == 1
    service.cancel.assert_not_awaited()


async def test_cancel_legacy_managed_booking_fails_closed(env):
    client, _, service, apply, audit = env
    service.cancel.side_effect = ManagedBookingMutationRequired()
    assert (await client.post(f'/api/bookings/{uuid4()}/cancel')).status_code == 409
    apply.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.parametrize('operation', ['reject', 'confirm', 'mark-completed', 'update'])
async def test_remaining_legacy_routes_reject_managed_booking(env, operation):
    client, _, service, apply, audit = env
    method = {'reject': 'reject', 'confirm': 'confirm', 'mark-completed': 'mark_completed',
              'update': 'update_booking'}[operation]
    getattr(service, method).side_effect = ManagedBookingMutationRequired()
    path = f'/api/bookings/{uuid4()}'
    response = await (client.put(path, json={}) if operation == 'update'
                      else client.post(f'{path}/{operation}'))
    assert response.status_code == 409
    apply.assert_not_awaited()
    audit.assert_not_awaited()


async def test_managed_reject_uses_typed_contract(env):
    client, identity, service, apply, audit = env
    key, booking = uuid4(), uuid4()
    headers = {'Idempotency-Key': str(key), 'X-Booking-Allocation-Revision': '1',
               'X-Booking-Catalog-Revision': '2'}
    assert (await client.post(f'/api/bookings/{booking}/reject', headers=headers)).status_code == 200
    assert apply.await_args.kwargs['mutation'].operation == 'reject'
    assert apply.await_args.args[1:3] == (identity['organization_id'], identity['auth_user_id'])
    service.reject.assert_not_awaited()
    audit.assert_awaited_once()
