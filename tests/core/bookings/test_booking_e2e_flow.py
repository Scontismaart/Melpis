"""Test E2E Flow Prenotazione Esterna (Task 5).

Verifica il flusso completo end-to-end:
Inbound WhatsApp Message -> ConversationOrchestrator -> BookingService -> BookingAdapterRouter -> SimplyBookAdapter
con ciclo completo Send-Then-Mark, verifica disponibilità, prenotazione e gestione fail-safe.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from httpx import Response

from src.core.bookings.adapters.simplybook_adapter import SimplyBookAdapter
from src.core.bookings.ports.base import (
    AvailabilityQuery,
    BookingResult,
    CreateBookingRequest,
    CustomerResult,
)
from src.core.bookings.router import BookingAdapterRouter, BookingMode
from src.core.bookings.service import BookingService
from src.core.receptionist.conversation_orchestrator import (
    ConversationOrchestrator,
    OrchestrationInput,
)
from src.models.schemas import DatiPrenotazione, ProfiloAttivita, RispostaOutput

ORG_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
MSG_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
LOGIN_URL = "https://user-api.simplybook.me/login"
PUBLIC_URL = "https://user-api.simplybook.me/"


@pytest.fixture
def mock_external_repo():
    repo = MagicMock()
    repo.get_credentials = AsyncMock(
        return_value={
            "organization_id": ORG_ID,
            "provider": "simplybook",
            "is_active": True,
            "company_login": "barber-sandbox",
            "api_key": "sb_test_api_key_123",
            "config": {
                "mode": "authoritative",
                "medical_dpa_signed": False,
                "timeout_seconds": 2.0,
            },
        }
    )
    repo.get_sync_record = AsyncMock(return_value=None)
    repo.get_sync_by_idempotency_key = AsyncMock(return_value=None)
    repo.record_sync_prepare = AsyncMock()
    repo.record_sync_success = AsyncMock()
    repo.record_sync_failure = AsyncMock()
    repo.claim_sync_slot = AsyncMock(
        return_value=({"sync_status": "pending"}, True)
    )
    return repo


@pytest.fixture
def mock_booking_repo():
    repo = MagicMock()
    repo.get_booking_for_message = AsyncMock(return_value=None)
    repo.mark_booking_requires_intervention = AsyncMock()
    repo.list_bookings = AsyncMock(return_value=[])
    repo.get_booking_settings = AsyncMock(
        return_value={"capienze_orarie": {"10:00": 10, "11:00": 10}}
    )
    repo.create_booking = AsyncMock(
        side_effect=lambda **kwargs: {
            "id": uuid.uuid4(),
            "organization_id": kwargs["organization_id"],
            "nome_cliente": kwargs["nome_cliente"],
            "telefono": kwargs["telefono"],
            "data": kwargs["data"],
            "ora": kwargs["ora"],
            "coperti": kwargs["coperti"],
            "note": kwargs["note"],
            "stato": "in_attesa",
            "richiede_intervento": kwargs["richiede_intervento"],
        }
    )
    return repo


@pytest.mark.asyncio
@respx.mock
async def test_full_pipeline_whatsapp_to_simplybook_success(
    mock_external_repo, mock_booking_repo, mock_provider_context_open,
):
    """Test E2E del flusso completo: messaggio utente -> AI -> BookingService -> Router -> SimplyBookAdapter."""
    # 1. Mock risposte wire SimplyBook JSON-RPC 2.0
    respx.post(LOGIN_URL).mock(
        return_value=Response(200, json={"jsonrpc": "2.0", "result": "test_token_xyz", "id": 1})
    )
    respx.post(PUBLIC_URL).mock(
        return_value=Response(
            200,
            json={
                "jsonrpc": "2.0",
                "result": {
                    "id": 999888,
                    "code": "SB-999888",
                    "bookings": [{"id": 999888}],
                },
                "id": 2,
            },
        )
    )

    # 2. Inizializzazione router e service
    router = BookingAdapterRouter(repo=mock_external_repo)
    service = BookingService(
        repo=mock_booking_repo,
        booking_router=router,
    )

    # 3. Setup Orchestrator con AI deterministica
    profilo = ProfiloAttivita(
        nome="Barberia Classica",
        tipo_attivita="Salone Parrucchiere / Barbiere",
        verticale="parrucchiere",
        tono="cordiale",
        orari="Lun-Sab 09:00-19:00",
    )

    fake_ai_output = RispostaOutput(
        risposta="Ho preso nota della tua prenotazione per il taglio alle 10:00 di sabato!",
        richiede_umano=False,
        motivo="prenotazione",
        categoria="parrucchiere",
        prenotazione=DatiPrenotazione(
            nome_cliente="Andrea Neri",
            data="2026-09-12",
            ora="10:00",
            coperti=1,
            note="Taglio barba e capelli",
        ),
    )

    orchestrator = ConversationOrchestrator(
        booking_service=service,
        billing_repo=None,
        doc_repo=None,
        conv_repo=None,
        org_repo=None,
    )

    with patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async", return_value=fake_ai_output), \
         patch("src.core.receptionist.conversation_orchestrator.classifica_intent") as mock_intent:

        mock_intent_res = MagicMock()
        mock_intent_res.intent = "prenotazione"
        mock_intent_res.confidence = 0.98
        mock_intent_res.source = "rule"
        mock_intent.return_value = mock_intent_res

        req = OrchestrationInput(
            organization_id=ORG_ID,
            message_id=MSG_ID,
            conversation_id="conv-e2e-1",
            text="Vorrei prenotare un taglio per sabato 12 settembre alle 10:00",
            sender_phone="+393471122334",
            sender_name="Andrea Neri",
            business_profile=profilo,
            is_simulation=False,
            auth_user_id=uuid.uuid4(),
        )

        out = await orchestrator.orchestrate(req)

        # 4. Asserzioni sul risultato dell'orchestrazione
        assert out.booking_created is not None
        assert out.booking_created["nome_cliente"] == "Andrea Neri"
        assert out.booking_created["external_sync_status"] == "synced"
        assert out.booking_created["external_booking_id"] == "999888"
        assert out.richiede_umano is False

        # 5. Verifica tracciamento Send-Then-Mark su DB (claim atomico)
        mock_external_repo.claim_sync_slot.assert_awaited_once()
        mock_external_repo.record_sync_success.assert_awaited_once_with(
            ORG_ID,
            f"ext-book:{ORG_ID}:{MSG_ID}",
            "999888",
        )


@pytest.mark.asyncio
@respx.mock
async def test_full_pipeline_external_failure_triggers_human_escalation(
    mock_external_repo, mock_booking_repo, mock_provider_context_open,
):
    """Test E2E fallimento esterno: se SimplyBook è irraggiungibile o rifiuta, scala all'operatore umano."""
    # 1. Mock SimplyBook login OK ma book() restituisce slot pieno
    respx.post(LOGIN_URL).mock(
        return_value=Response(200, json={"jsonrpc": "2.0", "result": "test_token_xyz", "id": 1})
    )
    respx.post(PUBLIC_URL).mock(
        return_value=Response(
            200,
            json={
                "jsonrpc": "2.0",
                "error": {"code": -32001, "message": "Time slot is not available"},
                "id": 2,
            },
        )
    )

    router = BookingAdapterRouter(repo=mock_external_repo)
    service = BookingService(
        repo=mock_booking_repo,
        booking_router=router,
    )

    profilo = ProfiloAttivita(
        nome="Barberia Classica",
        tipo_attivita="Salone Parrucchiere",
        verticale="parrucchiere",
        tono="cordiale",
        orari="Lun-Sab 09:00-19:00",
    )

    fake_ai_output = RispostaOutput(
        risposta="Verifico la disponibilità...",
        richiede_umano=False,
        motivo="prenotazione",
        categoria="parrucchiere",
        prenotazione=DatiPrenotazione(
            nome_cliente="Andrea Neri",
            data="2026-09-12",
            ora="10:00",
            coperti=1,
        ),
    )

    orchestrator = ConversationOrchestrator(
        booking_service=service,
        billing_repo=None,
        doc_repo=None,
        conv_repo=None,
        org_repo=None,
    )

    with patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async", return_value=fake_ai_output), \
         patch("src.core.receptionist.conversation_orchestrator.classifica_intent") as mock_intent:

        mock_intent_res = MagicMock()
        mock_intent_res.intent = "prenotazione"
        mock_intent_res.confidence = 0.98
        mock_intent_res.source = "rule"
        mock_intent.return_value = mock_intent_res

        req = OrchestrationInput(
            organization_id=ORG_ID,
            message_id=MSG_ID,
            conversation_id="conv-e2e-2",
            text="Prenotami alle 10:00",
            sender_phone="+393471122334",
            sender_name="Andrea Neri",
            business_profile=profilo,
            is_simulation=False,
            auth_user_id=uuid.uuid4(),
        )

        out = await orchestrator.orchestrate(req)

        # Invariante 11: Se il gestionale fallisce in authoritative mode,
        # richiede_intervento DEVE essere True sulla prenotazione locale
        assert out.booking_created is not None
        assert out.booking_created["external_sync_status"] == "failed"
        assert out.booking_created["richiede_intervento"] is True
        mock_external_repo.record_sync_failure.assert_awaited_once()
