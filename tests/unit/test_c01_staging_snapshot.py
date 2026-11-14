from __future__ import annotations

import json
import asyncio
import importlib.util
import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "c01_staging_snapshot.py"
_SPEC = importlib.util.spec_from_file_location("c01_staging_snapshot", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

C01SnapshotError = _MODULE.C01SnapshotError
_database_dump_args = _MODULE._database_dump_args
compare_snapshots = _MODULE.compare_snapshots
create_integrity_manifest = _MODULE.create_integrity_manifest
decrypt_bundle = _MODULE.decrypt_bundle
encrypt_bundle = _MODULE.encrypt_bundle
parse_source_env = _MODULE.parse_source_env
verify_integrity_manifest = _MODULE.verify_integrity_manifest
verify_persisted_backup = _MODULE.verify_persisted_backup
persist_encrypted_backup = _MODULE.persist_encrypted_backup
plan_clone_role_normalizations = _MODULE.plan_clone_role_normalizations
plan_clone_extension_normalizations = _MODULE.plan_clone_extension_normalizations
plan_clone_normalizations = _MODULE.plan_clone_normalizations
restore_encrypted_backup = _MODULE.restore_encrypted_backup
load_or_create_master_key = _MODULE.load_or_create_master_key
parse_local_restore_env = _MODULE._parse_local_restore_env
validate_restore_container = _MODULE.validate_restore_container
build_bundle = _MODULE.build_bundle
open_bundle = _MODULE.open_bundle


_KEY = bytes(range(32))


def _env(**overrides):
    values = {
        "PGHOST": "aws-1-eu-central-1.pooler.supabase.com",
        "PGPORT": "5432",
        "PGDATABASE": "postgres",
        "PGUSER": "postgres.ucncxwnhcdrqonknkvau",
        "PGPASSWORD": "synthetic-password-never-output",
        "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": "/certs/prod-ca-2021.crt",
        "PGAPPNAME": "melpis-c01-preflight",
    }
    values.update(overrides)
    return "\n".join(f"{key}={value}" for key, value in values.items())


def _snapshot():
    return {
        "database": {"datname": "postgres", "owner": "postgres", "acl": "{...}"},
        "project_ref": "ucncxwnhcdrqonknkvau",
        "snapshot_search_path": "pg_catalog",
        "extensions": [{"extname": "vector", "extversion": "0.8.2", "schema_name": "extensions"}],
        "roles": [{"rolname": "authenticated", "rolbypassrls": False}],
        "memberships": [],
        "schemas": [{"nspname": "public", "owner": "postgres", "acl": "{...}"}],
        "default_privileges": [],
        "relations": [{"nspname": "public", "relname": "x", "acl": "{...}", "relrowsecurity": True}],
        "row_counts": [{"schema": "public", "name": "x", "count": 0}],
        "sequence_state": [{"schema": "public", "name": "x_id_seq", "last_value": "42",
                            "is_called": True, "start_value": "1", "increment_by": "1",
                            "min_value": "1", "max_value": "9223372036854775807",
                            "cache_size": "1", "cycle": False}],
        "policies": [{"schemaname": "public", "tablename": "x", "policyname": "org_scope"}],
        "column_privileges": [],
        "columns": [{"schema_name": "public", "relation_name": "x", "column_name": "id",
                     "ordinal_position": 1, "data_type": "bigint", "not_null": True,
                     "identity_kind": "", "generated_kind": "", "collation": None,
                     "column_default": "nextval('public.x_id_seq'::regclass)"}],
        "sequence_ownership": [{"sequence_schema": "public", "sequence_name": "x_id_seq",
                                "table_schema": "public", "table_name": "x", "column_name": "id",
                                "dependency_type": "a"}],
        "triggers": [{"schema_name": "auth", "table_name": "users", "tgname": "trg_sync_auth_user"}],
        "functions": [{"schema_name": "public", "proname": "sync_auth_user_profile", "security_definer": True,
                       "function_config": ["search_path=public, pg_temp"], "acl": "{...}"}],
        "constraints": [],
        "indexes": [],
        "types": [],
        "migration_history": [{"version": "20261016190116", "name": "melpis_v1_059_auth_access_lifecycle"}],
    }


def _valid_inspect():
    return {
        "Name": "/melpis-c01-restore-20261113-a1b2c3d4",
        "Id": "container-id-c01",
        "State": {"Running": True},
        "HostConfig": {"NetworkMode": "melpis-c01-restore-20261113-a1b2c3d4-net",
                       "PortBindings": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55432"}]},
                       "Binds": []},
        "Config": {"Image": "public.ecr.aws/supabase/postgres:17.6.1.171"},
        "Image": "sha256:" + "a" * 64,
        "Mounts": [{"Type": "tmpfs", "Destination": "/var/lib/postgresql/data"}],
        "NetworkSettings": {
            "Networks": {"melpis-c01-restore-20261113-a1b2c3d4-net": {"NetworkID": "network-id-c01"}},
            "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55432"}]},
        },
    }


