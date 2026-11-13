import asyncio
import inspect
import json
import logging
import os
import uuid

from pydantic import ValidationError

from src.agents.prompts import (
    assegna_variante,
    estrai_date_da_testo,
    formatta_disponibilita,
)
from src.core.billing.suspension import is_org_suspended
from src.core.bookings import SlotPienoError
from src.core.crew_runner import genera_risposta_async
from src.core.documenti.rag_context import recupera_contesto_documenti
from src.core.guardrails import faq_cache
from src.core.guardrails.feedback import rileva_feedback_emoji
from src.core.guardrails.intent_classifier import classifica_intent, modello_intent
from src.core.llm_routing import stima_costo_eur
from src.core.guardrails.validator import applica_guardrail, valida_risposta
from src.core.llm_config import LLMRouteRequest, budget_ratio_from_billing, route_llm
from src.core.notifications.email_service import enqueue_escalation
from src.core.security_logger import security_audit
from src.models.schemas import (
    CanaleMessaggio,
    LINGUA_DEFAULT,
    MessaggioInput,
    ProfiloAttivita,
    WhatsAppBusinessProfile,
)
from src.whatsapp.config import AppConfig, load_tenant_config

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30

ORG_SUSPENDED_REPLY = "Grazie per averci scritto, ti risponderemo al piu' presto."

DISCLOSURE_TEXT = (
    "Ciao! Sono l'assistente automatico di {nome}, un sistema di intelligenza "
    "artificiale. Scrivi OPERATORE se vuoi parlare con una persona."
)

HUMAN_WAIT_REPLY = "Ti passo una persona dello staff, un attimo!"


def _profile_from_dict(raw: dict | str | None, fallback_name: str = "Attivita") -> ProfiloAttivita:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    raw = raw or {}
    if not isinstance(raw, dict):
        raw = {}
    try:
        validated = WhatsAppBusinessProfile.model_validate(raw)
    except ValidationError as e:
        logger.error("business_profile validation failed", extra={
            "errors": e.errors(),
            "raw": raw,
        })
        validated = WhatsAppBusinessProfile()
    return ProfiloAttivita(
        nome=validated.nome or fallback_name,
        tipo_attivita=validated.tipo_attivita or "attivita commerciale",
        tono=validated.tono or "cordiale e professionale",
        orari=validated.orari or "",
        descrizione=validated.descrizione or "",
        servizi_principali=validated.servizi_principali or [],
        note_speciali=validated.note_speciali or [],
        lingue_supportate=validated.lingue_supportate or [LINGUA_DEFAULT],
        lingua_default=validated.lingua_default or LINGUA_DEFAULT,
        verticale=validated.verticale,
    )


from src.core.inbound.service import (
    decorate_with_disclosure,
    _extract_from,
)


