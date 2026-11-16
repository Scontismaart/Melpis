import os
import uuid
import pytest
import httpx
from unittest.mock import MagicMock
from fastapi import Depends, HTTPException

API_KEY = "test-api-key-12345"

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def set_env():
    os.environ["DATABASE_URL"] = ""
    os.environ["API_KEY_SERVICE"] = API_KEY


@pytest.fixture
async def async_client(repo, settings, booking_service, sample_org):
    from src.api.main import app
    from src.core.auth.dependencies import get_current_user, get_organization_context, get_token

    async def fixed_test_identity(token=Depends(get_token)):
        if token is None:
            raise HTTPException(401, "Test session required")
        if token != f"apikey:{API_KEY}":
            raise HTTPException(403, "Invalid test session")
        return {
            "source": "jwt", "aal": "aal2",
            "organization_id": str(sample_org["id"]), "ruolo": "owner",
            "auth_user_id": "bookings-test-owner", "user_id": None,
        }

    app.dependency_overrides[get_current_user] = fixed_test_identity
    app.dependency_overrides[get_organization_context] = fixed_test_identity
    app.state.repo = repo
    app.state.pool = MagicMock()
    app.state.booking_service = booking_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        try:
            yield c
        finally:
            app.dependency_overrides.clear()


async def test_semaforo_no_auth(async_client):
    resp = await async_client.get("/api/bookings/semaforo")
    assert resp.status_code == 401


async def test_semaforo_authenticated(async_client, sample_org):
    resp = await async_client.get("/api/bookings/semaforo", params={"data": "2026-08-01"}, headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 24


async def test_static_route_before_param(async_client, sample_org):
    # "semaforo" non deve matchare come {booking_id}
    resp = await async_client.get("/api/bookings/semaforo", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200


async def test_get_settings(async_client, sample_org):
    resp = await async_client.get("/api/bookings/settings", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200
    assert resp.json()["capienze_orarie"]["20:00"] == 40


async def test_create_booking(async_client, sample_org):
    resp = await async_client.post("/api/bookings", json={
        "nome_cliente": "Mario",
        "telefono": "+393331234567",
        "data": "2026-08-01",
        "ora": "20:00",
        "coperti": 4,
    }, headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["stato"] == "in_attesa"
    assert data["nome_cliente"] == "Mario"


async def test_get_booking(async_client, sample_org):
    # Creane una prima
    create_resp = await async_client.post("/api/bookings", json={
        "nome_cliente": "Mario",
        "telefono": "+393331234567",
        "data": "2026-08-01",
        "ora": "20:00",
        "coperti": 4,
    }, headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    b_id = create_resp.json()["id"]

    resp = await async_client.get(f"/api/bookings/{b_id}", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200
    assert resp.json()["id"] == b_id


async def test_legacy_booking_note_requires_attestation_on_reads(
    async_client, sample_org, repo, tomorrow
):
    booking = await repo.create_booking(
        organization_id=sample_org["id"],
        nome_cliente="Synthetic Guest",
        data=tomorrow,
        ora="18:00",
        coperti=2,
        note="synthetic legacy private note",
    )
    headers = {
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    }

    detail = await async_client.get(f"/api/bookings/{booking['id']}", headers=headers)
    listed = await async_client.get("/api/bookings", headers=headers)

    assert detail.status_code == 200
    assert detail.json()["note"] == ""
    assert listed.status_code == 200
    listed_booking = next(row for row in listed.json() if row["id"] == str(booking["id"]))
    assert listed_booking["note"] == ""
    stored = await repo.get_booking(sample_org["id"], booking["id"])
    assert stored["note"] == "synthetic legacy private note"


async def test_get_booking_not_found(async_client, sample_org):
    resp = await async_client.get("/api/bookings/00000000-0000-0000-0000-000000000000", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 404


async def test_other_organization_booking_cannot_be_read_or_updated(
    async_client, pg_pool, repo, tomorrow
):
    other_org_id = uuid.uuid4()
    await pg_pool.execute(
        "INSERT INTO organizations (id, name) VALUES ($1, 'Other Booking Org')",
        other_org_id,
    )
    foreign = await repo.create_booking(
        organization_id=other_org_id,
        nome_cliente="Tenant B guest",
        data=tomorrow,
        ora="18:00",
        coperti=2,
    )
    headers = {"X-API-Key": API_KEY}

    read = await async_client.get(f"/api/bookings/{foreign['id']}", headers=headers)
    update = await async_client.put(
        f"/api/bookings/{foreign['id']}",
        json={"nome_cliente": "Tampered by tenant A"},
        headers=headers,
    )

    assert read.status_code == 404
    assert update.status_code == 404
    unchanged = await repo.get_booking(other_org_id, foreign["id"])
    assert unchanged["nome_cliente"] == "Tenant B guest"


async def test_confirm_booking(async_client, sample_org):
    create_resp = await async_client.post("/api/bookings", json={
        "nome_cliente": "Mario",
        "telefono": "+393331234567",
        "data": "2026-08-01",
        "ora": "20:00",
        "coperti": 4,
    }, headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    b_id = create_resp.json()["id"]

    resp = await async_client.post(f"/api/bookings/{b_id}/confirm", headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200
    assert resp.json()["stato"] == "confermata"


async def test_list_bookings(async_client, sample_org):
    await async_client.post("/api/bookings", json={
        "nome_cliente": "Mario",
        "telefono": "+393331234567",
        "data": "2026-08-01",
        "ora": "20:00",
        "coperti": 4,
    }, headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    await async_client.post("/api/bookings", json={
        "nome_cliente": "Luigi",
        "telefono": "+393337654321",
        "data": "2026-08-01",
        "ora": "21:00",
        "coperti": 2,
    }, headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })

    resp = await async_client.get("/api/bookings", params={"data": "2026-08-01"}, headers={
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    })
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_update_booking(async_client, sample_org):
    headers = {
        "X-API-Key": API_KEY,
        "X-Organization-Id": str(sample_org["id"]),
    }
    create_resp = await async_client.post("/api/bookings", json={
        "nome_cliente": "Mario", "telefono": "+393331234567",
        "data": "2026-08-01", "ora": "20:00", "coperti": 4,
    }, headers=headers)
    b_id = create_resp.json()["id"]
    await async_client.post(f"/api/bookings/{b_id}/confirm", headers=headers)

    resp = await async_client.put(f"/api/bookings/{b_id}", json={
        "nome_cliente": "Mario Rossi", "note": "Finestra",
    }, headers=headers)

    assert resp.status_code == 200
    assert resp.json()["nome_cliente"] == "Mario Rossi"
    assert resp.json()["stato"] == "confermata"
