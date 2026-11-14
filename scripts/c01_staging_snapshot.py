"""C01-only encrypted snapshot and restore-fidelity gates for Supabase Staging.

This module is deliberately separate from the GDPR backup drill. It accepts a
single Staging Session Pooler identity, requires verified TLS, captures a
read-only PostgreSQL snapshot, and refuses to certify a restore when catalog
semantics differ. It has no Production mode and never applies migrations.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import ssl
import struct
import subprocess
import tarfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import asyncpg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_PROJECT_REF = "ucncxwnhcdrqonknkvau"
_POOLER_HOST = "aws-1-eu-central-1.pooler.supabase.com"
_DATABASE = "postgres"
_PORT = 5432
_CA_SHA256 = "700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7"
_PG_CLIENT_IMAGE = "postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3"
_MAGIC = b"MELPIS-C01-SNAPSHOT\x01"
_NONCE_BYTES = 12
_SALT_BYTES = 16
_MAX_BUNDLE_BYTES = 512 * 1024 * 1024
_TARGET_NAME = re.compile(r"melpis-c01-restore-[a-z0-9-]{8,64}\Z")
_ROLE_FLAG_NORMALIZATIONS = {
    "pgbouncer": {"rolbypassrls": (True, False)},
    "supabase_auth_admin": {"rolbypassrls": (True, False)},
}
_MISSING_ROLE_NORMALIZATIONS = {
    "supabase_realtime_admin": {
        "rolsuper": False, "rolinherit": False, "rolcreaterole": False,
        "rolcreatedb": False, "rolreplication": False, "rolbypassrls": False,
        "rolcanlogin": False, "rolconnlimit": -1, "valid_until": None,
        "rolconfig": None,
    },
}
_TARGET_IMAGE = "public.ecr.aws/supabase/postgres:17.6.1.171"
_SOURCE_PG_MAJOR_MINOR = (17, 6)
_ALLOWED_IMAGE_EXTENSION_EXTRA = {"pg_graphql": "graphql"}
_ALLOWED_IMAGE_EXTENSION_MISSING = {"vector": ("0.8.2", "extensions")}


@dataclass(frozen=True, slots=True)
class CloneNormalizationPlan:
    role_sql: tuple[str, ...]
    extension_actions: tuple[str, ...]
    source_role_fingerprint: str
    source_extension_fingerprint: str


@dataclass(frozen=True, slots=True)
class RestoreReceipt:
    project_ref: str
    target_name: str
    source_catalog_sha256: str
    restored_catalog_sha256: str
    normalization_actions: tuple[str, ...]
    quarantined: bool


class RestoreAdapter:
    """Narrow interface for a verified, disposable local target."""

    def inspect(self) -> dict[str, Any]:
        raise NotImplementedError

    def role_inventory(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        raise NotImplementedError

    def extension_inventory(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def available_extension_versions(self) -> list[dict[str, str]]:
        raise NotImplementedError

    def apply_local_sql(self, sql: str) -> None:
        raise NotImplementedError

    def drop_database(self, name: str) -> None:
        raise NotImplementedError

    def restore_archive(self, archive: bytes) -> None:
        raise NotImplementedError

    def capture_snapshot(self) -> dict[str, Any]:
        raise NotImplementedError

    def server_version(self) -> str:
        raise NotImplementedError

    def quarantine(self, reason_code: str) -> None:
        """Keep target running, private and labelled for operator inspection."""
        raise NotImplementedError


class DockerRestoreAdapter(RestoreAdapter):
    """Concrete local restore driver; never connects to a remote database.

    `snapshot_reader` must be a reviewed local collector which returns the same
    catalog shape as `_fetch_catalog_snapshot`. The driver itself only uses
    docker inspect/exec against the validated container name.
    """

    def __init__(self, container_name: str, snapshot_reader: Callable[[], dict[str, Any]]):
        if not _TARGET_NAME.fullmatch(container_name) or not callable(snapshot_reader):
            raise C01SnapshotError("c01_restore_adapter_configuration_invalid")
        self.container_name = container_name
        self._snapshot_reader = snapshot_reader
        self._quarantined = False

    def _docker(self, args: list[str], *, input_bytes: bytes | None = None) -> bytes:
        try:
            proc = subprocess.run(
                ["docker", *args], input=input_bytes, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, check=False, timeout=1800,
            )
        except Exception:
            raise C01SnapshotError("c01_local_restore_command_failed") from None
        if proc.returncode:
            raise C01SnapshotError("c01_local_restore_command_failed")
        return proc.stdout

    def _psql(self, database: str, sql: str) -> bytes:
        if database not in {"postgres", "template1"}:
            raise C01SnapshotError("c01_restore_database_name_rejected")
        return self._docker(["exec", self.container_name, "psql", "-X", "-q", "-A", "-t",
                             "-v", "ON_ERROR_STOP=1", "--username=supabase_admin",
                             f"--dbname={database}", "-c", sql])

    def inspect(self) -> dict[str, Any]:
        payload = self._docker(["inspect", self.container_name])
        try:
            values = json.loads(payload)
            if not isinstance(values, list) or len(values) != 1:
                raise ValueError
            container = values[0]
            networks = container["NetworkSettings"]["Networks"]
            if not isinstance(networks, dict) or len(networks) != 1:
                raise ValueError
            network_name = next(iter(networks))
            network_data = json.loads(self._docker(["network", "inspect", network_name]))
            if not isinstance(network_data, list) or len(network_data) != 1:
                raise ValueError
            return {"container": container, "network": network_data[0]}
        except (ValueError, TypeError, json.JSONDecodeError):
            raise C01SnapshotError("c01_restore_target_inspect_failed") from None

    def role_inventory(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        sql = """SELECT jsonb_build_object(
          'roles',(SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.rolname),'[]'::jsonb) FROM (
            SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolreplication,
                   rolbypassrls,rolcanlogin,rolconnlimit,rolvaliduntil::text AS valid_until,rolconfig
              FROM pg_roles) r),
          'memberships',(SELECT COALESCE(jsonb_agg(to_jsonb(m) ORDER BY granted_role,member_role,grantor_role),'[]'::jsonb) FROM (
            SELECT g.rolname AS granted_role,mb.rolname AS member_role,gr.rolname AS grantor_role,
                   a.admin_option,a.inherit_option,a.set_option
              FROM pg_auth_members a JOIN pg_roles g ON g.oid=a.roleid
              JOIN pg_roles mb ON mb.oid=a.member JOIN pg_roles gr ON gr.oid=a.grantor) m))::text"""
        try:
            value = json.loads(self._psql("postgres", sql))
            if set(value) != {"roles", "memberships"}:
                raise ValueError
            return value["roles"], value["memberships"]
        except (ValueError, TypeError, json.JSONDecodeError):
            raise C01SnapshotError("c01_clone_role_inventory_failed") from None

    def extension_inventory(self) -> list[dict[str, Any]]:
        sql = """SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY extname),'[]'::jsonb)::text FROM (
          SELECT e.extname,e.extversion,n.nspname AS schema_name,pg_get_userbyid(e.extowner) AS owner
            FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace) x"""
        try:
            value = json.loads(self._psql("postgres", sql))
            if not isinstance(value, list):
                raise ValueError
            return value
        except (ValueError, TypeError, json.JSONDecodeError):
            raise C01SnapshotError("c01_clone_extension_inventory_failed") from None

    def available_extension_versions(self) -> list[dict[str, str]]:
        sql = """SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY name,version),'[]'::jsonb)::text FROM (
          SELECT name,version FROM pg_available_extension_versions) x"""
        try:
            value = json.loads(self._psql("postgres", sql))
            if not isinstance(value, list):
                raise ValueError
            return value
        except (ValueError, TypeError, json.JSONDecodeError):
            raise C01SnapshotError("c01_clone_extension_inventory_failed") from None

    def server_version(self) -> str:
        return self._psql("postgres", "SELECT current_setting('server_version')").decode().strip()

    def apply_local_sql(self, sql: str) -> None:
        if not _is_allowed_clone_role_sql(sql):
            raise C01SnapshotError("c01_clone_role_statement_rejected")
        self._psql("postgres", sql)

    def drop_database(self, name: str) -> None:
        if name != "postgres":
            raise C01SnapshotError("c01_restore_database_name_rejected")
        self._psql("template1", 'DROP DATABASE "postgres" WITH (FORCE)')

    def restore_archive(self, archive: bytes) -> None:
        if not archive or len(archive) > _MAX_BUNDLE_BYTES:
            raise C01SnapshotError("c01_database_dump_invalid")
        self._docker(["exec", "-i", self.container_name, "pg_restore", "--create",
                      "--exit-on-error", "--no-password", "--username=supabase_admin",
                      "--dbname=template1"], input_bytes=archive)

    def capture_snapshot(self) -> dict[str, Any]:
        try:
            return self._snapshot_reader()
        except C01SnapshotError:
            raise
        except Exception:
            raise C01SnapshotError("c01_restore_snapshot_capture_failed") from None

    def preflight(self) -> None:
        # Validate protected local credentials/connectivity while target is still intact.
        self._snapshot_reader()

    def quarantine(self, reason_code: str) -> None:
        # Isolation (network=none, no ports/binds, tmpfs) is immutable here.
        # Keep the container alive; changing/deleting it could destroy evidence.
        if reason_code != "c01_restore_partial_quarantined":
            raise C01SnapshotError("c01_restore_quarantine_reason_rejected")
        self._quarantined = True


def _parse_local_restore_env(text: str, *, expected_port: int) -> dict[str, str]:
    allowed = {"PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE"}
    values: dict[str, str] = {}
    try:
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or key not in allowed or key in values or "\x00" in value:
                raise ValueError
            values[key] = value
        if set(values) != allowed:
            raise ValueError
        if (values["PGHOST"] != "127.0.0.1" or int(values["PGPORT"]) != expected_port
                or values["PGDATABASE"] != "postgres" or values["PGUSER"] != "postgres"
                or values["PGSSLMODE"] != "disable" or not values["PGPASSWORD"]):
            raise ValueError
        return values
    except (TypeError, ValueError):
        raise C01SnapshotError("c01_local_restore_configuration_rejected") from None


def local_pg17_snapshot_reader(adapter: DockerRestoreAdapter, env_file: str | Path) -> Callable[[], dict[str, Any]]:
    """Build a loopback-only host collector for the validated internal clone."""
    env_path = Path(env_file).resolve()

    def capture() -> dict[str, Any]:
        inspected = adapter.inspect()
        container, network = inspected["container"], inspected["network"]
        if not network.get("Internal") or len(network.get("Containers", {})) != 1:
            raise C01SnapshotError("c01_restore_target_not_isolated")
        bindings = container["HostConfig"].get("PortBindings", {})
        try:
            binding = bindings["5432/tcp"][0]
            port = int(binding["HostPort"])
            if binding["HostIp"] != "127.0.0.1":
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            raise C01SnapshotError("c01_restore_loopback_binding_invalid") from None
        _assert_private_file(env_path)
        try:
            settings = _parse_local_restore_env(env_path.read_text(encoding="utf-8"), expected_port=port)
        except OSError:
            raise C01SnapshotError("c01_local_restore_configuration_rejected") from None

        async def fetch() -> dict[str, Any]:
            connection = None
            try:
                connection = await asyncpg.connect(
                    host=settings["PGHOST"], port=port, database=settings["PGDATABASE"],
                    user=settings["PGUSER"], password=settings["PGPASSWORD"], ssl=False,
                    timeout=10, command_timeout=180,
                    server_settings={"application_name": "melpis-c01-local-restore-check"},
                )
                version = await connection.fetchval("SELECT current_setting('server_version')")
                if not re.fullmatch(r"17\.6\.1\.171(?:\s.*)?", version):
                    raise C01SnapshotError("c01_restore_postgres_version_mismatch")
                return await _fetch_catalog_snapshot(connection)
            except C01SnapshotError:
                raise
            except Exception:
                raise C01SnapshotError("c01_restore_snapshot_capture_failed") from None
            finally:
                if connection is not None:
                    await connection.close()

        return asyncio.run(fetch())

    return capture
_MISSING_MEMBERSHIP_NORMALIZATIONS = {
    ("anon", "supabase_realtime_admin", "supabase_admin", False, False, True),
    ("authenticated", "supabase_realtime_admin", "supabase_admin", False, False, True),
    ("service_role", "supabase_realtime_admin", "supabase_admin", False, False, True),
}


class C01SnapshotError(RuntimeError):
    """Non-sensitive, stable error for C01 backup/restore gates."""


@dataclass(frozen=True, slots=True)
class SourceSettings:
    project_ref: str
    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str
    ca_container_path: str
    application_name: str


def parse_source_env(text: str, *, expected_ref: str = _PROJECT_REF) -> SourceSettings:
    """Parse and pin the source configuration without exposing credential values."""
    values: dict[str, str] = {}
    allowed = {
        "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
        "PGSSLMODE", "PGSSLROOTCERT", "PGAPPNAME",
    }
    try:
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or key not in allowed or key in values or "\x00" in value:
                raise ValueError
            values[key] = value
        if set(values) != allowed:
            raise ValueError
        if expected_ref != _PROJECT_REF:
            raise ValueError
        user = f"postgres.{expected_ref}"
        port = int(values["PGPORT"])
        if (
            values["PGHOST"] != _POOLER_HOST
            or port != _PORT
            or values["PGDATABASE"] != _DATABASE
            or values["PGUSER"] != user
            or values["PGSSLMODE"] != "verify-full"
            or values["PGSSLROOTCERT"] != "/certs/prod-ca-2021.crt"
            or not values["PGPASSWORD"]
            or values["PGAPPNAME"] != "melpis-c01-preflight"
        ):
            raise ValueError
        return SourceSettings(
            expected_ref, values["PGHOST"], port, values["PGDATABASE"], user,
            values["PGPASSWORD"], values["PGSSLMODE"], values["PGSSLROOTCERT"],
            values["PGAPPNAME"],
        )
    except (TypeError, ValueError):
        raise C01SnapshotError("c01_source_configuration_rejected") from None


def source_dsn(settings: SourceSettings) -> str:
    """Build an internal DSN. Callers must not log or include it in diagnostics."""
    return (
        f"postgresql://{quote(settings.user, safe='')}:{quote(settings.password, safe='')}"
        f"@{settings.host}:{settings.port}/{settings.database}"
    )


def verified_ssl_context(ca_file: str | Path) -> ssl.SSLContext:
    path = Path(ca_file)
    if not path.is_file():
        raise C01SnapshotError("c01_ca_certificate_missing")
    try:
        if not hmac.compare_digest(hashlib.sha256(path.read_bytes()).hexdigest(), _CA_SHA256):
            raise C01SnapshotError("c01_ca_certificate_invalid")
        context = ssl.create_default_context(cafile=str(path.resolve()))
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context
    except (OSError, ssl.SSLError):
        raise C01SnapshotError("c01_ca_certificate_invalid") from None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _derive_key(master_key: bytes, salt: bytes, label: bytes) -> bytes:
    if type(master_key) is not bytes or len(master_key) != 32:
        raise C01SnapshotError("c01_encryption_key_invalid")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=label).derive(master_key)


def encrypt_bundle(bundle: bytes, master_key: bytes) -> bytes:
    if not isinstance(bundle, bytes) or not bundle or len(bundle) > _MAX_BUNDLE_BYTES:
        raise C01SnapshotError("c01_bundle_size_invalid")
    salt, nonce = secrets.token_bytes(_SALT_BYTES), secrets.token_bytes(_NONCE_BYTES)
    header = _MAGIC + salt + nonce
    ciphertext = AESGCM(_derive_key(master_key, salt, b"melpis.c01.snapshot.v1")).encrypt(
        nonce, bundle, header,
    )
    return header + struct.pack(">Q", len(ciphertext)) + ciphertext


def decrypt_bundle(artifact: bytes, master_key: bytes) -> bytes:
    header_len = len(_MAGIC) + _SALT_BYTES + _NONCE_BYTES
    if not isinstance(artifact, bytes) or len(artifact) < header_len + 8 + 16:
        raise C01SnapshotError("c01_artifact_invalid")
    if not artifact.startswith(_MAGIC):
        raise C01SnapshotError("c01_artifact_invalid")
    header = artifact[:header_len]
    salt = header[len(_MAGIC):len(_MAGIC) + _SALT_BYTES]
    nonce = header[-_NONCE_BYTES:]
    size = struct.unpack(">Q", artifact[header_len:header_len + 8])[0]
    ciphertext = artifact[header_len + 8:]
    if size != len(ciphertext) or size > _MAX_BUNDLE_BYTES + 16:
        raise C01SnapshotError("c01_artifact_invalid")
    try:
        return AESGCM(_derive_key(master_key, salt, b"melpis.c01.snapshot.v1")).decrypt(
            nonce, ciphertext, header,
        )
    except InvalidTag:
        raise C01SnapshotError("c01_artifact_authentication_failed") from None


def create_integrity_manifest(artifact: bytes, master_key: bytes, metadata: dict[str, Any]) -> bytes:
    public = {
        "format": "melpis-c01-aesgcm-v1",
        "project_ref": _PROJECT_REF,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact_bytes": len(artifact),
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
        "metadata_sha256": hashlib.sha256(_json_bytes(metadata)).hexdigest(),
    }
    key = _derive_key(master_key, b"melpis-c01-manifest-v1", b"melpis.c01.manifest.hmac.v1")
    public["hmac_sha256"] = hmac.new(key, _json_bytes(public), hashlib.sha256).hexdigest()
    return _json_bytes(public)


def verify_integrity_manifest(artifact: bytes, manifest_bytes: bytes, master_key: bytes) -> dict[str, Any]:
    try:
        public = json.loads(manifest_bytes)
        supplied = public.pop("hmac_sha256")
        if not isinstance(supplied, str) or public.get("format") != "melpis-c01-aesgcm-v1":
            raise ValueError
        if public.get("project_ref") != _PROJECT_REF:
            raise ValueError
        if public.get("artifact_bytes") != len(artifact):
            raise ValueError
        if not hmac.compare_digest(public.get("artifact_sha256", ""), hashlib.sha256(artifact).hexdigest()):
            raise ValueError
        key = _derive_key(master_key, b"melpis-c01-manifest-v1", b"melpis.c01.manifest.hmac.v1")
        expected = hmac.new(key, _json_bytes(public), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError
        return public
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        raise C01SnapshotError("c01_manifest_invalid") from None


def build_bundle(database_dump: bytes, roles_sql: bytes, snapshot: dict[str, Any]) -> bytes:
    """Create a tar bundle in memory only; no plaintext temporary files."""
    if not database_dump or not roles_sql or not isinstance(snapshot, dict):
        raise C01SnapshotError("c01_bundle_incomplete")
    raw = BytesIO()
    try:
        with tarfile.open(fileobj=raw, mode="w:gz") as archive:
            for name, payload in (
                ("database.dump", database_dump),
                ("roles.sql", roles_sql),
                ("snapshot.json", _json_bytes(snapshot)),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o600
                info.mtime = 0
                archive.addfile(info, BytesIO(payload))
        value = raw.getvalue()
    except (OSError, tarfile.TarError):
        raise C01SnapshotError("c01_bundle_failed") from None
    if not value or len(value) > _MAX_BUNDLE_BYTES:
        raise C01SnapshotError("c01_bundle_size_invalid")
    return value


def open_bundle(bundle: bytes) -> dict[str, bytes]:
    expected = {"database.dump", "roles.sql", "snapshot.json"}
    try:
        with tarfile.open(fileobj=BytesIO(bundle), mode="r:gz") as archive:
            members = archive.getmembers()
            if {item.name for item in members} != expected or any(not item.isfile() for item in members):
                raise ValueError
            if sum(item.size for item in members) > _MAX_BUNDLE_BYTES:
                raise ValueError
            contents = {item.name: archive.extractfile(item).read() for item in members}
            json.loads(contents["snapshot.json"])
            return contents
    except (OSError, EOFError, tarfile.TarError, ValueError, AttributeError, json.JSONDecodeError):
        raise C01SnapshotError("c01_bundle_invalid") from None


def validate_restore_container(
    inspect_payload: dict[str, Any], network_payload: dict[str, Any], *, expected_image_id: str,
    normalization_plan: CloneNormalizationPlan,
) -> None:
    """Fail closed unless target is a private, disposable Supabase PG17 container."""
    try:
        state = inspect_payload["State"]
        host = inspect_payload["HostConfig"]
        config = inspect_payload["Config"]
        mounts = inspect_payload["Mounts"]
        name = inspect_payload["Name"].lstrip("/")
        container_id = inspect_payload["Id"]
        networks = inspect_payload["NetworkSettings"]["Networks"]
        if not isinstance(networks, dict) or len(networks) != 1:
            raise ValueError
        network_name, endpoint = next(iter(networks.items()))
        ports = host.get("PortBindings")
        pg_binding = ports.get("5432/tcp") if isinstance(ports, dict) else None
        observed_ports = inspect_payload["NetworkSettings"].get("Ports")
        net_containers = network_payload.get("Containers")
        safe_mount = (
            len(mounts) == 1 and mounts[0]["Type"] == "tmpfs"
            and mounts[0]["Destination"] == "/var/lib/postgresql/data"
        )
        if (
            not _TARGET_NAME.fullmatch(name)
            or not state.get("Running")
            or len(networks) != 1
            or network_name != f"{name}-net"
            or host.get("NetworkMode") != network_name
            or network_payload.get("Name") != network_name
            or network_payload.get("Internal") is not True
            or not isinstance(net_containers, dict) or set(net_containers) != {container_id}
            or endpoint.get("NetworkID") != network_payload.get("Id")
            or not isinstance(pg_binding, list) or len(pg_binding) != 1
            or pg_binding[0].get("HostIp") != "127.0.0.1"
            or not re.fullmatch(r"[1-9][0-9]{3,4}", str(pg_binding[0].get("HostPort", "")))
            or set(ports) != {"5432/tcp"}
            or observed_ports != ports
            or host.get("PublishAllPorts") is True
            or host.get("Binds") not in ([], None)
            or not safe_mount
            or config.get("Image") != _TARGET_IMAGE
            or inspect_payload.get("Image") != expected_image_id
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_image_id)
            or not isinstance(normalization_plan, CloneNormalizationPlan)
            or not re.fullmatch(r"[0-9a-f]{64}", normalization_plan.source_role_fingerprint)
            or not re.fullmatch(r"[0-9a-f]{64}", normalization_plan.source_extension_fingerprint)
            or normalization_plan.extension_actions != (
                "recreate-postgres-from-template0", "discard-image-only-pg_graphql",
                "restore-source-vector-0.8.2",
            )
            or any(not _is_allowed_clone_role_sql(sql) for sql in normalization_plan.role_sql)
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise C01SnapshotError("c01_restore_target_not_isolated") from None


async def _fetch_catalog_snapshot(conn: asyncpg.Connection) -> dict[str, Any]:
    """Read schema/security metadata and row counts from a single MVCC snapshot."""
    extensions = await conn.fetch("""
        SELECT e.extname, e.extversion, n.nspname AS schema_name,
               pg_get_userbyid(e.extowner) AS owner
          FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
         ORDER BY e.extname
    """)
    pgcrypto = next((row["schema_name"] for row in extensions if row["extname"] == "pgcrypto"), None)
    if not pgcrypto:
        raise C01SnapshotError("c01_data_fingerprint_unavailable")
    roles = await conn.fetch("""
        SELECT r.rolname, r.rolsuper, r.rolinherit, r.rolcreaterole, r.rolcreatedb,
               r.rolreplication, r.rolbypassrls, r.rolcanlogin, r.rolconnlimit,
               r.rolvaliduntil::text AS valid_until, r.rolconfig
          FROM pg_roles r ORDER BY r.rolname
    """)
    memberships = await conn.fetch("""
        SELECT granted.rolname AS granted_role, member.rolname AS member_role,
               grantor.rolname AS grantor_role, m.admin_option,
               m.inherit_option, m.set_option
          FROM pg_auth_members m
          JOIN pg_roles granted ON granted.oid=m.roleid
          JOIN pg_roles member ON member.oid=m.member
          JOIN pg_roles grantor ON grantor.oid=m.grantor
         ORDER BY 1,2,3
    """)
    schemas = await conn.fetch("""
        SELECT n.nspname, pg_get_userbyid(n.nspowner) AS owner,
               COALESCE(n.nspacl, acldefault('n', n.nspowner))::text AS acl
          FROM pg_namespace n
         WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
         ORDER BY n.nspname
    """)
    default_privileges = await conn.fetch("""
        SELECT pg_get_userbyid(d.defaclrole) AS owner, n.nspname AS schema_name,
               d.defaclobjtype::text AS object_type, d.defaclacl::text AS acl
          FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
         ORDER BY owner,schema_name,object_type
    """)
    relations = await conn.fetch("""
        SELECT n.nspname, c.relname, c.relkind::text AS kind,
               pg_get_userbyid(c.relowner) AS owner,
               COALESCE(c.relacl, acldefault((CASE WHEN c.relkind='S' THEN 's' ELSE 'r' END)::"char", c.relowner))::text AS acl,
               c.relrowsecurity, c.relforcerowsecurity,
               COALESCE(c.reloptions, ARRAY[]::text[]) AS options,
               CASE WHEN c.relkind IN ('r','p','m') THEN true ELSE false END AS count_rows,
               CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid, true) ELSE NULL END AS view_definition
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
           AND c.relkind IN ('r','p','v','m','S','f')
         ORDER BY n.nspname,c.relname
    """)
    policies = await conn.fetch("""
        SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
          FROM pg_policies ORDER BY schemaname,tablename,policyname
    """)
    column_privileges = await conn.fetch("""
        SELECT n.nspname, c.relname, a.attname, COALESCE(a.attacl, '{}'::aclitem[])::text AS acl
          FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE a.attnum>0 AND NOT a.attisdropped
           AND n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
           AND a.attacl IS NOT NULL
         ORDER BY n.nspname,c.relname,a.attnum
    """)
    columns = await conn.fetch("""
        SELECT n.nspname AS schema_name, c.relname AS relation_name, a.attname AS column_name,
               a.attnum AS ordinal_position, format_type(a.atttypid,a.atttypmod) AS data_type,
               a.attnotnull AS not_null, a.attidentity::text AS identity_kind,
               a.attgenerated::text AS generated_kind,
               CASE WHEN coll.oid IS NULL THEN NULL ELSE format('%I.%I',cn.nspname,coll.collname) END AS collation,
               pg_get_expr(d.adbin,d.adrelid) AS column_default
          FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace
          LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
          LEFT JOIN pg_collation coll ON coll.oid=a.attcollation AND a.attcollation<>0
          LEFT JOIN pg_namespace cn ON cn.oid=coll.collnamespace
         WHERE a.attnum>0 AND NOT a.attisdropped
           AND c.relkind IN ('r','p','v','m','f')
           AND n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
         ORDER BY n.nspname,c.relname,a.attnum
    """)
    sequence_ownership = await conn.fetch("""
        SELECT sn.nspname AS sequence_schema, seq.relname AS sequence_name,
               tn.nspname AS table_schema, tbl.relname AS table_name, att.attname AS column_name,
               d.deptype::text AS dependency_type
          FROM pg_depend d JOIN pg_class seq ON d.classid='pg_class'::regclass AND d.objid=seq.oid
          JOIN pg_namespace sn ON sn.oid=seq.relnamespace
          JOIN pg_class tbl ON d.refclassid='pg_class'::regclass AND d.refobjid=tbl.oid
          JOIN pg_namespace tn ON tn.oid=tbl.relnamespace
          JOIN pg_attribute att ON att.attrelid=tbl.oid AND att.attnum=d.refobjsubid
         WHERE seq.relkind='S' AND d.deptype IN ('a','i')
           AND sn.nspname <> 'information_schema' AND sn.nspname !~ '^pg_'
           AND tn.nspname <> 'information_schema' AND tn.nspname !~ '^pg_'
         ORDER BY sn.nspname,seq.relname,tn.nspname,tbl.relname,att.attname,d.deptype
    """)
    triggers = await conn.fetch("""
        SELECT n.nspname AS schema_name, c.relname AS table_name, t.tgname,
               t.tgenabled::text AS enabled, pg_get_triggerdef(t.oid, true) AS definition,
               p.proname AS function_name, pg_get_userbyid(p.proowner) AS function_owner,
               p.prosecdef AS security_definer, p.proconfig AS function_config
          FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
         WHERE NOT t.tgisinternal AND n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
         ORDER BY n.nspname,c.relname,t.tgname
    """)
    functions = await conn.fetch("""
        SELECT n.nspname AS schema_name, p.proname, p.prokind::text AS routine_kind,
               pg_get_function_identity_arguments(p.oid) AS args,
               pg_get_userbyid(p.proowner) AS owner, p.prosecdef AS security_definer,
               p.proconfig AS function_config,
               CASE WHEN p.prokind='a' THEN NULL ELSE pg_get_functiondef(p.oid) END AS definition,
               COALESCE(p.proacl, acldefault('f', p.proowner))::text AS acl,
               a.aggkind::text AS aggregate_kind, a.aggnumdirectargs AS aggregate_direct_arguments,
               a.aggtransfn::regprocedure::text AS aggregate_transition,
               a.aggfinalfn::regprocedure::text AS aggregate_final,
               a.aggcombinefn::regprocedure::text AS aggregate_combine,
               a.aggserialfn::regprocedure::text AS aggregate_serialize,
               a.aggdeserialfn::regprocedure::text AS aggregate_deserialize,
               a.aggmtransfn::regprocedure::text AS aggregate_moving_transition,
               a.aggminvtransfn::regprocedure::text AS aggregate_inverse_transition,
               a.aggmfinalfn::regprocedure::text AS aggregate_moving_final,
               a.aggtranstype::regtype::text AS aggregate_transition_type,
               a.aggtransspace AS aggregate_transition_space,
               a.agginitval AS aggregate_initial_value,
               a.aggmtransspace AS aggregate_moving_transition_space,
               a.aggminitval AS aggregate_moving_initial_value,
               a.aggfinalextra AS aggregate_final_extra,
               a.aggmfinalextra AS aggregate_moving_final_extra,
               a.aggfinalmodify::text AS aggregate_final_modify,
               a.aggmfinalmodify::text AS aggregate_moving_final_modify,
               a.aggsortop::regoperator::text AS aggregate_sort_operator
          FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          LEFT JOIN pg_aggregate a ON a.aggfnoid=p.oid
         WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
         ORDER BY n.nspname,p.proname,args
    """)
    extension_dependencies = await conn.fetch("""
        SELECT e.extname, pg_describe_object(d.classid,d.objid,d.objsubid) AS dependent_object,
               d.deptype::text AS dependency_type
          FROM pg_depend d JOIN pg_extension e ON d.refclassid='pg_extension'::regclass
                               AND d.refobjid=e.oid
         ORDER BY e.extname,dependent_object,dependency_type
    """)
    constraints = await conn.fetch("""
        SELECT n.nspname, c.relname, con.conname, con.contype::text AS kind,
               pg_get_constraintdef(con.oid, true) AS definition,
               con.convalidated AS validated, con.condeferrable AS deferrable,
               con.condeferred AS initially_deferred
          FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
         ORDER BY n.nspname,c.relname,con.conname
    """)
    indexes = await conn.fetch("""
        SELECT n.nspname,c.relname,i.relname AS index_name,pg_get_indexdef(i.oid) AS definition,
               x.indisvalid,x.indisready,x.indislive,x.indisunique,x.indisprimary
          FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid
          JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
         ORDER BY n.nspname,c.relname,i.relname
    """)
    types = await conn.fetch("""
        SELECT n.nspname, t.typname, t.typtype::text AS kind,
               pg_get_userbyid(t.typowner) AS owner,
               t.typnotnull, t.typdefault, t.typcategory::text AS category,
               CASE WHEN t.typtype='e' THEN (
                 SELECT array_agg(e.enumlabel ORDER BY e.enumsortorder)
                   FROM pg_enum e WHERE e.enumtypid=t.oid
               ) ELSE NULL END AS enum_labels
          FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
         WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
           AND t.typrelid=0 AND t.typisdefined
         ORDER BY n.nspname,t.typname
    """)
    database = await conn.fetchrow("""
        SELECT d.datname, pg_get_userbyid(d.datdba) AS owner,
               COALESCE(d.datacl, acldefault('d', d.datdba))::text AS acl
          FROM pg_database d WHERE d.datname=current_database()
    """)
    large_objects = await conn.fetchrow(f"""
        SELECT count(DISTINCT m.oid)::bigint AS object_count,
               count(p.loid)::bigint AS page_count,
               COALESCE(sum(octet_length(p.data)),0)::bigint AS byte_count,
               encode("{pgcrypto.replace(chr(34), chr(34)*2)}"."digest"(
                 convert_to(COALESCE(string_agg(
                   p.loid::text || ':' || p.pageno::text || ':' ||
                   encode("{pgcrypto.replace(chr(34), chr(34)*2)}"."digest"(p.data,'sha256'),'hex'),
                   E'\\n' ORDER BY p.loid,p.pageno), ''), 'UTF8'), 'sha256'), 'hex') AS sha256,
               (SELECT COALESCE(string_agg(
                         x.oid::text || ':' || pg_get_userbyid(x.lomowner) || ':' ||
                         COALESCE(x.lomacl, acldefault('L', x.lomowner))::text, E'\\n' ORDER BY x.oid), '')
                  FROM pg_largeobject_metadata x) AS owners_acl
          FROM pg_largeobject_metadata m
          LEFT JOIN pg_largeobject p ON p.loid=m.oid
    """)
    history_exists = await conn.fetchval("SELECT to_regclass('supabase_migrations.schema_migrations') IS NOT NULL")
    history = []
    if history_exists:
        history = await conn.fetch("SELECT version::text AS version,name FROM supabase_migrations.schema_migrations ORDER BY version")

    counts: list[dict[str, Any]] = []
    sequence_state: list[dict[str, Any]] = []
    for row in relations:
        if row["count_rows"]:
            identifier = f'"{row["nspname"].replace(chr(34), chr(34)*2)}"."{row["relname"].replace(chr(34), chr(34)*2)}"'
            digest_function = f'"{pgcrypto.replace(chr(34), chr(34)*2)}"."digest"'
            row_summary = await conn.fetchrow(
                f"SELECT count(*) AS row_count, encode({digest_function}("
                f"convert_to(COALESCE(string_agg(row_to_json(t)::text, E'\\n' "
                f"ORDER BY row_to_json(t)::text), ''), 'UTF8'), 'sha256'), 'hex') AS sha256 "
                f"FROM {identifier} AS t"
            )
            counts.append({"schema": row["nspname"], "name": row["relname"],
                           "count": row_summary["row_count"], "sha256": row_summary["sha256"]})
        if row["kind"] == "S":
            identifier = f'"{row["nspname"].replace(chr(34), chr(34)*2)}"."{row["relname"].replace(chr(34), chr(34)*2)}"'
            state = await conn.fetchrow(f"SELECT last_value::text AS last_value, is_called FROM {identifier}")
            definition = await conn.fetchrow("""
                SELECT s.seqstart::text AS start_value, s.seqincrement::text AS increment_by,
                       s.seqmin::text AS min_value, s.seqmax::text AS max_value,
                       s.seqcache::text AS cache_size, s.seqcycle AS cycle
                  FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname=$1 AND c.relname=$2
            """, row["nspname"], row["relname"])
            if state is None or definition is None:
                raise C01SnapshotError("c01_sequence_snapshot_unavailable")
            sequence_state.append({
                "schema": row["nspname"], "name": row["relname"],
                **dict(state), **dict(definition),
            })

    data = {
        "project_ref": _PROJECT_REF,
        "database": dict(database) if database else None,
        "extensions": [dict(row) for row in extensions],
        "extension_dependencies": [dict(row) for row in extension_dependencies],
        "roles": [dict(row) for row in roles],
        "memberships": [dict(row) for row in memberships],
        "schemas": [dict(row) for row in schemas],
        "default_privileges": [dict(row) for row in default_privileges],
        "relations": [{key: value for key, value in dict(row).items() if key != "count_rows"} for row in relations],
        "row_counts": counts,
        "sequence_state": sequence_state,
        "policies": [dict(row) for row in policies],
        "column_privileges": [dict(row) for row in column_privileges],
        "columns": [dict(row) for row in columns],
        "sequence_ownership": [dict(row) for row in sequence_ownership],
        "triggers": [dict(row) for row in triggers],
        "functions": [dict(row) for row in functions],
        "constraints": [dict(row) for row in constraints],
        "indexes": [dict(row) for row in indexes],
        "large_objects": dict(large_objects) if large_objects else None,
        "types": [dict(row) for row in types],
        "migration_history": [dict(row) for row in history],
    }
    return json.loads(_json_bytes(data))


def compare_snapshots(source: dict[str, Any], restored: dict[str, Any]) -> None:
    """Require exact security-relevant and data parity; no implicit normalization."""
    if source != restored:
        raise C01SnapshotError("c01_restore_catalog_or_data_mismatch")


def plan_clone_extension_normalizations(
    source_extensions: list[dict[str, Any]], target_extensions: list[dict[str, Any]],
    available_versions: list[dict[str, str]],
) -> tuple[str, ...]:
    """Accept only the observed PG17.6.1.171 image-vs-Staging extension delta.

    The database archive is restored with --create from template0. Consequently
    the image's initial pg_graphql installation is discarded with its default
    database, while source pg_dump recreates vector 0.8.2 in the target DB.
    This function only emits a plan; it does not execute SQL.
    """
    source = {row["extname"]: row for row in source_extensions}
    target = {row["extname"]: row for row in target_extensions}
    if len(source) != len(source_extensions) or len(target) != len(target_extensions):
        raise C01SnapshotError("c01_clone_extension_inventory_mismatch")
    source_names, target_names = set(source), set(target)
    missing = source_names - target_names
    extra = target_names - source_names
    if missing != set(_ALLOWED_IMAGE_EXTENSION_MISSING) or extra != set(_ALLOWED_IMAGE_EXTENSION_EXTRA):
        raise C01SnapshotError("c01_clone_extension_inventory_mismatch")
    for name in source_names & target_names:
        left, right = source[name], target[name]
        for field in ("extversion", "schema_name", "owner"):
            if left.get(field) != right.get(field):
                raise C01SnapshotError("c01_clone_extension_attribute_mismatch")
    for name, schema in _ALLOWED_IMAGE_EXTENSION_EXTRA.items():
        if target[name].get("schema_name") != schema:
            raise C01SnapshotError("c01_clone_extension_attribute_mismatch")
    available = {(row.get("name"), row.get("version")) for row in available_versions}
    for name, (version, _schema) in _ALLOWED_IMAGE_EXTENSION_MISSING.items():
        if source[name].get("extversion") != version or (name, version) not in available:
            raise C01SnapshotError("c01_clone_extension_version_unavailable")
    return (
        "recreate-postgres-from-template0",
        "discard-image-only-pg_graphql",
        "restore-source-vector-0.8.2",
    )


def plan_clone_normalizations(
    source_snapshot: dict[str, Any], target_roles: list[dict[str, Any]],
    target_memberships: list[dict[str, Any]], target_extensions: list[dict[str, Any]],
    available_versions: list[dict[str, str]],
) -> CloneNormalizationPlan:
    if source_snapshot.get("project_ref") != _PROJECT_REF:
        raise C01SnapshotError("c01_source_snapshot_wrong_project")
    role_sql = plan_clone_role_normalizations(
        source_snapshot.get("roles", []), target_roles,
        source_snapshot.get("memberships", []), target_memberships,
    )
    extension_actions = plan_clone_extension_normalizations(
        source_snapshot.get("extensions", []), target_extensions, available_versions,
    )
    return CloneNormalizationPlan(
        tuple(role_sql), extension_actions,
        hashlib.sha256(_json_bytes(source_snapshot.get("roles"))).hexdigest(),
        hashlib.sha256(_json_bytes(source_snapshot.get("extensions"))).hexdigest(),
    )


def plan_clone_role_normalizations(
    source_roles: list[dict[str, Any]], target_roles: list[dict[str, Any]],
    source_memberships: list[dict[str, Any]], target_memberships: list[dict[str, Any]],
) -> list[str]:
    """Return only the documented local image drift; reject every other difference.

    This function never runs SQL. A caller may apply the returned statements only
    inside a newly-created, isolated clone after recording the before/after state.
    """
    expected = {row["rolname"]: row for row in source_roles}
    actual = {row["rolname"]: row for row in target_roles}
    missing, extra = expected.keys() - actual.keys(), actual.keys() - expected.keys()
    if extra or missing - _MISSING_ROLE_NORMALIZATIONS.keys():
        raise C01SnapshotError("c01_clone_role_inventory_mismatch")
    sql: list[str] = []
    for name in sorted(missing):
        expected_row = expected[name]
        if any(expected_row.get(key) != value for key, value in _MISSING_ROLE_NORMALIZATIONS[name].items()):
            raise C01SnapshotError("c01_clone_role_attribute_mismatch")
        sql.append(
            f'CREATE ROLE "{name}" NOLOGIN NOBYPASSRLS NOINHERIT '
            "NOCREATEROLE NOCREATEDB NOREPLICATION"
        )
    attributes = set(next(iter(expected.values()))) - {"rolname"} if expected else set()
    for name in sorted(expected.keys() & actual.keys()):
        for flag in sorted(attributes):
            source_value = expected[name].get(flag)
            target_value = actual[name].get(flag)
            if source_value == target_value:
                continue
            allowed = _ROLE_FLAG_NORMALIZATIONS.get(name, {}).get(flag)
            if allowed != (target_value, source_value) or (name == "supabase_admin" and flag == "rolsuper"):
                raise C01SnapshotError("c01_clone_role_attribute_mismatch")
            if flag == "rolbypassrls" and source_value is False:
                sql.append(f'ALTER ROLE "{name}" NOBYPASSRLS')
            else:
                raise C01SnapshotError("c01_clone_role_attribute_mismatch")
    def membership_key(row):
        return (row["granted_role"], row["member_role"], row["grantor_role"],
                row["admin_option"], row["inherit_option"], row["set_option"])
    try:
        source_membership_set = {membership_key(row) for row in source_memberships}
        target_membership_set = {membership_key(row) for row in target_memberships}
    except (KeyError, TypeError):
        raise C01SnapshotError("c01_clone_role_membership_mismatch") from None
    missing_memberships = source_membership_set - target_membership_set
    if (target_membership_set - source_membership_set
            or missing_memberships - _MISSING_MEMBERSHIP_NORMALIZATIONS):
        raise C01SnapshotError("c01_clone_role_membership_mismatch")
    for granted, member, grantor, admin, inherit, set_option in sorted(missing_memberships):
        sql.append(
            f'GRANT "{granted}" TO "{member}" WITH ADMIN {str(admin).upper()}, '
            f'INHERIT {str(inherit).upper()}, SET {str(set_option).upper()}'
        )
    return sql


def _is_allowed_clone_role_sql(sql: str) -> bool:
    if sql in {
        'ALTER ROLE "pgbouncer" NOBYPASSRLS',
        'ALTER ROLE "supabase_auth_admin" NOBYPASSRLS',
        'CREATE ROLE "supabase_realtime_admin" NOLOGIN NOBYPASSRLS NOINHERIT NOCREATEROLE NOCREATEDB NOREPLICATION',
    }:
        return True
    return bool(re.fullmatch(
        r'GRANT "(?:anon|authenticated|service_role)" TO "supabase_realtime_admin" '
        r'WITH ADMIN FALSE, INHERIT FALSE, SET TRUE', sql,
    ))


async def _role_snapshot(settings: SourceSettings, ca_file: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = None
    try:
        conn = await asyncpg.connect(
            host=settings.host, port=settings.port, database=settings.database,
            user=settings.user, password=settings.password,
            ssl=verified_ssl_context(ca_file), timeout=20, command_timeout=30,
            server_settings={"application_name": settings.application_name},
        )
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            roles = await conn.fetch("""
                SELECT r.rolname,r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,
                       r.rolreplication,r.rolbypassrls,r.rolcanlogin,r.rolconnlimit,
                       r.rolvaliduntil::text AS valid_until,r.rolconfig
                  FROM pg_roles r ORDER BY r.rolname
            """)
            memberships = await conn.fetch("""
                SELECT granted.rolname AS granted_role,member.rolname AS member_role,
                       grantor.rolname AS grantor_role,m.admin_option,m.inherit_option,m.set_option
                  FROM pg_auth_members m JOIN pg_roles granted ON granted.oid=m.roleid
                  JOIN pg_roles member ON member.oid=m.member JOIN pg_roles grantor ON grantor.oid=m.grantor
                 ORDER BY 1,2,3
            """)
        return [dict(row) for row in roles], [dict(row) for row in memberships]
    except C01SnapshotError:
        raise
    except Exception:
        raise C01SnapshotError("c01_role_snapshot_failed") from None
    finally:
        if conn is not None:
            await conn.close()


@asynccontextmanager
async def source_snapshot(settings: SourceSettings, ca_file: str | Path):
    """Keep the exporting transaction open while pg_dump imports its snapshot."""
    context = verified_ssl_context(ca_file)
    conn = None
    tx = None
    try:
        conn = await asyncpg.connect(
            host=settings.host, port=settings.port, database=settings.database,
            user=settings.user, password=settings.password, ssl=context,
            timeout=20, command_timeout=180,
            server_settings={"application_name": settings.application_name},
        )
        tx = conn.transaction(isolation="repeatable_read", readonly=True)
        await tx.start()
        await conn.execute("SET LOCAL statement_timeout='180s'")
        await conn.execute("SET LOCAL lock_timeout='5s'")
        snapshot_id = await conn.fetchval("SELECT pg_export_snapshot()")
        snapshot = await _fetch_catalog_snapshot(conn)
        if not snapshot_id:
            raise C01SnapshotError("c01_snapshot_unavailable")
        yield snapshot_id, snapshot
    except C01SnapshotError:
        raise
    except Exception:
        # Database diagnostics may contain endpoint or query values.
        raise C01SnapshotError("c01_snapshot_failed") from None
    finally:
        if conn is not None:
            if tx is not None:
                try:
                    await tx.rollback()
                except Exception:
                    pass
            await conn.close()


def _assert_private_file(path: Path) -> None:
    try:
        if not path.is_file() or path.is_symlink():
            raise ValueError
        if os.name == "posix":
            info = path.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError
            return
        if os.name == "nt":
            script = r"""
