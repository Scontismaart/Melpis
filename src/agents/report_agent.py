from crewai import Agent, Task, Crew, Process

from src.core.llm_config import LLMRouteRequest, crea_llm
from src.agents.prompts_report import (
    costruisci_system_prompt_report,
    costruisci_user_prompt_report,
)
from src.models.schemas import StatisticheReport, ReportOutput


def crea_report_agent(model: str | None = None, provider_context=None) -> Agent:
    return Agent(
        role="Analista business conversazioni clienti",
        goal=(
            "Analizzare le statistiche delle conversazioni della giornata "
            "e produrre un report narrativo con trend, pattern e suggerimenti "
            "proattivi per il titolare dell'attività."
        ),
        backstory=costruisci_system_prompt_report(),
        llm=crea_llm(
            model=model,
            temperature=0.5,
            route_request=LLMRouteRequest(task_type="report"),
            provider_context=provider_context,
            reason="daily_report",
        ),
        verbose=False,
        allow_delegation=False,
    )


def crea_report_task(agent: Agent, statistiche: StatisticheReport) -> Task:
    descrizione = costruisci_user_prompt_report(statistiche)

    return Task(
        description=descrizione,
        expected_output=(
            "Un oggetto ReportOutput con: analisi_testuale (stringa, riepilogo "
            "narrativo di 3-6 frasi) e suggerimenti (lista di stringhe, 1-3 "
            "suggerimenti specifici basati sui dati)."
        ),
        agent=agent,
        output_pydantic=ReportOutput,
    )


def crea_report_crew(
    statistiche: StatisticheReport, model: str | None = None, provider_context=None
) -> Crew:
    agent = crea_report_agent(model=model, provider_context=provider_context)
    task = crea_report_task(agent, statistiche)

    return Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
    )
