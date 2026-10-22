from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

logger = logging.getLogger(__name__)

BACKOFF_SCHEDULE = [
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(hours=1),
    timedelta(hours=6),
]


class MultiChannelRetryWorker:
    """
    Worker unificato per il retry delle consegne outbound (WhatsApp e Instagram).
    Include:
    - Reaping dei claim pendenti e lease expiry (SKIP LOCKED safe)
    - Routing dei retry verso l'adapter appropriato (WhatsApp / Instagram)
    - Backoff esponenziale su fallimento
    - Dead-lettering e status update su esaurimento tentativi
    """

    def __init__(
        self,
        app_config: Any,
        repo: Any,
        service: Any = None,
        channel_router: Any = None,
    ):
        self.app_config = app_config
        self.repo = repo
        self.service = service
        self.channel_router = channel_router
        self.max_retries = getattr(app_config, "max_retry_attempts", 5)

    async def process_next_batch(self):
        await self.repo.reap_stale_claims()
        schedule_confirmations = getattr(self.repo, "enqueue_ready_booking_confirmations", None)
        if callable(schedule_confirmations):
            await schedule_confirmations()
        attempts = await self.repo.claim_delivery_attempts(limit=10)
        for attempt in attempts:
            try:
                await self._process_one(attempt)
            except Exception as e:
                logger.error("Error processing delivery attempt %s: %s", attempt["id"], e)

    async def _process_one(self, attempt: dict, *, confirmation_guarded=False):
        message_id = attempt["message_id"]
        payload = await self.repo.reconstruct_payload_for_retry(message_id)
        if not payload:
            await self.repo.update_delivery_attempt(attempt["id"], "failed", {"error": "message not found"})
            return

        org_id = payload["organization_id"]
        if payload.get("handling_type") == "booking_confirmation" and not confirmation_guarded:
            guard = getattr(self.repo, "booking_confirmation_delivery_guard", None)
            if not callable(guard):
                await self.repo.update_delivery_attempt(
                    attempt["id"], "failed", {"error": "booking_confirmation_guard_unavailable"},
                )
                return
            async with guard(org_id, message_id) as confirmed:
                if not confirmed:
                    await self.repo.update_delivery_attempt(
                        attempt["id"], "failed", {"error": "booking_no_longer_confirmed"},
                    )
                    return
                return await self._process_one(attempt, confirmation_guarded=True)
        # P1.2 — mai reinviare alla cieca: la riga puo' essere gia' stata
        # inviata (mark arrivato dopo il claim) o in attesa webhook.
        from src.whatsapp.service import _row_awaiting_webhook, _row_is_delivered

        if _row_is_delivered(payload):
            await self.repo.update_delivery_attempt(
                attempt["id"], "succeeded", {"skipped": "already_delivered"})
            return
        if _row_awaiting_webhook(payload):
            # Ambiguous fresca: possibile invio in volo o status in arrivo.
            # Non toccare l'attempt (resta processing): il reaper lo ricicla
            # dopo il timeout e intanto il webhook riconcilia lo stato.
            logger.info(
                "Delivery attempt %s skipped (sending_ambiguous, awaiting webhook) for %s",
                attempt["id"], message_id,
            )
            return
        channel = (payload.get("channel") or payload.get("canale") or "whatsapp").lower()
        attempt_num = attempt["attempt_number"]

        try:
            if channel == "instagram" and self.channel_router:
                adapter = self.channel_router.get_adapter("instagram")
                to_dest = (
                    payload.get("to")
                    or payload.get("recipient")
                    or payload.get("content", {}).get("from")
                    or payload.get("content", {}).get("to")
                )
                text = (
                    payload.get("text")
                    or payload.get("content", {}).get("text", {}).get("body", "")
                    or payload.get("content_text", "")
                )
                key = payload.get("idempotency_key")
                if not key:
                    raise RuntimeError("Legacy Instagram outbound requires manual reconciliation")
                result = await adapter.send_reply(org_id=org_id, to_destination=to_dest, text=text,
                                                  idempotency_key=key)
                if not result.success:
                    raise RuntimeError(result.error or "Instagram delivery failed")
            else:
                # Default channel: WhatsApp
                if self.service and hasattr(self.service, "attempt_delivery"):
                    import src.whatsapp.config as wa_config
                    tenant = await wa_config.load_tenant_config(org_id, self.app_config, self.repo)
                    from src.whatsapp.client import MetaClient
                    client = MetaClient(tenant)
                    try:
                        result = await self.service.attempt_delivery(
                            message_id=message_id,
                            phone_number_id=tenant.phone_number_id,
                            access_token=tenant.access_token,
                            payload=payload.get("content", {}),
                            meta_client=client,
                            organization_id=org_id,
                        )
                        from src.core.channels.delivery import provider_message_id
                        provider_message_id(result)
                    finally:
                        await client.close()
                elif self.channel_router:
                    adapter = self.channel_router.get_adapter("whatsapp")
                    to_dest = (
                        payload.get("to")
                        or payload.get("recipient")
                        or payload.get("content", {}).get("to")
                    )
                    text = (
                        payload.get("text")
                        or payload.get("content", {}).get("text", {}).get("body", "")
                        or payload.get("content_text", "")
                    )
                    import src.whatsapp.config as wa_config
                    tenant = await wa_config.load_tenant_config(org_id, self.app_config, self.repo)
                    result = await adapter.send_reply(
                        org_id=org_id, to_destination=to_dest, text=text, tenant_config=tenant,
                        idempotency_key=payload.get("idempotency_key") or f"retry:{message_id}"
                    )
                    if not result.success:
                        raise RuntimeError(result.error or "WhatsApp delivery failed")
                else:
                    raise RuntimeError(f"No delivery handler available for channel {channel}")

            await self.repo.update_delivery_attempt(attempt["id"], "succeeded")
        except Exception as e:
            # A network exception can follow provider acceptance. Never turn its
            # durable ambiguous marker into a retryable failure at exhaustion.
            latest = await self.repo.reconstruct_payload_for_retry(message_id)
            if latest and (_row_awaiting_webhook(latest) or _row_is_delivered(latest)):
                await self.repo.update_delivery_attempt(attempt["id"], "failed",
                                                        {"error": "requires_reconciliation"})
                return
            logger.warning("Delivery attempt %d failed for %s: %s", attempt_num, message_id, e)
            if attempt_num >= self.max_retries:
                await self.repo.update_delivery_attempt(attempt["id"], "failed", {"error": str(e)})
                await self.repo.update_message_status(
                    message_id,
                    "failed",
                    error_code="max_retries",
                    error_title=str(e),
                    organization_id=org_id,
                )
            else:
                next_retry = datetime.now(timezone.utc) + BACKOFF_SCHEDULE[min(attempt_num, len(BACKOFF_SCHEDULE) - 1)]
                await self.repo.update_delivery_attempt(attempt["id"], "pending", {"error": str(e)})
                await self.repo.insert_delivery_attempt(message_id, next_retry)
