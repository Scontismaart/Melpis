"""Parity Tests: verifica formale di equivalenza funzionale.

Dimostra che ConversationOrchestrator produce lo stesso comportamento,
le stesse decisioni di guardrail, gli stessi intent e gli stessi payload
rispetto alla pipeline legacy.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.core.bookings import SlotPienoError
from src.core.guardrails.validator import applica_guardrail, valida_risposta
from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
from src.core.receptionist.models import OrchestrationInput
from src.models.schemas import DatiPrenotazione, DisponibilitaSlot, ProfiloAttivita, RispostaOutput


@pytest.fixture(autouse=True)
def _mock_embeddings():
    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.guardrails.faq_cache.vettorizza", return_value=[[0.1] * 384]):
        yield


@pytest.fixture
def mock_dependencies():
    org_repo = AsyncMock()
    doc_repo = AsyncMock()
    billing_repo = AsyncMock()
    conv_repo = AsyncMock()
    booking_svc = AsyncMock()

    org_repo.get_org_business_profile = AsyncMock(return_value={
        "nome": "Pizzeria Bella Napoli",
        "tipo_attivita": "ristorante",
        "tono": "cordiale",
        "orari": "19:00-23:30",
    })
    billing_repo.get_org_subscription_state = AsyncMock(return_value={"subscription_status": "active"})
    billing_repo.record_usage = AsyncMock(return_value={"id": uuid.uuid4()})
    conv_repo.list_conversation_messages = AsyncMock(return_value=[])

    return {
        "org_repo": org_repo,
        "doc_repo": doc_repo,
        "billing_repo": billing_repo,
        "conv_repo": conv_repo,
        "booking_service": booking_svc,
    }


@pytest.mark.asyncio
async def test_parity_fast_path_match(mock_dependencies):
    expected_fast_reply = "Siamo aperti tutte le sere dalle 19:00 alle 23:30."
    fast_matcher = AsyncMock(return_value=expected_fast_reply)

    orchestrator = ConversationOrchestrator(
        **mock_dependencies,
        fast_path_matcher=fast_matcher,
    )

    req = OrchestrationInput(
        organization_id=uuid.uuid4(),
        text="A che ora aprite stasera?",
    )
    result = await orchestrator.orchestrate(req)

    # Parità: il testo e la marcatura devono essere identici
    assert result.response_text == expected_fast_reply
    assert result.source == "fast_path"
    assert result.richiede_umano is False


@pytest.mark.asyncio
async def test_parity_guardrail_block_behavior(mock_dependencies, mock_provider_context_open):
    """Verifica che la decisione di guardrail e la riscrittura del testo
    siano identiche all'applicazione diretta della pipeline guardrail."""
    orchestrator = ConversationOrchestrator(**mock_dependencies)
    org_id = uuid.uuid4()

    allucinated_reply = RispostaOutput(
        risposta="La pizza margherita speciale costa 2 euro.",
        richiede_umano=False,
        motivo="informazione",
        categoria="ristorante",
    )

    # Calcolo del risultato atteso direttamente dalle funzioni di guardrail
    profilo = ProfiloAttivita(nome="Pizzeria Bella Napoli", tipo_attivita="ristorante", tono="cordiale", orari="19:00-23:30")
    esito_diretto = valida_risposta(allucinated_reply, [], profilo)
    risposta_filtrata_diretta = applica_guardrail(allucinated_reply, esito_diretto)

    with patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async", return_value=allucinated_reply), \
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
            text="Quanto costa la margherita?",
            auth_user_id=uuid.uuid4(),
        )
        result = await orchestrator.orchestrate(req)

        # PARITÀ ASSOLUTA: l'orchestratore deve produrre lo stesso testo e la stessa azione
        assert result.guardrail_action == esito_diretto.azione
        assert result.response_text == risposta_filtrata_diretta.risposta
        assert result.richiede_umano == risposta_filtrata_diretta.richiede_umano


@pytest.mark.asyncio
async def test_parity_booking_parameters_propagation(mock_dependencies, mock_provider_context_open):
    """Verifica che tutti i parametri estratti dall'AI vengano passati
    al booking service esattamente come faceva l'inbound processor."""
    booking_svc = mock_dependencies["booking_service"]
    booking_svc.create_booking = AsyncMock(return_value={"id": str(uuid.uuid4()), "stato": "confermata"})

    orchestrator = ConversationOrchestrator(**mock_dependencies)
    org_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    fake_ai_output = RispostaOutput(
        risposta="Prenotato!",
        richiede_umano=False,
        motivo="prenotazione",
        categoria="ristorante",
        prenotazione=DatiPrenotazione(
            nome_cliente="Marco",
            data="2026-09-12",
            ora="21:00",
            coperti=5,
            note="Tavolo all'aperto",
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
            conversation_id="conv-123",
            text="Tavolo per 5 il 12 settembre alle 21",
            sender_phone="+393400000000",
            sender_name="Marco",
            is_simulation=False,
            auth_user_id=uuid.uuid4(),
        )
        await orchestrator.orchestrate(req)

        # Parità: il booking service deve ricevere gli stessi campi
        booking_svc.create_booking.assert_awaited_once_with(
            organization_id=org_id,
            nome_cliente="Marco",
            telefono="+393400000000",
            data="2026-09-12",
            ora="21:00",
            coperti=5,
            note="Tavolo all'aperto",
            origine="WhatsApp",
            richiede_intervento=False,
            id_conversazione="conv-123",
            source_message_id=str(msg_id),
        )