def _valid_network():
    return {"Name": "melpis-c01-restore-20261113-a1b2c3d4-net", "Internal": True,
            "Id": "network-id-c01", "Containers": {"container-id-c01": {}}}


def test_source_configuration_is_pinned_to_staging_session_pooler():
    settings = parse_source_env(_env())
    assert settings.project_ref == "ucncxwnhcdrqonknkvau"
    assert settings.port == 5432
    assert settings.sslmode == "verify-full"


@pytest.mark.parametrize("override", [
    {"PGHOST": "db.ucncxwnhcdrqonknkvau.supabase.co"},
    {"PGHOST": "aws-1-eu-central-1.pooler.supabase.com", "PGPORT": "6543"},
    {"PGUSER": "postgres.otherprojectref000000"},
    {"PGDATABASE": "production"},
    {"PGSSLMODE": "require"},
    {"PGSSLMODE": "disable"},
    {"PGSSLROOTCERT": "/wrong/ca.crt"},
    {"PGAPPNAME": "unspecified"},
])
def test_source_configuration_rejects_wrong_target_or_weak_tls(override):
    with pytest.raises(C01SnapshotError, match="c01_source_configuration_rejected"):
        parse_source_env(_env(**override))


def test_source_configuration_rejects_duplicate_or_unexpected_keys():
    with pytest.raises(C01SnapshotError):
        parse_source_env(_env() + "\nPGHOST=evil")
    with pytest.raises(C01SnapshotError):
        parse_source_env(_env() + "\nPGOPTIONS=-c search_path=public")


def test_windows_private_acl_is_exact_but_case_insensitive():
    acl = {
        "Protected": True,
        "Me": "FAMIGLIA\\muzzo",
        "Entries": [
            {"Who": "Famiglia\\muzzo", "Type": "Allow", "Rights": "FullControl",
             "Inherit": "None", "Propagate": "None", "Inherited": False},
            {"Who": "BUILTIN\\Administrators", "Type": "Allow", "Rights": "FullControl",
             "Inherit": "None", "Propagate": "None", "Inherited": False},
            {"Who": "NT AUTHORITY\\SYSTEM", "Type": "Allow", "Rights": "FullControl",
             "Inherit": "None", "Propagate": "None", "Inherited": False},
        ],
    }
    assert _MODULE._windows_private_acl_is_valid(acl)
    acl["Entries"].append({"Who": "Everyone", "Type": "Allow", "Rights": "FullControl",
                           "Inherit": "None", "Propagate": "None", "Inherited": False})
    assert not _MODULE._windows_private_acl_is_valid(acl)


