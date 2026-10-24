"""Shared route helpers, billing governance, and mock-safe symbol resolvers (Invarianti 1, 8, 10).

Fornisce helper centralizzati riutilizzati dai router modulari per:
- Audit log org-scoped
- Snapshot billing & accounting consumi AI
- Blocco feature in base al piano
- Storico eventi condiviso per la modalità demo senza DB
- Risoluzione dinamica dei simboli per piena compatibilità con patch("src.api.main.*")
"""

import logging
import sys
from datetime import datetime
from typing import Any
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Storico condiviso per la modalità demo in-memory
_storico_eventi: list[Any] = []
_prossimo_id_evento: int = 0


def get_shared_event_history() -> list[Any]:
    """Restituisce la lista globale degli eventi demo in-memory."""
    return _storico_eventi


def next_event_id(tipo: str) -> str:
    """Genera un identificatore univoco formattato per gli eventi demo."""
    global _prossimo_id_evento
    _prossimo_id_evento += 1
    return f"{tipo}-{datetime.now().strftime('%Y%m%d')}-{_prossimo_id_evento}"


async def audit_event(
    request: Request,
    user: dict,
    action: str,
    target_table: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Persist an org-scoped audit event before acknowledging the operation."""
    repo = getattr(request.app.state, "repo", None)
    organization_id = user.get("organization_id")
    if repo is None or not organization_id:
        raise RuntimeError("Audit storage and organization scope are required")
    from src.core.db.repositories.billing_repo import BillingRepository
    await BillingRepository(repo.pool).enqueue_governance(organization_id, "audit", {
        "action": action, "user_id": user.get("user_id"), "auth_user_id": user.get("auth_user_id"),
        "target_table": target_table, "target_id": target_id, "details": details or {},
    })


async def enforce_org_rate_limit(
    organization_id: str | None, operation: str, limit: int, window_seconds: int
) -> None:
    """Cap expensive work by a tenant resolved from the authenticated user."""
    if not organization_id:
        raise HTTPException(status_code=403, detail="Organizzazione richiesta")
    from src.core.rate_limit import get_rate_limiter

    limiter = await get_rate_limiter()
    if await limiter.hit(f"org:{organization_id}:{operation}", limit, window_seconds):
        raise HTTPException(status_code=429, detail="Troppe richieste. Riprova più tardi.")


async def get_billing_snapshot(repo, organization_id: str | None) -> dict | None:
    """Recupera lo stato di billing del tenant per il routing AI budget-aware (Invariante 8)."""
    if repo is None or not organization_id:
        return None
    try:
        return await repo.get_organization_billing(organization_id)
    except Exception as e:
        logger.warning("billing_snapshot_unavailable org_id=%s error_type=%s",
                       organization_id, type(e).__name__)
        return None


async def record_ai_usage(
    repo,
    organization_id: str | None,
    task_type: str,
    user_text: str,
    billing: dict | None,
    metadata: dict | None = None,
) -> None:
    """Legacy sink disabled: planned routes and caller metrics are not evidence."""
    raise RuntimeError("AI usage must be recorded by the provider accounting adapter")


async def check_feature_blocked_by_plan(repo, org_id: str | None, feature: str) -> str | None:
    """Verifica se il piano corrente dell'organizzazione include la feature richiesta.

    Regole di accesso:
    1. Sospensione / Cancellazione: se l'abbonamento e' 'canceled', 'unpaid' o il trial e' scaduto,
       l'accesso alle feature a pagamento e' bloccato (fail-closed, Invariante 8).
    2. Trial attivo: se l'org e' in prova gratuita valida, opera con il tier 'pro' (Crescita).
    3. Piano sconosciuto / anomalo: se plan_slug e' valorizzato ma non corrisponde a una chiave
       in PLANS, blocca l'accesso e logga un warning (fail-closed contro drift di naming).
    4. Missing scope, billing or storage always denies privileged features.
    """
    if not repo or not org_id:
        return "Impossibile verificare l'abbonamento. Riprova più tardi."
    billing = await get_billing_snapshot(repo, org_id)
    if not billing:
        return "Impossibile verificare l'abbonamento. Riprova più tardi."

    if billing.get("ai_accounting_blocked"):
        return "Contabilità AI in aggiornamento. Riprova più tardi."

    status = billing.get("subscription_status")
    trial_end = billing.get("trial_end")

    # 1. Verifica sospensione abbonamento / scadenza trial (Invariante 8)
    from src.core.billing.suspension import is_org_suspended
    if is_org_suspended(status, trial_end) or status in ("canceled", "unpaid", "incomplete_expired"):
        return "Abbonamento sospeso o scaduto. Rinnova l'abbonamento per accedere a questa funzionalità."

    # 2. Risoluzione piano effettivo
    plan_slug = billing.get("plan")
    if status == "trialing" and not plan_slug:
        plan_slug = "pro"

    # 3. Mappatura piano e blocco fail-closed su plan_slug inatteso
    from src.core.billing.plans import PLANS
    plan = PLANS.get(plan_slug)
    if not plan:
        logger.warning(
            "[feature_gating] piano sconosciuto '%s' per org=%s (status=%s): blocco prudenziale fail-closed",
            plan_slug, org_id, status,
        )
        return f"Configurazione piano '{plan_slug}' non riconosciuta. Contatta l'assistenza per verificare l'abbonamento."

    # Unknown capabilities must not accidentally authorize future features.
    if feature not in {"rag", "recensioni"}:
        return "Funzionalità non riconosciuta."
    # 4. Verifica capabilities del piano
    if feature == "rag" and not plan.has_rag:
        return "Knowledge Base AI non disponibile con l'abbonamento corrente."
    if feature == "recensioni" and not plan.has_reviews:
        return f"Il piano {plan.name} non include la gestione delle recensioni. Effettua l'upgrade per abilitarla."
    return None


# ── Risolutori Dinamici per la Backward Compatibility dei Mock di Test ─────

def resolve_vettorizza():
    """Risolve vettorizza garantendo che i patch su src.api.main.vettorizza abbiano effetto."""
    main_mod = sys.modules.get("src.api.main")
    if main_mod and hasattr(main_mod, "vettorizza"):
        return getattr(main_mod, "vettorizza")
    from src.core.documenti.embeddings import vettorizza
    return vettorizza


def resolve_estrai_da_url():
    """Risolve estrai_da_url garantendo che i patch su src.api.main.estrai_da_url abbiano effetto."""
    main_mod = sys.modules.get("src.api.main")
    if main_mod and hasattr(main_mod, "estrai_da_url"):
        return getattr(main_mod, "estrai_da_url")
    from src.core.documenti.web_extractor import estrai_da_url
    return estrai_da_url


def resolve_genera_risposta_recensione():
    """Risolve genera_risposta_recensione garantendo che i patch su src.api.main abbiano effetto."""
    main_mod = sys.modules.get("src.api.main")
    if main_mod and hasattr(main_mod, "genera_risposta_recensione"):
        return getattr(main_mod, "genera_risposta_recensione")
    from src.core.crew_runner_review import genera_risposta_recensione
    return genera_risposta_recensione
