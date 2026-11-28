import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.auth.dependencies import get_organization_context


class MockTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockConnection:
    def __init__(self, fetchrow_data=None, fetch_data=None, fetchval_data=None):
        self.fetchrow_data = fetchrow_data or {}
        self.fetch_data = fetch_data or []
        self.fetchval_data = fetchval_data
        self.executed = []

    def transaction(self):
        return MockTransaction()

    async def fetchrow(self, query, *args):
        for q_sub, result in self.fetchrow_data.items():
            if q_sub in query:
                if callable(result):
                    return await result(*args) if hasattr(result, "__await__") else result(*args)
                return result
        return None

    async def fetch(self, query, *args):
        return self.fetch_data

    async def fetchval(self, query, *args):
        return self.fetchval_data

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "EXECUTE 1"


class MockPool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        class _AcquireContext:
            def __init__(self, conn):
                self.conn = conn
            async def __aenter__(self):
                return self.conn
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
        return _AcquireContext(self._conn)


@pytest.fixture
def client():
    return TestClient(app)


def test_get_team_members_unauthorized(client):
    resp = client.get("/api/team/members")
    assert resp.status_code == 401


@pytest.mark.parametrize("status,plan,limit,can_add", [
    ("trialing", None, 3, True),
    ("active", "business", None, True),
    ("active", "unknown", None, False),
    (None, "pro", None, False),
])
def test_get_team_members_preserves_access_and_requires_verified_seat_entitlement(client, status, plan, limit, can_add):
    test_org_id = str(uuid.uuid4())
    test_user_id = str(uuid.uuid4())

    mock_conn = MockConnection(
        fetchrow_data={
            "SELECT plan, users_limit, subscription_status, trial_end FROM organizations": {
                "plan": plan,
                "users_limit": None,
                "subscription_status": status,
                "trial_end": datetime.now(timezone.utc) + timedelta(days=1) if status == "trialing" else None,
            }
        },
        fetch_data=[
            {
                "id": uuid.uuid4(),
                "user_id": uuid.UUID(test_user_id),
                "nome": "Owner Test",
                "email": "owner@azienda.it",
                "ruolo": "owner",
                "joined_at": None,
            }
        ],
    )
    app.state.pool = MockPool(mock_conn)

    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": test_org_id,
        "ruolo": "owner",
        "source": "jwt",
        "user_id": test_user_id,
    }

    try:
        resp = client.get("/api/team/members")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["users_limit"] == limit
        assert data["can_add_more"] is can_add
        assert len(data["members"]) == 1
        assert data["members"][0]["email"] == "owner@azienda.it"
    finally:
        app.dependency_overrides.clear()


def test_post_team_member_invalid_email(client):
    test_org_id = str(uuid.uuid4())
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": test_org_id,
        "ruolo": "owner",
        "source": "jwt",
    }
    try:
        resp = client.post("/api/team/members", json={"email": "invalid-email", "ruolo": "staff"})
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_post_team_member_manager_cannot_invite_manager(client):
    test_org_id = str(uuid.uuid4())
    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": test_org_id,
        "ruolo": "manager",
        "source": "jwt",
    }
    try:
        resp = client.post("/api/team/members", json={"email": "collega@azienda.it", "ruolo": "manager"})
        assert resp.status_code == 403
        assert "ruolo 'staff'" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_post_team_member_creates_invitation_without_direct_membership(client):
    test_org_id = str(uuid.uuid4())
    mock_conn = MockConnection()
    app.state.pool = MockPool(mock_conn)

    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": test_org_id,
        "ruolo": "owner",
        "source": "jwt",
    }
    try:
        with patch("src.api.routes.team.create_invitation", new_callable=AsyncMock) as create:
            create.return_value = {"detail": "Invito inviato"}
            resp = client.post("/api/team/members", json={"email": "collega@azienda.it", "ruolo": "staff"})
        assert resp.status_code == 200
        assert resp.json() == {"detail": "Invito inviato"}
        create.assert_awaited_once()
        assert create.await_args.kwargs["email"] == "collega@azienda.it"
        assert not mock_conn.executed
    finally:
        app.dependency_overrides.clear()


def test_delete_team_member_owner_protected(client):
    test_org_id = str(uuid.uuid4())
    owner_user_id = str(uuid.uuid4())

    mock_conn = MockConnection(
        fetchrow_data={
            "SELECT om.id, om.ruolo, up.email": {
                "id": uuid.uuid4(),
                "ruolo": "owner",
                "email": "owner@azienda.it",
            }
        }
    )
    app.state.pool = MockPool(mock_conn)

    app.dependency_overrides[get_organization_context] = lambda: {
        "organization_id": test_org_id,
        "ruolo": "owner",
        "source": "jwt",
        "user_id": str(uuid.uuid4()),
    }
    try:
        resp = client.delete(f"/api/team/members/{owner_user_id}")
        assert resp.status_code == 400
        assert "proprietario" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()
