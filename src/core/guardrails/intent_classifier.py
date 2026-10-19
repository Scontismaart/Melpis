"""
intent_classifier.py
--------------------
Classificatore di intent (roadmap task 12): gira PRIMA del responder
principale e distribuisce le informazioni di routing a chi ne ha bisogno
(tier del modello, cache FAQ, guardrail).

Strategia a due stadi per non sprecare budget:
1. euristica keyword (gratuita, istantanea) — riusa gli insiemi gia'
   definiti in llm_routing e li estende;
2. modello economico SOLO quando l'euristica e' incerta (confidenza sotto
   soglia), con prompt minimo, temperatura 0 e timeout corto.

Mai bloccante: su errore, timeout o JSON malformato vince l'euristica.
Il modello e' configurabile con OPENROUTER_MODEL_INTENT (default: il
modello cheap del routing, es. gpt-4o-mini).
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass

from src.core.llm_config import LLMRouteRequest, crea_llm
from src.core.llm_routing import _ESCALATION_KEYWORDS, _FAQ_KEYWORDS

INTENTS = frozenset({"faq", "booking", "complaint", "chitchat", "out_of_scope"})

# Sotto questa confidenza dell'euristica vale la pena spendere una chiamata
# al modello economico.
_LLM_TRIGGER_CONFIDENZA = 0.6
_TIMEOUT_DEFAULT = 2.5

_BOOKING_KEYWORDS = {
    "prenotare", "prenoto", "prenota", "prenotazione", "tavolo",
    "appuntamento", "posto", "disponibilita", "disponibile", "disponibili",
}

_CHITCHAT_KEYWORDS = {
    "ciao", "buongiorno", "buonasera", "salve", "hey", "hello", "hi",
    "grazie", "perfetto", "ok", "arrivederci", "buonanotte",
}

_PROMPT_CLASSIFICATORE = (
    "Classifica l'intento del messaggio di un cliente a un'attivita' "
    "commerciale (ristorante, parrucchiere, studio medico...).\n"
    "Intenti possibili: faq (domande su orari/prezzi/menu/servizi), "
    "booking (richieste di prenotazione), complaint (reclami/rabbia/"
    "urgenti), chitchat (saluti/ringraziamenti), out_of_scope (altro).\n"
    'Rispondi SOLO con JSON: {{"intent": "...", "confidence": 0.0-1.0}}\n\n'
    'Messaggio: "{testo}"'
)


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float
    source: str  # "heuristic" | "llm"


def _tokens(testo: str) -> set[str]:
    return {
        token.strip("?!.,;:()[]{}\"'").lower()
        for token in (testo or "").split()
    }


def _euristica(testo: str) -> IntentResult:
    lowered = (testo or "").lower()
    words = _tokens(testo)

    if any(keyword in lowered for keyword in _ESCALATION_KEYWORDS):
        return IntentResult("complaint", 0.9, "heuristic")
    if words & _BOOKING_KEYWORDS:
        return IntentResult("booking", 0.85, "heuristic")
    if (words & _FAQ_KEYWORDS) and not (words & _BOOKING_KEYWORDS):
        return IntentResult("faq", 0.85, "heuristic")
    if (words & _CHITCHAT_KEYWORDS) and len(words) <= 6:
        return IntentResult("chitchat", 0.75, "heuristic")
    return IntentResult("out_of_scope", 0.2, "heuristic")


def _parse_llm_output(raw: str) -> IntentResult | None:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        intent = str(data.get("intent", "")).strip()
        confidence = float(data.get("confidence", 0.0))
    except (ValueError, TypeError):
        return None
    if intent not in INTENTS:
        return None
    confidence = min(max(confidence, 0.0), 1.0)
    return IntentResult(intent, confidence, "llm")


def _llm_enabled() -> bool:
    return os.getenv("GUARDRAIL_INTENT_LLM_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _modello_intent() -> str:
    configured = os.getenv("AI_MODEL", "").strip()
    if configured:
        return os.getenv("AI_MODEL_INTENT", "").strip() or os.getenv("AI_MODEL_CHEAP", "").strip() or configured
    return (
        os.getenv("OPENROUTER_MODEL_INTENT", "").strip()
        or os.getenv("OPENROUTER_MODEL_CHEAP", "").strip()
        or "groq/llama-3.1-8b-instant"
    )


# Alias pubblico: i chiamanti loggano il modello usato nell'usage event.
modello_intent = _modello_intent

async def classifica_intent(
    testo: str,
    *,
    allow_llm: bool = False,
    provider_context=None,
) -> IntentResult:
    """Classifica l'intento del messaggio. Non solleva mai: il peggio che
    possa accadere e' l'esito euristico (source="heuristic")."""
    esito = _euristica(testo)
    if esito.confidence >= _LLM_TRIGGER_CONFIDENZA or not allow_llm or not _llm_enabled():
        return esito

    try:
        timeout = float(os.getenv("GUARDRAIL_INTENT_TIMEOUT", str(_TIMEOUT_DEFAULT)))
        timeout = max(0.1, min(timeout, _TIMEOUT_DEFAULT))
        llm = crea_llm(
            model=_modello_intent(), temperature=0.0, timeout=timeout,
            route_request=LLMRouteRequest(task_type="intent_classification"),
            provider_context=provider_context, reason="intent_classification",
        )
        raw = await asyncio.wait_for(
            asyncio.to_thread(llm.call, _PROMPT_CLASSIFICATORE.format(testo=testo)),
            timeout=timeout,
        )
        return _parse_llm_output(raw) or esito
    except Exception:  # noqa: BLE001 — fail-open deliberato: vince l'euristica
        return esito
