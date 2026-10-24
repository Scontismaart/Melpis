"""Unit tests per ConversationOrchestrator.

Testa in isolamento (mock-first, esecuzione < 1s) tutti i rami di esecuzione:
- Fast-path match
- FAQ Cache hit
- Generazione LLM con RAG
- Guardrail pipeline
- Modalità Simulazione (is_simulation=True, nessuna mutazione DB)
- Modalità Reale (is_simulation=False, create_booking con slot_lock)
- Slot pieno con fasce alternative
- Accounting token e cost governance
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.core.bookings import SlotPienoError
from src.core.receptionist.conversation_orchestrator import (
    ConversationOrchestrator,
)
from src.core.receptionist.models import OrchestrationInput
from src.models.schemas import DatiPrenotazione, DisponibilitaSlot, RispostaOutput
from tests.provider_accounting_fakes import provider_context_stub


@pytest.fixture(autouse=True)
def _mock_embeddings():
    """Blocca il download del modello di embedding durante i test unitari."""
    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.guardrails.faq_cache.vettorizza", return_value=[[0.1] * 384]):
        yield


@pytest.fixture(autouse=True)
def _allow_rag_at_entitlement_boundary():
    """Keep orchestrator unit tests focused; entitlement policy has DB-backed tests."""
    with patch("src.core.documenti.access.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.core.documenti.rag_context.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.core.guardrails.faq_cache.rag_access", new_callable=AsyncMock, return_value=True):
        yield


@pytest.fixture(autouse=True)
def _mock_provider_accounting_context(monkeypatch):
    from src.core.provider_accounting import ProviderAccountingContext

    async def open_context(cls, pool, organization_id, *, task_type,
                           actor_auth_user_id=None, worker_message_id=None,
                           simulation_request_id=None, simulation_claim_token=None,
                           quota_operation=None, quota_ticket=None,
                           context=None):
        return provider_context_stub(
            task_type=task_type, organization_id=organization_id,
            quota_operation=quota_operation, quota_ticket=quota_ticket,
        )

    monkeypatch.setattr(ProviderAccountingContext, "open", classmethod(open_context))


@pytest.fixture
def mock_dependencies():
    org_repo = AsyncMock()
    doc_repo = AsyncMock()
    billing_repo = AsyncMock()
    conv_repo = AsyncMock()
    booking_svc = AsyncMock()

    org_repo.get_org_business_profile = AsyncMock(return_value={
        "nome": "Trattoria da Mario",
        "tipo_attivita": "ristorante",
        "tono": "cordiale",
        "orari": "12:00-15:00, 19:30-23:00",
    })
    billing_repo.get_org_subscription_state = AsyncMock(return_value={
        "subscription_status": "active",
        "messages_used_this_period": 10,
        "messages_limit": 1000,
    })
    billing_repo.record_usage = AsyncMock(return_value={"id": uuid.uuid4()})
    conv_repo.list_conversation_messages = AsyncMock(return_value=[])

    return {
        "org_repo": org_repo,
        "doc_repo": doc_repo,
        "billing_repo": billing_repo,
        "conv_repo": conv_repo,
        "booking_service": booking_svc,
        "booking_svc": booking_svc,
    }


@pytest.mark.asyncio
async def test_fast_path_hit(mock_dependencies):
    fast_matcher = AsyncMock(return_value="Siamo aperti dalle 12 alle 15 e dalle 19:30 alle 23.")
    orchestrator = ConversationOrchestrator(
        **mock_dependencies,
        fast_path_matcher=fast_matcher,
    )

    req = OrchestrationInput(
        organization_id=uuid.uuid4(),
        text="A che ora aprite?",
    )
    result = await orchestrator.orchestrate(req)

    assert result.source == "fast_path"
    assert result.response_text == "Siamo aperti dalle 12 alle 15 e dalle 19:30 alle 23."
    assert result.richiede_umano is False
    fast_matcher.assert_awaited_once()


@pytest.mark.asyncio
async def test_faq_cache_hit(mock_dependencies):
    orchestrator = ConversationOrchestrator(**mock_dependencies)
    org_id = uuid.uuid4()

    with patch("src.core.documenti.access.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.core.guardrails.faq_cache.cache_enabled", return_value=True), \
         patch("src.core.receptionist.conversation_orchestrator.classifica_intent") as mock_intent, \
         patch("src.core.guardrails.faq_cache.cerca_in_cache", return_value="Il nostro menu comprende opzioni senza glutine."):

        mock_intent_res = MagicMock()
        mock_intent_res.intent = "faq"
        mock_intent_res.source = "rule"
        mock_intent_res.confidence = 0.95
        mock_intent.return_value = mock_intent_res

        req = OrchestrationInput(
            organization_id=org_id,
            text="Avete opzioni senza glutine?",
        )
        result = await orchestrator.orchestrate(req)

        assert result.source == "faq_cache"
        assert result.response_text == "Il nostro menu comprende opzioni senza glutine."
        assert result.intent == "faq"
        mock_dependencies["billing_repo"].record_usage.assert_awaited_with(
            org_id, "cache_hit", metadata={
                "conversation_id": "",
                "message_id": "",
                "intent": "faq",
            }
        )


@pytest.mark.asyncio
async def test_standard_llm_generation_with_accounting(mock_dependencies):
    orchestrator = ConversationOrchestrator(**mock_dependencies)
    org_id = uuid.uuid4()

    fake_ai_output = RispostaOutput(
        risposta="Certamente, possiamo accogliervi!",
        richiede_umano=False,
        motivo="informazione",
        categoria="ristorante",
    )

    with patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async", return_value=fake_ai_output) as mock_responder, \
         patch("src.core.receptionist.conversation_orchestrator.classifica_intent") as mock_intent, \
         patch("src.core.receptionist.conversation_orchestrator.recupera_contesto_documenti") as mock_rag:

        mock_intent_res = MagicMock()
        mock_intent_res.intent = "prenotazione"
        mock_intent_res.source = "rule"
        mock_intent_res.confidence = 0.9
        mock_intent.return_value = mock_intent_res

        mock_rag_res = MagicMock()
        mock_rag_res.testo = "Menu speciale del giorno: pasta fresca"
        mock_rag_res.chunks = []
        mock_rag.return_value = mock_rag_res

        req = OrchestrationInput(
            organization_id=org_id,
            text="Vorrei venire stasera per cena",
        )
        result = await orchestrator.orchestrate(req)

        assert result.source == "llm"
        assert result.response_text == "Certamente, possiamo accogliervi!"
        assert result.richiede_umano is False

        assert mock_responder.await_args.kwargs["provider_context"].task_type == "customer_message"
        record_usage_calls = mock_dependencies["billing_repo"].record_usage.call_args_list
        ai_response_calls = [c for c in record_usage_calls if c[0][1] == "ai_response"]
        assert ai_response_calls == []


@pytest.mark.asyncio
async def test_simulation_mode_never_creates_booking_row(mock_dependencies):
    """Verifica che in modalità is_simulation=True la disponibilità sia verificata in sola lettura
    senza MAI chiamare create_booking né acquisire slot_lock (Invariante 5 e Punto 1)."""
    booking_svc = mock_dependencies["booking_service"]
    booking_svc.verifica_disponibilita = AsyncMock(return_value=DisponibilitaSlot(
        data="2026-09-10",
        ora="20:30",
        coperti_massimi=40,
        coperti_prenotati=10,
        coperti_liberi=30,
        stato="verde",
    ))
    booking_svc.create_booking = AsyncMock()

    orchestrator = ConversationOrchestrator(**mock_dependencies)
    org_id = uuid.uuid4()

    fake_ai_output = RispostaOutput(
        risposta="Perfetto! Tavolo per 4 persone il 10 settembre alle 20:30.",
        richiede_umano=False,
        motivo="prenotazione",
        categoria="ristorante",
        prenotazione=DatiPrenotazione(
            nome_cliente="Mario Rossi",
            data="2026-09-10",
            ora="20:30",
            coperti=4,
        ),
    )

    with patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async", return_value=fake_ai_output), \
         patch("src.core.receptionist.conversation_orchestrator.classifica_intent") as mock_intent:

        mock_intent_res = MagicMock()
        mock_intent_res.intent = "prenotazione"
        mock_intent_res.source = "rule"
        mock_intent.return_value = mock_intent_res

        req = OrchestrationInput(
            organization_id=org_id,
            text="Vorrei un tavolo per 4 il 10 settembre alle 20:30 a nome Mario Rossi",
            is_simulation=True,  # MODALITA' SIMULATORE
        )
        result = await orchestrator.orchestrate(req)

        # 1. Verifica sola lettura eseguita
        booking_svc.verifica_disponibilita.assert_awaited_once_with(
            org_id, "2026-09-10", "20:30", coperti=4
        )
        assert result.disponibilita_slot is not None
        assert result.disponibilita_slot["stato"] == "verde"

        # 2. ZERO SCRITTURE: create_booking NON deve mai essere chiamato!
        booking_svc.create_booking.assert_not_called()
        assert result.booking_created is None
        assert result.prenotazione.coperti == 4


@pytest.mark.asyncio
async def test_real_mode_does_not_book_before_a04_commit(mock_dependencies):
    """L'orchestratore non crea prenotazioni prima del commit A04."""
    booking_svc = mock_dependencies["booking_service"]
    booking_svc.create_booking = AsyncMock(return_value={"id": str(uuid.uuid4()), "stato": "confermata"})

    orchestrator = ConversationOrchestrator(**mock_dependencies)
    org_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    fake_ai_output = RispostaOutput(
        risposta="Prenotazione confermata per 2 persone alle 20:00!",
        richiede_umano=False,
        motivo="prenotazione",
        categoria="ristorante",
        prenotazione=DatiPrenotazione(
            nome_cliente="Luigi",
            data="2026-09-10",
            ora="20:00",
            coperti=2,
        ),
    )

    with patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async", return_value=fake_ai_output), \
         patch("src.core.receptionist.conversation_orchestrator.classifica_intent") as mock_intent:

        mock_intent_res = MagicMock()
        mock_intent_res.intent = "prenotazione"
        mock_intent_res.source = "rule"
        mock_intent.return_value = mock_intent_res

        req = OrchestrationInput(
            organization_id=org_id,
            message_id=msg_id,
            text="Tavolo per 2 alle 20",
            sender_phone="+39333111222",
            is_simulation=False,  # CANALE REALE
        )
        result = await orchestrator.orchestrate(req)

        booking_svc.create_booking.assert_not_awaited()
        assert result.booking_created is None
        assert result.richiede_umano is True