$p = $env:MELPIS_C01_ACL_TARGET
Import-Module Microsoft.PowerShell.Security -ErrorAction Stop
$acl = Get-Acl -LiteralPath $p
$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$entries = @($acl.Access | ForEach-Object {
  [pscustomobject]@{ Who=$_.IdentityReference.Value; Type=$_.AccessControlType.ToString(); Rights=$_.FileSystemRights.ToString(); Inherit=$_.InheritanceFlags.ToString(); Propagate=$_.PropagationFlags.ToString() }
})
[pscustomobject]@{ Protected=$acl.AreAccessRulesProtected; Me=$me; Entries=$entries } | ConvertTo-Json -Compress -Depth 5
"""
            powershell = shutil.which("pwsh")
            if not powershell:
                raise ValueError
            child_env = os.environ.copy()
            child_env["MELPIS_C01_ACL_TARGET"] = str(path)
            proc = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False, timeout=15,
                env=child_env,
            )
            value = json.loads(proc.stdout) if proc.returncode == 0 else None
            if not value or not _windows_private_acl_is_valid(value):
                raise ValueError
            return
        raise ValueError
    except Exception:
        raise C01SnapshotError("c01_secret_file_not_private") from None


def _windows_private_acl_is_valid(value: Any) -> bool:
    """Compare Windows account names case-insensitively and require exact ACLs."""
    entries = value.get("Entries") if isinstance(value, dict) else None
    if isinstance(entries, dict):  # PowerShell serializes a one-element array as an object.
        entries = [entries]
    if (not isinstance(entries, list) or value.get("Protected") is not True
            or not isinstance(value.get("Me"), str)):
        return False
    expected = {value["Me"].casefold(), "BUILTIN\\administrators".casefold(),
                "NT AUTHORITY\\system".casefold()}
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        if (entry.get("Type") != "Allow" or entry.get("Rights") not in ("FullControl", "268435456")
                or entry.get("Inherit") != "None" or entry.get("Propagate") != "None"
                or not isinstance(entry.get("Who"), str)):
            return False
        seen.add(entry["Who"].casefold())
    return seen == expected


def load_source_settings(env_file: str | Path) -> SourceSettings:
    path = Path(env_file).resolve()
    _assert_private_file(path)
    try:
        return parse_source_env(path.read_text(encoding="utf-8"))
    except OSError:
        raise C01SnapshotError("c01_source_configuration_rejected") from None


def dump_with_docker(
    settings: SourceSettings, env_file: str | Path, ca_file: str | Path,
    snapshot_id: str, *, client_image: str = _PG_CLIENT_IMAGE,
) -> tuple[bytes, bytes]:
    """Run read-only pg_dump + password-free role dump inside ephemeral PG17 client."""
    if not re.fullmatch(r"[0-9A-Fa-f-]{16,64}", snapshot_id):
        raise C01SnapshotError("c01_snapshot_id_invalid")
    env_path, cert = Path(env_file).resolve(), Path(ca_file).resolve()
    _assert_private_file(env_path)
    if (not cert.is_file() or settings.sslmode != "verify-full"
            or client_image != _PG_CLIENT_IMAGE):
        raise C01SnapshotError("c01_dump_inputs_invalid")
    verified_ssl_context(cert)
    mount = f"type=bind,source={cert},target={settings.ca_container_path},readonly"
    common = [
        "docker", "run", "--rm", "--network", "bridge", "--env-file", str(env_path),
        "--mount", mount, client_image,
    ]
    dump_args = _database_dump_args(snapshot_id)
    roles_args = ["pg_dumpall", "--roles-only", "--no-role-passwords", "--no-password"]
    try:
        archive = subprocess.run(common + dump_args, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, check=False, timeout=1800)
        if archive.returncode or not archive.stdout or len(archive.stdout) > _MAX_BUNDLE_BYTES:
            raise C01SnapshotError("c01_database_dump_failed")
        roles = subprocess.run(common + roles_args, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, check=False, timeout=300)
        if roles.returncode or not roles.stdout or len(roles.stdout) > 8 * 1024 * 1024:
            raise C01SnapshotError("c01_roles_dump_failed")
        return archive.stdout, roles.stdout
    except C01SnapshotError:
        raise
    except Exception:
        raise C01SnapshotError("c01_dump_failed") from None


async def create_staging_backup(
    env_file: str | Path, ca_file: str | Path, backup_dir: str | Path,
    master_key: bytes,
) -> tuple[Path, Path, dict[str, Any]]:
    """Capture and encrypt a Staging-only snapshot; caller retains its transaction through pg_dump."""
    env_path, ca_path = Path(env_file).resolve(), Path(ca_file).resolve()
    settings = load_source_settings(env_path)
    async with source_snapshot(settings, ca_path) as (snapshot_id, snapshot):
        database_dump, roles_sql = dump_with_docker(settings, env_path, ca_path, snapshot_id)
        roles_after, memberships_after = await _role_snapshot(settings, ca_path)
        if roles_after != snapshot["roles"] or memberships_after != snapshot["memberships"]:
            raise C01SnapshotError("c01_role_inventory_changed_during_backup")
    # The source MVCC transaction closed after both database and role dumps finished.
    # Verify that the role dump has no password hashes before packaging it.
    try:
        role_text = roles_sql.decode("utf-8")
    except UnicodeDecodeError:
        raise C01SnapshotError("c01_roles_dump_invalid") from None
    if re.search(r"(?im)^\s*(CREATE|ALTER)\s+ROLE\b.*\bPASSWORD\b", role_text):
        raise C01SnapshotError("c01_roles_dump_invalid")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_name = f"c01-staging-{stamp}-{secrets.token_hex(6)}.enc"
    artifact_path, manifest_path = persist_encrypted_backup(
        backup_dir, artifact_name, database_dump, roles_sql, snapshot, master_key,
    )
    return artifact_path, manifest_path, snapshot


def _database_dump_args(snapshot_id: str) -> list[str]:
    """Arguments preserve database ownership, object ownership and ACLs."""
    if not re.fullmatch(r"[0-9A-Fa-f-]{16,64}", snapshot_id):
        raise C01SnapshotError("c01_snapshot_id_invalid")
    return ["pg_dump", "--format=custom", "--create", "--quote-all-identifiers",
            "--snapshot", snapshot_id, "--no-password", "--dbname=postgres"]


def _enforce_private_directory(path: Path) -> None:
    if os.name == "posix":
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
        info = path.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise C01SnapshotError("c01_backup_directory_not_private")
        return
    if os.name == "nt":
        script = r"""
