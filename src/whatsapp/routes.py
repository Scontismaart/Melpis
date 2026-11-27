import logging
import os
import httpx
from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from src.core.auth.dependencies import require_mfa, require_ruolo
from src.core.channels.sandbox_policy import assert_recipient_allowed
from src.whatsapp.config import AppConfig, TenantConfig
from src.whatsapp.repository import Repository as WhatsAppRepository
from src.whatsapp.service import WhatsAppService

logger = logging.getLogger(__name__)


class CredentialSafeRoute(APIRoute):
    """FastAPI validation errors must never echo submitted Meta credentials."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                raise HTTPException(422, "Dati WhatsApp non validi. Controlla i campi richiesti.")

        return handler


router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp-settings"], route_class=CredentialSafeRoute)


def _get_wrepo(request: Request) -> WhatsAppRepository:
    pool = request.app.state.pool
    if pool is None:
        raise HTTPException(500, "Database non disponibile")
    return WhatsAppRepository(pool=pool)


class WhatsAppAccountRequest(BaseModel):
    phone_number_id: str = Field(min_length=1, max_length=64, pattern=r"^[0-9]+$", description="Phone Number ID da Meta Business Suite")
    waba_id: str = Field(min_length=1, max_length=64, pattern=r"^[0-9]+$", description="WhatsApp Business Account ID")
    access_token: str = Field(min_length=1, max_length=8192, description="System User Access Token di Meta")
    display_phone_number: Optional[str] = Field(default=None, max_length=32)


class WhatsAppTestMessageRequest(BaseModel):
    to_phone: Optional[str] = Field(default=None, max_length=32, description="Numero di telefono destinatario del test (es. +393401234567)")
    idempotency_key: Optional[UUID] = None


@router.get("/settings")
@router.get("/account")
async def get_whatsapp_settings(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Restituisce lo stato dell'account WhatsApp collegato e la disponibilità del webhook."""
    org_id = user["organization_id"]
    wrepo = _get_wrepo(request)
    cfg = await wrepo.get_tenant_config(org_id)
    
    secret_ok = bool(os.getenv("META_APP_SECRET"))
    verify_ok = bool(os.getenv("META_VERIFY_TOKEN"))
    webhook_active = secret_ok and verify_ok

    if not cfg:
        return {
            "connesso": False,
            "status": "disconnected",
            "phone_number_id": None,
            "waba_id": None,
            "display_phone_number": None,
            "verified_name": None,
            "webhook_active": webhook_active,
            "message": "Nessun numero WhatsApp collegato.",
        }

    return {
        "connesso": True,
        "status": "connected",
        "phone_number_id": cfg["phone_number_id"],
        "waba_id": cfg.get("waba_id"),
        "display_phone_number": cfg["phone_number_id"],
        "webhook_active": webhook_active,
        "message": "Credenziali WhatsApp collegate. Webhook e consegna da verificare.",
    }


