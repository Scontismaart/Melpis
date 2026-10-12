import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock

pytestmark = pytest.mark.usefixtures("reset_db")


API_KEY = "test-inbox-api-key"


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


class TestInboxAPI:
    async def _create_org_with_staff(self, pg_pool):
        async with pg_pool.acquire() as conn:
            org = await conn.fetchrow(
                "INSERT INTO organizations (id, name) VALUES ($1, 'Inbox Test') RETURNING id",
                uuid.uuid4()
            )
            auth_user = await conn.fetchrow(
                "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
                "staff@test.com"
            )
            profile = await conn.fetchrow(
                "SELECT * FROM user_profiles WHERE auth_user_id = $1", auth_user["id"]
            )
            await conn.execute(
                "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'staff')",
                org["id"], profile["id"]
            )
            contact = await conn.fetchrow(
                "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, '+393991234567') RETURNING id",
                uuid.uuid4(), org["id"]
            )
            conv = await conn.fetchrow(
                "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3) RETURNING id",
                uuid.uuid4(), org["id"], contact["id"]
            )
        return org, profile, conv

    async def test_list_tickets_empty(self, async_client):
        repo, pg_pool, app = async_client
        org = await pg_pool.fetchrow(
            "INSERT INTO organizations (id, name) VALUES ($1, 'Empty Org') RETURNING id",
            uuid.uuid4()
        )
        async with await _make_client(app, org["id"], uuid.uuid4()) as client:
            response = await client.get("/api/inbox/tickets")
            assert response.status_code == 200
            assert response.json() == {"tickets": [], "has_more": False}

    async def test_list_tickets_with_pending(self, async_client):
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_staff(pg_pool)
        # Create another staff user for auth
        auth_user2 = await pg_pool.fetchrow(
            "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
            "staff2@test.com"
        )
        profile2 = await pg_pool.fetchrow(
            "SELECT * FROM user_profiles WHERE auth_user_id = $1", auth_user2["id"]
        )
        await pg_pool.execute(
            "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'staff')",
            org["id"], profile2["id"]
        )

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))

        async with await _make_client(app, org["id"], profile2["id"]) as client:
            response = await client.get("/api/inbox/tickets")
            assert response.status_code == 200
            data = response.json()
            assert len(data["tickets"]) == 1
            assert data["tickets"][0]["id"] == str(conv["id"])
            assert data["tickets"][0]["ticket_status"] == "PENDING_STAFF"

    async def test_claim_ticket(self, async_client):
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_staff(pg_pool)

        # Create staff user for claiming
        auth_staff = await pg_pool.fetchrow(
            "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
            "claimer@test.com"
        )
        staff_profile = await pg_pool.fetchrow(
            "SELECT * FROM user_profiles WHERE auth_user_id = $1", auth_staff["id"]
        )
        await pg_pool.execute(
            "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'staff')",
            org["id"], staff_profile["id"]
        )

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))

        async with await _make_client(app, org["id"], staff_profile["id"]) as client:
            response = await client.post(
                f"/api/inbox/claim/{conv['id']}",
                json={"expected_version": 2}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["ticket_status"] == "CLAIMED"
            assert data["assigned_to"] == str(staff_profile["id"])

    async def test_claim_ticket_conflict(self, async_client):
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_staff(pg_pool)

        auth_staff = await pg_pool.fetchrow(
            "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
            "conflicter@test.com"
        )
        staff_profile = await pg_pool.fetchrow(
            "SELECT * FROM user_profiles WHERE auth_user_id = $1", auth_staff["id"]
        )
        await pg_pool.execute(
            "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'staff')",
            org["id"], staff_profile["id"]
        )

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))

        async with await _make_client(app, org["id"], staff_profile["id"]) as client:
            response = await client.post(
                f"/api/inbox/claim/{conv['id']}",
                json={"expected_version": 1}  # wrong version
            )
            assert response.status_code == 409

    async def test_get_ticket_not_found(self, async_client):
        repo, pg_pool, app = async_client
        org = await pg_pool.fetchrow(
            "INSERT INTO organizations (id, name) VALUES ($1, 'Test') RETURNING id",
            uuid.uuid4()
        )
        async with await _make_client(app, org["id"], uuid.uuid4()) as client:
            response = await client.get(f"/api/inbox/tickets/{uuid.uuid4()}")
            assert response.status_code == 404

    async def test_other_organization_ticket_cannot_be_read_or_claimed(self, async_client, pg_pool):
        _repo, _pool, app = async_client
        org_a, profile_a, _conv_a = await self._create_org_with_staff(pg_pool)
        org_b, _profile_b, conv_b = await self._create_org_with_staff(pg_pool)

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        escalated = await wrepo.escalate_to_human(str(conv_b["id"]), str(org_b["id"]))
        assert escalated is not None
        before = await wrepo.get_conversation(str(conv_b["id"]), str(org_b["id"]))
        assert before["ticket_status"] == "PENDING_STAFF"

        async with await _make_client(app, org_a["id"], profile_a["id"]) as client:
            read = await client.get(f"/api/inbox/tickets/{conv_b['id']}")
            mutation = await client.post(
                f"/api/inbox/claim/{conv_b['id']}",
                json={"expected_version": before["version"]},
            )

        assert read.status_code == 404
        assert mutation.status_code == 404
        after = await wrepo.get_conversation(str(conv_b["id"]), str(org_b["id"]))
        assert after["ticket_status"] == before["ticket_status"]
        assert after["assigned_to"] is None
        assert after["version"] == before["version"]

    async def test_release_ticket(self, async_client):
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_staff(pg_pool)

        auth_staff = await pg_pool.fetchrow(
            "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
            "releaser@test.com"
        )
        staff_profile = await pg_pool.fetchrow(
            "SELECT * FROM user_profiles WHERE auth_user_id = $1", auth_staff["id"]
        )
        await pg_pool.execute(
            "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'staff')",
            org["id"], staff_profile["id"]
        )

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))
        await wrepo.claim_ticket(str(conv["id"]), str(staff_profile["id"]), expected_version=2, organization_id=str(org["id"]))

        async with await _make_client(app, org["id"], staff_profile["id"]) as client:
            response = await client.post(f"/api/inbox/release/{conv['id']}")
            assert response.status_code == 200
            data = response.json()
            assert data["ticket_status"] == "PENDING_STAFF"

    async def test_resolve_ticket(self, async_client):
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_staff(pg_pool)

        auth_staff = await pg_pool.fetchrow(
            "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
            "resolver@test.com"
        )
        staff_profile = await pg_pool.fetchrow(
            "SELECT * FROM user_profiles WHERE auth_user_id = $1", auth_staff["id"]
        )
        await pg_pool.execute(
            "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'staff')",
            org["id"], staff_profile["id"]
        )

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))
        await wrepo.claim_ticket(str(conv["id"]), str(staff_profile["id"]), expected_version=2, organization_id=str(org["id"]))

        async with await _make_client(app, org["id"], staff_profile["id"]) as client:
            response = await client.post(f"/api/inbox/resolve/{conv['id']}")
            assert response.status_code == 200
            data = response.json()
            assert data["ticket_status"] == "RESOLVED"

    async def test_inbox_router_registered(self, async_client):
        repo, pg_pool, app = async_client

        def all_paths(routes):
            for r in routes:
                p = getattr(r, "path", None)
                if p:
                    yield p
                original = getattr(r, "original_router", None)
                if original is not None:
                    yield from all_paths(getattr(original, "routes", []))
                nested = getattr(r, "routes", None)
                if nested:
                    yield from all_paths(nested)

        paths = list(all_paths(app.routes))
        assert any("/api/inbox/tickets" in p for p in paths)

    async def test_reply_requires_claim_by_you(self, async_client):
        """Task 6: non si puo' rispondere a un ticket non CLAIMED da te."""
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_staff(pg_pool)

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))  # PENDING_STAFF, non CLAIMED

        async with await _make_client(app, org["id"], profile["id"]) as client:
            response = await client.post(
                f"/api/inbox/reply/{conv['id']}",
                json={"content": "Certo, confermo!", "idempotency_key": "test-key-1"},
            )
            assert response.status_code == 409

    async def test_reply_success_sends_via_whatsapp(self, async_client):
        """Task 6: risposta manuale inoltrata a Meta Cloud API (mockata,
        niente rete reale) su ticket CLAIMED dall'operatore che risponde."""
        from unittest.mock import AsyncMock, patch, MagicMock
        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_org_with_staff(pg_pool)

        auth_staff = await pg_pool.fetchrow(
            "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
            "replier@test.com"
        )
        staff_profile = await pg_pool.fetchrow(
            "SELECT * FROM user_profiles WHERE auth_user_id = $1", auth_staff["id"]
        )
        await pg_pool.execute(
            "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'staff')",
            org["id"], staff_profile["id"]
        )

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))
        await wrepo.claim_ticket(str(conv["id"]), str(staff_profile["id"]), expected_version=2, organization_id=str(org["id"]))

        fake_tenant_config = MagicMock(phone_number_id="123", access_token="tok")
        fake_result = {"id": "msg-uuid-1", "status": "sent"}

        with patch("src.core.inbox.routes.load_tenant_config", AsyncMock(return_value=fake_tenant_config)), \
             patch.object(
                 __import__("src.whatsapp.service", fromlist=["WhatsAppService"]).WhatsAppService,
                 "send_whatsapp_message",
                 AsyncMock(return_value=fake_result),
             ) as mock_send:
            async with await _make_client(app, org["id"], staff_profile["id"]) as client:
                response = await client.post(
                    f"/api/inbox/reply/{conv['id']}",
                    json={"content": "Certo, confermo la prenotazione!", "idempotency_key": "test-key-ok"},
                )
                assert response.status_code == 200
                data = response.json()
                assert data["message_id"] == "msg-uuid-1"
                assert data["status"] == "sent"

        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["idempotency_key"] == "test-key-ok"
        assert call_kwargs["payload"]["text"]["body"] == "Certo, confermo la prenotazione!"


