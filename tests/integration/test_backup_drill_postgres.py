"""Opt-in physical encrypted restore and erasure quarantine on disposable PG16/18.

The test only accepts the named C2.4 containers and creates random synthetic
roles/databases without modifying or deleting any existing database.
"""

import hmac
import json
import os
import secrets
import subprocess
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.backup import drill
from src.core.gdpr.restore_checkpoint import (
    RestoreBlocked,
    verify_restore_journal_replay,
)
from src.core.gdpr.restore_journal import JournalCheckpoint

_TARGETS = (
    ("melpis-c24-full-pg16", 55432, "postgres:16-alpine", "postgres", False),
    ("melpis-c24-full-pg18", 55433, "postgres:18-alpine", "postgres", False),
    ("melpis-c24-vector-pg16", 55434, "pgvector/pgvector:0.7.4-pg16", "test", True),
    ("melpis-c24-vector-pg18", 55435, "pgvector/pgvector:0.8.1-pg18", "test", True),
)


def _docker(*args, input_bytes=None):
    try:
        result = subprocess.run(["docker", *args], input=input_bytes, capture_output=True,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pytest.fail("dedicated synthetic PostgreSQL command unavailable", pytrace=False)
    if result.returncode:
        pytest.fail("dedicated synthetic PostgreSQL command failed", pytrace=False)
    return result.stdout


def _assert_dedicated(container, port, image):
    info = _docker("inspect", "--format", "{{.Name}} {{.Config.Image}}", container).decode("ascii").strip()
    mapping = _docker("port", container, "5432/tcp").decode("ascii").strip()
    if info != f"/{container} {image}" or mapping != f"127.0.0.1:{port}":
        pytest.fail("unexpected PostgreSQL container identity or binding", pytrace=False)


async def _connect(url):
    try:
        return await asyncpg.connect(url, timeout=15)
    except Exception:  # noqa: BLE001 - Do not display connection parameters in test output.
        pytest.fail("synthetic database connection failed", pytrace=False)


def _provision(container, admin, with_extensions):
    suffix = uuid4().hex
    role = "melpis_drill_" + suffix[:16]
    source = "melpis_backup_source_" + suffix[:16]
    target = "melpis_restore_drill_" + suffix[:16]
    password = secrets.token_hex(32)
    sql = (f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB "
           f"NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n"
           f"CREATE DATABASE {source} OWNER {role} TEMPLATE template0;\n"
           f"CREATE DATABASE {target} OWNER {role} TEMPLATE template0;\n"
           f"REVOKE ALL ON DATABASE {source} FROM PUBLIC;\n"
           f"REVOKE ALL ON DATABASE {target} FROM PUBLIC;\n")
    _docker("exec", "-i", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
            "-q", "-U", admin, "-d", "postgres", input_bytes=sql.encode())
    if with_extensions:
        for database in (source, target):
            _docker("exec", "-i", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
                    "-q", "-U", admin, "-d", database,
                    input_bytes=b"CREATE EXTENSION pgcrypto; CREATE EXTENSION vector; CREATE EXTENSION btree_gist;")
    return role, password, source, target


def _cleanup(container, admin, role, source, target):
    # These identifiers are generated locally from UUID hex strings above.
    # Remove only the disposable role/databases created by this test.
    sql = f'''\
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname IN ('{source}', '{target}') AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "{source}";
DROP DATABASE IF EXISTS "{target}";
DROP ROLE IF EXISTS "{role}";
'''
    _docker("exec", "-i", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
            "-q", "-U", admin, "-d", "postgres", input_bytes=sql.encode())


def _url(role, password, port, database):
    return f"postgresql://{role}:{password}@127.0.0.1:{port}/{database}"


def _docker_spawn(container):
    original = drill._spawn

    async def spawn(*args, **kwargs):
        environment = dict(kwargs.pop("env"))
        environment.update({"PGHOST": "127.0.0.1", "PGPORT": "5432",
                            "PGPASSFILE": "/dev/null", "PGSERVICEFILE": "/dev/null"})
        for name in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "DOCKER_CONFIG", "DOCKER_HOST"):
            if name in os.environ:
                environment[name] = os.environ[name]
        names = ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGPASSFILE",
                 "PGSERVICEFILE", "PGAPPNAME", "PGCLIENTENCODING", "PGSSLMODE")
        forwarded = [item for name in names if name in environment for item in ("--env", name)]
        return await original("docker", "exec", "-i", *forwarded, container, *args,
                              env=environment, **kwargs)

    return spawn


