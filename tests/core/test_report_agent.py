from datetime import datetime
from unittest.mock import patch

import pytest
from crewai import LLM

from src.agents.report_agent import crea_report_agent
from src.core.priorita import Priorita
from src.core.llm_routing import LLMRoute, LLMRouteRequest, route_llm
from src.models.schemas import EventoDashboard, ReportOutput, StatisticheReport
from tests.provider_accounting_fakes import provider_context_stub


def test_crea_report_agent_instrada_sul_task_report():
    """Audit: report_agent usava crea_llm() senza route_request finendo
    sul modello free; ora deve sempre passare task_type='report'."""
    reale = LLM(model="openrouter/openai/gpt-4o-mini", api_key="sk-test")

    context = provider_context_stub(task_type="report")
    with patch(
        "src.agents.report_agent.crea_llm", return_value=reale
    ) as spy:
        crea_report_agent(provider_context=context)

    assert spy.call_count == 1
    chiamata = spy.call_args
    assert chiamata.kwargs["temperature"] == 0.5
    assert chiamata.kwargs["route_request"] == LLMRouteRequest(
        task_type="report"
    )
    assert chiamata.kwargs["provider_context"] is context


def test_task_report_usa_tier_premium(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_CHEAP", "cheap/model")
    monkeypatch.setenv("OPENROUTER_MODEL_PREMIUM", "premium/model")

    route = route_llm(LLMRouteRequest(task_type="report"))

    assert route.tier == "premium"
    assert route.model == "premium/model"
    assert route.reason == "premium_task"


class _FakeCrew:
    def __init__(self, risultato):
        self._risultato = risultato

    def kickoff(self):
        return self._risultato


class _FakeRisultato:
    def __init__(self, output):
        self.pydantic = output


def _statistiche() -> StatisticheReport:
    return StatisticheReport(
        periodo="2026-08-17",
        totale_messaggi=10,
        gestiti_da_ai=8,
        girati_a_umano=2,
    )


def _output() -> ReportOutput:
    return ReportOutput(
        data="2026-08-17",
        statistiche=_statistiche(),
        analisi_testuale="ok",
        suggerimenti=["test"],
        generato_il="2026-08-17T20:00:00",
    )


def _storico() -> list[EventoDashboard]:
    return [
        EventoDashboard(
            id=f"e{i}",
            tipo_evento="messaggio",
            timestamp=datetime.now(),
            priorita=Priorita.MEDIA,
            testo_originale=f"messaggio {i}",
            risposta_ai="ciao",
            gestito_da_ai=True,
        )
        for i in range(4)
    ]


def test_genera_report_dispatches_only_with_verified_context(monkeypatch):
    from src.core import crew_runner_report

    monkeypatch.setattr(
        crew_runner_report,
        "route_llm",
        lambda request: LLMRoute(
            model="report/primary",
            tier="premium",
            reason="test",
            fallback_models=("report/fallback",),
        ),
    )

    dispatches = []

    def fake_crea_report_crew(statistiche, model=None, provider_context=None):
        dispatches.append((model, provider_context))
        return _FakeCrew(_FakeRisultato(_output()))

    monkeypatch.setattr(
        crew_runner_report, "crea_report_crew", fake_crea_report_crew
    )

    context = provider_context_stub(task_type="report")
    risultato = crew_runner_report.genera_report(_storico(), provider_context=context)

    assert dispatches == [("report/primary", context)]
    assert risultato.analisi_testuale == "ok"
    assert risultato.statistiche.totale_messaggi == len(_storico())


def test_genera_report_failure_is_sanitized_and_no_unaccounted_fallback(monkeypatch):
    from src.core import crew_runner_report

    monkeypatch.setattr(
        crew_runner_report,
        "route_llm",
        lambda request: LLMRoute(
            model="report/primary",
            tier="premium",
            reason="test",
            fallback_models=("report/fallback",),
        ),
    )

    calls = []
    def fake_crea_report_crew(statistiche, model=None, provider_context=None):
        calls.append(model)
        raise RuntimeError("sempre rotto")

    monkeypatch.setattr(
        crew_runner_report, "crea_report_crew", fake_crea_report_crew
    )

    with pytest.raises(RuntimeError, match="Tutti i modelli configurati") as error:
        crew_runner_report.genera_report(_storico(), provider_context=provider_context_stub())
    assert calls == ["report/primary"]
    assert "sempre rotto" not in str(error.value)
