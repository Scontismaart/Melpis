"""Live inference is opt-in, never triggered by incidental local API keys."""
import os
from pathlib import Path

import pytest
import uuid

from src.core.rate_limit import reset_memory_rate_limiter


@pytest.fixture(autouse=True)
def synthetic_file_quota_policy(monkeypatch):
    """Test-only capacity for existing file fixtures; never a product default.

    Quota boundary tests override these synthetic values, including None.
    Runtime plan and trial budgets remain unconfigured pending approval.
    """
    from src.core.billing import plans
    for plan in plans.PLANS.values():
        monkeypatch.setattr(plan, "rag_file_bytes_limit", 1024 ** 3)
    monkeypatch.setattr(plans, "TRIAL_RAG_FILE_BYTES_LIMIT", 1024 ** 3)


# Local, ignored browser-QA helpers are executable scripts: importing them
# launches Playwright immediately.  Keep them available for manual visual
# checks without allowing pytest's filename discovery to execute them during
# the deterministic unit/integration suite.
_LOCAL_BROWSER_QA_SCRIPTS = frozenset({
    "browser_qa_runner.py",
    "browser_qa_runner_full.py",
    "capture_dashboard_views.py",
    "capture_full.py",
    "check_sections.py",
    "crop.py",
    "production_smoke_test.py",
    "test_inbox_responsive.py",
    "test_panoramica_responsive.py",
    "test_prenotazioni_responsive.py",
    "test_recensioni_responsive.py",
    "test_team_responsive.py",
    "verify_recovered_landing.py",
    "verify_regression_fix.py",
})


def pytest_ignore_collect(collection_path: Path, config):
    """Exclude executable local browser helpers from normal suite discovery."""
    if collection_path.parent == Path(__file__).parent:
        return collection_path.name in _LOCAL_BROWSER_QA_SCRIPTS
    return None


@pytest.fixture(autouse=True)
def isolate_memory_rate_limiter():
    """Keep request quotas deterministic and isolated between tests.

    Production uses the shared Redis backend.  Test and development runs use
    the process-wide in-memory backend, so without an explicit reset unrelated
    TestClient fixtures can collectively exhaust the 100-request window and
    make later tests fail with spurious HTTP 429 responses.
    """
    reset_memory_rate_limiter()
    yield
    reset_memory_rate_limiter()


@pytest.fixture
def install_test_identity():
    """Install a test-only JWT identity without reviving service-key user auth.

    Older route tests use a short opaque credential for convenience.  The
    override maps that credential to the same trusted identity shape produced
    by a verified Supabase JWT, while keeping production dependencies intact.
    """
    def install(
        app,
        api_key: str,
        *,
        default_org_id=None,
        ruolo="owner",
        user_id="00000000-0000-0000-0000-000000000001",
    ):
        from fastapi import Depends, Header, HTTPException
        from src.core.auth.dependencies import get_current_user, get_organization_context, get_token

        async def identity(
            token=Depends(get_token),
            x_organization_id: str | None = Header(None),
        ):
            if token is None:
                raise HTTPException(401, "Test session required")
            accepted = {api_key, f"apikey:{api_key}", f"{api_key}-aal1"}
            if token not in accepted:
                raise HTTPException(403, "Invalid test session")
            org_id = x_organization_id or default_org_id
            return {
                "source": "jwt",
                "aal": "aal1" if token.endswith("-aal1") else "aal2",
                "organization_id": str(org_id) if org_id else None,
                "ruolo": ruolo,
                "auth_user_id": user_id,
                "session_id": f"test-session-{user_id}",
                "user_id": user_id,
            }

        app.dependency_overrides[get_current_user] = identity
        app.dependency_overrides[get_organization_context] = identity

    return install


@pytest.fixture
def mock_provider_context_open(monkeypatch):
    """Authorize tests whose provider transport is mocked, never live.

    Tests that exercise provider accounting itself must use the PostgreSQL
    fixture and real ``ProviderAccountingContext.open`` instead.
    """
    from src.core.provider_accounting import ProviderAccountingContext
    from tests.provider_accounting_fakes import provider_context_stub

    opened = []

    async def open_test_context(
        _pool,
        organization_id,
        *,
        task_type,
        actor_auth_user_id=None,
        worker_message_id=None,
        simulation_request_id=None,
        simulation_claim_token=None,
        quota_operation=None,
        quota_ticket=None,
        context=None,
    ):
        test_context = provider_context_stub(
            task_type=task_type,
            organization_id=organization_id,
            quota_operation=quota_operation,
            quota_ticket=quota_ticket,
        )
        opened.append({
            "organization_id": uuid.UUID(str(organization_id)),
            "task_type": task_type,
            "actor_auth_user_id": actor_auth_user_id,
            "worker_message_id": worker_message_id,
            "simulation_request_id": simulation_request_id,
            "simulation_claim_token": simulation_claim_token,
            "quota_operation_id": getattr(quota_operation, "id", None),
            "quota_claim_id": getattr(quota_ticket, "claim_id", None),
            "context": context,
        })
        return test_context

    monkeypatch.setattr(
        ProviderAccountingContext, "open", staticmethod(open_test_context),
    )
    return opened


def pytest_addoption(parser):
    parser.addoption("--live-llm", action="store_true", default=False,
                     help="Allow explicitly confirmed Groq FREE account inference")


def pytest_configure(config):
    config.addinivalue_line("markers", "live_llm: real inference, disabled unless --live-llm")
    if config.getoption("--live-llm"):
        if (os.getenv("LLM_COST_POLICY", "free_only") != "free_only"
                or os.getenv("GROQ_FREE_ACCOUNT_CONFIRMED") != "true"
                or not os.getenv("GROQ_API_KEY")):
            raise pytest.UsageError("Live tests require free_only and a confirmed Groq FREE account")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live-llm"):
        for item in items:
            if item.get_closest_marker("live_llm"):
                item.add_marker(pytest.mark.skip(reason="Live LLM disabled: requires explicit --live-llm"))
