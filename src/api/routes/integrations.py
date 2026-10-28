"""Integrations, Channel Connectivity Testing & Audit Log API Routes (Invarianti 1, 2, 10).

Gestisce:
- Stato delle integrazioni canali (WhatsApp, Instagram, Webhook Meta)
- Test di connettività credenziali canali (WhatsApp, Instagram, Calendar, Webhook)
- Consultazione sicura e org-scoped dell'audit log
"""

import json
import logging
import os
from typing import Any
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.api.dependencies import get_external_booking_repo, get_repo, require_ruolo
from src.core.bookings.router import BookingMode
from src.core.auth.dependencies import require_mfa

logger = logging.getLogger(__name__)

router = APIRouter(tags=["integrations"])


# ── Audit log: lettura org-scoped ──────────────────────────────────────

@router.get("/api/audit")
async def lista_audit(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Restituisce gli eventi di audit log per l'organizzazione autenticata."""
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(401, "Nessuna organizzazione collegata")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    repo = get_repo(request)
    async with repo.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.action, a.target_table, a.target_id, a.details,
                   a.created_at,
                   u.email AS user_email
            FROM audit_log a
            LEFT JOIN user_profiles u ON u.id = a.user_id
            WHERE a.organization_id = $1::uuid
            ORDER BY a.created_at DESC
            LIMIT $2 OFFSET $3
        """, org_id, limit + 1, offset)
    has_more = len(rows) > limit
    return {
        "eventi": [
            {
                "action": r["action"],
                "target_table": r["target_table"],
                "target_id": r["target_id"],
                "details": json.loads(r["details"]) if isinstance(r["details"], str) else (r["details"] or {}),
                "created_at": r["created_at"].isoformat(),
                "user_email": r["user_email"],
            }
            for r in rows[:limit]
        ],
        "has_more": has_more,
    }


# ── Integrazioni: stato canali e webhook ───────────────────────────────

@router.get("/api/integrazioni/stato")
async def stato_integrazioni(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Restituisce lo stato di connessione dei canali esterni per l'organizzazione."""
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(401, "Nessuna organizzazione collegata")
    repo = get_repo(request)
    async with repo.pool.acquire() as conn:
        wa = await conn.fetchrow("""
            SELECT phone_number_id, waba_id, updated_at
            FROM whatsapp_accounts WHERE organization_id = $1::uuid
        """, org_id)
        ig = await conn.fetchrow("""
            SELECT ig_user_id, updated_at
            FROM instagram_accounts WHERE organization_id = $1::uuid
        """, org_id)
    return {
        "whatsapp": {
            "connesso": wa is not None,
            "phone_number_id": wa["phone_number_id"] if wa else None,
            "aggiornato": wa["updated_at"].isoformat() if wa and wa["updated_at"] else None,
        },
        "instagram": {
            "connesso": ig is not None,
            "ig_user_id": ig["ig_user_id"] if ig else None,
            "aggiornato": ig["updated_at"].isoformat() if ig and ig["updated_at"] else None,
        },
        "webhook_meta": {
            "configurato": bool(os.getenv("META_APP_SECRET")) and bool(os.getenv("META_VERIFY_TOKEN")),
        },
    }


@router.post("/api/integrazioni/test/{canale}")
async def test_integrazione(
    canale: str,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Esegue un test di connettività verso le API esterne del canale specificato."""
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(401, "Nessuna organizzazione collegata")

    repo = get_repo(request)
    encryption_key = os.getenv("ENCRYPTION_KEY", "")

    if canale == "whatsapp":
        async with repo.pool.acquire() as conn:
            wa = await conn.fetchrow(
                "SELECT phone_number_id, waba_id, access_token FROM whatsapp_accounts WHERE organization_id = $1::uuid",
                org_id,
            )
        if not wa:
            return {
                "canale": "whatsapp",
                "status": "disconnected",
                "success": False,
                "message": "Nessun account WhatsApp collegato.",
            }

        token = wa["access_token"]
        if encryption_key:
            try:
                from cryptography.fernet import Fernet
                cipher = Fernet(encryption_key.encode())
                token = cipher.decrypt(token.encode()).decode()
            except Exception:
                pass

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                res = await client.get(
                    f"https://graph.facebook.com/v21.0/{wa['phone_number_id']}",
                    params={"fields": "display_phone_number,verified_name", "access_token": token},
                )
                if res.status_code == 200:
                    data = res.json()
                    name = data.get("verified_name") or data.get("display_phone_number") or wa["phone_number_id"]
                    return {
                        "canale": "whatsapp",
                        "status": "connected",
                        "success": True,
                        "message": f"WhatsApp Business verificato ({name})",
                        "details": data,
                    }
                elif res.status_code in (400, 401, 403):
                    return {
                        "canale": "whatsapp",
                        "status": "expired_token",
                        "success": False,
                        "message": "Token Meta scaduto o non valido. Aggiorna il token.",
                    }
                else:
                    return {
                        "canale": "whatsapp",
                        "status": "error",
                        "success": False,
                        "message": f"Errore Meta Graph API (HTTP {res.status_code}).",
                    }
        except (httpx.TimeoutException, httpx.ConnectError):
            return {
                "canale": "whatsapp",
                "status": "error",
                "success": False,
                "message": "Timeout durante la verifica Meta Graph API.",
            }

    elif canale == "instagram":
        async with repo.pool.acquire() as conn:
            ig = await conn.fetchrow(
                "SELECT ig_user_id, access_token FROM instagram_accounts WHERE organization_id = $1::uuid",
                org_id,
            )
        if not ig:
            return {
                "canale": "instagram",
                "status": "disconnected",
                "success": False,
                "message": "Nessun account Instagram collegato.",
            }

        token = ig["access_token"]
        if encryption_key:
            try:
                from cryptography.fernet import Fernet
                cipher = Fernet(encryption_key.encode())
                token = cipher.decrypt(token.encode()).decode()
            except Exception:
                pass

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                res = await client.get(
                    f"https://graph.facebook.com/v21.0/{ig['ig_user_id']}",
                    params={"fields": "id,username,name", "access_token": token},
                )
                if res.status_code == 200:
                    data = res.json()
                    username = data.get("username") or ig["ig_user_id"]
                    return {
                        "canale": "instagram",
                        "status": "connected",
                        "success": True,
                        "message": f"Instagram Direct verificato (@{username})",
                        "details": data,
                    }
                elif res.status_code in (400, 401, 403):
                    return {
                        "canale": "instagram",
                        "status": "expired_token",
                        "success": False,
                        "message": "Token Instagram scaduto o non valido.",
                    }
                else:
                    return {
                        "canale": "instagram",
                        "status": "error",
                        "success": False,
                        "message": f"Errore Instagram API (HTTP {res.status_code}).",
                    }
        except (httpx.TimeoutException, httpx.ConnectError):
            return {
                "canale": "instagram",
                "status": "error",
                "success": False,
                "message": "Timeout durante la verifica Instagram API.",
            }

    elif canale == "calendar":
        async with repo.pool.acquire() as conn:
            cal = await conn.fetchrow(
                "SELECT * FROM google_calendar_credentials WHERE organization_id = $1::uuid",
                org_id,
            )
        if not cal:
            return {
                "canale": "calendar",
                "status": "disconnected",
                "success": False,
                "message": "Nessun account Google Calendar collegato.",
            }

        token = cal["access_token"]
        if encryption_key:
            try:
                from cryptography.fernet import Fernet
                cipher = Fernet(encryption_key.encode())
                token = cipher.decrypt(token.encode()).decode()
            except Exception:
                pass

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                res = await client.get(
                    "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if res.status_code == 200:
                    return {
                        "canale": "calendar",
                        "status": "connected",
                        "success": True,
                        "message": "Sincronizzazione Google Calendar verificata e attiva.",
                        "details": {"calendar_id": cal["calendar_id"], "sync_enabled": cal["sync_enabled"]},
                    }
                elif res.status_code in (401, 403):
                    return {
                        "canale": "calendar",
                        "status": "expired_token",
                        "success": False,
                        "message": "Token Google scaduto o revocato. Riconnetti l'agenda Google.",
                    }
                else:
                    return {
                        "canale": "calendar",
                        "status": "error",
                        "success": False,
                        "message": f"Risposta Google Calendar: HTTP {res.status_code}",
                    }
        except (httpx.TimeoutException, httpx.ConnectError):
            return {
                "canale": "calendar",
                "status": "error",
                "success": False,
                "message": "Timeout durante la verifica di Google Calendar.",
            }

    elif canale == "webhook":
        secret_ok = bool(os.getenv("META_APP_SECRET"))
        verify_ok = bool(os.getenv("META_VERIFY_TOKEN"))
        if secret_ok and verify_ok:
            return {
                "canale": "webhook",
                "status": "connected",
                "success": True,
                "message": "Endpoint webhook attivo con validazione HMAC-SHA256.",
            }
        else:
            return {
                "canale": "webhook",
                "status": "pending_verification",
                "success": False,
                "message": "Credenziali Webhook mancanti sul server.",
            }

    raise HTTPException(400, f"Canale non supportato: {canale}")


# ── Integrazioni Gestionale di Prenotazione (PMS / Booking System) ─────────

class ConfigureBookingIntegrationRequest(BaseModel):
    """Payload per configurazione o aggiornamento delle credenziali del gestionale."""
    provider: str = Field(description="Identificativo del gestionale: simplybook, zak, fake, internal")
    credentials: dict[str, Any] = Field(default_factory=dict, description="Envelope credenziali cifrate at-rest")
    mode: str = Field(default="authoritative", description="Modalità operativa: authoritative, shadow, mirror, local_only")
    config: dict[str, Any] = Field(default_factory=dict, description="Parametri di configurazione (es. medical_dpa_signed, timeout_seconds)")
    is_active: bool = True


class UpdateBookingModeRequest(BaseModel):
    """Payload per la modifica della modalità operativa dell'integrazione."""
    mode: str = Field(description="Nuova modalità operativa: authoritative, shadow, mirror, local_only")


@router.post("/api/v1/integrations/booking")
async def configura_integrazione_booking(
    req: ConfigureBookingIntegrationRequest,
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    mfa: dict = Depends(require_mfa()),
):
    """Configura o aggiorna le credenziali e la modalità per il gestionale di prenotazioni.

    Invarianti:
    - Invariante 1 (Tenant Isolation): Esegue la scrittura strettamente sull'organization_id autenticato.
    - Invariante 10 (Sicurezza Segreti): Cifra le credenziali at-rest e non le restituisce mai in chiaro.
    - Vincolo di Governance: WuBook ZaK (o alias) non è certificato per produzione in modalità authoritative/mirror.
    """
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(401, "Nessuna organizzazione collegata")

    provider = req.provider.strip().lower()
    mode_str = req.mode.strip().lower()

    # Validazione modalità
    try:
        mode = BookingMode(mode_str)
    except ValueError:
        raise HTTPException(
            400,
            detail=f"Modalità operativa non valida: {req.mode}. Valori ammessi: authoritative, shadow, mirror, local_only",
        )

    # Vincolo tassativo di governance per WuBook ZaK (e suoi alias)
    if provider in ("zak", "wubook", "wubook_zak") and mode not in (BookingMode.SHADOW, BookingMode.LOCAL_ONLY):
        raise HTTPException(
            400,
            detail=(
                "L'integrazione con WuBook ZaK è attualmente un template architetturale e non è ancora certificata "
                "per la produzione in modalità authoritative/mirror. È consentita solo la modalità 'shadow' o 'local_only'."
            ),
        )

    ext_repo = get_external_booking_repo(request)
    full_config = {**req.config, "mode": mode.value}

    res = await ext_repo.save_credentials(
        organization_id=org_id,
        provider=provider,
        credentials=req.credentials,
        config=full_config,
        is_active=req.is_active,
    )

    # Invariante 10: Mai restituire credenziali in chiaro nella risposta
    return {
        "success": True,
        "message": "Integrazione gestionale di prenotazione configurata con successo.",
        "provider": res.get("provider"),
        "mode": res.get("config", {}).get("mode", mode.value),
        "is_active": res.get("is_active", True),
        "updated_at": res.get("updated_at"),
    }


@router.get("/api/v1/integrations/booking/status")
async def stato_integrazione_booking(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Restituisce lo stato di configurazione, modalità e salute del gestionale senza esporre credenziali.

    Invarianti:
    - Invariante 1 (Tenant Isolation): Lettura limitata all'organizzazione del richiedente.
    - Invariante 10 (Sicurezza Segreti): Le credenziali decifrate non vengono MAI serializzate nella risposta.
    """
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(401, "Nessuna organizzazione collegata")

    ext_repo = get_external_booking_repo(request)
    creds_row = await ext_repo.get_credentials(org_id)

    if not creds_row or not creds_row.get("is_active", True):
        return {
            "is_configured": False,
            "provider": None,
            "mode": "local_only",
            "is_active": False,
            "circuit_breaker": {"state": "CLOSED", "failure_count": 0},
            "last_sync": None,
            "medical_dpa_signed": False,
        }

    config = creds_row.get("config") or {}
    mode = config.get("mode", "authoritative")
    provider = creds_row.get("provider")
    medical_dpa_signed = (config.get("medical_dpa_signed") is True)

    last_sync_record = await ext_repo.get_latest_sync(org_id)
    last_sync = None
    if last_sync_record:
        updated_val = last_sync_record.get("updated_at")
        last_sync = {
            "id": str(last_sync_record.get("id")),
            "status": last_sync_record.get("sync_status"),
            "external_booking_id": last_sync_record.get("external_booking_id"),
            "sync_error": last_sync_record.get("sync_error"),
            "retry_count": last_sync_record.get("retry_count", 0),
            "updated_at": updated_val.isoformat() if hasattr(updated_val, "isoformat") else str(updated_val),
        }

    return {
        "is_configured": True,
        "provider": provider,
        "mode": mode,
        "is_active": creds_row.get("is_active", True),
        "medical_dpa_signed": medical_dpa_signed,
        "circuit_breaker": {
            "state": "CLOSED",
            "threshold": config.get("circuit_breaker_threshold", 5),
            "timeout_seconds": config.get("timeout_seconds", 4.0),
        },
        "last_sync": last_sync,
        "updated_at": creds_row.get("updated_at"),
    }


@router.patch("/api/v1/integrations/booking/mode")
async def aggiorna_modalita_booking(
    req: UpdateBookingModeRequest,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Aggiorna la modalità operativa per l'integrazione di prenotazione corrente."""
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(401, "Nessuna organizzazione collegata")

    mode_str = req.mode.strip().lower()
    try:
        new_mode = BookingMode(mode_str)
    except ValueError:
        raise HTTPException(
            400,
            detail=f"Modalità operativa non valida: {req.mode}. Valori ammessi: authoritative, shadow, mirror, local_only",
        )

    ext_repo = get_external_booking_repo(request)
    existing = await ext_repo.get_credentials(org_id)
    if not existing:
        raise HTTPException(404, "Nessuna integrazione di prenotazione configurata per questa organizzazione")

    provider = existing.get("provider", "").lower()
    # Vincolo tassativo di governance per ZaK
    if provider in ("zak", "wubook", "wubook_zak") and new_mode not in (BookingMode.SHADOW, BookingMode.LOCAL_ONLY):
        raise HTTPException(
            400,
            detail=(
                "L'integrazione con WuBook ZaK è attualmente un template architetturale e non è ancora certificata "
                "per la produzione in modalità authoritative/mirror. È consentita solo la modalità 'shadow' o 'local_only'."
            ),
        )

    updated = await ext_repo.update_mode(org_id, new_mode.value)
    if updated is None:
        raise HTTPException(
            409,
            "Configurazione booking cambiata durante l'operazione; aggiorna e riprova.",
        )
    return {
        "success": True,
        "message": f"Modalità operativa aggiornata a '{new_mode.value}'.",
        "provider": updated.get("provider", provider),
        "mode": new_mode.value,
        "updated_at": updated.get("updated_at"),
    }


@router.delete("/api/v1/integrations/booking")
async def elimina_integrazione_booking(
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    mfa: dict = Depends(require_mfa()),
):
    """Rimuove l'integrazione di prenotazione e le credenziali cifrate per l'organizzazione corrente."""
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(401, "Nessuna organizzazione collegata")

    ext_repo = get_external_booking_repo(request)
    deleted = await ext_repo.delete_credentials(org_id)
    return {
        "success": True,
        "message": "Integrazione gestionale di prenotazione rimossa con successo.",
        "deleted": deleted,
    }

