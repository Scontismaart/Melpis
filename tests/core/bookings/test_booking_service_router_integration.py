"""Test integrazione BookingService con BookingAdapterRouter (Task 4).

Verifica:
- Creazione con Send-Then-Mark
- Modalità authoritative: successo vs fallimento (richiede_intervento=True per escalation umana)
- Data minimization fail-closed attraverso il service
- Cancellazione tramite router
"""
from __future__ import annotations

import uuid
from datetime import date, time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.bookings.ports.base import BookingResult, CreateBookingRequest
from src.core.bookings.router import BookingAdapterRouter, BookingMode
from src.core.bookings.service import BookingService

ORG_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def mock_booking_repo():
    repo = MagicMock()
    repo.get_booking_for_message = AsyncMock(return_value=None)
    repo.mark_booking_requires_intervention = AsyncMock()
    repo.list_bookings = AsyncMock(return_value=[])
    repo.get_booking_settings = AsyncMock(
        return_value={"capienze_orarie": {"10:00": 10, "11:00": 10}}
    )
    repo.create_booking = AsyncMock(
        side_effect=lambda **kwargs: {
            "id": uuid.uuid4(),
            "organization_id": kwargs["organization_id"],
            "nome_cliente": kwargs["nome_cliente"],
            "telefono": kwargs["telefono"],
            "data": kwargs["data"],
            "ora": kwargs["ora"],
            "coperti": kwargs["coperti"],
            "note": kwargs["note"],
            "stato": kwargs["stato"],
            "richiede_intervento": kwargs["richiede_intervento"],
        }
    )
    repo.cancel_legacy_booking = AsyncMock(
        return_value={
            "id": uuid.uuid4(),
            "organization_id": ORG_ID,
            "stato": "cancellata",
            "external_booking_id": "ext-booking-123",
        }
    )
    return repo


@pytest.fixture
def mock_router():
    router = MagicMock(spec=BookingAdapterRouter)
    router.dispatch_create_booking = AsyncMock()
    router.dispatch_cancel_booking = AsyncMock()
    return router


@pytest.mark.asyncio
async def test_create_booking_dispatches_to_router_on_success(mock_booking_repo, mock_router):
    mock_router.dispatch_create_booking.return_value = BookingResult(
        success=True,
        external_booking_id="ext-42",
        stato="confermata",
        sync_status="synced",
    )

    service = BookingService(repo=mock_booking_repo, booking_router=mock_router)

    res = await service.create_booking(
        org_id=ORG_ID,
        nome_cliente="Laura Bianchi",
        telefono="393331122334",
        data="2026-09-12",
        ora="10:00",
        coperti=1,
        note="Taglio punte e piega",
        verticale="parrucchiere",
    )

    assert res["external_sync_status"] == "synced"
    assert res["external_booking_id"] == "ext-42"
    assert res["richiede_intervento"] is False
    mock_router.dispatch_create_booking.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_booking_escalates_to_human_on_external_failure(
    mock_booking_repo, mock_router
):
    """Invariante 11: Se il gestionale esterno rifiuta, richiede_intervento diventa True."""
    mock_router.dispatch_create_booking.return_value = BookingResult(
        success=False,
        stato="errore",
        error_code="slot_full",
        error_message="Nessuna disponibilità sul gestionale esterno",
        sync_status="failed",
    )

    service = BookingService(repo=mock_booking_repo, booking_router=mock_router)

    res = await service.create_booking(
        org_id=ORG_ID,
        nome_cliente="Paolo Verdi",
        telefono="393339988776",
        data="2026-09-12",
        ora="10:00",
        coperti=1,
        note="Visita specialistica",
        verticale="studio_medico",
    )

    assert res["external_sync_status"] == "failed"
    assert res["richiede_intervento"] is True


@pytest.mark.asyncio
async def test_create_booking_data_minimization_integration(mock_booking_repo):
    """Verifica che il router applicato dal service minimizzi le note sanitarie a monte."""
    real_router = BookingAdapterRouter(repo=None)
    mock_adapter = MagicMock()
    mock_adapter.create_booking = AsyncMock(
        return_value=BookingResult(
            success=True,
            external_booking_id="ext-med-1",
            stato="confermata",
            sync_status="synced",
        )
    )
    real_router.resolve_adapter = AsyncMock(
        return_value=(mock_adapter, BookingMode.AUTHORITATIVE, {"medical_dpa_signed": False})
    )

    service = BookingService(repo=mock_booking_repo, booking_router=real_router)

    await service.create_booking(
        org_id=ORG_ID,
        nome_cliente="Paziente Test",
        telefono="393339988776",
        data="2026-09-12",
        ora="10:00",
        coperti=1,
        note="Forte aritmia cardiaca, allergico a penicillina",
        verticale="studio_medico",
    )

    # Verifica la richiesta arrivata all'adapter esterno: le note DEVONO essere vuote
    call_args = mock_adapter.create_booking.await_args
    req_sent: CreateBookingRequest = call_args[0][1]
    assert req_sent.note == ""
    assert "aritmia" not in req_sent.note


@pytest.mark.asyncio
async def test_cancel_booking_dispatches_to_router(mock_booking_repo, mock_router):
    mock_router.dispatch_cancel_booking.return_value = BookingResult(
        success=True,
        stato="cancellata",
    )

    service = BookingService(repo=mock_booking_repo, booking_router=mock_router)
    b_id = uuid.uuid4()
    await service.cancel(ORG_ID, b_id)

    mock_booking_repo.cancel_legacy_booking.assert_awaited_once_with(ORG_ID, b_id)
    mock_router.dispatch_cancel_booking.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_guard_failure_does_not_dispatch(mock_booking_repo, mock_router):
    from src.core.db.repositories.booking_repo import ManagedBookingMutationRequired

    mock_booking_repo.cancel_legacy_booking.side_effect = ManagedBookingMutationRequired()
    service = BookingService(repo=mock_booking_repo, booking_router=mock_router)
    with pytest.raises(ManagedBookingMutationRequired):
        await service.cancel(ORG_ID, uuid.uuid4())
    mock_router.dispatch_cancel_booking.assert_not_awaited()
