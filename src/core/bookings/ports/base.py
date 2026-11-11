"""Modello Canonico e Protocollo BookingSystemPort (Task 1).

Definisce i contratti universali tipizzati (Pydantic v2) e l'interfaccia astratta
asincrona BookingSystemPort per l'integrazione con sistemi di prenotazione interni ed esterni.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any, Literal, Protocol, runtime_checkable
from pydantic import BaseModel, Field


# ── Risorse e Servizi ────────────────────────────────────────

class ServiceItem(BaseModel):
    """Servizio o prestazione prenotabile (taglio capelli, visita medica, tavolo, camera)."""
    service_id: str
    nome: str
    descrizione: str = ""
    durata_minuti: int = 60
    prezzo_cent: int | None = None
    valuta: str = "EUR"
    categoria: str = "generale"


class TimeRange(BaseModel):
    """Intervallo orario di apertura."""
    inizio: time
    fine: time


class DaySchedule(BaseModel):
    """Orario settimanale per un singolo giorno (0=Lunedì, 6=Domenica)."""
    giorno_settimana: int
    aperto: bool = True
    fasce: list[TimeRange] = Field(default_factory=list)


class OpeningHoursResult(BaseModel):
    """Orari complessivi di apertura e giorni di chiusura."""
    orari: list[DaySchedule]
    festivi_chiusi: list[date] = Field(default_factory=list)
    timezone: str = "Europe/Rome"


# ── Anagrafica Cliente ───────────────────────────────────────

class CustomerQuery(BaseModel):
    """Criteri di ricerca per cliente."""
    telefono: str | None = None
    email: str | None = None
    customer_id: str | None = None


class CustomerResult(BaseModel):
    """Anagrafica cliente unificata."""
    customer_id: str
    nome: str
    cognome: str = ""
    telefono: str
    email: str | None = None
    note: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """Restituisce il nome completo se disponibile, altrimenti il fallback cortese 'Gentile ospite'."""
        full = f"{self.nome} {self.cognome}".strip()
        return full if full else "Gentile ospite"


# Alias per comodità
BookingCustomer = CustomerResult


# ── Disponibilità e Slot ─────────────────────────────────────

class AvailabilityQuery(BaseModel):
    """Interrogazione di disponibilità per data, servizio e quantita."""
    data_inizio: date
    data_fine: date
    service_id: str | None = None
    coperti_o_quantita: int = 1
    operatore_id: str | None = None
    # Estensioni per settore Ospitalità / Hospitality (ZaK PMS)
    adulti: int = 1
    bambini: int = 0
    eta_bambini: list[int] = Field(default_factory=list)
    board_type: str | None = Field(default=None, description="Regime di trattamento: RO, BB, HB, FB, AI")


class SlotAvailability(BaseModel):
    """Singola fascia oraria con esito di disponibilità."""
    data: date
    ora_inizio: time
    ora_fine: time
    disponibile: bool
    capacita_residua: int = 0
    operatore_id: str | None = None
    service_id: str | None = None
    prezzo_cent: int | None = Field(default=None, description="Tariffa dinamica in centesimi per il periodo/trattamento")
    board_type: str | None = None


class AvailabilityResult(BaseModel):
    """Esito complessivo della verifica disponibilità."""
    success: bool
    slots: list[SlotAvailability] = Field(default_factory=list)
    alternative_consigliate: list[str] = Field(default_factory=list)
    error_message: str | None = None


# ── Mutazioni di Prenotazione ────────────────────────────────

class CreateBookingRequest(BaseModel):
    """Richiesta di creazione appuntamento con chiave di idempotenza obbligatoria."""
    idempotency_key: str = Field(description="Chiave deterministica generata a monte")
    customer: CustomerResult
    data: date
    ora_inizio: time
    durata_minuti: int = 60
    data_fine: date | None = Field(default=None, description="Data di fine/check-out per soggiorni multi-giorno (Hotel/Hospitality)")
    service_id: str | None = None
    coperti: int = 1
    note: str = ""
    origine: str = "WhatsApp"
    source_message_id: str | None = None
    internal_booking_id: uuid.UUID | None = None
    # Estensioni per settore Ospitalità / Hospitality (ZaK PMS)
    adulti: int = 1
    bambini: int = 0
    eta_bambini: list[int] = Field(default_factory=list)
    board_type: str | None = Field(default=None, description="Regime di trattamento: RO, BB, HB, FB, AI")


class UpdateBookingRequest(BaseModel):
    """Richiesta di modifica appuntamento."""
    idempotency_key: str
    external_booking_id: str
    nuova_data: date | None = None
    nuova_data_fine: date | None = None
    nuova_ora_inizio: time | None = None
    nuovo_service_id: str | None = None
    nuovi_coperti: int | None = None
    nuove_note: str | None = None


class CancelBookingRequest(BaseModel):
    """Richiesta di cancellazione appuntamento."""
    idempotency_key: str
    external_booking_id: str
    motivo: str = ""


class BookingResult(BaseModel):
    """Risultato standardizzato di una mutazione di prenotazione."""
    success: bool
    external_booking_id: str | None = None
    stato: Literal["confermata", "in_attesa", "rifiutata", "cancellata", "errore"]
    data: date | None = None
    ora_inizio: time | None = None
    dettagli: dict[str, Any] = Field(default_factory=dict)
    sync_status: Literal["synced", "pending_retry", "failed", "uncertain", "local_only", "skipped_shadow"] = "synced"
    error_code: str | None = None
    error_message: str | None = None


# ── Protocollo della Porta Astratta ──────────────────────────

@runtime_checkable
class BookingSystemPort(Protocol):
    """Porta canonica standard per l'interazione con qualsiasi sistema di prenotazione."""

    async def get_opening_hours(self, org_id: uuid.UUID | str) -> OpeningHoursResult:
        """Restituisce orari di apertura settimanali e giorni di chiusura."""
        ...

    async def get_services(self, org_id: uuid.UUID | str) -> list[ServiceItem]:
        """Elenca i servizi o prestazioni configurati sul gestionale."""
        ...

    async def get_customer(self, org_id: uuid.UUID | str, query: CustomerQuery) -> CustomerResult | None:
        """Recupera l'anagrafica cliente (ricerca per telefono, email o ID)."""
        ...

    async def get_availability(self, org_id: uuid.UUID | str, query: AvailabilityQuery) -> AvailabilityResult:
        """Verifica la disponibilità reale di slot in un intervallo temporale."""
        ...

    async def create_booking(self, org_id: uuid.UUID | str, req: CreateBookingRequest) -> BookingResult:
        """Crea un appuntamento sul gestionale garantendo l'idempotenza."""
        ...

    async def update_booking(self, org_id: uuid.UUID | str, req: UpdateBookingRequest) -> BookingResult:
        """Modifica data, orario o parametri di un appuntamento esistente."""
        ...

    async def cancel_booking(self, org_id: uuid.UUID | str, req: CancelBookingRequest) -> BookingResult:
        """Annulla una prenotazione sul gestionale."""
        ...
