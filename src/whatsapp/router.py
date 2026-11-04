import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from fastapi import APIRouter, Request, Response, HTTPException, Query
from src.whatsapp.config import AppConfig
from src.whatsapp.models import IngoingWebhook
from src.whatsapp.idempotency import dedup_check
from src.core.channels.inbound.meta_security import MetaWebhookSecurity
from src.core.db.repositories.webhook_inbox_repo import WebhookInboxRepository
from pydantic import ValidationError

logger = logging.getLogger(__name__)

MAX_BODY_SIZE = 5 * 1024 * 1024  # 5 MB
TIMESTAMP_TOLERANCE = 300  # ±5 minuti per replay check


def create_router(app_config: AppConfig = None, repo = None, security: MetaWebhookSecurity = None):
    router = APIRouter(prefix="/webhooks", tags=["whatsapp"])

    default_secret = (app_config.app_secret if app_config and app_config.app_secret else None) or os.getenv("META_APP_SECRET", "")
    default_verify = (app_config.verify_token if app_config and app_config.verify_token else None) or os.getenv("META_VERIFY_TOKEN", "")
    webhook_sec = security or MetaWebhookSecurity(
        app_secret=default_secret,
        verify_token=default_verify,
        max_body_size=MAX_BODY_SIZE,
        timestamp_tolerance=TIMESTAMP_TOLERANCE,
    )

    @router.get("/whatsapp")
    async def verify_webhook(
        hub_mode: str = Query(None, alias="hub.mode"),
        hub_verify_token: str = Query(None, alias="hub.verify_token"),
        hub_challenge: str = Query(None, alias="hub.challenge"),
    ):
        verify_token_configured = (app_config.verify_token if app_config and app_config.verify_token else None) or os.getenv("META_VERIFY_TOKEN") or webhook_sec.verify_token
        return webhook_sec.verify_challenge(
            hub_mode=hub_mode,
            hub_verify_token=hub_verify_token,
            hub_challenge=hub_challenge,
            override_token=verify_token_configured,
        )

    @router.post("/whatsapp")
    async def receive_webhook(request: Request):
        active_repo = getattr(request.app.state, "wrepo", None) or repo
        active_secret = (app_config.app_secret if app_config and app_config.app_secret else None) or os.getenv("META_APP_SECRET") or webhook_sec.app_secret
        trace_id = getattr(request.state, "trace_id", uuid.uuid4().hex[:16])

        # Validazione unificata MetaWebhookSecurity: replay protection, streaming body limit, HMAC-SHA256
        body = await webhook_sec.authenticate_and_read(request, override_secret=active_secret)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            client_ip = request.client.host if request.client else "unknown"
            logger.warning(
                json.dumps({
                    "event": "webhook_json_invalid",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "ip": client_ip,
                    "path": request.url.path,
                    "reason": "invalid JSON body",
                })
            )
            raise HTTPException(status_code=400, detail="Invalid JSON")

        try:
            webhook = IngoingWebhook.model_validate(data)
        except ValidationError:
            raise HTTPException(status_code=400, detail="Invalid webhook payload")
        if not active_repo:
            raise HTTPException(status_code=503, detail="Webhook storage unavailable")
        inbox = WebhookInboxRepository(active_repo.pool)
        for entry in webhook.entry:
            for change in entry.changes:
                value = change.value
                if change.field == "message_template_status_update":
                    for org in await active_repo.get_orgs_by_waba_id(entry.id) or []:
                        await inbox.enqueue(org["organization_id"], "whatsapp_template", {
                            "object": webhook.object,
                            "entry": [{"id": entry.id, "changes": [change.model_dump(by_alias=True, exclude_none=True)]}],
                        }, trace_id)
                    continue

                pid = None
                if value.metadata and value.metadata.phone_number_id:
                    pid = value.metadata.phone_number_id
                if not pid:
                    continue
                if not active_repo:
                    continue
                org_data = await active_repo.get_org_by_phone_number_id(pid)
                if not org_data:
                    logger.warning("Unknown phone_number_id: %s", pid)
                    continue
                org_id = org_data["organization_id"]

                # Minimize positively identified medical input before durable inbox storage.
                from src.core.bookings.privacy_boundary import inbound_classification_status, minimize_whatsapp_change
                payload = change.model_dump(by_alias=True, exclude_none=True)
                classification = await inbound_classification_status(active_repo, org_id)
                if classification != "unknown":
                    payload = minimize_whatsapp_change(payload, quarantine=classification == "error")
                await inbox.enqueue(org_id, "whatsapp", {
                    "object": webhook.object if classification == "unknown" else "whatsapp_business_account",
                    "entry": [{"id": entry.id if classification == "unknown" else None, "changes": [payload]}],
                }, trace_id)

        return Response(status_code=200)

    return router


async def _read_limited_body(request: Request, max_size: int = MAX_BODY_SIZE) -> bytes:
    """Compat shim: delega al componente unificato MetaWebhookSecurity."""
    return await MetaWebhookSecurity(max_body_size=max_size).read_limited_body(request)