$p = $env:MELPIS_C01_ACL_TARGET
Import-Module Microsoft.PowerShell.Security -ErrorAction Stop
$acl = Get-Acl -LiteralPath $p
$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$entries = @($acl.Access | ForEach-Object {
  [pscustomobject]@{ Who=$_.IdentityReference.Value; Type=$_.AccessControlType.ToString(); Rights=$_.FileSystemRights.ToString(); Inherit=$_.InheritanceFlags.ToString(); Propagate=$_.PropagationFlags.ToString() }
})
[pscustomobject]@{ Protected=$acl.AreAccessRulesProtected; Me=$me; Entries=$entries } | ConvertTo-Json -Compress -Depth 5
"""
        try:
            powershell = shutil.which("pwsh")
            if not powershell:
                raise ValueError
            child_env = os.environ.copy()
            child_env["MELPIS_C01_ACL_TARGET"] = str(path)
            proc = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False, timeout=15,
                env=child_env,
            )
            value = json.loads(proc.stdout) if proc.returncode == 0 else None
            if not value or not _windows_private_acl_is_valid(value):
                raise ValueError
        except Exception:
            raise C01SnapshotError("c01_backup_directory_not_private") from None
        return
    raise C01SnapshotError("c01_backup_directory_not_private")


def persist_encrypted_backup(
    directory: str | Path, artifact_name: str, database_dump: bytes,
    roles_sql: bytes, snapshot: dict[str, Any], master_key: bytes,
) -> tuple[Path, Path]:
    if not re.fullmatch(r"c01-staging-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}\.enc", artifact_name):
        raise C01SnapshotError("c01_artifact_name_invalid")
    target_dir = Path(directory).resolve()
    _enforce_private_directory(target_dir)
    bundle = build_bundle(database_dump, roles_sql, snapshot)
    artifact = encrypt_bundle(bundle, master_key)
    metadata = {
        "project_ref": _PROJECT_REF,
        "database": snapshot.get("database", {}).get("datname"),
        "extensions": snapshot.get("extensions"),
        "migration_count": len(snapshot.get("migration_history", [])),
        "catalog_sha256": hashlib.sha256(_json_bytes(snapshot)).hexdigest(),
    }
    manifest = create_integrity_manifest(artifact, master_key, metadata)
    artifact_path = target_dir / artifact_name
    manifest_path = artifact_path.with_suffix(".manifest.json")
    if artifact_path.exists() or manifest_path.exists():
        raise C01SnapshotError("c01_artifact_exists")
    try:
        with artifact_path.open("xb") as handle:
            os.chmod(artifact_path, 0o600)
            handle.write(artifact)
            handle.flush()
            os.fsync(handle.fileno())
        with manifest_path.open("xb") as handle:
            os.chmod(manifest_path, 0o600)
            handle.write(manifest)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        # Keep a partial encrypted artifact for explicit operator review; never delete evidence here.
        raise C01SnapshotError("c01_artifact_persist_failed") from None
    return artifact_path, manifest_path


def verify_persisted_backup(artifact_path: str | Path, manifest_path: str | Path, master_key: bytes) -> dict[str, bytes]:
    try:
        artifact_file, manifest_file = Path(artifact_path).resolve(), Path(manifest_path).resolve()
        _assert_private_file(artifact_file)
        _assert_private_file(manifest_file)
        artifact = artifact_file.read_bytes()
        manifest = manifest_file.read_bytes()
        public = verify_integrity_manifest(artifact, manifest, master_key)
        bundle = open_bundle(decrypt_bundle(artifact, master_key))
        snapshot = json.loads(bundle["snapshot.json"])
        if not hmac.compare_digest(
            public.get("metadata_sha256", ""),
            hashlib.sha256(_json_bytes({
                "project_ref": _PROJECT_REF,
                "database": snapshot.get("database", {}).get("datname"),
                "extensions": snapshot.get("extensions"),
                "migration_count": len(snapshot.get("migration_history", [])),
                "catalog_sha256": hashlib.sha256(_json_bytes(snapshot)).hexdigest(),
            })).hexdigest(),
        ):
            raise C01SnapshotError("c01_manifest_invalid")
        return bundle
    except C01SnapshotError:
        raise
    except OSError:
        raise C01SnapshotError("c01_backup_unavailable") from None


def load_master_key(key_file: str | Path) -> bytes:
    """Load a DPAPI-protected local key; plaintext key files are not accepted."""
    path = Path(key_file).resolve()
    _assert_private_file(path)
    try:
        protected = path.read_bytes()
    except OSError:
        raise C01SnapshotError("c01_encryption_key_unavailable") from None
    key = _dpapi_unprotect(protected)
    if len(key) != 32:
        raise C01SnapshotError("c01_encryption_key_invalid")
    return key


def load_or_create_master_key(key_file: str | Path) -> bytes:
    """Persist an AES key under current-user DPAPI and protected local ACLs."""
    path = Path(key_file).resolve()
    if path.exists():
        return load_master_key(path)
    if os.name != "nt":
        raise C01SnapshotError("c01_native_key_protection_unavailable")
    _enforce_private_directory(path.parent)
    key = secrets.token_bytes(32)
    protected = _dpapi_protect(key)
    try:
        with path.open("xb") as handle:
            handle.write(protected)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_private_file(path)
    except OSError:
        raise C01SnapshotError("c01_encryption_key_persist_failed") from None
    return key


def _dpapi_transform(value: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise C01SnapshotError("c01_native_key_protection_unavailable")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    source_buf = ctypes.create_string_buffer(value)
    source = DataBlob(len(value), ctypes.cast(source_buf, ctypes.POINTER(ctypes.c_byte)))
    entropy_bytes = b"Melpis.C01.Staging.BackupKey.v1"
    entropy_buf = ctypes.create_string_buffer(entropy_bytes)
    entropy = DataBlob(len(entropy_bytes), ctypes.cast(entropy_buf, ctypes.POINTER(ctypes.c_byte)))
    output = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    description = ctypes.c_wchar_p("Melpis C01 local backup key")
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    try:
        if protect:
            ok = crypt32.CryptProtectData(
                ctypes.byref(source), description, ctypes.byref(entropy), None, None,
                flags, ctypes.byref(output),
            )
        else:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                flags, ctypes.byref(output),
            )
        if not ok or not output.pbData or not output.cbData:
            raise C01SnapshotError("c01_native_key_protection_failed")
        return ctypes.string_at(output.pbData, output.cbData)
    except C01SnapshotError:
        raise
    except Exception:
        raise C01SnapshotError("c01_native_key_protection_failed") from None
    finally:
        if output.pbData:
            kernel32.LocalFree(output.pbData)


def _dpapi_protect(value: bytes) -> bytes:
    return b"C01DPAPI1" + _dpapi_transform(value, protect=True)


def _dpapi_unprotect(value: bytes) -> bytes:
    if not value.startswith(b"C01DPAPI1"):
        raise C01SnapshotError("c01_encryption_key_invalid")
    return _dpapi_transform(value[len(b"C01DPAPI1"):], protect=False)


def restore_encrypted_backup(
    artifact_path: str | Path, manifest_path: str | Path, master_key: bytes,
    target: RestoreAdapter, *, expected_image_id: str,
) -> RestoreReceipt:
    """Restore one authenticated archive into a private disposable PG17 clone.

    The adapter must implement Docker operations with argument arrays, suppress
    command output, and never expose database contents. Once the target database
    is dropped, every failure leaves the clone quarantined; this function never
    retries, deletes the target, or attempts an automatic rollback.
    """
    bundle = verify_persisted_backup(artifact_path, manifest_path, master_key)
    try:
        source_snapshot = json.loads(bundle["snapshot.json"])
        roles_sql = bundle["roles.sql"].decode("utf-8")
        if re.search(r"(?i)\bPASSWORD\b", roles_sql):
            raise ValueError
        if source_snapshot.get("project_ref") != _PROJECT_REF:
            raise ValueError
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        raise C01SnapshotError("c01_backup_payload_invalid") from None

    try:
        inspect_payload = target.inspect()
        target_roles, target_memberships = target.role_inventory()
        target_extensions = target.extension_inventory()
        available_versions = target.available_extension_versions()
        plan = plan_clone_normalizations(
            source_snapshot, target_roles, target_memberships, target_extensions,
            available_versions,
        )
        validate_restore_container(
            inspect_payload["container"], inspect_payload["network"], expected_image_id=expected_image_id,
            normalization_plan=plan,
        )
        version = target.server_version()
        if not re.fullmatch(r"17\.6\.1\.171(?:\s.*)?", version):
            raise C01SnapshotError("c01_restore_postgres_version_mismatch")
        if hasattr(target, "preflight"):
            target.preflight()
    except C01SnapshotError:
        raise
    except Exception:
        raise C01SnapshotError("c01_restore_preflight_failed") from None

    destructive_started = False
    try:
        for sql in plan.role_sql:
            target.apply_local_sql(sql)
        roles_after, memberships_after = target.role_inventory()
        if plan_clone_role_normalizations(
            source_snapshot["roles"], roles_after,
            source_snapshot["memberships"], memberships_after,
        ):
            raise C01SnapshotError("c01_clone_role_normalization_incomplete")

        # Re-check isolation and the image immediately before the destructive step.
        latest_inspect = target.inspect()
        validate_restore_container(
            latest_inspect["container"], latest_inspect["network"],
            expected_image_id=expected_image_id, normalization_plan=plan,
        )
        destructive_started = True
        target.drop_database("postgres")  # adapter must connect to template1
        target.restore_archive(bundle["database.dump"])  # archive has --create
        restored_snapshot = target.capture_snapshot()
        compare_snapshots(source_snapshot, restored_snapshot)
        source_hash = hashlib.sha256(_json_bytes(source_snapshot)).hexdigest()
        restored_hash = hashlib.sha256(_json_bytes(restored_snapshot)).hexdigest()
        if not hmac.compare_digest(source_hash, restored_hash):
            raise C01SnapshotError("c01_restore_catalog_or_data_mismatch")
        target_name = inspect_payload["container"]["Name"].lstrip("/")
        return RestoreReceipt(
            _PROJECT_REF, target_name, source_hash, restored_hash,
            plan.extension_actions + plan.role_sql, True,
        )
    except C01SnapshotError:
        if destructive_started:
            try:
                target.quarantine("c01_restore_partial_quarantined")
            except Exception:
                pass
            raise C01SnapshotError("c01_restore_partial_quarantined") from None
        raise
    except Exception:
        if destructive_started:
            try:
                target.quarantine("c01_restore_partial_quarantined")
            except Exception:
                pass
            raise C01SnapshotError("c01_restore_partial_quarantined") from None
        raise C01SnapshotError("c01_restore_failed") from None


def main() -> int:
    """Explicit operator entrypoint. Restore only targets a prebuilt local clone."""
    parser = argparse.ArgumentParser(prog="c01-staging-snapshot")
    subparsers = parser.add_subparsers(dest="action", required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("--source-env", required=True)
    backup.add_argument("--ca-file", required=True)
    backup.add_argument("--backup-dir", required=True)
    backup.add_argument("--key-file", required=True)
    restore = subparsers.add_parser("restore")
    restore.add_argument("--artifact", required=True)
    restore.add_argument("--manifest", required=True)
    restore.add_argument("--key-file", required=True)
    restore.add_argument("--container", required=True)
    restore.add_argument("--image-id", required=True)
    restore.add_argument("--local-env", required=True)
    try:
        args = parser.parse_args()
        key = load_or_create_master_key(args.key_file)
        if args.action == "backup":
            artifact, _manifest, _snapshot = asyncio.run(create_staging_backup(
                args.source_env, args.ca_file, args.backup_dir, key,
            ))
            print(f"c01_backup_created artifact={artifact.name}")
            return 0
        adapter = DockerRestoreAdapter(args.container, lambda: {})
        adapter._snapshot_reader = local_pg17_snapshot_reader(adapter, args.local_env)
        receipt = restore_encrypted_backup(
            args.artifact, args.manifest, key, adapter, expected_image_id=args.image_id,
        )
        print(f"c01_restore_verified target={receipt.target_name} catalog_sha256={receipt.restored_catalog_sha256}")
        return 0
    except C01SnapshotError as exc:
        # All raised messages are stable codes; never emit subprocess or DB diagnostics.
        print(str(exc))
        return 1
    except SystemExit as exc:
        return int(exc.code)


if __name__ == "__main__":
    raise SystemExit(main())