class TestRealJwtPropagatesUserId:
    """C.1 — il fix che mancava: `get_organization_context` oggi NON
    propaga `user_id`, quindi le route inbox (che leggono
    `user["user_id"]`) vanno in KeyError 500 in produzione con un JWT
    reale. I test storici lo mascherano iniettando `user_id` a mano nel
    dict mockato: qui invece passiamo per il path VERO —
    get_current_user (JWT) -> get_organization_context (membership DB) ->
    route che legge user["user_id"]."""

    async def _setup_org_and_jwt_user(self, pg_pool, monkeypatch):
        async with pg_pool.acquire() as conn:
            org = await conn.fetchrow(
                "INSERT INTO organizations (id, name) VALUES ($1, 'Jwt Org') RETURNING id",
                uuid.uuid4()
            )
            auth_user = await conn.fetchrow(
                "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
                "jwt-owner@test.com"
            )
            profile = await conn.fetchrow(
                "SELECT * FROM user_profiles WHERE auth_user_id = $1", auth_user["id"]
            )
            await conn.execute(
                "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'owner')",
                org["id"], profile["id"]
            )
            contact = await conn.fetchrow(
                "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, '+39333111222') RETURNING id",
                uuid.uuid4(), org["id"]
            )
            conv = await conn.fetchrow(
                "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3) RETURNING id",
                uuid.uuid4(), org["id"], contact["id"]
            )
        return org, auth_user, profile, conv

    async def test_claim_via_real_jwt_does_not_crash(
        self, async_client, monkeypatch
    ):
        """Regression guard per il bug P1: claim su un ticket PENDING_STAFF
        con un JWT Supabase reale. PRIMA del fix questa chiamata finiva in
        500 (KeyError 'user_id'); DOPO deve funzionare e restituire 200."""
        import src.core.auth.dependencies as deps

        monkeypatch.setenv("SUPABASE_URL", "https://myproj.supabase.co")
        monkeypatch.setenv("SUPABASE_JWT_AUD", "authenticated")
        monkeypatch.setattr(
            deps, "_get_supabase_jwks", AsyncMock(return_value=[{"kid": "k1"}])
        )

        repo, pg_pool, app = async_client
        org, auth_user, profile, conv = await self._setup_org_and_jwt_user(pg_pool, monkeypatch)

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))

        async def _fake_verify(token, *, allow_expired=False):
            return {"sub": str(auth_user["id"]), "aal": "aal2"}

        monkeypatch.setattr(deps, "verify_supabase_jwt", _fake_verify)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/inbox/claim/{conv['id']}",
                json={"expected_version": 2},
                headers={
                    "Authorization": "Bearer real.jwt.token",
                    "x-organization-id": str(org["id"]),
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["ticket_status"] == "CLAIMED"
            # user_id propagato: e' l'id del profilo collegato al JWT sub
            assert data["assigned_to"] == str(profile["id"])

    async def test_release_via_real_jwt(self, async_client, monkeypatch):
        """Rilascio col path JWT reale: il CLAIMED ticket viene rilasciato
        solo se user_id e' quello dell'assegnato (letto dalla membership)."""
        import src.core.auth.dependencies as deps

        monkeypatch.setenv("SUPABASE_URL", "https://myproj.supabase.co")
        monkeypatch.setenv("SUPABASE_JWT_AUD", "authenticated")
        monkeypatch.setattr(
            deps, "_get_supabase_jwks", AsyncMock(return_value=[{"kid": "k1"}])
        )

        repo, pg_pool, app = async_client
        org, auth_user, profile, conv = await self._setup_org_and_jwt_user(pg_pool, monkeypatch)

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))
        await wrepo.claim_ticket(str(conv["id"]), str(profile["id"]), expected_version=2, organization_id=str(org["id"]))

        async def _fake_verify(token, *, allow_expired=False):
            return {"sub": str(auth_user["id"]), "aal": "aal2"}

        monkeypatch.setattr(deps, "verify_supabase_jwt", _fake_verify)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/inbox/release/{conv['id']}",
                headers={
                    "Authorization": "Bearer real.jwt.token",
                    "x-organization-id": str(org["id"]),
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["ticket_status"] == "PENDING_STAFF"


class TestTeamAndAssign:
    async def _setup_org_owner_and_staff(self, pg_pool):
        async with pg_pool.acquire() as conn:
            org = await conn.fetchrow(
                "INSERT INTO organizations (id, name) VALUES ($1, 'Assign Org') RETURNING id",
                uuid.uuid4()
            )
            ids = {}
            for key, email, ruolo in [
                ("owner", "owner@test.com", "owner"),
                ("manager", "manager@test.com", "manager"),
                ("staff", "staff3@test.com", "staff"),
            ]:
                au = await conn.fetchrow(
                    "INSERT INTO auth.users (email) VALUES ($1) RETURNING id", email
                )
                prof = await conn.fetchrow(
                    "SELECT * FROM user_profiles WHERE auth_user_id = $1", au["id"]
                )
                await conn.execute(
                    "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, $3)",
                    org["id"], prof["id"], ruolo
                )
                ids[key] = prof
            contact = await conn.fetchrow(
                "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, '+39333999000') RETURNING id",
                uuid.uuid4(), org["id"]
            )
            conv = await conn.fetchrow(
                "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3) RETURNING id",
                uuid.uuid4(), org["id"], contact["id"]
            )
        return org, ids, conv

    async def test_list_team(self, async_client):
        repo, pg_pool, app = async_client
        org, ids, conv = await self._setup_org_owner_and_staff(pg_pool)
        async with await _make_client(app, org["id"], ids["owner"]["id"], ruolo="owner") as client:
            response = await client.get("/api/inbox/team")
        assert response.status_code == 200
        members = response.json()["members"]
        assert len(members) == 3
        assert {m["ruolo"] for m in members} == {"owner", "manager", "staff"}
        assert all(m["user_id"] for m in members)

    async def test_assign_by_owner(self, async_client):
        repo, pg_pool, app = async_client
        org, ids, conv = await self._setup_org_owner_and_staff(pg_pool)

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))

        async with await _make_client(app, org["id"], ids["owner"]["id"], ruolo="owner") as client:
            response = await client.post(
                f"/api/inbox/assign/{conv['id']}",
                json={"assigned_to": str(ids["staff"]["id"]), "expected_version": 2},
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ticket_status"] == "CLAIMED"
        assert data["assigned_to"] == str(ids["staff"]["id"])
        assert data["assigned_nome"] == ids["staff"]["nome"]

    async def test_assign_reassigns_claimed_ticket(self, async_client):
        repo, pg_pool, app = async_client
        org, ids, conv = await self._setup_org_owner_and_staff(pg_pool)

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))
        await wrepo.claim_ticket(str(conv["id"]), str(ids["staff"]["id"]), expected_version=2, organization_id=str(org["id"]))

        async with await _make_client(app, org["id"], ids["owner"]["id"], ruolo="owner") as client:
            response = await client.post(
                f"/api/inbox/assign/{conv['id']}",
                json={"assigned_to": str(ids["manager"]["id"]), "expected_version": 3},
            )
        assert response.status_code == 200, response.text
        assert response.json()["assigned_to"] == str(ids["manager"]["id"])

    async def test_assign_staff_forbidden(self, async_client):
        repo, pg_pool, app = async_client
        org, ids, conv = await self._setup_org_owner_and_staff(pg_pool)
        async with await _make_client(app, org["id"], ids["staff"]["id"], ruolo="staff") as client:
            response = await client.post(
                f"/api/inbox/assign/{conv['id']}",
                json={"assigned_to": str(ids["owner"]["id"]), "expected_version": 1},
            )
        assert response.status_code == 403

    async def test_assign_member_of_other_org_404(self, async_client):
        repo, pg_pool, app = async_client
        org, ids, conv = await self._setup_org_owner_and_staff(pg_pool)

        # un profilo di un'ALTRA org: non deve poter essere assegnato
        async with pg_pool.acquire() as conn:
            other = await conn.fetchrow(
                "INSERT INTO organizations (id, name) VALUES ($1, 'Other') RETURNING id",
                uuid.uuid4()
            )
            au = await conn.fetchrow(
                "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
                "outsider@test.com"
            )
            outside_profile = await conn.fetchrow(
                "SELECT * FROM user_profiles WHERE auth_user_id = $1", au["id"]
            )
            await conn.execute(
                "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'staff')",
                other["id"], outside_profile["id"]
            )

        async with await _make_client(app, org["id"], ids["owner"]["id"], ruolo="owner") as client:
            response = await client.post(
                f"/api/inbox/assign/{conv['id']}",
                json={"assigned_to": str(outside_profile["id"]), "expected_version": 1},
            )
        assert response.status_code == 404

    async def test_assign_cross_tenant_ticket_404(self, async_client):
        repo, pg_pool, app = async_client
        org, ids, conv = await self._setup_org_owner_and_staff(pg_pool)

        async with pg_pool.acquire() as conn:
            other = await conn.fetchrow(
                "INSERT INTO organizations (id, name) VALUES ($1, 'Other2') RETURNING id",
                uuid.uuid4()
            )
            other_contact = await conn.fetchrow(
                "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, '+39333123456') RETURNING id",
                uuid.uuid4(), other["id"]
            )
            other_conv = await conn.fetchrow(
                "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3) RETURNING id",
                uuid.uuid4(), other["id"], other_contact["id"]
            )

        async with await _make_client(app, org["id"], ids["owner"]["id"], ruolo="owner") as client:
            response = await client.post(
                f"/api/inbox/assign/{other_conv['id']}",
                json={"assigned_to": str(ids["staff"]["id"]), "expected_version": 1},
            )
        assert response.status_code == 404

    async def test_assign_version_conflict_409(self, async_client):
        repo, pg_pool, app = async_client
        org, ids, conv = await self._setup_org_owner_and_staff(pg_pool)

        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))

        async with await _make_client(app, org["id"], ids["owner"]["id"], ruolo="owner") as client:
            response = await client.post(
                f"/api/inbox/assign/{conv['id']}",
                json={"assigned_to": str(ids["staff"]["id"]), "expected_version": 999},
            )
        assert response.status_code == 409

