import pytest

ORG_ID = "8b13d4ca-995d-45f5-8b94-3d823d71168c"


@pytest.mark.asyncio
async def test_rag_query_passes_authorized_organization_to_shared_embed(monkeypatch):
    from src.core.documenti import isolated, rag_context

    calls = []

    async def embed(texts, tipo="passage", *, timeout=3.0, organization_id=None):
        calls.append((texts, tipo, organization_id))
        return [[1.0] + [0.0] * 383]

    class Repo:
        async def knowledge_version(self, organization_id):
            return 1

        async def search_similar(self, organization_id, embedding, k):
            return []

    monkeypatch.setattr(isolated, "embed_local", embed)
    monkeypatch.setattr(rag_context, "rag_access", lambda repo, organization_id: _yes())
    await rag_context.recupera_contesto_documenti(ORG_ID, "domanda", Repo())
    assert calls == [(["domanda"], "query", ORG_ID)]


async def _yes():
    return True


@pytest.mark.asyncio
async def test_faq_lookup_passes_same_organization_to_shared_embed(monkeypatch):
    from src.core.documenti import isolated
    from src.core.guardrails import faq_cache

    calls = []

    async def embed(texts, tipo="passage", *, timeout=3.0, organization_id=None):
        calls.append((texts, tipo, organization_id))
        return [[1.0] + [0.0] * 383]

    class Repo:
        async def faq_cache_lookup(self, organization_id, embedding, threshold):
            return None

    monkeypatch.setenv("GUARDRAIL_CACHE_ENABLED", "true")
    monkeypatch.setattr(isolated, "embed_local", embed)
    monkeypatch.setattr(faq_cache, "rag_access", lambda repo, organization_id: _yes())
    result = await faq_cache.cerca_in_cache(ORG_ID, "domanda", Repo())
    assert result is None
    assert calls == [(["domanda"], "query", ORG_ID)]
