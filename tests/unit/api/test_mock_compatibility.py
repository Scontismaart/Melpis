import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from src.api.routes.common import (
    resolve_vettorizza,
    resolve_estrai_da_url,
    resolve_genera_risposta_recensione,
    check_feature_blocked_by_plan,
    get_billing_snapshot,
    next_event_id,
    get_shared_event_history,
)


def test_next_event_id_format():
    ev_id = next_event_id("msg")
    assert ev_id.startswith("msg-")
    parts = ev_id.split("-")
    assert len(parts) >= 3


def test_shared_event_history_singleton():
    history = get_shared_event_history()
    assert isinstance(history, list)


def test_resolve_vettorizza_intercepts_mock():
    fake_vectors = [[0.9] * 384]
    with patch("src.api.main.vettorizza", return_value=fake_vectors) as mock_vet:
        fn = resolve_vettorizza()
        result = fn(["test text"], tipo="passage")
        assert result == fake_vectors
        assert mock_vet.called


@pytest.mark.asyncio
async def test_resolve_estrai_da_url_intercepts_mock():
    fake_web = {"testo": "Contenuto estratto", "titolo": "Titolo Pagina"}
    with patch("src.api.main.estrai_da_url", new=AsyncMock(return_value=fake_web)) as mock_extractor:
        fn = resolve_estrai_da_url()
        result = await fn("https://example.com")
        assert result == fake_web
        assert mock_extractor.called


def test_resolve_genera_risposta_recensione_intercepts_mock():
    fake_review = MagicMock(bozza_risposta="Grazie per il feedback!", sentiment="positivo")
    with patch("src.api.main.genera_risposta_recensione", return_value=fake_review) as mock_gen:
        fn = resolve_genera_risposta_recensione()
        result = fn(testo="Ottimo cibo", stelle=5, autore="Luca")
        assert result == fake_review
        assert mock_gen.called


@pytest.mark.asyncio
async def test_check_feature_blocked_by_plan_trial_has_rag_policy():
    """Trial RAG access is separate from its nullable original-file budget."""
    mock_repo = MagicMock()
    mock_repo.get_organization_billing = AsyncMock(return_value={
        "plan": None,
        "subscription_status": "trialing",
        "trial_end": datetime.now(timezone.utc) + timedelta(days=3),
    })
    assert await check_feature_blocked_by_plan(mock_repo, "org-trial", "rag") is None
    assert await check_feature_blocked_by_plan(mock_repo, "org-trial", "recensioni") is None


@pytest.mark.asyncio
async def test_check_feature_blocked_by_plan_starter_allows_rag():
    """RAG access does not depend on the original-file quota."""
    mock_repo = MagicMock()
    mock_repo.get_organization_billing = AsyncMock(return_value={"plan": "starter", "subscription_status": "active"})
    res = await check_feature_blocked_by_plan(mock_repo, "org-base", "rag")
    assert res is None
