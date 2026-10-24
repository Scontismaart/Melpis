"""Simulator, Chat Playground & AI Review Responder API Routes (Invarianti 1, 5, 7, 8, 10).

Gestisce:
- Simulatore chat interattivo (/api/messaggio) con instradamento su ConversationOrchestrator
- Generazione risposte e bozze per recensioni clienti (/api/recensione)
- Accounting token e costi AI budget-aware (Invariante 8)
- Storico eventi demo in-memory e isolamento rigoroso tenant (Invariante 1)
"""

import asyncio
import hashlib
import json
import logging
import uuid
import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from src.api.dependencies import (
    get_orchestrator,
    get_optional_organization_context,
    get_repo,
    require_ruolo,
)
from src.core.auth.dependencies import is_demo_mode
from src.api.routes.common import (
    check_feature_blocked_by_plan,
    enforce_org_rate_limit,
    get_billing_snapshot,
    get_shared_event_history,
    next_event_id,
    resolve_genera_risposta_recensione,
)
from src.core.conversation_store import store as conv_store
from src.core.billing.quota_reservations import QuotaLease
from src.core.priorita import calcola_priorita
from src.core.receptionist.models import OrchestrationInput
from src.models.business_profile import PROFILI_DEMO
from src.models.schemas import (
    EventoDashboard,
    MessaggioInput,
    RecensioneInput,
    RispostaOutput,
    RispostaRecensioneOutput,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["simulator"])


@router.post("/api/messaggio", response_model=RispostaOutput)
async def ricevi_messaggio(
    request: Request,
    messaggio: MessaggioInput,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    profilo_id: str = "trattoria_da_mario",
):
    """Simulatore messaggi per playground UI e test di interazione AI (Invarianti 1, 5, 7, 8)."""
    async with QuotaLease() as lease:
        return await _ricevi_messaggio(request, messaggio, response, idempotency_key, profilo_id, lease)


