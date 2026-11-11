"""Race sul Send-Then-Mark del create esterno (Step 0: rosso -> verde).

Semantica SQL reale: pre-check + prepare separati permettevano a due dispatch
concorrenti di superare entrambi il check e chiamare entrambi l'esterna
(provato rosso: 2 chiamate per la stessa chiave). Con il claim atomico
(claim_sync_slot: un solo vincitore per (org, key)) il secondo dispatch
riceve pending_retry senza toccare l'esterna: UNA sola chiamata.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.bookings.ports.base import (
    BookingResult,
    BookingSystemPort,
    CreateBookingRequest,
    CustomerResult,
)
from src.core.bookings.router import BookingAdapterRouter, BookingMode

ORG = uuid.UUID("55555555-5555-5555-5555-555555555555")


class AtomicClaimFakeRepo:
    """Semantica del claim atomico: INSERT DO NOTHING + retry-da-failed."""

    def __init__(self):
        self._rows: dict[tuple[str, str], dict] = {}
        self._lock = asyncio.Lock()
        self.get_credentials = AsyncMock(return_value=None)

    async def claim_sync_slot(self, organization_id, idempotency_key: str,
                              provider: str, internal_booking_id=None):
        async with self._lock:
            key = (str(organization_id), idempotency_key)
            existing = self._rows.get(key)
            if existing is None:
                row = {
                    "organization_id": organization_id,
                    "idempotency_key": idempotency_key,
                    "provider": provider,
                    "internal_booking_id": internal_booking_id,
                    "sync_status": "pending",
                    "external_booking_id": None,
                }
                self._rows[key] = row
                await asyncio.sleep(0)  # finestra di race: il perdente deve vedere pending
                return dict(row), True
            return dict(existing), False

    async def record_sync_success(self, organization_id, idempotency_key,
                                  external_booking_id):
        row = self._rows[(str(organization_id), idempotency_key)]
        row["sync_status"] = "synced"
        row["external_booking_id"] = external_booking_id
        return dict(row)

    async def record_sync_failure(self, organization_id, idempotency_key,
                                  error_message, sync_status="failed"):
        row = self._rows[(str(organization_id), idempotency_key)]
        row["sync_status"] = sync_status
        row["sync_error"] = error_message
        return dict(row)

    async def get_sync_record(self, organization_id, idempotency_key):
        return self._rows.get((str(organization_id), idempotency_key))

    get_sync_by_idempotency_key = get_sync_record


def _make_req() -> CreateBookingRequest:
    return CreateBookingRequest(
        idempotency_key="ext-book:race-001",
        customer=CustomerResult(
            customer_id="c1", nome="Ada", cognome="Rossi",
            telefono="+393330000111", email="ada@example.com",
        ),
        data=date(2026, 9, 20),
        ora_inizio=time(11, 0),
        service_id="svc-1",
    )


def _make_adapter() -> MagicMock:
    adapter = MagicMock(spec=BookingSystemPort)
    adapter.provider_name = "calcom"
    adapter.create_booking = AsyncMock(
        return_value=BookingResult(
            success=True, external_booking_id="ext-race",
            stato="confermata", sync_status="synced",
        )
    )
    return adapter


class TestSyncClaimRace:
    @pytest.mark.asyncio
    async def test_concurrent_dispatches_single_external_call(self):
        """Due dispatch concorrenti, stessa chiave: UNA sola chiamata esterna."""
        repo = AtomicClaimFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        adapter = _make_adapter()

        async def _dispatch():
            return await router.dispatch_create_booking(
                org_id=ORG, req=_make_req(), adapter=adapter,
                mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
            )

        results = await asyncio.gather(_dispatch(), _dispatch())

        assert adapter.create_booking.await_count == 1, (
            f"RACE: l'adapter esterno e' stato chiamato "
            f"{adapter.create_booking.await_count} volte per la stessa chiave"
        )
        # Il perdente non tocca mai l'esterna: o trova pending (pending_retry)
        # o arriva a call completata (replay synced). Entrambi esiti legittimi.
        statuses = sorted(r.sync_status for r in results)
        assert statuses in (["synced", "uncertain"], ["synced", "synced"])
        assert all(r.success == (r.sync_status == "synced") for r in results)
