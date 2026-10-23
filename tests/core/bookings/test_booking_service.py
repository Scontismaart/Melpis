from datetime import datetime, timezone, date, time
import pytest

pytestmark = pytest.mark.asyncio


async def test_verifica_disponibilita_slot_libero(booking_service, sample_org):
    disp = await booking_service.verifica_disponibilita(
        sample_org["id"], "2026-08-01", "20:00"
    )
    assert disp.coperti_liberi == 40
    assert disp.stato == "verde"


async def test_verifica_disponibilita_esclude_cancellata(booking_service, repo, sample_org):
    await repo.create_booking(organization_id=sample_org["id"], nome_cliente="X",
        data=date(2026, 8, 1), ora=time(20, 0), coperti=10, stato="cancellata")
    disp = await booking_service.verifica_disponibilita(
        sample_org["id"], "2026-08-01", "20:00"
    )
    assert disp.coperti_liberi == 40


async def test_verifica_disponibilita_esclude_no_show(booking_service, repo, sample_org):
    await repo.create_booking(organization_id=sample_org["id"], nome_cliente="X",
        data=date(2026, 8, 1), ora=time(20, 0), coperti=10, stato="no_show")
    disp = await booking_service.verifica_disponibilita(
        sample_org["id"], "2026-08-01", "20:00"
    )
    assert disp.coperti_liberi == 40


async def test_verifica_disponibilita_include_completata(booking_service, repo, sample_org):
    await repo.create_booking(organization_id=sample_org["id"], nome_cliente="X",
        data=date(2026, 8, 1), ora=time(20, 0), coperti=10, stato="completata",
        completata_at=datetime.now(timezone.utc))
    disp = await booking_service.verifica_disponibilita(
        sample_org["id"], "2026-08-01", "20:00"
    )
    assert disp.coperti_liberi == 30


async def test_semaforo_giorno_restituisce_slot(booking_service, sample_org):
    slots = await booking_service.semaforo_giorno(sample_org["id"], "2026-08-01")
    assert len(slots) == 24
    assert all(s.coperti_massimi == 40 for s in slots)


async def test_create_booking_success(booking_service, repo, sample_org):
    b = await booking_service.create_booking(
        sample_org["id"], nome_cliente="Mario", telefono="+393331234567",
        data="2026-08-01", ora="20:00", coperti=4,
    )
    assert b["stato"] == "in_attesa"
    assert b["nome_cliente"] == "Mario"


async def test_create_booking_slot_full(booking_service, repo, sample_org):
    for i in range(4):
        await repo.create_booking(organization_id=sample_org["id"], nome_cliente=f"G{i}",
            data=date(2026, 8, 1), ora=time(20, 0), coperti=10, stato="confermata")
    with pytest.raises(ValueError, match="slot pieno"):
        await booking_service.create_booking(
            sample_org["id"], nome_cliente="X",
            data="2026-08-01", ora="20:00", coperti=2,
        )


async def test_confirm_changes_stato(booking_service, repo, sample_org):
    b = await booking_service.create_booking(
        sample_org["id"], nome_cliente="Mario",
        data="2026-08-01", ora="20:00", coperti=4,
    )
    confirmed = await booking_service.confirm(sample_org["id"], b["id"])
    assert confirmed["stato"] == "confermata"


async def test_reject_changes_stato(booking_service, repo, sample_org):
    b = await booking_service.create_booking(
        sample_org["id"], nome_cliente="Mario",
        data="2026-08-01", ora="20:00", coperti=4,
    )
    rejected = await booking_service.reject(sample_org["id"], b["id"], "Siamo al completo")
    assert rejected["stato"] == "rifiutata"


async def test_reject_frees_capacity(booking_service, repo, sample_org):
    b = await booking_service.create_booking(
        sample_org["id"], nome_cliente="Mario",
        data="2026-08-01", ora="20:00", coperti=40,
    )
    await booking_service.reject(sample_org["id"], b["id"], "Completo")
    disp = await booking_service.verifica_disponibilita(
        sample_org["id"], "2026-08-01", "20:00"
    )
    assert disp.coperti_liberi == 40


async def test_mark_no_show(booking_service, repo, sample_org):
    b = await booking_service.create_booking(
        sample_org["id"], nome_cliente="Mario",
        data="2026-08-01", ora="20:00", coperti=4,
    )
    await booking_service.confirm(sample_org["id"], b["id"])
    ns = await booking_service.mark_no_show(sample_org["id"], b["id"])
    assert ns["stato"] == "no_show"
    assert ns["no_show_at"] is not None


async def test_mark_completed(booking_service, repo, sample_org):
    b = await booking_service.create_booking(
        sample_org["id"], nome_cliente="Mario",
        data="2026-08-01", ora="20:00", coperti=4,
    )
    await booking_service.confirm(sample_org["id"], b["id"])
    c = await booking_service.mark_completed(sample_org["id"], b["id"])
    assert c["stato"] == "completata"
    assert c["completata_at"] is not None


