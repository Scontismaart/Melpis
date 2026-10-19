"""Dashboard and Reporting API Routes (Invarianti 1, 2, 9).

Gestisce:
- Eventi aggregati per la Panoramica tenant (/api/dashboard)
- Eventi prioritari filtrati direttamente in SQL (/api/dashboard/prioritari)
- Report giornaliero (/api/report e /api/report/stato)
- Report settimanale automatizzato (/api/report/settimanale)
- Esportazione CSV prenotazioni completate (/api/report/csv)
"""

import json
import logging
import uuid
import asyncio
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.api.dependencies import require_ruolo
from src.core.crew_runner_report import genera_report as genera_report_completo
from src.core.scheduler import get_report_cache, set_report_cache
from src.core.provider_accounting import ProviderAccountingContext
from src.models.schemas import EventoDashboard, ReportOutput

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

# Finestra e tetto del result set della Panoramica: senza, l'endpoint
# scaricherebbe l'intera storia dell'org a ogni poll di 5 secondi
# (client: web/app.js avviaPanoramicaPolling). La UI usa una sparkline
# di 7 giorni, quindi 30 giorni coprono con margine.
DASHBOARD_EVENTI_WINDOW_DAYS = 30
DASHBOARD_EVENTI_MAX = 500


# CTE condiviso Panoramica: eventi unificati event_log + messages + reviews,
# con finestra temporale $2 (giorni). Parametri: $1 = organization_id, $2 = giorni.
_DASHBOARD_EVENTI_CTE = """\nWITH raw_events AS (
        -- 1. Eventi già registrati in event_log (con arricchimento risposta outbound se vuota)
        SELECT
            e.id::text AS id,
            e.tipo_evento,
            e.created_at AS timestamp,
            e.priorita,
            e.testo_originale,
            COALESCE(
                NULLIF(e.risposta_ai, ''),
                (SELECT out_m.content_text 
                 FROM messages out_m 
                 WHERE e.dettagli->>'conversation_id' IS NOT NULL
                   AND out_m.organization_id = $1
                   AND out_m.conversation_id = (e.dettagli->>'conversation_id')::uuid 
                   AND out_m.direction = 'outbound' 
                   AND out_m.created_at >= e.created_at 
                 ORDER BY out_m.created_at ASC LIMIT 1),
                ''
            ) AS risposta_ai,
            e.gestito_da_ai,
            e.dettagli
        FROM event_log e
        WHERE e.organization_id = $1
          AND e.created_at >= NOW() - make_interval(days => $2)
          AND e.tipo_evento IN ('messaggio', 'recensione')
        
        UNION ALL
        
        -- 2. Messaggi Inbound da WhatsApp / Instagram / Canali
        SELECT
            m.id::text AS id,
            'messaggio' AS tipo_evento,
            m.created_at AS timestamp,
            CASE 
                WHEN m.handling_type = 'escalated' THEN 'alta'
                ELSE 'media'
            END AS priorita,
            m.content_text AS testo_originale,
            COALESCE(
                (SELECT out_m.content_text 
                 FROM messages out_m 
                 WHERE out_m.conversation_id = m.conversation_id 
                   AND out_m.organization_id = $1
                   AND out_m.direction = 'outbound' 
                   AND out_m.created_at >= m.created_at 
                 ORDER BY out_m.created_at ASC LIMIT 1),
                (m.ai_reply_cache->>'text'),
                ''
            ) AS risposta_ai,
            CASE 
                WHEN m.handling_type = 'escalated' THEN false
                ELSE true
            END AS gestito_da_ai,
            jsonb_build_object('conversation_id', m.conversation_id::text, 'status', m.status) AS dettagli
        FROM messages m
        WHERE m.organization_id = $1
          AND m.direction = 'inbound'
          AND m.deleted_at IS NULL
          AND m.created_at >= NOW() - make_interval(days => $2)
          AND NOT EXISTS (
              SELECT 1 FROM event_log e 
              WHERE e.organization_id = m.organization_id 
                AND e.organization_id = $1
                AND e.source_id = m.id
          )
        
        UNION ALL
        
        -- 3. Recensioni non ancora in event_log
        SELECT
            r.id::text AS id,
            'recensione' AS tipo_evento,
            r.created_at AS timestamp,
            CASE 
                WHEN r.valutazione_stelle <= 2 THEN 'alta'
                WHEN r.valutazione_stelle = 3 THEN 'media'
                ELSE 'bassa'
            END AS priorita,
            r.testo AS testo_originale,
            '' AS risposta_ai,
            false AS gestito_da_ai,
            jsonb_build_object('stelle', r.valutazione_stelle, 'autore', r.autore, 'fonte', r.fonte) AS dettagli
        FROM reviews r
        WHERE r.organization_id = $1
          AND r.created_at >= NOW() - make_interval(days => $2)
          AND NOT EXISTS (
              SELECT 1 FROM event_log e 
              WHERE e.organization_id = r.organization_id 
                AND e.organization_id = $1
                AND e.source_id = r.id
          )
    )
"""


