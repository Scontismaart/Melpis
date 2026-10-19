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
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from src.api.dependencies import get_repo, require_ruolo
from src.api.routes.common import (
    audit_event,
    check_feature_blocked_by_plan,
    enforce_org_rate_limit,
    get_billing_snapshot,
    resolve_estrai_da_url,
    resolve_vettorizza,
)
from src.core.documenti.chunking import chunk_testo
from src.core.documenti.extractor import estrai_testo
from src.core.documenti.priorita import rileva_conflitti_prezzo
from src.core.documenti.qa_agent import rispondi
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


# ── RAG Q&A ──────────────────────────────────────────────────────────

@router.post("/api/documenti/chiedi", response_model=RispostaDocumento)
async def chiedi_documenti(
    domanda: DomandaInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Risponde a una domanda basandosi sui documenti indicizzati del tenant."""
    repo = get_repo(request)
    org_id = user.get("organization_id")
    blocco = await check_feature_blocked_by_plan(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    await enforce_org_rate_limit(org_id, "knowledge-qa", 60, 60)
    billing = await get_billing_snapshot(repo, org_id)
    output = await rispondi(
        org_id, domanda.domanda, repo, k=domanda.k, billing=billing,
        actor_auth_user_id=user.get("auth_user_id"),
        trace_id=getattr(request.state, "trace_id", None),
    )
    return output


# ── Conteggio ed Elenco Documenti ─────────────────────────────────────

@router.get("/api/documenti/conteggio")
async def conteggio_documenti(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Restituisce il numero totale di chunk indicizzati per l'organizzazione."""
    repo = get_repo(request)
    return {"chunk_indicizzati": await repo.count_chunks(user["organization_id"])}


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
    return {"detail": "Stato fonte aggiornato.", "documento": doc}


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
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Aggiunge una nuova FAQ indicizzata alla knowledge base."""
    repo = get_repo(request)
    org_id = user.get("organization_id")
    blocco = await check_feature_blocked_by_plan(repo, org_id, "rag")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)

    chunk_testo_singolo = f"Domanda: {faq.domanda}\nRisposta: {faq.risposta}"
    record = await repo.create_document(
        org_id,
        nome=faq.domanda,
        tipo="faq",
        fonte="faq",
        is_active=True,
        stato="indicizzata",
        metadata={"domanda": faq.domanda, "risposta": faq.risposta},
    )

    vettorizza = resolve_vettorizza()
    embeds = vettorizza([chunk_testo_singolo], tipo="passage")
    await repo.add_chunk(
        org_id,
        record["id"],
        0,
        chunk_testo_singolo,
        embeds[0],
        {"domanda": faq.domanda, "risposta": faq.risposta, "tipo": "faq", "document_id": str(record["id"])},
    )

    try:
        await repo.faq_cache_invalidate(org_id)
    except Exception as exc:
        logger.warning("faq_cache invalidation failed org=%s: %s", org_id, exc)

    return {"detail": "FAQ aggiunta alla knowledge base.", "id": str(record["id"])}


@router.put("/api/conoscenza/faq/{faq_id}")
async def aggiorna_faq(
    faq_id: str,
    faq: FAQInput,
    request: Request,
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

    chunk_testo_singolo = f"Domanda: {faq.domanda}\nRisposta: {faq.risposta}"
    await repo.update_document(
        org_id,
        faq_id,
        nome=faq.domanda,
        metadata={"domanda": faq.domanda, "risposta": faq.risposta},
        stato="indicizzata",
        errore="",
    )

    await repo.delete_document_chunks(org_id, faq_id)
    vettorizza = resolve_vettorizza()
    embeds = vettorizza([chunk_testo_singolo], tipo="passage")
    await repo.add_chunk(
        org_id,
        faq_id,
        0,
        chunk_testo_singolo,
        embeds[0],
        {"domanda": faq.domanda, "risposta": faq.risposta, "tipo": "faq", "document_id": faq_id},
    )

    try:
        await repo.faq_cache_invalidate(org_id)
    except Exception as exc:
        logger.warning("faq_cache invalidation failed org=%s: %s", org_id, exc)

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
        # Registra la fonte in stato di errore per visibilità in UI
        try:
            await repo.create_document(
                org_id,
                nome=web.url,
                tipo="web",
                fonte=web.url,
                is_active=False,
                stato="errore",
                errore=str(exc),
            )
        except Exception as db_err:
            logger.warning("Errore salvataggio stato fallimento web: %s", db_err)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    chunks = chunk_testo(dati_web["testo"])
    if not chunks:
        raise HTTPException(status_code=400, detail="Nessun testo indicizzabile estratto dalla pagina web.")

    titolo = dati_web["titolo"] or web.url
    record = await repo.create_document(
        org_id,
        nome=titolo,
        tipo="web",
        fonte=web.url,
        is_active=True,
        stato="indicizzata",
        metadata={"url": web.url, "titolo": titolo},
    )

    vettorizza = resolve_vettorizza()
    embeds = vettorizza(chunks, tipo="passage")
    for i, (chunk, emb) in enumerate(zip(chunks, embeds)):
        await repo.add_chunk(
            org_id,
            record["id"],
            i,
            chunk,
            emb,
            {"fonte": web.url, "tipo": "web", "document_id": str(record["id"])},
        )

    try:
        await repo.faq_cache_invalidate(org_id)
    except Exception as exc:
        logger.warning("faq_cache invalidation failed org=%s: %s", org_id, exc)

    return {
        "detail": f"Indicizzati {len(chunks)} chunk dalla pagina '{titolo}'.",
        "indicizzati": len(chunks),
        "nome": titolo,
        "id": str(record["id"]),
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

    return {
        "detail": "Dati struttura e listino servizi aggiornati e indicizzati.",
        "servizi": servizi_dicts,
        "orari": data.orari,
        "id": res["doc_id"],
    }


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
    conflitti = rileva_conflitti_prezzo(servizi_strutturati, all_chunks)
    return {"conflitti": conflitti, "totale": len(conflitti)}


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
        indicizzate = sum(1 for d in items if d.get("stato") == "indicizzata" and d.get("is_active", True))
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
    conflitti = rileva_conflitti_prezzo(servizi_strutturati, all_chunks)

    return {
        "faq": _stats_tipo("faq"),
        "documenti": _stats_tipo("documento"),
        "web": _stats_tipo("web"),
        "dati_struttura": _stats_tipo("dati_struttura"),
        "conflitti_totali": len(conflitti),
        "chunk_indicizzati": await repo.count_chunks(org_id),
    }


# ── Endpoint Documenti Legacy & Upload File ───────────────────────────

@router.post("/api/documenti/carica")
async def carica_documento(
    doc: CaricaDocumentoInput,
    request: Request,
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
    chunks = chunk_testo(doc.testo)
    if not chunks:
        raise HTTPException(status_code=400, detail="Testo senza contenuto indicizzabile.")

    record = await repo.create_document(
        org_id,
        doc.nome,
        tipo="upload",
        fonte="dashboard",
        is_active=True,
        stato="indicizzata",
    )
    vettorizza = resolve_vettorizza()
    embeds = vettorizza(chunks, tipo="passage")
    for i, (chunk, emb) in enumerate(zip(chunks, embeds)):
        await repo.add_chunk(
            org_id,
            record["id"],
            i,
            chunk,
            emb,
            {"fonte": doc.nome, "tipo": "upload", "document_id": str(record["id"])},
        )
    try:
        await repo.faq_cache_invalidate(org_id)
    except Exception as exc:
        logger.warning("faq_cache invalidation failed org=%s: %s", org_id, exc)
    return {"detail": f"Indicizzati {len(chunks)} chunk da '{doc.nome}'.", "indicizzati": len(chunks), "id": str(record["id"])}


@router.post("/api/documenti/carica-file")
async def carica_file_documento(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Carica un file (PDF, TXT, DOCX) ed estrae il testo per l'indicizzazione."""
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
        testo = estrai_testo(contenuto, nome, file.content_type or "")
        chunks = chunk_testo(testo)
        if not chunks:
            raise ValueError("Nessun testo indicizzabile estratto dal file.")
    except ValueError as exc:
        # Salva record in stato errore per visibilità immediata in UI
        try:
            await repo.create_document(
                org_id,
                nome,
                tipo="documento",
                fonte=nome,
                is_active=False,
                stato="errore",
                errore=str(exc),
            )
        except Exception as db_err:
            logger.warning("Errore salvataggio fallimento file: %s", db_err)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record = await repo.create_document(
        org_id,
        nome,
        tipo="documento",
        fonte=nome,
        is_active=True,
        stato="indicizzata",
    )
    vettorizza = resolve_vettorizza()
    embeds = vettorizza(chunks, tipo="passage")
    for i, (chunk, emb) in enumerate(zip(chunks, embeds)):
        await repo.add_chunk(
            org_id,
            record["id"],
            i,
            chunk,
            emb,
            {"fonte": nome, "tipo": "documento", "document_id": str(record["id"])},
        )
    try:
        await repo.faq_cache_invalidate(org_id)
    except Exception as exc:
        logger.warning("faq_cache invalidation failed org=%s: %s", org_id, exc)
    return {"detail": f"Indicizzati {len(chunks)} chunk da '{nome}'.", "indicizzati": len(chunks), "nome": nome, "id": str(record["id"])}


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
