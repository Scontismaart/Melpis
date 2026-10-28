"""Real PostgreSQL regression tests for the booking credential ACL boundary."""
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from cryptography.fernet import Fernet

from src.core.db.repositories.external_booking_repo import ExternalBookingRepository

_MIGRATION = Path(__file__).resolve().parents[2] / "src/core/db/migrations/067_booking_credentials_acl.sql"
_TABLE = "public.external_booking_credentials"


@pytest.fixture
async def acl_conn(pg_pool):
    async with pg_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            # The production Supabase backend role bypasses RLS; the local fixture
            # creates a bare role. Both this adjustment and grants are rolled back.
            await conn.execute("GRANT USAGE ON SCHEMA public, auth TO anon, authenticated")
            await conn.execute("ALTER ROLE service_role BYPASSRLS")
            await conn.execute(f"GRANT ALL ON {_TABLE} TO service_role")
            yield conn
        finally:
            await tx.rollback()


async def _apply(conn):
    await conn.execute(_MIGRATION.read_text(encoding="utf-8"))


async def _snapshot(conn, other=False):
    condition = "c.relname <> 'external_booking_credentials'" if other else "c.relname = 'external_booking_credentials'"
    return [tuple(r) for r in await conn.fetch(f"""
        SELECT c.relname, c.relacl::text, a.attname, a.attacl::text
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S') AND {condition}
        ORDER BY c.relname, a.attnum
    """)]


async def _org(conn):
    org = uuid4()
    await conn.execute("INSERT INTO organizations(id, name) VALUES($1, 'Synthetic ACL test')", org)
    return org


async def _member(conn, org, role):
    user = uuid4()
    await conn.execute("INSERT INTO auth.users(id,email) VALUES($1,$2)", user, f"{user}@example.invalid")
    profile = await conn.fetchval("SELECT id FROM user_profiles WHERE auth_user_id=$1", user)
    await conn.execute("INSERT INTO organization_memberships(organization_id,user_id,ruolo) VALUES($1,$2,$3)", org, profile, role)
    return user


async def _identity(conn, role, user=None, aal='aal1'):
    await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, true)", str(user or uuid4()))
    await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", '{"aal":"' + aal + '"}')
    await conn.execute(f"SET LOCAL ROLE {role}")


async def _denied(conn, sql, *args):
    # A savepoint restores the surrounding transaction after PostgreSQL 42501.
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await conn.execute(sql, *args)


async def test_client_grants_removed_and_operations_denied(acl_conn):
    conn = acl_conn
    org = await _org(conn)
    await conn.execute(f"INSERT INTO {_TABLE}(organization_id,provider,credentials_encrypted) VALUES($1,'synthetic','ciphertext')", org)
    members = {r: await _member(conn, org, r) for r in ('staff', 'manager', 'owner')}
    await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO PUBLIC, anon, authenticated")
    await conn.execute(f"GRANT SELECT (credentials_encrypted), UPDATE (config) ON {_TABLE} TO PUBLIC, anon, authenticated")
    before_other = await _snapshot(conn, other=True)
    await _apply(conn)
    assert await _snapshot(conn, other=True) == before_other
    for role in ('anon', 'authenticated'):
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            assert not await conn.fetchval("SELECT has_table_privilege($1,$2,$3)", role, _TABLE, privilege)
        for column in await conn.fetch("SELECT attname FROM pg_attribute WHERE attrelid=$1::regclass AND attnum>0 AND NOT attisdropped", _TABLE):
            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'REFERENCES'):
                assert not await conn.fetchval("SELECT has_column_privilege($1,$2,$3,$4)", role, _TABLE, column['attname'], privilege)
    first = await _snapshot(conn)
    await _apply(conn)
    assert await _snapshot(conn) == first
    for role, user, aal in [('anon', None, 'aal1')] + [
        ('authenticated', members[r], aal) for r in members for aal in ('aal1', 'aal2')
    ]:
        await _identity(conn, role, user, aal)
        for sql, args in (
            (f"SELECT credentials_encrypted FROM {_TABLE} WHERE organization_id=$1", (org,)),
            (f"INSERT INTO {_TABLE}(organization_id,provider,credentials_encrypted,config) VALUES($1,'x','x',jsonb_build_object('medical_dpa_signed',true))", (uuid4(),)),
            (f"UPDATE {_TABLE} SET config=jsonb_build_object('medical_dpa_signed',true) WHERE organization_id=$1", (org,)),
            (f"DELETE FROM {_TABLE} WHERE organization_id=$1", (org,)),
        ):
            await _denied(conn, sql, *args)
        await conn.execute("RESET ROLE")


