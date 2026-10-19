from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from tests.provider_accounting_fakes import provider_context_stub


ORG_A = "00000000-0000-0000-0000-00000000000a"
ORG_B = "00000000-0000-0000-0000-00000000000b"
TOKEN_ORG_USER_ONE = "verified-user-one"
TOKEN_ORG_USER_TWO = "verified-user-two"
TOKEN_OTHER_ORG = "verified-user-other-org"

PREVIEW_PAYLOAD = {
    "profilo": {
        "verticale": "parrucchiere",
        "nome_attivita": "Studio Capelli",
        "orari": "Mar-Sab 09:00-19:00",
        "tono": "gentile",
        "servizi": ["Taglio"],
    },
    "messaggio": "Avete posto oggi?",
}


@pytest.fixture
def preview_api(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")

    from src.api.main import app
    from src.api.routes import organization
    from src.core.auth.dependencies import get_organization_context
    from src.models.schemas import RispostaOutput

    identities = {
        TOKEN_ORG_USER_ONE: {
            "organization_id": ORG_A,
            "auth_user_id": "user-one",
            "user_id": "member-one",
        },
        TOKEN_ORG_USER_TWO: {
            "organization_id": ORG_A,
            "auth_user_id": "user-two",
            "user_id": "member-two",
        },
        TOKEN_OTHER_ORG: {
            "organization_id": ORG_B,
            "auth_user_id": "user-three",
            "user_id": "member-three",
        },
    }

    async def trusted_membership(request: Request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        identity = identities.get(token)
        if identity is None:
            raise HTTPException(status_code=401, detail="Token o API Key richiesti")
        return {**identity, "source": "jwt", "ruolo": "owner", "aal": "aal2"}

    repo = object()
    output = RispostaOutput(
        risposta="Sì, abbiamo disponibilità.",
        richiede_umano=False,
        motivo="",
        categoria="generico",
    )
    get_repo = MagicMock(return_value=repo)
    billing = AsyncMock(return_value={"subscription_status": "active", "messages_limit": 500, "messages_used_this_period": 0})
    generate_preview = AsyncMock(return_value=output)
    context = provider_context_stub()
    open_context = AsyncMock(return_value=context)

    app.dependency_overrides[get_organization_context] = trusted_membership
    monkeypatch.setattr(organization, "get_repo", get_repo)
    monkeypatch.setattr(organization, "get_billing_snapshot", billing)
    monkeypatch.setattr(organization, "generate_preview", generate_preview)
    monkeypatch.setattr(organization.ProviderAccountingContext, "open", open_context)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield {
            "client": client,
            "repo": get_repo,
            "billing": billing,
            "generate": generate_preview,
            "open_context": open_context,
        }
    app.dependency_overrides.clear()


def _preview(api, token=TOKEN_ORG_USER_ONE, *, organization_header=None):
    headers = {"Authorization": f"Bearer {token}"}
    if organization_header is not None:
        headers["X-Organization-Id"] = organization_header
    return api["client"].post(
        "/api/onboarding/preview", json=PREVIEW_PAYLOAD, headers=headers
    )


def test_preview_allows_ten_per_organization_then_blocks_eleventh(preview_api):
    responses = [_preview(preview_api) for _ in range(11)]

    assert [response.status_code for response in responses] == [200] * 10 + [429]
    assert preview_api["generate"].await_count == 10
    assert preview_api["repo"].call_count == 10


def test_preview_counter_is_shared_by_users_and_ignores_forged_org_header(preview_api):
    first_user = [
        _preview(preview_api, organization_header=ORG_B).status_code for _ in range(6)
    ]
    second_user = [
        _preview(preview_api, TOKEN_ORG_USER_TWO, organization_header=ORG_B).status_code
        for _ in range(5)
    ]

    assert first_user == [200] * 6
    assert second_user == [200] * 4 + [429]
    assert preview_api["generate"].await_count == 10
    assert [call.args for call in preview_api["billing"].await_args_list] == [
        (preview_api["repo"].return_value, ORG_A)
    ] * 10


def test_preview_rate_limit_is_separate_between_organizations(preview_api):
    org_a_responses = [_preview(preview_api).status_code for _ in range(10)]
    org_b_responses = [
        _preview(preview_api, TOKEN_OTHER_ORG).status_code for _ in range(10)
    ]
    org_a_eleventh = _preview(preview_api).status_code

    assert org_a_responses == [200] * 10
    assert org_b_responses == [200] * 10
    assert org_a_eleventh == 429
    assert preview_api["generate"].await_count == 20


def test_preview_limiter_failure_stops_repository_and_generation(
    preview_api, monkeypatch
):
    from src.core import rate_limit

    class FailsClosedForPreview:
        async def hit(self, key, limit, window_seconds):
            if key == f"org:{ORG_A}:onboarding_preview":
                raise RuntimeError("limiter unavailable")
            return False

    monkeypatch.setattr(
        rate_limit,
        "get_rate_limiter",
        AsyncMock(return_value=FailsClosedForPreview()),
    )

    response = _preview(preview_api)

    assert response.status_code == 500
    preview_api["repo"].assert_not_called()
    preview_api["billing"].assert_not_awaited()
    preview_api["generate"].assert_not_awaited()


def test_preview_missing_auth_never_generates(preview_api):
    response = preview_api["client"].post(
        "/api/onboarding/preview", json=PREVIEW_PAYLOAD
    )

    assert response.status_code == 401
    preview_api["repo"].assert_not_called()
    preview_api["generate"].assert_not_awaited()


def test_preview_missing_organization_never_generates(preview_api):
    from src.api.main import app
    from src.core.auth.dependencies import get_organization_context

    async def trusted_user_without_membership(request: Request):
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_ORG_USER_ONE}"
        return {"source": "jwt", "ruolo": "owner", "organization_id": None}

    app.dependency_overrides[get_organization_context] = trusted_user_without_membership
    response = _preview(preview_api)

    assert response.status_code == 401
    preview_api["repo"].assert_not_called()
    preview_api["generate"].assert_not_awaited()


def test_global_llm_limit_includes_onboarding_preview(preview_api, monkeypatch):
    from src.api import main

    monkeypatch.setattr(main, "LLM_GLOBAL_RATE_LIMIT", 0)

    response = _preview(preview_api)

    assert response.status_code == 429
    assert preview_api["repo"].call_count == 0
    preview_api["generate"].assert_not_awaited()
