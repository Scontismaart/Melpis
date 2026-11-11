"""Resilienza sync esterno: cancel idempotente, shadow terminale, chiave
deterministica senza source_message_id, sweep dei pending orfani."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.bookings.ports.base import (
    BookingResult,
    BookingSystemPort,
    CancelBookingRequest,
)
from src.core.bookings.router import BookingAdapterRouter, BookingMode

ORG = uuid.UUID("66666666-6666-6666-6666-666666666666")


class ClaimFakeRepo:
    """Claim atomico + marks, in-memory."""

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
                row = {"organization_id": organization_id,
                       "idempotency_key": idempotency_key, "provider": provider,
                       "internal_booking_id": internal_booking_id,
                       "sync_status": "pending", "external_booking_id": None}
                self._rows[key] = row
                return dict(row), True
            return dict(existing), False

    async def record_sync_success(self, organization_id, idempotency_key, external_booking_id):
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

    def row(self, org_id, key):
        return self._rows.get((str(org_id), key))


def _cancel_req(key="ext-cancel:org:b1", ext_id="ext-1") -> CancelBookingRequest:
    return CancelBookingRequest(idempotency_key=key, external_booking_id=ext_id)


def _cancel_adapter(ok=True) -> MagicMock:
    adapter = MagicMock(spec=BookingSystemPort)
    adapter.provider_name = "beds24"
    if ok:
        adapter.cancel_booking = AsyncMock(return_value=BookingResult(
            success=True, external_booking_id="ext-1",
            stato="cancellata", sync_status="synced"))
    else:
        adapter.cancel_booking = AsyncMock(return_value=BookingResult(
            success=False, stato="errore", error_code="x",
            error_message="reject", sync_status="failed"))
    return adapter


class TestCancelIdempotency:
    @pytest.mark.asyncio
    async def test_cancel_marks_synced(self):
        repo, router, adapter = ClaimFakeRepo(), None, _cancel_adapter()
        router = BookingAdapterRouter(repo=repo)
        res = await router.dispatch_cancel_booking(ORG, _cancel_req(), adapter=adapter, mode=BookingMode.AUTHORITATIVE)
        assert res.success is True and res.stato == "cancellata"
        assert repo.row(ORG, "ext-cancel:org:b1")["sync_status"] == "synced"

    @pytest.mark.asyncio
    async def test_duplicate_cancel_no_second_call(self):
        repo = ClaimFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        adapter = _cancel_adapter()
        await router.dispatch_cancel_booking(ORG, _cancel_req(), adapter=adapter, mode=BookingMode.AUTHORITATIVE)
        res = await router.dispatch_cancel_booking(ORG, _cancel_req(), adapter=adapter, mode=BookingMode.AUTHORITATIVE)
        assert res.success is True and res.sync_status == "synced"
        adapter.cancel_booking.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_concurrent_cancels_single_call(self):
        repo = ClaimFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        adapter = _cancel_adapter()
        results = await asyncio.gather(
            router.dispatch_cancel_booking(ORG, _cancel_req(), adapter=adapter, mode=BookingMode.AUTHORITATIVE),
            router.dispatch_cancel_booking(ORG, _cancel_req(), adapter=adapter, mode=BookingMode.AUTHORITATIVE),
        )
        assert adapter.cancel_booking.await_count == 1
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_unverified_cancel_is_uncertain_and_never_retried(self):
        repo = ClaimFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        bad = _cancel_adapter(ok=False)
        res = await router.dispatch_cancel_booking(ORG, _cancel_req(), adapter=bad, mode=BookingMode.AUTHORITATIVE)
        assert res.success is False
        assert repo.row(ORG, "ext-cancel:org:b1")["sync_status"] == "uncertain"
        good = _cancel_adapter(ok=True)
        res2 = await router.dispatch_cancel_booking(ORG, _cancel_req(), adapter=good, mode=BookingMode.AUTHORITATIVE)
        assert res2.success is False and res2.sync_status == "uncertain"
        good.cancel_booking.assert_not_awaited()
        assert repo.row(ORG, "ext-cancel:org:b1")["sync_status"] == "uncertain"


def _create_adapter(provider="zak", ok=True) -> MagicMock:
    from datetime import date, time

    from src.core.bookings.ports.base import CreateBookingRequest, CustomerResult

    adapter = MagicMock(spec=BookingSystemPort)
    adapter.provider_name = provider
    adapter.create_booking = AsyncMock(return_value=BookingResult(
        success=ok, external_booking_id="ext-s" if ok else None,
        stato="confermata" if ok else "rifiutata",
        error_message=None if ok else "no",
        sync_status="synced" if ok else "failed"))
    req = CreateBookingRequest(
        idempotency_key="ext-book:shadow-1",
        customer=CustomerResult(customer_id="c", nome="N", telefono="+390"),
        data=date(2026, 9, 21), ora_inizio=time(12, 0))
    return adapter, req


class TestShadowTerminalState:
    @pytest.mark.asyncio
    async def test_shadow_success_marks_synced(self):
        repo = ClaimFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        adapter, req = _create_adapter(ok=True)
        res = await router.dispatch_create_booking(
            ORG, req, adapter=adapter, mode=BookingMode.SHADOW, verticale="x")
        assert res.success is True and res.sync_status == "skipped_shadow"
        assert repo.row(ORG, "ext-book:shadow-1")["sync_status"] == "synced"

    @pytest.mark.asyncio
    async def test_shadow_failure_marks_uncertain_not_pending(self):
        repo = ClaimFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        adapter, req = _create_adapter(ok=False)
        adapter.create_booking = AsyncMock(side_effect=RuntimeError("timeout"))
        res = await router.dispatch_create_booking(
            ORG, req, adapter=adapter, mode=BookingMode.SHADOW, verticale="x")
        assert res.success is True  # non-blocking invariato
        assert res.sync_status == "uncertain"
        assert repo.row(ORG, "ext-book:shadow-1")["sync_status"] == "uncertain"
        await router.dispatch_create_booking(ORG, req, adapter=adapter, mode=BookingMode.SHADOW, verticale="x")
        adapter.create_booking.assert_awaited_once()


class TestDeterministicKey:
    @pytest.mark.asyncio
    async def test_same_inputs_same_key_replay(self):
        from src.core.bookings.service import BookingService

        repo = ClaimFakeRepo()
        router = BookingAdapterRouter(repo=repo)
        adapter = MagicMock(spec=BookingSystemPort)
        adapter.provider_name = "calcom"
        adapter.create_booking = AsyncMock(return_value=BookingResult(
            success=True, external_booking_id="ext-d",
            stato="confermata", sync_status="synced"))

        booking_repo = MagicMock()
        booking_repo.create_booking = AsyncMock(side_effect=lambda **kw: {
            "id": uuid.uuid4(), "organization_id": kw["organization_id"],
            "nome_cliente": kw["nome_cliente"], "telefono": kw["telefono"],
        })
        service = BookingService(repo=booking_repo, booking_router=router)
        disp = MagicMock()
        disp.coperti_liberi = 99
        disp.alternative = []
        service.verifica_disponibilita = AsyncMock(return_value=disp)
        service._valuta_richiede_deposito = AsyncMock(return_value=False)

        kwargs = dict(org_id=ORG, nome_cliente="Mario Rossi", data="2026-09-22",
                      ora="10:00", coperti=2, telefono="+393331112233")
        # Nessun source_message_id: due submit identici da dashboard
        with patch.object(router, "resolve_adapter",
                          return_value=(adapter, BookingMode.AUTHORITATIVE, {})):
            b1 = await service.create_booking(**kwargs)
            b2 = await service.create_booking(**kwargs)

        # Chiave deterministica sugli attributi (non id effimero di riga):
        # il secondo dispatch riusa la stessa chiave -> replay, una sola call
        assert adapter.create_booking.await_count == 1
        assert b1["external_sync_status"] == "synced"
        assert b2["external_sync_status"] == "synced"


class TestSweepJob:
    @pytest.mark.asyncio
    async def test_sweep_marks_stale_uncertain_and_counts(self):
        from src.core.bookings.sync_sweep_job import sweep_stale_syncs
        from src.core.db.repositories.external_booking_repo import (
            ExternalBookingRepository,
        )

        stale = {"organization_id": ORG, "idempotency_key": "k-stale",
                 "provider": "zak"}
        fresh = {"organization_id": ORG, "idempotency_key": "k-fresh",
                 "provider": "zak"}
        marked: list = []

        async def _get_pending(older_than_seconds=60, limit=50):
            assert older_than_seconds >= 1800 - 1
            return [stale, fresh]

        async def _fail(org, key, msg, sync_status="failed"):
            if key == "k-fresh":
                raise RuntimeError("db down")
            marked.append((org, key, msg, sync_status))
            return {"sync_status": sync_status}

        with patch.object(ExternalBookingRepository, "get_pending_syncs",
                          new=AsyncMock(side_effect=_get_pending)), \
             patch.object(ExternalBookingRepository, "record_sync_failure",
                          new=AsyncMock(side_effect=_fail)):
            out = await sweep_stale_syncs(MagicMock())

        assert out == {"examined": 2, "swept_to_uncertain": 1}
        assert marked[0][0] == ORG and marked[0][1] == "k-stale"
        assert "gestionale" in marked[0][2]
        assert marked[0][3] == "uncertain"

    @pytest.mark.asyncio
    async def test_sweep_empty_noop(self):
        from src.core.bookings.sync_sweep_job import sweep_stale_syncs
        from src.core.db.repositories.external_booking_repo import (
            ExternalBookingRepository,
        )

        with patch.object(ExternalBookingRepository, "get_pending_syncs",
                          new=AsyncMock(return_value=[])):
            assert await sweep_stale_syncs(MagicMock()) == {
                "examined": 0, "swept_to_uncertain": 0}
