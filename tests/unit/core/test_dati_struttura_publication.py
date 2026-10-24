from unittest.mock import AsyncMock

import pytest

from src.core.documenti import dati_struttura


class ProfileRepo:
    def __init__(self, events, docs=None):
        self.events = events
        self.docs = docs or []

    async def get_org_business_profile(self, organization_id):
        return {"descrizione": "Profilo manuale"}

    async def update_org_business_profile(self, organization_id, profile):
        self.events.append(("profile", dict(profile)))

    async def faq_cache_invalidate(self, organization_id):
        self.events.append(("cache", organization_id))

    async def list_sources(self, organization_id, *, tipo):
        return self.docs


@pytest.mark.asyncio
async def test_profile_is_saved_before_atomic_publication_to_existing_document(monkeypatch):
    events = []
    repo = ProfileRepo(events, docs=[{"id": "existing-doc"}])
    rag_check = AsyncMock(return_value=True)

    async def publish(*args, **kwargs):
        events.append(("publish", kwargs))
        return {"status": "indexed", "document": {
            "id": "existing-doc", "index_verified": True,
        }, "indexed": 1}

    monkeypatch.setattr(dati_struttura, "rag_access", rag_check)
    monkeypatch.setattr(dati_struttura, "index_content", publish)

    result = await dati_struttura.indicizza_dati_struttura(
        repo, "org-1", "09:00-17:00", [{"nome": "Taglio", "prezzo": 25}]
    )

    assert events[0][0] == "profile"
    assert events[0][1]["orari"] == "09:00-17:00"
    assert events.index(next(event for event in events if event[0] == "profile")) < events.index(
        next(event for event in events if event[0] == "publish")
    )
    publish_kwargs = next(event[1] for event in events if event[0] == "publish")
    assert publish_kwargs["document_id"] == "existing-doc"
    assert publish_kwargs["tipo"] == "dati_struttura"
    assert [event[0] for event in events].count("profile") == 1
    assert result["doc_id"] == "existing-doc"
    assert result["index_status"] == "indexed"
    assert result["index_verified"] is True


@pytest.mark.asyncio
async def test_profile_remains_saved_when_rag_is_unavailable(monkeypatch):
    events = []
    repo = ProfileRepo(events)
    monkeypatch.setattr(dati_struttura, "rag_access", AsyncMock(return_value=False))
    publish = AsyncMock()
    monkeypatch.setattr(dati_struttura, "index_content", publish)

    result = await dati_struttura.indicizza_dati_struttura(
        repo, "org-1", "09:00-17:00", [{"nome": "Taglio", "prezzo": 25}]
    )

    assert events[0][0] == "profile"
    assert events[0][1]["servizi_strutturati"] == [{"nome": "Taglio", "prezzo": 25}]
    publish.assert_not_awaited()
    assert result["doc_id"] is None
    assert result["index_status"] == "not_indexed"
    assert result["index_verified"] is False


@pytest.mark.asyncio
async def test_in_progress_status_is_returned_without_certifying_index(monkeypatch):
    repo = ProfileRepo([])
    monkeypatch.setattr(dati_struttura, "rag_access", AsyncMock(return_value=True))
    monkeypatch.setattr(dati_struttura, "index_content", AsyncMock(return_value={
        "status": "in_progress", "document": {"id": "pending-doc", "index_verified": False},
    }))

    result = await dati_struttura.indicizza_dati_struttura(repo, "org-1", "", [])

    assert result["doc_id"] == "pending-doc"
    assert result["index_status"] == "in_progress"
    assert result["index_verified"] is False
