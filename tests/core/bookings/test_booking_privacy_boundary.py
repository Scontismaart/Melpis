"""Privacy and idempotency at the external booking boundary."""
from datetime import date, time
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.core.bookings.ports.base import BookingResult, CreateBookingRequest, CustomerResult
from src.core.bookings.router import BookingAdapterRouter, DataMinimizationPolicy

ORG = UUID('11111111-1111-1111-1111-111111111111')
OTHER_ORG = UUID('22222222-2222-2222-2222-222222222222')
NOTE = 'Please use the side entrance.'


@pytest.fixture
def booking_request():
    return CreateBookingRequest(
        idempotency_key='booking-key',
        customer=CustomerResult(customer_id='customer', nome='Alex', cognome='Example', telefono='390000000000'),
        data=date(2026, 11, 1), ora_inizio=time(10), durata_minuti=30,
        service_id='service', coperti=1, note=NOTE,
    )


@pytest.mark.parametrize('dpa', [False, None, 'false', 'true', '', 0, 1, 1.0, [], {}, ['true'], True])
def test_sensitive_dpa_requires_literal_true(booking_request, dpa):
    result = DataMinimizationPolicy().apply(booking_request, 'studio_medico_dentista', dpa)
    assert result.note == (NOTE if dpa is True else '')
    assert booking_request.note == NOTE


@pytest.mark.parametrize('vertical', [
    'studio_medico', 'studio_medico_dentista', 'medico', 'clinica', 'sanitario',
    'odontoiatra', 'dentista', 'fisioterapia', '  STUDIO_MEDICO_DENTISTA  ',
    None, '', '   ', 'new_vertical', 'ristorante-extra', 12, [], {},
])
def test_sensitive_and_unrecognized_verticals_fail_closed(booking_request, vertical):
    result = DataMinimizationPolicy().apply(booking_request, vertical, False)
    assert result.note == ''
    assert booking_request.note == NOTE


@pytest.mark.parametrize('vertical', [
    'ristorante', 'parrucchiere', 'centro_estetico', 'hotel_bnb', 'hotel',
    '  RISTORANTE  ', ' Hotel_BnB ',
])
def test_known_nonmedical_verticals_preserve_notes(booking_request, vertical):
    assert DataMinimizationPolicy().apply(booking_request, vertical, False).note == NOTE
    assert booking_request.note == NOTE


@pytest.fixture
def boundary(monkeypatch):
    repo = MagicMock()
    repo.get_credentials = AsyncMock()
    repo.claim_sync_slot = AsyncMock(return_value=({'sync_status': 'pending'}, True))
    repo.record_sync_success = AsyncMock()
    repo.record_sync_failure = AsyncMock()
    adapter = MagicMock()
    adapter.provider_name = 'fake'
    adapter.create_booking = AsyncMock(return_value=BookingResult(
        success=True, external_booking_id='external-id', stato='confermata', sync_status='synced'))
    monkeypatch.setattr('src.core.bookings.router.FakeBookingAdapter', lambda: adapter)
    return repo, adapter, BookingAdapterRouter(repo=repo)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['shadow', 'authoritative'])
@pytest.mark.parametrize('vertical', ['studio_medico_dentista', 'studio_medico'])
@pytest.mark.parametrize('dpa,expected', [(True, NOTE), (False, ''), ('true', ''), ('false', ''), (1, ''), (0, ''), (None, ''), ({}, ''), ([], '')])
async def test_resolved_config_controls_actual_adapter_payload(boundary, booking_request, mode, vertical, dpa, expected):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': mode, 'medical_dpa_signed': dpa}}
    await router.dispatch_create_booking(ORG, booking_request, verticale=vertical, medical_dpa_signed=True)
    assert adapter.create_booking.await_args.args[1].note == expected
    assert booking_request.note == NOTE
    repo.get_credentials.assert_awaited_once_with(ORG)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['shadow', 'authoritative'])
@pytest.mark.parametrize('dpa', [False, 'false'])
async def test_resolved_nonmedical_dispatch_preserves_note(boundary, booking_request, mode, dpa):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': mode, 'medical_dpa_signed': dpa}}
    await router.dispatch_create_booking(ORG, booking_request, verticale='ristorante')
    assert adapter.create_booking.await_args.args[1].note == NOTE
    assert booking_request.note == NOTE
    repo.get_credentials.assert_awaited_once_with(ORG)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['shadow', 'authoritative'])
async def test_absent_config_dpa_cannot_inherit_caller_grant(boundary, booking_request, mode):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': mode}}
    await router.dispatch_create_booking(ORG, booking_request, verticale='studio_medico', medical_dpa_signed=True)
    assert adapter.create_booking.await_args.args[1].note == ''
    assert booking_request.note == NOTE


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['synced', 'pending'])
async def test_replay_does_not_send_again(boundary, booking_request, state):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': 'authoritative'}}
    repo.claim_sync_slot.return_value = ({'sync_status': state, 'external_booking_id': 'existing'}, False)
    result = await router.dispatch_create_booking(ORG, booking_request, verticale='studio_medico')
    assert result.sync_status == ('synced' if state == 'synced' else 'pending_retry')
    adapter.create_booking.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['shadow', 'authoritative'])
async def test_failed_attempt_and_retry_are_minimized(boundary, booking_request, mode):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': mode}}
    adapter.create_booking.side_effect = [RuntimeError('temporary failure'), BookingResult(
        success=True, external_booking_id='retried', stato='confermata', sync_status='synced')]
    await router.dispatch_create_booking(ORG, booking_request, verticale='studio_medico')
    await router.dispatch_create_booking(ORG, booking_request, verticale='studio_medico')
    assert [call.args[1].note for call in adapter.create_booking.await_args_list] == ['', '']
    repo.record_sync_failure.assert_awaited_once()
    repo.record_sync_success.assert_awaited_once()
    assert booking_request.note == NOTE


@pytest.mark.asyncio
async def test_same_key_is_scoped_to_each_organization(boundary, booking_request):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': 'authoritative'}}
    await router.dispatch_create_booking(ORG, booking_request, verticale='studio_medico')
    await router.dispatch_create_booking(OTHER_ORG, booking_request, verticale='studio_medico')
    assert [c.args[0] for c in repo.claim_sync_slot.await_args_list] == [ORG, OTHER_ORG]
    assert [c.args[0] for c in adapter.create_booking.await_args_list] == [ORG, OTHER_ORG]
    assert all(c.args[1].note == '' for c in adapter.create_booking.await_args_list)


@pytest.mark.asyncio
async def test_logs_do_not_include_credentials_config_or_note(boundary, booking_request, caplog):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'api_key': 'credential-marker',
        'config': {'mode': 'authoritative', 'private_setting': 'configuration-marker'}}
    with caplog.at_level('INFO'):
        await router.dispatch_create_booking(ORG, booking_request, verticale='studio_medico')
    assert 'credential-marker' not in caplog.text
    assert 'configuration-marker' not in caplog.text
    assert NOTE not in caplog.text
