import pytest
from datetime import datetime, timezone, timedelta

import src.api.main as api_main


class FakeRepo:
    def __init__(self, billing):
        self._billing = billing

    async def get_organization_billing(self, org_id):
        if self._billing is None:
            raise ValueError("org inesistente")
        return self._billing


@pytest.mark.asyncio
async def test_consentire_rag_su_piano_starter():
    repo = FakeRepo({"plan": "starter", "subscription_status": "active"})
    assert await api_main._piano_blocca_feature(repo, "org-1", "rag") is None


@pytest.mark.asyncio
async def test_consentire_rag_su_business():
    repo = FakeRepo({"plan": "business", "subscription_status": "active"})
    assert await api_main._piano_blocca_feature(repo, "org-1", "rag") is None


@pytest.mark.asyncio
async def test_blocca_recensioni_su_starter_non_su_pro():
    repo = FakeRepo({"plan": "starter", "subscription_status": "active"})
    assert (await api_main._piano_blocca_feature(repo, "org-1", "recensioni")) is not None
    repo2 = FakeRepo({"plan": "pro", "subscription_status": "active"})
    assert await api_main._piano_blocca_feature(repo2, "org-1", "recensioni") is None


@pytest.mark.asyncio
async def test_trial_senza_piano_accesso_pro():
    # Trial e piani paid hanno RAG; il budget originale file ha policy separata.
    repo = FakeRepo({"plan": None, "subscription_status": "trialing", "trial_end": datetime.now(timezone.utc) + timedelta(days=3)})
    assert await api_main._piano_blocca_feature(repo, "org-1", "rag") is None
    assert await api_main._piano_blocca_feature(repo, "org-1", "recensioni") is None


@pytest.mark.asyncio
async def test_billing_assente_blocca_failclosed():
    repo = FakeRepo(None)
    assert await api_main._piano_blocca_feature(repo, "org-1", "rag") is not None


@pytest.mark.asyncio
async def test_unknown_plan_slug_fails_closed(caplog):
    import logging
    repo = FakeRepo({"plan": "piano_inesistente", "subscription_status": "active"})
    with caplog.at_level(logging.WARNING):
        msg = await api_main._piano_blocca_feature(repo, "org-1", "rag")
    assert msg is not None
    assert "piano_inesistente" in msg
    assert "non riconosciuta" in msg
    assert "piano sconosciuto 'piano_inesistente'" in caplog.text


@pytest.mark.asyncio
async def test_canceled_subscription_blocks_features_even_on_business():
    # Invariante 8: anche se la colonna plan è 'business', un'utenza canceled è bloccata
    repo = FakeRepo({"plan": "business", "subscription_status": "canceled"})
    msg = await api_main._piano_blocca_feature(repo, "org-1", "rag")
    assert msg is not None
    assert "Abbonamento sospeso o scaduto" in msg


@pytest.mark.asyncio
async def test_unpaid_subscription_blocks_features():
    repo = FakeRepo({"plan": "business", "subscription_status": "unpaid"})
    msg = await api_main._piano_blocca_feature(repo, "org-1", "rag")
    assert msg is not None
    assert "Abbonamento sospeso o scaduto" in msg


@pytest.mark.asyncio
async def test_incomplete_expired_subscription_blocks_features():
    repo = FakeRepo({"plan": "business", "subscription_status": "incomplete_expired"})
    msg = await api_main._piano_blocca_feature(repo, "org-1", "recensioni")
    assert msg is not None
    assert "Abbonamento sospeso o scaduto" in msg


@pytest.mark.asyncio
async def test_expired_trial_blocks_features():
    from datetime import datetime, timezone, timedelta
    expired = datetime.now(timezone.utc) - timedelta(days=2)
    repo = FakeRepo({"plan": None, "subscription_status": "trialing", "trial_end": expired})
    msg = await api_main._piano_blocca_feature(repo, "org-1", "recensioni")
    assert msg is not None
    assert "Abbonamento sospeso o scaduto" in msg


@pytest.mark.asyncio
async def test_active_trial_allows_pro_features():
    from datetime import datetime, timezone, timedelta
    future = datetime.now(timezone.utc) + timedelta(days=5)
    repo = FakeRepo({"plan": None, "subscription_status": "trialing", "trial_end": future})
    assert await api_main._piano_blocca_feature(repo, "org-1", "recensioni") is None


@pytest.mark.asyncio
async def test_new_registration_null_status_is_blocked():
    # No billing state still fails closed, despite RAG being present on every plan.
    repo = FakeRepo({"plan": None, "subscription_status": None, "trial_end": None})
    rag_msg = await api_main._piano_blocca_feature(repo, "org-1", "rag")
    assert rag_msg is not None
    assert "sospeso" in rag_msg.lower()

    # Reviews remain plan-gated independently.
    reviews_msg = await api_main._piano_blocca_feature(repo, "org-1", "recensioni")
    assert reviews_msg is not None


@pytest.mark.parametrize("status", [None, "unknown", "incomplete", "incomplete_expired", "paused", "unpaid", "canceled"])
async def test_all_non_entitled_states_block(status):
    assert await api_main._piano_blocca_feature(FakeRepo({"plan": "business", "subscription_status": status}), "org-1", "rag")


async def test_accounting_hold_blocks_ai_features():
    repo = FakeRepo({"plan": "business", "subscription_status": "active", "ai_accounting_blocked": True})
    assert await api_main._piano_blocca_feature(repo, "org-1", "rag")


async def test_missing_paid_plan_is_not_a_trial():
    assert await api_main._piano_blocca_feature(FakeRepo({"plan": None, "subscription_status": "active"}), "org-1", "recensioni")


