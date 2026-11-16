"""Test unitari per CalComAdapter (REST API v2) e conformità a BookingSystemPort.

Verifica:
1. Conformità formale a BookingSystemPort (Protocol)
2. Autenticazione con API Key tenant-scoped e header cal-api-version
3. get_services (Event Types Cal.com)
4. get_opening_hours (Schedules) e fallback su orari standard
5. get_customer (ricerca tramite attendee delle prenotazioni)
6. get_availability (slot orari ISO 8601 UTC)
7. create_booking (creazione con metadata, idempotenza e attendee)
8. update_booking (reschedule su nuovo slot)
9. cancel_booking (cancellazione e idempotenza su prenotazione già cancellata)
10. Error Handling: 401, 403, 404, 409 (conflict), 422, 429 (rate limit con Retry-After), 5xx
11. Errori di rete e timeout (ConnectTimeout, ReadTimeout, ConnectError)
12. Risposta malformata non JSON
13. Circuit Breaker: apertura dopo N fallimenti e fast-fail con CircuitOpenError
14. Risoluzione tramite BookingAdapterRouter
"""
import json
import uuid
from datetime import date, time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.core.bookings.adapters.calcom_adapter import (
    CALCOM_API_VERSION,
    CALCOM_DEFAULT_BASE_URL,
    CalComAdapter,
    CalComAuthError,
    CalComConflictError,
    CalComError,
    CalComNetworkError,
    CalComNotFoundError,
    CalComRateLimitError,
    CalComServerError,
    CalComTimeoutError,
    CalComValidationError,
)
from src.core.bookings.adapters.simplybook_adapter import CircuitOpenError
from src.core.bookings.ports.base import (
    AvailabilityQuery,
    BookingResult,
    BookingSystemPort,
    CancelBookingRequest,
    CreateBookingRequest,
    CustomerQuery,
    CustomerResult,
    UpdateBookingRequest,
)
from src.core.bookings.router import BookingAdapterRouter, BookingMode

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TEST_API_KEY = "cal_live_test1234567890abcdef"


# ── 1. Conformità e Inizializzazione ─────────────────────────

def test_calcom_adapter_port_conformance():
    """Verifica che CalComAdapter implementi formalmente BookingSystemPort."""
    adapter = CalComAdapter(api_key=TEST_API_KEY)
    assert isinstance(adapter, BookingSystemPort)


def test_calcom_adapter_missing_api_key():
    """Inizializzazione senza API key deve sollevare CalComAuthError."""
    with pytest.raises(CalComAuthError, match="API key mancante"):
        CalComAdapter(api_key="")


def test_calcom_auth_headers():
    """Verifica che gli header HTTP includano Bearer token e cal-api-version corretta."""
    adapter = CalComAdapter(api_key=TEST_API_KEY)
    headers = adapter._auth_headers()
    assert headers["Authorization"] == f"Bearer {TEST_API_KEY}"
    assert headers["cal-api-version"] == CALCOM_API_VERSION
    assert headers["Content-Type"] == "application/json"


# ── 2. Services (Event Types) ────────────────────────────────