async def _make_api_key_client(app, org_id):
    """Simula una credenziale di servizio sulle API utente: deve fallire chiusa."""
    from src.core.auth.dependencies import get_organization_context

    async def fake_get_organization_context():
        return {
            "auth_user_id": None,
            "organization_id": str(org_id),
            "ruolo": "service_role",
            "source": "api_key",
        }

    app.dependency_overrides[get_organization_context] = fake_get_organization_context
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestApiKeySenzaUserId:
    """Una service API key non e' mai una sessione utente dell'inbox."""

    async def test_claim_without_user_id_403(self, async_client):
        repo, pg_pool, app = async_client
        org = await pg_pool.fetchrow(
            "INSERT INTO organizations (id, name) VALUES ($1, 'ApiKey Org') RETURNING id",
            uuid.uuid4()
        )
        contact = await pg_pool.fetchrow(
            "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, '+393991119999') RETURNING id",
            uuid.uuid4(), org["id"]
        )
        conv = await pg_pool.fetchrow(
            "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3) RETURNING id",
            uuid.uuid4(), org["id"], contact["id"]
        )
        from src.whatsapp.repository import Repository as WRepo
        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))

        async with await _make_api_key_client(app, org["id"]) as client:
            response = await client.post(
                f"/api/inbox/claim/{conv['id']}",
                json={"expected_version": 2},
            )
        assert response.status_code == 401

    async def test_release_resolve_reply_without_user_id_403(self, async_client):
        repo, pg_pool, app = async_client
        org = await pg_pool.fetchrow(
            "INSERT INTO organizations (id, name) VALUES ($1, 'ApiKey Org 2') RETURNING id",
            uuid.uuid4()
        )
        contact = await pg_pool.fetchrow(
            "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, '+393991118888') RETURNING id",
            uuid.uuid4(), org["id"]
        )
        conv = await pg_pool.fetchrow(
            "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3) RETURNING id",
            uuid.uuid4(), org["id"], contact["id"]
        )

        async with await _make_api_key_client(app, org["id"]) as client:
            release = await client.post(f"/api/inbox/release/{conv['id']}")
            assert release.status_code == 401

            resolve = await client.post(f"/api/inbox/resolve/{conv['id']}")
            assert resolve.status_code == 401

            reply = await client.post(
                f"/api/inbox/reply/{conv['id']}",
                json={"content": "test", "idempotency_key": "api-key-no-user"},
            )
            assert reply.status_code == 401

    async def test_list_and_get_are_forbidden_for_api_key(self, async_client):
        """Anche le letture tenant-scoped richiedono membership utente."""
        repo, pg_pool, app = async_client
        org = await pg_pool.fetchrow(
            "INSERT INTO organizations (id, name) VALUES ($1, 'ApiKey Org 3') RETURNING id",
            uuid.uuid4()
        )
        contact = await pg_pool.fetchrow(
            "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, '+393991117777') RETURNING id",
            uuid.uuid4(), org["id"]
        )
        conv = await pg_pool.fetchrow(
            "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3) RETURNING id",
            uuid.uuid4(), org["id"], contact["id"]
        )

        async with await _make_api_key_client(app, org["id"]) as client:
            listing = await client.get("/api/inbox/tickets")
            assert listing.status_code == 401

            single = await client.get(f"/api/inbox/tickets/{conv['id']}")
            assert single.status_code == 401