def test_windows_private_acl_accepts_only_expected_inherited_file_entries():
    expected_parent = [
        {"Who": "Famiglia\\muzzo", "Type": "Allow", "Rights": "FullControl",
         "Inherit": "ContainerInherit, ObjectInherit", "Propagate": "None", "Inherited": False},
        {"Who": "BUILTIN\\Administrators", "Type": "Allow", "Rights": "FullControl",
         "Inherit": "ContainerInherit, ObjectInherit", "Propagate": "None", "Inherited": False},
        {"Who": "NT AUTHORITY\\SYSTEM", "Type": "Allow", "Rights": "FullControl",
         "Inherit": "ContainerInherit, ObjectInherit", "Propagate": "None", "Inherited": False},
    ]
    acl = {
        "Protected": False, "Me": "Famiglia\\muzzo", "ParentProtected": True,
        "ParentEntries": expected_parent,
        "Entries": [
            {"Who": entry["Who"], "Type": "Allow", "Rights": "FullControl",
             "Inherit": "None", "Propagate": "None", "Inherited": True}
            for entry in expected_parent
        ],
    }
    assert _MODULE._windows_private_acl_is_valid(acl)
    acl["ParentProtected"] = False
    assert not _MODULE._windows_private_acl_is_valid(acl)


def test_windows_private_directory_requires_protected_inheritable_acl():
    entries = [
        {"Who": who, "Type": "Allow", "Rights": "FullControl",
         "Inherit": "ContainerInherit, ObjectInherit", "Propagate": "None", "Inherited": False}
        for who in ("Famiglia\\muzzo", "BUILTIN\\Administrators", "NT AUTHORITY\\SYSTEM")
    ]
    assert _MODULE._windows_private_directory_acl_is_valid(
        {"Protected": True, "Me": "Famiglia\\muzzo", "Entries": entries}
    )


def test_dump_arguments_preserve_database_owner_and_acl():
    args = _database_dump_args("00000003-0000001B-1")
    assert "--create" in args
    assert "--no-owner" not in args
    assert "--no-privileges" not in args


@pytest.mark.parametrize("role_dump", [
    "CREATE ROLE synthetic LOGIN PASSWORD 'hash';\n",
    "ALTER ROLE synthetic\n WITH LOGIN PASSWORD 'hash';\n",
])
def test_role_dump_password_guard_handles_single_and_multiline_statements(role_dump):
    assert _MODULE._role_dump_contains_password(role_dump)
    assert not _MODULE._role_dump_contains_password("ALTER ROLE synthetic WITH LOGIN;\n")


