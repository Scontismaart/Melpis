import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.auth.dependencies import get_organization_context


@pytest.fixture
def client():
    return TestClient(app)


def test_conteggio_documenti_unauthorized(client):
    res = client.get("/api/documenti/conteggio")
    assert res.status_code == 401


def test_ui_summary_unauthorized(client):
    res = client.get("/api/ui/summary")
    assert res.status_code == 401


def test_conoscenza_summary_unauthorized(client):
    res = client.get("/api/conoscenza/summary")
    assert res.status_code == 401


def test_ui_summary_authorized(client):
    mock_org_id = str(uuid.uuid4())
    mock_user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "organization_id": mock_org_id,
        "ruolo": "staff",
        "email": "staff@example.com",
    }
    mock_repo = AsyncMock()
    mock_repo.get_ui_summary.return_value = {
        "richieste_pendenti": 2,
        "documenti_attivi": 5,
        "recensioni_da_gestire": 1,
    }

    app.dependency_overrides[get_organization_context] = lambda: mock_user
    with patch.object(app.state, "repo", mock_repo, create=True):
        res = client.get("/api/ui/summary")
        assert res.status_code == 200
        data = res.json()
        assert data["richieste_pendenti"] == 2
        mock_repo.get_ui_summary.assert_awaited_once_with(mock_org_id)
    app.dependency_overrides.pop(get_organization_context, None)


def test_chiedi_documenti_rag_blocked_by_plan(client):
    mock_org_id = str(uuid.uuid4())
    mock_user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "organization_id": mock_org_id,
        "ruolo": "owner",
        "email": "owner@example.com",
    }
    mock_repo = AsyncMock()
    # Plan 'starter' does not have RAG
    mock_repo.get_organization_billing.return_value = {
        "plan": "starter", "subscription_status": "active"
    }

    app.dependency_overrides[get_organization_context] = lambda: mock_user
    with patch.object(app.state, "repo", mock_repo, create=True):
        res = client.post("/api/documenti/chiedi", json={"domanda": "Quali sono i vostri orari?", "k": 3})
        assert res.status_code == 403
        assert "non include la Knowledge Base AI" in res.json()["detail"]
    app.dependency_overrides.pop(get_organization_context, None)


def test_carica_documento_empty_text_400(client):
    mock_user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "ruolo": "owner",
        "email": "owner@example.com",
    }
    app.dependency_overrides[get_organization_context] = lambda: mock_user
    res = client.post("/api/documenti/carica", json={"nome": "Menu", "testo": "   "})
    assert res.status_code == 400
    assert "vuoto" in res.json()["detail"].lower()
    app.dependency_overrides.pop(get_organization_context, None)


def test_carica_file_documento_success(client):
    mock_org_id = str(uuid.uuid4())
    mock_doc_id = str(uuid.uuid4())
    mock_user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "organization_id": mock_org_id,
        "ruolo": "owner",
        "email": "owner@example.com",
    }
    mock_repo = AsyncMock()
    mock_repo.get_organization_billing.return_value = {"plan": "business", "subscription_status": "active"}
    mock_repo.faq_cache_invalidate.return_value = None

    app.dependency_overrides[get_organization_context] = lambda: mock_user
    published_document = {"id": mock_doc_id, "stato": "indicizzata", "index_verified": True}
    with patch.object(app.state, "repo", mock_repo, create=True), \
         patch("src.api.routes.knowledge.index_content", new_callable=AsyncMock, return_value={
             "status": "indexed", "document": published_document, "indexed": 2,
         }) as publish:
        res = client.post(
            "/api/documenti/carica-file",
            files={"file": ("listino_speciale.txt", b"Il prezzo segreto del corso VIP e' 499 euro.\nInclude tutoraggio.", "text/plain")},
        )
        assert res.status_code == 200
        data = res.json()
        assert "Indicizzati" in data["detail"]
        assert data["nome"] == "listino_speciale.txt"
        assert data["id"] == mock_doc_id
        publish.assert_awaited_once()
        args, kwargs = publish.await_args
        assert args[:3] == (mock_repo, mock_org_id, "listino_speciale.txt")
        assert kwargs["file_content"].startswith(b"Il prezzo segreto")
        assert kwargs["tipo"] == "documento"
        assert kwargs["fonte"] == "listino_speciale.txt"
        assert kwargs["request_key"] is None
        assert kwargs["embedding_fn"] is not None
        # Cache publication/invalidation belongs to the canonical indexing service.
        mock_repo.faq_cache_invalidate.assert_not_awaited()
    app.dependency_overrides.pop(get_organization_context, None)


