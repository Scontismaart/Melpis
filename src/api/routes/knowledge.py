"""Knowledge Base, Documents, FAQ & RAG QA API Routes (Invarianti 1, 2, 8, 10).

Gestisce:
- RAG Q&A sui documenti aziendali (/api/documenti/chiedi) con accounting consumi (Invariante 8)
- Upload, gestione e cancellazione documenti (/api/documenti/*)
- Gestione FAQ aziendali e invalidazione cache semantica (/api/conoscenza/faq/*)
- Importazione e indicizzazione pagine Web (/api/conoscenza/web)
- Dati di struttura e listino servizi strutturato (/api/conoscenza/dati-struttura)
- Rilevamento conflitti di prezzo RAG vs listino (/api/conoscenza/conflitti)
- Riepilogo sintetico e aggregato Knowledge (/api/conoscenza/summary e /api/ui/summary)
"""

import logging
import hashlib
import asyncio
import json
import uuid
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from src.api.dependencies import get_repo, require_ruolo
from src.api.routes.common import (
    audit_event,
    check_feature_blocked_by_plan,
    enforce_org_rate_limit,
    resolve_estrai_da_url,
    resolve_vettorizza,
)
from src.core.documenti.chunking import chunk_testo
from src.core.documenti.indexing import index_content, knowledge_limits
from src.core.documenti.priorita import rileva_conflitti_prezzo
from src.core.documenti.qa_agent import rispondi
from src.core.billing.quota_reservations import QuotaConflict, QuotaLease, QuotaOperation, QuotaReservations
from src.models.schemas import (
    CaricaDocumentoInput,
    DatiStrutturaInput,
    DomandaInput,
    FAQInput,
    RispostaDocumento,
    WebImportInput,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge"])


def _index_in_progress(result: dict, *, name: str, include_name: bool = True):
    """Return the shared accepted response when indexing is not yet verified."""
    if result.get("status") != "in_progress":
        return None
    document = result.get("document") or {}
    payload = {
        "detail": "Contenuto ricevuto; indicizzazione in corso.",
        "id": str(document.get("id") or result.get("id") or ""),
        "indicizzati": 0,
        "stato": "in_elaborazione",
    }
    if include_name:
        payload["nome"] = name
    return JSONResponse(status_code=202, content=payload)


def _indexed_count(result: dict) -> int:
    if result.get("status") not in {"indexed", "replay"}:
        raise HTTPException(status_code=503, detail="Indicizzazione non disponibile.")
    try:
        return max(0, int(result.get("indexed", 0)))
    except (TypeError, ValueError):
        return 0


# ── RAG Q&A ──────────────────────────────────────────────────────────

@router.post("/api/documenti/chiedi", response_model=RispostaDocumento)
async def chiedi_documenti(
    domanda: DomandaInput,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Risponde a una domanda basandosi sui documenti indicizzati del tenant."""
    async with QuotaLease() as lease:
        return await _chiedi_documenti(domanda, request, idempotency_key, user, lease)


async def _chiedi_documenti(domanda, request, idempotency_key, user, lease):
    repo = get_repo(request)
    org_id = user.get("organization_id")
    blocco = await check_feature_blocked_by_plan(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    await enforce_org_rate_limit(org_id, "knowledge-qa", 60, 60)
    try:
        request_uuid = uuid.UUID(idempotency_key or "")
        auth_user_uuid = uuid.UUID(str(user.get("auth_user_id")))
        organization_uuid = uuid.UUID(str(org_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="Idempotency-Key o identità utente non validi")

    payload_hash = hashlib.sha256(json.dumps(
        {"organization_id": str(organization_uuid), "domanda": domanda.domanda,
         "k": domanda.k}, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    operation = QuotaOperation(
        organization_id=organization_uuid, source_id=request_uuid, channel="qa",
        payload_hash=payload_hash, auth_user_id=auth_user_uuid,
    )
    reservations = QuotaReservations(getattr(repo, "pool", None))
    try:
        reservation = await reservations.reserve(operation)
    except QuotaConflict:
        raise HTTPException(status_code=409, detail="Richiesta già in elaborazione o da riconciliare")
    except Exception as exc:
        logger.error("knowledge_qa=reservation_failed org_id=%s error_type=%s",
                     org_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Impossibile riservare la risposta")

    state = reservation.get("status") if reservation else None
    if state == "replay":
        cached = reservation.get("response")
        if not isinstance(cached, dict):
            raise HTTPException(status_code=503, detail="Risposta memorizzata non disponibile")
        from src.core.documenti.sources import authorized_sources
        if not await authorized_sources(repo, org_id, cached.get("fonti")):
            raise HTTPException(409, "Le fonti della risposta precedente non sono piu autorizzate o disponibili.")
        return cached
    if state == "quota_exceeded":
        raise HTTPException(status_code=429, detail="Quota risposte AI esaurita")
    if state == "in_progress":
        raise HTTPException(status_code=409, detail="Richiesta già in elaborazione")
    if state == "reconciliation_required":
        raise HTTPException(status_code=503, detail="Richiesta in verifica contabile")
    if state != "reserved" or reservation.get("ticket") is None:
        raise HTTPException(status_code=503, detail="Impossibile riservare la risposta")

    ticket = reservation["ticket"]
    lease.bind(reservations, operation, ticket)
    try:
        output = await rispondi(
            org_id, domanda.domanda, repo, k=domanda.k,
            billing=reservation.get("billing"),
            actor_auth_user_id=auth_user_uuid,
            trace_id=getattr(request.state, "trace_id", None),
            quota_operation=operation, quota_ticket=ticket,
        )
        final = output.pop("_quota_final", None)
        if final is None:
            # No retrieved evidence means no provider-generated answer. Release
            # only after A03 confirms that no uncertain cost is outstanding.
            await reservations.release(operation, ticket)
            return output
        await lease.checkpoint()
        stored = await reservations.commit(operation, ticket, final)
        await lease.stop()
        return stored
    except HTTPException:
        raise
    except Exception as exc:
        try:
            await reservations.release(operation, ticket)
        except Exception:
            logger.warning("knowledge_qa=reservation_held org_id=%s error_type=%s",
                           org_id, type(exc).__name__)
        logger.error("knowledge_qa=answer_failed org_id=%s error_type=%s",
                     org_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Risposta non disponibile; richiesta in verifica")


# ── Conteggio ed Elenco Documenti ─────────────────────────────────────

@router.get("/api/documenti/conteggio")
async def conteggio_documenti(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Restituisce il numero totale di chunk indicizzati per l'organizzazione."""
    repo = get_repo(request)
    return {"chunk_indicizzati": await repo.count_chunks(user["organization_id"])}


@router.get("/api/documenti/limiti")
async def limiti_documenti(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Limiti tecnici e commerciali e uso della KB per l'organizzazione attiva."""
    repo = get_repo(request)
    return await knowledge_limits(repo, user["organization_id"])


@router.get("/api/documenti/elenco")
async def elenco_documenti(
    request: Request,
    tipo: str | None = None,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Elenco di tutte le fonti documentali registrate per l'organizzazione."""
    repo = get_repo(request)
    return {"documenti": await repo.list_sources(user["organization_id"], tipo=tipo)}


@router.patch("/api/documenti/{documento_id}/toggle")
async def toggle_documento_api(
    documento_id: str,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Attiva o disattiva una fonte documentale."""
    repo = get_repo(request)
    doc = await repo.toggle_document_active(user["organization_id"], documento_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Fonte non trovata.")
    try:
        await repo.faq_cache_invalidate(user["organization_id"])
    except Exception as exc:
        logger.warning("faq_cache invalidation failed org=%s: %s", user["organization_id"], exc)
    public = {key: value for key, value in doc.items() if key in {
        "id", "organization_id", "nome", "tipo", "fonte", "is_active", "stato",
        "metadata", "caricato_il", "updated_at", "index_verified", "revision",
    }}
    return {"detail": "Stato fonte aggiornato.", "documento": public}


# ── UI Summary Polling ────────────────────────────────────────────────

@router.get("/api/ui/summary")
async def ui_summary(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Conteggi org-scoped aggregati per il polling efficiente del frontend."""
    repo = get_repo(request)
    return await repo.get_ui_summary(user["organization_id"])


# ── Conoscenza: FAQ ───────────────────────────────────────────────────

@router.post("/api/conoscenza/faq")
async def crea_faq(
    faq: FAQInput,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Aggiunge una nuova FAQ indicizzata alla knowledge base."""
    repo = get_repo(request)
    org_id = user.get("organization_id")
    blocco = await check_feature_blocked_by_plan(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)

    text = f"Domanda: {faq.domanda}\nRisposta: {faq.risposta}"
    result = await index_content(
        repo, org_id, faq.domanda,
        request_key=idempotency_key, text=text, tipo="faq", fonte="faq",
        metadata={"domanda": faq.domanda, "risposta": faq.risposta},
        embedding_fn=resolve_vettorizza(),
    )
    pending = _index_in_progress(result, name=faq.domanda, include_name=False)
    if pending:
        return pending
    _indexed_count(result)
    document = result.get("document") or {}
    return {"detail": "FAQ aggiunta alla knowledge base.", "id": str(document.get("id") or result.get("id") or "")}


@router.put("/api/conoscenza/faq/{faq_id}")
async def aggiorna_faq(
    faq_id: str,
    faq: FAQInput,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Aggiorna il contenuto e i vettori di una FAQ esistente."""
    repo = get_repo(request)
    org_id = user["organization_id"]
    blocco = await check_feature_blocked_by_plan(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    esistente = await repo.get_document(org_id, faq_id)
    if not esistente or esistente.get("tipo") != "faq":
        raise HTTPException(status_code=404, detail="FAQ non trovata.")

    text = f"Domanda: {faq.domanda}\nRisposta: {faq.risposta}"
    result = await index_content(
        repo, org_id, faq.domanda,
        request_key=idempotency_key, text=text, tipo="faq", fonte="faq",
        document_id=faq_id, metadata={"domanda": faq.domanda, "risposta": faq.risposta},
        embedding_fn=resolve_vettorizza(),
    )
    pending = _index_in_progress(result, name=faq.domanda, include_name=False)
    if pending:
        return pending
    _indexed_count(result)
    return {"detail": "FAQ aggiornata con successo.", "id": faq_id}


@router.delete("/api/conoscenza/faq/{faq_id}")
async def elimina_faq(
    faq_id: str,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Elimina una FAQ dalla knowledge base."""
    repo = get_repo(request)
    org_id = user["organization_id"]
    eliminati = await repo.delete_document(org_id, faq_id)
    if not eliminati:
        raise HTTPException(status_code=404, detail="FAQ non trovata.")
    try:
        await repo.faq_cache_invalidate(org_id)
    except Exception as exc:
        logger.warning("faq_cache invalidation failed org=%s: %s", org_id, exc)
    return {"detail": "FAQ rimossa dalla knowledge base."}


# ── Conoscenza: Pagine Web ───────────────────────────────────────────

@router.post("/api/conoscenza/web")
async def importa_pagina_web(
    web: WebImportInput,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Importa il contenuto testuale di una pagina web e la indicizza nel vector store."""
    repo = get_repo(request)
    org_id = user.get("organization_id")
    blocco = await check_feature_blocked_by_plan(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    await enforce_org_rate_limit(org_id, "knowledge-index", 20, 600)

    estrai_da_url = resolve_estrai_da_url()
    try:
        dati_web = await estrai_da_url(web.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not dati_web["testo"].strip():
        raise HTTPException(status_code=400, detail="Nessun testo indicizzabile estratto dalla pagina web.")

    titolo = dati_web["titolo"] or web.url
    result = await index_content(
        repo, org_id, titolo,
        request_key=idempotency_key, text=dati_web["testo"], tipo="web", fonte=web.url,
        metadata={"url": web.url, "titolo": titolo}, embedding_fn=resolve_vettorizza(),
    )
    pending = _index_in_progress(result, name=titolo)
    if pending:
        return pending
    indexed = _indexed_count(result)
    document = result.get("document") or {}
    return {
        "detail": f"Indicizzati {indexed} chunk dalla pagina '{titolo}'.",
        "indicizzati": indexed,
        "nome": titolo,
        "id": str(document.get("id") or result.get("id") or ""),
    }


# ── Conoscenza: Dati Struttura ───────────────────────────────────────

@router.get("/api/conoscenza/dati-struttura")
async def get_dati_struttura(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Restituisce il profilo dei servizi strutturati e orari del tenant."""
    repo = get_repo(request)
    org_id = user["organization_id"]
    bp = await repo.get_org_business_profile(org_id) or {}
    servizi_raw = bp.get("servizi_strutturati") or []

    # Fallback da servizi_principali non tipizzati se vuoto
    if not servizi_raw and bp.get("servizi_principali"):
        servizi_raw = [
            {"id": str(uuid.uuid4()), "nome": str(s), "prezzo": 0.0, "durata_minuti": 30, "operatore": ""}
            for s in bp.get("servizi_principali", [])
        ]

    docs = await repo.list_sources(org_id, tipo="dati_struttura")
    doc_info = docs[0] if docs else None

    return {
        "servizi": servizi_raw,
        "orari": bp.get("orari", ""),
        "stato": doc_info.get("stato", "indicizzata") if doc_info else "indicizzata",
        "is_active": doc_info.get("is_active", True) if doc_info else True,
        "updated_at": doc_info.get("updated_at") if doc_info else None,
    }


@router.put("/api/conoscenza/dati-struttura")
async def salva_dati_struttura(
    data: DatiStrutturaInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Salva e indicizza i dati struttura e il listino servizi tipizzato."""
    repo = get_repo(request)
    org_id = user.get("organization_id")
    blocco = await check_feature_blocked_by_plan(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    await enforce_org_rate_limit(org_id, "knowledge-index", 20, 600)

    servizi_dicts = []
    for s in data.servizi:
        s_dict = s.model_dump()
        if not s_dict.get("id"):
            s_dict["id"] = str(uuid.uuid4())
        servizi_dicts.append(s_dict)

    from src.core.documenti.dati_struttura import indicizza_dati_struttura
    res = await indicizza_dati_struttura(
        repo,
        org_id,
        orari=data.orari,
        servizi_dicts=servizi_dicts,
    )

    response_data = {
        "servizi": servizi_dicts,
        "orari": data.orari,
        "id": res["doc_id"],
        "index_status": res["index_status"],
        "index_verified": res["index_verified"],
    }
    if res["index_status"] == "in_progress":
        return JSONResponse(
            status_code=202,
            content={**response_data, "detail": "Dati struttura salvati; indicizzazione in corso."},
        )
    detail = (
        "Dati struttura e listino servizi aggiornati e indicizzati."
        if res["index_verified"] is True
        else "Dati struttura salvati; indicizzazione non verificata."
    )

    return {**response_data, "detail": detail}


# ── Conoscenza: Rilevamento Conflitti e Riepilogo ─────────────────────

@router.get("/api/conoscenza/conflitti")
async def verifica_conflitti_api(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Rileva conflitti di prezzo tra listino strutturato e chunk liberi nel vector store."""
    repo = get_repo(request)
    org_id = user["organization_id"]
    bp = await repo.get_org_business_profile(org_id) or {}
    servizi_strutturati = bp.get("servizi_strutturati") or []
    all_chunks = await repo.list_all_active_chunks(org_id)
    complete = len(all_chunks) <= 4000 and not any(c.get("content_truncated") for c in all_chunks)
    conflitti = await asyncio.to_thread(rileva_conflitti_prezzo, servizi_strutturati, all_chunks[:4000])
    return {"conflitti": conflitti, "totale": len(conflitti), "complete": complete,
            "operational_limit": None if complete else "conflict_scan_chunks"}


@router.get("/api/conoscenza/summary")
async def knowledge_summary(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Statistiche complete su tutte le tipologie di fonti della knowledge base del tenant."""
    repo = get_repo(request)
    org_id = user["organization_id"]
    tutti = await repo.list_sources(org_id)

    def _stats_tipo(tipo_val: str):
        items = [d for d in tutti if d.get("tipo") == tipo_val or (tipo_val == "documento" and d.get("tipo") == "upload")]
        totale = len(items)
        attive = sum(1 for d in items if d.get("is_active", True))
        indicizzate = sum(1 for d in items if d.get("stato") == "indicizzata" and d.get("index_verified") is True and d.get("is_active", True))
        errori = sum(1 for d in items if d.get("stato") == "errore")
        ultimi = [d.get("updated_at") or d.get("caricato_il") for d in items if d.get("updated_at") or d.get("caricato_il")]
        ultimo_aggiornamento = max(ultimi).isoformat() if ultimi else None
        return {
            "totale": totale,
            "attive": attive,
            "indicizzate": indicizzate,
            "errori": errori,
            "ultimo_aggiornamento": ultimo_aggiornamento,
        }

    bp = await repo.get_org_business_profile(org_id) or {}
    servizi_strutturati = bp.get("servizi_strutturati") or []
    all_chunks = await repo.list_all_active_chunks(org_id)
    complete = len(all_chunks) <= 4000 and not any(c.get("content_truncated") for c in all_chunks)
    conflitti = await asyncio.to_thread(rileva_conflitti_prezzo, servizi_strutturati, all_chunks[:4000])

    return {
        "faq": _stats_tipo("faq"),
        "documenti": _stats_tipo("documento"),
        "web": _stats_tipo("web"),
        "dati_struttura": _stats_tipo("dati_struttura"),
        "conflitti_totali": len(conflitti),
        "conflitti_completi": complete,
        "chunk_indicizzati": await repo.count_chunks(org_id),
    }


# ── Endpoint Documenti Legacy & Upload File ───────────────────────────

@router.post("/api/documenti/carica")
async def carica_documento(
    doc: CaricaDocumentoInput,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Carica testo manuale e lo indicizza nella knowledge base."""
    if not doc.testo.strip():
        raise HTTPException(status_code=400, detail="Testo vuoto.")

    repo = get_repo(request)
    org_id = user.get("organization_id")
    blocco = await check_feature_blocked_by_plan(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    await enforce_org_rate_limit(org_id, "knowledge-index", 20, 600)
    result = await index_content(
        repo, org_id, doc.nome,
        request_key=idempotency_key, text=doc.testo, tipo="upload", fonte="dashboard",
        embedding_fn=resolve_vettorizza(),
    )
    pending = _index_in_progress(result, name=doc.nome)
    if pending:
        return pending
    indexed = _indexed_count(result)
    document = result.get("document") or {}
    return {
        "detail": f"Indicizzati {indexed} chunk da '{doc.nome}'.",
        "indicizzati": indexed,
        "id": str(document.get("id") or result.get("id") or ""),
    }


@router.post("/api/documenti/carica-file")
async def carica_file_documento(
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Carica un file ed estrae il testo per l'indicizzazione."""
    nome = file.filename or "documento"
    repo = get_repo(request)
    org_id = user.get("organization_id")
    blocco = await check_feature_blocked_by_plan(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    await enforce_org_rate_limit(org_id, "knowledge-index", 20, 600)
    max_file_size = 20 * 1024 * 1024
    contenuto = await file.read(max_file_size + 1)
    if not contenuto:
        raise HTTPException(status_code=400, detail="Il file è vuoto.")
    if len(contenuto) > max_file_size:
        raise HTTPException(status_code=413, detail="Il file supera il limite di 20 MB.")

    try:
        result = await index_content(
            repo, org_id, nome,
            request_key=idempotency_key, file_content=contenuto,
            content_type=file.content_type or "", tipo="documento", fonte=nome,
            embedding_fn=resolve_vettorizza(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pending = _index_in_progress(result, name=nome)
    if pending:
        return pending
    indexed = _indexed_count(result)
    document = result.get("document") or {}
    return {
        "detail": f"Indicizzati {indexed} chunk da '{nome}'.",
        "indicizzati": indexed,
        "nome": nome,
        "id": str(document.get("id") or result.get("id") or ""),
    }


@router.post("/api/documenti/{documento_id}/reindicizza")
async def reindicizza_file_documento(
    documento_id: str,
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Sostituisce in modo atomico l'indice usando il file originale selezionato."""
    repo = get_repo(request)
    org_id = user.get("organization_id")
    blocco = await check_feature_blocked_by_plan(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    await enforce_org_rate_limit(org_id, "knowledge-index", 20, 600)
    existing = await repo.get_document(org_id, documento_id)
    if not existing or existing.get("tipo") not in {"documento", "upload"}:
        raise HTTPException(status_code=404, detail="Documento non trovato.")

    max_file_size = 20 * 1024 * 1024
    contenuto = await file.read(max_file_size + 1)
    if not contenuto:
        raise HTTPException(status_code=400, detail="Il file è vuoto.")
    if len(contenuto) > max_file_size:
        raise HTTPException(status_code=413, detail="Il file supera il limite di 20 MB.")
    nome = existing.get("nome") or file.filename or "documento"
    try:
        result = await index_content(
            repo, org_id, nome,
            request_key=idempotency_key, file_content=contenuto,
            content_type=file.content_type or "", tipo=existing["tipo"],
            fonte=existing.get("fonte") or nome, document_id=documento_id,
            metadata=existing.get("metadata") or None, embedding_fn=resolve_vettorizza(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pending = _index_in_progress(result, name=nome)
    if pending:
        return pending
    indexed = _indexed_count(result)
    return {
        "detail": f"Indicizzati {indexed} chunk da '{nome}'.",
        "indicizzati": indexed,
        "nome": nome,
        "id": documento_id,
    }


@router.delete("/api/documenti/{documento_id}")
async def elimina_documento_api(
    documento_id: str,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Elimina una fonte documentale e tutti i suoi vettori."""
    repo = get_repo(request)
    org_id = user["organization_id"]
    eliminati = await repo.delete_document(org_id, documento_id)
    if not eliminati:
        raise HTTPException(status_code=404, detail="Documento non trovato.")
    await audit_event(
        request,
        user,
        "documento_eliminato",
        target_table="documents",
        details={"documento_id": documento_id, "chunk_eliminati": eliminati},
    )
    try:
        await repo.faq_cache_invalidate(org_id)
    except Exception as exc:
        logger.warning("faq_cache invalidation failed org=%s: %s", org_id, exc)
    return {"detail": "Documento rimosso dalla knowledge base.", "chunk_eliminati": eliminati}
