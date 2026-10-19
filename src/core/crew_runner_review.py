from src.agents.review_agent import crea_review_crew
from src.core.llm_config import LLMRouteRequest, budget_ratio_from_billing, route_llm
from src.models.schemas import LINGUA_DEFAULT, RispostaRecensioneOutput
from src.core.provider_accounting import ProviderAccountingContext, ProviderAccountingError


def genera_risposta_recensione(
    testo: str,
    stelle: int | None = None,
    autore: str = "",
    billing: dict | None = None,
    lingue_supportate: list[str] | None = None,
    lingua_default: str = LINGUA_DEFAULT,
    profilo_attivita: dict | None = None,
    contesto_documenti: str = "",
    provider_context: ProviderAccountingContext | None = None,
) -> RispostaRecensioneOutput:
    if type(provider_context) is not ProviderAccountingContext:
        raise ProviderAccountingError("AI generation is not authorized or accounting is unavailable")
    provider_context.assert_task({"review"})
    from src.core.billing.suspension import assert_ai_generation_allowed
    assert_ai_generation_allowed(provider_context.billing_snapshot)
    billing = provider_context.billing_snapshot
    route = route_llm(
        LLMRouteRequest(
            task_type="review",
            user_text=testo,
            remaining_budget_ratio=budget_ratio_from_billing(billing),
        )
    )
    errors: list[str] = []
    # A single route avoids silently continuing when a failed attempt left
    # accounting unresolved. CrewAI retries that actually dispatch are each
    # recorded by the private LLM adapter.
    for model in [route.model]:
        try:
            crew = crea_review_crew(
                testo, stelle, autore, model=model,
                lingue_supportate=lingue_supportate,
                lingua_default=lingua_default,
                profilo_attivita=profilo_attivita,
                contesto_documenti=contesto_documenti,
                provider_context=provider_context,
            )
            risultato = crew.kickoff()
            break
        except Exception:
            errors.append(model)
    else:
        raise RuntimeError(
            "Tutti i modelli configurati hanno fallito. " + " | ".join(errors)
        )

    output = risultato.pydantic
    if output is None or not isinstance(output, RispostaRecensioneOutput):
        raise RuntimeError(
            "Il modello non ha restituito un output conforme a "
            "RispostaRecensioneOutput."
        )

    return output