def test_carica_file_documento_empty_file_400(client):
    mock_user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "ruolo": "owner",
        "email": "owner@example.com",
    }
    mock_repo = AsyncMock()
    mock_repo.get_organization_billing.return_value = {"plan": "business", "subscription_status": "active"}
    app.dependency_overrides[get_organization_context] = lambda: mock_user
    with patch.object(app.state, "repo", mock_repo, create=True):
        res = client.post(
            "/api/documenti/carica-file",
            files={"file": ("vuoto.txt", b"", "text/plain")},
        )
        assert res.status_code == 400
        assert "vuoto" in res.json()["detail"].lower()
    app.dependency_overrides.pop(get_organization_context, None)


def test_carica_file_documento_too_large_413(client):
    mock_user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "ruolo": "owner",
        "email": "owner@example.com",
    }
    mock_repo = AsyncMock()
    mock_repo.get_organization_billing.return_value = {"plan": "business", "subscription_status": "active"}
    app.dependency_overrides[get_organization_context] = lambda: mock_user
    with patch.object(app.state, "repo", mock_repo, create=True):
        # 20MB + 10 bytes
        huge_payload = b"X" * (20 * 1024 * 1024 + 10)
        res = client.post(
            "/api/documenti/carica-file",
            files={"file": ("huge.txt", huge_payload, "text/plain")},
        )
        assert res.status_code == 413
        assert "supera il limite" in res.json()["detail"].lower()
    app.dependency_overrides.pop(get_organization_context, None)


@pytest.mark.asyncio
async def test_carica_file_documento_caps_read_before_size_check():
    from src.api.routes.knowledge import carica_file_documento

    mock_repo = AsyncMock()
    mock_file = MagicMock()
    mock_file.filename = "huge.txt"
    mock_file.read = AsyncMock(return_value=b"X" * (20 * 1024 * 1024 + 1))
    mock_request = MagicMock()
    user = {"organization_id": str(uuid.uuid4())}

    with patch("src.api.routes.knowledge.get_repo", return_value=mock_repo), \
         patch("src.api.routes.knowledge.check_feature_blocked_by_plan", new_callable=AsyncMock, return_value=None):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await carica_file_documento(mock_request, mock_file, None, user)

    assert exc.value.status_code == 413
    mock_file.read.assert_awaited_once_with(20 * 1024 * 1024 + 1)


