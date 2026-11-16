"""Privacy and idempotency at the external booking boundary."""
import asyncio
from datetime import date, time
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.core.bookings.ports.base import BookingResult, CreateBookingRequest, CustomerResult
from src.core.bookings.router import BookingAdapterRouter, DataMinimizationPolicy

ORG = UUID('11111111-1111-1111-1111-111111111111')
OTHER_ORG = UUID('22222222-2222-2222-2222-222222222222')
NOTE = 'Please use the side entrance.'


class _ClassificationConn:
    def __init__(self, repo):
        self.repo = repo

    def transaction(self):
        class _Transaction:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *_args):
                return False

        return _Transaction()

    async def execute(self, query, *_args):
        if "pg_advisory_xact_lock_shared" in query:
            return "SELECT 1"
        raise AssertionError(f"Unexpected query: {query}")

    async def fetchval(self, query, *_args):
        if "to_regclass('public.booking_privacy_classification_reader')" in query:
            return "booking_privacy_classification_reader"
        raise AssertionError(f"Unexpected query: {query}")

    async def fetchrow(self, query, *_args):
        if "FROM public.organizations AS o" in query:
            return self.repo.booking_privacy_classification
        raise AssertionError(f"Unexpected query: {query}")


class _ClassificationPool:
    def __init__(self, repo):
        self.repo = repo

    def acquire(self):
        repo = self.repo

        class _Acquire:
            async def __aenter__(self):
                return _ClassificationConn(repo)

            async def __aexit__(self, *_args):
                return False

        return _Acquire()


class _AsyncpgRecordShape:
    """Small asyncpg.Record stand-in: keys() returns an iterator."""

    def __init__(self, **values):
        self._values = values

    def keys(self):
        return iter(self._values)

    def __getitem__(self, key):
        return self._values[key]


@pytest.fixture
def booking_request():
    return CreateBookingRequest(
        idempotency_key='booking-key',
        customer=CustomerResult(customer_id='customer', nome='Alex', cognome='Example', telefono='390000000000'),
        data=date(2026, 11, 1), ora_inizio=time(10), durata_minuti=30,
        service_id='service', coperti=1, note=NOTE,
    )


@pytest.mark.parametrize('dpa', [False, None, 'false', 'true', '', 0, 1, 1.0, [], {}, ['true'], True])
def test_sensitive_note_is_removed_regardless_of_dpa(booking_request, dpa):
    result = DataMinimizationPolicy().apply(booking_request, 'studio_medico_dentista', dpa)
    assert result.note == ''
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
    repo.booking_privacy_classification = None
    repo.pool = _ClassificationPool(repo)
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
@pytest.mark.parametrize('dpa', [True, False, 'true', 'false', 1, 0, None, {}, []])
async def test_legacy_config_cannot_allow_medical_notes(boundary, booking_request, mode, vertical, dpa):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': mode, 'medical_dpa_signed': dpa}}
    await router.dispatch_create_booking(ORG, booking_request, verticale=vertical, medical_dpa_signed=True)
    assert adapter.create_booking.await_args.args[1].note == ''
    assert booking_request.note == NOTE
    repo.get_credentials.assert_awaited_once_with(ORG)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['shadow', 'authoritative'])
@pytest.mark.parametrize('dpa', [False, 'false'])
async def test_external_dispatch_drops_note_even_with_valid_classification(boundary, booking_request, mode, dpa):
    repo, adapter, router = boundary
    repo.booking_privacy_classification = {
        'profile_verticale': 'ristorante',
        'sector': 'ristorante',
        'operational_notes_allowed': True,
        'evidence_valid': True,
    }
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': mode, 'medical_dpa_signed': dpa}}
    await router.dispatch_create_booking(ORG, booking_request, verticale='ristorante')
    assert adapter.create_booking.await_args.args[1].note == ''
    assert booking_request.note == NOTE
    repo.get_credentials.assert_awaited_once_with(ORG)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['shadow', 'authoritative'])
