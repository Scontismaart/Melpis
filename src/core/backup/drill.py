"""Encrypted backup/restore drill for a dedicated, quarantined database.

This drill never grants traffic access and never reconciles post-backup erasures.
The restore target must already be an empty, restricted database created solely
for this run. Database URLs and subprocess diagnostics are never logged.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import re
import secrets
import stat
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import uuid4

import asyncpg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from src.core.db.scoping import system_scope

logger = logging.getLogger(__name__)

_MAGIC = b"MELPIS-DRILL-BACKUP\x01"
_SALT_BYTES = 16
_CHUNK_BYTES = 64 * 1024
_RECORD_HEADER = struct.Struct(">cI")
_FOOTER = struct.Struct(">QQ32s")
_TARGET_NAME = re.compile(r"melpis_restore_drill_[0-9a-f]{16,32}\Z")
_ADVISORY_KEY = (188635145, 43024)
_COMMAND_TIMEOUT_SECONDS = 3600


class BackupDrillError(RuntimeError):
    """A deliberately non-sensitive backup/restore failure code."""


@dataclass(frozen=True, slots=True)
class DrillResult:
    ok: bool
    backup_path: str
    started_at: str
    finished_at: str
    detail: str
    traffic_authorized: bool = False
    erasure_reconciliation: str = "required"
    external_copies_certified: bool = False


@dataclass(frozen=True, slots=True)
class _ConnectionSpec:
    database: str
    environment: dict[str, str]


def _backup_key(explicit: bytes | None) -> bytes:
    if explicit is not None:
        if type(explicit) is not bytes or len(explicit) != 32:
            raise BackupDrillError("backup_drill_key_invalid")
        return explicit
    encoded = os.getenv("BACKUP_DRILL_ENCRYPTION_KEY", "")
    try:
        if not encoded or not encoded.isascii():
            raise ValueError
        key = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
        if len(key) != 32:
            raise ValueError
        return key
    except (ValueError, binascii.Error):
        raise BackupDrillError("backup_drill_key_invalid") from None


def _connection_spec(dsn: str) -> _ConnectionSpec:
    """Build a restricted libpq environment without placing credentials in argv."""
    try:
        if type(dsn) is not str or not dsn or "\x00" in dsn:
            raise ValueError
        parsed = urlsplit(dsn)
        if (parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname
                or parsed.username is None or not parsed.password or parsed.fragment or parsed.port == 0):
            raise ValueError
        database = unquote(parsed.path.removeprefix("/"))
        user = unquote(parsed.username)
        password = unquote(parsed.password) if parsed.password is not None else None
        if (not database or "/" in database or not user or any("\x00" in item
                for item in (database, user, password or ""))):
            raise ValueError
        options = dict(parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True))
        if len(options) != len(parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)):
            raise ValueError
        allowed = {
            "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT",
            "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
            "connect_timeout": "PGCONNECT_TIMEOUT", "application_name": "PGAPPNAME",
        }
        if options.keys() - allowed.keys() or any("\x00" in value for value in options.values()):
            raise ValueError
        if "sslmode" in options and options["sslmode"] not in {
            "disable", "allow", "prefer", "require", "verify-ca", "verify-full",
        }:
            raise ValueError
        # Do not forward ambient credentials or libpq settings to subprocesses.
        inherited = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
                     "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
        environment = {name: os.environ[name] for name in inherited if name in os.environ}
        environment.update({
            "PGHOST": parsed.hostname, "PGPORT": str(parsed.port or 5432),
            "PGUSER": user, "PGDATABASE": database, "PGPASSFILE": os.devnull,
            "PGSERVICEFILE": os.devnull, "PGAPPNAME": "melpis_backup_drill",
            "PGCLIENTENCODING": "UTF8",
        })
        if password is not None:
            environment["PGPASSWORD"] = password
        environment.update({allowed[name]: value for name, value in options.items()})
        return _ConnectionSpec(database, environment)
    except (TypeError, ValueError, UnicodeError):
        raise BackupDrillError("backup_drill_connection_invalid") from None


def _archive_cipher(key: bytes, salt: bytes) -> AESGCM:
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                   info=b"melpis.backup-drill.archive.v1").derive(key)
    return AESGCM(derived)


def _record(cipher: AESGCM, header: bytes, marker: bytes, sequence: int, plaintext: bytes) -> bytes:
    if not 0 <= sequence < 2**32:
        raise BackupDrillError("backup_drill_archive_too_large")
    sequence_bytes = sequence.to_bytes(4, "big")
    nonce = b"\x00" * 8 + sequence_bytes
    encrypted = cipher.encrypt(nonce, plaintext, header + marker + sequence_bytes)
    return _RECORD_HEADER.pack(marker, len(encrypted)) + encrypted


def _read_exact(stream, length: int) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise BackupDrillError("backup_drill_archive_invalid")
    return data


def _archive_chunks(stream, key: bytes):
    """Yield authenticated plaintext chunks; reject truncation and extra data."""
    header = _read_exact(stream, len(_MAGIC) + _SALT_BYTES)
    if not header.startswith(_MAGIC):
        raise BackupDrillError("backup_drill_archive_invalid")
    cipher = _archive_cipher(key, header[len(_MAGIC):])
    digest, count, total = hashlib.sha256(), 0, 0
    while True:
        marker, size = _RECORD_HEADER.unpack(_read_exact(stream, _RECORD_HEADER.size))
        if marker == b"D":
            if not 17 <= size <= _CHUNK_BYTES + 16:
                raise BackupDrillError("backup_drill_archive_invalid")
        elif marker == b"F":
            if size != _FOOTER.size + 16:
                raise BackupDrillError("backup_drill_archive_invalid")
        else:
            raise BackupDrillError("backup_drill_archive_invalid")
        if count >= 2**32:
            raise BackupDrillError("backup_drill_archive_invalid")
        sequence = count.to_bytes(4, "big")
        try:
            plaintext = cipher.decrypt(b"\x00" * 8 + sequence, _read_exact(stream, size),
                                       header + marker + sequence)
        except InvalidTag:
            raise BackupDrillError("backup_drill_archive_invalid") from None
        if marker == b"F":
            expected_count, expected_total, expected_digest = _FOOTER.unpack(plaintext)
            if (not count or expected_count != count or expected_total != total
                    or expected_digest != digest.digest() or stream.read(1)):
                raise BackupDrillError("backup_drill_archive_invalid")
            return
        digest.update(plaintext)
        count += 1
        total += len(plaintext)
        yield plaintext


def _new_archive(directory: Path, started: datetime):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not directory.is_dir():
        raise BackupDrillError("backup_drill_directory_invalid")
    if os.name == "posix":
        info = directory.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise BackupDrillError("backup_drill_directory_invalid")
    path = directory / f"drill-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex}.enc"
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        return path, os.fdopen(fd, "w+b")
    except BaseException:
        os.close(fd)
        raise


async def _spawn(*args, **kwargs):
    return await asyncio.create_subprocess_exec(*args, **kwargs)


async def _stop_process(process) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


async def _dump_encrypted(stream, key: bytes, spec: _ConnectionSpec, snapshot: str) -> None:
    process = await _spawn(
        "pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--no-password",
        "--no-comments", "--no-security-labels",
        f"--dbname={spec.database}", f"--snapshot={snapshot}", env=spec.environment,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        header = _MAGIC + secrets.token_bytes(_SALT_BYTES)
        cipher = _archive_cipher(key, header[len(_MAGIC):])
        stream.write(header)
        digest, count, total = hashlib.sha256(), 0, 0
        while True:
            chunk = await process.stdout.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            stream.write(_record(cipher, header, b"D", count, chunk))
            count += 1
            total += len(chunk)
        if await process.wait() != 0 or not count:
            raise BackupDrillError("backup_drill_dump_failed")
        stream.write(_record(cipher, header, b"F", count, _FOOTER.pack(count, total, digest.digest())))
        stream.flush()
        os.fsync(stream.fileno())
    finally:
        await _stop_process(process)


async def _restore_archive(stream, key: bytes, spec: _ConnectionSpec) -> None:
    # Authenticate the entire archive before giving any bytes to pg_restore.
    stream.seek(0)
    for _ in _archive_chunks(stream, key):
        pass
    stream.seek(0)
    process = await _spawn(
        "pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "--no-password",
        f"--dbname={spec.database}", env=spec.environment, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        for chunk in _archive_chunks(stream, key):
            process.stdin.write(chunk)
            await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()
        if await process.wait() != 0:
            raise BackupDrillError("backup_drill_restore_failed")
    except (BrokenPipeError, ConnectionResetError):
        raise BackupDrillError("backup_drill_restore_failed") from None
    finally:
        await _stop_process(process)


async def _identity(conn):
    row = await conn.fetchrow("""SELECT current_database() AS database_name,
        inet_server_addr()::text AS server_address, inet_server_port() AS server_port,
        d.oid::bigint AS database_oid FROM pg_database d WHERE d.datname=current_database()""")
    if not row or not row["server_address"] or not row["server_port"]:
        raise BackupDrillError("backup_drill_database_identity_unavailable")
    return row


async def _target_safe(conn, *, require_empty: bool) -> None:
    row = await conn.fetchrow("""SELECT current_database() AS database_name,
        d.datdba=(SELECT oid FROM pg_roles WHERE rolname=session_user) AS owner_is_session,
        NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls)
          AND NOT EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid) AS role_is_restricted,
        NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(d.datacl,acldefault('d',d.datdba))) a
          WHERE a.grantee<>d.datdba AND a.privilege_type IN ('CONNECT','CREATE','TEMPORARY')) AS acl_is_private,
        NOT pg_is_in_recovery() AS writable,
        (SELECT count(*) FROM pg_stat_activity a WHERE a.datid=d.oid
          AND a.pid<>pg_backend_pid() AND a.backend_type='client backend') AS other_clients
      FROM pg_database d JOIN pg_roles r ON r.rolname=session_user
      WHERE d.datname=current_database()""")
    if (not row or not _TARGET_NAME.fullmatch(row["database_name"])
            or not row["owner_is_session"] or not row["role_is_restricted"]
            or not row["acl_is_private"] or not row["writable"] or row["other_clients"] != 0):
        raise BackupDrillError("backup_drill_target_not_isolated")
    if require_empty:
        # Only the project's vetted PostgreSQL extensions may be preinstalled
        # by an administrator. Their objects contain no tenant data. Any other
        # user-created object makes this target ineligible for a drill.
        occupied = await conn.fetchval("""WITH RECURSIVE permitted(classid,objid) AS (
          SELECT d.classid,d.objid FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid
            WHERE d.refclassid='pg_extension'::regclass AND d.deptype='e'
              AND e.extname IN ('pgcrypto','vector','btree_gist')
          UNION
          SELECT d.classid,d.objid FROM pg_depend d JOIN permitted p
            ON d.refclassid=p.classid AND d.refobjid=p.objid WHERE d.deptype IN ('i','a')
        ) SELECT
          EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname NOT IN ('public','extensions','information_schema')
            AND n.nspname !~ '^pg_')
          OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
              AND NOT EXISTS(SELECT 1 FROM permitted p WHERE p.classid='pg_class'::regclass AND p.objid=c.oid))
          OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
              AND NOT EXISTS(SELECT 1 FROM permitted x WHERE x.classid='pg_proc'::regclass AND x.objid=p.oid))
          OR EXISTS(SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
            WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
              AND NOT EXISTS(SELECT 1 FROM permitted p WHERE p.classid='pg_type'::regclass AND p.objid=t.oid))
          OR EXISTS(SELECT 1 FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace
            WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
              AND NOT EXISTS(SELECT 1 FROM permitted p WHERE p.classid='pg_operator'::regclass AND p.objid=o.oid))
          OR EXISTS(SELECT 1 FROM pg_opclass o JOIN pg_namespace n ON n.oid=o.opcnamespace
            WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
              AND NOT EXISTS(SELECT 1 FROM permitted p WHERE p.classid='pg_opclass'::regclass AND p.objid=o.oid))
          OR EXISTS(SELECT 1 FROM pg_opfamily o JOIN pg_namespace n ON n.oid=o.opfnamespace
            WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
              AND NOT EXISTS(SELECT 1 FROM permitted p WHERE p.classid='pg_opfamily'::regclass AND p.objid=o.oid))
          OR EXISTS(SELECT 1 FROM pg_cast c WHERE c.oid>=16384
              AND NOT EXISTS(SELECT 1 FROM permitted p WHERE p.classid='pg_cast'::regclass AND p.objid=c.oid))
          OR EXISTS(SELECT 1 FROM pg_am a WHERE a.oid>=16384
              AND NOT EXISTS(SELECT 1 FROM permitted p WHERE p.classid='pg_am'::regclass AND p.objid=a.oid))
          OR EXISTS(SELECT 1 FROM pg_extension WHERE extname NOT IN ('plpgsql','pgcrypto','vector','btree_gist'))
          OR EXISTS(SELECT 1 FROM pg_largeobject_metadata)
          OR EXISTS(SELECT 1 FROM pg_event_trigger)
          OR EXISTS(SELECT 1 FROM pg_publication)
          OR EXISTS(SELECT 1 FROM pg_foreign_server)
          OR EXISTS(SELECT 1 FROM pg_default_acl)""")
        if occupied:
            raise BackupDrillError("backup_drill_target_not_empty")


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


async def _extension_inventory(conn) -> tuple[tuple[str, str, str], ...]:
    rows = await conn.fetch("""SELECT e.extname,e.extversion,n.nspname FROM pg_extension e
        JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY e.extname""")
    if any(row["extname"] not in {"plpgsql", "pgcrypto", "vector", "btree_gist"} for row in rows):
        raise BackupDrillError("backup_drill_extension_not_supported")
    return tuple((row["extname"], row["extversion"], row["nspname"]) for row in rows)


@system_scope("isolated encrypted backup parity check counts every table in the verified source/quarantine snapshot")
async def _table_counts(conn) -> dict[tuple[str, str], int]:
    tables = await conn.fetch("""SELECT n.nspname AS schema_name,c.relname AS table_name
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p') AND NOT c.relispartition
          AND n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
        ORDER BY n.nspname,c.relname""")
    counts = {}
    for row in tables:
        name = (_quoted_identifier(row["schema_name"]) + "." + _quoted_identifier(row["table_name"]))
        counts[(row["schema_name"], row["table_name"])] = await conn.fetchval(f"SELECT count(*) FROM {name}")
    return counts


async def run_backup_restore_drill(
    database_url: str | None = None,
    verify_database_url: str | None = None,
    backup_dir: str | None = None,
    *, encryption_key: bytes | None = None,
) -> DrillResult:
    """Create an encrypted snapshot and restore it only into an isolated empty DB.

    The target remains restricted and quarantined. This result never proves that
    independent post-backup erasure or stay-purge journals have been reconciled.
    """
    if os.getenv("BACKUP_DRILL_ENABLED") != "true":
        raise BackupDrillError("backup_drill_disabled")
    started = datetime.now(timezone.utc)
    source_url = database_url or os.getenv("BACKUP_DRILL_SOURCE_DSN", "")
    target_url = verify_database_url or os.getenv("BACKUP_DRILL_TARGET_DSN", "")
    key = _backup_key(encryption_key)
    source_spec, target_spec = _connection_spec(source_url), _connection_spec(target_url)
    source = target = stream = None
    try:
        source = await asyncpg.connect(source_url, timeout=15, command_timeout=600)
        target = await asyncpg.connect(target_url, timeout=15, command_timeout=600)
        source_id, target_id = await _identity(source), await _identity(target)
        # Database names must differ even if aliases or proxies hide a shared server.
        if (source_id["database_name"] == target_id["database_name"]
                or (source_id["server_address"], source_id["server_port"], source_id["database_oid"])
                == (target_id["server_address"], target_id["server_port"], target_id["database_oid"])):
            raise BackupDrillError("backup_drill_target_is_source")
        if (source_id["database_name"] != source_spec.database
                or target_id["database_name"] != target_spec.database):
            raise BackupDrillError("backup_drill_database_identity_unavailable")
        if not await target.fetchval("SELECT pg_try_advisory_lock($1,$2)", *_ADVISORY_KEY):
            raise BackupDrillError("backup_drill_target_in_use")
        await _target_safe(target, require_empty=True)
        async with source.transaction(isolation="repeatable_read", readonly=True):
            source_extensions = await _extension_inventory(source)
            if source_extensions != await _extension_inventory(target):
                raise BackupDrillError("backup_drill_extension_mismatch")
            if await source.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_foreign_table f
                JOIN pg_class c ON c.oid=f.ftrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_')"""):
                raise BackupDrillError("backup_drill_external_tables_unsupported")
            counts = await _table_counts(source)
            large_objects = await source.fetchval("SELECT count(*) FROM pg_largeobject_metadata")
            if ("public", "organizations") not in counts or ("public", "user_profiles") not in counts:
                raise BackupDrillError("backup_drill_source_schema_unavailable")
            snapshot = await source.fetchval("SELECT pg_export_snapshot()")
            path, stream = _new_archive(Path(backup_dir or os.getenv("BACKUP_DRILL_DIR", "backups")), started)
            await asyncio.wait_for(_dump_encrypted(stream, key, source_spec, snapshot),
                                   timeout=_COMMAND_TIMEOUT_SECONDS)
        await _target_safe(target, require_empty=True)
        await asyncio.wait_for(_restore_archive(stream, key, target_spec), timeout=_COMMAND_TIMEOUT_SECONDS)
        await _target_safe(target, require_empty=False)
        if (await _extension_inventory(target) != source_extensions
                or await _table_counts(target) != counts
                or await target.fetchval("SELECT count(*) FROM pg_largeobject_metadata") != large_objects):
            raise BackupDrillError("backup_drill_restore_verification_failed")
        return DrillResult(
            ok=True, backup_path=str(path), started_at=started.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            detail="encrypted_snapshot_restored_to_quarantine",
        )
    except BackupDrillError:
        logger.error("backup_restore_drill=failed")
        raise
    except Exception:  # noqa: BLE001 - Never expose driver or process diagnostics.
        logger.error("backup_restore_drill=failed")
        raise BackupDrillError("backup_drill_failed") from None
    finally:
        if stream is not None:
            try:
                stream.close()
            except OSError:
                logger.error("backup_restore_drill=archive_close_failed")
        for conn in (target, source):
            if conn is not None:
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001 - Close failures can contain connection details.
                    logger.error("backup_restore_drill=connection_close_failed")


async def run_monthly_loop() -> None:
    try:
        interval_seconds = int(os.getenv("BACKUP_DRILL_INTERVAL_SECONDS", str(30 * 24 * 60 * 60)))
        if not 3600 <= interval_seconds <= 30 * 24 * 60 * 60:
            raise ValueError
    except ValueError:
        raise BackupDrillError("backup_drill_interval_invalid") from None
    run_on_start = os.getenv("BACKUP_DRILL_RUN_ON_START", "false").lower() == "true"
    if run_on_start and os.getenv("BACKUP_DRILL_ENABLED") == "true":
        await run_backup_restore_drill()
    while True:
        await asyncio.sleep(interval_seconds)
        if os.getenv("BACKUP_DRILL_ENABLED") == "true":
            await run_backup_restore_drill()
