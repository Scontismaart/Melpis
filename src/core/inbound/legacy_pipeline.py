from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from src.agents.prompts import (
    assegna_variante,
    estrai_date_da_testo,
    formatta_disponibilita,
)
from src.core.bookings.service import SlotPienoError
from src.core.ai_safety import apply_booking_result, booking_failed, replay_requires_intervention, validate_ai_input
from src.core.crew_runner import genera_risposta_async
from src.core.documenti.rag_context import recupera_contesto_documenti
from src.core.guardrails import faq_cache
from src.core.guardrails.intent_classifier import classifica_intent, modello_intent
from src.core.guardrails.validator import applica_guardrail, valida_risposta
from src.core.provider_accounting import ProviderAccountingContext
from src.core.verticals import get_vertical_strategy
from pydantic import ValidationError
from src.models.schemas import CanaleMessaggio, LINGUA_DEFAULT, MessaggioInput, ProfiloAttivita, WhatsAppBusinessProfile

logger = logging.getLogger(__name__)


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
        logger.error("business_profile validation failed error_type=%s", type(e).__name__)
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


@dataclass
class LegacyExecutionResult:
    """Esito dell'esecuzione della pipeline legacy isolata."""
    handled: bool = False
    response_text: str = ""
    richiede_umano: bool = False
    motivo: str = ""
    esito: Any = None
    intent_result: Any = None
    pren: Any = None
    q_emb: Any = None
    variante_prompt: str = "control"
    quota_final: Any = None
    quota_exempt: bool = False


