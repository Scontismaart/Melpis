"""P1.4 Fase A: cablaggio Airtable AI tools (DB-free, NO delete).

Copre: thread-through tools Agent/Crew/runner, gate intent+budget+connessione,
esclusione categorica del delete, usage attribution, guardrail invariata,
tenant binding dei tool.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
from src.core.receptionist.models import OrchestrationInput
from src.core.documenti.rag_context import ContestoDocumenti
from src.integrations.airtable.wiring import (
    AI_TOOL_INTENTS,
    FASE_A_EXCLUDED_TOOLS,
    build_airtable_tools_for_org,
    select_airtable_tools,
)
from src.models.schemas import ProfiloAttivita, RispostaOutput
from tests.provider_accounting_fakes import provider_context_stub

ORG = uuid.uuid4()


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch, tmp_path):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("MISTRAL_API_KEY", "mock-mistral-key-for-test")
    monkeypatch.setenv("GROQ_API_KEY", "mock-groq-free-key-for-test")
    monkeypatch.setenv("GROQ_FREE_ACCOUNT_CONFIRMED", "true")
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.setenv("CREWAI_STORAGE_DIR", str(tmp_path / "crewai"))
    try:
        from contextlib import nullcontext
        import crewai_core.lock_store
        crewai_core.lock_store.set_lock_backend(lambda *a, **kw: nullcontext())
    except Exception:
        pass
    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.guardrails.faq_cache.vettorizza", return_value=[[0.1] * 384]):
        yield



def _tool(name):
    t = MagicMock()
    t.name = name
    return t


class TestToolThreading:
    def _profilo(self):
        from src.models.schemas import ProfiloAttivita
        return ProfiloAttivita(nome="Test", tipo_attivita="ristorante",
                               tono="cordiale", orari="12-15")

    def test_crea_crew_rejects_injected_crm_tools(self):
        from src.agents.responder_agent import crea_crew
        from src.integrations.airtable.ai_service import AirtableAIService
        from src.integrations.airtable.ai_tools import FindCustomerTool

        tools = [FindCustomerTool(service=AirtableAIService(),
                                  organization_id=ORG)]
        profilo = self._profilo()
        from src.models.schemas import MessaggioInput
        crew = crea_crew(
            profilo, MessaggioInput(testo="ciao", canale="whatsapp"),
            tools=tools, provider_context=provider_context_stub(organization_id=ORG))
        assert not crew.agents[0].tools

    def test_crea_crew_default_no_tools(self):
        from src.agents.responder_agent import crea_crew
        from src.models.schemas import MessaggioInput

        profilo = self._profilo()
        crew = crea_crew(
            profilo, MessaggioInput(testo="ciao", canale="whatsapp"),
            provider_context=provider_context_stub(organization_id=ORG),
        )
        assert not getattr(crew.agents[0], "tools", None)


class TestSelectGate:
    @pytest.mark.asyncio
    async def test_non_crm_intent_no_tools_factory_not_called(self):
        factory = AsyncMock()
        for intent in ("faq", "chitchat", "out_of_scope", None):
            assert await select_airtable_tools(
                intent=intent, budget_ratio=0.9,
                organization_id=ORG, factory=factory) == []
        factory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_low_budget_kill_switch(self):
        factory = AsyncMock(return_value=[_tool("airtable_find_customer")])
        for intent in ("booking", "complaint"):
            assert await select_airtable_tools(
                intent=intent, budget_ratio=0.05,
                organization_id=ORG, factory=factory) == []
        factory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_factory_or_org_no_tools(self):
        assert await select_airtable_tools(
            intent="booking", budget_ratio=0.9,
            organization_id=ORG, factory=None) == []
        factory = AsyncMock()
        assert await select_airtable_tools(
            intent="booking", budget_ratio=0.9,
            organization_id=None, factory=factory) == []
        factory.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_factory_error_fail_closed(self):
        factory = AsyncMock(side_effect=RuntimeError("db down"))
        assert await select_airtable_tools(
            intent="booking", budget_ratio=0.9,
            organization_id=ORG, factory=factory) == []

    @pytest.mark.asyncio
    async def test_delete_always_filtered(self):
        factory = AsyncMock(return_value=[
            _tool("airtable_find_customer"),
            _tool("airtable_delete_record"),
            _tool("airtable_create_customer"),
        ])
        tools = await select_airtable_tools(
            intent="complaint", budget_ratio=0.9,
            organization_id=ORG, factory=factory)
        names = [t.name for t in tools]
        assert "airtable_delete_record" not in names
        assert names == []
        factory.assert_not_awaited()
        assert "airtable_delete_record" in FASE_A_EXCLUDED_TOOLS


class TestBuildForOrg:
    @pytest.mark.asyncio
    async def test_no_active_connection_no_tools(self):
        conn_repo = MagicMock()
        conn_repo.get_default_active_connection = AsyncMock(return_value=None)
        tools = await build_airtable_tools_for_org(
            ORG, conn_repo, MagicMock(), MagicMock())
        assert tools == []

    @pytest.mark.asyncio
    async def test_active_connection_cannot_enable_crm_ai(self):
        conn_repo = MagicMock()
        conn_repo.get_default_active_connection = AsyncMock(return_value={
            "base_id": "appTest123", "is_active": True})
        tools = await build_airtable_tools_for_org(
            ORG, conn_repo, MagicMock(), MagicMock())
        names = [t.name for t in tools]
        assert names == []
        conn_repo.get_default_active_connection.assert_not_awaited()
        assert "airtable_delete_record" not in names
        # Tenant binding: ogni tool vincolato all'org, mai nello schema LLM
        for t in tools:
            assert str(t.organization_id) == str(ORG)
            schema_fields = set(t.args_schema.model_fields)
            assert "organization_id" not in schema_fields
            assert "base_id" not in schema_fields


def _mock_dependencies(**overrides):
    org_repo = AsyncMock()
    org_repo.get_org_business_profile = AsyncMock(return_value={
        "nome": "Trattoria", "tipo_attivita": "ristorante", "verticale": "ristorante"})
    billing_repo = AsyncMock()
    billing_repo.get_org_subscription_state = AsyncMock(return_value={
        "subscription_status": "active", "messages_used_this_period": 10,
        "messages_limit": 1000})
    billing_repo.record_usage = AsyncMock(return_value={"id": uuid.uuid4()})
    deps = {"org_repo": org_repo, "doc_repo": AsyncMock(),
            "billing_repo": billing_repo, "conv_repo": AsyncMock(),
            "booking_service": AsyncMock()}
    deps.update(overrides)
    return deps


def _intent_patch(intent):
    mock_intent_res = MagicMock()
    mock_intent_res.intent = intent
    mock_intent_res.source = "rule"
    mock_intent_res.confidence = 0.95
    return patch(
        "src.core.receptionist.conversation_orchestrator.classifica_intent",
        return_value=mock_intent_res)


class TestOrchestrateWiring:
    @pytest.mark.asyncio
    async def test_booking_intent_cannot_receive_crm_tools(self, mock_provider_context_open):
        deps = _mock_dependencies()
        factory = AsyncMock(return_value=[_tool("airtable_find_customer")])
        orchestrator = ConversationOrchestrator(
            **deps, airtable_tool_factory=factory)
        fake_out = RispostaOutput(risposta="Trovato!", richiede_umano=False,
                                  motivo="x", categoria="ristorante")
        with _intent_patch("booking"), \
             patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async",
                   new_callable=AsyncMock) as mock_gen, \
             patch("src.core.receptionist.conversation_orchestrator.recupera_contesto_documenti") as mock_rag:
            mock_rag_res = MagicMock()
            mock_rag_res.testo = ""
            mock_rag_res.chunks = []
            mock_rag.return_value = mock_rag_res
            mock_gen.return_value = fake_out
            req = OrchestrationInput(
                organization_id=ORG, auth_user_id=uuid.uuid4(),
                text="cercami il cliente Rossi",
            )
            result = await orchestrator.orchestrate(req)

        factory.assert_not_awaited()
        assert mock_gen.call_args[1]["tools"] is None
        assert result.response_text == "Trovato!"
        # Usage attribution per offerta tool
        calls = deps["billing_repo"].record_usage.call_args_list
        assert not any(c[0][1] == "airtable_ai_tools" for c in calls)

    @pytest.mark.asyncio
    async def test_faq_intent_no_tools(self, mock_provider_context_open):
        deps = _mock_dependencies()
        factory = AsyncMock()
        orchestrator = ConversationOrchestrator(
            **deps, airtable_tool_factory=factory)
        fake_out = RispostaOutput(risposta="Orari 12-15", richiede_umano=False,
                                  motivo="x", categoria="ristorante")
        with _intent_patch("faq"), \
             patch("src.core.guardrails.faq_cache.cache_enabled", return_value=False), \
             patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async",
                   new_callable=AsyncMock) as mock_gen, \
             patch("src.core.receptionist.conversation_orchestrator.recupera_contesto_documenti") as mock_rag:
            mock_rag_res = MagicMock()
            mock_rag_res.testo = ""
            mock_rag_res.chunks = []
            mock_rag.return_value = mock_rag_res
            mock_gen.return_value = fake_out
            await orchestrator.orchestrate(
                OrchestrationInput(
                    organization_id=ORG, auth_user_id=uuid.uuid4(),
                    text="a che ora aprite?"))

        factory.assert_not_awaited()
        assert mock_gen.call_args[1]["tools"] is None

    @pytest.mark.asyncio
    async def test_no_factory_no_tools(self, mock_provider_context_open):
        deps = _mock_dependencies()
        orchestrator = ConversationOrchestrator(**deps)
        fake_out = RispostaOutput(risposta="Ok", richiede_umano=False,
                                  motivo="x", categoria="ristorante")
        with _intent_patch("booking"), \
             patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async",
                   new_callable=AsyncMock) as mock_gen, \
             patch("src.core.receptionist.conversation_orchestrator.recupera_contesto_documenti") as mock_rag:
            mock_rag_res = MagicMock()
            mock_rag_res.testo = ""
            mock_rag_res.chunks = []
            mock_rag.return_value = mock_rag_res
            mock_gen.return_value = fake_out
            await orchestrator.orchestrate(
                OrchestrationInput(
                    organization_id=ORG, auth_user_id=uuid.uuid4(), text="prenota"))
        assert mock_gen.call_args[1]["tools"] is None

    @pytest.mark.asyncio
    async def test_guardrail_still_applies_with_tools(self, mock_provider_context_open):
        deps = _mock_dependencies()
        deps["doc_repo"].knowledge_version = AsyncMock(return_value=7)
        deps["doc_repo"].doc_repo = deps["doc_repo"]
        factory = AsyncMock(return_value=[_tool("airtable_find_customer")])
        orchestrator = ConversationOrchestrator(
            **deps, airtable_tool_factory=factory)
        hallucinated = RispostaOutput(
            risposta="Il menu degustazione costa 15 euro.",
            richiede_umano=False, motivo="x", categoria="ristorante")
        with _intent_patch("booking"), \
             patch("src.core.receptionist.conversation_orchestrator.genera_risposta_async",
                   return_value=hallucinated), \
             patch("src.core.receptionist.conversation_orchestrator.recupera_contesto_documenti",
                   new_callable=AsyncMock, return_value=ContestoDocumenti(
                       testo="menu senza prezzi",
                       chunks=[{"content": "menu senza prezzi", "document_id": str(uuid.uuid4()),
                                "revision": 1}],
                       knowledge_epoch=7, reason="verified")), \
             patch("src.core.documenti.access.rag_access", new_callable=AsyncMock, return_value=True), \
             patch("src.core.documenti.rag_context.rag_access", new_callable=AsyncMock, return_value=True):
            result = await orchestrator.orchestrate(
                OrchestrationInput(
                    organization_id=ORG, auth_user_id=uuid.uuid4(),
                    text="quanto costa il menu?"))

        assert result.guardrail_action == "block"