async def recupera_eventi_dashboard(pool, org_id: str | None) -> list[EventoDashboard]:
    """Recupera la lista unificata degli eventi per la dashboard del tenant (event_log + messages + reviews)."""
    if not pool or not org_id:
        return []

    try:
        org_uuid = uuid.UUID(str(org_id))
    except (ValueError, TypeError):
        return []

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _DASHBOARD_EVENTI_CTE + """
                SELECT * FROM (
                    SELECT DISTINCT ON (id) *
                    FROM raw_events
                    ORDER BY id, timestamp DESC
                ) dedup
                ORDER BY timestamp DESC
                LIMIT $3
            """, org_uuid, DASHBOARD_EVENTI_WINDOW_DAYS, DASHBOARD_EVENTI_MAX)

        eventi: list[EventoDashboard] = []
        for r in rows:
            dettagli = json.loads(r["dettagli"]) if isinstance(r["dettagli"], str) else (r["dettagli"] or {})
            eventi.append(EventoDashboard(
                id=str(r["id"]),
                tipo_evento=r["tipo_evento"] if r["tipo_evento"] in ("messaggio", "recensione") else "messaggio",
                timestamp=r["timestamp"],
                priorita=r["priorita"] if r["priorita"] in ("alta", "media", "bassa") else "media",
                testo_originale=r["testo_originale"] or "",
                risposta_ai=r["risposta_ai"] or "",
                gestito_da_ai=bool(r["gestito_da_ai"]),
                dettagli=dettagli,
            ))
        eventi.sort(key=lambda e: e.timestamp, reverse=True)
        return eventi
    except Exception as e:
        logger.error("Errore recupero eventi dashboard per org %s: %s", org_id, e)
        return []


async def recupera_eventi_prioritari(pool, org_id: str | None, limite: int) -> list[EventoDashboard]:
    """Eventi priorita' alta/media per la colonna Prioritari della
    Panoramica: filtro, ordinamento (alta prima, poi timestamp crescente)
    e LIMIT eseguiti dal database, senza rieseguire la query completa."""
    if not pool or not org_id:
        return []
    try:
        org_uuid = uuid.UUID(str(org_id))
    except (ValueError, TypeError):
        return []
    limite = max(1, min(int(limite or 5), 50))
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _DASHBOARD_EVENTI_CTE + """
                SELECT * FROM (
                    SELECT DISTINCT ON (id) *
                    FROM raw_events
                    WHERE priorita <> 'bassa'
                    ORDER BY id, timestamp DESC
                ) dedup
                ORDER BY CASE WHEN priorita = 'alta' THEN 0 ELSE 1 END,
                         timestamp ASC
                LIMIT $3
                """,
                org_uuid, DASHBOARD_EVENTI_WINDOW_DAYS, limite)
        return [
            EventoDashboard(
                id=str(r["id"]),
                tipo_evento=r["tipo_evento"] if r["tipo_evento"] in ("messaggio", "recensione") else "messaggio",
                timestamp=r["timestamp"],
                priorita=r["priorita"] if r["priorita"] in ("alta", "media", "bassa") else "media",
                testo_originale=r["testo_originale"] or "",
                risposta_ai=r["risposta_ai"] or "",
                gestito_da_ai=bool(r["gestito_da_ai"]),
                dettagli=json.loads(r["dettagli"]) if isinstance(r["dettagli"], str) else (r["dettagli"] or {}),
            )
            for r in rows
        ]
    except Exception as e:
        logger.error("Errore recupero prioritari per org %s: %s", org_id, e)
        return []


# ── Endpoint Dashboard & Panoramica ────────────────────────────────────