@pytest.mark.asyncio
async def test_get_services_success():
    """get_services mappa correttamente gli Event Types di Cal.com v2 in ServiceItem."""
    mock_payload = {
        "status": "success",
        "data": [
            {
                "id": 101,
                "title": "Visita Cardiologica",
                "slug": "visita-cardiologica",
                "description": "Controllo specialistico completo",
                "lengthInMinutes": 45,
                "price": 120.0,
                "currency": "EUR",
            },
            {
                "id": 102,
                "title": "Consulenza Breve",
                "slug": "consulenza-breve",
                "lengthInMinutes": 15,
                "price": 0,
                "currency": "EUR",
            },
        ],
    }

    def handler(request: httpx.Request):
        assert request.url.path == "/v2/event-types"
        assert request.method == "GET"
        assert request.headers["Authorization"] == f"Bearer {TEST_API_KEY}"
        return httpx.Response(200, json=mock_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    services = await adapter.get_services(ORG_ID)
    assert len(services) == 2

    s1 = services[0]
    assert s1.service_id == "101"
    assert s1.nome == "Visita Cardiologica"
    assert s1.durata_minuti == 45
    assert s1.prezzo_cent == 12000
    assert s1.valuta == "EUR"
    assert s1.categoria == "visita"

    s2 = services[1]
    assert s2.service_id == "102"
    assert s2.durata_minuti == 15
    assert s2.prezzo_cent == 0


# ── 3. Opening Hours & Schedules ─────────────────────────────

@pytest.mark.asyncio
async def test_get_opening_hours_success():
    """get_opening_hours mappa gli orari dallo schedule Cal.com."""
    mock_payload = {
        "status": "success",
        "data": [
            {
                "id": 1,
                "name": "Default Schedule",
                "timeZone": "Europe/Rome",
                "availability": [
                    {
                        "days": ["Monday", "Tuesday", "Wednesday"],
                        "startTime": "09:00",
                        "endTime": "13:00",
                    }
                ],
            }
        ],
    }

    def handler(request: httpx.Request):
        assert request.url.path == "/v2/schedules"
        return httpx.Response(200, json=mock_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    oh = await adapter.get_opening_hours(ORG_ID)
    assert len(oh.orari) == 7
    # Lunedì aperto con fascia 09:00 - 13:00
    assert oh.orari[0].aperto is True
    assert len(oh.orari[0].fasce) == 1
    assert oh.orari[0].fasce[0].inizio == time(9, 0)
    assert oh.orari[0].fasce[0].fine == time(13, 0)
    # Domenica chiuso
    assert oh.orari[6].aperto is False


@pytest.mark.asyncio
async def test_get_opening_hours_fallback():
    """In caso di endpoint schedules vuoto o con errore 404, fallback su orari feriali standard."""
    def handler(request: httpx.Request):
        return httpx.Response(404, json={"message": "No schedules"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    oh = await adapter.get_opening_hours(ORG_ID)
    assert len(oh.orari) == 7
    # Lunedì-Venerdì aperto, Weekend chiuso
    assert oh.orari[0].aperto is True
    assert oh.orari[5].aperto is False
    assert oh.orari[6].aperto is False


# ── 4. Customer Search ───────────────────────────────────────

@pytest.mark.asyncio
async def test_get_customer_found():
    """get_customer trova l'anagrafica cliente cercandola negli attendee delle prenotazioni."""
    mock_payload = {
        "status": "success",
        "data": [
            {
                "id": 501,
                "attendees": [
                    {
                        "id": 99,
                        "name": "Mario Rossi",
                        "email": "mario.rossi@example.com",
                        "phoneNumber": "+393401234567",
                        "timeZone": "Europe/Rome",
                    }
                ],
            }
        ],
    }

    def handler(request: httpx.Request):
        assert request.url.path == "/v2/bookings"
        assert request.url.params.get("attendeeEmail") == "mario.rossi@example.com"
        return httpx.Response(200, json=mock_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    cust = await adapter.get_customer(ORG_ID, CustomerQuery(email="mario.rossi@example.com"))
    assert cust is not None
    assert cust.nome == "Mario"
    assert cust.cognome == "Rossi"
    assert cust.email == "mario.rossi@example.com"
    assert cust.telefono == "+393401234567"


@pytest.mark.asyncio
async def test_get_customer_not_found():
    """get_customer restituisce None se nessun attendee corrisponde."""
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"status": "success", "data": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    cust = await adapter.get_customer(ORG_ID, CustomerQuery(email="inesistente@test.com"))
    assert cust is None


# ── 5. Availability ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_availability_success():
    """get_availability interroga /v2/slots/available con timestamp ISO UTC e parsa gli slot."""
    mock_payload = {
        "status": "success",
        "data": {
            "slots": {
                "2026-09-10": [
                    {"time": "2026-09-10T09:00:00.000Z"},
                    {"time": "2026-09-10T10:00:00.000Z"},
                ]
            }
        },
    }

    captured_params = {}

    def handler(request: httpx.Request):
        nonlocal captured_params
        assert request.url.path == "/v2/slots/available"
        captured_params = dict(request.url.params)
        return httpx.Response(200, json=mock_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    query = AvailabilityQuery(
        data_inizio=date(2026, 9, 10),
        data_fine=date(2026, 9, 10),
        service_id="101",
    )
    res = await adapter.get_availability(ORG_ID, query)

    assert res.success is True
    assert len(res.slots) == 2
    assert res.slots[0].data == date(2026, 9, 10)
    assert res.slots[0].ora_inizio == time(9, 0)
    assert res.slots[0].disponibile is True
    assert res.slots[1].ora_inizio == time(10, 0)

    # Verifica formattazione timestamp UTC
    assert captured_params["startTime"] == "2026-09-10T00:00:00Z"
    assert captured_params["endTime"] == "2026-09-10T23:59:59Z"
    assert captured_params["eventTypeId"] == "101"


# ── 6. Create Booking ────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_booking_success():
    """create_booking invia payload conforme con start ISO UTC, attendee e idempotency_key."""
    mock_payload = {
        "status": "success",
        "data": {
            "id": 888,
            "uid": "b_cal_xyz123",
            "title": "Visita con Mario Rossi",
            "start": "2026-09-10T10:00:00Z",
            "status": "ACCEPTED",
        },
    }

    sent_body = {}

    def handler(request: httpx.Request):
        nonlocal sent_body
        assert request.url.path == "/v2/bookings"
        assert request.method == "POST"
        sent_body = json.loads(request.content.decode())
        return httpx.Response(201, json=mock_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    req = CreateBookingRequest(
        idempotency_key="ext-book:1111:msg-001",
        customer=CustomerResult(
            customer_id="cust-1",
            nome="Mario",
            cognome="Rossi",
            telefono="+393401234567",
            email="mario@example.com",
        ),
        data=date(2026, 9, 10),
        ora_inizio=time(10, 0),
        service_id="101",
        note="Prima visita di controllo",
    )

    res = await adapter.create_booking(ORG_ID, req)
    assert res.success is True
    assert res.external_booking_id == "b_cal_xyz123"
    assert res.stato == "confermata"
    assert res.sync_status == "synced"

    # Verifica body inviato
    assert sent_body["start"] == "2026-09-10T10:00:00Z"
    assert sent_body["eventTypeId"] == 101
    assert sent_body["attendee"]["name"] == "Mario Rossi"
    assert sent_body["attendee"]["email"] == "mario@example.com"
    assert sent_body["attendee"]["phoneNumber"] == "+393401234567"
    assert sent_body["metadata"]["idempotency_key"] == "ext-book:1111:msg-001"
    assert sent_body["notes"] == "Prima visita di controllo"


@pytest.mark.asyncio
async def test_create_booking_conflict_409():
    """HTTP 409 (slot già occupato) deve essere mappato a stato='rifiutata' con error_code='slot_full'."""
    def handler(request: httpx.Request):
        return httpx.Response(409, json={"status": "error", "message": "Slot already taken"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    req = CreateBookingRequest(
        idempotency_key="ext-book:1111:conflict-01",
        customer=CustomerResult(customer_id="c1", nome="Giulia", telefono="+393330001122"),
        data=date(2026, 9, 10),
        ora_inizio=time(11, 0),
        service_id="101",
    )

    res = await adapter.create_booking(ORG_ID, req)
    assert res.success is False
    assert res.stato == "rifiutata"
    assert res.error_code == "slot_full"
    assert res.sync_status == "failed"


# ── 7. Reschedule / Update Booking ───────────────────────────

@pytest.mark.asyncio
async def test_update_booking_reschedule_success():
    """update_booking chiama POST /v2/bookings/{uid}/reschedule con il nuovo timestamp ISO."""
    mock_payload = {
        "status": "success",
        "data": {
            "uid": "b_cal_rescheduled_999",
            "start": "2026-09-12T15:00:00Z",
            "status": "ACCEPTED",
        },
    }

    sent_body = {}

    def handler(request: httpx.Request):
        nonlocal sent_body
        assert request.url.path == "/v2/bookings/b_cal_xyz123/reschedule"
        assert request.method == "POST"
        sent_body = json.loads(request.content.decode())
        return httpx.Response(200, json=mock_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    req = UpdateBookingRequest(
        idempotency_key="ext-upd:1111:001",
        external_booking_id="b_cal_xyz123",
        nuova_data=date(2026, 9, 12),
        nuova_ora_inizio=time(15, 0),
        nuove_note="Posticipo richiesto dal paziente",
    )

    res = await adapter.update_booking(ORG_ID, req)
    assert res.success is True
    assert res.external_booking_id == "b_cal_rescheduled_999"
    assert res.stato == "confermata"
    assert res.sync_status == "synced"
    assert sent_body["start"] == "2026-09-12T15:00:00Z"
    assert sent_body["reschedulingReason"] == "Posticipo richiesto dal paziente"


# ── 8. Cancel Booking ────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_booking_success():
    """cancel_booking chiama POST /v2/bookings/{uid}/cancel."""
    mock_payload = {
        "status": "success",
        "data": {"uid": "b_cal_xyz123", "status": "CANCELLED"},
    }

    def handler(request: httpx.Request):
        assert request.url.path == "/v2/bookings/b_cal_xyz123/cancel"
        assert request.method == "POST"
        body = json.loads(request.content.decode())
        assert body["cancellationReason"] == "Imprevisto lavorativo"
        return httpx.Response(200, json=mock_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    req = CancelBookingRequest(
        idempotency_key="ext-cancel:1111:001",
        external_booking_id="b_cal_xyz123",
        motivo="Imprevisto lavorativo",
    )

    res = await adapter.cancel_booking(ORG_ID, req)
    assert res.success is True
    assert res.stato == "cancellata"
    assert res.sync_status == "synced"


@pytest.mark.asyncio
async def test_cancel_booking_idempotent_already_cancelled():
    """Se Cal.com restituisce errore indicando che la prenotazione è già cancellata, trattiamo come successo."""
    def handler(request: httpx.Request):
        return httpx.Response(400, json={"message": "Booking already cancelled"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    req = CancelBookingRequest(
        idempotency_key="ext-cancel:1111:002",
        external_booking_id="b_cal_xyz123",
    )

    res = await adapter.cancel_booking(ORG_ID, req)
    assert res.success is True
    assert res.stato == "cancellata"


# ── 9. Error Handling & HTTP Statuses ────────────────────────

@pytest.mark.asyncio
async def test_error_auth_401():
    """HTTP 401 deve sollevare CalComAuthError."""
    def handler(request: httpx.Request):
        return httpx.Response(401, json={"message": "Invalid API key"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    with pytest.raises(CalComAuthError, match="401"):
        await adapter.get_services(ORG_ID)


@pytest.mark.asyncio
async def test_error_auth_403():
    """HTTP 403 deve sollevare CalComAuthError."""
    def handler(request: httpx.Request):
        return httpx.Response(403, json={"message": "Forbidden scope"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    with pytest.raises(CalComAuthError, match="403"):
        await adapter.get_services(ORG_ID)


@pytest.mark.asyncio
async def test_error_not_found_404():
    """HTTP 404 deve sollevare CalComNotFoundError."""
    def handler(request: httpx.Request):
        return httpx.Response(404, json={"message": "Not found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    with pytest.raises(CalComNotFoundError):
        await adapter.get_services(ORG_ID)


@pytest.mark.asyncio
async def test_error_validation_422():
    """HTTP 422 deve sollevare CalComValidationError."""
    def handler(request: httpx.Request):
        return httpx.Response(422, text="Invalid ISO string")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    with pytest.raises(CalComValidationError):
        await adapter.get_services(ORG_ID)


@pytest.mark.asyncio
async def test_error_rate_limit_429():
    """HTTP 429 deve sollevare CalComRateLimitError ed estrarre il Retry-After header."""
    def handler(request: httpx.Request):
        return httpx.Response(429, headers={"Retry-After": "45"}, json={"message": "Rate limit exceeded"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    with pytest.raises(CalComRateLimitError) as exc_info:
        await adapter.get_services(ORG_ID)
    assert exc_info.value.retry_after == 45


@pytest.mark.asyncio
async def test_error_server_500():
    """HTTP 500 deve sollevare CalComServerError e incrementare i fallimenti del circuit breaker."""
    def handler(request: httpx.Request):
        return httpx.Response(500, text="Internal Server Error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    with pytest.raises(CalComServerError):
        await adapter.get_services(ORG_ID)
    assert adapter._circuit_failure_count == 1


@pytest.mark.asyncio
async def test_error_timeout():
    """Timeout HTTP deve sollevare CalComTimeoutError."""
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("Socket timeout")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    with pytest.raises(CalComTimeoutError):
        await adapter.get_services(ORG_ID)
    assert adapter._circuit_failure_count == 1


@pytest.mark.asyncio
async def test_error_network():
    """Errore socket/connessione deve sollevare CalComNetworkError."""
    def handler(request: httpx.Request):
        raise httpx.ConnectError("Connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    with pytest.raises(CalComNetworkError):
        await adapter.get_services(ORG_ID)
    assert adapter._circuit_failure_count == 1


@pytest.mark.asyncio
async def test_error_malformed_response():
    """Risposta 200 con corpo non JSON deve sollevare CalComError."""
    def handler(request: httpx.Request):
        return httpx.Response(200, text="<html><body>Gateway Error</body></html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    with pytest.raises(CalComError, match="non valida"):
        await adapter.get_services(ORG_ID)


# ── 10. Circuit Breaker Fast-Fail ────────────────────────────

@pytest.mark.asyncio
async def test_circuit_breaker_fast_fail():
    """Dopo N fallimenti consecutivi, il circuito si apre e solleva istantaneamente CircuitOpenError."""
    fail_count = 0

    def handler(request: httpx.Request):
        nonlocal fail_count
        fail_count += 1
        return httpx.Response(503, text="Service Unavailable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, circuit_breaker_threshold=3, client=client)

    # 3 fallimenti consecutivi
    for _ in range(3):
        with pytest.raises(CalComServerError):
            await adapter.get_services(ORG_ID)

    assert adapter._circuit_is_open is True
    assert fail_count == 3

    # La quarta chiamata deve sollevare CircuitOpenError senza inviare richieste HTTP (fast-fail)
    with pytest.raises(CircuitOpenError):
        await adapter.get_services(ORG_ID)

    assert fail_count == 3  # Nessuna nuova richiesta inviata


# ── 11. Integrazione con BookingAdapterRouter ────────────────

@pytest.mark.asyncio
async def test_router_resolves_calcom():
    """BookingAdapterRouter risolve e istanzia CalComAdapter quando provider='calcom'."""
    mock_repo = MagicMock()
    mock_repo.get_credentials = AsyncMock(return_value={
        "provider": "calcom",
        "api_key": "cal_live_secret_123",
        "is_active": True,
        "config": {
            "mode": "authoritative",
            "timeout_seconds": 3.0,
            "circuit_breaker_threshold": 4,
        },
    })

    router = BookingAdapterRouter(repo=mock_repo)
    adapter, mode, config = await router.resolve_adapter(ORG_ID)

    assert isinstance(adapter, CalComAdapter)
    assert mode == BookingMode.AUTHORITATIVE
    assert adapter._timeout == 3.0
    assert adapter._circuit_breaker_threshold == 4


@pytest.mark.asyncio
async def test_router_dispatch_create_booking_with_calcom():
    """Verifica il flusso completo di dispatch del router verso CalComAdapter con Send-Then-Mark."""
    mock_payload = {
        "status": "success",
        "data": {
            "id": 777,
            "uid": "b_cal_full_flow",
            "start": "2026-09-15T09:00:00Z",
            "status": "ACCEPTED",
        },
    }

    captured_body = {}

    def handler(request: httpx.Request):
        nonlocal captured_body
        assert request.url.path == "/v2/bookings"
        captured_body = json.loads(request.content.decode())
        return httpx.Response(201, json=mock_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cal_adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    mock_repo = MagicMock()
    mock_repo.get_credentials = AsyncMock(return_value={
        "provider": "calcom",
        "api_key": TEST_API_KEY,
        "is_active": True,
        "config": {"mode": "authoritative"},
    })
    mock_repo.get_sync_record = AsyncMock(return_value=None)
    mock_repo.get_sync_by_idempotency_key = AsyncMock(return_value=None)
    mock_repo.record_sync_prepare = AsyncMock()
    mock_repo.record_sync_success = AsyncMock()
    mock_repo.record_sync_failure = AsyncMock()
    mock_repo.claim_sync_slot = AsyncMock(
        return_value=({"sync_status": "pending"}, True)
    )

    router = BookingAdapterRouter(repo=mock_repo)

    req = CreateBookingRequest(
        idempotency_key="ext-book:test-calcom-01",
        customer=CustomerResult(
            customer_id="cust-1",
            nome="Giuseppe",
            cognome="Verdi",
            telefono="+393331112233",
            email="giuseppe@example.com",
        ),
        data=date(2026, 9, 15),
        ora_inizio=time(9, 0),
        service_id="101",
        note="Sintomi influenzali",
    )

    # Chiamata di dispatch con verticale medico e DPA non firmato (Invariante 6 GDPR)
    res = await router.dispatch_create_booking(
        org_id=ORG_ID,
        req=req,
        adapter=cal_adapter,
        mode=BookingMode.AUTHORITATIVE,
        verticale="studio_medico",
        medical_dpa_signed=False,
    )

    assert res.success is True
    assert res.external_booking_id == "b_cal_full_flow"
    assert res.stato == "confermata"
    assert res.sync_status == "synced"

    # Verifica GDPR Data Minimization (Invariante 6): note rimosse a monte prima dell'invio a Cal.com!
    assert "notes" not in captured_body

    # Verifica Send-Then-Mark (claim atomico:
    # org, idempotency_key, provider, internal_booking_id)
    mock_repo.claim_sync_slot.assert_awaited_once()
    claim_args = mock_repo.claim_sync_slot.call_args[0]
    assert claim_args[0] == ORG_ID
    assert claim_args[1] == "ext-book:test-calcom-01"
    assert claim_args[2] == "calcom"  # provider_name corretto in 3a posizione!
    assert claim_args[3] is None  # internal_booking_id non impostato in questa request

    mock_repo.record_sync_success.assert_awaited_once_with(
        ORG_ID, "ext-book:test-calcom-01", "b_cal_full_flow"
    )


@pytest.mark.asyncio
async def test_router_dispatch_calcom_strips_medical_notes_with_dpa_flag():
    """Il flag DPA legacy non autorizza l'invio di note verso Cal.com."""
    captured_body = {}

    def handler(request: httpx.Request):
        nonlocal captured_body
        captured_body = json.loads(request.content.decode())
        return httpx.Response(201, json={"status": "success", "data": {"uid": "b_cal_dpa_ok"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cal_adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    mock_repo = MagicMock()
    mock_repo.get_credentials = AsyncMock(return_value={
        "provider": "calcom", "api_key": TEST_API_KEY, "is_active": True, "config": {"mode": "authoritative"}
    })
    mock_repo.get_sync_by_idempotency_key = AsyncMock(return_value=None)
    mock_repo.record_sync_prepare = AsyncMock()
    mock_repo.record_sync_success = AsyncMock()
    mock_repo.claim_sync_slot = AsyncMock(
        return_value=({"sync_status": "pending"}, True)
    )

    router = BookingAdapterRouter(repo=mock_repo)

    req = CreateBookingRequest(
        idempotency_key="ext-book:test-dpa-ok",
        customer=CustomerResult(customer_id="c1", nome="Anna", telefono="+393330009988", email="anna@test.it"),
        data=date(2026, 9, 16),
        ora_inizio=time(15, 0),
        service_id="101",
        note="Consulenza post-operatoria autorizzata",
    )

    res = await router.dispatch_create_booking(
        org_id=ORG_ID,
        req=req,
        adapter=cal_adapter,
        mode=BookingMode.AUTHORITATIVE,
        verticale="studio_medico",
        medical_dpa_signed=True,  # DPA firmato
    )

    assert res.success is True
    assert not captured_body.get("notes")


# ── 12. Gestione Email Fallback e Data Quality Invariants ────

@pytest.mark.asyncio
async def test_create_booking_with_real_email():
    """1. Cliente con email reale: invia l'email reale e contrassegna is_synthetic_email=False."""
    sent_payload = {}

    def handler(request: httpx.Request):
        nonlocal sent_payload
        assert request.url.path == "/v2/bookings"
        sent_payload = json.loads(request.content.decode())
        return httpx.Response(201, json={"status": "success", "data": {"uid": "b_real_1"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    req = CreateBookingRequest(
        idempotency_key="key-real-1",
        customer=CustomerResult(
            customer_id="c1",
            nome="Laura",
            cognome="Bianchi",
            telefono="+393331234567",
            email="laura.bianchi@example.com",
        ),
        data=date(2026, 9, 20),
        ora_inizio=time(10, 0),
        service_id="101",
    )

    res = await adapter.create_booking(ORG_ID, req)
    assert res.success is True
    assert sent_payload["attendee"]["email"] == "laura.bianchi@example.com"
    assert sent_payload["metadata"]["is_synthetic_email"] is False
    assert sent_payload["metadata"]["original_phone"] == "+393331234567"


@pytest.mark.asyncio
async def test_create_booking_without_email_generates_rfc2606_synthetic():
    """2. Cliente senza email: genera un indirizzo deterministico su dominio .invalid e tagga i metadati."""
    sent_payload = {}

    def handler(request: httpx.Request):
        nonlocal sent_payload
        assert request.url.path == "/v2/bookings"
        sent_payload = json.loads(request.content.decode())
        return httpx.Response(201, json={"status": "success", "data": {"uid": "b_synth_1"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, placeholder_email_domain="noemail.invalid", client=client)

    req = CreateBookingRequest(
        idempotency_key="key-synth-1",
        customer=CustomerResult(
            customer_id="c2",
            nome="Marco",
            cognome="Neri",
            telefono="+39 340 9876543",
            email=None,
        ),
        data=date(2026, 9, 20),
        ora_inizio=time(11, 0),
        service_id="101",
    )

    res = await adapter.create_booking(ORG_ID, req)
    assert res.success is True
    assert sent_payload["attendee"]["email"] == "wa_393409876543@noemail.invalid"
    assert sent_payload["metadata"]["is_synthetic_email"] is True
    assert sent_payload["metadata"]["original_phone"] == "+39 340 9876543"


@pytest.mark.asyncio
async def test_create_booking_retry_idempotency_deterministic_email():
    """3. Retry della prenotazione: stesso telefono genera lo stesso synthetic email senza collisioni casuali."""
    captured_emails = []

    def handler(request: httpx.Request):
        nonlocal captured_emails
        body = json.loads(request.content.decode())
        captured_emails.append(body["attendee"]["email"])
        return httpx.Response(201, json={"status": "success", "data": {"uid": "b_retry_1"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    customer = CustomerResult(customer_id="c3", nome="Gianna", telefono="+393281122334", email=None)
    req = CreateBookingRequest(
        idempotency_key="idemp-key-repeat",
        customer=customer,
        data=date(2026, 9, 21),
        ora_inizio=time(14, 0),
        service_id="101",
    )

    # Prima esecuzione e successivo retry (es. dopo network glitch o riconnessione WhatsApp)
    res1 = await adapter.create_booking(ORG_ID, req)
    res2 = await adapter.create_booking(ORG_ID, req)

    assert res1.success is True and res2.success is True
    assert len(captured_emails) == 2
    assert captured_emails[0] == captured_emails[1] == "wa_393281122334@noemail.invalid"


@pytest.mark.asyncio
async def test_get_customer_masks_synthetic_email():
    """4. Lookup cliente successivo: l'adapter intercetta l'email sintetica e restituisce email=None verso downstream."""
    mock_payload = {
        "status": "success",
        "data": [
            {
                "id": 901,
                "attendees": [
                    {
                        "id": 88,
                        "name": "Gianna",
                        "email": "wa_393281122334@noemail.invalid",
                        "phoneNumber": "+393281122334",
                        "timeZone": "Europe/Rome",
                    }
                ],
            }
        ],
    }

    def handler(request: httpx.Request):
        return httpx.Response(200, json=mock_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    # Ricerca per telefono
    cust = await adapter.get_customer(ORG_ID, CustomerQuery(telefono="+393281122334"))
    assert cust is not None
    assert cust.nome == "Gianna"
    assert cust.telefono == "+393281122334"
    # Invariante fondamentale: email sintetica MAI esposta downstream!
    assert cust.email is None
    assert cust.metadata["is_synthetic_email"] is True


@pytest.mark.asyncio
async def test_subsequent_update_and_cancel_with_synthetic_booking():
    """5. Update/reschedule e cancel successivi: funzionano correttamente tramite bookingUid, indipendentemente dall'email."""
    reschedule_called = False
    cancel_called = False

    def handler(request: httpx.Request):
        nonlocal reschedule_called, cancel_called
        if request.url.path == "/v2/bookings/b_synth_uid_99/reschedule":
            reschedule_called = True
            return httpx.Response(200, json={"status": "success", "data": {"uid": "b_synth_uid_99"}})
        if request.url.path == "/v2/bookings/b_synth_uid_99/cancel":
            cancel_called = True
            return httpx.Response(200, json={"status": "success", "data": {"uid": "b_synth_uid_99"}})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CalComAdapter(api_key=TEST_API_KEY, client=client)

    # Reschedule
    upd_res = await adapter.update_booking(
        ORG_ID,
        UpdateBookingRequest(
            idempotency_key="upd-synth-1",
            external_booking_id="b_synth_uid_99",
            nuova_data=date(2026, 9, 25),
            nuova_ora_inizio=time(16, 0),
        ),
    )
    assert upd_res.success is True
    assert reschedule_called is True

    # Cancel
    cancel_res = await adapter.cancel_booking(
        ORG_ID,
        CancelBookingRequest(
            idempotency_key="canc-synth-1",
            external_booking_id="b_synth_uid_99",
            motivo="Impossibilitato a partecipare",
        ),
    )
    assert cancel_res.success is True
    assert cancel_called is True


@pytest.mark.asyncio
async def test_configurable_placeholder_domain():
    """Verifica che il dominio placeholder sia configurabile per tenant e riconosciuto da is_synthetic_email."""
    adapter = CalComAdapter(
        api_key=TEST_API_KEY,
        placeholder_email_domain="custom-tenant.invalid",
    )
    cust = CustomerResult(customer_id="c4", nome="Luca", telefono="+39111222333", email=None)
    synth = adapter._generate_synthetic_email(cust)
    assert synth == "wa_39111222333@custom-tenant.invalid"
    assert adapter.is_synthetic_email(synth) is True
    assert adapter.is_synthetic_email("luca@gmail.com") is False


