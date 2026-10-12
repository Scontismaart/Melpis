"""Phase 8 auth access lifecycle integration coverage.

These tests use the real PostgreSQL repositories and auth router. JWTs are
signed locally with an RSA key; only JWKS retrieval and token denylist lookup
are replaced, so request parsing, signature validation, authorization, and DB
state transitions remain real.
"""

import glob
import asyncio
import time
import uuid

import httpx
import pytest
import json
import jwt
from fastapi import FastAPI
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from src.core.auth import dependencies
from src.core.auth.routes import router as auth_router
from src.core.db.repository import CoreRepository


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
def signing_key(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "phase8-local-rsa", "alg": "RS256", "use": "sig"})
    monkeypatch.setenv("SUPABASE_URL", "https://phase8.example.test")
    monkeypatch.setenv("SUPABASE_JWT_AUD", "authenticated")

    async def local_jwks():
        return [public_jwk]

    async def not_revoked(_token):
        return False

    monkeypatch.setattr(dependencies, "_get_supabase_jwks", local_jwks)
    monkeypatch.setattr(dependencies, "is_token_revoked", not_revoked)
    dependencies.JWKS_CACHE.update(keys=None, expires_at=0)
    return private_key


@pytest.fixture
async def auth_client(pg_pool):
    app = FastAPI()
    app.include_router(auth_router)
    app.state.repo = CoreRepository(pool=pg_pool)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://phase8.test") as client:
        yield client


def signed_access_token(private_key, auth_user_id):
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(auth_user_id),
            "aud": "authenticated",
            "iss": "https://phase8.example.test/auth/v1",
            "iat": now,
            "exp": now + 3600,
            "email": f"{auth_user_id}@phase8.invalid",
            "session_id": str(uuid.uuid4()),
            "aal": "aal2",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "phase8-local-rsa"},
    )


async def create_auth_user(pg_pool, *, email=None):
    auth_user_id = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, $2)",
            auth_user_id,
            email or f"{auth_user_id}@phase8.invalid",
        )
        row = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE auth_user_id = $1", auth_user_id
        )
    return auth_user_id, row["id"] if row else None


async def add_membership(pg_pool, organization_id, user_profile_id, role="owner"):
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO organization_memberships "
            "(organization_id, user_id, ruolo, joined_at) VALUES ($1, $2, $3, now())",
            organization_id,
            user_profile_id,
            role,
        )


async def request_me(client, token, organization_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if organization_id:
        headers["X-Organization-Id"] = str(organization_id)
    return await client.get("/api/auth/me", headers=headers)


async def lifecycle_row(pg_pool, auth_user_id):
    async with pg_pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT auth_user_id, provisioned_at, disabled_at "
            "FROM auth_access_lifecycle WHERE auth_user_id = $1",
            auth_user_id,
        )


@pytest.mark.parametrize("revocation", ["disabled", "membership_removed"])
@pytest.mark.parametrize("method,path", [
    ("POST", "/api/whatsapp/connect"),
    ("GET", "/api/calendar/auth"),
    ("GET", "/api/reviews/google/auth"),
])
async def test_phase7_integrations_deny_revoked_phase8_principal(
    pg_pool, repo, sample_org, signing_key, revocation, method, path,
):
    from src.whatsapp.routes import router as whatsapp_router
    from src.core.calendar.routes import router as calendar_router
    from src.core.reviews.google_routes import router as reviews_router

    auth_user_id, profile_id = await create_auth_user(pg_pool)
    await add_membership(pg_pool, sample_org["id"], profile_id)
    old_token = signed_access_token(signing_key, auth_user_id)
    if revocation == "disabled":
        await repo.disable_auth_access(str(auth_user_id))
    else:
        await pg_pool.execute("DELETE FROM organization_memberships WHERE user_id=$1", profile_id)
    app = FastAPI()
    for integration_router in (whatsapp_router, calendar_router, reviews_router):
        app.include_router(integration_router)
    app.state.repo = repo
    app.state.pool = pg_pool
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://phase78.test") as client:
        response = await client.request(method, path, headers={
            "Authorization": f"Bearer {old_token}", "X-Organization-Id": str(sample_org["id"]),
        }, json={"phone_number_id": "123", "waba_id": "456", "access_token": "synthetic-invalid"} if method == "POST" else None)
    assert response.status_code == 403
    assert "synthetic-invalid" not in response.text


