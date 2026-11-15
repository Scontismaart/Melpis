from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.core.auth.dependencies import get_organization_context
from src.core.auth.signup_policy import beta_email_is_allowed


def test_beta_roster_is_exact_normalized_and_fail_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.delenv("MELPIS_BETA_ALLOWED_EMAILS", raising=False)
    assert not beta_email_is_allowed("owner@example.test")

    monkeypatch.setenv("MELPIS_BETA_ALLOWED_EMAILS", " Owner@Example.Test , second@example.test ")
    assert beta_email_is_allowed("owner@example.test")
    assert beta_email_is_allowed(" SECOND@example.test ")
    assert not beta_email_is_allowed("owner@example.test.attacker.invalid")
    assert not beta_email_is_allowed(None)


def test_unknown_environment_requires_roster(monkeypatch):
    monkeypatch.setenv("APP_ENV", "preproduction")
    monkeypatch.delenv("MELPIS_BETA_ALLOWED_EMAILS", raising=False)
    assert not beta_email_is_allowed("owner@example.test")


def test_local_development_keeps_self_signup_available(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("MELPIS_BETA_ALLOWED_EMAILS", raising=False)
    assert beta_email_is_allowed("owner@example.test")


def _request_with_repo(repo):
    app = SimpleNamespace(
        state=SimpleNamespace(repo=repo),
        dependency_overrides={},
    )
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/example",
        "raw_path": b"/api/example",
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
        "app": app,
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_unlisted_first_me_user_cannot_jit_provision(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.delenv("MELPIS_BETA_ALLOWED_EMAILS", raising=False)
    repo = SimpleNamespace(
        get_memberships_by_auth=AsyncMock(return_value=[]),
        get_or_create_organization_with_owner=AsyncMock(),
    )

    with pytest.raises(HTTPException) as denied:
        await get_organization_context(
            _request_with_repo(repo),
            current_user={
                "source": "jwt",
                "auth_user_id": "synthetic-auth-id",
                "email": "unlisted@example.test",
            },
            x_organization_id=None,
        )

    assert denied.value.status_code == 403
    repo.get_or_create_organization_with_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_member_keeps_access_without_roster_entry(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.delenv("MELPIS_BETA_ALLOWED_EMAILS", raising=False)
    membership = {
        "organization_id": "synthetic-org-id",
        "ruolo": "staff",
        "user_id": "synthetic-user-id",
    }
    repo = SimpleNamespace(
        get_memberships_by_auth=AsyncMock(return_value=[membership]),
        get_or_create_organization_with_owner=AsyncMock(),
    )

    result = await get_organization_context(
        _request_with_repo(repo),
        current_user={
            "source": "jwt",
            "auth_user_id": "synthetic-auth-id",
            "email": "existing@example.test",
        },
        x_organization_id=None,
    )

    assert result["organization_id"] == "synthetic-org-id"
    repo.get_or_create_organization_with_owner.assert_not_awaited()
