import base64
import os
import uuid

import httpx
import pytest
import asyncpg

from src.core.rate_limit import RedisRateLimiter

pytestmark = pytest.mark.asyncio


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttl = {}

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        self.ttl[key] = seconds

    async def eval(self, script, numkeys, key, seconds):
        assert numkeys == 1
        count = await self.incr(key)
        if count == 1 or key not in self.ttl:
            await self.expire(key, seconds)
        return count


async def test_redis_rate_limiter_shared_across_instances():
    redis = FakeRedis()
    first = RedisRateLimiter(redis)
    second = RedisRateLimiter(redis)

    assert await first.hit("tenant:org", 2, 60) is False
    assert await second.hit("tenant:org", 2, 60) is False
    assert await first.hit("tenant:org", 2, 60) is True
    assert redis.ttl["rl:tenant:org"] == 60


async def test_cookie_mutation_requires_origin_and_csrf(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("CREWAI_STORAGE_DIR", str(tmp_path / "crewai"))
    async def fake_logout(access_token, *, scope="local"):
        return None
    monkeypatch.setattr("src.core.auth.bff.logout", fake_logout)
    from src.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://app.example.com") as client:
        no_origin = await client.post(
            "/api/auth/logout",
            cookies={"wa_at": "jwt", "wa_csrf": "csrf"},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert no_origin.status_code == 403

        bad_token = await client.post(
            "/api/auth/logout",
            cookies={"wa_at": "jwt", "wa_csrf": "csrf"},
            headers={"Origin": "https://app.example.com", "X-CSRF-Token": "wrong"},
        )
        assert bad_token.status_code == 403

        ok = await client.post(
            "/api/auth/logout",
            cookies={"wa_at": "jwt", "wa_csrf": "csrf"},
            headers={"Origin": "https://app.example.com", "X-CSRF-Token": "csrf"},
        )
        assert ok.status_code == 200


async def test_docs_protected_only_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DOCS_BASIC_AUTH_USER", "docs")
    monkeypatch.setenv("DOCS_BASIC_AUTH_PASSWORD", "secret")
    from src.core.security.docs import require_docs_access

    class Req:
        headers = {}
        client = type("Client", (), {"host": "203.0.113.10"})()

    with pytest.raises(Exception):
        require_docs_access(Req())

    token = base64.b64encode(b"docs:secret").decode()
    Req.headers = {"authorization": f"Basic {token}"}
    require_docs_access(Req())


async def test_api_key_rejected_from_public_ip(monkeypatch):
    monkeypatch.setenv("API_KEY_ALLOWED_CIDRS", "10.0.0.0/8")
    from src.core.auth.api_key_guard import api_key_request_allowed

    class Req:
        headers = {}
        client = type("Client", (), {"host": "198.51.100.10"})()

    assert api_key_request_allowed(Req()) is False


async def test_rls_blocks_cross_tenant_direct_query(pg_pool, sample_org, other_org):
    auth_user_id = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid AS $$
                SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
            $$ LANGUAGE sql STABLE;
        """)
        await conn.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
                    CREATE ROLE authenticated;
                END IF;
            END $$;
        """)
        await conn.execute("GRANT USAGE ON SCHEMA public, auth TO authenticated")
        await conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO authenticated")
        await conn.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, 'rls@test.com')",
            auth_user_id,
        )
        up = await conn.fetchrow("SELECT id FROM user_profiles WHERE auth_user_id = $1", auth_user_id)
        await conn.execute(
            "INSERT INTO organization_memberships (organization_id, user_id, ruolo) VALUES ($1, $2, 'owner')",
            sample_org["id"], up["id"],
        )
        await conn.execute(
            "INSERT INTO documents (id, organization_id, nome) VALUES ($1, $2, 'own.pdf')",
            uuid.uuid4(), sample_org["id"],
        )
        await conn.execute(
            "INSERT INTO documents (id, organization_id, nome) VALUES ($1, $2, 'other.pdf')",
            uuid.uuid4(), other_org["id"],
        )
        await conn.execute("SET ROLE authenticated")
        await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, false)", str(auth_user_id))
        rows = await conn.fetch("SELECT nome FROM documents ORDER BY nome")
        await conn.execute("RESET ROLE")

    assert [r["nome"] for r in rows] == ["own.pdf"]