def _verify_hmac(body: bytes, signature: str, secret: str) -> bool:
    """Compat shim: delega al componente unificato MetaWebhookSecurity."""
    return MetaWebhookSecurity(app_secret=secret).verify_hmac(body, signature)


async def _handle_status_update(repo, org_id, status):
    wam_id = status.id
    new_status = status.status
    biz_data = getattr(status, "biz_opaque_callback_data", None)
    errors = getattr(status, "errors", None)

    # Idempotenza atomica: se gia' processato, skip
    if not await dedup_check(repo.pool, wam_id, "status", new_status):
        return

    if biz_data:
        try:
            uid = uuid.UUID(biz_data)
        except ValueError:
            logger.warning(
                json.dumps({
                    "event": "webhook_invalid_callback_data",
                    "wam_id": wam_id,
                    "biz_opaque_callback_data": biz_data,
                    "reason": "non-UUID, fallback a wam_id",
                })
            )
            # Fallback: aggiorna tramite wam_id (garantito da Meta)
            if wam_id:
                await repo.update_message_status_by_wam_id(
                    wam_id, new_status,
                    error_code=str(errors[0]["code"]) if errors else None,
                    error_title=errors[0]["title"] if errors else None,
                    error_details=errors,
                    organization_id=org_id,
                )
            return
        await repo.update_message_status(
            uid, new_status,
            wam_id=wam_id,
            error_code=str(errors[0]["code"]) if errors else None,
            error_title=errors[0]["title"] if errors else None,
            error_details=errors,
            organization_id=org_id,
        )
        return

    if wam_id:
        await repo.update_message_status_by_wam_id(
            wam_id, new_status,
            error_code=str(errors[0]["code"]) if errors else None,
            error_title=errors[0]["title"] if errors else None,
            error_details=errors,
            organization_id=org_id,
        )


async def _handle_inbound_message(repo, org_id, msg, contacts, trace_id=None):
    trace_id = trace_id or uuid.uuid4().hex[:16]
    # Idempotenza atomica
    if not await dedup_check(repo.pool, msg.id, "message", ""):
        logger.info("message_id=%s trace_id=%s action=duplicate_skipped", msg.id, trace_id)
        return

    if not contacts:
        logger.warning("message_id=%s trace_id=%s event=contacts_empty", msg.id, trace_id)

    from src.core.bookings.privacy_boundary import inbound_classification_status, minimize_whatsapp_change
    classification = await inbound_classification_status(repo, org_id)
    if classification != "unknown":
        from src.whatsapp.models import MessageEntry
        clean = minimize_whatsapp_change({"value": {"messages": [msg.model_dump(by_alias=True, exclude_none=True)]}}, quarantine=classification == "error")
        msg = MessageEntry.model_validate(clean["value"]["messages"][0])
    from_number = msg.from_
    contact = await repo.get_or_create_contact(org_id, from_number)
    conv = await repo.get_or_create_conversation(org_id, contact["id"])
    async with repo.pool.acquire() as conn:
        async with conn.transaction():
            await repo.upsert_message(
                id=uuid.uuid4(),
                organization_id=org_id,
                conversation_id=conv["id"],
                wam_id=msg.id,
                direction="inbound",
                message_type=msg.type,
                content=msg.model_dump(by_alias=True, exclude_none=True),
                content_text=msg.text.body if msg.text else None,
                status="received_pending_ai",
                conn=conn,
            )


async def _handle_template_status_update(repo, value, entry_id=None):
    """Applica lo stato template a TUTTE le org collegate al waba_id.

    L'evento Meta e' a livello WABA (entry.id) senza phone_number_id e il waba_id
    e' 1:N per realta' Meta (WABA condiviso). Il fan-out elimina la write nel
    tenant errato: ogni update resta scoped sul proprio organization_id e lo
    stato upstream (verita' condivisa del WABA) raggiunge tutte le org legittime.
    """
    waba_id = entry_id
    if not waba_id:
        logger.warning("Missing waba_id for template status update")
        return
    get_all = getattr(repo, "get_orgs_by_waba_id", None)
    if callable(get_all):
        orgs = await get_all(waba_id) or []
    else:
        # Retrocompatibilita': repo che espongono solo il lookup singolare.
        single = await repo.get_org_by_waba_id(waba_id)
        orgs = [single] if single else []
    if not orgs:
        logger.warning("Unknown waba_id for template status update: %s", waba_id)
        return
    if len(orgs) > 1:
        logger.warning(
            "Shared waba_id %s matches %d organizations: fan-out template status",
            waba_id, len(orgs),
        )
    for org_data in orgs:
        await repo.update_template_status(
            organization_id=org_data["organization_id"],
            name=value.message_template_name,
            language=value.message_template_language,
            status=value.message_template_status,
            rejected_reason=getattr(value, "reason", None),
        )
