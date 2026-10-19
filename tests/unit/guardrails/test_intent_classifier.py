"""Classificatore di intent (task 12): euristica keyword prima, modello
economico solo quando l'euristica e' incerta, fallback euristico su
errore/timeout. Mai bloccante per il path di produzione."""

import time
from unittest.mock import patch

import pytest

from src.core.guardrails.intent_classifier import IntentResult, classifica_intent
from tests.provider_accounting_fakes import provider_context_stub


def _uncertain_text() -> str:
    """Testo senza keyword forti: forza il path LLM quando abilitato."""
    return "vorrei sapere se potete fare una cosa particolare per noi"


class TestEuristica:
    async def test_faq_con_keyword(self):
        r = await classifica_intent("A che ora aprite la sera?")
        assert r.intent == "faq"
        assert r.source == "heuristic"
        assert r.confidence >= 0.8

    async def test_booking(self):
        r = await classifica_intent("Vorrei prenotare un tavolo per domani sera")
        assert r.intent == "booking"
        assert r.source == "heuristic"

    async def test_complaint_vince_su_tutto(self):
        r = await classifica_intent("Voglio un rimborso subito, pessimo servizio!")
        assert r.intent == "complaint"
        assert r.source == "heuristic"

    async def test_chitchat(self):
        r = await classifica_intent("grazie mille!")
        assert r.intent == "chitchat"
        assert r.source == "heuristic"

    async def test_testo_incerto_resta_euristica_se_llm_disabilitato(self, monkeypatch):
        monkeypatch.setenv("GUARDRAIL_INTENT_LLM_ENABLED", "false")
        with patch("src.core.guardrails.intent_classifier.crea_llm") as mock_llm_factory:
            r = await classifica_intent(_uncertain_text())
        mock_llm_factory.assert_not_called()
        assert r.source == "heuristic"
        assert r.confidence < 0.6


class _FakeLLM:
    def __init__(self, response: str, delay: float = 0.0):
        self._response = response
        self._delay = delay

    def call(self, prompt: str) -> str:
        if self._delay:
            time.sleep(self._delay)
        return self._response


class TestLLMPath:
    @pytest.fixture
    def provider_context(self):
        return provider_context_stub(task_type="intent_classification")

    async def test_llm_classifica_testo_incerto(self, monkeypatch, provider_context):
        monkeypatch.setenv("GUARDRAIL_INTENT_LLM_ENABLED", "true")
        fake = _FakeLLM('{"intent": "faq", "confidence": 0.9}')
        with patch("src.core.guardrails.intent_classifier.crea_llm", return_value=fake):
            r = await classifica_intent(
                _uncertain_text(), allow_llm=True, provider_context=provider_context,
            )
        assert r == IntentResult(intent="faq", confidence=0.9, source="llm")

    async def test_llm_risposta_con_markdown_fence(self, monkeypatch, provider_context):
        monkeypatch.setenv("GUARDRAIL_INTENT_LLM_ENABLED", "true")
        fake = _FakeLLM('```json\n{"intent": "booking", "confidence": 0.85}\n```')
        with patch("src.core.guardrails.intent_classifier.crea_llm", return_value=fake):
            r = await classifica_intent(
                _uncertain_text(), allow_llm=True, provider_context=provider_context,
            )
        assert r.intent == "booking"
        assert r.source == "llm"

    async def test_llm_json_invalido_fallback_euristica(self, monkeypatch, provider_context):
        monkeypatch.setenv("GUARDRAIL_INTENT_LLM_ENABLED", "true")
        fake = _FakeLLM("non so, forse faq?")
        with patch("src.core.guardrails.intent_classifier.crea_llm", return_value=fake):
            r = await classifica_intent(
                _uncertain_text(), allow_llm=True, provider_context=provider_context,
            )
        assert r.source == "heuristic"

    async def test_llm_solleva_fallback_euristica(self, monkeypatch, provider_context):
        monkeypatch.setenv("GUARDRAIL_INTENT_LLM_ENABLED", "true")

        class _BrokenLLM:
            def call(self, prompt):
                raise RuntimeError("provider down")

        with patch("src.core.guardrails.intent_classifier.crea_llm", return_value=_BrokenLLM()):
            r = await classifica_intent(
                _uncertain_text(), allow_llm=True, provider_context=provider_context,
            )
        assert r.source == "heuristic"

    async def test_llm_timeout_fallback_euristica(self, monkeypatch, provider_context):
        monkeypatch.setenv("GUARDRAIL_INTENT_LLM_ENABLED", "true")
        monkeypatch.setenv("GUARDRAIL_INTENT_TIMEOUT", "0.1")
        fake = _FakeLLM('{"intent": "faq", "confidence": 0.9}', delay=5.0)
        start = time.monotonic()
        with patch("src.core.guardrails.intent_classifier.crea_llm", return_value=fake):
            r = await classifica_intent(
                _uncertain_text(), allow_llm=True, provider_context=provider_context,
            )
        elapsed = time.monotonic() - start
        assert r.source == "heuristic"
        assert elapsed < 4, f"il timeout non e' scattato: {elapsed:.1f}s"

    async def test_euristica_sicura_non_chiama_llm(self, monkeypatch, provider_context):
        """Keyword forte = confidenza alta: il modello economico NON viene
        sprecato su messaggi che l'euristica gia' classifica bene."""
        monkeypatch.setenv("GUARDRAIL_INTENT_LLM_ENABLED", "true")
        with patch("src.core.guardrails.intent_classifier.crea_llm") as mock_llm_factory:
            await classifica_intent(
                "quanto costa il menu?", allow_llm=True,
                provider_context=provider_context,
            )
        mock_llm_factory.assert_not_called()

    async def test_intent_invalido_dal_llm_scartato(self, monkeypatch, provider_context):
        """Il classificatore LLM risponde con un intent fuori dal set:
        non lo accettiamo, resta l'euristica."""
        monkeypatch.setenv("GUARDRAIL_INTENT_LLM_ENABLED", "true")
        fake = _FakeLLM('{"intent": "spam", "confidence": 0.99}')
        with patch("src.core.guardrails.intent_classifier.crea_llm", return_value=fake):
            r = await classifica_intent(
                _uncertain_text(), allow_llm=True, provider_context=provider_context,
            )
        assert r.source == "heuristic"
