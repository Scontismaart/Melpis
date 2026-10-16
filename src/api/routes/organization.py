"""Organization & Onboarding API Routes (Invarianti 1, 2, 8).

Gestisce:
- Verticali e lingue disponibili
- Profilo onboarding (lettura e salvataggio)
- Preview AI onboarding (budget-aware)
- Impostazioni organizzazione (fuso orario)
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.dependencies import get_repo, require_ruolo
from src.api.routes.common import audit_event, get_billing_snapshot, record_ai_usage
from src.core.billing.suspension import AIGenerationBlocked, assert_ai_generation_allowed
from src.core.onboarding import (
    ProfileCacheInvalidationError,
    generate_preview,
    get_profile,
    list_verticals,
    save_profile,
)
from src.models.schemas import (
    OnboardingProfileInput,
    PreviewInput,
    RispostaOutput,
    LINGUE_DISPONIBILI,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["organization"])

_TIMEZONE_COMUNI = [
    "Europe/Rome", "Europe/London", "Europe/Paris", "Europe/Berlin",
    "Europe/Madrid", "America/New_York", "America/Chicago",
    "America/Los_Angeles", "America/Sao_Paulo", "Asia/Dubai",
    "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney", "UTC",
]


@router.get("/api/onboarding/verticali")
def onboarding_verticali(user: dict = Depends(require_ruolo("owner", "manager", "staff"))):
    """Restituisce i verticali supportati e l'elenco delle lingue disponibili."""
    return {
        "verticali": list_verticals(),
        "lingue_disponibili": sorted(LINGUE_DISPONIBILI),
    }


@router.get("/api/onboarding/profilo")
async def onboarding_profilo(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Recupera il profilo di onboarding per l'organizzazione dell'utente autenticato."""
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(
            status_code=401,
            detail="Nessuna organizzazione collegata: inserisci API key e Organization ID.",
        )
    repo = get_repo(request)
    return {"profilo": await get_profile(org_id, repo)}


@router.post("/api/onboarding/profilo")
async def onboarding_salva_profilo(
    profilo: OnboardingProfileInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Salva o aggiorna il profilo di onboarding dell'organizzazione."""
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(
            status_code=401,
            detail="Nessuna organizzazione collegata: inserisci API key e Organization ID.",
        )
    repo = get_repo(request)
    try:
        profilo_salvato = await save_profile(org_id, profilo, repo)
    except ProfileCacheInvalidationError as exc:
        logger.error("FAQ cache invalidation failed after profile save org=%s", org_id)
        raise HTTPException(
            status_code=503,
            detail="Profilo salvato, ma non è stato possibile aggiornare la cache FAQ. Riprova.",
        ) from exc

    if profilo.orari:
        try:
            from src.core.documenti.dati_struttura import indicizza_dati_struttura
            await indicizza_dati_struttura(repo, org_id, profilo.orari)
        except Exception as exc:
            logger.warning("indicizza_dati_struttura in onboarding_salva_profilo failed: %s", exc)

    await audit_event(
        request,
        user,
        "profilo.aggiornato",
        target_table="onboarding_profiles",
        details={"nome_attivita": profilo.nome_attivita, "tono": profilo.tono},
    )
    return {"profilo": profilo_salvato}


@router.get("/api/impostazioni/organizzazione")
async def get_impostazioni_organizzazione(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Recupera le impostazioni correnti dell'organizzazione (es. timezone)."""
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(401, "Nessuna organizzazione collegata")
    repo = get_repo(request)
    async with repo.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT timezone, name FROM organizations WHERE id = $1::uuid", org_id
        )
    if not row:
        raise HTTPException(404, "Organizzazione non trovata")
    return {
        "timezone": row["timezone"] or "Europe/Rome",
        "nome": row["name"],
        "timezone_disponibili": _TIMEZONE_COMUNI,
    }


@router.put("/api/impostazioni/organizzazione")
async def put_impostazioni_organizzazione(
    body: dict,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Aggiorna il fuso orario dell'organizzazione."""
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(401, "Nessuna organizzazione collegata")
    tz = (body.get("timezone") or "").strip()
    if tz not in _TIMEZONE_COMUNI:
        raise HTTPException(422, "Fuso orario non valido")
    repo = get_repo(request)
    async with repo.pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET timezone = $2, updated_at = NOW() WHERE id = $1::uuid",
            org_id, tz,
        )
    await audit_event(
        request,
        user,
        "org.timezone_updated",
        target_table="organizations",
        details={"timezone": tz},
    )
    return {"ok": True, "timezone": tz}


@router.post("/api/onboarding/preview", response_model=RispostaOutput)
async def onboarding_preview(
    richiesta: PreviewInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Genera una risposta di anteprima durante il wizard di onboarding (Invariante 8)."""
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(
            status_code=401,
            detail="Nessuna organizzazione collegata: inserisci API key e Organization ID.",
        )
    repo = get_repo(request)
    billing = await get_billing_snapshot(repo, org_id)
    if not isinstance(billing, dict) or not billing or billing.get("ai_accounting_blocked"):
        raise HTTPException(status_code=503, detail="Impossibile verificare la disponibilità AI")
    try:
        assert_ai_generation_allowed(billing)
    except AIGenerationBlocked as exc:
        raise HTTPException(status_code=403, detail="Risposte AI non disponibili per questo piano o quota") from exc
    output = await generate_preview(org_id, richiesta, repo, billing=billing)
    await record_ai_usage(
        repo,
        org_id,
        "onboarding_preview",
        richiesta.messaggio,
        billing,
        {"verticale": richiesta.profilo.verticale},
    )
    return output
