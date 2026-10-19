from datetime import datetime

from src.agents.report_agent import crea_report_crew
from src.core.llm_routing import LLMRouteRequest, route_llm
from src.core.llm_config import budget_ratio_from_billing
from src.core.billing.suspension import assert_ai_generation_allowed
from src.core.provider_accounting import ProviderAccountingContext, ProviderAccountingError
from src.core.statistiche import calcola_statistiche
from src.models.schemas import EventoDashboard, StatisticheReport, ReportOutput


def _report_deterministico(statistiche: StatisticheReport) -> ReportOutput:
    totale = statistiche.totale_messaggi
    return ReportOutput(
        data=datetime.now().strftime("%Y-%m-%d"),
        statistiche=statistiche,
        analisi_testuale=(
            f"Riepilogo automatico: registrati {totale} messaggi. "
            "Per un'analisi AI più approfondita, verifica che il servizio sia disponibile."
        ),
        suggerimenti=[],
        generato_il=datetime.now().isoformat(),
    )


def genera_report(
    storico: list[EventoDashboard],
    provider_context: ProviderAccountingContext | None = None,
) -> ReportOutput:
    statistiche = calcola_statistiche(storico)

    if statistiche.totale_messaggi < 3:
        return ReportOutput(
            data=datetime.now().strftime("%Y-%m-%d"),
            statistiche=statistiche,
            analisi_testuale=(
                f"Giornata molto tranquilla: solo {statistiche.totale_messaggi} "
                f"{'messaggi' if statistiche.totale_messaggi != 1 else 'messaggio'}. "
                "Non ci sono abbastanza dati per un'analisi significativa."
            ),
            suggerimenti=[],
            generato_il=datetime.now().isoformat(),
        )

    if provider_context is None:
        return _report_deterministico(statistiche)
    if type(provider_context) is not ProviderAccountingContext:
        raise ProviderAccountingError("AI generation is not authorized or accounting is unavailable")
    provider_context.assert_task({"report"})
    assert_ai_generation_allowed(provider_context.billing_snapshot)

    route = route_llm(LLMRouteRequest(
        task_type="report",
        remaining_budget_ratio=budget_ratio_from_billing(provider_context.billing_snapshot),
    ))
    errors: list[str] = []
    for model in [route.model]:
        try:
            crew = crea_report_crew(statistiche, model=model, provider_context=provider_context)
            output = crew.kickoff().pydantic
            break
        except Exception as exc:
            errors.append(type(exc).__name__)
    else:
        raise RuntimeError(
            "Tutti i modelli configurati hanno fallito. " + " | ".join(errors)
        )

    if output is None or not isinstance(output, ReportOutput):
        raise RuntimeError(
            "Il modello non ha restituito un output conforme a ReportOutput."
        )

    output.data = datetime.now().strftime("%Y-%m-%d")
    output.statistiche = statistiche
    output.generato_il = datetime.now().isoformat()

    return output