class LegacyInboundPipeline:
    """
    Modulo di quarantena per il branch legacy di elaborazione inbound (~270 righe).
    Mantenuto esclusivamente per retrocompatibilità e kill-switch d'emergenza
    quando USE_CONVERSATION_ORCHESTRATOR=false.
    """

    def __init__(self, repo, booking_service=None):
        self.repo = repo
        self.booking_service = booking_service

    async def execute(
        self,
        org_id: uuid.UUID | str,
        msg: dict,
        text: str,
        content: dict,
        canale: str,
        business_profile_raw: dict,
        state: dict | None,
        claim_result: dict,
        heartbeat_coro=None,
        quota_operation=None,
        quota_ticket=None,
    ) -> LegacyExecutionResult:
        validate_ai_input(text)
        result = LegacyExecutionResult()

        messaggio = MessaggioInput(
            testo=text,
            canale=CanaleMessaggio(canale),
            id_conversazione=str(msg.get("conversation_id", "")),
            telefono_mittente=str(content.get("from") or content.get("from_") or "").strip(),
        )

        intent_fn = _get_proc_symbol("classifica_intent", classifica_intent)
        intent_result = await intent_fn(text, allow_llm=False)
        result.intent_result = intent_result

        if intent_result.source == "llm":
            try:
                modello_intent_fn = _get_proc_symbol("modello_intent", modello_intent)
                await self.repo.record_usage(
                    org_id,
                    "intent_classification",
                    metadata={
                        "intent": intent_result.intent,
                        "confidence": intent_result.confidence,
                        "message_id": str(msg["id"]),
                        "model": modello_intent_fn(),
                    },
                )
            except Exception as e:
                logger.warning("Intent usage logging failed for org %s msg %s: %s", org_id, msg["id"],type(e).__name__)

        q_emb = None
        faq_cache_mod = _get_proc_symbol("faq_cache", faq_cache)
        if faq_cache_mod.cache_enabled() and intent_result.intent == "faq":
            try:
                q_emb = await faq_cache_mod.embedding_query(text)
                cached_answer = await faq_cache_mod.cerca_in_cache(str(org_id), text, self.repo, q_emb=q_emb)
            except Exception as e:
                logger.warning("FAQ cache lookup failed for org %s msg %s: %s", org_id, msg["id"],type(e).__name__)
                cached_answer = None

            if cached_answer:
                try:
                    await self.repo.record_usage(
                        org_id,
                        "cache_hit",
                        metadata={
                            "conversation_id": str(msg.get("conversation_id", "")),
                            "message_id": str(msg["id"]),
                            "intent": intent_result.intent,
                        },
                    )
                except Exception as e:
                    logger.warning("Cache hit usage logging failed for org %s msg %s: %s", org_id, msg["id"],type(e).__name__)

                result.handled = True
                result.response_text = cached_answer
                result.richiede_umano = False
                result.q_emb = q_emb
                if quota_operation is not None and quota_ticket is not None:
                    from src.core.billing.quota_reservations import validate_final_response
                    from src.models.schemas import RispostaOutput
                    profilo = _profile_from_dict(business_profile_raw)
                    result.quota_final = validate_final_response(
                        quota_operation, quota_ticket,
                        RispostaOutput(
                            risposta=cached_answer, richiede_umano=False,
                            motivo="", categoria=intent_result.intent or "generico",
                        ),
                        profile=profilo,
                    )
                return result

        result.q_emb = q_emb
        ai_cached = claim_result.get("ai_reply_cache")
        profilo = _profile_from_dict(business_profile_raw)
        assegna_var_fn = _get_proc_symbol("assegna_variante", assegna_variante)
        variante_prompt = assegna_var_fn(str(org_id))
        result.variante_prompt = variante_prompt

        if ai_cached:
            if isinstance(ai_cached, dict):
                result.response_text = ai_cached.get("text", "")
                result.richiede_umano = bool(ai_cached.get("richiede_umano", False))
            elif isinstance(ai_cached, str):
                try:
                    parsed = json.loads(ai_cached)
                    if isinstance(parsed, dict):
                        result.response_text = parsed.get("text", "")
                        result.richiede_umano = bool(parsed.get("richiede_umano", False))
                    else:
                        result.response_text = parsed
                except Exception:
                    result.response_text = ai_cached
            return result

        if await self.repo.check_booking_exists(msg["id"], org_id):
            existing_booking = await self.repo.get_booking_for_message(org_id, msg["id"])
            result.response_text = "Richiesta di prenotazione già registrata. Lo staff può verificarne lo stato."
            result.richiede_umano = replay_requires_intervention(existing_booking)
            result.quota_exempt = True
            return result

        heartbeat_task = None
        if heartbeat_coro:
            heartbeat_task = asyncio.ensure_future(heartbeat_coro(msg["id"], org_id))

        try:
            provider_context = await ProviderAccountingContext.open(
                self.repo.pool,
                org_id,
                task_type="customer_message",
                worker_message_id=msg["id"],
                context={
                    "message_id": str(msg["id"]),
                    "conversation_id": str(msg.get("conversation_id") or "") or None,
                    "trace_id": str(msg.get("trace_id") or "") or None,
                },
                quota_operation=quota_operation,
                quota_ticket=quota_ticket,
            )
            recupera_doc_fn = _get_proc_symbol("recupera_contesto_documenti", recupera_contesto_documenti)
            contesto = await recupera_doc_fn(str(org_id), text, self.repo, q_emb=q_emb)

            # Recupera cronologia recente per mantenere il contesto multi-turn
            cronologia: list[tuple[str, str]] = []
            conversation_id_str = str(msg.get("conversation_id", "") or "")
            testi_cronologia: list[str] = []
            if conversation_id_str:
                try:
                    prior_msgs = await self.repo.list_conversation_messages(str(org_id), conversation_id_str, limit=20)
                    prior_msgs = [m for m in prior_msgs if str(m.get("id")) != str(msg["id"])]
                    ultimo_in = None
                    for pm in prior_msgs:
                        p_text = (pm.get("content_text") or "").strip()
                        if not p_text:
                            continue
                        testi_cronologia.append(p_text)
                        if pm.get("direction") == "inbound":
                            if ultimo_in is not None:
                                cronologia.append((ultimo_in, ""))
                            ultimo_in = p_text
                        elif pm.get("direction") == "outbound":
                            if ultimo_in is not None:
                                cronologia.append((ultimo_in, p_text))
                                ultimo_in = None
                            else:
                                cronologia.append(("", p_text))
                    if ultimo_in is not None:
                        cronologia.append((ultimo_in, ""))
                except Exception as e:
                    logger.warning("Recupero cronologia fallito per conv %s: %s", conversation_id_str,type(e).__name__)

            # Pre-fetch semaforo: estrae date dal testo e dalla cronologia recente
            contesto_disp = ""
            if self.booking_service:
                try:
                    estrai_date_fn = _get_proc_symbol("estrai_date_da_testo", estrai_date_da_testo)
                    formatta_disp_fn = _get_proc_symbol("formatta_disponibilita", formatta_disponibilita)
                    date_candidate = estrai_date_fn(text)
                    if not date_candidate and testi_cronologia:
                        for prev_t in reversed(testi_cronologia[-4:]):
                            cand = estrai_date_fn(prev_t)
                            if cand:
                                date_candidate = cand
                                break
                    if date_candidate:
                        all_slots = []
                        for d in date_candidate[:3]:
                            slots = await self.booking_service.semaforo_giorno(org_id, d)
                            all_slots.extend([s.model_dump() if hasattr(s, "model_dump") else s for s in slots])
                        if all_slots:
                            contesto_disp = formatta_disp_fn(all_slots)
                except Exception as e:
                    logger.warning("Semaforo pre-fetch failed for org %s: %s", org_id,type(e).__name__)

            genera_risp_fn = _get_proc_symbol("genera_risposta_async", genera_risposta_async)
            risposta = await genera_risp_fn(
                messaggio,
                profilo,
                cronologia=cronologia,
                billing=provider_context.billing_snapshot,
                contesto_documenti=contesto.testo,
                intent=intent_result.intent,
                variante=variante_prompt,
                contesto_disponibilita=contesto_disp,
                provider_context=provider_context,
            )
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
            if "provider_context" in locals():
                await provider_context.close()

        valida_fn = _get_proc_symbol("valida_risposta", valida_risposta)
        applica_guard_fn = _get_proc_symbol("applica_guardrail", applica_guardrail)
        esito = valida_fn(risposta, contesto.chunks, profilo)
        result.esito = esito
        if esito.azione != "none":
            risposta = applica_guard_fn(risposta, esito)
            if esito.azione == "block":
                logger.warning("guardrail block org_id=%s message_id=%s motivo=%s", org_id, msg["id"], esito.motivo)
                try:
                    await self.repo.record_usage(
                        org_id,
                        "guardrail_block",
                        metadata={
                            "motivo": esito.motivo,
                            "violazioni": list(esito.violazioni),
                            "conversation_id": str(msg.get("conversation_id", "")),
                            "message_id": str(msg["id"]),
                        },
                    )
                except Exception as e:
                    logger.warning("Guardrail usage logging failed for org %s msg %s: %s", org_id, msg["id"],type(e).__name__)

        strategy = get_vertical_strategy(profilo.verticale, organization_id=str(org_id))
        risposta.prenotazione = strategy.valida_e_arricchisci_prenotazione(risposta.prenotazione, text)
        pren = risposta.prenotazione
        result.pren = pren

        if pren and pren.data and pren.ora and pren.coperti and not risposta.richiede_umano:
            if self.booking_service and org_id and msg.get("id"):
                try:
                    created = await self.booking_service.create_booking(
                        org_id=org_id,
                        nome_cliente=pren.nome_cliente or ("Cliente Instagram" if canale == "instagram" else "Cliente WhatsApp"),
                        telefono="" if canale == "instagram" else messaggio.telefono_mittente,
                        data=pren.data,
                        ora=pren.ora,
                        coperti=pren.coperti,
                        note=pren.note,
                        origine="Instagram" if canale == "instagram" else "WhatsApp",
                        richiede_intervento=risposta.richiede_umano,
                        id_conversazione=str(msg.get("conversation_id", "")),
                        source_message_id=str(msg["id"]),
                    )
                    apply_booking_result(risposta, created)
                    logger.info("Booking %s created from AI response for org %s", created["id"], org_id)
                except SlotPienoError as e:
                    booking_failed(risposta)
                    if e.alternative:
                        alt_text = " o ".join(e.alternative)
                        risposta.risposta += f" Mi dispiace, alle {pren.ora} siamo al completo per {pren.coperti} persone. Ti andrebbe bene alle {alt_text}?"
                    else:
                        risposta.risposta += f" Mi dispiace, alle {pren.ora} siamo al completo per {pren.coperti} persone. Posso chiedere allo staff una fascia alternativa."
                    risposta.motivo = "slot_prenotazione_pieno"
                except Exception as e:
                    logger.error("Booking creation from AI failed for org %s error_type=%s", org_id, type(e).__name__)
                    booking_failed(risposta)
            else:
                booking_failed(risposta)

        result.response_text = risposta.risposta
        result.richiede_umano = bool(risposta.richiede_umano)
        result.motivo = getattr(risposta, "motivo", "") or (getattr(esito, "motivo", "") if esito else "")
        if quota_operation is not None and quota_ticket is not None:
            from src.core.billing.quota_reservations import validate_final_response
            result.quota_final = validate_final_response(
                quota_operation, quota_ticket, risposta,
                context_chunks=contesto.chunks, profile=profilo,
            )
        return result
