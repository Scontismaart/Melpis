"""Test BookingAdapterRouter — routing, 4 modalità operative e data minimization fail-closed (Task 4).

Copre i requisiti architetturali:
1. Routing: risoluzione adapter corretto (SimplyBook, Fake, Internal) da external_booking_credentials
2. 4 Modalità operative:
   - authoritative: gestionale esterno comanda, fallimento solleva / richiede_umano, successo fa Send-Then-Mark
   - shadow: DB locale comanda, chiamata esterna best-effort non bloccante
   - mirror: dual-write sincronizzato con stato pending -> synced
   - local_only: solo DB locale, nessun adapter esterno
3. Data Minimization Fail-Closed (Invariante 6):
   - verticale="studio_medico" + medical_dpa_signed=False -> note sanitarie sbiancate/minimizzate
   - verticale="studio_medico" + medical_dpa_signed=True  -> note preservate
   - verticale=None (incerto / query fallita) -> FAIL-CLOSED: note minimizzate per sicurezza
   - verticale="ristorante" / "parrucchiere" -> note preservate
4. Send-Then-Mark & Idempotency:
   - record_sync_prepare -> call adapter -> record_sync_success / record_sync_failure
   - replay con stessa idempotency_key non duplica la chiamata esterna
"""
from __future__ import annotations

import uuid
from datetime import date, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.bookings.adapters.fake_adapter import FakeBookingAdapter
from src.core.bookings.adapters.internal_adapter import InternalBookingAdapter
from src.core.bookings.adapters.simplybook_adapter import (
    CircuitOpenError,
    SimplyBookAdapter,
    SimplyBookTimeoutError,
)
from src.core.bookings.ports.base import (
    AvailabilityQuery,
    AvailabilityResult,
    BookingResult,
    BookingSystemPort,
    CancelBookingRequest,
    CreateBookingRequest,
    CustomerResult,
    ServiceItem,
    SlotAvailability,
)
from src.core.bookings.router import (
    BookingAdapterRouter,
    BookingMode,
    DataMinimizationPolicy,
)

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.get_credentials = AsyncMock()
    repo.record_sync_prepare = AsyncMock()
    repo.record_sync_success = AsyncMock()
    repo.record_sync_failure = AsyncMock()
    repo.get_sync_by_idempotency_key = AsyncMock(return_value=None)
    repo.claim_sync_slot = AsyncMock(
        return_value=({"sync_status": "pending"}, True)
    )
    return repo


@pytest.fixture
def sample_customer():
    return CustomerResult(
        customer_id="cust-1",
        nome="Mario",
        cognome="Rossi",
        telefono="393515205809",
        email="mario@example.com",
    )


@pytest.fixture
def sample_create_req(sample_customer):
    return CreateBookingRequest(
        idempotency_key="ext-book:1111:msg-100",
        customer=sample_customer,
        data=date(2026, 9, 15),
        ora_inizio=time(10, 0),
        durata_minuti=45,
        service_id="svc-1",
        coperti=1,
        note="Forte dolore alla schiena, assume anticoagulanti",
    )


# ── 1. Risoluzione Adapter ────────────────────────────────────

class TestAdapterResolution:
    @pytest.mark.asyncio
    async def test_no_credentials_returns_internal_adapter(self, mock_repo):
        """Se l'organizzazione non ha credenziali esterne, usa InternalBookingAdapter."""
        mock_repo.get_credentials.return_value = None
        router = BookingAdapterRouter(repo=mock_repo)

        adapter, mode, config = await router.resolve_adapter(ORG_ID)
        assert isinstance(adapter, InternalBookingAdapter)
        assert mode == BookingMode.LOCAL_ONLY

    @pytest.mark.asyncio
    async def test_simplybook_credentials_resolves_simplybook_adapter(self, mock_repo):
        """Credenziali simplybook valide istanziano SimplyBookAdapter."""
        mock_repo.get_credentials.return_value = {
            "organization_id": ORG_ID,
            "provider": "simplybook",
            "is_active": True,
            "company_login": "my-barbershop",
            "api_key": "sb-api-key",
            "config": {"mode": "authoritative", "medical_dpa_signed": False},
        }
        router = BookingAdapterRouter(repo=mock_repo)

        adapter, mode, config = await router.resolve_adapter(ORG_ID)
        assert isinstance(adapter, SimplyBookAdapter)
        assert mode == BookingMode.AUTHORITATIVE
        assert config.get("medical_dpa_signed") is False


