"""Data Transfer Objects per l'Application Layer Core AI Receptionist.

Definisce i contratti formali per l'invocazione di ConversationOrchestrator,
garantendo isolamento tra il trasporto (WhatsApp, Instagram, Webhook) e
il motore decisionale AI.
"""
from __future__ import annotations

import uuid
from typing import Any
from pydantic import BaseModel, Field, PrivateAttr

from src.models.schemas import DatiPrenotazione, ProfiloAttivita


class OrchestrationInput(BaseModel):
    """Input agnostico rispetto al canale per l'AI Receptionist."""
    organization_id: uuid.UUID | str | None = None
    # Set by authenticated dashboard routes; never accepted from request JSON.
    auth_user_id: uuid.UUID | str | None = Field(default=None, exclude=True)
    trace_id: str | None = Field(default=None, exclude=True)
    # Server-issued simulator quota claim. Excluded from all transport payloads.
    simulation_request_id: uuid.UUID | str | None = Field(default=None, exclude=True, repr=False)
    simulation_claim_token: uuid.UUID | str | None = Field(default=None, exclude=True, repr=False)
    # Internal A04 capabilities; never accepted from transport JSON or serialized.
    quota_operation: Any = Field(default=None, exclude=True, repr=False)
    quota_ticket: Any = Field(default=None, exclude=True, repr=False)
    message_id: uuid.UUID | str | None = None
    conversation_id: str = ""
    text: str
    channel: str = "whatsapp"
    sender_phone: str = ""
    sender_name: str = ""

    # Profilo facoltativo: se omesso viene caricato dal DB dell'organizzazione
    business_profile: ProfiloAttivita | dict | None = None

    # Cronologia multi-turn opzionale: se omessa viene letta dal conversation_repo
    cronologia: list[tuple[str, str]] | None = None

    # Snapshot billing opzionale (per routing budget-aware)
    billing_state: dict | None = None

    # Modalità Simulazione / Demo (Invariante 5):
    # Se True, esegue verifica di disponibilità in sola lettura senza MAI creare
    # righe in DB o acquisire lock di slot.
    is_simulation: bool = False

    # Se True e organization_id è valido, registra l'evento in usage_events (Invariante 8)
    record_billing_usage: bool = True


class OrchestrationOutput(BaseModel):
    """Risultato unificato e validato dell'orchestrazione AI Receptionist."""
    _quota_final: Any = PrivateAttr(default=None)
    response_text: str
    richiede_umano: bool = False
    motivo_richiesta_umano: str | None = None
    intent: str | None = None
    intent_confidence: float | None = None
    source: str = "llm"  # "fast_path", "faq_cache", "llm", "booking_exists", "guardrail_block"

    # Dati di prenotazione estratti e validati
    prenotazione: DatiPrenotazione | None = None

    # Record creato nel database (solo se is_simulation=False e prenotazione confermata)
    booking_created: dict | None = None

    # Dettaglio disponibilità slot (esito verifica capienza)
    disponibilita_slot: dict | None = None

    # Fasce alternative proposte in caso di slot pieno
    slot_full_alternatives: list[str] = Field(default_factory=list)

    # Esito guardrail
    guardrail_action: str = "none"  # "none", "warn", "block", "modify"
    guardrail_motivo: str | None = None

    # Metriche token & costo (Invariante 8)
    usage_metrics: dict = Field(default_factory=dict)