async def _source_schema(conn, org, person, conversation, message, booking, request):
    await conn.execute("""
        CREATE TABLE organizations(id uuid PRIMARY KEY, name text);
        CREATE TABLE user_profiles(id uuid PRIMARY KEY);
        CREATE TABLE contacts(id uuid PRIMARY KEY, organization_id uuid);
        CREATE TABLE conversations(id uuid, organization_id uuid, contact_id uuid);
        CREATE TABLE messages(id uuid, organization_id uuid, conversation_id uuid,
                              deleted_at timestamptz, ai_reply_cache jsonb, content_text text);
        CREATE TABLE booking_conversation_states(organization_id uuid, subject_id uuid);
        CREATE TABLE booking_conversation_requests(organization_id uuid, conversation_id uuid,
                                                   original_subject_id uuid);
        CREATE TABLE simulation_requests(organization_id uuid, request_id uuid,
                                         c24_subject_id uuid, response jsonb);
        CREATE TABLE gdpr_erasure_barriers(organization_id uuid, operation_id uuid, scope text,
                                           subject_id uuid, identifier_digest text, key_id text);
        CREATE TABLE gdpr_operation_steps(organization_id uuid, operation_id uuid, name text, status text);
        CREATE TABLE booking_stays(organization_id uuid, booking_id uuid, original_subject_id uuid);
        CREATE TABLE booking_allocation_requests(organization_id uuid, booking_id uuid);
        CREATE TABLE booking_allocation_sets(organization_id uuid, booking_id uuid);
        CREATE TABLE booking_resource_allocations(organization_id uuid, booking_id uuid);
        CREATE TABLE booking_stay_completion_evidence(organization_id uuid, booking_id uuid);
        CREATE TABLE booking_stay_purge_fences(organization_id uuid, booking_id uuid,
            original_subject_id uuid, purpose text, operation_id uuid, policy_version text);
        CREATE TABLE booking_stay_tombstones(organization_id uuid, booking_id uuid, request_id uuid,
            original_subject_id uuid, purpose text, operation_id uuid, policy_version text);
    """)
    await conn.execute("INSERT INTO organizations VALUES($1,'synthetic backup subject')", org)
    await conn.execute("INSERT INTO user_profiles VALUES($1)", uuid4())
    await conn.execute("INSERT INTO contacts VALUES($1,$2)", person, org)
    await conn.execute("INSERT INTO conversations VALUES($1,$2,$3)", conversation, org, person)
    await conn.execute("INSERT INTO messages VALUES($1,$2,$3,NULL,'{}','synthetic private content')",
                       message, org, conversation)
    await conn.execute("INSERT INTO booking_conversation_states VALUES($1,$2)", org, person)
    await conn.execute("INSERT INTO booking_conversation_requests VALUES($1,$2,$3)", org, conversation, person)
    await conn.execute("INSERT INTO simulation_requests VALUES($1,$2,$3,'{\"booking_state_proposal\":{}}')",
                       org, request, person)
    await conn.execute("INSERT INTO booking_stays VALUES($1,$2,$3)", org, booking, person)
    for table in ("booking_allocation_requests", "booking_allocation_sets", "booking_resource_allocations",
                  "booking_stay_completion_evidence"):
        await conn.execute(f"INSERT INTO {table} VALUES($1,$2)", org, booking)


def _evidence(org, person, operation, booking, request):
    signer, installation = Ed25519PrivateKey.generate(), uuid4()
    anchor = JournalCheckpoint(0, "0" * 64)
    digest, key_id = secrets.token_hex(32), secrets.token_hex(8)
    record = {"sequence": 1, "organization_id": str(org), "operation_id": str(operation),
              "scope": "contact", "subject_id": str(person),
              "identifier_digest": digest, "key_id": key_id}
    raw = json.dumps({"version": 1, "installation_id": str(installation),
                      "previous_sha256": anchor.sha256, "entries": [record]}).encode()
    signature = signer.sign(b"melpis.restore.erasure-journal.v1\0" + raw)
    key = secrets.token_bytes(32)
    payload = {"version": "c24-restore-v2", "organization_id": str(org), "backup_id": "synthetic-backup",
               "sequence": 1, "complete": True,
               "erasures": [{"operation_id": str(operation), "scope": "contact", "subject_id": str(person)}],
               "stay_purges": [{"booking_id": str(booking), "subject_id": str(person), "purpose": "erasure",
                                "operation_id": str(operation), "policy_version": "synthetic-policy",
                                "request_ids": [str(request)]}]}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    checkpoint = {"payload": payload, "signature": hmac.new(key, canonical, sha256).hexdigest()}
    trust = {"installation_id": installation, "trusted_public_key": signer.public_key(),
             "backup_checkpoint": anchor, "independent_tip": JournalCheckpoint(1, sha256(raw).hexdigest()),
             "stay_checkpoint": checkpoint, "checkpoint_verification_key": key,
             "backup_id": "synthetic-backup", "minimum_sequence": 1}
    return raw, signature, trust, digest, key_id