@router.post("/connect")
@router.post("/account")
async def connect_whatsapp_account(
    body: WhatsAppAccountRequest,
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    mfa: dict = Depends(require_mfa()),
):
    """Collega il numero WhatsApp Business dell'attività verificando le credenziali su Meta."""
    org_id = user["organization_id"]
    wrepo = _get_wrepo(request)
    phone_number_id = body.phone_number_id.strip()
    waba_id = body.waba_id.strip()
    access_token = body.access_token.strip()

    # Verifica preliminare su Meta Graph API
    verified_name = None
    display_phone_number = body.display_phone_number
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0)) as client:
            res = await client.get(
                f"https://graph.facebook.com/v21.0/{phone_number_id}",
                params={"fields": "display_phone_number,verified_name,code_verification_status"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if res.status_code == 200:
                data = res.json()
                if not isinstance(data, dict) or str(data.get("id", "")) != phone_number_id:
                    raise HTTPException(503, "Risposta Meta non valida. Configurazione non salvata.")
                verified_name = data.get("verified_name")
                if data.get("display_phone_number"):
                    display_phone_number = data.get("display_phone_number")
                ownership = await client.get(
                    f"https://graph.facebook.com/v21.0/{waba_id}/phone_numbers",
                    params={"fields": "id", "limit": 100},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if ownership.status_code != 200:
                    raise HTTPException(503, "Verifica dell'account Meta non disponibile. Configurazione non salvata.")
                ownership_data = ownership.json()
                if not isinstance(ownership_data, dict) or not isinstance(ownership_data.get("data"), list):
                    raise HTTPException(503, "Risposta Meta non valida. Configurazione non salvata.")
                numbers = ownership_data["data"]
                if any(not isinstance(number, dict) or not isinstance(number.get("id"), (str, int)) for number in numbers):
                    raise HTTPException(503, "Risposta Meta non valida. Configurazione non salvata.")
                if not any(str(number.get("id")) == phone_number_id for number in numbers if isinstance(number, dict)):
                    raise HTTPException(400, "Il numero non appartiene all'account WhatsApp indicato.")
            elif res.status_code in (400, 401, 403):
                raise HTTPException(
                    status_code=400,
                    detail="Verifica Meta non riuscita. Controlla Phone Number ID e Token.",
                )
            else:
                raise HTTPException(503, "Verifica Meta temporaneamente non disponibile. Riprova.")
    except (httpx.RequestError, ValueError):
        logger.warning("meta_graph_verification_unavailable org_id=%s", org_id)
        raise HTTPException(503, "Verifica Meta temporaneamente non disponibile. Riprova.")

    try:
        await wrepo.save_tenant_config(org_id, phone_number_id, waba_id, access_token)
    except Exception:
        logger.error("whatsapp_save_failed org_id=%s", org_id)
        raise HTTPException(status_code=500, detail="Impossibile salvare la configurazione WhatsApp.")

    # Tentativo di sottoscrizione automatica WABA al webhook dell'app
    webhook_subscription_active = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            subscription = await client.post(
                f"https://graph.facebook.com/v21.0/{waba_id}/subscribed_apps",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if subscription.status_code == 200:
                subscription_data = subscription.json()
                webhook_subscription_active = (
                    isinstance(subscription_data, dict)
                    and subscription_data.get("success") is True
                )
    except Exception:
        logger.warning("waba_subscribed_apps_warning org_id=%s", org_id)

    return {
        "ok": True,
        "status": "connected",
        "message": "Credenziali WhatsApp salvate. Ricezione e consegna da verificare.",
        "phone_number_id": phone_number_id,
        "waba_id": waba_id,
        "verified_name": verified_name,
        "display_phone_number": display_phone_number or phone_number_id,
        "webhook_subscription_active": webhook_subscription_active,
        "webhook_subscription_warning": None if webhook_subscription_active else "Sottoscrizione webhook non confermata su Meta.",
    }


@router.post("/disconnect")
@router.delete("/account")
async def disconnect_whatsapp_account(
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    mfa: dict = Depends(require_mfa()),
):
    """Disconnette il numero WhatsApp Business dell'organizzazione."""
    org_id = user["organization_id"]
    wrepo = _get_wrepo(request)
    deleted = await wrepo.delete_tenant_config(org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Nessun account WhatsApp collegato da disconnettere.")
    return {"ok": True, "deleted": True, "message": "Numero WhatsApp disconnesso con successo."}


@router.post("/send-test")
async def send_test_message(
    body: WhatsAppTestMessageRequest,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Invia un messaggio reale o verifica la capacità di invio su WhatsApp."""
    org_id = user["organization_id"]
    wrepo = _get_wrepo(request)
    cfg = await wrepo.get_tenant_config(org_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Nessun account WhatsApp collegato.")

    try:
        token = wrepo.decrypt_token(cfg["access_token"])
    except Exception:
        logger.warning("whatsapp_test_token_unavailable org_id=%s", org_id)
        raise HTTPException(status_code=503, detail="Credenziali WhatsApp non disponibili.")

    phone_number_id = cfg["phone_number_id"]
    to_phone = (body.to_phone or "").strip().replace(" ", "").replace("-", "").removeprefix("+")

    if to_phone:
        try:
            assert_recipient_allowed("whatsapp", to_phone)
        except ValueError:
            raise HTTPException(
                status_code=403,
                detail="Destinatario non autorizzato per il messaggio di test.",
            )
        if body.idempotency_key is None:
            raise HTTPException(status_code=422, detail="idempotency_key UUID obbligatorio per l'invio.")
        if not await wrepo.has_recent_whatsapp_inbound(org_id, to_phone):
            raise HTTPException(status_code=403, detail="Nessun messaggio WhatsApp recente da questo destinatario.")
        prefs = await wrepo.get_contact_prefs(org_id, to_phone)
        if not prefs or prefs.get("marketing_opt_out") or prefs.get("consent_status") == "withdrawn":
            raise HTTPException(status_code=403, detail="Destinatario non autorizzato per il messaggio di test.")
        app_config = AppConfig(
            app_secret=os.environ.get("META_APP_SECRET", ""),
            encryption_key=os.environ.get("ENCRYPTION_KEY", ""),
            postgres_dsn="",
            verify_token=os.environ.get("META_VERIFY_TOKEN", ""),
        )
        tenant_config = TenantConfig(
            organization_id=UUID(str(org_id)),
            phone_number_id=phone_number_id,
            waba_id=cfg.get("waba_id") or "",
            access_token=token,
        )
        payload = {
            "type": "text",
            "text": {"body": "👋 Ciao! Questo è un messaggio di prova inviato da Melpis AI. Il tuo canale WhatsApp Business è configurato e operativo!"},
        }
        try:
            result = await WhatsAppService(app_config, wrepo).send_whatsapp_message(
                org_id=tenant_config.organization_id,
                to_number=to_phone,
                payload=payload,
                category="marketing",
                meta_client=None,
                tenant_config=tenant_config,
                idempotency_key=f"manual-test:{body.idempotency_key}",
                handling_type="manual_test",
            )
        except WhatsAppService.MessageBlockedByOptOut:
            raise HTTPException(status_code=403, detail="Destinatario non autorizzato per il messaggio di test.")
        except WhatsAppService.MessageUsageExceeded:
            raise HTTPException(status_code=429, detail="Limite messaggi raggiunto.")
        except ValueError:
            raise HTTPException(status_code=409, detail="Chiave di invio già usata con un messaggio diverso.")
        except Exception:
            logger.warning("whatsapp_test_delivery_unconfirmed org_id=%s", org_id)
            return {
                "success": False,
                "status": "pending",
                "message": "Consegna non confermata. Riprova con la stessa chiave di invio.",
                "message_id": None,
            }
        wam_id = result.get("wam_id") if isinstance(result, dict) else None
        if wam_id and result.get("status") in {"sent", "delivered", "read"}:
            return {
                "success": True,
                "status": "sent",
                "message": "Messaggio di test inviato con successo.",
                "message_id": wam_id,
            }
        return {
            "success": False,
            "status": "pending",
            "message": "Consegna non confermata. Riprova con la stessa chiave di invio.",
            "message_id": None,
        }
    else:
        # Verifica di raggiungibilità Graph API
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                res = await client.get(
                    f"https://graph.facebook.com/v21.0/{phone_number_id}",
                    params={"fields": "display_phone_number,verified_name"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if res.status_code == 200:
                    d = res.json()
                    if not isinstance(d, dict) or str(d.get("id", "")) != phone_number_id:
                        return {"success": False, "status": "error", "message": "Risposta Meta non valida."}
                    return {
                        "success": True,
                        "status": "connected",
                        "message": "Credenziali WhatsApp verificate.",
                    }
                else:
                    return {
                        "success": False,
                        "status": "error",
                        "message": f"Verifica Meta non riuscita (HTTP {res.status_code}).",
                    }
        except (httpx.RequestError, ValueError, TypeError):
            return {
                "success": False,
                "status": "timeout",
                "message": "Impossibile contattare Meta Graph API.",
            }