# ── 2. Data Minimization Fail-Closed (Invariante 6) ───────────

class TestDataMinimization:
    def test_medical_vertical_without_dpa_minimizes_note(self, sample_create_req):
        """Studio medico SENZA DPA firmato -> le note cliniche vengono rimosse/sbiancate."""
        policy = DataMinimizationPolicy()
        minimized_req = policy.apply(
            req=sample_create_req,
            verticale="studio_medico",
            medical_dpa_signed=False,
        )
        assert minimized_req.note != sample_create_req.note
        assert "anticoagulanti" not in minimized_req.note
        assert "dolore" not in minimized_req.note
        assert minimized_req.note == ""

    def test_medical_vertical_with_dpa_preserves_note(self, sample_create_req):
        """Studio medico CON DPA firmato -> le note sono autorizzate e preservate."""
        policy = DataMinimizationPolicy()
        minimized_req = policy.apply(
            req=sample_create_req,
            verticale="studio_medico",
            medical_dpa_signed=True,
        )
        assert minimized_req.note == sample_create_req.note
        assert "anticoagulanti" in minimized_req.note

    def test_unknown_vertical_fails_closed_minimizing(self, sample_create_req):
        """FAIL-CLOSED: Se il verticale è None (incerto o errore fetch), minimizza per sicurezza."""
        policy = DataMinimizationPolicy()
        minimized_req = policy.apply(
            req=sample_create_req,
            verticale=None,
            medical_dpa_signed=False,
        )
        # Fail-closed: se non possiamo provare che è non-medico, proteggiamo i dati sanitari
        assert minimized_req.note == ""

    def test_non_medical_vertical_preserves_note(self, sample_create_req):
        """Verticale chiaramente non-medico (es. ristorante, parrucchiere) preserva le note."""
        policy = DataMinimizationPolicy()
        minimized_req = policy.apply(
            req=sample_create_req,
            verticale="parrucchiere",
            medical_dpa_signed=False,
        )
        assert minimized_req.note == sample_create_req.note

    @pytest.mark.parametrize("dpa_value", [None, "false", "", 0, [], {}, "non-true-string"])
    def test_medical_vertical_non_true_dpa_fails_closed(self, sample_create_req, dpa_value):
        """Invariante 6: QUALSIASI valore diverso da True esplicito (None, 'false', 0, vuoto, null) minimizza."""
        policy = DataMinimizationPolicy()
        minimized_req = policy.apply(
            req=sample_create_req,
            verticale="studio_medico",
            medical_dpa_signed=dpa_value,
        )
        assert minimized_req.note == ""
        assert "anticoagulanti" not in minimized_req.note



# ── 3. Modalità Operative (Send-Then-Mark) ────────────────────

