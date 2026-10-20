from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import uuid
from typing import Any, Callable

from src.core.security_logger import security_audit
from src.core.ai_safety import replay_requires_intervention
from src.core.billing.suspension import is_org_suspended
from src.core.channels.base import ChannelOutboundPort
from src.core.channels.delivery import DeliveryUnconfirmed, provider_message_id
from src.core.channels.instagram_adapter import InstagramOutboundAdapter
from src.core.channels.router import OutboundChannelRouter
from src.core.channels.whatsapp_adapter import WhatsAppOutboundAdapter
from src.core.guardrails.feedback import rileva_feedback_emoji
from src.core.inbound.legacy_pipeline import LegacyInboundPipeline
from src.core.inbound.models import ProcessingOutcome
from src.core.notifications.email_service import enqueue_escalation
from src.core.receptionist.conversation_orchestrator import ConversationOrchestrator
from src.core.receptionist.models import OrchestrationInput
from src.whatsapp.config import AppConfig, load_tenant_config

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30
ORG_SUSPENDED_REPLY = "Grazie per averci scritto, ti risponderemo al piu' presto."
DISCLOSURE_TEXT = (
    "Ciao! Sono l'assistente automatico di {nome}, un sistema di intelligenza "
    "artificiale. Scrivi OPERATORE se vuoi parlare con una persona."
)
HUMAN_WAIT_REPLY = "Ti passo una persona dello staff, un attimo!"


def _get_proc_symbol(name: str, default_val: Any) -> Any:
    from unittest.mock import AsyncMock, MagicMock
    if isinstance(default_val, (AsyncMock, MagicMock)):
        return default_val
    import sys
    mod = sys.modules.get("src.whatsapp.inbound_processor")
    if mod and hasattr(mod, name):
        val = getattr(mod, name)
        if isinstance(val, (AsyncMock, MagicMock)) or val is not default_val:
            return val
    return default_val


def _extract_from(content: dict) -> str:
    if not isinstance(content, dict):
        return ""
    return str(content.get("from") or content.get("from_") or "").strip()


async def decorate_with_disclosure(
    org_id, from_number: str, testo: str, repo, nome_attivita: str = "Attivita"
) -> str:
    if not from_number:
        return testo
    contact = await repo.get_or_create_contact(org_id, from_number)
    sent = await repo.mark_ai_disclosure_sent(contact["id"], org_id)
    if not sent:
        return testo
    return DISCLOSURE_TEXT.format(nome=nome_attivita) + "\n\n" + testo


