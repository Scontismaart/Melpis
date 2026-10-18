"""
validator.py
------------
Guardrail di validazione post-LLM: controlla la risposta generata dal
responder PRIMA che venga inviata al cliente (roadmap task 12).

Tre esiti possibili:
- "none": la risposta passa invariata;
- "trim": correzione leggera (lunghezza oltre il limite, markdown non
  renderizzabile da WhatsApp) con testo modificato;
- "block": violazione grave (prezzo non presente nel contesto RAG, frase
  di rinuncia, risposta vuota) -> il chiamante sostituisce il testo col
  fallback verso lo staff e imposta richiede_umano=True, riusando il
  flusso di escalation HITL esistente.

Il validatore e' puramente deterministico (regex e soglie): nessuna
chiamata LLM aggiuntiva, latenza trascurabile, comportamento testabile.

Anti-allucinazione prezzi: ogni importo in euro presente nella risposta
deve comparire anche nel contesto RAG (chunk) o nel profilo dell'attivita'
(orari/servizi/note). Un importo non verificabile e' potenzialmente
inventato dal modello: la risposta viene bloccata.
"""

import os
import re
from dataclasses import dataclass, field

from src.models.schemas import ProfiloAttivita, RispostaOutput

DEFAULT_MAX_REPLY_CHARS = 800

FALLBACK_STAFF_TEXT = (
    "Non ho questa informazione al momento, ti metto in contatto con lo staff!"
)

# Importi monetari: "€15", "€ 15,50", "15€", "15 €", "15,50 euro", "15.50 Euro".
# I numeri "secchi" (orari, coperti, date) NON matchano: serve il simbolo o
# la parola "euro" — e' quello che distingue un prezzo da un orario. Nota:
# dopo "euro" serve \b (parola), dopo "€" no (simbolo non-word).
_PREZZO_RE = re.compile(
    r"€\s*(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s*(?:€|euro\b)",
    re.IGNORECASE,
)

_FRASI_VIETATE = (
    "non posso aiutarti",
    "non sono in grado di aiutarti",
    "non posso esserti d'aiuto",
    "non so rispondere",
)

# Markdown che WhatsApp non renderizza: bold/italic con ** __, heading #,
# liste -/* e numerate. Le liste diventano bullet "•" per restare leggibili.
_MD_BOLD_RE = re.compile(r"(\*\*|__)(.*?)\1")
_MD_HEADER_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MD_ULIST_RE = re.compile(r"^(\s*)[-*]\s+", re.MULTILINE)
_MD_OLIST_RE = re.compile(r"^(\s*)\d+\.\s+", re.MULTILINE)


@dataclass(frozen=True)
class EsitoValidazione:
    """Esito del guardrail: `azione` guida il chiamante, `testo` e' la
    versione (eventualmente corretta) della risposta, `violazioni` serve
    ai log/usage events per iterare sui prompt."""

    azione: str  # "none" | "trim" | "block"
    testo: str
    motivo: str = ""
    violazioni: tuple[str, ...] = field(default_factory=tuple)


def _normalizza_importo(raw: str) -> str:
    """'15', '15,50' e '15.50' -> '15.00' / '15.50': confronto alla pari
    tra formato italiano e anglosassone."""
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        return raw
    return f"{value:.2f}"


def _estrai_importi(testo: str) -> set[str]:
    out: set[str] = set()
    for match in _PREZZO_RE.finditer(testo):
        raw = match.group(1) or match.group(2)
        if raw:
            out.add(_normalizza_importo(raw))
    return out


def _testo_profilo(profilo: ProfiloAttivita | None) -> str:
    if profilo is None:
        return ""
    parti = [profilo.orari or ""]
    parti.extend(profilo.servizi_principali or [])
    parti.extend(profilo.note_speciali or [])
    return " ".join(parti)


def _rimuovi_markdown(testo: str) -> str:
    testo = _MD_BOLD_RE.sub(r"\2", testo)
    testo = _MD_HEADER_RE.sub("", testo)
    testo = _MD_ULIST_RE.sub(r"\1• ", testo)
    testo = _MD_OLIST_RE.sub(r"\1• ", testo)
    return testo


