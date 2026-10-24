"""
rag_context.py
--------------
Retrieval RAG org-scoped condiviso tra i path che alimentano il responder:
la preview del wizard onboarding e il flusso WhatsApp reale. Produce il
testo di contesto da iniettare nel prompt (parametro `contesto_documenti`
di genera_risposta_async) e i chunk strutturati che il guardrail post-LLM
usa per verificare il grounding dei prezzi (task 12).

Mai bloccante per il path di produzione: su errore O timeout restituisce
contesto vuoto, cosi' un retrieval lento o rotto non lascia mai un
messaggio cliente appeso. Il filtro per organization_id vive nella query
SQL (`WHERE ... organization_id = $1`), quindi il contesto di un tenant non
puo' contenere chunk di un altro.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from src.core.documenti.embeddings import vettorizza
from src.core.documenti.embeddings import vettorizza as _production_embedding
from src.core.documenti.access import rag_access

logger = logging.getLogger(__name__)

DEFAULT_K = 3
DEFAULT_TIMEOUT = 3.0


@dataclass(frozen=True)
class ContestoDocumenti:
    """Esito del retrieval: `testo` e' la forma per il prompt (blocchi
    '-- nome --' concatenati), `chunks` la lista strutturata restituita da
    search_similar (content, metadata, document_name, ...) per i guardrail.
    Falsy quando non c'e' contesto (nessun chunk)."""

    testo: str = ""
    chunks: list[dict] = field(default_factory=list)
    knowledge_epoch: int | None = None
    reason: str = "no_sources"

    def __bool__(self) -> bool:
        return bool(self.chunks)


async def recupera_contesto_documenti(
    organization_id: str,
    testo: str,
    repo,
    k: int = DEFAULT_K,
    timeout: float = DEFAULT_TIMEOUT,
    q_emb: list[float] | None = None,
) -> ContestoDocumenti:
    """Recupera i k chunk piu' simili a `testo` tra i documenti dell'org e
    li formatta per il prompt. Restituisce ContestoDocumenti vuoto se non
    ci sono risultati, in caso di errore o se il retrieval sfora `timeout`
    secondi. `q_emb` permette di riusare un embedding gia' calcolato dal
    chiamante (es. cache FAQ): se assente viene calcolato qui."""
    try:
        async with asyncio.timeout(timeout):
            if not isinstance(testo, str) or not testo.strip() or len(testo) > 12000 or type(k) is not int or not 1 <= k <= 20:
                return ContestoDocumenti(reason="invalid_query")
            if not await rag_access(repo, organization_id):
                return ContestoDocumenti(reason="not_authorized")
            documents = getattr(repo, "doc_repo", repo)
            epoch = await documents.knowledge_version(organization_id)
            if q_emb is None:
                if vettorizza is not _production_embedding:
                    q_emb = (await asyncio.to_thread(vettorizza, [testo], tipo="query"))[0]
                else:
                    from src.core.documenti.isolated import embed_local
                    q_emb = (await embed_local([testo], tipo="query", timeout=timeout))[0]
            risultati = await repo.search_similar(organization_id, q_emb, k)
            if await documents.knowledge_version(organization_id) != epoch:
                return ContestoDocumenti(reason="knowledge_changed")
    except Exception as e:
        logger.warning("rag_context=unavailable organization_id=%s error_type=%s", organization_id, type(e).__name__)
        return ContestoDocumenti(reason="unavailable")

    if not risultati:
        return ContestoDocumenti(knowledge_epoch=epoch)

    from src.core.documenti.priorita import get_priorita_num

    def _priorita_sort_key(item):
        meta = item.get("metadata") or {}
        tipo = meta.get("tipo") or item.get("tipo") or ""
        # 1: dati_struttura, 2: faq, 3: documento/upload, 4: web
        p_num = get_priorita_num(tipo)
        dist = item.get("distance", 0.0)
        return (p_num, dist)

    risultati_ordinati = sorted(risultati, key=_priorita_sort_key)

    blocchi = []
    for r in risultati_ordinati:
        nome = (
            r.get("document_name")
            or (r.get("metadata") or {}).get("fonte")
            or "documento"
        )
        blocchi.append(f"-- {nome} --\n{r['content']}")
    return ContestoDocumenti(testo="\n\n".join(blocchi), chunks=risultati_ordinati,
                             knowledge_epoch=epoch, reason="verified")


async def require_current_context(context, repo, organization_id):
    """Reject stale evidence before a provider call and before exposing its result."""
    if not context.chunks:
        return
    from src.core.billing.suspension import AIGenerationBlocked
    try:
        documents = getattr(repo, "doc_repo", repo)
        async with asyncio.timeout(DEFAULT_TIMEOUT):
            valid = (type(context.knowledge_epoch) is int
                     and await rag_access(repo, organization_id)
                     and await documents.knowledge_version(organization_id) == context.knowledge_epoch)
    except Exception:
        valid = False
    if not valid:
        raise AIGenerationBlocked("Knowledge sources changed or are unavailable")
