from typing import Any

from src.agents.prompts import assegna_variante
from src.core.crew_runner import genera_risposta_async
from src.core.documenti.rag_context import recupera_contesto_documenti
from src.core.guardrails.validator import applica_guardrail, valida_risposta
from src.models.schemas import (
    MessaggioInput,
    OnboardingProfileInput,
    PreviewInput,
    ProfiloAttivita,
    RispostaOutput,
)

VERTICAL_TEMPLATES: dict[str, dict[str, Any]] = {
    "ristorante": {
        "label": "Ristorante",
        "tono": "caldo, diretto, familiare",
        "servizi": [
            "Pranzo e cena",
            "Prenotazioni tavoli",
            "Menu e carta vini",
            "Eventi privati su richiesta",
        ],
        "escalation": [
            "Allergie o intolleranze specifiche",
            "Gruppi numerosi o eventi privati",
            "Reclami su esperienze passate",
            "Richieste non presenti nel menu caricato",
        ],
        "esempio": "Avete posto per 4 domani sera?",
    },
    "parrucchiere": {
        "label": "Parrucchiere / Barber",
        "tono": "curato, rassicurante, pratico",
        "servizi": [
            "Taglio donna e uomo",
            "Piega",
            "Colore e tonalizzante",
            "Trattamenti cute e capelli",
        ],
        "escalation": [
            "Correzioni colore o lavori tecnici complessi",
            "Reazioni cutanee o problemi dermatologici",
            "Preventivi senza consulenza",
            "Reclami su servizi precedenti",
        ],
        "esempio": "Quanto dura un colore e piega?",
    },
    "hotel_bnb": {
        "label": "Hotel / B&B",
        "tono": "accogliente, preciso, ospitale",
        "servizi": [
            "Check-in e check-out",
            "Camere e disponibilita",
            "Colazione",
            "Indicazioni e servizi locali",
        ],
        "escalation": [
            "Modifiche o cancellazioni prenotazioni",
            "Richieste di rimborso",
            "Problemi durante il soggiorno",
            "Richieste accessibilita specifiche",
        ],
        "esempio": "A che ora posso fare check-in?",
    },
    "centro_estetico": {
        "label": "Centro estetico",
        "tono": "delicato, professionale, chiaro",
        "servizi": [
            "Manicure e pedicure",
            "Ceretta",
            "Trattamenti viso",
            "Massaggi e percorsi corpo",
        ],
        "escalation": [
            "Gravidanza, allergie o condizioni mediche",
            "Trattamenti invasivi o controindicazioni",
            "Reazioni dopo un trattamento",
            "Richieste di diagnosi",
        ],
        "esempio": "Posso fare un trattamento viso se ho pelle sensibile?",
    },
    "studio_medico_dentista": {
        "label": "Studio medico / dentista",
        "tono": "calmo, istituzionale, prudente",
        "servizi": [
            "Prenotazione visite",
            "Informazioni su orari e sede",
            "Promemoria appuntamenti",
            "Indicazioni amministrative",
        ],
        "escalation": [
            "Sintomi, diagnosi o consigli clinici",
            "Dolore acuto o urgenze",
            "Farmaci, terapie o referti",
            "Dati sanitari sensibili",
        ],
        "esempio": "Ho dolore a un dente, cosa posso prendere?",
    },
}


class ProfileCacheInvalidationError(RuntimeError):
    """Profile persistence succeeded, but its org FAQ cache stayed stale."""


def list_verticals() -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "label": value["label"],
            "tono": value["tono"],
            "servizi": value["servizi"],
            "escalation": value["escalation"],
            "esempio": value["esempio"],
        }
        for key, value in VERTICAL_TEMPLATES.items()
    ]


def build_business_profile(payload: OnboardingProfileInput) -> ProfiloAttivita:
    template = VERTICAL_TEMPLATES[payload.verticale]
    servizi = payload.servizi or template["servizi"]
    escalation = payload.regole_escalation or template["escalation"]
    tono = payload.tono or template["tono"]
    return ProfiloAttivita(
        nome=payload.nome_attivita,
        tipo_attivita=template["label"],
        tono=tono,
        orari=payload.orari,
        descrizione=payload.descrizione,
        servizi_principali=servizi,
        note_speciali=escalation,
        lingue_supportate=payload.lingue_supportate,
        lingua_default=payload.lingua_default,
        verticale=payload.verticale,
    )


async def get_profile(organization_id: str, repo) -> dict | None:
    """Profilo onboarding dell'org, oppure fallback su business_profile se mai salvato.
    Org-scoped: il chiamante passa SEMPRE l'organization_id dell'utente."""
    prof = await repo.get_onboarding_profile(organization_id)
    bp = await repo.get_org_business_profile(organization_id) or {}
    if not prof:
        if bp:
            return {
                "organization_id": str(organization_id),
                "verticale": bp.get("verticale") or "ristorante",
                "nome_attivita": bp.get("nome") or bp.get("nome_attivita") or "",
                "orari": bp.get("orari") or "",
                "tono": bp.get("tono") or "professionale_caloroso",
                "descrizione": bp.get("descrizione") or "",
                "servizi": bp.get("servizi_principali") or [],
                "regole_escalation": bp.get("note_speciali") or [],
                "lingue_supportate": bp.get("lingue_supportate") or ["it"],
                "lingua_default": bp.get("lingua_default") or "it",
            }
        return None
    if bp.get("orari"):
        prof["orari"] = bp["orari"]
    return prof


async def save_profile(
    organization_id: str,
    payload: OnboardingProfileInput,
    repo,
) -> dict:
    """Persiste il profilo onboarding dell'org e sincronizza
    organizations.business_profile (usato dal responder WhatsApp reale)."""
    profile = build_business_profile(payload)
    saved = await repo.save_onboarding_profile(
        organization_id,
        payload.verticale,
        payload.nome_attivita,
        payload.orari,
        payload.tono,
        payload.servizi,
        payload.regole_escalation,
        payload.whatsapp_collegato,
        payload.documenti_importati,
        profile.model_dump(),
        payload.lingue_supportate,
        payload.lingua_default,
        descrizione=payload.descrizione,
    )
    try:
        await repo.faq_cache_invalidate(organization_id)
    except Exception as exc:
        raise ProfileCacheInvalidationError(
            "Profile saved, but FAQ cache invalidation failed"
        ) from exc
    return saved


async def generate_preview(
    organization_id: str,
    payload: PreviewInput,
    repo,
    billing: dict | None = None,
    *,
    usage_sink: dict | None = None,
) -> RispostaOutput:
    """Preview reale: costruisce il profilo dal payload del wizard e lo fa
    girare nel vero responder (crew + LLM), arricchito dal contesto RAG dei
    documenti dell'org. Se l'org non ha ancora documenti indicizzati il
    contesto e' vuoto e la preview procede comunque (nessun errore).
    Il guardrail post-LLM vale anche in preview: quello che il titolare
    vede e' cioo che il responder farebbe davvero (task 12)."""
    from src.core.billing.suspension import assert_ai_generation_allowed

    assert_ai_generation_allowed(billing)
    profilo = build_business_profile(payload.profilo)

    contesto = await recupera_contesto_documenti(
        organization_id, payload.messaggio, repo
    )

    messaggio = MessaggioInput(testo=payload.messaggio)
    risposta = await genera_risposta_async(
        messaggio,
        profilo,
        billing=billing,
        contesto_documenti=contesto.testo,
        variante=assegna_variante(organization_id),
        usage_sink=usage_sink,
    )
    esito = valida_risposta(risposta, contesto.chunks, profilo)
    return applica_guardrail(risposta, esito)
