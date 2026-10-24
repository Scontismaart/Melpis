"""test_route_mock_interception.py
-----------------------------------
Regression test suite guaranteeing that @patch("src.api.main.<symbol>")
actually intercepts execution through modular FastAPI routers without silent bypass
or external API key requirements (Invariants 1, 8, 10).
"""

import uuid
from contextlib import asynccontextmanager
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.auth.dependencies import get_organization_context
from src.core.documenti.rag_context import ContestoDocumenti
from tests.provider_accounting_fakes import provider_context_stub


@pytest.fixture
def clean_env(monkeypatch):
    """Garantisce l'assenza totale di API keys esterne per prevenire chiamate reali."""
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def mock_auth():
    org_id = str(uuid.uuid4())
    user = {
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
        "organization_id": org_id,
        "ruolo": "owner",
        "email": "owner@example.com",
    }
    app.dependency_overrides[get_organization_context] = lambda: user
    yield org_id, user
    app.dependency_overrides.pop(get_organization_context, None)


@pytest.fixture
def client():
    return TestClient(app)


def _mock_canonical_publication(repo):
    """Mock the tenant document repository at the begin/publish boundary."""
    doc_repo = MagicMock()
    document_id = str(uuid.uuid4())
    doc_repo.begin_index = AsyncMock(return_value={
        "status": "claimed", "document": {"id": document_id},
        "token": uuid.uuid4(), "fence": 1,
    })
    doc_repo.publish_index = AsyncMock(return_value={
        "id": document_id, "index_verified": True,
        "metadata": {"chunks": 1}, "stato": "indicizzata",
    })
    doc_repo.fail_index = AsyncMock(return_value=True)
    repo.doc_repo = doc_repo
    return doc_repo


def test_carica_testo_intercepts_main_vettorizza(client, mock_auth, clean_env):
    """Verifica che POST /api/documenti/carica intercetti la patch su src.api.main.vettorizza."""
    org_id, _ = mock_auth
    fake_vectors = [[0.1] * 384]

    mock_repo = MagicMock()
    doc_repo = _mock_canonical_publication(mock_repo)

    with patch.object(app.state, "repo", mock_repo, create=True), \
         patch("src.api.routes.knowledge.check_feature_blocked_by_plan", new_callable=AsyncMock, return_value=None), \
         patch("src.core.documenti.indexing.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.api.main.vettorizza", return_value=fake_vectors) as mock_vett:
        res = client.post("/api/documenti/carica", json={
            "testo": "Menu speciale della casa: Risotto ai funghi porcini.",
            "nome": "menu_speciale",
        })
        assert res.status_code == 200
        assert mock_vett.called, "patch('src.api.main.vettorizza') NON ha intercettato la route /api/documenti/carica!"
        doc_repo.begin_index.assert_awaited_once()
        doc_repo.publish_index.assert_awaited_once()


def test_importa_url_intercepts_main_estrai_e_vettorizza(client, mock_auth, clean_env):
    """Verifica che POST /api/conoscenza/web intercetti sia estrai_da_url che vettorizza."""
    org_id, _ = mock_auth
    fake_web = {"testo": "Contenuto della pagina web", "titolo": "Homepage"}
    fake_vectors = [[0.2] * 384]

    mock_repo = MagicMock()
    doc_repo = _mock_canonical_publication(mock_repo)

    with patch.object(app.state, "repo", mock_repo, create=True), \
         patch("src.api.routes.knowledge.check_feature_blocked_by_plan", new_callable=AsyncMock, return_value=None), \
         patch("src.core.documenti.indexing.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.api.main.estrai_da_url", new=AsyncMock(return_value=fake_web)) as mock_estrai, \
         patch("src.api.main.vettorizza", return_value=fake_vectors) as mock_vett:
        res = client.post("/api/conoscenza/web", json={
            "url": "https://example.com/orari-e-servizi",
            "titolo": "Orari Web",
        })
        assert res.status_code == 200
        assert mock_estrai.called, "patch('src.api.main.estrai_da_url') NON ha intercettato la route /api/conoscenza/web!"
        assert mock_vett.called, "patch('src.api.main.vettorizza') NON ha intercettato la route /api/conoscenza/web!"
        doc_repo.begin_index.assert_awaited_once()
        doc_repo.publish_index.assert_awaited_once()