def test_backup_refuses_password_role_dump_before_persisting_artifact(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    source = _snapshot()

    @asynccontextmanager
    async def fake_snapshot(_settings, _ca):
        yield "00000003-0000001B-1", source

    async def fake_role_snapshot(_settings, _ca):
        return source["roles"], source["memberships"]

    monkeypatch.setattr(_MODULE, "load_source_settings", lambda _path: object())
    monkeypatch.setattr(_MODULE, "source_snapshot", fake_snapshot)
    monkeypatch.setattr(_MODULE, "dump_with_docker", lambda *_args, **_kwargs: (
        b"synthetic custom archive", b"CREATE ROLE synthetic LOGIN PASSWORD 'credential-hash';\n",
    ))
    monkeypatch.setattr(_MODULE, "_role_snapshot", fake_role_snapshot)
    monkeypatch.setattr(_MODULE, "_enforce_private_directory", lambda _path: None)

    with pytest.raises(C01SnapshotError, match="c01_roles_dump_invalid"):
        asyncio.run(_MODULE.create_staging_backup("source.env", "ca.crt", tmp_path, _KEY))
    assert list(tmp_path.iterdir()) == []


def test_encrypted_bundle_roundtrip_and_manifest_integrity():
    snapshot = _snapshot()
    clear = build_bundle(b"synthetic custom pg archive", b"-- roles only; no passwords\n", snapshot)
    artifact = encrypt_bundle(clear, _KEY)
    manifest = create_integrity_manifest(artifact, _KEY, {"extensions": ["vector"]})
    public = verify_integrity_manifest(artifact, manifest, _KEY)
    assert public["project_ref"] == "ucncxwnhcdrqonknkvau"
    assert open_bundle(decrypt_bundle(artifact, _KEY))["database.dump"] == b"synthetic custom pg archive"
    with pytest.raises(C01SnapshotError, match="authentication_failed"):
        decrypt_bundle(artifact[:-1] + bytes([artifact[-1] ^ 1]), _KEY)
    with pytest.raises(C01SnapshotError, match="authentication_failed"):
        decrypt_bundle(artifact, bytes(reversed(_KEY)))


def test_manifest_detects_artifact_change_and_metadata_tampering():
    artifact = encrypt_bundle(build_bundle(b"dump", b"roles", _snapshot()), _KEY)
    manifest = create_integrity_manifest(artifact, _KEY, {"integrity": "fixture"})
    tampered = json.loads(manifest)
    tampered["artifact_sha256"] = "0" * 64
    with pytest.raises(C01SnapshotError, match="c01_manifest_invalid"):
        verify_integrity_manifest(artifact, json.dumps(tampered).encode(), _KEY)
    with pytest.raises(C01SnapshotError, match="c01_manifest_invalid"):
        verify_integrity_manifest(artifact + b"x", manifest, _KEY)


def test_persisted_backup_is_encrypted_and_manifested(tmp_path, monkeypatch):
    monkeypatch.setattr(_MODULE, "_enforce_private_directory", lambda path: None)
    monkeypatch.setattr(_MODULE, "_assert_private_file", lambda path: None)
    path, manifest = persist_encrypted_backup(
        tmp_path, "c01-staging-20261113T120000Z-a1b2c3d4e5f6.enc",
        b"private synthetic database archive", b"-- role definitions without passwords",
        _snapshot(), _KEY,
    )
    assert path.exists() and manifest.exists()
    assert b"private synthetic" not in path.read_bytes()
    contents = verify_persisted_backup(path, manifest, _KEY)
    assert contents["database.dump"] == b"private synthetic database archive"


def test_bundle_rejects_partial_or_unexpected_members():
    with pytest.raises(C01SnapshotError, match="c01_bundle_incomplete"):
        build_bundle(b"dump", b"", _snapshot())
    with pytest.raises(C01SnapshotError, match="c01_bundle_invalid"):
        open_bundle(b"not-a-tar")


def _normalization_plan():
    return _MODULE.CloneNormalizationPlan(
        ('ALTER ROLE "pgbouncer" NOBYPASSRLS',),
        ("recreate-postgres-from-template0", "discard-image-only-pg_graphql",
         "restore-source-vector-0.8.2"),
        "a" * 64, "b" * 64,
    )


def test_restore_target_requires_network_none_tmpfs_pg17_and_no_ports():
    validate_restore_container(_valid_inspect(), _valid_network(), expected_image_id="sha256:" + "a" * 64,
                               normalization_plan=_normalization_plan())
    bad_cases = []
    unsafe_network = _valid_inspect()
    unsafe_network["HostConfig"]["NetworkMode"] = "bridge"
    bad_cases.append(unsafe_network)
    exposed_port = _valid_inspect()
    exposed_port["HostConfig"]["PortBindings"] = {"5432/tcp": [{"HostPort": "55432"}]}
    bad_cases.append(exposed_port)
    bind_mount = _valid_inspect()
    bind_mount["HostConfig"]["Binds"] = ["C:\\data:/var/lib/postgresql/data"]
    bad_cases.append(bind_mount)
    wrong_mount = _valid_inspect()
    wrong_mount["Mounts"] = [{"Type": "volume", "Destination": "/var/lib/postgresql/data"}]
    bad_cases.append(wrong_mount)
    wrong_image = _valid_inspect()
    wrong_image["Config"]["Image"] = "postgres:17"
    bad_cases.append(wrong_image)
    for value in bad_cases:
        with pytest.raises(C01SnapshotError, match="c01_restore_target_not_isolated"):
            validate_restore_container(value, _valid_network(), expected_image_id="sha256:" + "a" * 64,
                                       normalization_plan=_normalization_plan())


@pytest.mark.parametrize("path,value", [
    (("extensions", 0, "extversion"), "0.7.0"),
    (("roles", 0, "rolbypassrls"), True),
    (("relations", 0, "acl"), "{authenticated=arwd/postgres}"),
    (("relations", 0, "relrowsecurity"), False),
    (("triggers", 0, "tgname"), "missing_auth_trigger"),
    (("functions", 0, "function_config"), ["search_path=public"]),
    (("row_counts", 0, "count"), 1),
    (("sequence_state", 0, "last_value"), "41"),
    (("sequence_state", 0, "is_called"), False),
    (("sequence_ownership", 0, "dependency_type"), "i"),
    (("columns", 0, "column_default"), None),
    (("migration_history", 0, "version"), "060"),
])
def test_restore_comparison_fails_closed_for_catalog_and_partial_restore_drift(path, value):
    source = _snapshot()
    restored = json.loads(json.dumps(source))
    section, index, field = path
    restored[section][index][field] = value
    with pytest.raises(C01SnapshotError, match="c01_restore_catalog_or_data_mismatch"):
        compare_snapshots(source, restored)


def test_restore_comparison_requires_role_inventory_and_auth_trigger():
    source = _snapshot()
    restored = json.loads(json.dumps(source))
    restored["roles"] = []  # required platform role is absent
    with pytest.raises(C01SnapshotError):
        compare_snapshots(source, restored)
    restored = json.loads(json.dumps(source))
    restored["triggers"] = []  # Auth trigger absent
    with pytest.raises(C01SnapshotError):
        compare_snapshots(source, restored)


def _role(name, **changes):
    value = {
        "rolname": name, "rolsuper": False, "rolinherit": True,
        "rolcreaterole": False, "rolcreatedb": False, "rolreplication": False,
        "rolbypassrls": False, "rolcanlogin": False, "rolconnlimit": -1, "valid_until": None,
        "rolconfig": None,
    }
    value.update(changes)
    return value


def test_clone_normalization_is_limited_to_two_local_nobypassrls_differences():
    source = [_role("pgbouncer"), _role("supabase_auth_admin")]
    target = [_role("pgbouncer", rolbypassrls=True), _role("supabase_auth_admin", rolbypassrls=True)]
    assert plan_clone_role_normalizations(source, target, [], []) == [
        'ALTER ROLE "pgbouncer" NOBYPASSRLS',
        'ALTER ROLE "supabase_auth_admin" NOBYPASSRLS',
    ]


def test_clone_normalizes_only_documented_realtime_admin_role_and_memberships():
    expected_row = _role("supabase_realtime_admin", rolinherit=False)
    source_roles = [_role("anon"), _role("authenticated"), _role("service_role", rolbypassrls=True), expected_row]
    target_roles = source_roles[:-1]
    source_memberships = [
        {"granted_role": role, "member_role": "supabase_realtime_admin", "grantor_role": "supabase_admin",
         "admin_option": False, "inherit_option": False, "set_option": True}
        for role in ("anon", "authenticated", "service_role")
    ]
    actual = plan_clone_role_normalizations(source_roles, target_roles, source_memberships, [])
    assert actual[0] == ('CREATE ROLE "supabase_realtime_admin" NOLOGIN NOBYPASSRLS NOINHERIT '
                         'NOCREATEROLE NOCREATEDB NOREPLICATION')
    assert actual[1:] == [
        'GRANT "anon" TO "supabase_realtime_admin" WITH ADMIN FALSE, INHERIT FALSE, SET TRUE',
        'GRANT "authenticated" TO "supabase_realtime_admin" WITH ADMIN FALSE, INHERIT FALSE, SET TRUE',
        'GRANT "service_role" TO "supabase_realtime_admin" WITH ADMIN FALSE, INHERIT FALSE, SET TRUE',
    ]


@pytest.mark.parametrize("target", [
    [_role("pgbouncer", rolbypassrls=True), _role("supabase_auth_admin", rolcreaterole=True)],
    [_role("pgbouncer", rolbypassrls=True)],
    [_role("pgbouncer", rolbypassrls=True), _role("supabase_auth_admin", rolbypassrls=True), _role("extra")],
])
def test_clone_normalization_rejects_unapproved_role_drift(target):
    source = [_role("pgbouncer"), _role("supabase_auth_admin")]
    with pytest.raises(C01SnapshotError):
        plan_clone_role_normalizations(source, target, [], [])


def test_clone_normalization_rejects_membership_drift():
    roles = [_role("pgbouncer")]
    with pytest.raises(C01SnapshotError, match="c01_clone_role_membership_mismatch"):
        plan_clone_role_normalizations(roles, roles, [], [{
            "granted_role": "supabase_admin", "member_role": "pgbouncer", "grantor_role": "postgres",
            "admin_option": True, "inherit_option": True, "set_option": True,
        }])


def _source_extensions():
    return [
        {"extname": "pg_stat_statements", "extversion": "1.11", "schema_name": "extensions", "owner": "postgres"},
        {"extname": "pgcrypto", "extversion": "1.3", "schema_name": "extensions", "owner": "postgres"},
        {"extname": "plpgsql", "extversion": "1.0", "schema_name": "pg_catalog", "owner": "postgres"},
        {"extname": "supabase_vault", "extversion": "0.3.1", "schema_name": "vault", "owner": "supabase_admin"},
        {"extname": "uuid-ossp", "extversion": "1.1", "schema_name": "extensions", "owner": "postgres"},
        {"extname": "vector", "extversion": "0.8.2", "schema_name": "extensions", "owner": "postgres"},
    ]


def test_extension_normalization_accepts_only_documented_pg_graphql_vector_delta():
    source = _source_extensions()
    target = [row for row in source if row["extname"] != "vector"] + [
        {"extname": "pg_graphql", "extversion": "1.5.11", "schema_name": "graphql", "owner": "postgres"},
    ]
    available = [{"name": "vector", "version": "0.8.2"}]
    assert plan_clone_extension_normalizations(source, target, available) == (
        "recreate-postgres-from-template0", "discard-image-only-pg_graphql",
        "restore-source-vector-0.8.2",
    )
    with pytest.raises(C01SnapshotError, match="c01_clone_extension_inventory_mismatch"):
        plan_clone_extension_normalizations(source, target + [{"extname": "evil"}], available)
    with pytest.raises(C01SnapshotError, match="c01_clone_extension_version_unavailable"):
        plan_clone_extension_normalizations(source, target, [])


class _FakeRestoreTarget:
    def __init__(self, source, *, fail_restore=False, fail_snapshot=False):
        self.source = source
        self.fail_restore = fail_restore
        self.fail_snapshot = fail_snapshot
        self.roles = source["roles"]
        self.memberships = source["memberships"]
        self.extensions = [row for row in source["extensions"] if row["extname"] != "vector"] + [
            {"extname": "pg_graphql", "extversion": "1.5.11", "schema_name": "graphql", "owner": "postgres"},
        ]
        self.sql = []
        self.calls = []
        self.partial = False

    def inspect(self):
        return {"container": _valid_inspect(), "network": _valid_network()}

    def role_inventory(self):
        return self.roles, self.memberships

    def extension_inventory(self):
        return self.extensions

    def available_extension_versions(self):
        return [{"name": "vector", "version": "0.8.2"}]

    def server_version(self):
        return "17.6.1.171"

    def apply_local_sql(self, sql):
        self.calls.append(("sql", sql))
        self.sql.append(sql)

    def drop_database(self, name):
        assert name == "postgres"
        self.calls.append(("drop", name))

    def restore_archive(self, archive):
        self.calls.append(("restore", len(archive)))
        if self.fail_restore:
            raise RuntimeError("sensitive database diagnostic")

    def capture_snapshot(self):
        self.calls.append(("capture",))
        if self.fail_snapshot:
            raise RuntimeError("private data diagnostic")
        return self.source

    def quarantine(self, reason_code):
        self.partial = True
        self.calls.append(("quarantine", reason_code))


def _persist_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(_MODULE, "_assert_private_file", lambda path: None)
    source = _snapshot()
    source["roles"] = [_role("pgbouncer"), _role("supabase_auth_admin")]
    source["extensions"] = _source_extensions()
    source["memberships"] = []
    artifact, manifest = persist_encrypted_backup(
        tmp_path, "c01-staging-20261113T120000Z-a1b2c3d4e5f6.enc",
        b"synthetic pg custom archive", b"-- roles only\n", source, _KEY,
    )
    return artifact, manifest, source


def test_restore_executes_only_local_normalization_then_compares_exact_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(_MODULE, "_enforce_private_directory", lambda path: None)
    artifact, manifest, source = _persist_fixture(tmp_path, monkeypatch)
    target = _FakeRestoreTarget(source)
    receipt = restore_encrypted_backup(artifact, manifest, _KEY, target,
                                       expected_image_id="sha256:" + "a" * 64)
    assert receipt.quarantined is True
    assert receipt.source_catalog_sha256 == receipt.restored_catalog_sha256
    assert target.calls[-2:] == [("restore", len(b"synthetic pg custom archive")), ("capture",)]


@pytest.mark.parametrize("failure", ["restore", "snapshot"])
def test_partial_restore_is_quarantined_and_never_reported_as_success(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(_MODULE, "_enforce_private_directory", lambda path: None)
    artifact, manifest, source = _persist_fixture(tmp_path, monkeypatch)
    target = _FakeRestoreTarget(source, fail_restore=failure == "restore",
                                fail_snapshot=failure == "snapshot")
    with pytest.raises(C01SnapshotError, match="c01_restore_partial_quarantined"):
        restore_encrypted_backup(artifact, manifest, _KEY, target,
                                 expected_image_id="sha256:" + "a" * 64)
    assert target.partial is True
    assert target.calls[-1] == ("quarantine", "c01_restore_partial_quarantined")


def test_restore_rejects_insecure_target_before_any_local_sql_or_drop(tmp_path, monkeypatch):
    monkeypatch.setattr(_MODULE, "_enforce_private_directory", lambda path: None)
    artifact, manifest, source = _persist_fixture(tmp_path, monkeypatch)
    target = _FakeRestoreTarget(source)
    target.inspect = lambda: {"container": {**_valid_inspect(), "HostConfig": {"NetworkMode": "bridge"}},
                              "network": _valid_network()}
    with pytest.raises(C01SnapshotError, match="c01_restore_target_not_isolated"):
        restore_encrypted_backup(artifact, manifest, _KEY, target,
                                 expected_image_id="sha256:" + "a" * 64)
    assert target.calls == []


def test_local_restore_env_is_loopback_only_and_bound_to_random_port():
    text = "\n".join([
        "PGHOST=127.0.0.1", "PGPORT=55432", "PGDATABASE=postgres",
        "PGUSER=postgres", "PGPASSWORD=synthetic-secret", "PGSSLMODE=disable",
    ])
    assert parse_local_restore_env(text, expected_port=55432)["PGHOST"] == "127.0.0.1"
    with pytest.raises(C01SnapshotError, match="c01_local_restore_configuration_rejected"):
        parse_local_restore_env(text.replace("127.0.0.1", "0.0.0.0"), expected_port=55432)
    with pytest.raises(C01SnapshotError, match="c01_local_restore_configuration_rejected"):
        parse_local_restore_env(text, expected_port=55433)


def test_master_key_persists_only_as_mocked_dpapi_blob(tmp_path, monkeypatch):
    key_path = tmp_path / "key.dpapi"
    monkeypatch.setattr(_MODULE, "os", SimpleNamespace(name="nt", fsync=os.fsync))
    monkeypatch.setattr(_MODULE, "_enforce_private_directory", lambda path: None)
    monkeypatch.setattr(_MODULE, "_assert_private_file", lambda path: None)
    monkeypatch.setattr(_MODULE, "_dpapi_protect", lambda value: b"C01DPAPI1" + bytes(x ^ 0x5a for x in value))
    monkeypatch.setattr(_MODULE, "_dpapi_unprotect", lambda value: bytes(x ^ 0x5a for x in value[9:])
                        if value.startswith(b"C01DPAPI1") else (_ for _ in ()).throw(C01SnapshotError("bad")))
    first = load_or_create_master_key(key_path)
    assert len(first) == 32 and first not in key_path.read_bytes()
    assert key_path.read_bytes().startswith(b"C01DPAPI1")
    assert load_or_create_master_key(key_path) == first