@pytest.mark.parametrize('grant', ['SELECT', 'UPDATE (config)'])
async def test_inherited_privilege_aborts_atomically(acl_conn, grant):
    conn = acl_conn
    await conn.execute("CREATE ROLE booking_acl_inherited NOLOGIN")
    await conn.execute(f"GRANT {grant} ON {_TABLE} TO booking_acl_inherited")
    await conn.execute("GRANT booking_acl_inherited TO authenticated")
    await conn.execute(f"GRANT SELECT ON {_TABLE} TO anon")
    before = await _snapshot(conn)
    other = await _snapshot(conn, other=True)
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await _apply(conn)
    assert await _snapshot(conn) == before
    assert await _snapshot(conn, other=True) == other
    assert await conn.fetchval("SELECT pg_has_role('authenticated','booking_acl_inherited','MEMBER')")


async def test_legacy_member_policy_exposes_read_while_gdpr_guard_blocks_update(acl_conn):
    conn = acl_conn
    org, other = await _org(conn), await _org(conn)
    user = await _member(conn, org, 'staff')
    for tenant in (org, other):
        await conn.execute(f"INSERT INTO {_TABLE}(organization_id,provider,credentials_encrypted) VALUES($1,'synthetic','ciphertext')", tenant)
    await conn.execute("""
        CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
        GRANT USAGE ON SCHEMA public, auth TO authenticated;
        GRANT SELECT ON organization_memberships, user_profiles TO authenticated;
    """)
    # Remove all policies on the target only, then recreate the exact 047 policy.
    policies = await conn.fetch("SELECT policyname FROM pg_policies WHERE schemaname='public' AND tablename='external_booking_credentials'")
    for row in policies:
        quoted = await conn.fetchval('SELECT quote_ident($1)', row['policyname'])
        await conn.execute(f"DROP POLICY {quoted} ON {_TABLE}")
    await conn.execute(f"""
        CREATE POLICY external_booking_credentials_org_member ON {_TABLE} FOR ALL
        USING (organization_id IN (SELECT om.organization_id FROM organization_memberships om JOIN user_profiles up ON up.id=om.user_id WHERE up.auth_user_id=auth.uid()))
        WITH CHECK (organization_id IN (SELECT om.organization_id FROM organization_memberships om JOIN user_profiles up ON up.id=om.user_id WHERE up.auth_user_id=auth.uid()));
        GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO authenticated;
    """)
    await _identity(conn, 'authenticated', user)
    row = await conn.fetchrow(f"SELECT credentials_encrypted, config FROM {_TABLE} WHERE organization_id=$1", org)
    assert row['credentials_encrypted'] == 'ciphertext'
    assert row['config'] is not None
    assert await conn.fetchrow(f"SELECT credentials_encrypted, config FROM {_TABLE} WHERE organization_id=$1", other) is None
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match='gdpr_erasure_barriers'):
        async with conn.transaction():
            await conn.execute(f"UPDATE {_TABLE} SET config=jsonb_build_object('medical_dpa_signed',true) WHERE organization_id=$1", org)
    await conn.execute('RESET ROLE')
    await _apply(conn)
    await _identity(conn, 'authenticated', user)
    await _denied(conn, f"SELECT credentials_encrypted, config FROM {_TABLE} WHERE organization_id=$1", org)
    await _denied(conn, f"UPDATE {_TABLE} SET config=jsonb_build_object('medical_dpa_signed',false) WHERE organization_id=$1", org)
    await conn.execute('RESET ROLE')


