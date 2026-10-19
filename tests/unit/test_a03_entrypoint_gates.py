from datetime import datetime
from inspect import signature
from unittest.mock import AsyncMock, patch

import pytest

from src.core.provider_accounting import ProviderAccountingError
from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
from src.core.receptionist.models import OrchestrationInput
from tests.provider_accounting_fakes import provider_context_stub


@pytest.mark.asyncio
async def test_anonymous_simulator_has_deterministic_response_without_provider():
    orchestrator = ConversationOrchestrator()
    with patch(
        "src.core.receptionist.conversation_orchestrator.genera_risposta_async",
        new_callable=AsyncMock,
    ) as provider_runner:
        output = await orchestrator.orchestrate(OrchestrationInput(
            text="domanda anonima", organization_id=None, record_billing_usage=False,
            is_simulation=True,
        ))
    provider_runner.assert_not_awaited()
    assert output.source == "manual_gate"
    assert "Accedi" in output.response_text


@pytest.mark.asyncio
async def test_responder_runner_requires_context_even_when_legacy_accounting_flag_is_off():
    from src.core.crew_runner import genera_risposta_async
    from src.models.schemas import MessaggioInput, ProfiloAttivita

    with patch("src.core.crew_runner.crea_crew") as build_crew:
        with pytest.raises(ProviderAccountingError):
            await genera_risposta_async(
                MessaggioInput(testo="test"),
                ProfiloAttivita(nome="A", tipo_attivita="test", tono="test", orari=""),
                billing={"subscription_status": "active", "messages_limit": 150,
                         "messages_used_this_period": 0},
                provider_context=None,
            )
    build_crew.assert_not_called()


def test_review_runner_refuses_to_construct_crew_without_context():
    from src.core.crew_runner_review import genera_risposta_recensione

    with patch("src.core.crew_runner_review.crea_review_crew") as build_crew:
        with pytest.raises(ProviderAccountingError):
            genera_risposta_recensione("recensione", provider_context=None)
    build_crew.assert_not_called()


def test_report_scheduler_uses_deterministic_summary_without_tenant_context():
    from src.core.crew_runner_report import genera_report
    from src.models.schemas import EventoDashboard
    from src.core.priorita import Priorita

    events = [
        EventoDashboard(
            id=f"e{i}", tipo_evento="messaggio", timestamp=datetime.now(),
            priorita=Priorita.MEDIA, testo_originale="test", risposta_ai="test",
            gestito_da_ai=True, dettagli={},
        )
        for i in range(4)
    ]
    with patch("src.core.crew_runner_report.crea_report_crew") as build_crew:
        report = genera_report(events)
    build_crew.assert_not_called()
    assert "Riepilogo automatico" in report.analisi_testuale


@pytest.mark.asyncio
async def test_document_qa_without_authenticated_context_never_calls_llm():
    from src.core.documenti.qa_agent import rispondi

    repo = AsyncMock()
    repo.pool = None
    repo.search_similar.return_value = [{
        "content": "Fonte test", "document_name": "manuale.pdf",
        "metadata": {"fonte": "manuale.pdf"}, "distance": 0.1,
    }]
    with patch("src.core.documenti.qa_agent.vettorizza", return_value=[[0.0] * 384]), \
         patch("src.core.documenti.qa_agent.crea_llm") as make_llm:
        result = await rispondi(
            "00000000-0000-0000-0000-000000000001", "domanda", repo,
            actor_auth_user_id=None,
        )
    make_llm.assert_not_called()
    assert "Non ho potuto analizzare" in result["risposta"]


@pytest.mark.asyncio
async def test_intent_llm_is_opt_in_and_context_bound(monkeypatch):
    from src.core.guardrails.intent_classifier import classifica_intent

    monkeypatch.setenv("GUARDRAIL_INTENT_LLM_ENABLED", "true")
    with patch("src.core.guardrails.intent_classifier.crea_llm") as make_llm:
        result = await classifica_intent("testo ambiguo che richiede classificazione")
    make_llm.assert_not_called()
    assert result.source == "heuristic"


def test_provider_factory_rejects_context_for_another_task(monkeypatch):
    from src.core.llm_config import LLMRouteRequest, crea_llm

    monkeypatch.setenv("LLM_COST_POLICY", "standard")
    context = provider_context_stub(task_type="review")
    with pytest.raises(ProviderAccountingError):
        crea_llm(
            model="groq/llama-3.1-8b-instant",
            route_request=LLMRouteRequest(task_type="document_qa"),
            provider_context=context,
        )


def test_observed_adapter_matches_installed_crewai_base_llm_contract():
    from crewai import BaseLLM
    from src.core.observed_llm import ObservedChatLLM

    for method_name in ("call", "acall"):
        assert tuple(signature(getattr(ObservedChatLLM, method_name)).parameters) == tuple(
            signature(getattr(BaseLLM, method_name)).parameters
        )


def test_onboarding_context_rejects_another_tenant_before_retrieval():
    from src.core.provider_accounting import ProviderAccountingError

    context = provider_context_stub(
        task_type="onboarding_preview",
        organization_id="00000000-0000-0000-0000-000000000001",
    )
    with pytest.raises(ProviderAccountingError):
        context.assert_scope(
            "00000000-0000-0000-0000-000000000002", {"onboarding_preview"},
        )