async def test_outbound_dedup_rls_is_enabled_and_backend_path_survives(
    pg_pool, sample_org, other_org,
):
    from src.core.db.repositories.message_repo import MessageRepository

    message_a = uuid.uuid4()
    message_b = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO outbound_dedup (message_id, organization_id, response_text) "
            "VALUES ($1, $2, 'private-a'), ($3, $4, 'private-b')",
            message_a, sample_org["id"], message_b, other_org["id"],
        )
        rls_enabled = await conn.fetchval(
            "SELECT relrowsecurity FROM pg_class "
            "WHERE oid = 'public.outbound_dedup'::regclass"
        )
        assert rls_enabled is True

        # Grant the client privileges on the target table so the original
        # assertions exercise RLS, while first proving the erasure-barrier
        # ACL independently. This explicit transaction is always rolled back
        # below, so all test-only grants are removed even when assertions pass.
        outbound_privileges = {privilege: await conn.fetchval(
            "SELECT has_table_privilege('authenticated','public.outbound_dedup',$1)", privilege,
        ) for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")}
        barrier_select = await conn.fetchval(
            "SELECT has_table_privilege('authenticated','public.gdpr_erasure_barriers','SELECT')"
        )
        test_grants = conn.transaction()
        await test_grants.start()
        try:
            await conn.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON public.outbound_dedup "
                "TO authenticated"
            )
            await conn.execute("GRANT USAGE ON SCHEMA public TO authenticated")
            await conn.execute("SET ROLE authenticated")
            try:
                visible = await conn.fetch(
                    "SELECT message_id FROM outbound_dedup WHERE organization_id = $1",
                    other_org["id"],
                )
                assert visible == []
                assert await conn.execute(
                    "UPDATE outbound_dedup SET response_text = 'stolen' "
                    "WHERE message_id = $1",
                    message_b,
                ) == "UPDATE 0"
                assert await conn.execute(
                    "DELETE FROM outbound_dedup WHERE message_id = $1", message_b
                ) == "DELETE 0"

                with pytest.raises(asyncpg.InsufficientPrivilegeError, match="gdpr_erasure_barriers"):
                    async with conn.transaction():  # savepoint keeps outer transaction usable
                        await conn.execute(
                            "INSERT INTO outbound_dedup "
                            "(message_id, organization_id, response_text) "
                            "VALUES ($1, $2, 'client-write')",
                            uuid.uuid4(), sample_org["id"],
                        )

                await conn.execute("RESET ROLE")
                await conn.execute(
                    "GRANT SELECT ON public.gdpr_erasure_barriers TO authenticated"
                )
                rls_enabled = await conn.fetchval(
                    "SELECT relrowsecurity FROM pg_class "
                    "WHERE oid = 'public.gdpr_erasure_barriers'::regclass"
                )
                assert rls_enabled is True
                assert await conn.fetchval(
                    "SELECT count(*) FROM pg_policies WHERE tablename='gdpr_erasure_barriers'"
                ) == 0
                await conn.execute("SET ROLE authenticated")
                with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
                    async with conn.transaction():  # savepoint isolates expected RLS denial
                        await conn.execute(
                            "INSERT INTO outbound_dedup "
                            "(message_id, organization_id, response_text) "
                            "VALUES ($1, $2, 'client-write')",
                            uuid.uuid4(), sample_org["id"],
                        )
            finally:
                await conn.execute("RESET ROLE")
        finally:
            await conn.execute("RESET ROLE")
            await test_grants.rollback()
        assert await conn.fetchval(
            "SELECT has_table_privilege('authenticated','public.gdpr_erasure_barriers','SELECT')"
        ) is barrier_select
        for privilege, had_privilege in outbound_privileges.items():
            assert await conn.fetchval(
                "SELECT has_table_privilege('authenticated','public.outbound_dedup',$1)", privilege,
            ) is had_privilege

    backend = MessageRepository(pool=pg_pool)
    await backend.save_outbound_dedup(message_a, sample_org["id"], "backend-write")
    result = await backend.get_outbound_dedup(sample_org["id"], message_a)
    assert result == {"response_text": "backend-write"}
