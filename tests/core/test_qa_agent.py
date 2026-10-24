import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_rispondi_requires_authenticated_provider_context():
    repo = AsyncMock()
    repo.doc_repo = repo
    repo.knowledge_version = AsyncMock(return_value=4)
    doc_id = uuid.uuid4()
    repo.search_similar = AsyncMock(return_value=[{
        "content": "Pizza margherita: 8 euro",
        "document_name": "menu.pdf",
        "metadata": {"fonte": "menu.pdf"},
        "distance": 0.1,
        "document_id": doc_id,
        "revision": 3,
    }])
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{"id": doc_id, "revision": 3}])
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=None)
    repo.pool = MagicMock()
    repo.pool.acquire.return_value = acquire
    org_id = str(uuid.uuid4())
    from src.core.documenti.qa_agent import rispondi
    with patch("src.core.documenti.access.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.core.documenti.rag_context.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.documenti.qa_agent.crea_llm") as make_llm:
        out = await rispondi(org_id, "quanto costa la pizza?", repo, k=5)
    repo.search_similar.assert_awaited_once()
    current_query, current_org, current_doc_ids = conn.fetch.await_args.args
    assert "index_verified" in current_query
    assert "revision" in current_query
    assert current_org == uuid.UUID(org_id)
    assert current_doc_ids == [doc_id]
    make_llm.assert_not_called()
    assert "Non ho potuto analizzare" in out["risposta"]
    # The source identity was current, but no authenticated provider context
    # was available; the route must not expose an answer with stale provenance.
    assert out["fonti"] == []


async def test_rispondi_senza_risultati_non_chiama_llm():
    repo = AsyncMock()
    repo.doc_repo = repo
    repo.knowledge_version = AsyncMock(return_value=4)
    repo.search_similar = AsyncMock(return_value=[])
    from src.core.documenti.qa_agent import rispondi
    with patch("src.core.documenti.access.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.core.documenti.rag_context.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.core.documenti.rag_context.vettorizza", return_value=[[0.1] * 384]), \
         patch("src.core.documenti.qa_agent.crea_llm") as mock_llm:
        out = await rispondi("org-1", "quanto costa?", repo)
    mock_llm.assert_not_called()
    assert out["fonti"] == []