@router.get("/api/dashboard", response_model=list[EventoDashboard])
async def ottieni_dashboard(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Restituisce la lista unificata degli eventi per la dashboard del tenant autenticato."""
    pool = getattr(request.app.state, "pool", None)
    org_id = user.get("organization_id")
    return await recupera_eventi_dashboard(pool, org_id)


@router.get("/api/dashboard/prioritari", response_model=list[EventoDashboard])
async def ottieni_eventi_prioritari(
    request: Request,
    limite: int = 5,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Restituisce gli eventi a priorità alta/media ordinati con alta per prima."""
    pool = getattr(request.app.state, "pool", None)
    org_id = user.get("organization_id")
    return await recupera_eventi_prioritari(pool, org_id, limite)


# ── Endpoint Report ───────────────────────────────────────────────────

@router.get("/api/report", response_model=ReportOutput)
async def ottieni_report(
    request: Request,
    forza: bool = False,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Genera o recupera dalla cache il report giornaliero per l'organizzazione."""
    pool = getattr(request.app.state, "pool", None)
    org_id = user.get("organization_id")
    eventi = await recupera_eventi_dashboard(pool, org_id)
    oggi = datetime.now().strftime("%Y-%m-%d")

    cache_key = f"{org_id}:{oggi}" if org_id else oggi
    if not forza:
        cached = get_report_cache(cache_key)
        if cached:
            return cached

    provider_context = None
    report_generated = False
    try:
        provider_context = await ProviderAccountingContext.open(
            pool,
            org_id,
            task_type="report",
            actor_auth_user_id=user.get("auth_user_id"),
            context={"trace_id": getattr(request.state, "trace_id", None)},
        )
        report = await asyncio.to_thread(genera_report_completo, eventi, provider_context)
        report_generated = True
    except Exception as e:
        logger.warning("report_ai_unavailable org_id=%s error_type=%s", org_id, type(e).__name__)
        # Dashboard access stays available; this is a deterministic summary,
        # not an AI generation, so billing/accounting failure cannot trigger a call.
        report = genera_report_completo(eventi)
    finally:
        if provider_context is not None:
            await provider_context.close()

    if report_generated:
        set_report_cache(cache_key, report)
    return report


@router.get("/api/report/stato")
def stato_report(user: dict = Depends(require_ruolo("owner", "manager", "staff"))):
    """Verifica se il report giornaliero è già pronto in cache."""
    oggi = datetime.now().strftime("%Y-%m-%d")
    report = get_report_cache(oggi)
    return {"disponibile": report is not None, "id": f"report-{oggi}" if report else None}


@router.get("/api/report/settimanale")
async def report_settimanale(
    forza: bool = False,
    request: Request = None,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Genera (e invia via email) il report settimanale per l'organizzazione
    dell'utente. Idempotente: non reinvia se gia' inviato per lo stesso
    periodo, a meno che forza=true."""
    from src.core.report.weekly_report import genera_e_invia_report_settimanale

    pool = getattr(request.app.state, "pool", None)
    if not pool:
        raise HTTPException(status_code=503, detail="Database non disponibile")

    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="Organizzazione non trovata")

    try:
        risultato = await genera_e_invia_report_settimanale(pool, str(org_id), forza=forza)
    except Exception as e:
        logger.error("Errore generazione report settimanale: %s", e)
        raise HTTPException(status_code=502, detail="Errore durante la generazione o invio del report settimanale")

    return risultato


@router.get("/api/report/csv")
async def export_csv_prenotazioni(
    da: str = None,
    a: str = None,
    request: Request = None,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Export CSV delle prenotazioni completate nel periodo.
    Default: ultima settimana (lunedi-domenica precedente)."""
    from src.core.report.csv_export import get_prenotazioni_completate, genera_csv
    from src.core.report.weekly_report import _calcola_periodo_settimanale

    pool = getattr(request.app.state, "pool", None)
    if not pool:
        raise HTTPException(status_code=503, detail="Database non disponibile")

    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="Organizzazione non trovata")

    # Date del periodo: default = settimana precedente
    if da and a:
        try:
            inizio = date.fromisoformat(da)
            fine = date.fromisoformat(a)
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato date non valido (YYYY-MM-DD)")
    else:
        inizio, fine = _calcola_periodo_settimanale()

    prenotazioni = await get_prenotazioni_completate(pool, str(org_id), inizio, fine)
    csv_bytes = genera_csv(prenotazioni)

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="prenotazioni-{inizio.isoformat()}.csv"',
        },
    )
