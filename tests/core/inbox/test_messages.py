import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.usefixtures("reset_db")

API_KEY = "test-inbox-messages-api-key"


@pytest.fixture(autouse=True)
def set_env():
    os.environ["API_KEY_SERVICE"] = API_KEY


@pytest.fixture
async def async_client(repo, pg_pool):
    from src.api.main import app

    app.state.repo = repo
    app.state.pool = pg_pool

    yield repo, pg_pool, app
    app.dependency_overrides.clear()


async def _make_client(app, org_id, user_id, ruolo="staff"):
    from src.core.auth.dependencies import get_organization_context

    async def fake_get_organization_context():
        return {
            "auth_user_id": str(uuid.uuid4()),
            "organization_id": str(org_id),
            "ruolo": ruolo,
            "source": "jwt",
            "user_id": str(user_id),
        }

    app.dependency_overrides[get_organization_context] = fake_get_organization_context
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestConversationMessages:
    async def _create_org_with_conv(self, pg_pool):
        async with pg_pool.acquire() as conn:
            org = await conn.fetchrow(
                "INSERT INTO organizations (id, name) VALUES ($1, 'Messages Test') RETURNING id",
                uuid.uuid4()
            )
            auth_user = await conn.fetchrow(
                "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
                "staff-messages@test.com"
            )
            profile = await conn.fetchrow(
                "SELECT * FROM user_profiles WHERE auth_user_id = $1", auth_user["id"]
            )
            await conn.execute(
                "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'staff')",
                org["id"], profile["id"]
            )
            contact = await conn.fetchrow(
                "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, '+393997654321') RETURNING id",
                uuid.uuid4(), org["id"]
            )
            conv = await conn.fetchrow(
                "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3) RETURNING id",
                uuid.uuid4(), org["id"], contact["id"]
            )
        return org, profile, conv

    async def _insert_message(self, pg_pool, org, conv, *, direction, text,
                              status, created_at, deleted=False):
        # handling_type obbligatorio per gli inbound 'handled': il trigger
        # event_log calcola gestito_da_ai = (handling_type = 'ai_handled'),
        # che con NULL restituisce NULL e viola il NOT NULL.
        handling_type = "ai_handled" if direction == "inbound" else None
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO messages
                       (id, organization_id, conversation_id, direction, message_type,
                        content, content_text, status, handling_type, created_at, deleted_at)
                   VALUES ($1, $2, $3, $4, 'text', $5::jsonb, $6, $7, $8, $9, $10)
                   RETURNING id""",
                uuid.uuid4(), org["id"], conv["id"], direction,
                '{"from": "+393997654321"}', text, status, handling_type, created_at,
                created_at if deleted else None,
            )
            return row["id"]

    async def test_messages_empty(self, async_client):
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_conv(pg_pool)

        async with await _make_client(app, org["id"], profile["id"]) as client:
            response = await client.get(f"/api/inbox/tickets/{conv['id']}/messages")
            assert response.status_code == 200
            assert response.json() == {"messages": [], "total": 0}

    async def test_messages_chronological_order(self, async_client):
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_conv(pg_pool)

        from datetime import datetime, timezone, timedelta
        base = datetime.now(timezone.utc) - timedelta(days=1)
        await self._insert_message(pg_pool, org, conv, direction="outbound",
                                   text="Buongiorno! Come possiamo aiutarti?",
                                   status="sent", created_at=base)
        await self._insert_message(pg_pool, org, conv, direction="inbound",
                                   text="Avete un tavolo per due stasera?",
                                   status="handled", created_at=base + timedelta(minutes=1))
        await self._insert_message(pg_pool, org, conv, direction="outbound",
                                   text="Certo, alle 20:00 va bene?",
                                   status="read", created_at=base + timedelta(minutes=2))

        async with await _make_client(app, org["id"], profile["id"]) as client:
            response = await client.get(f"/api/inbox/tickets/{conv['id']}/messages")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 3
            texts = [m["content_text"] for m in data["messages"]]
            assert texts == [
                "Buongiorno! Come possiamo aiutarti?",
                "Avete un tavolo per due stasera?",
                "Certo, alle 20:00 va bene?",
            ]
            directions = [m["direction"] for m in data["messages"]]
            assert directions == ["outbound", "inbound", "outbound"]
            first = data["messages"][0]
            assert set(first.keys()) >= {
                "id", "direction", "message_type", "content_text", "status",
                "handling_type", "created_at",
            }

    async def test_messages_cross_org_404(self, async_client):
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_conv(pg_pool)

        other_org = await pg_pool.fetchrow(
            "INSERT INTO organizations (id, name) VALUES ($1, 'Other Org') RETURNING id",
            uuid.uuid4()
        )

        async with await _make_client(app, other_org["id"], profile["id"]) as client:
            response = await client.get(f"/api/inbox/tickets/{conv['id']}/messages")
            assert response.status_code == 404

    async def test_messages_pagination(self, async_client):
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_conv(pg_pool)

        from datetime import datetime, timezone, timedelta
        base = datetime(2026, 8, 16, 11, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            await self._insert_message(
                pg_pool, org, conv, direction="inbound", text=f"msg-{i}",
                status="handled", created_at=base + timedelta(minutes=i),
            )

        async with await _make_client(app, org["id"], profile["id"]) as client:
            first_page = await client.get(
                f"/api/inbox/tickets/{conv['id']}/messages",
                params={"limit": 2, "offset": 0},
            )
            assert first_page.status_code == 200
            data = first_page.json()
            assert data["total"] == 3
            assert [m["content_text"] for m in data["messages"]] == ["msg-0", "msg-1"]

            second_page = await client.get(
                f"/api/inbox/tickets/{conv['id']}/messages",
                params={"limit": 2, "offset": 2},
            )
            assert second_page.status_code == 200
            data2 = second_page.json()
            assert data2["total"] == 3
            assert [m["content_text"] for m in data2["messages"]] == ["msg-2"]

    async def test_messages_exclude_soft_deleted(self, async_client):
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_conv(pg_pool)

        from datetime import datetime, timezone, timedelta
        base = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)
        await self._insert_message(pg_pool, org, conv, direction="inbound",
                                   text="visible", status="handled",
                                   created_at=base, deleted=False)
        await self._insert_message(pg_pool, org, conv, direction="inbound",
                                   text="cancellato-gdpr", status="handled",
                                   created_at=base + timedelta(minutes=1), deleted=True)

        async with await _make_client(app, org["id"], profile["id"]) as client:
            response = await client.get(f"/api/inbox/tickets/{conv['id']}/messages")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 1
            assert [m["content_text"] for m in data["messages"]] == ["visible"]
