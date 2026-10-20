"""ConversationOrchestrator - Motore unificato per l'AI Receptionist.

Separa la logica di dominio e applicativa (RAG, intent, FAQ cache, disponibilità,
LLM, guardrails, vertical strategies, booking creation) dal layer di trasporto
(WhatsApp, Instagram, Webhook Meta, Simulatore Dashboard).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Callable

from pydantic import ValidationError

from src.agents.prompts import (
    assegna_variante,
    estrai_date_da_testo,
    formatta_disponibilita,
)
from src.core.bookings import SlotPienoError
from src.core.ai_safety import apply_booking_result, booking_failed, validate_ai_input
from src.core.crew_runner import genera_risposta_async
from src.core.documenti.rag_context import ContestoDocumenti, recupera_contesto_documenti
from src.core.guardrails import faq_cache
from src.core.guardrails.intent_classifier import classifica_intent, modello_intent
from src.core.guardrails.validator import applica_guardrail, valida_risposta
from src.core.llm_config import budget_ratio_from_billing
from src.core.provider_accounting import ProviderAccountingContext
from src.core.receptionist.models import OrchestrationInput, OrchestrationOutput
from src.core.verticals import get_vertical_strategy
from src.models.schemas import (
    CanaleMessaggio,
    LINGUA_DEFAULT,
    MessaggioInput,
    ProfiloAttivita,
    WhatsAppBusinessProfile,
)

logger = logging.getLogger(__name__)

def filter_simulation_airtable_tools(tools: list, is_simulation: bool) -> list:
    """Never expose tenant CRM records to a simulator prompt."""
    return [] if is_simulation else tools


def profile_from_raw(raw: dict | str | None, fallback_name: str = "Attivita") -> ProfiloAttivita:
    """Normalizza un profilo business (dict, JSON o WhatsAppBusinessProfile) in ProfiloAttivita."""
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


class ConversationOrchestrator:
    """Orchestratore unificato del Core AI Receptionist."""

    def __init__(
        self,
        org_repo=None,
        doc_repo=None,
        billing_repo=None,
        conv_repo=None,
        booking_service=None,
        fast_path_matcher: Callable | None = None,
        booking_svc=None,
        airtable_tool_factory: Callable | None = None,
    ):
        self.org_repo = org_repo
        self.doc_repo = doc_repo
        self.billing_repo = billing_repo
        self.conv_repo = conv_repo
        self.booking_service = booking_service or booking_svc
        self.fast_path_matcher = fast_path_matcher
        # Factory per-request (org_id) -> list[BaseTool] Airtable Fase A (no delete).
        # None = AI Airtable disabilitata (comportamento storico invariato).
        self.airtable_tool_factory = airtable_tool_factory

    async def orchestrate(self, req: OrchestrationInput) -> OrchestrationOutput:
        """Esegue l'intero workflow decisionale dell'AI Receptionist per un messaggio."""
        validate_ai_input(req.text)
        org_id = req.organization_id
        if isinstance(org_id, str):
            try:
                org_id = uuid.UUID(org_id)
            except Exception:
                pass

        # Anonymous demo requests may use deterministic application behavior,
        # but cannot create a provider capability or call an LLM.
        if not org_id:
            return OrchestrationOutput(
                response_text="Accedi e collega un'organizzazione per usare l'assistente AI.",
                source="manual_gate", intent="out_of_scope", richiede_umano=False,
            )

        # 1. Normalizzazione Profilo Attività
        profilo = await self._resolve_profile(org_id, req.business_profile)
        business_profile_raw = (
            req.business_profile
            if isinstance(req.business_profile, dict)
            else (profilo.model_dump() if hasattr(profilo, "model_dump") else {})
        )

        # 2. Fast-Path statico (orari, indirizzo, contatti senza LLM)
        if self.fast_path_matcher:
            try:
                fast_reply = await self.fast_path_matcher(req.text, business_profile_raw)
                if fast_reply:
                    return OrchestrationOutput(
                        response_text=fast_reply,
                        source="fast_path",
                        intent="faq",
                        richiede_umano=False,
                    )
            except Exception as e:
                logger.warning("Fast-path matching failed: %s",type(e).__name__)

        # 3. Classificazione Intent
        intent_result = await classifica_intent(req.text, allow_llm=False)
        if (
            intent_result.source == "llm"
            and org_id
            and req.record_billing_usage
            and self.billing_repo
        ):
            try:
                await self.billing_repo.record_usage(
                    org_id,
                    "intent_classification",
                    metadata={
                        "intent": intent_result.intent,
                        "confidence": intent_result.confidence,
                        "message_id": str(req.message_id or ""),
                        "model": modello_intent(),
                    },
                )
            except Exception as e:
                logger.warning("Intent usage logging failed for org %s: %s", org_id,type(e).__name__)

        # 4. FAQ Cache semantica vettoriale
        q_emb = None
        if faq_cache.cache_enabled() and intent_result.intent == "faq" and org_id and self.doc_repo:
            try:
                q_emb = await faq_cache.embedding_query(req.text)
                cached_answer = await faq_cache.cerca_in_cache(str(org_id), req.text, self.doc_repo, q_emb=q_emb)
            except Exception as e:
                logger.warning("FAQ cache lookup failed for org %s: %s", org_id,type(e).__name__)
                cached_answer = None

            if cached_answer:
                if req.record_billing_usage and self.billing_repo:
                    try:
                        await self.billing_repo.record_usage(
                            org_id,
                            "cache_hit",
                            metadata={
                                "conversation_id": req.conversation_id,
                                "message_id": str(req.message_id or ""),
                                "intent": intent_result.intent,
                            },
                        )
                    except Exception as e:
                        logger.warning("Cache hit usage logging failed for org %s: %s", org_id,type(e).__name__)
                output = OrchestrationOutput(
                    response_text=cached_answer,
                    source="faq_cache",
                    intent=intent_result.intent,
                    intent_confidence=intent_result.confidence,
                    richiede_umano=False,
                )
                if req.quota_operation is not None and req.quota_ticket is not None:
                    from src.core.billing.quota_reservations import validate_final_response
                    from src.models.schemas import RispostaOutput
                    output._quota_final = validate_final_response(
                        req.quota_operation,
                        req.quota_ticket,
                        RispostaOutput(
                            risposta=cached_answer, richiede_umano=False,
                            motivo="", categoria=intent_result.intent or "generico",
                        ),
                        profile=profilo,
                    )
                return output

        # 5. RAG Retrieval vettoriale pgvector
        contesto = ContestoDocumenti()
        if org_id and self.doc_repo:
            try:
                contesto = await recupera_contesto_documenti(str(org_id), req.text, self.doc_repo, q_emb=q_emb)
            except Exception as e:
                logger.warning("RAG retrieval failed for org %s: %s", org_id,type(e).__name__)

        # 6. Ricostruzione Cronologia Multi-Turn
        cronologia, testi_cronologia = await self._resolve_history(org_id, req)

        # 7. Pre-fetch Semaforo Disponibilità
        contesto_disp = await self._prefetch_availability(org_id, req.text, testi_cronologia)

        # 8. Generazione LLM Multi-Turn
        billing_state = req.billing_state
        if billing_state is None and org_id and self.billing_repo:
            try:
                billing_state = await self.billing_repo.get_org_subscription_state(org_id)
            except Exception as e:
                logger.warning("Failed to fetch billing state for org %s: %s", org_id,type(e).__name__)

        variante_prompt = assegna_variante(str(org_id)) if org_id else "control"

        # 7.5 Airtable AI tools (Fase A, P1.4): offerti solo se intent CRM,
        # budget ok e connessione attiva; mai delete. Fail-closed: [].
        airtable_tools: list = []
        try:
            from src.integrations.airtable.wiring import select_airtable_tools

            airtable_tools = await select_airtable_tools(
                intent=intent_result.intent,
                budget_ratio=budget_ratio_from_billing(billing_state),
                organization_id=org_id,
                factory=self.airtable_tool_factory,
            )
            if req.is_simulation:
                airtable_tools = filter_simulation_airtable_tools(airtable_tools, True)
        except Exception as e:
            logger.warning("Airtable tools selection failed for org %s: %s", org_id,type(e).__name__)
            airtable_tools = []
        if airtable_tools and org_id and req.record_billing_usage and self.billing_repo:
            try:
                await self.billing_repo.record_usage(
                    org_id,
                    "airtable_ai_tools",
                    metadata={
                        "tools": sorted(getattr(t, "name", "?") for t in airtable_tools),
                        "intent": intent_result.intent,
                        "conversation_id": req.conversation_id,
                        "message_id": str(req.message_id or ""),
                    },
                )
            except Exception as e:
                logger.warning("Airtable tools usage logging failed for org %s: %s", org_id,type(e).__name__)

        provider_context = await ProviderAccountingContext.open(
            getattr(self.billing_repo, "pool", None),
            org_id,
            task_type="simulator" if req.is_simulation else "customer_message",
            actor_auth_user_id=req.auth_user_id,
            worker_message_id=(req.message_id if req.auth_user_id is None else None),
            simulation_request_id=req.simulation_request_id if req.is_simulation else None,
            simulation_claim_token=req.simulation_claim_token if req.is_simulation else None,
            quota_operation=req.quota_operation,
            quota_ticket=req.quota_ticket,
            context={
                "message_id": str(req.message_id) if req.message_id else None,
                "conversation_id": req.conversation_id or None,
                "trace_id": req.trace_id or None,
            },
        )
        try:
            canale_enum = (
                CanaleMessaggio(req.channel)
                if req.channel in ("whatsapp", "instagram", "demo")
                else CanaleMessaggio.DEMO
            )
        except Exception:
            canale_enum = CanaleMessaggio.DEMO

        messaggio = MessaggioInput(
            testo=req.text,
            canale=canale_enum,
            id_conversazione=req.conversation_id,
            telefono_mittente=req.sender_phone,
        )

        try:
            risposta = await genera_risposta_async(
                messaggio,
                profilo,
                cronologia=cronologia,
                billing=provider_context.billing_snapshot,
                contesto_documenti=contesto.testo,
                intent=intent_result.intent,
                variante=variante_prompt,
                contesto_disponibilita=contesto_disp,
                provider_context=provider_context,
                tools=airtable_tools or None,
            )
        finally:
            await provider_context.close()

        # 9. Guardrail Pipeline & Filtering
        esito = valida_risposta(risposta, contesto.chunks, profilo)
        guardrail_action = esito.azione
        guardrail_motivo = esito.motivo if esito.azione != "none" else None

        if esito.azione != "none":
            risposta = applica_guardrail(risposta, esito)
            if esito.azione == "block" and org_id and req.record_billing_usage and self.billing_repo:
                logger.warning("guardrail block org_id=%s motivo=%s", org_id, esito.motivo)
                try:
                    await self.billing_repo.record_usage(
                        org_id,
                        "guardrail_block",
                        metadata={
                            "motivo": esito.motivo,
                            "violazioni": list(esito.violazioni),
                            "conversation_id": req.conversation_id,
                            "message_id": str(req.message_id or ""),
                        },
                    )
                except Exception as e:
                    logger.warning("Guardrail usage logging failed for org %s: %s", org_id,type(e).__name__)

        # Provider costs and dispatch attempts are persisted one by one by the
        # execution-local adapter. Final billable responses remain a separate
        # A04 concern and are intentionally not inferred here.

        # 11. Vertical Strategy & Booking Creation / Validation
        strategy = get_vertical_strategy(profilo.verticale, organization_id=str(org_id) if org_id else None)
        risposta.prenotazione = strategy.valida_e_arricchisci_prenotazione(risposta.prenotazione, req.text)
        pren = risposta.prenotazione

        booking_created = None
        disponibilita_slot = None
        slot_full_alternatives = []

        if pren and pren.data and pren.ora and pren.coperti and not risposta.richiede_umano:
            if req.is_simulation:
                # MODALITA' SIMULAZIONE: solo verifica capienza in sola lettura (Invariante 5)
                # Non acquisisce slot_lock e non scrive mai in bookings
                if self.booking_service and org_id:
                    try:
                        slot = await self.booking_service.verifica_disponibilita(
                            org_id, pren.data, pren.ora, coperti=pren.coperti
                        )
                        disponibilita_slot = (
                            slot.model_dump() if hasattr(slot, "model_dump") else slot
                        )
                    except Exception as e:
                        logger.warning("Simulation availability check failed: %s",type(e).__name__)
            else:
                # CANALE REALE: creazione deterministica con slot_lock e gestione SlotPienoError
                if self.booking_service and org_id and req.message_id:
                    try:
                        nome = pren.nome_cliente or (
                            req.sender_name
                            or ("Cliente Instagram" if req.channel == "instagram" else "Cliente WhatsApp")
                        )
                        tel = req.sender_phone
                        orig = "Instagram" if req.channel == "instagram" else "WhatsApp"
                        created = await self.booking_service.create_booking(
                                organization_id=org_id,
                                nome_cliente=nome,
                                data=pren.data,
                                ora=pren.ora,
                                coperti=pren.coperti,
                                telefono=tel,
                                note=pren.note or "",
                                origine=orig,
                                richiede_intervento=risposta.richiede_umano,
                                id_conversazione=req.conversation_id,
                                source_message_id=str(req.message_id) if req.message_id else None,
                        )
                        apply_booking_result(risposta, created)
                        booking_created = created
                        logger.info(
                            "Booking %s created from AI response for org %s",
                            created.get("id") if isinstance(created, dict) else created,
                            org_id,
                        )
                    except SlotPienoError as e:
                        booking_failed(risposta)
                        slot_full_alternatives = e.alternative or []
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

        # 12. Salvataggio in FAQ Cache (se abilitata, intent faq, non bloccata da guardrail e non simulazione)
        if (
            not req.is_simulation
            and org_id
            and self.doc_repo
            and faq_cache.cache_enabled()
            and intent_result.intent == "faq"
            and guardrail_action != "block"
            and not risposta.richiede_umano
            and not (pren and pren.data and pren.ora and pren.coperti)
        ):
            try:
                await faq_cache.salva_in_cache(
                    str(org_id),
                    req.text,
                    risposta.risposta,
                    self.doc_repo,
                    q_emb=q_emb,
                    prompt_variant=variante_prompt,
                )
            except Exception as e:
                logger.warning("FAQ cache store failed for org %s: %s", org_id,type(e).__name__)

        output = OrchestrationOutput(
            response_text=risposta.risposta,
            richiede_umano=bool(risposta.richiede_umano),
            motivo_richiesta_umano=risposta.motivo,
            intent=intent_result.intent,
            intent_confidence=intent_result.confidence,
            source="llm",
            prenotazione=pren,
            booking_created=booking_created,
            disponibilita_slot=disponibilita_slot,
            slot_full_alternatives=slot_full_alternatives,
            guardrail_action=guardrail_action,
            guardrail_motivo=guardrail_motivo,
        )
        if req.quota_operation is not None and req.quota_ticket is not None:
            from src.core.billing.quota_reservations import validate_final_response
            output._quota_final = validate_final_response(
                req.quota_operation, req.quota_ticket, risposta,
                context_chunks=contesto.chunks, profile=profilo,
            )
        return output

    async def _resolve_profile(self, org_id, raw_profile) -> ProfiloAttivita:
        if isinstance(raw_profile, ProfiloAttivita):
            return raw_profile
        if raw_profile:
            return profile_from_raw(raw_profile)
        if org_id and self.org_repo:
            try:
                db_profile = await self.org_repo.get_org_business_profile(org_id)
                if db_profile:
                    return profile_from_raw(db_profile)
            except Exception as e:
                logger.warning("Profile resolution from DB failed for org %s: %s", org_id,type(e).__name__)
        return ProfiloAttivita(
            nome="Attività",
            tipo_attivita="attività commerciale",
            tono="cordiale e professionale",
            orari="",
        )

    async def _resolve_history(self, org_id, req: OrchestrationInput) -> tuple[list[tuple[str, str]], list[str]]:
        if req.cronologia is not None:
            testi = [msg for turn in req.cronologia for msg in turn if msg]
            return req.cronologia, testi

        cronologia: list[tuple[str, str]] = []
        testi: list[str] = []
        if req.conversation_id and org_id and self.conv_repo:
            try:
                # Carica messaggi recenti se il repository li supporta (MessageRepo o ConvRepo)
                list_msgs_fn = getattr(self.conv_repo, "list_conversation_messages", None)
                if list_msgs_fn is None and hasattr(self, "msg_repo"):
                    list_msgs_fn = getattr(self.msg_repo, "list_conversation_messages", None)

                if list_msgs_fn:
                    prior_msgs = await list_msgs_fn(str(org_id), req.conversation_id, limit=20)
                    prior_msgs = [m for m in prior_msgs if str(m.get("id")) != str(req.message_id)]
                    ultimo_in = None
                    for pm in prior_msgs:
                        p_text = (pm.get("content_text") or "").strip()
                        if not p_text:
                            continue
                        testi.append(p_text)
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
                logger.warning("History resolution failed for conv %s: %s", req.conversation_id,type(e).__name__)

        return cronologia, testi

    async def _prefetch_availability(self, org_id, text: str, testi_cronologia: list[str]) -> str:
        if not self.booking_service or not org_id:
            return ""
        try:
            date_candidate = estrai_date_da_testo(text)
            if not date_candidate and testi_cronologia:
                for prev_t in reversed(testi_cronologia[-4:]):
                    cand = estrai_date_da_testo(prev_t)
                    if cand:
                        date_candidate = cand
                        break
            if date_candidate:
                all_slots = []
                for d in date_candidate[:3]:
                    slots = await self.booking_service.semaforo_giorno(org_id, d)
                    all_slots.extend([s.model_dump() if hasattr(s, "model_dump") else s for s in slots])
                if all_slots:
                    return formatta_disponibilita(all_slots)
        except Exception as e:
            logger.warning("Semaforo pre-fetch failed for org %s: %s", org_id,type(e).__name__)
        return ""
