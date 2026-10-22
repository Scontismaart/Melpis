import uuid
from types import SimpleNamespace
import pytest
from unittest.mock import AsyncMock
from fastapi import HTTPException

from src.core.auth.dependencies import (
    get_token,
    get_current_user,
    get_organization_context,
    require_ruolo,
    get_repo,
    get_optional_organization_context,
)


class TestGetToken:
    async def test_no_token_returns_none(self):
        result = await get_token(request=_fake_request(), authorization=None, x_api_key=None)
        assert result is None

    async def test_bearer_token_extracted(self):
        result = await get_token(request=_fake_request(), authorization="Bearer my.jwt.token", x_api_key=None)
        assert result == "my.jwt.token"

    async def test_invalid_authorization_scheme_is_rejected(self):
        with pytest.raises(HTTPException) as exc:
            await get_token(request=_fake_request(), authorization="Basic credentials", x_api_key=None)
        assert exc.value.status_code == 401

    async def test_api_key_prefixed(self):
        result = await get_token(request=_fake_request(), authorization=None, x_api_key="sk-test-123")
        assert result == "apikey:sk-test-123"

    async def test_bff_cookie_token(self):
        import src.core.auth.bff as bff_module
        class FakeReq:
            class _App:
                class _State:
                    repo = None
                state = _State()
            app = _App()
            cookies = {bff_module.access_cookie_name(): "cookie.jwt.token"}
        result = await get_token(request=FakeReq(), authorization=None, x_api_key=None)
        assert result == "cookie.jwt.token"


class TestGetCurrentUser:
    async def test_no_token_in_demo_returns_anonymous(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "true")
        result = await get_current_user(request=_fake_request(repo=object()), token=None)
        assert result == {
            "auth_user_id": None,
            "organization_id": None,
            "ruolo": None,
            "source": "anonymous",
        }

    async def test_no_token_without_demo_raises_401(self, monkeypatch):
        monkeypatch.delenv("DEMO_MODE", raising=False)
        monkeypatch.setattr("src.core.auth.dependencies.ENV_LOADED", True)
        with pytest.raises(HTTPException) as exc:
            await get_current_user(request=_fake_request(repo=None), token=None)
        assert exc.value.status_code == 401

    async def test_valid_service_key_is_not_user_auth(self, monkeypatch):
        monkeypatch.setenv("API_KEY_SERVICE", "sk-test-key")
        with pytest.raises(HTTPException) as exc:
            await get_current_user(request=_fake_request(), token="apikey:sk-test-key")
        assert exc.value.status_code == 403


class TestGetOptionalOrganizationContext:
    async def test_no_credentials_is_anonymous_only_when_demo_is_enabled(self, monkeypatch):
        monkeypatch.setattr("src.core.auth.dependencies.is_demo_mode", lambda: True)
        user = await get_optional_organization_context(_fake_request())
        assert user["source"] == "anonymous"

    async def test_no_credentials_in_production_context_raises_401(self, monkeypatch):
        monkeypatch.setattr("src.core.auth.dependencies.is_demo_mode", lambda: False)
        with pytest.raises(HTTPException) as exc:
            await get_optional_organization_context(_fake_request())
        assert exc.value.status_code == 401

    async def test_refresh_cookie_without_access_cookie_is_not_anonymous(self, monkeypatch):
        import src.core.auth.bff as bff
        monkeypatch.setattr("src.core.auth.dependencies.is_demo_mode", lambda: True)
        req = _fake_request(cookies={bff.refresh_cookie_name(): "refresh-token"})
        with pytest.raises(HTTPException) as exc:
            await get_optional_organization_context(req)
        assert exc.value.status_code == 401

    async def test_invalid_bearer_propagates_authentication_error(self, monkeypatch):
        from unittest.mock import patch
        req = _fake_request()
        req.headers["Authorization"] = "Bearer invalid"
        monkeypatch.setattr("src.core.auth.dependencies.get_token", AsyncMock(return_value="invalid"))
        with patch("src.core.auth.dependencies.get_current_user", new=AsyncMock(
            side_effect=HTTPException(status_code=403, detail="invalid token")
        )):
            with pytest.raises(HTTPException) as exc:
                await get_optional_organization_context(req)
        assert exc.value.status_code == 403

    async def test_invalid_api_key_raises_403(self, monkeypatch):
        monkeypatch.setenv("API_KEY_SERVICE", "sk-real-key")
        with pytest.raises(HTTPException) as exc:
            await get_current_user(request=_fake_request(), token="apikey:sk-wrong-key")
        assert exc.value.status_code == 403


def _fake_request(repo=None, cookies=None):
    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        url = SimpleNamespace(path='/api/me')
        app = type("App", (), {"state": type("State", (), {"repo": None})()})()
        client = FakeClient()
        headers = {}
    
    req = FakeRequest()
    req.app.state.repo = repo
    req.cookies = cookies or {}
    return req


