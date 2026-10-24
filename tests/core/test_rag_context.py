import asyncio
import time
import uuid

import pytest
from unittest.mock import AsyncMock, patch

from src.core.documenti.rag_context import ContestoDocumenti, recupera_contesto_documenti


@pytest.fixture(autouse=True)
def _no_real_embedding_model():
    with patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.documenti.rag_context.rag_access", new_callable=AsyncMock, return_value=True):
        yield


def _repo():
    repo = AsyncMock()
    repo.doc_repo = repo
    repo.knowledge_version = AsyncMock(return_value=7)
    return repo


async def test_formatta_chunk_con_nome_documento():
    repo = _repo()
    repo.search_similar = AsyncMock(return_value=[
        {"id": uuid.uuid4(), "content": "Apriamo alle 12:00",
         "metadata": {"fonte": "menu.pdf"}, "document_name": "menu.pdf"},
        {"id": uuid.uuid4(), "content": "Chiuso il lunedi'",
         "metadata": {}, "document_name": "orari.txt"},
    ])
    ctx = await recupera_contesto_documenti("org-a", "quando siete aperti?", repo)
    assert "-- menu.pdf --\nApriamo alle 12:00" in ctx.testo
    assert "-- orari.txt --\nChiuso il lunedi'" in ctx.testo
    # ContestoDocumenti espone anche i chunk strutturati per il guardrail
    # post-LLM (verifica grounding dei prezzi sul contenuto reale).
    assert isinstance(ctx, ContestoDocumenti)
    assert len(ctx.chunks) == 2
    assert ctx.chunks[0]["content"] == "Apriamo alle 12:00"
    assert ctx.knowledge_epoch == 7
    assert ctx.reason == "verified"
    repo.search_similar.assert_awaited_once_with("org-a", [0.1] * 384, 3)


async def test_q_emb_precalcolato_riusato_skip_vettorizza():
    """Il processor calcola l'embedding una volta sola (cache FAQ + RAG):
    se q_emb e' passato, vettorizza non viene chiamato di nuovo."""
    repo = _repo()
    repo.search_similar = AsyncMock(return_value=[
        {"content": "x", "document_name": "d.txt"}
    ])
    emb = [0.2] * 384
    ctx = await recupera_contesto_documenti("org-a", "testo", repo, q_emb=emb)
    assert ctx.testo != ""
    repo.search_similar.assert_awaited_once_with("org-a", emb, 3)
    # vettorizza non e' stato chiamato: il patch autouse avrebbe reso la
    # chiamata innocua, quindi verifichiamo via call count esplicito.
    from src.core.documenti.rag_context import vettorizza
    assert vettorizza.call_count == 0


async def test_senza_risultati_contesto_vuoto():
    repo = _repo()
    repo.search_similar = AsyncMock(return_value=[])
    ctx = await recupera_contesto_documenti("org-a", "testo", repo)
    assert ctx.testo == ""
    assert ctx.chunks == []
    assert not ctx


async def test_errore_retrieval_contesto_vuoto():
    async def broken(*args, **kwargs):
        raise RuntimeError("pgvector down")

    repo = _repo()
    repo.search_similar = AsyncMock(side_effect=broken)
    ctx = await recupera_contesto_documenti("org-a", "testo", repo)
    assert ctx.testo == ""
    assert ctx.chunks == []


async def test_errore_vettorizza_contesto_vuoto():
    repo = _repo()
    with patch("src.core.documenti.rag_context.vettorizza", side_effect=RuntimeError("no model")):
        ctx = await recupera_contesto_documenti("org-a", "testo", repo)
    assert ctx.testo == ""
    repo.search_similar.assert_not_awaited()


async def test_timeout_reale_scade_e_restituisce_vuoto():
    """asyncio.wait_for che scade DAVVERO: search_similar resta appeso oltre
    il timeout, la chiamata viene cancellata e il contesto e' vuoto. Se
    wait_for non scadesse, questo test durerebbe ~60s e fallirebbe."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def hanging(*args, **kwargs):
        started.set()
        await release.wait()
        return [{"content": "x", "document_name": "x.pdf"}]

    repo = _repo()
    repo.search_similar = AsyncMock(side_effect=hanging)

    start = time.monotonic()
    ctx = await recupera_contesto_documenti("org-a", "testo", repo, timeout=0.05)
    elapsed = time.monotonic() - start

    assert await started.wait()  # il retrieval e' partito davvero
    release.set()  # pulizia: sblocca la coroutine appesa (gia' cancellata)
    await asyncio.sleep(0)

    assert ctx.testo == ""
    assert elapsed < 2, f"wait_for non ha scaduto: elapsed={elapsed:.2f}s"


async def test_rifiuta_contesto_se_knowledge_version_cambia_durante_retrieval():
    repo = _repo()
    repo.knowledge_version = AsyncMock(side_effect=[7, 8])
    repo.search_similar = AsyncMock(return_value=[{"content": "dato vecchio", "document_name": "x.pdf"}])

    ctx = await recupera_contesto_documenti("org-a", "testo", repo)

    assert ctx.testo == ""
    assert ctx.chunks == []
    assert ctx.knowledge_epoch is None
    assert ctx.reason == "knowledge_changed"