class TestReplyDispatchPerCanale:
    """Punto 10: la reply manuale di un ticket Instagram esce su Instagram
    DM, non su WhatsApp."""

    async def _create_instagram_ticket(self, pg_pool):
        async with pg_pool.acquire() as conn:
            org = await conn.fetchrow(
                "INSERT INTO organizations (id, name) VALUES ($1, 'IG Reply Org') RETURNING id",
                uuid.uuid4()
            )
            auth_user = await conn.fetchrow(
                "INSERT INTO auth.users (email) VALUES ($1) RETURNING id",
                "ig-reply-staff@test.com"
            )
            profile = await conn.fetchrow(
                "SELECT * FROM user_profiles WHERE auth_user_id = $1", auth_user["id"]
            )
            await conn.execute(
                "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'staff')",
                org["id"], profile["id"]
            )
            # contatto Instagram: phone_number contiene l'IG user id
            contact = await conn.fetchrow(
                "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, 'ig-user-777') RETURNING id",
                uuid.uuid4(), org["id"]
            )
            conv = await conn.fetchrow(
                "INSERT INTO conversations (id, organization_id, contact_id, canale) "
                "VALUES ($1, $2, $3, 'instagram') RETURNING id",
                uuid.uuid4(), org["id"], contact["id"]
            )
        return org, profile, conv

    async def test_reply_on_instagram_ticket_uses_instagram_service(self, async_client):
        from unittest.mock import patch, MagicMock
        from src.whatsapp.repository import Repository as WRepo
        from src.instagram.config import InstagramTenantConfig

        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_instagram_ticket(pg_pool)

        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))
        await wrepo.claim_ticket(str(conv["id"]), str(profile["id"]), expected_version=2, organization_id=str(org["id"]))

        mock_send = AsyncMock(return_value={"id": "msg-ig-1", "status": "sent"})
        fake_ig_service = MagicMock()
        fake_ig_service.send_instagram_message = mock_send
        fake_config = InstagramTenantConfig(
            organization_id=org["id"], ig_user_id="17841400000000000", access_token="tok"
        )

        with patch("src.core.inbox.routes.load_tenant_config", AsyncMock()) as mock_wa_load, \
             patch("src.whatsapp.service.WhatsAppService.send_whatsapp_message", AsyncMock()) as mock_wa_send, \
             patch("src.instagram.config.load_instagram_config", AsyncMock(return_value=fake_config)), \
             patch("src.instagram.service.InstagramService", MagicMock(return_value=fake_ig_service)):
            async with await _make_client(app, org["id"], profile["id"]) as client:
                response = await client.post(
                    f"/api/inbox/reply/{conv['id']}",
                    json={"content": "Ti confermo il tavolo!", "idempotency_key": "ig-reply-key-1"},
                )

        assert response.status_code == 200, response.text
        assert response.json() == {"message_id": "msg-ig-1", "status": "sent"}

        mock_send.assert_awaited_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to_ig_id"] == "ig-user-777"
        assert kwargs["text"] == "Ti confermo il tavolo!"
        assert kwargs["idempotency_key"] == "ig-reply-key-1"
        # nessun invio WhatsApp e nessun tentativo di caricare il tenant WA
        mock_wa_send.assert_not_called()
        mock_wa_load.assert_not_called()

    async def test_reply_on_instagram_ticket_without_account_409(self, async_client):
        from unittest.mock import patch
        from src.whatsapp.repository import Repository as WRepo

        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_instagram_ticket(pg_pool)

        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))
        await wrepo.claim_ticket(str(conv["id"]), str(profile["id"]), expected_version=2, organization_id=str(org["id"]))

        with patch("src.instagram.config.load_instagram_config", AsyncMock(return_value=None)), \
             patch("src.instagram.service.InstagramService") as mock_ig_cls:
            async with await _make_client(app, org["id"], profile["id"]) as client:
                response = await client.post(
                    f"/api/inbox/reply/{conv['id']}",
                    json={"content": "test", "idempotency_key": "ig-reply-key-2"},
                )

        assert response.status_code == 409
        mock_ig_cls.assert_not_called()

    async def test_ticket_list_exposes_canale(self, async_client):
        from src.whatsapp.repository import Repository as WRepo

        repo, pg_pool, app = async_client
        org, profile, conv = await self._create_instagram_ticket(pg_pool)

        wrepo = WRepo(pool=pg_pool)
        await wrepo.escalate_to_human(str(conv["id"]), str(org["id"]))

        async with await _make_client(app, org["id"], profile["id"]) as client:
            response = await client.get("/api/inbox/tickets")
            assert response.status_code == 200
            tickets = response.json()["tickets"]
            assert len(tickets) == 1
            assert tickets[0]["canale"] == "instagram"