async def test_new_user_is_provisioned_on_first_me_request(
    pg_pool, auth_client, signing_key
):
    auth_user_id, _ = await create_auth_user(pg_pool)
    token = signed_access_token(signing_key, auth_user_id)

    response = await request_me(auth_client, token)

    assert response.status_code == 200
    assert response.json()["ruolo"] == "owner"
    row = await lifecycle_row(pg_pool, auth_user_id)
    assert row["provisioned_at"] is not None
    assert row["disabled_at"] is None


async def test_existing_member_can_access_without_creating_another_org(
    pg_pool, auth_client, signing_key, sample_org
):
    auth_user_id, profile_id = await create_auth_user(pg_pool)
    await add_membership(pg_pool, sample_org["id"], profile_id)
    before = await pg_pool.fetchval("SELECT count(*) FROM organizations")

    response = await request_me(
        auth_client, signed_access_token(signing_key, auth_user_id)
    )

    assert response.status_code == 200
    assert response.json()["organization_id"] == str(sample_org["id"])
    assert await pg_pool.fetchval("SELECT count(*) FROM organizations") == before


async def test_removing_all_memberships_does_not_reprovision_existing_user(
    pg_pool, auth_client, signing_key, repo, sample_org
):
    auth_user_id, profile_id = await create_auth_user(pg_pool)
    token = signed_access_token(signing_key, auth_user_id)
    await add_membership(pg_pool, sample_org["id"], profile_id)
    first_response = await request_me(auth_client, token)
    assert first_response.status_code == 200
    await repo.get_auth_access_allowed(str(auth_user_id))
    before = await pg_pool.fetchval("SELECT count(*) FROM organizations")
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM organization_memberships WHERE user_id = $1", profile_id)

    response = await request_me(auth_client, token)

    assert response.status_code == 403
    assert await pg_pool.fetchval("SELECT count(*) FROM organizations") == before
    assert await repo.get_memberships_by_auth(str(auth_user_id)) == []
    assert (await lifecycle_row(pg_pool, auth_user_id))["provisioned_at"] is not None


@pytest.mark.parametrize("with_membership", [False, True], ids=["no-membership", "member"])
async def test_disabled_user_is_denied_with_or_without_membership(
    pg_pool, auth_client, signing_key, repo, sample_org, with_membership
):
    auth_user_id, profile_id = await create_auth_user(pg_pool)
    if with_membership:
        await add_membership(pg_pool, sample_org["id"], profile_id)
    before_orgs = await pg_pool.fetchval("SELECT count(*) FROM organizations")
    before_memberships = await pg_pool.fetchval("SELECT count(*) FROM organization_memberships")
    await repo.disable_auth_access(str(auth_user_id))

    response = await request_me(
        auth_client, signed_access_token(signing_key, auth_user_id)
    )

    assert response.status_code == 403
    assert (await lifecycle_row(pg_pool, auth_user_id))["disabled_at"] is not None
    assert await pg_pool.fetchval("SELECT count(*) FROM organizations") == before_orgs
    assert await pg_pool.fetchval("SELECT count(*) FROM organization_memberships") == before_memberships


async def test_membership_removal_preserves_access_to_other_organization(
    pg_pool, auth_client, signing_key, sample_org, other_org
):
    auth_user_id, profile_id = await create_auth_user(pg_pool)
    await add_membership(pg_pool, sample_org["id"], profile_id, "owner")
    await add_membership(pg_pool, other_org["id"], profile_id, "staff")
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM organization_memberships WHERE user_id = $1 AND organization_id = $2",
            profile_id,
            sample_org["id"],
        )

    response = await request_me(
        auth_client,
        signed_access_token(signing_key, auth_user_id),
        other_org["id"],
    )

    assert response.status_code == 200
    assert response.json()["organization_id"] == str(other_org["id"])
    assert response.json()["ruolo"] == "staff"