class _SingleConnectionPool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def test_backend_repository_roundtrip_and_tenant_scope(acl_conn, monkeypatch):
    conn = acl_conn
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
    org, other = await _org(conn), await _org(conn)
    before = await conn.fetchrow("SELECT has_table_privilege('service_role',$1,'SELECT,INSERT,UPDATE,DELETE') AS service, has_table_privilege(current_user,$1,'SELECT,INSERT,UPDATE,DELETE') AS owner", _TABLE)
    await _apply(conn)
    after = await conn.fetchrow("SELECT has_table_privilege('service_role',$1,'SELECT,INSERT,UPDATE,DELETE') AS service, has_table_privilege(current_user,$1,'SELECT,INSERT,UPDATE,DELETE') AS owner", _TABLE)
    assert tuple(after) == tuple(before) == (True, True)
    await conn.execute('SET LOCAL ROLE service_role')
    repo = ExternalBookingRepository(_SingleConnectionPool(conn))
    await repo.save_credentials(org, 'synthetic', {'access_token': 'old', 'client_id': 'synthetic'})
    await repo.save_credentials(other, 'synthetic', {'access_token': 'other'})
    refreshed = await repo.update_credential_fields(org, {'access_token': 'new'})
    assert refreshed['credentials'] == {'access_token': 'new', 'client_id': 'synthetic'}
    assert (await repo.get_credentials(other))['credentials'] == {'access_token': 'other'}
    assert await repo.delete_credentials(org)
    assert await repo.get_credentials(org) is None
    assert (await repo.get_credentials(other))['credentials']['access_token'] == 'other'
    await conn.execute('RESET ROLE')


async def test_regrant_and_permissive_policy_cannot_bypass_restrictive_deny(acl_conn):
    conn = acl_conn
    org = await _org(conn)
    user = await _member(conn, org, 'staff')
    await conn.execute(f"INSERT INTO {_TABLE}(organization_id,provider,credentials_encrypted) VALUES($1,'synthetic','ciphertext')", org)
    await _apply(conn)
    await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO authenticated")
    await conn.execute(f"CREATE POLICY synthetic_permissive ON {_TABLE} FOR ALL TO authenticated USING (true) WITH CHECK (true)")
    await _identity(conn, 'authenticated', user)
    assert await conn.fetch(f"SELECT * FROM {_TABLE} WHERE organization_id=$1", org) == []
    assert await conn.execute(f"UPDATE {_TABLE} SET config=jsonb_build_object('medical_dpa_signed',true) WHERE organization_id=$1", org) == 'UPDATE 0'
    assert await conn.execute(f"DELETE FROM {_TABLE} WHERE organization_id=$1", org) == 'DELETE 0'
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await conn.execute(f"INSERT INTO {_TABLE}(organization_id,provider,credentials_encrypted,config) VALUES($1,'synthetic','ciphertext',jsonb_build_object('medical_dpa_signed',true))", uuid4())
    await conn.execute('RESET ROLE')


async def test_backend_capability_from_public_aborts_without_implicit_grant(acl_conn):
    conn = acl_conn
    await conn.execute(f"REVOKE SELECT ON {_TABLE} FROM service_role")
    await conn.execute(f"GRANT SELECT ON {_TABLE} TO PUBLIC")
    assert await conn.fetchval("SELECT has_table_privilege('service_role',$1,'SELECT')", _TABLE)
    before = await _snapshot(conn)
    other = await _snapshot(conn, other=True)
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await _apply(conn)
    assert await _snapshot(conn) == before
    assert await _snapshot(conn, other=True) == other


async def test_backend_without_bypass_requires_role_contract_review(acl_conn):
    conn = acl_conn
    await conn.execute("ALTER ROLE service_role NOBYPASSRLS")
    before = await _snapshot(conn)
    other = await _snapshot(conn, other=True)
    with pytest.raises(asyncpg.PostgresError, match='Backend row access requires role-contract review'):
        async with conn.transaction():
            await _apply(conn)
    assert await _snapshot(conn) == before
    assert await _snapshot(conn, other=True) == other
