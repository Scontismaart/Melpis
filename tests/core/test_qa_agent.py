import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.asyncio


async def test_rispondi_requires_authenticated_provider_context():
    repo = AsyncMock()
    repo.search_similar = AsyncMock(return_value=[{
        "content": "Pizza margherita: 8 euro",
        "document_name": "menu.pdf",
        "metadata": {"fonte": "menu.pdf"},
        "distance": 0.1,
    }])
    from src.core.documenti.qa_agent import rispondi
    with patch("src.core.documenti.qa_agent.vettorizza",
               return_value=[[0.1] * 384]), \
         patch("src.core.documenti.qa_agent.crea_llm") as make_llm:
        out = await rispondi("org-1", "quanto costa la pizza?", repo, k=5)
    repo.search_similar.assert_awaited_once()
    make_llm.assert_not_called()
    assert "Non ho potuto analizzare" in out["risposta"]
    assert out["fonti"][0]["documento"] == "menu.pdf"


async def test_rispondi_senza_risultati_non_chiama_llm():
    repo = AsyncMock()
    repo.search_similar = AsyncMock(return_value=[])
    from src.core.documenti.qa_agent import rispondi
    with patch("src.core.documenti.qa_agent.vettorizza",
               return_value=[[0.1] * 384]), \
         patch("src.core.documenti.qa_agent.crea_llm") as mock_llm:
        out = await rispondi("org-1", "quanto costa?", repo)
    mock_llm.assert_not_called()
    assert out["fonti"] == []
