"""
dati_struttura.py
-----------------
Gestione e indicizzazione unificata dei Dati Struttura (Priorità 1 - Massima autorevolezza):
Listino servizi tipizzato e orari di apertura ufficiali.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.documenti.access import rag_access
from src.core.documenti.indexing import index_content

logger = logging.getLogger(__name__)


async def indicizza_dati_struttura(
    repo: Any,
    organization_id: str,
    orari: str,
    servizi_dicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Salva sempre i dati struttura; pubblica il relativo indice solo se RAG è abilitato."""
    bp = await repo.get_org_business_profile(organization_id) or {}
    if servizi_dicts is None:
        servizi_dicts = bp.get("servizi_strutturati") or []

    righe_servizi: list[str] = []
    for s in servizi_dicts:
        prezzo = float(s.get("prezzo", 0.0))
        durata = s.get("durata_minuti", 30)
        op = s.get("operatore", "")
        op_str = f" | Operatore: {op}" if op else ""
        righe_servizi.append(
            f"- {s.get('nome', '')}: Prezzo {prezzo:.2f}€ | Durata {durata} min{op_str}"
        )

    testo_servizi = "\n".join(righe_servizi) if righe_servizi else "Nessun servizio specifico configurato."
    testo_completo = (
        "DATI STRUTTURA AUTOREVOLI DELL'ATTIVITÀ (PRIORITÀ 1 - MASSIMA):\n"
        "Listino Servizi Ufficiale:\n"
        f"{testo_servizi}\n\n"
        f"Orari di Apertura Ufficiali:\n{orari or 'Orari non specificati'}\n"
    )

    bp["servizi_strutturati"] = servizi_dicts
    if servizi_dicts:
        bp["servizi_principali"] = [f"{s.get('nome')} ({float(s.get('prezzo', 0)):.2f}€)" for s in servizi_dicts]
    if orari:
        bp["orari"] = orari
    await repo.update_org_business_profile(organization_id, bp)

    if orari and hasattr(repo, "pool"):
        try:
            async with repo.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE onboarding_profiles SET orari = $2 WHERE organization_id = $1",
                    organization_id, orari
                )
        except Exception as exc:
            logger.warning("update onboarding_profiles orari failed: %s", exc)

    try:
        await repo.faq_cache_invalidate(organization_id)
    except Exception as exc:
        logger.warning("faq_cache invalidation failed org=%s: %s", organization_id, exc)

    existing_docs = await repo.list_sources(organization_id, tipo="dati_struttura")
    doc_id = existing_docs[0]["id"] if existing_docs else None
    index_status = "not_indexed"
    index_verified = False
    if await rag_access(repo, organization_id):
        try:
            from src.api.routes.common import resolve_vettorizza
            embedding_fn = resolve_vettorizza()
        except ImportError:
            embedding_fn = None
        result = await index_content(
            repo,
            organization_id,
            "Dati struttura e listino servizi",
            text=testo_completo,
            tipo="dati_struttura",
            fonte="dati_struttura",
            document_id=doc_id,
            metadata={"servizi": servizi_dicts, "orari": orari},
            embedding_fn=embedding_fn,
            expected_profile=bp,
        )
        document = result.get("document") or {}
        doc_id = document.get("id") or doc_id
        index_status = result.get("status") or "unknown"
        index_verified = document.get("index_verified") is True

    return {
        "doc_id": str(doc_id) if doc_id else None,
        "index_status": index_status,
        "index_verified": index_verified,
        "orari": orari,
        "servizi": servizi_dicts,
    }
