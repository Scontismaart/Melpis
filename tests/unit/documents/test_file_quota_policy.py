"""Policy and helper checks for original-upload byte accounting."""

import subprocess
import sys

import pytest

from src.core.billing import plans
from src.core.documenti.file_quota import (
    FileQuotaError,
    admit,
    policy_limit,
    snapshot,
    validate_file_evidence,
)


class QuotaConnection:
    def __init__(self, *, plan="starter", status="active", committed=0,
                 reserved=0, unknown=0):
        self.organization = {"plan": plan, "subscription_status": status}
        self.usage = {
            "committed": committed,
            "reserved": reserved,
            "unknown": unknown,
        }
        self.calls = []

    async def fetchrow(self, query, organization_id):
        self.calls.append(query)
        return self.organization if "FROM organizations" in query else self.usage


@pytest.mark.parametrize("slug", sorted(plans.PLANS))
def test_all_current_plans_include_rag_without_approved_file_budget(slug, monkeypatch):
    monkeypatch.setattr(plans.PLANS[slug], "rag_file_bytes_limit", None)
    plan = plans.PLANS[slug]
    assert plan.has_rag is True
    assert plan.rag_file_bytes_limit is None


def test_trial_rag_file_budget_is_unconfigured_by_default(monkeypatch):
    monkeypatch.setattr(plans, "TRIAL_RAG_FILE_BYTES_LIMIT", None)
    assert plans.TRIAL_RAG_FILE_BYTES_LIMIT is None
    assert policy_limit({"plan": "business", "subscription_status": "trialing"}) == (None, "trial")


def test_fresh_interpreter_sees_unconfigured_product_defaults():
    program = """
from src.core.billing import plans
from src.core.documenti.file_quota import policy_limit
assert plans.TRIAL_RAG_FILE_BYTES_LIMIT is None
assert all(plan.rag_file_bytes_limit is None for plan in plans.PLANS.values())
assert all(plan.has_rag for plan in plans.PLANS.values())
assert policy_limit({'plan': 'starter', 'subscription_status': 'active'}) == (None, 'paid')
assert policy_limit({'plan': None, 'subscription_status': 'trialing'}) == (None, 'trial')
print('default policy verified')
"""
    result = subprocess.run(
        [sys.executable, "-c", program], check=True, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "default policy verified"


def test_policy_selects_paid_or_trial_budget_without_crossing_scopes(monkeypatch):
    # Small numbers here are synthetic test inputs, not proposed commercial limits.
    monkeypatch.setattr(plans.PLANS["starter"], "rag_file_bytes_limit", 17)
    monkeypatch.setattr(plans, "TRIAL_RAG_FILE_BYTES_LIMIT", 9)

    assert policy_limit({"plan": "starter", "subscription_status": "active"}) == (17, "paid")
    assert policy_limit({"plan": "starter", "subscription_status": "trialing"}) == (9, "trial")


def test_file_evidence_requires_server_style_size_and_digest_pair():
    validate_file_evidence(0, None)
    validate_file_evidence(7, "a" * 64)

    for size, digest in ((True, "a" * 64), (-1, "a" * 64), (1, None), (1, "A" * 64),
                         (0, "a" * 64)):
        with pytest.raises(ValueError):
            validate_file_evidence(size, digest)


@pytest.mark.asyncio
async def test_snapshot_reports_unknown_documents_and_separate_reservations(monkeypatch):
    monkeypatch.setattr(plans.PLANS["starter"], "rag_file_bytes_limit", None)
    connection = QuotaConnection(committed=8, reserved=4, unknown=1)
    result = await snapshot(connection, "00000000-0000-0000-0000-000000000001")

    assert result == {
        "status": "not_configured",
        "limit_bytes": None,
        "scope": "paid",
        "committed_bytes": 8,
        "reserved_bytes": 4,
        "occupied_bytes": 12,
        "unknown_documents": 1,
        "remaining_bytes": None,
        "originals_retained": False,
    }


@pytest.mark.asyncio
async def test_admission_fails_closed_when_budget_is_unconfigured(monkeypatch):
    monkeypatch.setattr(plans.PLANS["starter"], "rag_file_bytes_limit", None)
    connection = QuotaConnection(plan="starter", committed=0, reserved=0, unknown=0)
    with pytest.raises(FileQuotaError, match="file_quota_not_configured"):
        await admit(connection, "00000000-0000-0000-0000-000000000001", None, 1)


@pytest.mark.asyncio
async def test_admission_uses_synthetic_plan_budget_and_allows_nonincremental_replace(monkeypatch):
    # Small numbers here are synthetic test inputs, not proposed commercial limits.
    monkeypatch.setattr(plans.PLANS["starter"], "rag_file_bytes_limit", 10)
    connection = QuotaConnection(plan="starter", committed=8, reserved=0, unknown=0)

    await admit(connection, "00000000-0000-0000-0000-000000000001",
                 {"file_bytes": 8, "index_file_bytes": None}, 5)
    with pytest.raises(FileQuotaError, match="file_quota_exceeded"):
        await admit(connection, "00000000-0000-0000-0000-000000000001", None, 3)


@pytest.mark.asyncio
async def test_trial_admission_uses_its_separate_synthetic_budget(monkeypatch):
    # Small numbers here are synthetic test inputs, not proposed commercial limits.
    monkeypatch.setattr(plans, "TRIAL_RAG_FILE_BYTES_LIMIT", 6)
    connection = QuotaConnection(plan="business", status="trialing")

    with pytest.raises(FileQuotaError, match="file_quota_exceeded"):
        await admit(connection, "00000000-0000-0000-0000-000000000001", None, 7)
