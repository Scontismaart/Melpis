from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.core.crew_runner as crew_runner
from src.core.provider_accounting import ProviderAccountingError
from src.models.schemas import MessaggioInput, ProfiloAttivita, RispostaOutput
from tests.provider_accounting_fakes import provider_context_stub


class FakeCrew:
    usage_metrics = SimpleNamespace(prompt_tokens=999, completion_tokens=999, total_tokens=1998)

    async def kickoff_async(self):
        return SimpleNamespace(
            pydantic=RispostaOutput(risposta="ciao", richiede_umano=False, motivo="ok")
        )


def _profilo():
    return ProfiloAttivita(nome="Test", tipo_attivita="ristorante", tono="cordiale", orari="9-18")


@pytest.mark.asyncio
async def test_runner_requires_provider_context_before_building_crew():
    with patch("src.core.crew_runner.crea_crew") as make_crew:
        with pytest.raises(ProviderAccountingError):
            await crew_runner.genera_risposta_async(
                MessaggioInput(testo="ciao"), _profilo(),
            )
    make_crew.assert_not_called()


@pytest.mark.asyncio
async def test_crew_usage_metrics_are_not_used_as_provider_accounting(monkeypatch):
    context = provider_context_stub()
    captured = {}

    def build_crew(*args, **kwargs):
        captured.update(kwargs)
        return FakeCrew()

    monkeypatch.setattr(crew_runner, "crea_crew", build_crew)
    monkeypatch.setattr(
        crew_runner,
        "route_llm",
        lambda _request: SimpleNamespace(model="groq/model", fallback_models=[], reason="test"),
    )
    monkeypatch.setattr(crew_runner, "_route_request_for_message", lambda *args, **kwargs: None)

    output = await crew_runner.genera_risposta_async(
        MessaggioInput(testo="ciao"), _profilo(), provider_context=context,
    )

    assert output.risposta == "ciao"
    assert captured["provider_context"] is context
    assert "usage_sink" not in captured