def test_chiedi_documenti_success(client):
    mock_org_id = str(uuid.uuid4())
    auth_user_id = str(uuid.uuid4())
    mock_user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "auth_user_id": auth_user_id,
        "organization_id": mock_org_id,
        "ruolo": "owner",
        "email": "owner@example.com",
    }
    mock_repo = AsyncMock()
    mock_repo.get_organization_billing.return_value = {"plan": "business", "subscription_status": "active"}

    mock_qa_output = {
        "risposta": "Il prezzo del corso VIP e' 499 euro e include tutoraggio.",
        "fonti": [{"fonte": "listino_speciale.txt", "tipo": "documento"}],
        "richiede_operatore": False,
        "motivo": None,
        "_quota_final": object(),
    }
    quota_events = []

    class FakeQuotaReservations:
        def __init__(self, pool):
            quota_events.append(("init", pool))

        async def reserve(self, operation):
            quota_events.append(("reserve", operation))
            return {"status": "reserved", "ticket": object()}

        async def renew(self, operation, ticket):
            return ticket

        async def commit(self, operation, ticket, final):
            quota_events.append(("commit", operation, ticket, final))
            return {"risposta": mock_qa_output["risposta"], "fonti": [{
                "documento": "listino_speciale.txt", "tipo": "documento",
                "score": 0.1, "stato": "indicizzata", "is_active": True,
                "priorita": 3,
            }]}

        async def release(self, operation, ticket):
            quota_events.append(("release", operation, ticket))
            return True

    app.dependency_overrides[get_organization_context] = lambda: mock_user
    with patch.object(app.state, "repo", mock_repo, create=True), \
         patch("src.api.routes.knowledge.QuotaReservations", FakeQuotaReservations), \
         patch("src.api.routes.knowledge.rispondi", new_callable=AsyncMock, return_value=mock_qa_output):
        res = client.post(
            "/api/documenti/chiedi",
            json={"domanda": "Quanto costa il corso VIP?", "k": 3},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["risposta"] == "Il prezzo del corso VIP e' 499 euro e include tutoraggio."
        assert data["fonti"][0]["documento"] == "listino_speciale.txt"
        assert data["fonti"][0]["tipo"] == "documento"
        assert set(data) == {"risposta", "fonti"}
        assert all("quota" not in key.lower() and "ticket" not in key.lower()
                   for key in data)
        assert [event[0] for event in quota_events] == ["init", "reserve", "commit"]
        assert quota_events[1][1].channel == "qa"
        assert str(quota_events[1][1].auth_user_id) == auth_user_id
    app.dependency_overrides.pop(get_organization_context, None)


def test_elimina_documento_api_success(client):
    mock_org_id = str(uuid.uuid4())
    mock_doc_id = str(uuid.uuid4())
    mock_user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "organization_id": mock_org_id,
        "ruolo": "owner",
        "email": "owner@example.com",
    }
    mock_repo = AsyncMock()
    mock_repo.delete_document.return_value = 3  # 3 chunks deleted
    mock_repo.faq_cache_invalidate.return_value = None

    app.dependency_overrides[get_organization_context] = lambda: mock_user
    with patch.object(app.state, "repo", mock_repo, create=True), \
         patch("src.api.routes.knowledge.audit_event", new_callable=AsyncMock):
        res = client.delete(f"/api/documenti/{mock_doc_id}")
        assert res.status_code == 200
        data = res.json()
        assert "rimosso" in data["detail"].lower()
        assert data["chunk_eliminati"] == 3
        mock_repo.delete_document.assert_awaited_once_with(mock_org_id, mock_doc_id)
        mock_repo.faq_cache_invalidate.assert_awaited_once_with(mock_org_id)
    app.dependency_overrides.pop(get_organization_context, None)


def test_toggle_documento_api_success(client):
    mock_org_id = str(uuid.uuid4())
    mock_doc_id = str(uuid.uuid4())
    mock_user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "organization_id": mock_org_id,
        "ruolo": "owner",
        "email": "owner@example.com",
    }
    mock_repo = AsyncMock()
    mock_repo.toggle_document_active.return_value = {"id": mock_doc_id, "is_active": False}
    mock_repo.faq_cache_invalidate.return_value = None

    app.dependency_overrides[get_organization_context] = lambda: mock_user
    with patch.object(app.state, "repo", mock_repo, create=True):
        res = client.patch(f"/api/documenti/{mock_doc_id}/toggle")
        assert res.status_code == 200
        data = res.json()
        assert data["documento"]["is_active"] is False
        mock_repo.toggle_document_active.assert_awaited_once_with(mock_org_id, mock_doc_id)
    app.dependency_overrides.pop(get_organization_context, None)

