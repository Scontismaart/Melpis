from crewai import Agent, Task, Crew, Process

from src.core.llm_config import LLMRouteRequest, crea_llm
from src.agents.review_prompts import (
    costruisci_system_prompt_review,
    costruisci_user_prompt_review,
)
from src.models.schemas import LINGUA_DEFAULT, RispostaRecensioneOutput


def crea_review_agent(
    model: str | None = None,
    lingue_supportate: list[str] | None = None,
    lingua_default: str = LINGUA_DEFAULT,
    provider_context=None,
    reason: str = "review",
) -> Agent:
    return Agent(
        role="Esperto gestione reputazione online",
        goal=(
            "Analizzare recensioni dei clienti e produrre bozze di risposta "
            "professionali, misurate, mai difensive."
        ),
        backstory=costruisci_system_prompt_review(lingue_supportate, lingua_default),
        llm=crea_llm(
            model=model,
            temperature=0.3,
            route_request=LLMRouteRequest(task_type="review"),
            provider_context=provider_context,
            reason=reason,
        ),
        verbose=False,
        allow_delegation=False,
    )


def crea_review_task(
    agent: Agent,
    testo: str,
    stelle: int | None = None,
    autore: str = "",
    profilo_attivita: dict | None = None,
    contesto_documenti: str = "",
) -> Task:
    descrizione = costruisci_user_prompt_review(
        testo, stelle, autore,
        profilo_attivita=profilo_attivita,
        contesto_documenti=contesto_documenti,
    )

    return Task(
        description=descrizione,
        expected_output=(
            "Un oggetto RispostaRecensioneOutput con: bozza_risposta (stringa), "
            "sentiment (positiva/neutra/negativa), richiede_revisione_urgente "
            "(bool), motivo (stringa), categoria (stringa)."
        ),
        agent=agent,
        output_pydantic=RispostaRecensioneOutput,
    )


def crea_review_crew(
    testo: str,
    stelle: int | None = None,
    autore: str = "",
    model: str | None = None,
    lingue_supportate: list[str] | None = None,
    lingua_default: str = LINGUA_DEFAULT,
    profilo_attivita: dict | None = None,
    contesto_documenti: str = "",
    provider_context=None,
) -> Crew:
    agent = crea_review_agent(model=model, lingue_supportate=lingue_supportate,
                              lingua_default=lingua_default, provider_context=provider_context)
    task = crea_review_task(
        agent, testo, stelle, autore, profilo_attivita, contesto_documenti
    )

    return Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
    )
