from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core.provider_accounting import ProviderAccountingError
from tests.provider_accounting_fakes import provider_context_stub


def test_review_runner_refuses_without_verified_context():
    from src.core.crew_runner_review import genera_risposta_recensione

    with patch("src.core.crew_runner_review.crea_review_crew") as make_crew:
        with pytest.raises(ProviderAccountingError):
            genera_risposta_recensione("review text")
    make_crew.assert_not_called()


def test_review_runner_passes_context_into_single_provider_path():
    from src.core.crew_runner_review import genera_risposta_recensione
    from src.core.llm_routing import LLMRoute
    from src.models.schemas import RispostaRecensioneOutput

    context = provider_context_stub(task_type="review")
    output = RispostaRecensioneOutput(
        id="", stato="bozza_generata", bozza_risposta="Grazie per il feedback.",
        sentiment="positiva", richiede_revisione_urgente=False,
        motivo="", categoria="generico",
    )
    crew = MagicMock()
    crew.kickoff.return_value = SimpleNamespace(pydantic=output)
    with patch("src.core.crew_runner_review.route_llm", return_value=LLMRoute(
        model="groq/model-primary", tier="premium", reason="review",
        fallback_models=("groq/model-fallback",),
    )), patch("src.core.crew_runner_review.crea_review_crew", return_value=crew) as make_crew:
        result = genera_risposta_recensione("review text", provider_context=context)

    assert result is output
    assert make_crew.call_count == 1
    assert make_crew.call_args.kwargs["provider_context"] is context