async def test_provisioning_wrappers_reject_disabled_and_previously_provisioned_users(
    pg_pool, repo, sample_org
):
    disabled_id, _ = await create_auth_user(pg_pool)
    await repo.disable_auth_access(str(disabled_id))
    with pytest.raises(PermissionError):
        await repo.get_or_create_organization_with_owner(str(disabled_id), "Nope")
    with pytest.raises(PermissionError):
        await repo.create_organization_with_owner(str(disabled_id), "Nope")

    removed_id, profile_id = await create_auth_user(pg_pool)
    await add_membership(pg_pool, sample_org["id"], profile_id)
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM organization_memberships WHERE user_id = $1", profile_id)
    with pytest.raises(PermissionError):
        await repo.get_or_create_organization_with_owner(str(removed_id), "Nope")


@pytest.mark.parametrize("disable_account", [True, False], ids=["disabled", "membership-revoked"])
async def test_oauth_callback_after_revocation_does_not_issue_session(
    pg_pool, repo, monkeypatch, sample_org, disable_account
):
    # OAuth exchange succeeds at the provider, but app lifecycle state remains
    # authoritative and the callback must not mint local session cookies.
    import src.core.auth.bff as bff_module
    from src.core.auth.routes import router as router

    auth_user_id, profile_id = await create_auth_user(pg_pool)
    if disable_account:
        await repo.disable_auth_access(str(auth_user_id))
    else:
        await add_membership(pg_pool, sample_org["id"], profile_id)
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM organization_memberships WHERE user_id = $1", profile_id
            )
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://phase8.test") as client:
        start = await client.get("/api/auth/google/start")

        async def provider_exchange(_code, _verifier):
            return {
                "access_token": "provider-access-token",
                "refresh_token": "provider-refresh-token",
                "expires_in": 3600,
                "token_type": "bearer",
                "user": {"id": str(auth_user_id), "email": f"{auth_user_id}@phase8.invalid"},
            }

        monkeypatch.setattr(bff_module, "exchange_pkce", provider_exchange)
        callback = await client.get(
            "/api/auth/google/callback",
            params={"code": "one-time-code", "state": str(uuid.uuid4())},
            headers={"Cookie": "; ".join(f"{k}={v}" for k, v in start.cookies.items())},
        )

    assert callback.status_code == 302
    assert "errore=google" in callback.headers["location"]
    assert not any(cookie.startswith("wa_at=") for cookie in callback.headers.get_list("set-cookie"))
    assert not any(cookie.startswith("wa_rt=") for cookie in callback.headers.get_list("set-cookie"))


async def test_disabled_user_login_does_not_issue_session_cookies(
    pg_pool, repo, signing_key, monkeypatch
):
    import src.core.auth.bff as bff_module

    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    auth_user_id, _ = await create_auth_user(pg_pool)
    await repo.disable_auth_access(str(auth_user_id))
    app = FastAPI()
    app.include_router(auth_router)
    app.state.repo = repo
    transport = httpx.ASGITransport(app=app)

    async def provider_login(_email, _password):
        return {
            "access_token": signed_access_token(signing_key, auth_user_id),
            "refresh_token": "fresh-provider-refresh-token",
            "user": {"id": str(auth_user_id)},
        }

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bff_module, "login", provider_login)
    monkeypatch.setattr("src.core.auth.routes._record_login_success", noop)
    async with httpx.AsyncClient(transport=transport, base_url="http://phase8.test") as client:
        response = await client.post(
            "/api/auth/login", json={"email": "disabled@phase8.invalid", "password": "synthetic"}
        )

    assert response.status_code == 403
    assert not any(cookie.startswith("wa_at=") for cookie in response.headers.get_list("set-cookie"))
    assert not any(cookie.startswith("wa_rt=") for cookie in response.headers.get_list("set-cookie"))