def _trim_a_confine_frase(testo: str, max_chars: int) -> str:
    if len(testo) <= max_chars:
        return testo
    finestra = testo[: max_chars + 1]
    taglio = max(finestra.rfind(p) for p in (".", "!", "?", "…"))
    if taglio > 0:
        return testo[: taglio + 1]
    return testo[:max_chars].rstrip() + "…"


def valida_risposta(
    risposta: RispostaOutput,
    contesto_chunks: list[dict] | None = None,
    profilo: ProfiloAttivita | None = None,
    max_chars: int | None = None,
) -> EsitoValidazione:
    """Valida l'output del responder contro le regole del task 12.

    `contesto_chunks` sono i chunk RAG recuperati per questa domanda (il
    campo `content` di ciascuno e' la fonte di verita' ammessa); `profilo`
    autorizza eventuali prezzi indicati da orari/servizi/note dell'attivita'.
    """
    chunks = contesto_chunks or []
    testo = (risposta.risposta or "").strip()

    if not testo:
        return EsitoValidazione("block", FALLBACK_STAFF_TEXT, "risposta_vuota",
                                ("risposta_vuota",))

    # 1. Grounding prezzi: ogni importo in risposta deve esistere nel
    #    contesto RAG o nel profilo. Contesto vuoto + prezzo = no grounding.
    importi_risposta = _estrai_importi(testo)
    if importi_risposta:
        fonti = " ".join(str(c.get("content", "")) for c in chunks)
        fonti += " " + _testo_profilo(profilo)
        importi_consentiti = _estrai_importi(fonti)
        non_verificati = importi_risposta - importi_consentiti
        if non_verificati:
            return EsitoValidazione(
                "block", FALLBACK_STAFF_TEXT, "prezzo_non_verificato",
                ("prezzo_non_verificato",),
            )

    # 2. Frasi vietate: il responder che si tira indietro ("non posso
    #    aiutarti") senza girare allo staff e' una brutta esperienza: meglio
    #    il fallback esplicito con escalation.
    lowered = testo.lower()
    if any(frase in lowered for frase in _FRASI_VIETATE):
        return EsitoValidazione("block", FALLBACK_STAFF_TEXT, "frase_vietata",
                                ("frase_vietata",))

    # 3. Correzioni leggere: markdown non renderizzabile su WhatsApp.
    testo_pulito = _rimuovi_markdown(testo).strip()
    if not testo_pulito:
        return EsitoValidazione("block", FALLBACK_STAFF_TEXT, "risposta_vuota",
                                ("risposta_vuota",))

    # 4. Lunghezza massima (tono WhatsApp: breve e diretto).
    if max_chars is None:
        max_chars = int(os.getenv("GUARDRAIL_MAX_REPLY_CHARS", str(DEFAULT_MAX_REPLY_CHARS)))
    testo_finale = _trim_a_confine_frase(testo_pulito, max_chars)

    if testo_finale != testo:
        motivi = []
        if testo_pulito != testo:
            motivi.append("formato_markdown")
        if len(testo_pulito) > max_chars:
            motivi.append("lunghezza_eccessiva")
        return EsitoValidazione("trim", testo_finale,
                                "+".join(motivi) or "correzione", tuple(motivi))
    return EsitoValidazione("none", testo)


def applica_guardrail(risposta: RispostaOutput, esito: EsitoValidazione) -> RispostaOutput:
    """Applica l'esito alla risposta: trim sostituisce il testo, block
    sostituisce il testo col fallback e forza l'escalation a umano."""
    if esito.azione == "none":
        return risposta
    if esito.azione == "trim":
        return risposta.model_copy(update={"risposta": esito.testo})
    guarded = risposta.model_copy(update={
        "risposta": FALLBACK_STAFF_TEXT,
        "richiede_umano": True,
        "motivo": f"guardrail_{esito.motivo}",
        "prenotazione": None,
    })
    guarded._guardrail_blocked = True
    return guarded