class TestBookingModes:
    @pytest.mark.asyncio
    async def test_authoritative_mode_success(self, mock_repo, sample_create_req):
        """Authoritative: prepara sync -> chiama adapter -> aggiorna sync a success."""
        mock_adapter = MagicMock(spec=BookingSystemPort)
        mock_adapter.create_booking = AsyncMock(
            return_value=BookingResult(
                success=True,
                external_booking_id="ext-99",
                stato="confermata",
                sync_status="synced",
            )
        )
        router = BookingAdapterRouter(repo=mock_repo)

        res = await router.dispatch_create_booking(
            org_id=ORG_ID,
            req=sample_create_req,
            adapter=mock_adapter,
            mode=BookingMode.AUTHORITATIVE,
            verticale="parrucchiere",
            medical_dpa_signed=False,
        )

        assert res.success is True
        assert res.external_booking_id == "ext-99"
        mock_repo.claim_sync_slot.assert_awaited_once()
        mock_repo.record_sync_success.assert_awaited_once_with(
            ORG_ID, sample_create_req.idempotency_key, "ext-99"
        )

    @pytest.mark.asyncio
    async def test_authoritative_mode_failure_records_sync_failure(
        self, mock_repo, sample_create_req
    ):
        """Authoritative: se l'adapter esterno fallisce, registra failure su sync e propaga errore."""
        mock_adapter = MagicMock(spec=BookingSystemPort)
        mock_adapter.create_booking = AsyncMock(
            return_value=BookingResult(
                success=False,
                stato="rifiutata",
                error_code="slot_unavailable",
                error_message="Slot già occupato",
                sync_status="failed",
            )
        )
        router = BookingAdapterRouter(repo=mock_repo)

        res = await router.dispatch_create_booking(
            org_id=ORG_ID,
            req=sample_create_req,
            adapter=mock_adapter,
            mode=BookingMode.AUTHORITATIVE,
            verticale="parrucchiere",
            medical_dpa_signed=False,
        )

        assert res.success is False
        mock_repo.claim_sync_slot.assert_awaited_once()
        mock_repo.record_sync_failure.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_authoritative_mode_circuit_open_fast_fails(
        self, mock_repo, sample_create_req
    ):
        """In authoritative mode, circuit breaker solleva CircuitOpenError -> gestito con sync_failure."""
        mock_adapter = MagicMock(spec=BookingSystemPort)
        mock_adapter.create_booking = AsyncMock(
            side_effect=CircuitOpenError("Circuit is open: richiede_umano=True")
        )
        router = BookingAdapterRouter(repo=mock_repo)

        res = await router.dispatch_create_booking(
            org_id=ORG_ID,
            req=sample_create_req,
            adapter=mock_adapter,
            mode=BookingMode.AUTHORITATIVE,
            verticale="parrucchiere",
            medical_dpa_signed=False,
        )

        assert res.success is False
        assert res.error_code == "external_outcome_uncertain"
        assert res.sync_status == "uncertain"
        mock_repo.record_sync_failure.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shadow_mode_does_not_block_on_external_failure(
        self, mock_repo, sample_create_req
    ):
        """Shadow mode: l'errore dell'adapter esterno non blocca l'esito principale."""
        mock_adapter = MagicMock(spec=BookingSystemPort)
        mock_adapter.create_booking = AsyncMock(
            side_effect=SimplyBookTimeoutError("Timeout di rete")
        )
        router = BookingAdapterRouter(repo=mock_repo)

        res = await router.dispatch_create_booking(
            org_id=ORG_ID,
            req=sample_create_req,
            adapter=mock_adapter,
            mode=BookingMode.SHADOW,
            verticale="parrucchiere",
            medical_dpa_signed=False,
        )

        # In shadow mode, l'esito principale è comunque confermato localmente
        assert res.success is True
        assert res.sync_status == "uncertain"

    @pytest.mark.asyncio
    async def test_idempotent_replay_returns_cached_sync(
        self, mock_repo, sample_create_req
    ):
        """Se una prenotazione con la stessa idempotency_key è già synced, non richiama l'adapter."""
        mock_repo.claim_sync_slot.return_value = (
            {
                "idempotency_key": sample_create_req.idempotency_key,
                "sync_status": "synced",
                "external_booking_id": "ext-already-created",
            },
            False,
        )
        mock_adapter = MagicMock(spec=BookingSystemPort)
        router = BookingAdapterRouter(repo=mock_repo)

        res = await router.dispatch_create_booking(
            org_id=ORG_ID,
            req=sample_create_req,
            adapter=mock_adapter,
            mode=BookingMode.AUTHORITATIVE,
            verticale="parrucchiere",
            medical_dpa_signed=False,
        )

        assert res.success is True
        assert res.external_booking_id == "ext-already-created"
        # L'adapter esterno non è stato invocato una seconda volta
        mock_adapter.create_booking.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotent_replay_pending_guards_concurrent_invocation(
        self, mock_repo, sample_create_req
    ):
        """Se una prenotazione con la stessa idempotency_key è ancora pending, non duplica la chiamata esterna."""
        mock_repo.claim_sync_slot.return_value = (
            {
                "idempotency_key": sample_create_req.idempotency_key,
                "sync_status": "pending",
            },
            False,
        )
        mock_adapter = MagicMock(spec=BookingSystemPort)
        router = BookingAdapterRouter(repo=mock_repo)

        res = await router.dispatch_create_booking(
            org_id=ORG_ID,
            req=sample_create_req,
            adapter=mock_adapter,
            mode=BookingMode.AUTHORITATIVE,
            verticale="parrucchiere",
            medical_dpa_signed=False,
        )

        assert res.sync_status == "uncertain"
        assert res.success is False
        mock_adapter.create_booking.assert_not_called()

    @pytest.mark.asyncio
    async def test_historical_failed_replay_requires_reconciliation(
        self, mock_repo, sample_create_req
    ):
        """Un vecchio failed non dimostra che il provider non abbia ricevuto la richiesta."""
        mock_repo.claim_sync_slot.return_value = (
            {
                "idempotency_key": sample_create_req.idempotency_key,
                "sync_status": "failed",
            },
            False,
        )
        mock_adapter = MagicMock(spec=BookingSystemPort)
        mock_adapter.create_booking = AsyncMock(
            return_value=BookingResult(
                success=True,
                external_booking_id="ext-retry-success",
                stato="confermata",
                sync_status="synced",
            )
        )
        router = BookingAdapterRouter(repo=mock_repo)

        res = await router.dispatch_create_booking(
            org_id=ORG_ID,
            req=sample_create_req,
            adapter=mock_adapter,
            mode=BookingMode.AUTHORITATIVE,
            verticale="parrucchiere",
            medical_dpa_signed=False,
        )

        assert res.success is False
        assert res.sync_status == "uncertain"
        mock_adapter.create_booking.assert_not_awaited()
        mock_repo.claim_sync_slot.assert_awaited_once()
        mock_repo.record_sync_success.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mirror_mode_raises_not_implemented(
        self, mock_repo, sample_create_req
    ):
        """La modalità mirror non è supportata senza webhook in ingresso e solleva NotImplementedError."""
        mock_adapter = MagicMock(spec=BookingSystemPort)
        router = BookingAdapterRouter(repo=mock_repo)

        with pytest.raises(NotImplementedError) as excinfo:
            await router.dispatch_create_booking(
                org_id=ORG_ID,
                req=sample_create_req,
                adapter=mock_adapter,
                mode=BookingMode.MIRROR,
                verticale="parrucchiere",
                medical_dpa_signed=False,
            )
        assert "webhook in ingresso" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_resolve_adapter_falls_back_from_mirror_to_authoritative(
        self, mock_repo
    ):
        """Se la config tenant indica mirror, resolve_adapter fa fallback a authoritative per sicurezza."""
        mock_repo.get_credentials.return_value = {
            "organization_id": ORG_ID,
            "provider": "simplybook",
            "is_active": True,
            "company_login": "my-barber",
            "api_key": "sb-key",
            "config": {"mode": "mirror"},
        }
        router = BookingAdapterRouter(repo=mock_repo)

        adapter, mode, config = await router.resolve_adapter(ORG_ID)
        assert mode == BookingMode.AUTHORITATIVE

