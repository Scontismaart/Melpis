"""Suite di test per ApaleoAdapter (tests/core/bookings/test_apaleo_adapter.py).

Copre:
1. Port conformance (BookingSystemPort protocol)
2. Inizializzazione e validazione credenziali
3. Token acquisition e injection dell'header Authorization
4. Token refresh automatico dopo HTTP 401
5. get_opening_hours con mapping timezone
6. get_services con conversione Unit Groups (camere)
7. get_availability con semantica hospitality (stay multi-giorno, offers, rate plan, prezzi dinamici)
8. create_booking con verifica dell'header nativo Idempotency-Key e mapping payload
9. cancel_booking con 204 No Content e gestione idempotente se già cancellata
10. update_booking con amend date
11. get_customer con ricerca testSearch e mapping canonical CustomerResult
12. Error handling: 401, 403, 404, 409 (conflict/slot_full), 422 (validation), 429 (rate-limit con Retry-After), 5xx, timeout, malformed response
13. Circuit breaker con fast-fail
14. Zero Secret Leak nel logging e rappresentazione stringa
"""
import asyncio
import json
import time
import uuid
from datetime import date, time as dtime, timedelta

import httpx
import pytest

from src.core.bookings.adapters.apaleo_adapter import (
    ApaleoAdapter,
    ApaleoAuthError,
    ApaleoConflictError,
    ApaleoError,
    ApaleoNetworkError,
    ApaleoNotFoundError,
    ApaleoRateLimitError,
    ApaleoServerError,
    ApaleoTimeoutError,
    ApaleoValidationError,
)
from src.core.bookings.adapters.apaleo_auth import ApaleoOAuthClient
from src.core.bookings.adapters.simplybook_adapter import CircuitOpenError
from src.core.bookings.ports.base import (
    AvailabilityQuery,
    BookingSystemPort,
    CancelBookingRequest,
    CreateBookingRequest,
    CustomerQuery,
    CustomerResult,
    UpdateBookingRequest,
)

ORG_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
TEST_CLIENT_ID = "APALEO_CLIENT_TEST"
TEST_CLIENT_SECRET = "APALEO_SECRET_TEST"
TEST_PROPERTY_ID = "MUC"


def create_mock_auth_client(transport: httpx.MockTransport) -> ApaleoOAuthClient:
    """Crea un ApaleoOAuthClient connesso a un client mock con token pre-calcolato."""
    mock_http_client = httpx.AsyncClient(transport=transport)
    return ApaleoOAuthClient(
        organization_id=ORG_ID,
        client_id=TEST_CLIENT_ID,
        client_secret=TEST_CLIENT_SECRET,
        initial_access_token="initial_valid_test_token_999",
        initial_expires_at=time.time() + 3600,
        client=mock_http_client,
    )


# ── 1. Port Conformance & Initialization ─────────────────────

def test_apaleo_adapter_port_conformance():
    """ApaleoAdapter rispetta il protocollo standard BookingSystemPort."""
    auth = ApaleoOAuthClient(
        organization_id=ORG_ID,
        client_id=TEST_CLIENT_ID,
        client_secret=TEST_CLIENT_SECRET,
        initial_access_token="mock",
        initial_expires_at=time.time() + 3600,
    )
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id=TEST_PROPERTY_ID, auth_client=auth)
    assert isinstance(adapter, BookingSystemPort)
    assert adapter.provider_name == "apaleo"
    assert adapter.organization_id == str(ORG_ID)


def test_missing_credentials_raises_auth_error():
    """Inizializzazione senza client_id o client_secret solleva ApaleoAuthError."""
    with pytest.raises(ApaleoAuthError):
        ApaleoAdapter(organization_id=ORG_ID, client_id="", client_secret="")


# ── 2. Token Acquisition & Headers ───────────────────────────

@pytest.mark.asyncio
async def test_token_acquisition_and_authorization_headers():
    """Le chiamate ad Apaleo includono l'header Authorization: Bearer <token>."""
    captured_headers = {}

    def handler(request: httpx.Request):
        nonlocal captured_headers
        captured_headers = dict(request.headers)
        return httpx.Response(200, json={"properties": [{"id": "MUC", "name": "Hotel Munich"}]})

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    await adapter.get_services(ORG_ID)
    assert "authorization" in captured_headers
    assert captured_headers["authorization"] == "Bearer initial_valid_test_token_999"