@pytest.mark.asyncio
async def test_orchestrator_does_not_probe_slot_by_writing(mock_dependencies):
    """Le alternative richiedono una lettura autorizzata, non un tentativo di INSERT."""
    booking_svc = mock_dependencies["booking_service"]
    booking_svc.create_booking = AsyncMock(side_effect=SlotPienoError("Slot esaurito", alternative=["19:30", "21:30"]))

    orchestrator = ConversationOrchestrator(**mock_dependencies)
    org_id = uuid.uuid4()

    fake_ai_output = RispostaOutput(
        risposta="Controllo per il tavolo.",
        richiede_umano=False,
        motivo="prenotazione",
        categoria="ristorante",
        prenotazione=DatiPrenotazione(
            nome_cliente="Anna",
            data="2026-09-10",
            ora="20:30",
            coperti=6,
        ),
    )

    with patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async", return_value=fake_ai_output), \
         patch("src.core.receptionist.conversation_orchestrator.classifica_intent") as mock_intent:

        mock_intent_res = MagicMock()
        mock_intent_res.intent = "prenotazione"
        mock_intent_res.source = "rule"
        mock_intent.return_value = mock_intent_res

        req = OrchestrationInput(
            organization_id=org_id,
            text="Vorrei prenotare per 6 alle 20:30",
            message_id=uuid.uuid4(),
            is_simulation=False,
        )
        result = await orchestrator.orchestrate(req)

        booking_svc.create_booking.assert_not_awaited()
        assert result.slot_full_alternatives == []
        assert result.richiede_umano is True