async def test_cross_tenant_isolation(booking_service, repo, sample_org, other_org):
    await booking_service.create_booking(
        sample_org["id"], nome_cliente="Org1",
        data="2026-08-01", ora="20:00", coperti=2,
    )
    disp_other = await booking_service.verifica_disponibilita(
        other_org["id"], "2026-08-01", "20:00"
    )
    assert disp_other.coperti_liberi == 40


async def test_update_booking_keeps_status_for_profile_change(booking_service, sample_org):
    b = await booking_service.create_booking(
        sample_org["id"], nome_cliente="Mario", data="2026-08-01",
        ora="20:00", coperti=4,
    )
    await booking_service.confirm(sample_org["id"], b["id"])

    updated = await booking_service.update_booking(
        sample_org["id"], b["id"], nome_cliente="Mario Rossi", note="Finestra"
    )

    assert updated["stato"] == "confermata"
    assert updated["nome_cliente"] == "Mario Rossi"


async def test_update_booking_schedule_returns_to_pending(booking_service, sample_org):
    b = await booking_service.create_booking(
        sample_org["id"], nome_cliente="Mario", data="2026-08-01",
        ora="20:00", coperti=4,
    )
    await booking_service.confirm(sample_org["id"], b["id"])

    updated = await booking_service.update_booking(
        sample_org["id"], b["id"], data="2026-08-02"
    )

    assert updated["stato"] == "in_attesa"
    assert updated["data"].isoformat() == "2026-08-02"


# ── Google is not local booking capacity ─────────────────────────────────

from datetime import timedelta as _timedelta

from src.core.bookings.memory_repo import InMemoryBookingRepo
from src.core.bookings.service import BookingService


class FakeCalendarGoogle:
    """Fake del solo metodo usato dal check disponibilità; sync_booking_state
    no-op perché create_booking la invoca dopo l'INSERT."""

    def __init__(self, busy=None, error=False):
        self.busy = busy or []
        self.error = error
        self.synced = []
        self.availability_calls = []

    async def get_busy_intervals(self, org_id, data):
        self.availability_calls.append((org_id, data))
        if self.error:
            raise RuntimeError("google down")
        return self.busy

    async def sync_booking_state(self, booking, org_id):
        self.synced.append(booking)


def _domani_iso():
    return (date.today() + _timedelta(days=1)).isoformat()


async def test_create_booking_internal_mode_ignores_google_busy_intervals():
    repo = InMemoryBookingRepo()
    data = _domani_iso()
    # A provider-side overlap cannot veto a locally authoritative booking.
    busy = [
        (
            datetime.fromisoformat(f"{data}T00:00:00"),
            datetime.fromisoformat(f"{data}T23:59:00"),
        )
    ]
    calendar = FakeCalendarGoogle(busy=busy)
    svc = BookingService(repo, None, None, calendar_service=calendar)

    booking = await svc.create_booking(
        org_id="org-1", nome_cliente="Mario", telefono="+393331112223",
        data=data, ora="20:00", coperti=2,
    )

    assert booking["stato"] == "in_attesa"
    assert await repo.get_booking("org-1", booking["id"]) is not None
    assert calendar.busy == busy
    assert calendar.availability_calls == []
    assert calendar.synced == [booking]


async def test_create_booking_optional_calendar_sync_does_not_query_google_availability():
    repo = InMemoryBookingRepo()
    await repo.upsert_booking_settings_config(
        "org-1", {"a05": {"authority": "internal_calendar_sync"}},
    )
    data = _domani_iso()
    calendar = FakeCalendarGoogle(error=True)
    svc = BookingService(repo, None, None, calendar_service=calendar)
    booking = await svc.create_booking(
        org_id="org-1", nome_cliente="Mario", telefono="+393331112223",
        data=data, ora="20:00", coperti=2,
    )
    assert booking["stato"] == "in_attesa"
    assert await repo.get_booking("org-1", booking["id"]) is not None
    assert calendar.error is True
    assert calendar.availability_calls == []
    assert calendar.synced == [booking]


async def test_update_schedule_optional_calendar_sync_uses_local_capacity_only():
    repo = InMemoryBookingRepo()
    await repo.upsert_booking_settings_config(
        "org-1", {"a05": {"authority": "internal_calendar_sync"}},
    )
    calendar = FakeCalendarGoogle(busy=[])
    svc = BookingService(repo, None, None, calendar_service=calendar)
    data_libera = _domani_iso()
    booking = await svc.create_booking(
        org_id="org-1", nome_cliente="Mario", telefono="+393331112223",
        data=data_libera, ora="12:00", coperti=2,
    )
    # Moving to a provider-busy time remains subject to local capienza only.
    calendar.busy = [
        (
            datetime.fromisoformat(f"{data_libera}T00:00:00"),
            datetime.fromisoformat(f"{data_libera}T23:59:00"),
        )
    ]
    updated = await svc.update_booking("org-1", booking["id"], ora="20:00")
    assert updated["ora"].strftime("%H:%M") == "20:00"
    assert await repo.get_booking("org-1", booking["id"]) is not None
    assert calendar.availability_calls == []