async def test_disabled_user_refresh_does_not_rotate_or_issue_session(
    pg_pool, repo, signing_key, monkeypatch
):
    import src.core.auth.bff as bff_module

    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    auth_user_id, _ = await create_auth_user(pg_pool)
    await repo.disable_auth_access(str(auth_user_id))
    app = FastAPI()
    app.include_router(auth_router)
    app.state.repo = repo
    transport = httpx.ASGITransport(app=app)
    rotate_calls = []
    revoke_calls = []

    async def provider_refresh(*args, **kwargs):
        rotate_calls.append((args, kwargs))
        return {"access_token": "rotated", "refresh_token": "rotated-refresh"}

    async def not_revoked(token):
        return False

    async def record_revoke(token):
        revoke_calls.append(token)

    monkeypatch.setattr(bff_module, "refresh", provider_refresh)
    monkeypatch.setattr("src.core.auth.routes.is_token_revoked", not_revoked)
    monkeypatch.setattr("src.core.auth.routes.revoke_token", record_revoke)
    async with httpx.AsyncClient(transport=transport, base_url="http://phase8.test") as client:
        response = await client.post(
            "/api/auth/refresh",
            cookies={
                bff_module.access_cookie_name(): signed_access_token(signing_key, auth_user_id),
                bff_module.refresh_cookie_name(): "old-provider-refresh-token",
            },
        )

    assert response.status_code == 403
    assert rotate_calls == []
    assert revoke_calls == []
    assert not any(cookie.startswith("wa_at=") for cookie in response.headers.get_list("set-cookie"))
    assert not any(cookie.startswith("wa_rt=") for cookie in response.headers.get_list("set-cookie"))


async def test_client_database_role_cannot_change_lifecycle_state(pg_pool):
    auth_user_id, _ = await create_auth_user(pg_pool)
    async with pg_pool.acquire() as conn:
        await conn.execute("SET ROLE authenticated")
        with pytest.raises(Exception):
            await conn.execute(
                "UPDATE auth_access_lifecycle SET disabled_at = now() WHERE auth_user_id = $1",
                auth_user_id,
            )
        await conn.execute("RESET ROLE")
    row = await lifecycle_row(pg_pool, auth_user_id)
    assert row["disabled_at"] is None


async def test_profile_recreation_cannot_reset_disabled_lifecycle_state(
    pg_pool, repo, sample_org
):
    auth_user_id, profile_id = await create_auth_user(pg_pool)
    await add_membership(pg_pool, sample_org["id"], profile_id)
    await repo.get_auth_access_allowed(str(auth_user_id))
    await repo.disable_auth_access(str(auth_user_id))
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM user_profiles WHERE id = $1", profile_id)
        recreated = await conn.fetchrow(
            "INSERT INTO user_profiles (auth_user_id, email) VALUES ($1, $2) RETURNING id",
            auth_user_id,
            f"recreated-{auth_user_id}@phase8.invalid",
        )
        assert recreated is not None

    row = await lifecycle_row(pg_pool, auth_user_id)
    assert row["disabled_at"] is not None
    assert row["provisioned_at"] is not None
    assert await repo.get_auth_access_allowed(str(auth_user_id)) is False