class InboundProcessingService:
    """
    Servizio applicativo puro che orchestra la pipeline dei 12 step per l'elaborazione
    di messaggi inbound multicanale (WhatsApp & Instagram).
    """

    def __init__(
        self,
        app_config: AppConfig,
        repo: Any,
        service: Any = None,
        booking_service: Any = None,
        orchestrator: ConversationOrchestrator | None = None,
        channel_router: OutboundChannelRouter | None = None,
        send_reply_fn: Callable | None = None,
        finalize_fn: Callable | None = None,
    ):
        self.app_config = app_config
        self.repo = repo
        self.service = service
        self.booking_service = booking_service

        # Feature flags: l'orchestrator è configurabile da AppConfig o env
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

        if channel_router is not None:
            self.channel_router = channel_router
        else:
            wa_adapter = WhatsAppOutboundAdapter(service)
            ig_adapter = InstagramOutboundAdapter(
                repo, getattr(app_config, "encryption_key", "")
            )
            self.channel_router = OutboundChannelRouter(
                {"whatsapp": wa_adapter, "instagram": ig_adapter}
            )

        self.legacy_pipeline = LegacyInboundPipeline(repo, booking_service)
        self._send_reply_fn = send_reply_fn
        self._finalize_fn = finalize_fn

    async def _heartbeat_loop(self, msg_id, organization_id):
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self.repo.update_heartbeat(msg_id, organization_id)
        except asyncio.CancelledError:
            pass

    async def _finalize_message(
        self,
        msg_id: str,
        handling_type: str = "ai_handled",
        organization_id: Any = None,
        meta_message_id: str | None = None,
        **kwargs,
    ) -> bool:
        if self._finalize_fn:
            return await self._finalize_fn(
                msg_id,
                handling_type=handling_type,
                meta_message_id=meta_message_id,
                organization_id=organization_id,
            )
        if meta_message_id:
            await self.repo.mark_message_sent(msg_id, meta_message_id, organization_id)
        return await self.repo.try_mark_replied(
            msg_id, handling_type=handling_type, organization_id=organization_id
        )

    async def _send_reply(
        self,
        org_id,
        msg: dict,
        content: dict,
        tenant_config,
        testo_risposta: str,
        handling_type: str = "ai_handled",
    ) -> dict:
        if self._send_reply_fn:
            result = await self._send_reply_fn(
                org_id, msg, content, tenant_config, testo_risposta, handling_type
            )
            provider_message_id(result)
            return result
        canale = msg.get("canale") or "whatsapp"
        to_dest = _extract_from(content)
        adapter = self.channel_router.get_adapter(canale)
        # Chiave deterministica per inbound: doppio processing dello stesso
        # messaggio riusa la riga esistente (mai doppio invio Meta; invio se
        # la riga non e' mai partita). Non tocca i custom _send_reply_fn.
        reply_key = f"reply:{msg.get('id')}" if msg.get("id") else None
        res = await adapter.send_reply(
            org_id=org_id,
            to_destination=to_dest,
            text=testo_risposta,
            tenant_config=tenant_config,
            handling_type=handling_type,
            idempotency_key=reply_key,
        )
        if not res.success:
            raise DeliveryUnconfirmed(res.error or "Outbound delivery failed")
        result = res.raw_response or {"wam_id": res.wam_id}
        provider_message_id(result)
        return result

    async def _escalate(self, org_id, msg, content, tenant_config, text, handling_type="escalated"):
        """Create the staff ticket regardless of courtesy-message delivery."""
        try:
            conv = await self.repo.escalate_to_human(str(msg["conversation_id"]), org_id)
            if conv:
                _get_proc_symbol("enqueue_escalation", enqueue_escalation)(
                    org_id=str(org_id), conversation_id=str(msg["conversation_id"]),
                    contact_name=_extract_from(content) or "cliente",
                    pool=getattr(self.repo, "pool", None),
                )
        except Exception:
            logger.exception("Escalation failed org_id=%s message_id=%s", org_id, msg["id"])
            await self.repo.record_processing_failure(msg["id"], org_id, "escalation_failed")
            return ProcessingOutcome(action="error", handling_type="escalation_failed")

        meta_id = None
        try:
            if text:
                res = await self._send_reply(org_id, msg, content, tenant_config, text, handling_type)
                meta_id = provider_message_id(res)
        except Exception:
            # The deterministic staff transition already succeeded. Retrying
            # the inbound message could invoke the LLM or the courtesy send a
            # second time, so keep the human escalation authoritative. The
            # outbound row (when created) remains queued/ambiguous for explicit
            # reconciliation and this structured error is observable without
            # logging message content or credentials.
            logger.exception(
                "escalation_notice_delivery_failed organization_id=%s "
                "conversation_id=%s message_id=%s trace_id=%s",
                org_id,
                msg.get("conversation_id"),
                msg.get("id"),
                msg.get("trace_id", "unavailable"),
            )

        await self._finalize_message(
            msg["id"],
            handling_type=handling_type,
            meta_message_id=meta_id,
            organization_id=org_id,
        )
        return ProcessingOutcome(action="handled", handling_type=handling_type, richiede_umano=True)

    async def process_message(self, msg: dict) -> ProcessingOutcome:
        org_id = msg["organization_id"]
        text = msg.get("content_text", "")
        content = msg.get("content", {})
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                content = {}
        if not isinstance(content, dict):
            content = {}
        canale = msg.get("canale") or "whatsapp"

        # STOP is a consent command. It must be persisted even when the
        # organization has exhausted its paid message quota.
        if self.service:
            opt_out = await self.service.check_opt_out(text)
            if opt_out["is_opt_out"]:
                from_number = _extract_from(content)
                contact = await self.repo.get_or_create_contact(org_id, from_number)
                await self.repo.record_consent_event(
                    contact_id=contact["id"],
                    event_type="opt_out",
                    method="keyword_match",
                    triggering_message_id=msg["id"],
                    matched_text=text,
                    organization_id=org_id,
                )
                security_audit(
                    "consent_opt_out",
                    contact_id=str(contact["id"]),
                    organization_id=str(org_id),
                )
                await self._finalize_message(
                    msg["id"], handling_type="opt_out", organization_id=org_id
                )
                return ProcessingOutcome(action="handled", handling_type="opt_out")

        # ── STEP 1: Quota Check & Claim Atomico (P0 Concorrenza) ──────────────
        claim_result = await self.repo.claim_message_for_processing(msg["id"], org_id)
        status = claim_result.get("status")
        if status in ("not_found", "already_sent"):
            return ProcessingOutcome(action="ignored", handling_type=status)
        if status == "currently_processing":
            logger.info(
                "Message %s is currently being processed by another worker. Yielding.",
                msg["id"],
            )
            return ProcessingOutcome(action="yielded", handling_type="currently_processing")

        if status == "legacy_charge_pending":
            logger.warning(
                "inbound=legacy_charge_pending organization_id=%s message_id=%s trace_id=%s",
                org_id, msg["id"], msg.get("trace_id", "unavailable"),
            )
            return ProcessingOutcome(action="error", handling_type="quota_reconciliation_required")

        if status == "quota_exceeded":
            cfg_loader = _get_proc_symbol("load_tenant_config", load_tenant_config)
            tenant_config = await cfg_loader(org_id, self.app_config, self.repo)
            return await self._escalate(org_id, msg, content, tenant_config,
                "Stiamo ricevendo troppe richieste, attendi l'operatore.", "quota_exceeded")

        # ── STEP 3: Richiesta Operatore Umano (Invariante 11) ─────────────────
        if self.service:
            wants_human = await self.service.check_human_request(text)
            if wants_human:
                cfg_loader = _get_proc_symbol("load_tenant_config", load_tenant_config)
                tenant_config = await cfg_loader(org_id, self.app_config, self.repo)
                return await self._escalate(org_id, msg, content, tenant_config, HUMAN_WAIT_REPLY)

        # ── STEP 4: Feedback Emoji Customer (👍 / 👎) ──────────────────────────
        feedback_emoji = rileva_feedback_emoji(text)
        if feedback_emoji:
            await self._handle_feedback_emoji(org_id, msg, feedback_emoji)
            return ProcessingOutcome(action="handled", handling_type="feedback")

        # ── STEP 5: Booking Reminder Reply ────────────────────────────────────
        if self.booking_service:
            booking_reply = await self.booking_service.handle_reminder_reply(
                org_id, _extract_from(content), text
            )
            if booking_reply:
                await self._finalize_message(
                    msg["id"], handling_type="automation", organization_id=org_id
                )
                return ProcessingOutcome(action="handled", handling_type="automation")

        # ── STEP 6: Sospensione Tenant (Invariante 8) ──────────────────────────
        state = await self.repo.get_org_subscription_state(org_id)
        if not state or state.get("ai_accounting_blocked") or is_org_suspended(
            state.get("subscription_status"), state.get("trial_end")
        ):
            logger.warning(
                "org_id=%s message_id=%s event=org_suspended — risposta AI inibita",
                org_id,
                msg["id"],
            )
            cfg_loader = _get_proc_symbol("load_tenant_config", load_tenant_config)
            tenant_config = await cfg_loader(org_id, self.app_config, self.repo)
            try:
                res = await self._send_reply(
                    org_id,
                    msg,
                    content,
                    tenant_config,
                    ORG_SUSPENDED_REPLY,
                    handling_type="automation",
                )
                meta_id = provider_message_id(res)
                await self._finalize_message(
                    msg["id"],
                    handling_type="suspended",
                    meta_message_id=meta_id,
                    organization_id=org_id,
                )
            except Exception as e:
                logger.error("Org suspended message send failed for %s: %s", msg["id"], e)
                return ProcessingOutcome(action="error", handling_type="delivery_failed", error=str(e))
            return ProcessingOutcome(action="handled", handling_type="suspended")

        # ── STEP 7: Ticket Claimed da Operatore Staff ──────────────────────────
        get_conv_fn = getattr(self.repo, "get_conversation", None)
        if get_conv_fn is not None:
            conv_res = get_conv_fn(str(msg["conversation_id"]), org_id)
            conversation = await conv_res if inspect.isawaitable(conv_res) else conv_res
            if (
                isinstance(conversation, dict)
                and conversation.get("ticket_status") == "CLAIMED"
            ):
                logger.info(
                    "Ticket %s is CLAIMED, skipping AI response for message %s",
                    msg["conversation_id"],
                    msg["id"],
                )
                await self._finalize_message(
                    msg["id"],
                    handling_type="claimed_by_operator",
                    organization_id=org_id,
                )
                return ProcessingOutcome(action="handled", handling_type="claimed_by_operator")

        # ── STEP 8: Profilo Business & Configurazione Tenant ───────────────────
        cfg_loader = _get_proc_symbol("load_tenant_config", load_tenant_config)
        tenant_config = await cfg_loader(org_id, self.app_config, self.repo)
        if tenant_config is not None:
            business_profile_raw = (
                getattr(tenant_config, "business_profile", None) or {}
            )
        else:
            business_profile_raw = await self.repo.get_org_business_profile(org_id) or {}
        if isinstance(business_profile_raw, str):
            try:
                business_profile_raw = json.loads(business_profile_raw)
            except Exception:
                business_profile_raw = {}
        if not isinstance(business_profile_raw, dict):
            business_profile_raw = {}

        # Keep the old outbound-dedup marker for compatibility checks, but
        # never deliver it as an authority. Any cache/dedup marker means this
        # can be a retry after a committed AI answer whose delivery failed;
        # verify the immutable A04 record before a deterministic fast path can
        # produce a different response.
        outbound_dedup = await self.repo.get_outbound_dedup(msg["organization_id"], msg["id"])

        from src.core.billing.quota_reservations import QuotaOperation, QuotaReservations
        try:
            source_id = uuid.UUID(str(msg["id"]))
            payload_hash = hashlib.sha256(json.dumps(
                {"organization_id": str(org_id), "message_id": str(source_id),
                 "channel": canale, "text": text},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            quota_operation = QuotaOperation(
                organization_id=uuid.UUID(str(org_id)), source_id=source_id,
                channel="inbound", payload_hash=payload_hash,
            )
            quota_reservations = QuotaReservations(getattr(self.repo, "pool", None))
        except Exception as exc:
            logger.error(
                "inbound=quota_operation_unavailable organization_id=%s message_id=%s error_type=%s",
                org_id, msg.get("id", "unavailable"), type(exc).__name__,
            )
            return ProcessingOutcome(action="error", handling_type="quota_reservation_unavailable")
        reservation = None
        quota_ticket = None
        replay_marker_present = (
            claim_result.get("ai_reply_cache") is not None or outbound_dedup is not None
        )
        if replay_marker_present:
            try:
                reservation = await quota_reservations.reserve(quota_operation)
            except Exception as exc:
                logger.warning(
                    "inbound=quota_replay_verification_failed organization_id=%s message_id=%s error_type=%s",
                    org_id, msg["id"], type(exc).__name__,
                )
                return ProcessingOutcome(action="error", handling_type="quota_reconciliation_required")

            replay_status = reservation.get("status") if reservation else None
            if replay_status == "replay":
                cached = reservation.get("response")
                if isinstance(cached, str):
                    try:
                        cached = json.loads(cached)
                    except ValueError:
                        cached = None
                if not isinstance(cached, dict) or not isinstance(
                    cached.get("risposta", cached.get("text")), str
                ):
                    return ProcessingOutcome(action="error", handling_type="quota_replay_unavailable")
                claim_result["ai_reply_cache"] = {
                    "text": cached.get("risposta", cached.get("text", "")),
                    "richiede_umano": bool(cached.get("richiede_umano", False)),
                    "motivo": cached.get("motivo", ""),
                }
            elif replay_status == "in_progress":
                return ProcessingOutcome(action="yielded", handling_type="currently_processing")
            elif replay_status == "reserved":
                # A marker without a committed A04 response is legacy or
                # inconsistent state. No provider call has started in this
                # retry, so release only this fresh reservation and fail shut.
                ticket = reservation.get("ticket")
                if ticket is not None:
                    try:
                        await quota_reservations.release(quota_operation, ticket)
                    except Exception as exc:
                        logger.warning(
                            "inbound=quota_replay_release_deferred organization_id=%s message_id=%s error_type=%s",
                            org_id, msg["id"], type(exc).__name__,
                        )
                logger.warning(
                    "inbound=unverified_replay_marker organization_id=%s message_id=%s",
                    org_id, msg["id"],
                )
                return ProcessingOutcome(action="error", handling_type="quota_reconciliation_required")
            else:
                # Exhausted quota, stale leases, malformed state and unknown
                # results cannot authorize delivery of a cached response.
                return ProcessingOutcome(action="error", handling_type="quota_reconciliation_required")

        # ── STEP 9: Fast-Path Statico ──────────────────────────────────────────
        # Genuine new messages retain the non-billable deterministic path.
        # Marked retries bypass it and proceed only with the A04 ledger result.
        if not replay_marker_present and self.service:
            fast_reply = await self.service.fast_path_match(text, business_profile_raw)
            if fast_reply:
                from_number = _extract_from(content)
                nome = (business_profile_raw or {}).get("nome") or "Attivita"
                decorated = await decorate_with_disclosure(
                    org_id, from_number, fast_reply, self.repo, nome_attivita=nome
                )
                try:
                    res = await self._send_reply(
                        org_id, msg, content, tenant_config, decorated
                    )
                    meta_id = provider_message_id(res)
                    await self._finalize_message(
                        msg["id"],
                        handling_type="ai_handled",
                        meta_message_id=meta_id,
                        organization_id=org_id,
                    )
                except Exception as e:
                    logger.error("Fast reply send failed for %s: %s", msg["id"], e)
                    return ProcessingOutcome(action="error", handling_type="delivery_failed", error=str(e))
                return ProcessingOutcome(action="handled", handling_type="ai_handled")

        # ── STEP 11: Cognitive Orchestration ──────────────────────────────────
        risposta_text = ""
        richiede_umano = False
        esito = None
        intent_result = None
        pren = None
        q_emb = None
        variante_prompt = "control"
        # Existing cache/dedup markers were already validated before fast path.
        # New AI work reserves here before any provider work.
        if reservation is None:
            try:
                reservation = await quota_reservations.reserve(quota_operation)
            except Exception as exc:
                logger.error(
                    "inbound=quota_reservation_unavailable organization_id=%s message_id=%s error_type=%s",
                    org_id, msg["id"], type(exc).__name__,
                )
                return ProcessingOutcome(action="error", handling_type="quota_reservation_unavailable")

        reservation_status = reservation.get("status") if reservation else None
        if reservation_status == "quota_exceeded":
            return await self._escalate(
                org_id, msg, content, tenant_config,
                "Stiamo ricevendo molte richieste, ti risponderà un operatore.",
                "quota_exceeded",
            )
        if reservation_status == "replay":
            cached = reservation.get("response")
            if isinstance(cached, str):
                try:
                    cached = json.loads(cached)
                except ValueError:
                    cached = None
            if not isinstance(cached, dict):
                return ProcessingOutcome(action="error", handling_type="quota_replay_unavailable")
            claim_result["ai_reply_cache"] = {
                "text": cached.get("risposta", cached.get("text", "")),
                "richiede_umano": bool(cached.get("richiede_umano", False)),
                "motivo": cached.get("motivo", ""),
            }
        elif reservation_status == "reserved":
            quota_ticket = reservation.get("ticket")
            if quota_ticket is None:
                return ProcessingOutcome(action="error", handling_type="quota_ticket_unavailable")
            # Ignore any preexisting unverified cache; a reservation would have
            # rejected it in QuotaReservations._lock before reaching this path.
            claim_result["ai_reply_cache"] = None
        elif reservation_status == "in_progress":
            return ProcessingOutcome(action="yielded", handling_type="currently_processing")
        else:
            # In particular, an expired lease is not evidence that the
            # provider did not run. Leave its reservation pending.
            return ProcessingOutcome(action="error", handling_type="quota_reconciliation_required")

        async def release_quota_if_safe():
            nonlocal quota_ticket
            if quota_ticket is None or quota_reservations is None:
                return
            try:
                await quota_reservations.release(quota_operation, quota_ticket)
            except Exception as exc:
                logger.warning(
                    "inbound=quota_release_deferred organization_id=%s message_id=%s error_type=%s",
                    org_id, msg["id"], type(exc).__name__,
                )
            quota_ticket = None

        async def commit_quota_if_valid(final_response, *, allow_nonbillable=False):
            nonlocal quota_ticket
            if quota_ticket is None or quota_reservations is None:
                return
            if final_response is None:
                if allow_nonbillable:
                    await release_quota_if_safe()
                    return
                raise RuntimeError("A04 final response proof missing")
            await quota_reservations.commit(quota_operation, quota_ticket, final_response)
            quota_ticket = None

        if outbound_dedup and reservation_status == "reserved":
            # A dedup row without a committed A04 record is a legacy or
            # inconsistent answer. No provider request has started, so release
            # this new reservation and require reconciliation instead of
            # trusting the row or invoking AI again.
            await release_quota_if_safe()
            logger.warning(
                "inbound=unverified_outbound_dedup organization_id=%s message_id=%s",
                org_id, msg["id"],
            )
            return ProcessingOutcome(action="error", handling_type="quota_reconciliation_required")

        if self.use_orchestrator:
            ai_cached = claim_result.get("ai_reply_cache")
            if ai_cached:
                if isinstance(ai_cached, dict):
                    risposta_text = ai_cached.get("text", ai_cached.get("risposta", ""))
                    richiede_umano = bool(ai_cached.get("richiede_umano", False))
                elif isinstance(ai_cached, str):
                    try:
                        parsed = json.loads(ai_cached)
                        if isinstance(parsed, dict):
                            risposta_text = parsed.get("text", parsed.get("risposta", ""))
                            richiede_umano = bool(parsed.get("richiede_umano", False))
                        else:
                            risposta_text = parsed
                    except Exception:
                        risposta_text = ai_cached
            elif await self.repo.check_booking_exists(msg["id"], org_id):
                existing_booking = await self.repo.get_booking_for_message(org_id, msg["id"])
                risposta_text = "Richiesta di prenotazione già registrata. Lo staff può verificarne lo stato."
                richiede_umano = replay_requires_intervention(existing_booking)
                await release_quota_if_safe()
            else:
                heartbeat_task = asyncio.ensure_future(
                    self._heartbeat_loop(msg["id"], org_id)
                )
                try:
                    req = OrchestrationInput(
                        organization_id=org_id,
                        message_id=msg["id"],
                        conversation_id=str(msg.get("conversation_id", "")),
                        text=text,
                        channel=canale,
                        sender_phone=_extract_from(content),
                        sender_name=content.get("from") or content.get("from_") or "",
                        business_profile=business_profile_raw,
                        is_simulation=False,
                        record_billing_usage=True,
                        quota_operation=quota_operation,
                        quota_ticket=quota_ticket,
                    )
                    out = await self.orchestrator.orchestrate(req)
                    risposta_text = out.response_text
                    richiede_umano = out.richiede_umano
                    if out.source in {"fast_path", "manual_gate"}:
                        await release_quota_if_safe()
                    else:
                        await commit_quota_if_valid(out._quota_final)
                except Exception as exc:
                    await release_quota_if_safe()
                    logger.error(
                        "inbound=generation_or_commit_failed organization_id=%s message_id=%s error_type=%s",
                        org_id, msg["id"], type(exc).__name__,
                    )
                    return ProcessingOutcome(action="error", handling_type="ai_generation_failed")
                finally:
                    heartbeat_task.cancel()
        else:
            # Esecuzione isolata nel modulo quarantenato LegacyInboundPipeline
            try:
                legacy_res = await self.legacy_pipeline.execute(
                    org_id=org_id,
                    msg=msg,
                    text=text,
                    content=content,
                    canale=canale,
                    business_profile_raw=business_profile_raw,
                    state=state,
                    claim_result=claim_result,
                    heartbeat_coro=self._heartbeat_loop,
                    quota_operation=quota_operation,
                    quota_ticket=quota_ticket,
                )
            except Exception as exc:
                await release_quota_if_safe()
                logger.error(
                    "inbound=legacy_generation_failed organization_id=%s message_id=%s error_type=%s",
                    org_id, msg["id"], type(exc).__name__,
                )
                return ProcessingOutcome(action="error", handling_type="ai_generation_failed")
            if legacy_res.handled:
                try:
                    await commit_quota_if_valid(
                        legacy_res.quota_final,
                        allow_nonbillable=legacy_res.quota_exempt,
                    )
                except Exception as exc:
                    await release_quota_if_safe()
                    logger.error(
                        "inbound=quota_commit_failed organization_id=%s message_id=%s error_type=%s",
                        org_id, msg["id"], type(exc).__name__,
                    )
                    return ProcessingOutcome(action="error", handling_type="quota_commit_failed")
                from_number = _extract_from(content)
                nome = (business_profile_raw or {}).get("nome") or "Attivita"
                decorated = await decorate_with_disclosure(
                    org_id, from_number, legacy_res.response_text, self.repo, nome
                )
                try:
                    res = await self._send_reply(
                        org_id, msg, content, tenant_config, decorated
                    )
                    meta_id = provider_message_id(res)
                    await self._finalize_message(
                        msg["id"],
                        handling_type="ai_handled",
                        meta_message_id=meta_id,
                        organization_id=org_id,
                    )
                except Exception as e:
                    logger.error("Legacy reply send failed for %s: %s", msg["id"], e)
                    return ProcessingOutcome(action="error", handling_type="delivery_failed", error=str(e))
                return ProcessingOutcome(action="handled", handling_type="ai_handled")

            risposta_text = legacy_res.response_text
            richiede_umano = legacy_res.richiede_umano
            esito = legacy_res.esito
            intent_result = legacy_res.intent_result
            pren = legacy_res.pren
            q_emb = legacy_res.q_emb
            variante_prompt = legacy_res.variante_prompt
            try:
                await commit_quota_if_valid(
                    legacy_res.quota_final,
                    allow_nonbillable=legacy_res.quota_exempt,
                )
            except Exception as exc:
                await release_quota_if_safe()
                logger.error(
                    "inbound=quota_commit_failed organization_id=%s message_id=%s error_type=%s",
                    org_id, msg["id"], type(exc).__name__,
                )
                return ProcessingOutcome(action="error", handling_type="quota_commit_failed")

            if self.shadow_orchestrator and self.orchestrator and quota_operation is None:
                try:
                    shadow_req = OrchestrationInput(
                        organization_id=org_id,
                        message_id=msg["id"],
                        conversation_id=str(msg.get("conversation_id", "")),
                        text=text,
                        channel=canale,
                        sender_phone=_extract_from(content),
                        sender_name=content.get("from") or content.get("from_") or "",
                        business_profile=business_profile_raw,
                        is_simulation=True,
                        record_billing_usage=True,
                    )
                    shadow_out = await self.orchestrator.orchestrate(shadow_req)
                    logger.info(
                        "[SHADOW_ORCHESTRATOR] msg_id=%s legacy_len=%d orch_len=%d orch_source=%s channel=%s",
                        msg["id"],
                        len(risposta_text),
                        len(shadow_out.response_text),
                        shadow_out.source,
                        canale,
                    )
                except Exception as shadow_err:
                    logger.warning("[SHADOW_ORCHESTRATOR] Comparison error: %s", shadow_err)

        # ── STEP 12: Consegna Outbound & Finalizzazione ─────────────────────────
        nome_attivita = (business_profile_raw or {}).get("nome") or "Attivita"

        if richiede_umano:
            return await self._escalate(org_id, msg, content, tenant_config, risposta_text)

        from_number = _extract_from(content)
        dec_fn = _get_proc_symbol("decorate_with_disclosure", decorate_with_disclosure)
        decorated = await dec_fn(
            org_id, from_number, risposta_text, self.repo, nome_attivita
        )

        await self.repo.save_outbound_dedup(msg["id"], org_id, decorated)

        try:
            res = await self._send_reply(org_id, msg, content, tenant_config, decorated)
            meta_message_id = provider_message_id(res)
            await self._finalize_message(
                msg["id"],
                handling_type="ai_handled",
                meta_message_id=meta_message_id,
                organization_id=org_id,
            )
        except Exception as e:
            logger.error("Meta send failed for %s: %s", msg["id"], e)
            return ProcessingOutcome(
                action="error",
                error=str(e),
                response_text=decorated,
            )

        # Se in modalità legacy e sussistono le condizioni, salva in FAQ Cache
        if (
            not self.use_orchestrator
            and esito
            and intent_result
            and intent_result.intent == "faq"
            and getattr(esito, "azione", "") != "block"
            and not richiede_umano
            and not (pren and pren.data and pren.ora and pren.coperti)
        ):
            from src.core.guardrails import faq_cache

            if faq_cache.cache_enabled():
                try:
                    await faq_cache.salva_in_cache(
                        str(org_id),
                        text,
                        risposta_text,
                        self.repo,
                        q_emb=q_emb,
                        prompt_variant=variante_prompt,
                    )
                except Exception as e:
                    logger.warning("FAQ cache store failed for org %s msg %s: %s", org_id, msg["id"], e)

        return ProcessingOutcome(
            action="handled",
            handling_type="ai_handled",
            meta_message_id=meta_message_id,
            response_text=decorated,
        )

    async def _handle_feedback_emoji(self, org_id, msg, value: str):
        try:
            ultimo_ai = await self.repo.get_last_ai_outbound_message(
                org_id, str(msg["conversation_id"])
            )
            if ultimo_ai:
                await self.repo.registra_feedback(
                    organization_id=org_id,
                    message_id=ultimo_ai["id"],
                    conversation_id=str(msg["conversation_id"]),
                    source="customer_emoji",
                    value=value,
                )
                logger.info(
                    "feedback cliente %s su risposta AI %s (org %s)",
                    value,
                    ultimo_ai["id"],
                    org_id,
                )
        except Exception as e:
            logger.error("Registrazione feedback emoji fallita msg %s: %s", msg["id"], e)
        await self._finalize_message(
            msg["id"], handling_type="feedback", organization_id=org_id
        )
