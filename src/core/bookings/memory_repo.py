"""InMemoryBookingRepo — fallback per demo mode (nessun database).

Implementa i metodi di CoreRepository che BookingService usa nel flusso
dashboard (list, create, settings, semaforo). La logica di calcolo della
disponibilita' resta in BookingService: questo repo fornisce solo lo stato
in-memory con gli stessi tipi (date/time) della controparte PostgreSQL.

Nota: confirm/mark_no_show/mark_completed accedono a repo.pool direttamente
e non sono coperti qui — non fanno parte del percorso frontend demo.
"""

import uuid
from contextlib import nullcontext
from datetime import date, datetime, time, timezone
from copy import deepcopy


SLOT_ORE = [f"{h:02d}:00" for h in range(24)]


class InMemoryBookingRepo:
    def __init__(self):
        self._bookings: list[dict] = []
        self._settings: dict[str, dict] = {}
        self.pool = None

    # ── Bookings ──────────────────────────────────────────────

    def slot_lock(self, organization_id, data, ora):
        """No-op: lo store demo e' in-process e monoprocesso, niente race
        reale da serializzare. Stessa firma di CoreRepository.slot_lock."""
        return nullcontext()


    async def create_booking(self, organization_id, nome_cliente, data, ora, coperti,
                             telefono="", note="", stato="in_attesa", origine="Dashboard",
                             richiede_intervento=False, id_conversazione=None,
                             contact_id=None, richiede_deposito=False,
                             completata_at=None, tipo_evento="", source_message_id=None):
        if isinstance(data, str):
            data = date.fromisoformat(data)
        if isinstance(ora, str):
            ore, minuti = ora.split(":")
            ora = time(int(ore), int(minuti))
        if source_message_id is not None:
            for b in self._bookings:
                if b.get("organization_id") == organization_id and b.get("source_message_id") == source_message_id:
                    return deepcopy(b)

        booking = {
            "id": uuid.uuid4(),
            "organization_id": organization_id,
            "contact_id": contact_id,
            "nome_cliente": nome_cliente,
            "telefono": telefono,
            "data": data,
            "ora": ora,
            "coperti": coperti,
            "note": note,
            "stato": stato,
            "origine": origine,
            "richiede_intervento": richiede_intervento,
            "id_conversazione": id_conversazione,
            "richiede_deposito": richiede_deposito,
            "completata_at": completata_at,
            "tipo_evento": tipo_evento,
            "source_message_id": source_message_id,
        }
        self._bookings.append(booking)
        return deepcopy(booking)

    async def get_booking(self, organization_id, booking_id):
        for b in self._bookings:
            if b["organization_id"] == organization_id and str(b["id"]) == str(booking_id):
                return deepcopy(b)
        return None

    async def list_bookings(self, organization_id, data=None):
        if data is not None and isinstance(data, str):
            data = date.fromisoformat(data)
        rows = [
            b for b in self._bookings
            if b["organization_id"] == organization_id
            and (data is None or b["data"] == data)
        ]
        rows.sort(key=lambda b: (b["data"], b["ora"]))
        return [deepcopy(b) for b in rows]

    async def list_bookings_by_stato(self, organization_id, stato):
        rows = [
            b for b in self._bookings
            if b["organization_id"] == organization_id and b["stato"] == stato
        ]
        rows.sort(key=lambda b: (b["data"], b["ora"]))
        return [deepcopy(b) for b in rows]

    async def update_booking_status(self, organization_id, booking_id, stato, expected_status=None, expected=None):
        for b in self._bookings:
            if b["organization_id"] == organization_id and str(b["id"]) == str(booking_id):
                if expected_status is not None and b["stato"] != expected_status:
                    return None
                if expected and any(b.get(key) != expected.get(key) for key in ("stato", "data", "ora", "coperti") if key in expected):
                    return None
                b["stato"] = stato
                return deepcopy(b)
        return None

    async def update_booking_details(self, organization_id, booking_id,
                                     nome_cliente, telefono, data, ora,
                                     coperti, note, stato, expected=None):
        if isinstance(data, str):
            data = date.fromisoformat(data)
        if isinstance(ora, str):
            ore, minuti = ora.split(":")
            ora = time(int(ore), int(minuti))
        for b in self._bookings:
            if b["organization_id"] == organization_id and str(b["id"]) == str(booking_id):
                if expected and any(b.get(key) != expected.get(key) for key in ("stato", "data", "ora", "coperti") if key in expected):
                    return None
                b.update({
                    "nome_cliente": nome_cliente,
                    "telefono": telefono,
                    "data": data,
                    "ora": ora,
                    "coperti": coperti,
                    "note": note,
                    "stato": stato,
                })
                return deepcopy(b)
        return None

    async def mark_booking_completed(self, organization_id, booking_id, expected):
        updated = await self.update_booking_status(
            organization_id, booking_id, "completata", expected=expected,
        )
        if updated:
            for booking in self._bookings:
                if booking["organization_id"] == organization_id and str(booking["id"]) == str(booking_id):
                    booking["completata_at"] = datetime.now(timezone.utc)
                    return deepcopy(booking)
        return None

    async def mark_booking_requires_intervention(self, organization_id, booking_id, expected=None):
        for booking in self._bookings:
            if booking["organization_id"] == organization_id and str(booking["id"]) == str(booking_id):
                if expected and any(booking.get(key) != expected.get(key) for key in ("stato", "data", "ora", "coperti") if key in expected):
                    return
                booking["richiede_intervento"] = True
                return

    async def update_booking_reminder_status(self, organization_id, booking_id,
                                             reminder_status, responded_at=None):
        for b in self._bookings:
            if b["organization_id"] == organization_id and str(b["id"]) == str(booking_id):
                b["reminder_status"] = reminder_status
                if responded_at:
                    b["reminder_responded_at"] = responded_at
                return deepcopy(b)
        return None

    # ── Booking settings ──────────────────────────────────────

    async def get_booking_settings(self, organization_id):
        return deepcopy(self._settings.get(str(organization_id))) if self._settings.get(str(organization_id)) else None

    async def upsert_booking_settings(self, organization_id, fasce_orarie=None,
                                      capienze_orarie=None):
        key = str(organization_id)
        current = deepcopy(self._settings.get(key)) or {
            "fasce_orarie": SLOT_ORE,
            "capienze_orarie": {f: 40 for f in SLOT_ORE},
            "config": {},
        }
        current["fasce_orarie"] = fasce_orarie or SLOT_ORE
        current["capienze_orarie"] = capienze_orarie or {f: 40 for f in SLOT_ORE}
        self._settings[key] = current
        return deepcopy(current)

    async def upsert_booking_settings_config(self, organization_id, config):
        if not isinstance(config, dict) or "catalog_v2" in config:
            raise ValueError("Use the protected catalogue endpoint")
        key = str(organization_id)
        current = deepcopy(self._settings.get(key)) or {
            "fasce_orarie": SLOT_ORE,
            "capienze_orarie": {f: 40 for f in SLOT_ORE},
            "config": {},
        }
        protected = (current.get("config") or {}).get("catalog_v2")
        has_protected = "catalog_v2" in (current.get("config") or {})
        current["config"] = deepcopy(config)
        if has_protected:
            current["config"]["catalog_v2"] = deepcopy(protected)
        self._settings[key] = current
        return deepcopy(current)