@pytest.mark.asyncio
@pytest.mark.parametrize("container,port,image,admin,with_extensions", _TARGETS)
async def test_encrypted_restore_stays_quarantined_until_signed_erasure_and_stay_replay(
    container, port, image, admin, with_extensions, tmp_path, monkeypatch,
):
    if os.getenv("MELPIS_C24_BACKUP_INTEGRATION") != "1":
        pytest.skip("dedicated synthetic PostgreSQL containers are opt-in")
    _assert_dedicated(container, port, image)
    role, password, source_db, target_db = _provision(container, admin, with_extensions)
    source_url, target_url = _url(role, password, port, source_db), _url(role, password, port, target_db)
    org, person, conversation, message, booking, request, operation = (uuid4() for _ in range(7))
    try:
        source = await _connect(source_url)
        try:
            await _source_schema(source, org, person, conversation, message, booking, request)
        finally:
            await source.close()

        monkeypatch.setenv("BACKUP_DRILL_ENABLED", "true")
        monkeypatch.setattr(drill, "_spawn", _docker_spawn(container))
        result = await drill.run_backup_restore_drill(source_url, target_url, str(tmp_path),
                                                      encryption_key=secrets.token_bytes(32))
        assert result.ok and result.traffic_authorized is False
        assert result.erasure_reconciliation == "required"
        assert b"synthetic private content" not in Path(result.backup_path).read_bytes()

        raw, signature, trust, digest, key_id = _evidence(org, person, operation, booking, request)
        restored = await _connect(target_url)
        try:
            async def verify(sig=signature):
                async with restored.transaction():
                    return await verify_restore_journal_replay(restored, org, raw, sig, **trust)
    
            with pytest.raises(RestoreBlocked):
                await verify()
            await restored.execute("INSERT INTO booking_stay_purge_fences VALUES($1,$2,$3,'erasure',$4,'synthetic-policy')",
                                   org, booking, person, operation)
            await restored.execute("INSERT INTO booking_stay_tombstones VALUES($1,$2,$3,$4,'erasure',$5,'synthetic-policy')",
                                   org, booking, request, person, operation)
            with pytest.raises(RestoreBlocked):
                await verify()  # Fence alone must not certify restored content.
            async with restored.transaction():
                for table in ("booking_stays", "booking_allocation_requests", "booking_allocation_sets",
                              "booking_resource_allocations", "booking_stay_completion_evidence"):
                    await restored.execute(f"DELETE FROM {table} WHERE organization_id=$1 AND booking_id=$2", org, booking)
                await restored.execute("DELETE FROM booking_conversation_states WHERE organization_id=$1 AND subject_id=$2", org, person)
                await restored.execute("DELETE FROM booking_conversation_requests WHERE organization_id=$1 AND original_subject_id=$2", org, person)
                await restored.execute("DELETE FROM simulation_requests WHERE organization_id=$1 AND c24_subject_id=$2", org, person)
                await restored.execute("UPDATE messages SET deleted_at=NOW(),ai_reply_cache=NULL,content_text=NULL WHERE organization_id=$1 AND id=$2", org, message)
                await restored.execute("INSERT INTO gdpr_erasure_barriers VALUES($1,$2,'contact',$3,$4,$5)", org, operation, person, digest, key_id)
                await restored.execute("INSERT INTO gdpr_operation_steps VALUES($1,$2,'local','confirmed')", org, operation)
            proof = await verify()
            assert proof["verified_erasures"] == 1
            assert proof["traffic_authorized"] is False and proof["external_copies_certified"] is False
            assert (await verify())["verified_erasures"] == 1
            with pytest.raises(RestoreBlocked):
                await verify(bytes([signature[0] ^ 1]) + signature[1:])
        finally:
            await restored.close()
    finally:
        _cleanup(container, admin, role, source_db, target_db)