class InboundProcessor:
    """
    Facade di retrocompatibilità al 100% per l'elaborazione dei messaggi inbound.
    Delega l'orchestrazione applicativa a InboundProcessingService e InboundWorker.
    Mantiene identiche signature, attributi e metodi interni per preservare i test esistenti.
    """

    def __init__(
        self,
        app_config: AppConfig,
        repo,
        service,
        booking_service=None,
        orchestrator=None,
        private_booking_pool=None,
        private_booking_application_pool=None,
    ):
        self.app_config = app_config
        self.repo = repo
        self.service = service
        self.booking_service = booking_service
        self.use_orchestrator = getattr(
            app_config, "use_conversation_orchestrator", False
        ) or (
            os.getenv("USE_CONVERSATION_ORCHESTRATOR", "false").lower()
            in ("true", "1", "yes")
        )
        self.shadow_orchestrator = (
            os.getenv("SHADOW_ORCHESTRATOR", "false").lower() in ("true", "1", "yes")
        )
        if orchestrator is not None:
            self.orchestrator = orchestrator
        elif self.use_orchestrator or self.shadow_orchestrator:
            from src.core.receptionist.conversation_orchestrator import (
                ConversationOrchestrator,
            )
            self.orchestrator = ConversationOrchestrator(
                org_repo=getattr(repo, "org_repo", repo),
                doc_repo=getattr(repo, "doc_repo", repo),
                billing_repo=getattr(repo, "billing_repo", repo),
                conv_repo=getattr(repo, "conv_repo", repo),
                booking_service=booking_service,
                fast_path_matcher=service.fast_path_match if service else None,
            )
        else:
            self.orchestrator = None

        from src.core.inbound.service import InboundProcessingService
        from src.core.workers.inbound_worker import InboundWorker
        self.inbound_service = InboundProcessingService(
            app_config=app_config,
            repo=repo,
            service=service,
            booking_service=booking_service,
            orchestrator=self.orchestrator,
            send_reply_fn=lambda *args, **kwargs: self._send_ai_reply(*args, **kwargs),
            finalize_fn=lambda *args, **kwargs: self._finalize_message(*args, **kwargs),
        )
        self.inbound_service.use_orchestrator = self.use_orchestrator
        self.inbound_service.shadow_orchestrator = self.shadow_orchestrator
        self.worker = InboundWorker(repo=repo, inbound_service=self.inbound_service,
                                    private_booking_pool=private_booking_pool,
                                    private_booking_application_pool=private_booking_application_pool)

    async def process_next_batch(self):
        await self.repo.reap_stale_claims()
        messages = await self.repo.claim_inbound_messages(limit=10)
        for msg in messages:
            try:
                await self._process_one(msg)
            except Exception as e:
                logger.exception("Error processing message %s: %s", msg["id"], e)

    async def _heartbeat_loop(self, msg_id, organization_id):
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self.repo.update_heartbeat(msg_id, organization_id)
        except asyncio.CancelledError:
            pass

    async def _process_one(self, msg: dict):
        # Sincronizza lo stato dei flag a runtime se modificati dopo l'istanziazione
        self.inbound_service.use_orchestrator = self.use_orchestrator
        self.inbound_service.shadow_orchestrator = self.shadow_orchestrator
        self.inbound_service.orchestrator = self.orchestrator
        await self.inbound_service.process_message(msg)

    async def _handle_feedback_emoji(self, org_id, msg, value: str):
        await self.inbound_service._handle_feedback_emoji(org_id, msg, value)

    async def _finalize_message(self, msg_id: str, handling_type: str, organization_id,
                                meta_message_id: str | None = None) -> bool:
        """
        Punto unico di finalizzazione condiviso per tutti i flussi.
        Marca il messaggio come inviato a Meta (sent_at) e come risolto (replied_at/status=handled).
        Viene chiamato SOLO DOPO che qualsiasi side-effect esterno (invio Meta) ha avuto successo confermato.
        """
        if meta_message_id:
            await self.repo.mark_message_sent(msg_id, meta_message_id, organization_id)
        return await self.repo.try_mark_replied(msg_id, handling_type=handling_type,
                                                 organization_id=organization_id)

    async def _send_ai_reply(self, org_id, msg, content, tenant_config, testo_risposta,
                             handling_type="ai_handled") -> dict:
        canale = msg.get("canale") or "whatsapp"
        if canale == "instagram":
            return await self._send_instagram_reply(org_id, msg, content, testo_risposta, handling_type=handling_type)
        to_number = _extract_from(content)
        if not to_number or not tenant_config:
            logger.warning("Impossibile inviare risposta AI per messaggio %s: numero o tenant_config mancante", msg["id"])
            return {}
        payload = {"to": to_number, "type": "text", "text": {"body": testo_risposta}}
        try:
            return await self.service.send_whatsapp_message(
                org_id=org_id,
                to_number=to_number,
                payload=payload,
                category="service",
                meta_client=None,
                tenant_config=tenant_config,
                handling_type=handling_type,
                idempotency_key=f"reply:{msg['id']}",
            )
        except Exception as e:
            from src.whatsapp.service import WhatsAppService
            usage_exc = getattr(self.service, "MessageUsageExceeded", None)
            if not (isinstance(usage_exc, type) and issubclass(usage_exc, BaseException)):
                usage_exc = WhatsAppService.MessageUsageExceeded
            if isinstance(e, usage_exc):
                logger.warning("Quota messaggi esaurita per org %s: risposta AI non inviata", org_id)
                raise
            logger.error("Invio risposta AI fallito per messaggio %s: %s", msg["id"], e)
            raise

    async def _send_instagram_reply(self, org_id, msg, content, testo_risposta,
                                    handling_type="ai_handled") -> dict:
        """Invio della risposta AI via Instagram DM. Se l'org non ha un
        account Instagram collegato (non dovrebbe accadere: il webhook
        arriva solo per account registrati) logga e non crasha."""
        to_ig_id = content.get("from", "")
        if not to_ig_id:
            logger.warning("Impossibile inviare risposta AI Instagram per messaggio %s: mittente mancante", msg["id"])
            return {}
        from src.instagram.config import load_instagram_config
        from src.instagram.repository import InstagramRepository
        from src.instagram.service import InstagramService

        try:
            ig_config = await load_instagram_config(
                org_id, self.app_config.encryption_key, InstagramRepository(self.repo.pool)
            )
            if not ig_config:
                logger.warning("Org %s: account Instagram non configurato, risposta AI non inviata", org_id)
                return {}
            return await InstagramService(self.repo).send_instagram_message(
                org_id=org_id,
                to_ig_id=to_ig_id,
                text=testo_risposta,
                ig_config=ig_config,
                handling_type=handling_type,
                idempotency_key=f"reply:{msg['id']}",
            )
        except Exception as e:
            logger.error("Invio risposta AI Instagram fallito per messaggio %s: %s", msg["id"], e)
            raise