class TestGetOrganizationContext:
    async def test_api_key_cannot_select_arbitrary_organization(self):
        user = {"source": "api_key", "ruolo": "service_role"}
        with pytest.raises(HTTPException) as exc:
            await get_organization_context(
                request=_fake_request(), current_user=user, x_organization_id="org-123",
            )
        assert exc.value.status_code == 401

    async def test_no_memberships_raises_403(self):
        mock_repo = AsyncMock()
        mock_repo.get_memberships_by_auth.return_value = []
        user = {"source": "jwt", "auth_user_id": str(uuid.uuid4()), "ruolo": None}
        with pytest.raises(HTTPException) as exc:
            await get_organization_context(
                request=_fake_request(repo=mock_repo),
                current_user=user,
                x_organization_id=None,
            )
        assert exc.value.status_code == 403

    async def test_single_membership_resolves_without_header(self):
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        auth_user_id = str(uuid.uuid4())
        mock_repo = AsyncMock()
        mock_repo.get_memberships_by_auth.return_value = [
            {"ruolo": "manager", "organization_id": org_id, "user_id": user_id}
        ]
        user = {"source": "jwt", "auth_user_id": auth_user_id, "ruolo": None}
        result = await get_organization_context(
            request=_fake_request(repo=mock_repo),
            current_user=user,
            x_organization_id=None,
        )
        assert result["ruolo"] == "manager"
        assert result["organization_id"] == org_id
        assert result["user_id"] == user_id
        mock_repo.get_memberships_by_auth.assert_awaited_once_with(auth_user_id)

    async def test_multi_membership_selects_validated_org(self):
        org_a = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        mock_repo = AsyncMock()
        mock_repo.get_memberships_by_auth.return_value = [
            {"ruolo": "owner", "organization_id": org_a, "user_id": str(uuid.uuid4())},
            {"ruolo": "staff", "organization_id": org_b, "user_id": str(uuid.uuid4())},
        ]
        user = {"source": "jwt", "auth_user_id": str(uuid.uuid4()), "ruolo": None}
        result = await get_organization_context(
            request=_fake_request(repo=mock_repo),
            current_user=user,
            x_organization_id=org_b,
        )
        assert result["organization_id"] == org_b
        assert result["ruolo"] == "staff"

    async def test_multi_membership_without_header_raises_403(self):
        mock_repo = AsyncMock()
        mock_repo.get_memberships_by_auth.return_value = [
            {"ruolo": "owner", "organization_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())},
            {"ruolo": "staff", "organization_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())},
        ]
        user = {"source": "jwt", "auth_user_id": str(uuid.uuid4()), "ruolo": None}
        with pytest.raises(HTTPException) as exc:
            await get_organization_context(
                request=_fake_request(repo=mock_repo),
                current_user=user,
                x_organization_id=None,
            )
        assert exc.value.status_code == 403

    async def test_multi_membership_org_not_member_raises_403(self):
        mock_repo = AsyncMock()
        mock_repo.get_memberships_by_auth.return_value = [
            {"ruolo": "owner", "organization_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())},
            {"ruolo": "staff", "organization_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())},
        ]
        user = {"source": "jwt", "auth_user_id": str(uuid.uuid4()), "ruolo": None}
        with pytest.raises(HTTPException) as exc:
            await get_organization_context(
                request=_fake_request(repo=mock_repo),
                current_user=user,
                x_organization_id=str(uuid.uuid4()),
            )
        assert exc.value.status_code == 403


class TestGetRepo:
    def test_missing_repo_raises_500(self):
        class FakeRequest:
            app = type("App", (), {"state": type("State", (), {"repo": None})()})()
        with pytest.raises(HTTPException) as exc:
            get_repo(FakeRequest())
        assert exc.value.status_code == 500


class TestRequireRuolo:
    async def test_owner_allowed_when_admin(self):
        async def dummy_dep():
            return {"source": "jwt", "ruolo": "owner", "organization_id": str(uuid.uuid4())}
        check = require_ruolo("owner", "manager")
        result = await check(user=await dummy_dep())
        assert result["ruolo"] == "owner"

    async def test_staff_blocked_from_admin(self):
        async def dummy_dep():
            return {"source": "jwt", "ruolo": "staff", "organization_id": str(uuid.uuid4())}
        check = require_ruolo("owner", "manager")
        with pytest.raises(HTTPException) as exc:
            await check(user=await dummy_dep())
        assert exc.value.status_code == 403

    async def test_service_role_never_bypasses_check(self):
        async def dummy_dep():
            return {"ruolo": None, "source": "api_key"}
        check = require_ruolo("owner")
        with pytest.raises(HTTPException) as exc:
            await check(user=await dummy_dep())
        assert exc.value.status_code == 401