def test_simulatore_recensione_intercepts_genera_risposta(client, mock_auth, clean_env):
    """Verifica che POST /api/recensione intercetti genera_risposta_recensione."""
    org_id, _ = mock_auth
    fake_review = MagicMock(
        bozza_risposta="Grazie per la recensione!",
        sentiment="positivo",
        ragionamento="Cliente felice",
        motivo="Ottima esperienza",
        categoria="servizio",
        richiede_revisione_urgente=False,
    )

    mock_repo = MagicMock()
    mock_repo.get_organization_billing = AsyncMock(return_value={"plan": "business", "subscription_status": "active"})
    mock_repo.get_onboarding_profile = AsyncMock(return_value={})
    mock_repo.get_review_by_external_id = AsyncMock(return_value=None)
    mock_repo.create_review = AsyncMock(return_value={"id": str(uuid.uuid4())})
    mock_repo.record_usage = AsyncMock()
    mock_repo.doc_repo = mock_repo
    mock_repo.knowledge_version = AsyncMock(return_value=7)

    @asynccontextmanager
    async def claim(*_args, **_kwargs):
        yield True

    async def open_accounting_context(pool, organization_id, *, task_type,
                                      actor_auth_user_id=None, worker_message_id=None,
                                      context=None):
        return provider_context_stub(task_type=task_type, organization_id=organization_id)

    with patch.object(app.state, "repo", mock_repo, create=True), \
         patch("src.api.main.genera_risposta_recensione", return_value=fake_review) as mock_gen, \
         patch("src.core.provider_accounting.ProviderAccountingContext.open", new=AsyncMock(side_effect=open_accounting_context)), \
         patch("src.core.reviews.ai_governance.authorize_review_generation", new_callable=AsyncMock, return_value={"plan": "business"}), \
         patch("src.core.documenti.rag_context.recupera_contesto_documenti", new_callable=AsyncMock, return_value=ContestoDocumenti(
             testo="knowledge", chunks=[{"content": "knowledge"}], knowledge_epoch=7, reason="verified")), \
         patch("src.core.documenti.rag_context.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.core.reviews.idempotency.claim_external_review", claim):
        res = client.post("/api/recensione", json={
            "testo": "Personale gentilissimo e cibo squisito.",
            "valutazione_stelle": 5,
            "autore": "Mario Rossi",
            "lingua": "it",
            "external_id": "unit-review-1",
        })
        assert res.status_code == 200
        assert mock_gen.called, "patch('src.api.main.genera_risposta_recensione') NON ha intercettato la route!"
        assert res.json()["bozza_risposta"] == "Grazie per la recensione!"


def test_onboarding_profilo_indicizza_dati_struttura_intercepts_vettorizza(client, mock_auth, clean_env):
    """Verifica che il salvataggio del profilo onboarding con orari intercetti vettorizza senza fallire."""
    org_id, _ = mock_auth
    fake_vectors = [[0.3] * 384]

    mock_repo = MagicMock()
    mock_repo.get_org_business_profile = AsyncMock(return_value={"servizi_strutturati": []})
    mock_repo.list_sources = AsyncMock(return_value=[])
    doc_repo = _mock_canonical_publication(mock_repo)
    mock_repo.save_onboarding_profile = AsyncMock(return_value={"nome_attivita": "Ristorante Da Mario"})
    mock_repo.faq_cache_invalidate = AsyncMock()
    mock_repo.update_org_business_profile = AsyncMock()

    with patch.object(app.state, "repo", mock_repo, create=True), \
         patch("src.core.documenti.dati_struttura.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.core.documenti.indexing.rag_access", new_callable=AsyncMock, return_value=True), \
         patch("src.api.main.vettorizza", return_value=fake_vectors) as mock_vett, \
         patch("src.api.routes.organization.audit_event", new_callable=AsyncMock):
        res = client.post("/api/onboarding/profilo", json={
            "verticale": "ristorante",
            "nome_attivita": "Ristorante Da Mario",
            "orari": "Lun-Dom 12:00 - 23:00",
            "tono": "caloroso",
            "regole_escalation": [],
        })
        assert res.status_code == 200
        assert mock_vett.called, "patch('src.api.main.vettorizza') NON ha intercettato indicizza_dati_struttura in onboarding!"
        doc_repo.begin_index.assert_awaited_once()
        doc_repo.publish_index.assert_awaited_once()