async def test_lifecycle_migration_replay_preserves_never_provisioned_and_disabled_state(
    pg_pool, repo
):
    never_id, _ = await create_auth_user(pg_pool)
    disabled_id, _ = await create_auth_user(pg_pool)
    await repo.get_auth_access_allowed(str(never_id))
    await repo.get_auth_access_allowed(str(disabled_id))
    await repo.disable_auth_access(str(disabled_id))
    before = {
        str(never_id): await lifecycle_row(pg_pool, never_id),
        str(disabled_id): await lifecycle_row(pg_pool, disabled_id),
    }
    migrations = []
    for path in sorted(glob.glob("src/core/db/migrations/0*.sql")):
        with open(path, encoding="utf-8") as migration_file:
            if "auth_access_lifecycle" in migration_file.read():
                migrations.append(path)
    assert len(migrations) == 1
    migration_path = migrations[0]
    async with pg_pool.acquire() as conn:
        with open(migration_path, encoding="utf-8") as migration_file:
            migration = migration_file.read()
        await conn.execute(migration)
        await conn.execute(migration)
    assert await lifecycle_row(pg_pool, never_id) == before[str(never_id)]
    assert await lifecycle_row(pg_pool, disabled_id) == before[str(disabled_id)]


async def test_concurrent_first_access_creates_one_organization(pg_pool, repo):
    auth_user_id, _ = await create_auth_user(pg_pool)

    first, second = await asyncio.gather(
        repo.get_or_create_organization_with_owner(str(auth_user_id), "Concurrent A"),
        repo.get_or_create_organization_with_owner(str(auth_user_id), "Concurrent B"),
    )

    assert first["organization_id"] == second["organization_id"]
    assert len(await repo.get_memberships_by_auth(str(auth_user_id))) == 1


async def test_concurrent_jit_and_disable_serialize_on_lifecycle_row(pg_pool, repo):
    auth_user_id, _ = await create_auth_user(pg_pool)
    lock_conn = await pg_pool.acquire()
    transaction = lock_conn.transaction()
    await transaction.start()
    await lock_conn.fetchrow(
        "SELECT auth_user_id FROM auth_access_lifecycle "
        "WHERE auth_user_id = $1 FOR UPDATE",
        auth_user_id,
    )
    jit_task = asyncio.create_task(
        repo.get_or_create_organization_with_owner(str(auth_user_id), "Race")
    )
    await asyncio.sleep(0.05)
    disable_task = asyncio.create_task(repo.disable_auth_access(str(auth_user_id)))
    await asyncio.sleep(0.05)
    await transaction.commit()
    await pg_pool.release(lock_conn)
    jit_result, disable_result = await asyncio.gather(
        jit_task, disable_task, return_exceptions=True
    )

    assert disable_result is True
    assert not isinstance(jit_result, Exception) or isinstance(jit_result, PermissionError)
    assert (await lifecycle_row(pg_pool, auth_user_id))["disabled_at"] is not None
    with pytest.raises(PermissionError):
        await repo.get_or_create_organization_with_owner(str(auth_user_id), "After disable")


async def test_authenticated_database_role_loses_rls_rows_after_disable(pg_pool, repo, sample_org):
    """Model PostgREST's authenticated role and caller-subject claim."""
    auth_user_id, profile_id = await create_auth_user(pg_pool)
    await add_membership(pg_pool, sample_org["id"], profile_id)
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
            LANGUAGE sql STABLE AS $$
                SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
            $$
        """)
        await conn.execute(
            "GRANT USAGE ON SCHEMA public, auth TO authenticated"
        )
        await conn.execute(
            "GRANT SELECT ON organizations, organization_memberships, user_profiles "
            "TO authenticated"
        )

    async def visible_row_counts():
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE authenticated")
                await conn.execute(
                    "SELECT set_config('request.jwt.claim.sub', $1, true)",
                    str(auth_user_id),
                )
                return (
                    await conn.fetchval("SELECT count(*) FROM public.user_profiles"),
                    await conn.fetchval("SELECT count(*) FROM public.organization_memberships"),
                )

    assert await visible_row_counts() == (1, 1)
    await repo.disable_auth_access(str(auth_user_id))
    assert await visible_row_counts() == (0, 0)

    with pytest.raises(Exception):
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE authenticated")
                await conn.execute(
                    "UPDATE public.auth_access_lifecycle SET disabled_at = now() "
                    "WHERE auth_user_id = $1",
                    auth_user_id,
                )