# ── 3. Token Refresh Automatico su 401 ───────────────────────

@pytest.mark.asyncio
async def test_automatic_token_refresh_on_401():
    """In caso di HTTP 401, l'adapter forza il refresh del token e ripete la richiesta con successo."""
    call_count = 0

    def handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        if request.url.path == "/connect/token":
            # Chiamata di refresh token all'Identity server
            return httpx.Response(200, json={"access_token": "refreshed_new_token_777", "expires_in": 3600})

        if request.url.path == "/inventory/v1/properties/MUC":
            if request.headers.get("authorization") == "Bearer initial_valid_test_token_999":
                return httpx.Response(401, json={"error": "invalid_token"})
            if request.headers.get("authorization") == "Bearer refreshed_new_token_777":
                return httpx.Response(200, json={"id": "MUC", "timeZone": "Europe/Berlin"})

        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    mock_http_client = httpx.AsyncClient(transport=transport)
    auth = ApaleoOAuthClient(
        organization_id=ORG_ID,
        client_id=TEST_CLIENT_ID,
        client_secret=TEST_CLIENT_SECRET,
        initial_access_token="initial_valid_test_token_999",
        initial_expires_at=time.time() + 3600,
        client=mock_http_client,
    )
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=mock_http_client)

    hours = await adapter.get_opening_hours(ORG_ID)
    assert hours.timezone == "Europe/Berlin"
    assert len(hours.orari) == 7


# ── 4. get_opening_hours ─────────────────────────────────────

@pytest.mark.asyncio
async def test_get_opening_hours_success():
    """get_opening_hours restituisce orari standard di check-in (14:00-22:00) e la timezone della struttura."""
    def handler(request: httpx.Request):
        assert request.url.path == "/inventory/v1/properties/MUC"
        return httpx.Response(200, json={"id": "MUC", "name": "Boutique Hotel", "timeZone": "Europe/Rome"})

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    res = await adapter.get_opening_hours(ORG_ID)
    assert res.timezone == "Europe/Rome"
    assert len(res.orari) == 7
    assert res.orari[0].fasce[0].inizio == dtime(14, 0)
    assert res.orari[0].fasce[0].fine == dtime(22, 0)


# ── 5. get_services (Unit Groups / Camere) ───────────────────