async def _ricevi_messaggio(request, messaggio, response, idempotency_key, profilo_id, lease):
    trace_id = getattr(request.state, "trace_id", "unavailable")
    # Solo la modalita' demo esplicitamente abilitata accetta richieste anonime.
    org_id = None
    profilo = None
    repo = getattr(request.app.state, "repo", None)
    user = await get_optional_organization_context(request)
    if user and user.get("source") == "anonymous":
        if not is_demo_mode():
            raise HTTPException(status_code=401, detail="Sessione utente richiesta")
    elif user and user.get("organization_id"):
        org_id = user["organization_id"]
        if repo is None:
            raise HTTPException(status_code=503, detail="Database non disponibile")
        billing = await get_billing_snapshot(repo, org_id)
        if not billing:
            raise HTTPException(status_code=503, detail="Impossibile verificare l'abbonamento")
        if billing.get("ai_accounting_blocked"):
            raise HTTPException(status_code=503, detail="Contabilità AI non disponibile")
        status = billing.get("subscription_status")
        from src.core.billing.suspension import is_org_suspended
        if is_org_suspended(status, billing.get("trial_end")) or status in (
            "canceled", "unpaid", "incomplete_expired"
        ):
            raise HTTPException(status_code=403, detail="Abbonamento sospeso o scaduto")
        try:
            org_data = await repo.get_organization(org_id)
        except Exception:
            logger.warning("simulator=profile_load_failed org_id=%s trace_id=%s", org_id, trace_id)
            raise HTTPException(status_code=503, detail="Impossibile caricare il profilo attività")
        if not org_data or not org_data.get("business_profile"):
            raise HTTPException(status_code=409, detail="Completa il profilo attività prima di usare il simulatore")
        try:
            from src.whatsapp.inbound_processor import _profile_from_dict
            profilo = _profile_from_dict(
                org_data["business_profile"],
                fallback_name=org_data.get("name", "Attività"),
            )
        except Exception:
            logger.warning("simulator=profile_invalid org_id=%s trace_id=%s", org_id, trace_id)
            raise HTTPException(status_code=503, detail="Impossibile caricare il profilo attività")
    else:
        raise HTTPException(status_code=401, detail="Sessione utente richiesta")

    request_uuid = None
    auth_user_uuid = None
    payload_hash = None
    quota_operation = None
    quota_ticket = None
    quota_reservations = None
    if org_id is not None:
        await enforce_org_rate_limit(str(org_id), "ai_simulator", 20, 60)
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Header Idempotency-Key richiesto")
        try:
            request_uuid = uuid.UUID(idempotency_key)
            auth_user_uuid = uuid.UUID(str(user.get("auth_user_id")))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=400, detail="Idempotency-Key o identità utente non validi")

        # Timestamp is server-generated by MessaggioInput when omitted, so it
        # is excluded from the stable request fingerprint.
        stable_payload = {
            "testo": messaggio.testo,
            "canale": messaggio.canale.value,
            "id_conversazione": messaggio.id_conversazione,
            "telefono_mittente": messaggio.telefono_mittente,
        }
        payload_hash = hashlib.sha256(
            json.dumps(stable_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        try:
            from src.core.billing.quota_reservations import QuotaOperation, QuotaReservations
            quota_operation = QuotaOperation(
                organization_id=uuid.UUID(str(org_id)), source_id=request_uuid,
                channel="simulator", payload_hash=payload_hash,
                auth_user_id=auth_user_uuid,
            )
            quota_reservations = QuotaReservations(getattr(repo, "pool", None))
            reservation = await quota_reservations.reserve(quota_operation)
        except Exception:
            logger.error("simulator=reservation_failed org_id=%s trace_id=%s", org_id, trace_id)
            raise HTTPException(status_code=503, detail="Impossibile riservare la richiesta simulata")

        reservation_status = reservation.get("status") if reservation else None
        if reservation_status == "replay":
            cached = reservation.get("response")
            if isinstance(cached, str):
                try:
                    cached = json.loads(cached)
                except ValueError:
                    cached = None
            if not isinstance(cached, dict):
                raise HTTPException(status_code=503, detail="Risposta simulata non disponibile")
            response.headers["Idempotency-Replayed"] = "true"
            return RispostaOutput.model_validate(cached)
        if reservation_status == "payload_conflict":
            raise HTTPException(status_code=409, detail="Idempotency-Key già usata con contenuto diverso")
        if reservation_status == "in_progress":
            raise HTTPException(status_code=409, detail="Richiesta simulata già in elaborazione")
        if reservation_status == "quota_exceeded":
            raise HTTPException(status_code=429, detail="Quota messaggi esaurita")
        if reservation_status == "reconciliation_required":
            raise HTTPException(status_code=503, detail="Richiesta in verifica contabile")
        if reservation_status != "reserved":
            raise HTTPException(status_code=503, detail="Impossibile riservare la richiesta simulata")
        quota_ticket = reservation.get("ticket")
        if quota_ticket is None:
            raise HTTPException(status_code=503, detail="Proprietà della richiesta simulata non disponibile")
        lease.bind(quota_reservations, quota_operation, quota_ticket)

    if org_id is None:
        if not is_demo_mode():
            raise HTTPException(status_code=401, detail="Sessione utente richiesta")
        profilo = PROFILI_DEMO.get(profilo_id)
        if profilo is None:
            raise HTTPException(status_code=404, detail=f"Profilo '{profilo_id}' non trovato")
        demo_session = request.cookies.get("melpis_demo_session")
        if not demo_session:
            demo_session = uuid.uuid4().hex + uuid.uuid4().hex
            from src.core.auth.bff import cookie_secure
            response.set_cookie(
                key="melpis_demo_session",
                value=demo_session,
                httponly=True,
                secure=cookie_secure(),
                samesite="strict",
                max_age=60 * 60 * 24,
                path="/api/messaggio",
            )

    # Cronologia del simulatore: la chiave e' client-controlled, quindi per
    # le org autenticate va namespaced per evitare incroci tra tenant.
    identity = str(user.get("user_id") or user.get("auth_user_id") or "anonymous")
    scope = f"{org_id}:{identity}" if org_id else f"demo:{demo_session}"
    chiave_conv = f"{scope}:{messaggio.id_conversazione}"
    cronologia = conv_store.recupera_cronologia(chiave_conv)

    # Billing snapshot per il routing budget-aware (invariante 8)
    billing = None if org_id is None else billing

    # Core AI Receptionist Orchestrator (Fase 2)
    orchestrator = get_orchestrator(request)

    req = OrchestrationInput(
        organization_id=org_id,
        auth_user_id=user.get("auth_user_id") if org_id else None,
        trace_id=trace_id,
        conversation_id=messaggio.id_conversazione,
        text=messaggio.testo,
        channel="demo" if not org_id else "whatsapp",
        sender_phone=messaggio.telefono_mittente or "",
        business_profile=profilo,
        cronologia=cronologia,
        billing_state=billing,
        is_simulation=True,
        simulation_request_id=quota_operation.id if quota_operation else None,
        simulation_claim_token=quota_ticket.claim_id if quota_ticket else None,
        quota_operation=quota_operation,
        quota_ticket=quota_ticket,
        # A03 records provider attempts from the verified simulation claim;
        # this legacy flag does not bill the final response.
        record_billing_usage=bool(org_id),
    )

    try:
        out = await orchestrator.orchestrate(req)
    except Exception:
        logger.error("simulator=generation_failed org_id=%s trace_id=%s", org_id or "demo", trace_id)
        if org_id is not None:
            try:
                await quota_reservations.release(quota_operation, quota_ticket)
            except Exception:
                logger.warning("simulator=quota_release_deferred org_id=%s trace_id=%s", org_id, trace_id)
        raise HTTPException(status_code=502, detail="Impossibile generare la risposta al momento. Riprova più tardi.")

    risposta = RispostaOutput(
        risposta=out.response_text,
        richiede_umano=out.richiede_umano,
        motivo=out.motivo_richiesta_umano or "",
        categoria=out.intent or "generico",
        prenotazione=out.prenotazione,
    )

    if org_id is not None and (out.source in {"fast_path", "manual_gate"} or out._quota_exempt is True):
        try:
            await quota_reservations.release(quota_operation, quota_ticket)
        except Exception:
            logger.warning("simulator=quota_release_deferred org_id=%s trace_id=%s", org_id, trace_id)
    elif org_id is not None:
        try:
            final = out._quota_final
            if final is None:
                raise RuntimeError("A04 final response proof missing")
            await lease.checkpoint()
            persisted = await quota_reservations.commit(quota_operation, quota_ticket, final)
            await lease.stop()
            risposta = RispostaOutput.model_validate(persisted)
        except Exception:
            # A commit may have succeeded despite a connection error. The
            # fenced release is safe and returns false for a committed answer.
            try:
                await quota_reservations.release(quota_operation, quota_ticket)
            except Exception:
                logger.warning("simulator=quota_reconciliation_required org_id=%s trace_id=%s", org_id, trace_id)
            logger.error("simulator=response_persist_failed org_id=%s trace_id=%s", org_id, trace_id)
            raise HTTPException(status_code=503, detail="Impossibile salvare la risposta simulata")

    # Keep conversation history only after the idempotent response is durable.
    conv_store.aggiungi(chiave_conv, messaggio.testo, risposta.risposta)

    prenotazione_id = None
    pren = out.prenotazione
    if pren and pren.data and pren.ora and pren.coperti and not org_id:
        from src.core.prenotazioni import crea_prenotazione_dashboard
        from src.models.schemas import PrenotazioneManualeInput
        try:
            demo_input = PrenotazioneManualeInput(
                nome_cliente=pren.nome_cliente or "Cliente",
                telefono=pren.telefono or "",
                data=pren.data,
                ora=pren.ora,
                coperti=pren.coperti,
                note=pren.note,
                stato="In attesa" if risposta.richiede_umano else "Confermato da IA",
                origine="WhatsApp",
            )
            creata = crea_prenotazione_dashboard(demo_input)
            prenotazione_id = getattr(creata, "id", None)
        except Exception:
            logger.warning("simulator=demo_booking_persist_failed trace_id=%s", trace_id)

    # Solo il percorso demo anonimo alimenta lo storico demo condiviso:
    # le org autenticate hanno i propri dati su DB.
    if not org_id:
        storico = get_shared_event_history()
        storico.append(
            EventoDashboard(
                id=next_event_id("msg"),
                tipo_evento="messaggio",
                timestamp=messaggio.timestamp,
                priorita=calcola_priorita(risposta),
                testo_originale=messaggio.testo,
                risposta_ai=risposta.risposta,
                gestito_da_ai=not risposta.richiede_umano,
                dettagli={
                    "categoria": risposta.categoria,
                    "richiede_umano": risposta.richiede_umano,
                    "motivo": risposta.motivo,
                    "prenotazione_id": prenotazione_id,
                    "prenotazione_simulata": out.disponibilita_slot,
                    "guardrail": out.guardrail_action,
                },
            )
        )
    return risposta


@router.post("/api/recensione", response_model=RispostaRecensioneOutput)
async def ricevi_recensione(
    recensione: RecensioneInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    repo = get_repo(request)
    org_id = user.get("organization_id")
    if not org_id or not recensione.external_id or not recensione.external_id.strip():
        raise HTTPException(status_code=422, detail="external_id stabile obbligatorio per la recensione.")
    await enforce_org_rate_limit(org_id, "review-ai", 30, 60)

    from src.core.reviews.idempotency import claim_external_review
    try:
        async with claim_external_review(repo.pool, str(org_id), recensione.external_id) as claimed:
            if not claimed:
                raise HTTPException(
                    status_code=409,
                    detail="Recensione già in elaborazione. Riprova tra poco.",
                )
            return await _ricevi_recensione_impl(recensione, request, user)
    except HTTPException:
        raise
    except Exception:
        trace_id = getattr(request.state, "trace_id", "unavailable")
        logger.error("review=claim_failed org_id=%s trace_id=%s", org_id, trace_id)
        raise HTTPException(
            status_code=503,
            detail="Generazione AI temporaneamente non disponibile.",
        )


async def _ricevi_recensione_impl(
    recensione: RecensioneInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Genera una risposta AI per una recensione ricevuta e ne persiste lo stato (Invarianti 1, 8, 10)."""
    repo = get_repo(request)
    org_id = user.get("organization_id")
    blocco = await check_feature_blocked_by_plan(repo, org_id, "recensioni")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    billing = await get_billing_snapshot(repo, org_id)

    # Idempotency check before profile/RAG/provider work.
    if org_id and recensione.external_id:
        existing = await repo.get_review_by_external_id(org_id, recensione.external_id)
        if existing:
            return RispostaRecensioneOutput(
                id=str(existing["id"]), stato=existing["stato"],
                bozza_risposta=existing.get("bozza_risposta", ""),
                sentiment=existing.get("sentiment", ""),
                richiede_revisione_urgente=bool(existing.get("richiede_revisione_urgente", False)),
                motivo="", categoria=existing.get("categoria", ""),
            )
    from src.core.reviews.ai_governance import (
        authorize_review_generation, generic_review_fallback,
        validate_review_draft,
    )
    try:
        billing = await authorize_review_generation(repo, str(org_id))
    except Exception:
        raise HTTPException(status_code=503, detail="Generazione AI temporaneamente non disponibile.")

    # Lingue dell'org dal profilo onboarding: se non esiste ancora (onboarding
    # non completato) si usano i default ["it"]/"it", nessun errore.
    profilazione = await repo.get_onboarding_profile(org_id) or {}
    lingue = profilazione.get("lingue_supportate") or None
    lingua_default = profilazione.get("lingua_default") or None
    profilo_pubblico = {
        "nome_attivita": profilazione.get("nome_attivita") or "",
        "verticale": profilazione.get("verticale") or "",
        "tono": profilazione.get("tono") or "",
        "lingue_supportate": lingue,
        "lingua_default": lingua_default,
    }
    from src.core.documenti.rag_context import recupera_contesto_documenti
    contesto = await recupera_contesto_documenti(str(org_id), recensione.testo, repo)
    if not contesto.testo.strip():
        output = generic_review_fallback(recensione.testo)
    else:
        genera_risposta_recensione = resolve_genera_risposta_recensione()
        from src.core.documenti.rag_context import require_current_context
        await require_current_context(contesto, repo, org_id)
        from src.core.provider_accounting import ProviderAccountingContext
        provider_context = None
        try:
            provider_context = await ProviderAccountingContext.open(
                getattr(repo, "pool", None),
                org_id,
                task_type="review",
                actor_auth_user_id=user.get("auth_user_id"),
                context={"trace_id": getattr(request.state, "trace_id", None)},
            )
            output = await asyncio.to_thread(
                lambda: genera_risposta_recensione(
                    testo=recensione.testo,
                    stelle=recensione.valutazione_stelle,
                    autore=recensione.autore,
                    billing=billing,
                    lingue_supportate=lingue,
                    lingua_default=lingua_default,
                    profilo_attivita=profilo_pubblico,
                    contesto_documenti=contesto.testo,
                    provider_context=provider_context,
                )
            )
        except Exception:
            trace_id = getattr(request.state, "trace_id", "unavailable")
            logger.error("review=generate_failed org_id=%s trace_id=%s", org_id, trace_id)
            raise HTTPException(status_code=502, detail="Impossibile generare la bozza di risposta. Riprova più tardi.")
        finally:
            if provider_context is not None:
                await provider_context.close()
        await require_current_context(contesto, repo, org_id)
    try:
        validate_review_draft(output.bozza_risposta, recensione.testo, contesto.testo)
    except Exception:
        trace_id = getattr(request.state, "trace_id", "unavailable")
        logger.error("review=generate_failed org_id=%s trace_id=%s", org_id, trace_id)
        raise HTTPException(
            status_code=502,
            detail="Impossibile generare la bozza di risposta. Riprova più tardi.",
        )

    stato = "bozza_generata"
    review_id = str(uuid.uuid4())
    if org_id:
        try:
            review = await repo.create_review(
                organization_id=org_id,
                testo=recensione.testo,
                valutazione_stelle=recensione.valutazione_stelle,
                fonte=recensione.fonte,
                autore=recensione.autore,
                external_id=recensione.external_id,
                bozza_risposta=output.bozza_risposta,
                sentiment=output.sentiment,
                categoria=output.categoria,
                richiede_revisione_urgente=output.richiede_revisione_urgente,
                stato=stato,
            )
            review_id = str(review["id"])
        except asyncpg.UniqueViolationError:
            # external_id gia' presente per questa org: non e' un errore,
            # e' il dedup che doveva funzionare. Riusiamo la riga esistente
            # invece di restituire un id fittizio mai salvato.
            esistente = await repo.get_review_by_external_id(org_id, recensione.external_id)
            if esistente is None:
                logger.error("[recensione] Conflitto univoco senza riga trovata org=%s external_id=%s", org_id, recensione.external_id)
                raise HTTPException(status_code=502, detail="Impossibile salvare la recensione, riprova.")
            review_id = str(esistente["id"])
            stato = esistente["stato"]
        except Exception:
            trace_id = getattr(request.state, "trace_id", "unavailable")
            logger.error("review=persist_failed org_id=%s trace_id=%s", org_id, trace_id)
            raise HTTPException(status_code=502, detail="Impossibile salvare la recensione, riprova.")

    return RispostaRecensioneOutput(
        id=review_id,
        stato=stato,
        bozza_risposta=output.bozza_risposta,
        sentiment=output.sentiment,
        richiede_revisione_urgente=output.richiede_revisione_urgente,
        motivo=output.motivo,
        categoria=output.categoria,
    )
