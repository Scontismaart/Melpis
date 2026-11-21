"""API export proof with PostgreSQL snapshots and a real one-time Valkey token."""
import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from src.core.auth.dependencies import get_current_user, get_organization_context
from src.core.gdpr import token_store
from src.core.gdpr.routes import router
from tests.core.gdpr.test_exports_a06 import seed_export_fixture
from tests.core.gdpr.test_token_store_valkey import valkey  # noqa: F401
from tests.core.test_quota_reservation_lifecycle import _auth_member


@pytest.mark.asyncio
async def test_contact_export_consumes_one_valkey_token_after_tenant_check(
    repo, sample_org, other_org, valkey, caplog,
):
    org_id = sample_org["id"]
    auth_id = await _auth_member(repo.pool, org_id, role="owner")
    membership = (await repo.get_memberships_by_auth(str(auth_id)))[0]
    seeded = await seed_export_fixture(repo.pool, org_id)
    await seed_export_fixture(repo.pool, other_org["id"])

    async def verified_session():
        return {
            "source": "jwt",
            "aal": "aal2",
            "organization_id": str(org_id),
            "auth_user_id": str(auth_id),
            "user_id": str(membership["user_id"]),
            "session_id": str(uuid4_session),
            "ruolo": "owner",
        }

    uuid4_session = uuid4()
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    app.dependency_overrides[get_current_user] = verified_session
    app.dependency_overrides[get_organization_context] = verified_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test.invalid") as client:
        response = await client.post(
            "/api/gdpr/export",
            json={
                "request_id": str(uuid4()),
                "scope": "contact",
                "subject_id": str(seeded["contact_a"]),
            },
        )
        assert response.status_code == 200
        token = response.cookies[token_store.DOWNLOAD_COOKIE]
        url = response.json()["download_url"]
        assert url == "/api/gdpr/download"
        cookie = {"Cookie": f"{token_store.DOWNLOAD_COOKIE}={token}"}

        denied = await client.get(url, headers={**cookie, "X-Organization-Id": str(other_org["id"])})
        assert denied.status_code == 403
        assert await token_store.read_token(token) is not None

        headers = {**cookie, "X-Organization-Id": str(org_id)}
        attempts = await asyncio.gather(client.get(url, headers=headers), client.get(url, headers=headers))
        assert sorted(item.status_code for item in attempts) == [200, 404]
        delivered = next(item for item in attempts if item.status_code == 200)
        assert "+10000000001" in delivered.text
        assert "+10000000002" not in delivered.text
        assert seeded["body_a"] not in delivered.text
        assert seeded["body_b"] not in delivered.text
        assert delivered.json()["manifest"]["scope"] == "contact"
        assert token not in caplog.text
        assert await token_store.read_token(token) is None
