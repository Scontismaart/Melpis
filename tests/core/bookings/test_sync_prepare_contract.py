"""Contratto record_sync_prepare -> provider call -> record_sync_success/failure.

Reproduce il bug P0: il router passava (org, key, internal_booking_id, provider_name)
contro la firma reale (org, key, provider, internal_booking_id). Con il bug, un repo
che applica la semantica reale (come ExternalBookingRepository: uuid.UUID() sul 4o
argomento stringa) solleva ValueError e il create esterno non parte mai.

StrictFakeRepo sotto replica fedelmente la semantica del repository reale:
stessa firma, stessa coercizione UUID, stessa unique (org, key), stessi stati.
I test falliscono con il bug e passano con la correzione.
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

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


class StrictFakeRepo:
    """Replica la semantica reale di ExternalBookingRepository.record_sync_*.

    - Stessa firma posizionale di record_sync_prepare / claim_sync_slot.
    - Coercizione uuid.UUID(internal_booking_id) come il codice reale: un nome
      provider al posto dell'UUID solleva ValueError (era il bug P0).
    - Unique (organization_id, idempotency_key), stati pending/synced/failed.
    - claim_sync_slot atomico: un solo vincitore per (org, key).
    """

    def __init__(self):
        self._rows: dict[tuple[str, str], dict] = {}
        self._lock = asyncio.Lock()
        self.get_credentials = AsyncMock(return_value=None)

    def _coerce(self, organization_id, provider, internal_booking_id):
        if isinstance(organization_id, str):
            organization_id = uuid.UUID(organization_id)
        if isinstance(internal_booking_id, str):
            internal_booking_id = uuid.UUID(internal_booking_id)
        try:
            uuid.UUID(str(provider))
            raise ValueError(f"provider must be a name, got UUID-like: {provider!r}")
        except ValueError as exc:
            if "must be a name" in str(exc):
                raise
        return organization_id, provider, internal_booking_id

    async def claim_sync_slot(self, organization_id, idempotency_key: str,
                              provider: str, internal_booking_id=None):
        organization_id, provider, internal_booking_id = self._coerce(
            organization_id, provider, internal_booking_id)
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
                    "sync_error": None,
                }
                self._rows[key] = row
                return dict(row), True
            return dict(existing), False

    async def record_sync_prepare(
        self,
        organization_id,
        idempotency_key: str,
        provider: str,
        internal_booking_id=None,
        sync_status: str = "pending",
    ) -> dict:
        row, _acquired = await self.claim_sync_slot(
            organization_id, idempotency_key, provider, internal_booking_id)
        return row or {}

    async def record_sync_success(self, organization_id, idempotency_key, external_booking_id):
        row = self._rows[(str(organization_id), idempotency_key)]
        row["sync_status"] = "synced"
        row["external_booking_id"] = external_booking_id
        row["sync_error"] = None
        return dict(row)

    async def record_sync_failure(self, organization_id, idempotency_key, error_message, sync_status="failed"):
        row = self._rows[(str(organization_id), idempotency_key)]
        row["sync_status"] = sync_status
        row["sync_error"] = error_message
        return dict(row)

    async def get_sync_record(self, organization_id, idempotency_key):
        return self._rows.get((str(organization_id), idempotency_key))

    get_sync_by_idempotency_key = get_sync_record

    def row(self, org_id, key):
        return self._rows.get((str(org_id), key))


def _make_req(key: str, internal_id: uuid.UUID | None = None) -> CreateBookingRequest:
    return CreateBookingRequest(
        idempotency_key=key,
        customer=CustomerResult(
            customer_id="cust-1", nome="Mario", cognome="Rossi",
            telefono="+393331112233", email="mario@example.com",
        ),
        data=date(2026, 9, 15),
        ora_inizio=time(10, 0),
        service_id="svc-1",
        internal_booking_id=internal_id,
    )


def _make_adapter(provider_name: str = "fake", succeed: bool = True) -> MagicMock:
    adapter = MagicMock(spec=BookingSystemPort)
    adapter.provider_name = provider_name
    if succeed:
        adapter.create_booking = AsyncMock(
            return_value=BookingResult(
                success=True, external_booking_id="ext-1",
                stato="confermata", sync_status="synced",
            )
        )
    else:
        adapter.create_booking = AsyncMock(
            return_value=BookingResult(
                success=False, stato="rifiutata", error_code="slot_full",
                error_message="Slot occupato", sync_status="failed",
            )
        )
    return adapter


class TestSyncPrepareContract:
    @pytest.mark.asyncio
    async def test_create_external_booking_prepare_success_flow(self):
        """prepare -> provider call -> success: riga scritta con org/provider/internal-id/key corretti."""
        repo = StrictFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        internal_id = uuid.uuid4()
        req = _make_req("ext-book:contract-01", internal_id)
        adapter = _make_adapter("calcom")

        res = await router.dispatch_create_booking(
            org_id=ORG_A, req=req, adapter=adapter,
            mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
        )

        assert res.success is True
        assert res.external_booking_id == "ext-1"
        adapter.create_booking.assert_awaited_once()
        row = repo.row(ORG_A, "ext-book:contract-01")
        assert row is not None
        assert row["organization_id"] == ORG_A
        assert row["idempotency_key"] == "ext-book:contract-01"
        assert row["provider"] == "calcom"
        assert row["internal_booking_id"] == internal_id
        assert row["sync_status"] == "synced"
        assert row["external_booking_id"] == "ext-1"

    @pytest.mark.asyncio
    async def test_sync_failure_flow(self):
        """prepare -> provider rifiuto -> failure registrata con errore."""
        repo = StrictFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        req = _make_req("ext-book:contract-fail")
        adapter = _make_adapter("beds24", succeed=False)

        res = await router.dispatch_create_booking(
            org_id=ORG_A, req=req, adapter=adapter,
            mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
        )

        assert res.success is False
        row = repo.row(ORG_A, "ext-book:contract-fail")
        assert row["sync_status"] == "uncertain"
        assert row["sync_error"] == "external_outcome_unverified"
        assert row["provider"] == "beds24"

    @pytest.mark.asyncio
    async def test_duplicate_idempotency_no_second_call(self):
        """synced replay -> no-op, adapter mai richiamato la seconda volta."""
        repo = StrictFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        req = _make_req("ext-book:contract-dup")
        adapter = _make_adapter("apaleo")

        first = await router.dispatch_create_booking(
            org_id=ORG_A, req=req, adapter=adapter,
            mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
        )
        assert first.success is True

        second = await router.dispatch_create_booking(
            org_id=ORG_A, req=req, adapter=adapter,
            mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
        )
        assert second.success is True
        assert second.external_booking_id == "ext-1"
        assert second.sync_status == "synced"
        adapter.create_booking.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pending_guards_concurrent_second_call(self):
        """Riga pending (chiamata in corso) -> seconda chiamata non duplica l'esterna."""
        repo = StrictFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        req = _make_req("ext-book:contract-pending")
        adapter = _make_adapter("simplybook")
        await repo.record_sync_prepare(ORG_A, req.idempotency_key, "simplybook", None)

        res = await router.dispatch_create_booking(
            org_id=ORG_A, req=req, adapter=adapter,
            mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
        )

        assert res.sync_status == "uncertain"
        adapter.create_booking.assert_not_called()

    @pytest.mark.asyncio
    async def test_historical_failed_requires_reconciliation(self):
        """failed storico non fornisce la prova di mancato invio."""
        repo = StrictFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        req = _make_req("ext-book:contract-retry")
        await repo.record_sync_prepare(ORG_A, req.idempotency_key, "zak", None)
        await repo.record_sync_failure(ORG_A, req.idempotency_key, "boom")
        adapter = _make_adapter("zak")

        res = await router.dispatch_create_booking(
            org_id=ORG_A, req=req, adapter=adapter,
            mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
        )

        assert res.success is False and res.sync_status == "uncertain"
        adapter.create_booking.assert_not_awaited()
        assert repo.row(ORG_A, "ext-book:contract-retry")["sync_status"] == "failed"

    @pytest.mark.asyncio
    async def test_concurrent_dispatches_single_row_no_crash(self):
        """Due dispatch concorrenti stessa key: nessuna eccezione, una sola riga, stato finale coerente."""
        repo = StrictFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        req = _make_req("ext-book:contract-race")
        adapter = _make_adapter("calcom")

        results = await asyncio.gather(
            router.dispatch_create_booking(
                org_id=ORG_A, req=req, adapter=adapter,
                mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
            ),
            router.dispatch_create_booking(
                org_id=ORG_A, req=req, adapter=adapter,
                mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
            ),
        )

        assert all(r.success for r in results)
        rows = [r for (o, _), r in repo._rows.items() if o == str(ORG_A)]
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "synced"

    @pytest.mark.asyncio
    async def test_tenant_isolation(self):
        """Tenant B non vede/riusa la riga di Tenant A: prepare separato, replay separato."""
        repo = StrictFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        adapter = _make_adapter("calcom")

        await router.dispatch_create_booking(
            org_id=ORG_A, req=_make_req("ext-book:shared-key"), adapter=adapter,
            mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
        )
        # Stessa key ma altro tenant -> NON è replay: chiama l'esterna e scrive riga propria
        res_b = await router.dispatch_create_booking(
            org_id=ORG_B, req=_make_req("ext-book:shared-key"), adapter=adapter,
            mode=BookingMode.AUTHORITATIVE, verticale="parrucchiere",
        )

        assert res_b.success is True
        assert adapter.create_booking.await_count == 2
        row_a = repo.row(ORG_A, "ext-book:shared-key")
        row_b = repo.row(ORG_B, "ext-book:shared-key")
        assert row_a["organization_id"] == ORG_A
        assert row_b["organization_id"] == ORG_B
        assert row_a is not row_b