async def test_cancelled_external_create_is_recorded_uncertain(boundary, booking_request, mode):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': mode}}
    adapter.create_booking.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await router.dispatch_create_booking(ORG, booking_request, verticale='ristorante')

    assert adapter.create_booking.await_args.args[1].note == ''
    repo.record_sync_failure.assert_awaited_once_with(
        ORG,
        booking_request.idempotency_key,
        'external_outcome_unverified',
        sync_status='uncertain',
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['shadow', 'authoritative'])
async def test_cancelled_success_receipt_is_recorded_uncertain(boundary, booking_request, mode):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': mode}}
    repo.record_sync_success.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await router.dispatch_create_booking(ORG, booking_request, verticale='ristorante')

    adapter.create_booking.assert_awaited_once()
    repo.record_sync_failure.assert_awaited_once_with(
        ORG,
        booking_request.idempotency_key,
        'external_outcome_unverified',
        sync_status='uncertain',
    )


@pytest.mark.asyncio
async def test_unattested_nonmedical_profile_does_not_preserve_note(boundary, booking_request):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': 'authoritative'}}

    await router.dispatch_create_booking(ORG, booking_request, verticale='ristorante')

    assert adapter.create_booking.await_args.args[1].note == ''


@pytest.mark.asyncio
async def test_protected_medical_classification_overrides_restaurant_profile(boundary, booking_request):
    repo, adapter, router = boundary
    repo.booking_privacy_classification = {
        'profile_verticale': 'ristorante',
        'sector': 'medical_disabled',
        'operational_notes_allowed': False,
        'evidence_valid': True,
    }
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': 'authoritative'}}

    await router.dispatch_create_booking(ORG, booking_request, verticale='ristorante')

    assert adapter.create_booking.await_args.args[1].note == ''


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'classification',
    [
        None,
        {'profile_verticale': 'ristorante', 'sector': 'ristorante',
         'operational_notes_allowed': False, 'evidence_valid': True},
        {'profile_verticale': 'ristorante', 'sector': 'ristorante',
         'operational_notes_allowed': True, 'evidence_valid': False},
        {'profile_verticale': 'ristorante', 'sector': 'hotel',
         'operational_notes_allowed': True, 'evidence_valid': True},
    ],
)
async def test_note_attestation_requires_matching_profile_and_permission(classification):
    from src.core.bookings.privacy_boundary import attested_booking_note_verticale

    if classification is None:
        assert attested_booking_note_verticale('ristorante', classification) is None
    else:
        assert attested_booking_note_verticale(
            classification['profile_verticale'], classification
        ) is None


@pytest.mark.asyncio
async def test_database_record_with_iterator_keys_preserves_attested_note():
    from src.core.bookings.privacy_boundary import resolve_booking_note_verticale

    repo = MagicMock()
    repo.booking_privacy_classification = _AsyncpgRecordShape(
        profile_verticale='ristorante',
        sector='ristorante',
        operational_notes_allowed=True,
        evidence_valid=True,
    )
    repo.pool = _ClassificationPool(repo)

    assert await resolve_booking_note_verticale(repo, ORG) == 'ristorante'


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
    assert result.sync_status == ('synced' if state == 'synced' else 'uncertain')
    adapter.create_booking.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['shadow', 'authoritative'])
async def test_uncertain_attempt_is_minimized_and_not_retried(boundary, booking_request, mode):
    repo, adapter, router = boundary
    repo.get_credentials.return_value = {'provider': 'fake', 'config': {'mode': mode}}
    adapter.create_booking.side_effect = [RuntimeError('temporary failure'), BookingResult(
        success=True, external_booking_id='retried', stato='confermata', sync_status='synced')]
    repo.claim_sync_slot.side_effect = [({'sync_status': 'pending'}, True),
                                       ({'sync_status': 'uncertain'}, False)]
    await router.dispatch_create_booking(ORG, booking_request, verticale='studio_medico')
    await router.dispatch_create_booking(ORG, booking_request, verticale='studio_medico')
    assert [call.args[1].note for call in adapter.create_booking.await_args_list] == ['']
    repo.record_sync_failure.assert_awaited_once()
    repo.record_sync_success.assert_not_awaited()
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