@pytest.mark.asyncio
async def test_guardrail_block_modifies_response_and_logs(mock_dependencies):
    """Verifica che un'allucinazione di prezzo sia intercettata e neutralizzata dal guardrail."""
    orchestrator = ConversationOrchestrator(**mock_dependencies)
    org_id = uuid.uuid4()

    fake_ai_output = RispostaOutput(
        risposta="Il menu degustazione costa solo 15 euro a persona.",
        richiede_umano=False,
        motivo="informazione",
        categoria="ristorante",
    )

    with patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async", return_value=fake_ai_output), \
         patch("src.core.receptionist.conversation_orchestrator.classifica_intent") as mock_intent, \
         patch("src.core.receptionist.conversation_orchestrator.recupera_contesto_documenti") as mock_rag:

        mock_intent_res = MagicMock()
        mock_intent_res.intent = "informazione"
        mock_intent_res.source = "rule"
        mock_intent.return_value = mock_intent_res

        mock_rag_res = MagicMock()
        mock_rag_res.testo = ""
        mock_rag_res.chunks = []
        mock_rag.return_value = mock_rag_res

        req = OrchestrationInput(
            organization_id=org_id,
            text="Quanto costa il menu degustazione?",
        )
        result = await orchestrator.orchestrate(req)

        # Il guardrail deve intercettare il prezzo non verificato nei chunk RAG
        assert result.guardrail_action == "block"
        assert result.richiede_umano is True

        # Verifica chiamata a record_usage per guardrail_block
        calls = mock_dependencies["billing_repo"].record_usage.call_args_list
        gb_calls = [c for c in calls if len(c[0]) > 1 and c[0][1] == "guardrail_block"]
        assert len(gb_calls) == 1
        meta = gb_calls[0][1]["metadata"]
        assert meta["motivo"] == "prezzo_non_verificato"
        assert "prezzo_non_verificato" in meta["violazioni"]
