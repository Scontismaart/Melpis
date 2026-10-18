"""Migration 060 contracts on the disposable CI PostgreSQL fixture only.

Each test rolls back its own grants, synthetic role membership and marker
changes. No shared schema reset or external database connection is introduced.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest


_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "src/core/db/migrations/060_quota_provenance.sql"
)
_MARKER = UUID("6206d423-cea9-5480-bb9b-59d757503f14")
_TABLES = ("messages", "usage_events")
_CLIENTS = ("anon", "authenticated")
_WRITES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


@pytest.fixture
async def quota_conn(pg_pool):
    async with pg_pool.acquire() as conn:
        async with conn.transaction():
            # An explicit final rollback also protects the happy path. Nested
            # migration failures use savepoints below and cannot leak ACLs.
            rollback = conn.transaction()
            await rollback.start()
            try:
                for table in _TABLES:
                    await conn.execute(f"GRANT ALL ON TABLE public.{table} TO service_role")
                yield conn
            finally:
                await rollback.rollback()


async def _apply(conn):
    await conn.execute(_MIGRATION.read_text(encoding="utf-8"))


async def _marker(conn):
    row = await conn.fetchrow(
        """SELECT organization_id, event_type, quantity, metadata
             FROM public.usage_events WHERE id = $1""",
        _MARKER,
    )
    if row is None:
        return None
    value = dict(row)
    if isinstance(value["metadata"], str):
        value["metadata"] = json.loads(value["metadata"])
    return value


async def _acl_snapshot(conn):
    tables = await conn.fetch(
        """SELECT c.relname, c.relacl::text, c.relrowsecurity, c.relowner
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY($1::text[])
            ORDER BY c.relname""",
        list(_TABLES),
    )
    columns = await conn.fetch(
        """SELECT c.relname, a.attname, a.attacl::text
             FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY($1::text[])
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY c.relname, a.attnum""",
        list(_TABLES),
    )
    return [tuple(row) for row in tables], [tuple(row) for row in columns]


async def _columns(conn, table):
    return await conn.fetch(
        """SELECT a.attname, quote_ident(a.attname) AS quoted
             FROM pg_attribute a
            WHERE a.attrelid = to_regclass($1)
              AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum""",
        f"public.{table}",
    )


async def _clear_client_grants(conn):
    for table in _TABLES:
        await conn.execute(
            f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC, anon, authenticated CASCADE"
        )
        for column in await _columns(conn, table):
            await conn.execute(
                f"REVOKE ALL ({column['quoted']}) ON TABLE public.{table} "
                "FROM PUBLIC, anon, authenticated CASCADE"
            )


def _assert_marker(row):
    assert row is not None
    assert row["organization_id"] is None
    assert row["event_type"] == "system.quota_trust_epoch"
    assert row["quantity"] == 0
    metadata = row["metadata"]
    assert set(metadata) == {"protocol_version", "trust_epoch"}
    assert type(metadata["protocol_version"]) is int
    assert metadata["protocol_version"] == 1
    assert isinstance(metadata["trust_epoch"], str)
    UUID(metadata["trust_epoch"])


@pytest.mark.parametrize("table", _TABLES)
async def test_060_revokes_client_and_public_table_and_column_writes(quota_conn, table):
    conn = quota_conn
    await _clear_client_grants(conn)
    await conn.execute(f"GRANT ALL ON TABLE public.{table} TO PUBLIC, anon, authenticated")
    await conn.execute(
        f"GRANT INSERT (id), UPDATE (id), REFERENCES (id) "
        f"ON TABLE public.{table} TO PUBLIC, anon, authenticated"
    )

    await _apply(conn)

    for role in _CLIENTS:
        for privilege in _WRITES:
            assert not await conn.fetchval(
                "SELECT has_table_privilege($1, $2, $3)",
                role, f"public.{table}", privilege,
            ), (table, role, privilege)
        for column in await _columns(conn, table):
            for privilege in ("INSERT", "UPDATE", "REFERENCES"):
                assert not await conn.fetchval(
                    "SELECT has_column_privilege($1, $2, $3, $4)",
                    role, f"public.{table}", column["attname"], privilege,
                ), (table, role, column["attname"], privilege)

    public_writes = await conn.fetchval(
        """SELECT count(*) FROM pg_class c,
                  LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) x
             WHERE c.oid = to_regclass($1) AND x.grantee = 0
               AND x.privilege_type <> 'SELECT'""",
        f"public.{table}",
    )
    assert public_writes == 0


@pytest.mark.parametrize("table", _TABLES)
async def test_060_preserves_existing_select_and_its_grant_options(quota_conn, table):
    conn = quota_conn
    await _clear_client_grants(conn)
    await conn.execute(f"GRANT SELECT ON public.{table} TO anon WITH GRANT OPTION")
    await conn.execute(
        f"GRANT SELECT (id) ON public.{table} TO authenticated WITH GRANT OPTION"
    )
    await conn.execute(f"GRANT SELECT ON public.{table} TO PUBLIC")
    await _apply(conn)

    assert await conn.fetchval(
        "SELECT has_table_privilege('anon', $1, 'SELECT WITH GRANT OPTION')",
        f"public.{table}",
    )
    assert await conn.fetchval(
        "SELECT has_column_privilege('authenticated', $1, 'id', 'SELECT WITH GRANT OPTION')",
        f"public.{table}",
    )
    assert await conn.fetchval(
        """SELECT EXISTS (SELECT 1 FROM pg_class c, LATERAL aclexplode(c.relacl) x
             WHERE c.oid = to_regclass($1) AND x.grantee = 0
               AND x.privilege_type = 'SELECT')""",
        f"public.{table}",
    )


@pytest.mark.parametrize("table", _TABLES)
async def test_060_does_not_create_previously_missing_select(quota_conn, table):
    conn = quota_conn
    await _clear_client_grants(conn)
    await _apply(conn)
    for role in _CLIENTS:
        assert not await conn.fetchval(
            "SELECT has_table_privilege($1, $2, 'SELECT')", role, f"public.{table}"
        )
        assert not await conn.fetchval(
            "SELECT has_any_column_privilege($1, $2, 'SELECT')", role, f"public.{table}"
        )


@pytest.mark.parametrize("table", _TABLES)
async def test_060_preserves_service_role_and_table_owner_writes(quota_conn, table):
    conn = quota_conn
    await conn.execute(f"GRANT ALL ON TABLE public.{table} TO service_role")
    owner = await conn.fetchval(
        "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = to_regclass($1)",
        f"public.{table}",
    )
    before = {}
    for role in ("service_role", owner):
        for privilege in _WRITES:
            before[(role, privilege)] = await conn.fetchval(
                "SELECT has_table_privilege($1, $2, $3)", role, f"public.{table}", privilege
            )
    assert all(before.values())
    await _apply(conn)
    for (role, privilege), expected in before.items():
        assert await conn.fetchval(
            "SELECT has_table_privilege($1, $2, $3)", role, f"public.{table}", privilege
        ) is expected


async def test_060_reserved_marker_shape_and_recognized_rerun_rotates_epoch(quota_conn):
    conn = quota_conn
    await conn.execute("DELETE FROM public.usage_events WHERE id = $1", _MARKER)
    await _apply(conn)
    first = await _marker(conn)
    _assert_marker(first)
    acl_before = await _acl_snapshot(conn)
    await _apply(conn)
    second = await _marker(conn)
    _assert_marker(second)
    assert first["metadata"]["trust_epoch"] != second["metadata"]["trust_epoch"]
    assert await _acl_snapshot(conn) == acl_before


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"protocol_version": "1", "trust_epoch": str(uuid4())},
        {"protocol_version": True, "trust_epoch": str(uuid4())},
        {"protocol_version": 2, "trust_epoch": str(uuid4())},
        {"protocol_version": 1, "trust_epoch": "not-a-uuid"},
        {"protocol_version": 1, "trust_epoch": str(uuid4()), "unknown": True},
    ],
)
async def test_060_unknown_marker_metadata_aborts_without_acl_changes(quota_conn, metadata):
    conn = quota_conn
    await conn.execute(
        "UPDATE public.usage_events SET metadata = $2::jsonb WHERE id = $1",
        _MARKER, json.dumps(metadata),
    )
    await conn.execute("GRANT INSERT ON public.messages TO authenticated")
    before = await _acl_snapshot(conn), await _marker(conn)
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await _apply(conn)
    assert (await _acl_snapshot(conn), await _marker(conn)) == before


@pytest.mark.parametrize("field,value", [("event_type", "other.event"), ("quantity", 1)])
async def test_060_unknown_marker_identity_aborts(quota_conn, field, value):
    conn = quota_conn
    await conn.execute(
        f"UPDATE public.usage_events SET {field} = $2 WHERE id = $1", _MARKER, value
    )
    before = await _acl_snapshot(conn), await _marker(conn)
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await _apply(conn)
    assert (await _acl_snapshot(conn), await _marker(conn)) == before


async def test_060_tenant_scoped_reserved_marker_aborts(quota_conn):
    conn = quota_conn
    org_id = uuid4()
    await conn.execute(
        "INSERT INTO public.organizations (id, name) VALUES ($1, 'Synthetic quota test')",
        org_id,
    )
    await conn.execute(
        "UPDATE public.usage_events SET organization_id = $2 WHERE id = $1",
        _MARKER, org_id,
    )
    before = await _acl_snapshot(conn), await _marker(conn)
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await _apply(conn)
    assert (await _acl_snapshot(conn), await _marker(conn)) == before


@pytest.mark.parametrize("table", _TABLES)
@pytest.mark.parametrize("scope", ["table", "column"])
async def test_060_inherited_client_write_aborts_entire_migration(quota_conn, table, scope):
    conn = quota_conn
    role = f"quota_test_parent_{uuid4().hex}"
    await conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
    await conn.execute(f'GRANT "{role}" TO authenticated')
    privilege = "UPDATE" if scope == "table" else "UPDATE (id)"
    await conn.execute(f'GRANT {privilege} ON public.{table} TO "{role}"')
    await conn.execute("GRANT INSERT ON public.messages TO anon")
    before = await _acl_snapshot(conn), await _marker(conn)
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await _apply(conn)
    assert (await _acl_snapshot(conn), await _marker(conn)) == before
    assert await conn.fetchval(
        "SELECT has_column_privilege('authenticated', $1, 'id', 'UPDATE')",
        f"public.{table}",
    )


@pytest.mark.parametrize("table", _TABLES)
async def test_060_requires_rls_already_enabled_and_does_not_change_it(quota_conn, table):
    conn = quota_conn
    await conn.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
    before = await _acl_snapshot(conn), await _marker(conn)
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await _apply(conn)
    assert (await _acl_snapshot(conn), await _marker(conn)) == before


@pytest.mark.parametrize("table", _TABLES)
async def test_060_backend_write_only_via_public_aborts_without_losing_capabilities(
    quota_conn, table
):
    conn = quota_conn
    await conn.execute(f"REVOKE ALL ON public.{table} FROM service_role")
    await conn.execute(f"GRANT ALL ON public.{table} TO PUBLIC")
    assert await conn.fetchval(
        "SELECT has_table_privilege('service_role', $1, 'INSERT')", f"public.{table}"
    )
    before = await _acl_snapshot(conn), await _marker(conn)
    with pytest.raises(asyncpg.PostgresError, match="Backend table capability changed"):
        async with conn.transaction():
            await _apply(conn)
    assert (await _acl_snapshot(conn), await _marker(conn)) == before


@pytest.mark.parametrize("table", _TABLES)
@pytest.mark.parametrize("role", _CLIENTS)
async def test_060_real_client_mutations_are_denied_with_42501(quota_conn, table, role):
    conn = quota_conn
    await conn.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    await conn.execute(f"GRANT ALL ON public.{table} TO {role}")
    await _apply(conn)
    await conn.execute(f"SET LOCAL ROLE {role}")
    try:
        for command in (
            f"INSERT INTO public.{table} DEFAULT VALUES",
            f"UPDATE public.{table} SET id = id WHERE false",
            f"DELETE FROM public.{table} WHERE false",
            f"TRUNCATE TABLE public.{table}",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
                async with conn.transaction():
                    await conn.execute(command)
            assert denied.value.sqlstate == "42501"
    finally:
        await conn.execute("RESET ROLE")


@pytest.mark.parametrize("table", _TABLES)
async def test_060_dependent_select_grant_restrict_failure_rolls_back(quota_conn, table):
    conn = quota_conn
    dependent = f"quota_select_dependent_{uuid4().hex}"
    await conn.execute(f'CREATE ROLE "{dependent}" NOLOGIN')
    await conn.execute("GRANT USAGE ON SCHEMA public TO anon")
    await conn.execute(f"GRANT SELECT ON public.{table} TO anon WITH GRANT OPTION")
    await conn.execute("SET LOCAL ROLE anon")
    try:
        await conn.execute(f'GRANT SELECT ON public.{table} TO "{dependent}"')
    finally:
        await conn.execute("RESET ROLE")
    before = await _acl_snapshot(conn), await _marker(conn)
    with pytest.raises(asyncpg.DependentObjectsStillExistError) as denied:
        async with conn.transaction():
            await _apply(conn)
    assert denied.value.sqlstate == "2BP01"
    assert (await _acl_snapshot(conn), await _marker(conn)) == before


@pytest.mark.parametrize("table", _TABLES)
async def test_060_pg17_maintain_client_revoked_backend_preserved(quota_conn, table):
    conn = quota_conn
    if int(await conn.fetchval("SHOW server_version_num")) < 170000:
        pytest.skip("MAINTAIN privilege requires PostgreSQL 17 or newer")
    await conn.execute(f"GRANT MAINTAIN ON public.{table} TO PUBLIC, anon, authenticated")
    await conn.execute(f"GRANT MAINTAIN ON public.{table} TO service_role")
    await _apply(conn)
    for role in _CLIENTS:
        assert not await conn.fetchval(
            "SELECT has_table_privilege($1, $2, 'MAINTAIN')", role, f"public.{table}"
        )
    assert await conn.fetchval(
        "SELECT has_table_privilege('service_role', $1, 'MAINTAIN')", f"public.{table}"
    )