@pytest.mark.asyncio
async def test_get_services_maps_unit_groups():
    """get_services mappa le categorie di camera (Unit Groups) in ServiceItem canonicali."""
    def handler(request: httpx.Request):
        assert request.url.path == "/inventory/v1/unit-groups"
        assert request.url.params["propertyId"] == "MUC"
        return httpx.Response(
            200,
            json={
                "unitGroups": [
                    {
                        "id": "MUC-DBL",
                        "code": "DBL",
                        "name": {"it": "Camera Matrimoniale", "en": "Double Room"},
                        "description": {"it": "Letto king size con vista giardino"},
                        "type": "BedRoom",
                    },
                    {
                        "id": "MUC-SUI",
                        "code": "SUI",
                        "name": "Junior Suite",
                        "description": "Ampia suite con zona living",
                        "type": "Suite",
                    },
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    services = await adapter.get_services(ORG_ID)
    assert len(services) == 2
    assert services[0].service_id == "MUC-DBL"
    assert services[0].nome == "Camera Matrimoniale"
    assert services[0].durata_minuti == 1440  # 1 notte
    assert services[0].categoria == "BedRoom"

    assert services[1].service_id == "MUC-SUI"
    assert services[1].nome == "Junior Suite"


# ── 6. get_availability (Hospitality Semantics) ──────────────

@pytest.mark.asyncio
async def test_get_availability_hospitality_offers():
    """get_availability interroga le offerte di soggiorno (/booking/v1/offers) rispettando le date e l'occupazione."""
    captured_params = {}

    def handler(request: httpx.Request):
        nonlocal captured_params
        assert request.url.path == "/booking/v1/offers"
        captured_params = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "property": {"id": "MUC"},
                "offers": [
                    {
                        "arrival": "2026-09-10T14:00:00+02:00",
                        "departure": "2026-09-12T11:00:00+02:00",
                        "availableUnits": 3,
                        "unitGroup": {"id": "MUC-DBL", "name": "Double Room"},
                        "ratePlan": {"id": "MUC-BAR", "name": "Best Available Rate"},
                        "totalGrossAmount": {"amount": 250.0, "currency": "EUR"},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    query = AvailabilityQuery(
        data_inizio=date(2026, 9, 10),
        data_fine=date(2026, 9, 12),
        service_id="MUC-DBL",
        adulti=2,
    )
    res = await adapter.get_availability(ORG_ID, query)

    assert res.success is True
    assert len(res.slots) == 1
    slot = res.slots[0]
    assert slot.data == date(2026, 9, 10)
    assert slot.ora_inizio == dtime(14, 0)
    assert slot.ora_fine == dtime(11, 0)
    assert slot.disponibile is True
    assert slot.capacita_residua == 3
    assert slot.prezzo_cent == 25000  # 250.00 EUR -> 25000 centesimi
    assert slot.service_id == "MUC-DBL"

    # Verifica parametri inviati
    assert captured_params["arrival"] == "2026-09-10"
    assert captured_params["departure"] == "2026-09-12"
    assert captured_params["adults"] == "2"


@pytest.mark.asyncio
async def test_get_availability_empty_or_conflict():
    """Se non ci sono offerte disponibili, restituisce lista vuota senza errori."""
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"offers": []})

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    res = await adapter.get_availability(
        ORG_ID,
        AvailabilityQuery(data_inizio=date(2026, 9, 10), data_fine=date(2026, 9, 12)),
    )
    assert res.success is True
    assert res.slots == []


# ── 7. create_booking con Idempotency-Key Header ─────────────

@pytest.mark.asyncio
async def test_create_booking_with_native_idempotency_key():
    """create_booking invia l'header nativo Idempotency-Key e costruisce il payload hospitality."""
    captured_request = {}

    def handler(request: httpx.Request):
        nonlocal captured_request
        if request.url.path == "/booking/v1/offers":
            return httpx.Response(
                200,
                json={
                    "offers": [
                        {
                            "unitGroup": {"id": "MUC-DBL"},
                            "ratePlan": {"id": "MUC-BAR"},
                            "timeSlices": [{"ratePlanId": "MUC-BAR"}, {"ratePlanId": "MUC-BAR"}],
                        }
                    ]
                },
            )

        if request.url.path == "/booking/v1/bookings":
            captured_request["headers"] = dict(request.headers)
            captured_request["body"] = json.loads(request.content.decode())
            return httpx.Response(
                201,
                json={
                    "id": "BKG-XPGMSXGF",
                    "reservationIds": [{"id": "RES-XPGMSXGF-1"}],
                },
            )

        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    req = CreateBookingRequest(
        idempotency_key="idemp_hotel_999",
        customer=CustomerResult(
            customer_id="cust_001",
            nome="Mario",
            cognome="Rossi",
            telefono="+393401234567",
            email="mario.rossi@example.com",
        ),
        data=date(2026, 9, 10),
        data_fine=date(2026, 9, 12),
        ora_inizio=dtime(15, 0),
        service_id="MUC-DBL",
        adulti=2,
        note="Arrivo tardivo dopo le 20",
    )

    result = await adapter.create_booking(ORG_ID, req)

    assert result.success is True
    assert result.external_booking_id == "RES-XPGMSXGF-1"
    assert result.stato == "confermata"
    assert result.dettagli["booking_id"] == "BKG-XPGMSXGF"

    # Verifica header Idempotency-Key
    assert captured_request["headers"]["idempotency-key"] == "idemp_hotel_999"

    # Verifica payload del corpo
    body = captured_request["body"]
    assert body["booker"]["firstName"] == "Mario"
    assert body["booker"]["lastName"] == "Rossi"
    assert body["booker"]["email"] == "mario.rossi@example.com"
    res_entry = body["reservations"][0]
    assert res_entry["arrival"] == "2026-09-10"
    assert res_entry["departure"] == "2026-09-12"
    assert res_entry["adults"] == 2
    assert res_entry["guestComment"] == "Arrivo tardivo dopo le 20"
    assert len(res_entry["timeSlices"]) == 2


@pytest.mark.asyncio
async def test_create_booking_conflict_409_returns_slot_full():
    """In caso di HTTP 409 (overbooking / camera non disponibile), restituisce stato='rifiutata'."""
    def handler(request: httpx.Request):
        if request.url.path == "/booking/v1/offers":
            return httpx.Response(200, json={"offers": []})
        if request.url.path == "/booking/v1/bookings":
            return httpx.Response(
                409,
                json={"messages": ["No available units in the specified group"]},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    req = CreateBookingRequest(
        idempotency_key="idemp_conflict_01",
        customer=CustomerResult(customer_id="c1", nome="Luigi", cognome="Bianchi", telefono="+39340999999"),
        data=date(2026, 9, 10),
        data_fine=date(2026, 9, 11),
        ora_inizio=dtime(14, 0),
        service_id="MUC-DBL",
    )

    res = await adapter.create_booking(ORG_ID, req)
    assert res.success is False
    assert res.stato == "rifiutata"
    assert res.error_code == "slot_full"


@pytest.mark.asyncio
async def test_create_booking_rejects_missing_or_placeholder_name():
    """create_booking rifiuta la creazione se mancano nome e cognome reali o se sono placeholder generici."""
    adapter = ApaleoAdapter(
        organization_id=ORG_ID,
        property_id="MUC",
        auth_client=create_mock_auth_client(httpx.MockTransport(lambda _: httpx.Response(200))),
    )

    # Caso 1: Solo nome di battesimo senza cognome
    req1 = CreateBookingRequest(
        idempotency_key="idemp_no_surname",
        customer=CustomerResult(customer_id="c1", nome="Mario", cognome="", telefono="+393401234567"),
        data=date(2026, 9, 10),
        ora_inizio=dtime(14, 0),
        service_id="MUC-DBL",
    )
    res1 = await adapter.create_booking(ORG_ID, req1)
    assert res1.success is False
    assert res1.error_code == "missing_guest_name"

    # Caso 2: Segnaposto generici ("Ospite", "WhatsApp")
    req2 = CreateBookingRequest(
        idempotency_key="idemp_placeholder",
        customer=CustomerResult(customer_id="c2", nome="Ospite", cognome="WhatsApp", telefono="+393401234567"),
        data=date(2026, 9, 10),
        ora_inizio=dtime(14, 0),
        service_id="MUC-DBL",
    )
    res2 = await adapter.create_booking(ORG_ID, req2)
    assert res2.success is False
    assert res2.error_code == "missing_guest_name"

    # Caso 3: Nome parzialmente o totalmente mascherato ("M***o")
    req3 = CreateBookingRequest(
        idempotency_key="idemp_masked",
        customer=CustomerResult(customer_id="c3", nome="M***o", cognome="Rossi", telefono="+393401234567"),
        data=date(2026, 9, 10),
        ora_inizio=dtime(14, 0),
        service_id="MUC-DBL",
    )
    res3 = await adapter.create_booking(ORG_ID, req3)
    assert res3.success is False
    assert res3.error_code == "missing_guest_name"


@pytest.mark.asyncio
async def test_create_booking_accepts_split_full_name():
    """Se il cliente fornisce il nome completo in customer.nome ('Mario Rossi'), l'adapter lo suddivide automaticamente."""
    captured = {}

    def handler(request: httpx.Request):
        if request.url.path == "/booking/v1/offers":
            return httpx.Response(200, json={"offers": []})
        if request.url.path == "/booking/v1/bookings":
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(201, json={"id": "BKG-OK", "reservationIds": [{"id": "RES-OK"}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    req = CreateBookingRequest(
        idempotency_key="idemp_split_name",
        customer=CustomerResult(customer_id="c1", nome="Mario Rossi", cognome="", telefono="+393401234567"),
        data=date(2026, 9, 10),
        ora_inizio=dtime(14, 0),
        service_id="MUC-DBL",
    )
    res = await adapter.create_booking(ORG_ID, req)
    assert res.success is True
    assert captured["body"]["reservations"][0]["primaryGuest"]["firstName"] == "Mario"
    assert captured["body"]["reservations"][0]["primaryGuest"]["lastName"] == "Rossi"
    assert captured["body"]["booker"]["firstName"] == "Mario"
    assert captured["body"]["booker"]["lastName"] == "Rossi"


# ── 8. cancel_booking ────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_booking_success():
    """cancel_booking invia PUT su /reservation-actions/{id}/cancel e gestisce 204 No Content."""
    def handler(request: httpx.Request):
        assert request.method == "PUT"
        assert request.url.path == "/booking/v1/reservation-actions/RES-123/cancel"
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    res = await adapter.cancel_booking(
        ORG_ID,
        CancelBookingRequest(idempotency_key="can_001", external_booking_id="RES-123"),
    )
    assert res.success is True
    assert res.stato == "cancellata"


@pytest.mark.asyncio
async def test_cancel_booking_idempotent_already_cancelled():
    """Se la prenotazione è già cancellata, risponde con successo idempotente."""
    def handler(request: httpx.Request):
        return httpx.Response(
            400,
            json={"messages": ["Reservation RES-123 is already canceled."]},
        )

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    res = await adapter.cancel_booking(
        ORG_ID,
        CancelBookingRequest(idempotency_key="can_002", external_booking_id="RES-123"),
    )
    assert res.success is True
    assert res.stato == "cancellata"
    assert res.dettagli.get("already_cancelled") is True


# ── 9. update_booking ────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_booking_success():
    """update_booking invia PUT su amend con le nuove date e l'header Idempotency-Key."""
    captured = {}

    def handler(request: httpx.Request):
        nonlocal captured
        assert request.method == "PUT"
        assert request.url.path == "/booking/v1/reservation-actions/RES-123/amend"
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"id": "RES-123"})

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    res = await adapter.update_booking(
        ORG_ID,
        UpdateBookingRequest(
            idempotency_key="amend_001",
            external_booking_id="RES-123",
            nuova_data=date(2026, 9, 15),
            nuova_data_fine=date(2026, 9, 17),
        ),
    )
    assert res.success is True
    assert res.stato == "confermata"
    assert captured["headers"]["idempotency-key"] == "amend_001"
    assert captured["body"]["arrival"] == "2026-09-15"
    assert captured["body"]["departure"] == "2026-09-17"


# ── 10. get_customer ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_customer_found():
    """get_customer ricerca per telefono ed estrae i dettagli dell'ospite principale."""
    def handler(request: httpx.Request):
        assert request.url.path == "/booking/v1/reservations"
        assert request.url.params["textSearch"] == "+393401234567"
        return httpx.Response(
            200,
            json={
                "reservations": [
                    {
                        "id": "RES-789",
                        "bookingId": "BKG-999",
                        "status": "Confirmed",
                        "primaryGuest": {
                            "id": "GUEST-1",
                            "firstName": "Anna",
                            "lastName": "Verdi",
                            "phone": "+393401234567",
                            "email": "anna.verdi@example.com",
                        },
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    cust = await adapter.get_customer(ORG_ID, CustomerQuery(telefono="+393401234567"))
    assert cust is not None
    assert cust.customer_id == "GUEST-1"
    assert cust.nome == "Anna"
    assert cust.cognome == "Verdi"
    assert cust.email == "anna.verdi@example.com"
    assert cust.metadata["reservation_id"] == "RES-789"


@pytest.mark.asyncio
async def test_get_customer_masked_pii_sanitized():
    """Se Apaleo restituisce PII mascherati ('***' per policy Omit linked), vengono sanificati e display_name fa fallback."""
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "reservations": [
                    {
                        "id": "RES-MASKED",
                        "bookingId": "BKG-MASKED",
                        "status": "Confirmed",
                        "primaryGuest": {
                            "id": "GUEST-MASKED",
                            "firstName": "***",
                            "lastName": "***",
                            "phone": "***",
                            "email": "***@***.***",
                        },
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    cust = await adapter.get_customer(ORG_ID, CustomerQuery(telefono="+393401234567"))
    assert cust is not None
    assert cust.nome == ""
    assert cust.cognome == ""
    assert cust.email is None
    # Il telefono ripiega sul numero noto della query WhatsApp
    assert cust.telefono == "+393401234567"
    # Il display name adotta il fallback sicuro
    assert cust.display_name == "Gentile ospite"


@pytest.mark.asyncio
async def test_get_customer_partial_masking_sanitized():
    """Se Apaleo restituisce PII parzialmente mascherati (es. 'M***o', 'R***i'), vengono sanificati per evitare leak."""
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "reservations": [
                    {
                        "id": "RES-PARTIAL",
                        "bookingId": "BKG-PARTIAL",
                        "status": "Confirmed",
                        "primaryGuest": {
                            "id": "GUEST-PARTIAL",
                            "firstName": "M***o",
                            "lastName": "R***i",
                            "phone": "+39340***567",
                            "email": "***rossi@***.com",
                        },
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    cust = await adapter.get_customer(ORG_ID, CustomerQuery(telefono="+393401234567"))
    assert cust is not None
    # Sia il nome che il cognome parziali con asterischi vengono sanificati
    assert cust.nome == ""
    assert cust.cognome == ""
    assert cust.email is None
    # Il telefono ripiega sul numero non mascherato della query
    assert cust.telefono == "+393401234567"
    assert cust.display_name == "Gentile ospite"


@pytest.mark.asyncio
async def test_get_customer_not_found():
    """Se non ci sono prenotazioni corrispondenti, restituisce None."""
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"reservations": []})

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    cust = await adapter.get_customer(ORG_ID, CustomerQuery(telefono="+390000000000"))
    assert cust is None


# ── 11. Error Handling: 403, 404, 422, 429, 5xx, Timeout, Malformed ──

@pytest.mark.asyncio
async def test_error_auth_403_forbidden():
    """HTTP 403 solleva ApaleoAuthError."""
    def handler(request: httpx.Request):
        return httpx.Response(403, json={"error": "Forbidden"})

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    with pytest.raises(ApaleoAuthError, match="403"):
        await adapter.get_services(ORG_ID)


@pytest.mark.asyncio
async def test_error_not_found_404():
    """HTTP 404 solleva ApaleoNotFoundError."""
    def handler(request: httpx.Request):
        return httpx.Response(404, json={"messages": ["Property not found"]})

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="INVALID", auth_client=auth, client=client)

    with pytest.raises(ApaleoNotFoundError):
        await adapter.get_opening_hours(ORG_ID)


@pytest.mark.asyncio
async def test_error_validation_422():
    """HTTP 422 solleva ApaleoValidationError con i messaggi di validazione estratti."""
    def handler(request: httpx.Request):
        return httpx.Response(
            422,
            json={"messages": ["Departure date must be after arrival date"]},
        )

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    with pytest.raises(ApaleoValidationError, match="Departure date"):
        await adapter._request("GET", "/test")


@pytest.mark.asyncio
async def test_error_rate_limit_429_with_retry_after():
    """HTTP 429 estrae l'header Retry-After e solleva ApaleoRateLimitError."""
    def handler(request: httpx.Request):
        return httpx.Response(429, headers={"Retry-After": "45"}, text="Too Many Requests")

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    with pytest.raises(ApaleoRateLimitError) as exc_info:
        await adapter.get_services(ORG_ID)

    assert exc_info.value.retry_after == 45


@pytest.mark.asyncio
async def test_error_server_500():
    """HTTP 500 solleva ApaleoServerError."""
    def handler(request: httpx.Request):
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    with pytest.raises(ApaleoServerError):
        await adapter.get_services(ORG_ID)


@pytest.mark.asyncio
async def test_error_timeout():
    """Timeout solleva ApaleoTimeoutError."""
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("Read timed out")

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    with pytest.raises(ApaleoTimeoutError):
        await adapter.get_services(ORG_ID)


@pytest.mark.asyncio
async def test_error_malformed_json_response():
    """Risposta 200 non JSON solleva ApaleoError."""
    def handler(request: httpx.Request):
        return httpx.Response(200, text="NOT_VALID_JSON_!!!")

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(organization_id=ORG_ID, property_id="MUC", auth_client=auth, client=client)

    with pytest.raises(ApaleoError, match="malformata"):
        await adapter.get_services(ORG_ID)


# ── 12. Circuit Breaker Fast-Fail ────────────────────────────

@pytest.mark.asyncio
async def test_circuit_breaker_fast_fail():
    """5 errori consecutivi aprono il circuito, provocando fast-fail (<1ms) con CircuitOpenError."""
    def handler(request: httpx.Request):
        return httpx.Response(503, text="Service Unavailable")

    transport = httpx.MockTransport(handler)
    auth = create_mock_auth_client(transport)
    client = httpx.AsyncClient(transport=transport)
    adapter = ApaleoAdapter(
        organization_id=ORG_ID,
        property_id="MUC",
        auth_client=auth,
        circuit_breaker_threshold=3,
        circuit_breaker_reset_seconds=30.0,
        client=client,
    )

    # 3 errori consecutivi aprono il circuito
    for _ in range(3):
        with pytest.raises(ApaleoServerError):
            await adapter.get_services(ORG_ID)

    # La 4a chiamata fallisce istantaneamente tramite CircuitOpenError senza I/O
    start = time.perf_counter()
    with pytest.raises(CircuitOpenError):
        await adapter.get_services(ORG_ID)
    duration_ms = (time.perf_counter() - start) * 1000
    assert duration_ms < 5  # Fast fail sub-millisecondo


# ── 13. Zero Secret Leak ─────────────────────────────────────

def test_zero_secret_leak_in_adapter_repr():
    """__repr__ e __str__ non devono esporre client_secret, token o dati riservati."""
    auth = ApaleoOAuthClient(
        organization_id=ORG_ID,
        client_id="CLIENT_123",
        client_secret="SUPER_SECRET_CLIENT_PASSWORD_999",
        initial_access_token="SUPER_SECRET_ACCESS_TOKEN_XYZ",
        initial_expires_at=time.time() + 3600,
    )
    adapter = ApaleoAdapter(
        organization_id=ORG_ID,
        property_id="HOTEL_MUC",
        auth_client=auth,
    )

    r_repr = repr(adapter)
    r_str = str(adapter)

    assert "SUPER_SECRET_CLIENT_PASSWORD_999" not in r_repr
    assert "SUPER_SECRET_ACCESS_TOKEN_XYZ" not in r_repr
    assert "SUPER_SECRET_CLIENT_PASSWORD_999" not in r_str
    assert "SUPER_SECRET_ACCESS_TOKEN_XYZ" not in r_str

    assert "HOTEL_MUC" in r_repr
    assert str(ORG_ID) in r_repr


# ── 14. Integrazione con BookingAdapterRouter e BookingService ─

@pytest.mark.asyncio
async def test_router_resolves_apaleo_adapter():
    """BookingAdapterRouter risolve le credenziali di tipo 'apaleo' istanziando ApaleoAdapter."""
    from unittest.mock import AsyncMock, MagicMock
    from src.core.bookings.router import BookingAdapterRouter, BookingMode

    mock_repo = MagicMock()
    mock_repo.get_credentials = AsyncMock(
        return_value={
            "organization_id": ORG_ID,
            "provider": "apaleo",
            "is_active": True,
            "client_id": "CLIENT_TEST_ROUTER",
            "client_secret": "SECRET_TEST_ROUTER",
            "property_id": "MUC",
            "config": {"mode": "authoritative", "timeout_seconds": 6.0},
        }
    )

    router = BookingAdapterRouter(repo=mock_repo)
    adapter, mode, config = await router.resolve_adapter(ORG_ID)

    assert isinstance(adapter, ApaleoAdapter)
    assert adapter.provider_name == "apaleo"
    assert adapter._property_id == "MUC"
    assert mode == BookingMode.AUTHORITATIVE
    assert config["timeout_seconds"] == 6.0


@pytest.mark.asyncio
async def test_bookingservice_router_apaleo_dispatch_flow():
    """Verifica del flusso architetturale: BookingService -> BookingAdapterRouter -> BookingSystemPort -> ApaleoAdapter."""
    from unittest.mock import AsyncMock, MagicMock
    from src.core.bookings.router import BookingAdapterRouter
    from src.core.bookings.service import BookingService

    # Mock del trasporto per Apaleo API
    def handler(request: httpx.Request):
        if request.url.path == "/connect/token":
            return httpx.Response(200, json={"access_token": "token_flow_test", "expires_in": 3600})
        if request.url.path == "/booking/v1/offers":
            return httpx.Response(200, json={"offers": []})
        if request.url.path == "/booking/v1/bookings":
            return httpx.Response(201, json={"id": "BKG_FLOW_01", "reservationIds": [{"id": "RES_FLOW_01"}]})
        return httpx.Response(404)

    mock_transport = httpx.MockTransport(handler)
    mock_http_client = httpx.AsyncClient(transport=mock_transport)

    auth = ApaleoOAuthClient(
        organization_id=ORG_ID,
        client_id=TEST_CLIENT_ID,
        client_secret=TEST_CLIENT_SECRET,
        initial_access_token="token_flow_test",
        initial_expires_at=time.time() + 3600,
        client=mock_http_client,
    )
    apaleo_adapter = ApaleoAdapter(
        organization_id=ORG_ID,
        property_id="MUC",
        auth_client=auth,
        client=mock_http_client,
    )

    mock_repo = MagicMock()
    mock_repo.get_credentials = AsyncMock(
        return_value={
            "organization_id": ORG_ID,
            "provider": "apaleo",
            "is_active": True,
            "client_id": TEST_CLIENT_ID,
            "client_secret": TEST_CLIENT_SECRET,
            "property_id": "MUC",
            "config": {"mode": "authoritative"},
        }
    )
    mock_repo.record_sync_prepare = AsyncMock()
    mock_repo.record_sync_success = AsyncMock()
    mock_repo.record_sync_failure = AsyncMock()
    mock_repo.get_sync_by_idempotency_key = AsyncMock(return_value=None)
    mock_repo.claim_sync_slot = AsyncMock(
        return_value=({"sync_status": "pending"}, True)
    )

    from src.core.bookings.router import BookingMode
    router = BookingAdapterRouter(repo=mock_repo)
    router.resolve_adapter = AsyncMock(return_value=(apaleo_adapter, BookingMode.AUTHORITATIVE, {}))

    # Mock del repo interno di BookingService
    mock_booking_repo = MagicMock()
    mock_booking_repo.pool = None
    mock_booking_repo.list_bookings = AsyncMock(return_value=[])
    mock_booking_repo.get_booking_settings = AsyncMock(
        return_value={"capienze_orarie": {"15:00": 10}}
    )
    mock_booking_repo.create_booking = AsyncMock(
        side_effect=lambda **kwargs: {
            "id": uuid.uuid4(),
            "organization_id": kwargs.get("organization_id", ORG_ID),
            "nome_cliente": kwargs.get("nome_cliente"),
            "telefono": kwargs.get("telefono"),
            "data": kwargs.get("data"),
            "ora": kwargs.get("ora"),
            "coperti": kwargs.get("coperti", 1),
            "note": kwargs.get("note", ""),
            "stato": kwargs.get("stato", "confermata"),
            "richiede_intervento": kwargs.get("richiede_intervento", False),
        }
    )

    service = BookingService(
        booking_repo=mock_booking_repo,
        booking_router=router,
    )

    created_booking = await service.create_booking(
        org_id=ORG_ID,
        nome_cliente="Laura Bianchi",
        telefono="+393471234567",
        data="2026-09-20",
        ora="15:00",
        coperti=2,
        note="Soggiorno di lavoro",
        external_service_id="MUC-DBL",
    )

    # Verifica che il flusso sia completato e lo stato sia sincronizzato
    assert created_booking["external_sync_status"] == "synced"
    assert created_booking["external_booking_id"] == "RES_FLOW_01"
    assert mock_repo.record_sync_success.called

