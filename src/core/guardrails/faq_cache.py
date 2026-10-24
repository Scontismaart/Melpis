"""
faq_cache.py
------------
Cache semantica delle risposte FAQ piu' frequenti (roadmap task 12): le
domande ripetute (orari, prezzi del menu, indirizzo...) vengono servite
dalla cache pgvector invece di passare dal responder, risparmiando token
e latenza.

Il matching e' semantico (stesso embedding MiniLM multilingue del RAG):
"quando aprite?" e "a che ora siete aperti?" colpiscono la stessa voce se
la distanza cosine resta sotto soglia. Ogni voce scade dopo il TTL e
l'intera cache di un org viene invalidata quando carica un nuovo
documento (i prezzi potrebbero essere cambiati).

Config env:
- GUARDRAIL_CACHE_ENABLED (default true)
- GUARDRAIL_CACHE_THRESHOLD (distanza cosine massima, default 0.08)
- GUARDRAIL_CACHE_TTL_HOURS (default 72)
"""

import asyncio
import os

from src.core.documenti.embeddings import vettorizza
from src.core.documenti.embeddings import vettorizza as _production_embedding
from src.core.documenti.access import rag_access

DEFAULT_THRESHOLD = 0.08
DEFAULT_TTL_HOURS = 72


def cache_enabled() -> bool:
    return os.getenv("GUARDRAIL_CACHE_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def soglia() -> float:
    return float(os.getenv("GUARDRAIL_CACHE_THRESHOLD", str(DEFAULT_THRESHOLD)))


def ttl_hours() -> int:
    return int(os.getenv("GUARDRAIL_CACHE_TTL_HOURS", str(DEFAULT_TTL_HOURS)))


async def embedding_query(testo: str) -> list[float]:
    """Embedding della domanda (stesso modello/prefisso 'query:' del RAG):
    calcolato una volta per messaggio e riusato per cache + retrieval."""
    async with asyncio.timeout(3):
        if vettorizza is not _production_embedding:
            return (await asyncio.to_thread(vettorizza, [testo], tipo="query"))[0]
        from src.core.documenti.isolated import embed_local
        return (await embed_local([testo], tipo="query"))[0]


async def cerca_in_cache(organization_id: str, testo: str, repo,
                         q_emb: list[float] | None = None) -> str | None:
    """Ritorna la risposta in cache per la domanda, o None. Il chiamante
    gestisce il caso None (prosegue col responder)."""
    async with asyncio.timeout(3):
        if not cache_enabled() or not await rag_access(repo, organization_id):
            return None
        if q_emb is None:
            q_emb = await embedding_query(testo)
        row = await getattr(repo, "doc_repo", repo).faq_cache_lookup(organization_id, q_emb, soglia())
        return row["answer_text"] if row else None


async def salva_in_cache(organization_id: str, testo: str, risposta: str, repo,
                         q_emb: list[float] | None = None,
                         prompt_variant: str = "control", expected_epoch: int | None = None) -> dict | None:
    """Salva la coppia domanda/risposta. Da chiamare solo per risposte
    effettivamente inviate (guardrail ok, no escalation, no prenotazione):
    e' la selezione di qualita' che tiene la cache pulita."""
    async with asyncio.timeout(3):
        if not cache_enabled() or type(expected_epoch) is not int or not await rag_access(repo, organization_id):
            return None
        if q_emb is None:
            q_emb = await embedding_query(testo)
        return await getattr(repo, "doc_repo", repo).faq_cache_store(
            organization_id, testo, risposta, q_emb,
            prompt_variant=prompt_variant, ttl_hours=ttl_hours(), expected_epoch=expected_epoch,
        )
