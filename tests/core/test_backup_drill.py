"""Security and integrity tests for the quarantined backup drill."""

import asyncio
import io
import os
import secrets
from pathlib import Path

import pytest

from src.core.backup import drill


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Connection:
    def __init__(self, name, *, source=False):
        self.name = name
        self.source = source
        self.owned = True
        self.restricted = True
        self.private = True
        self.writable = True
        self.other_clients = 0
        self.occupied = False
        self.locked = True
        self.restored = False
        self.mismatch = False
        self.closed = False
        self.preflight_count = 0
        self.occupy_after_first_check = False
        self.extensions = [("plpgsql", "1.0", "pg_catalog")]
        self.foreign_tables = False

    def transaction(self, **_):
        return _Transaction()

    async def fetchrow(self, query, *_):
        if "server_address" in query:
            return {"database_name": self.name, "server_address": "127.0.0.1",
                    "server_port": 5432, "database_oid": 1 if self.source else 2}
        if "owner_is_session" in query:
            return {"database_name": self.name, "owner_is_session": self.owned,
                    "role_is_restricted": self.restricted, "acl_is_private": self.private,
                    "writable": self.writable, "other_clients": self.other_clients}
        raise AssertionError("unexpected metadata query")

    async def fetchval(self, query, *_):
        if "pg_try_advisory_lock" in query:
            return self.locked
        if "pg_foreign_table" in query:
            return self.foreign_tables
        if "count(*) FROM pg_largeobject_metadata" in query:
            return 0
        if "pg_namespace" in query:
            self.preflight_count += 1
            return self.occupied or (self.occupy_after_first_check and self.preflight_count > 1)
        if "pg_export_snapshot" in query:
            return "00000001-1"
        if query.startswith("SELECT count(*) FROM"):
            if '"organizations"' in query:
                return 1 if not self.mismatch else 0
            if '"user_profiles"' in query:
                return 2
            if '"booking_conversation_states"' in query:
                return 3
        raise AssertionError("unexpected scalar query")

    async def fetch(self, query, *_):
        if "e.extname,e.extversion,n.nspname" in query:
            return [{"extname": name, "extversion": version, "nspname": schema}
                    for name, version, schema in self.extensions]
        if "c.relkind IN" not in query:
            raise AssertionError("unexpected table query")
        if self.source or self.restored:
            return [{"schema_name": "public", "table_name": name} for name in (
                "organizations", "user_profiles", "booking_conversation_states",
            )]
        return []

    async def close(self):
        self.closed = True


class _Output:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    async def read(self, size):
        chunk = self.data[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class _Input:
    def __init__(self):
        self.contents = bytearray()

    def write(self, data):
        self.contents.extend(data)

    async def drain(self):
        return None

    def close(self):
        return None

    async def wait_closed(self):
        return None


class _Process:
    def __init__(self, *, output=b"", target=None, code=0):
        self.stdout = _Output(output)
        self.stdin = _Input()
        self.returncode = None
        self.target = target
        self.code = code

    async def wait(self):
        self.returncode = self.code
        if self.target is not None and self.code == 0:
            self.target.restored = True
        return self.code

    def kill(self):
        self.returncode = -9


def _url(database, password):
    return f"postgresql://synthetic_user:{password}@127.0.0.1:5432/{database}"


def _archive(payload, key):
    stream = io.BytesIO()
    header = drill._MAGIC + secrets.token_bytes(drill._SALT_BYTES)
    cipher = drill._archive_cipher(key, header[len(drill._MAGIC):])
    stream.write(header)
    stream.write(drill._record(cipher, header, b"D", 0, payload))
    import hashlib
    stream.write(drill._record(cipher, header, b"F", 1,
                               drill._FOOTER.pack(1, len(payload), hashlib.sha256(payload).digest())))
    return stream.getvalue()


def _install_fakes(monkeypatch, *, source=None, target=None, dump_code=0):
    password = secrets.token_urlsafe(24)
    source = source or _Connection("synthetic_source", source=True)
    target = target or _Connection("melpis_restore_drill_" + secrets.token_hex(8))
    source_url, target_url = _url(source.name, password), _url(target.name, password)
    payload = b"private synthetic database data " * 5000
    commands, processes = [], []

    async def connect(url, **_):
        if url == source_url:
            return source
        if url == target_url:
            return target
        raise AssertionError("unexpected connection")

    async def spawn(*args, **kwargs):
        commands.append((args, kwargs))
        process = (_Process(output=payload, code=dump_code) if args[0] == "pg_dump"
                   else _Process(target=target))
        processes.append(process)
        return process

    monkeypatch.setenv("BACKUP_DRILL_ENABLED", "true")
    monkeypatch.setattr(drill.asyncpg, "connect", connect)
    monkeypatch.setattr(drill, "_spawn", spawn)
    return source, target, source_url, target_url, password, payload, commands, processes


@pytest.mark.asyncio
async def test_drill_encrypts_and_restores_only_to_isolated_empty_database(tmp_path, monkeypatch):
    source, target, source_url, target_url, password, payload, commands, processes = _install_fakes(monkeypatch)
    key = secrets.token_bytes(32)
    result = await drill.run_backup_restore_drill(source_url, target_url, str(tmp_path), encryption_key=key)

    assert result.ok and result.traffic_authorized is False
    assert result.erasure_reconciliation == "required"
    assert result.external_copies_certified is False
    assert result.detail == "encrypted_snapshot_restored_to_quarantine"
    archive = Path(result.backup_path)
    assert archive.suffix == ".enc" and payload[:100] not in archive.read_bytes()
    assert b"".join(drill._archive_chunks(io.BytesIO(archive.read_bytes()), key)) == payload
    if os.name == "posix":
        assert archive.stat().st_mode & 0o077 == 0
    assert len(list(tmp_path.iterdir())) == 1
    assert bytes(processes[1].stdin.contents) == payload
    assert [command[0][0] for command in commands] == ["pg_dump", "pg_restore"]
    for args, kwargs in commands:
        assert password not in " ".join(args)
        assert source_url not in " ".join(args) and target_url not in " ".join(args)
        assert "--clean" not in args and "--if-exists" not in args
        assert kwargs["stderr"] == asyncio.subprocess.DEVNULL
        assert kwargs["env"]["PGPASSWORD"] == password
    assert source.closed and target.closed


@pytest.mark.asyncio
async def test_same_database_is_rejected_even_with_distinct_urls(tmp_path, monkeypatch):
    name = "melpis_restore_drill_" + secrets.token_hex(8)
    source, target, source_url, target_url, _, _, commands, _ = _install_fakes(
        monkeypatch, source=_Connection(name, source=True), target=_Connection(name),
    )
    target_url = target_url.replace("127.0.0.1", "localhost")
    async def connect(url, **_):
        return source if url == source_url else target
    monkeypatch.setattr(drill.asyncpg, "connect", connect)
    with pytest.raises(drill.BackupDrillError, match="target_is_source"):
        await drill.run_backup_restore_drill(source_url, target_url, str(tmp_path), encryption_key=secrets.token_bytes(32))
    assert not commands and not list(tmp_path.iterdir())
    assert source.closed and target.closed


@pytest.mark.parametrize("unsafe", ["name", "owner", "role", "acl", "readonly", "other", "occupied", "locked"])
@pytest.mark.asyncio
async def test_unsafe_target_never_starts_dump_or_mutates_target(tmp_path, monkeypatch, unsafe):
    source, target, source_url, target_url, _, _, commands, _ = _install_fakes(monkeypatch)
    if unsafe == "name":
        target.name = "customer_database"
        target_url = _url(target.name, secrets.token_urlsafe(24))
        # The fake connection maps the exact configured target URL.
        async def connect(url, **_):
            return source if url == source_url else target
        monkeypatch.setattr(drill.asyncpg, "connect", connect)
    elif unsafe == "owner":
        target.owned = False
    elif unsafe == "role":
        target.restricted = False
    elif unsafe == "acl":
        target.private = False
    elif unsafe == "readonly":
        target.writable = False
    elif unsafe == "other":
        target.other_clients = 1
    elif unsafe == "occupied":
        target.occupied = True
    else:
        target.locked = False
    with pytest.raises(drill.BackupDrillError):
        await drill.run_backup_restore_drill(source_url, target_url, str(tmp_path), encryption_key=secrets.token_bytes(32))
    assert not commands and not list(tmp_path.iterdir())
    assert source.closed and target.closed


@pytest.mark.asyncio
async def test_target_that_changes_after_dump_is_not_restored(tmp_path, monkeypatch):
    _, target, source_url, target_url, _, payload, commands, _ = _install_fakes(monkeypatch)
    target.occupy_after_first_check = True
    with pytest.raises(drill.BackupDrillError, match="target_not_empty"):
        await drill.run_backup_restore_drill(source_url, target_url, str(tmp_path), encryption_key=secrets.token_bytes(32))
    assert [command[0][0] for command in commands] == ["pg_dump"]
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and payload[:100] not in files[0].read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["extension_mismatch", "unsupported_extension", "foreign_table"])
async def test_unverifiable_source_or_target_never_starts_dump(tmp_path, monkeypatch, problem):
    source, target, source_url, target_url, _, _, commands, _ = _install_fakes(monkeypatch)
    if problem == "extension_mismatch":
        target.extensions.append(("vector", "0.8.1", "public"))
    elif problem == "unsupported_extension":
        source.extensions.append(("unreviewed_extension", "1.0", "public"))
    else:
        source.foreign_tables = True
    with pytest.raises(drill.BackupDrillError):
        await drill.run_backup_restore_drill(source_url, target_url, str(tmp_path), encryption_key=secrets.token_bytes(32))
    assert not commands and not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_authenticated_archive_rejects_tampering_before_restore(monkeypatch):
    key = secrets.token_bytes(32)
    valid = _archive(b"synthetic archive payload", key)
    altered = bytearray(valid)
    altered[len(drill._MAGIC) + drill._SALT_BYTES + drill._RECORD_HEADER.size + 1] ^= 1
    called = []

    async def spawn(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("restore must not start")

    monkeypatch.setattr(drill, "_spawn", spawn)
    spec = drill._connection_spec(_url("melpis_restore_drill_" + secrets.token_hex(8), secrets.token_urlsafe(24)))
    for invalid, candidate_key in ((bytes(altered), key), (valid[:-1], key),
                                   (valid + b"x", key), (valid, secrets.token_bytes(32))):
        with pytest.raises(drill.BackupDrillError, match="archive_invalid"):
            await drill._restore_archive(io.BytesIO(invalid), candidate_key, spec)
    assert not called


@pytest.mark.asyncio
async def test_disabled_or_invalid_key_fails_before_any_connection(tmp_path, monkeypatch):
    calls = []
    async def connect(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("no connection permitted")
    monkeypatch.setattr(drill.asyncpg, "connect", connect)
    monkeypatch.delenv("BACKUP_DRILL_ENABLED", raising=False)
    with pytest.raises(drill.BackupDrillError, match="disabled"):
        await drill.run_backup_restore_drill("unused", "unused", str(tmp_path))
    monkeypatch.setenv("BACKUP_DRILL_ENABLED", "true")
    monkeypatch.setenv("BACKUP_DRILL_ENCRYPTION_KEY", "invalid")
    with pytest.raises(drill.BackupDrillError, match="key_invalid"):
        await drill.run_backup_restore_drill("unused", "unused", str(tmp_path))
    assert not calls and not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_database_failure_is_sanitized(tmp_path, monkeypatch, caplog):
    marker = secrets.token_urlsafe(32)
    async def connect(*_, **__):
        raise RuntimeError(marker)
    monkeypatch.setattr(drill.asyncpg, "connect", connect)
    monkeypatch.setenv("BACKUP_DRILL_ENABLED", "true")
    with pytest.raises(drill.BackupDrillError) as caught:
        await drill.run_backup_restore_drill(_url("source", marker),
            _url("melpis_restore_drill_" + secrets.token_hex(8), marker),
            str(tmp_path), encryption_key=secrets.token_bytes(32))
    assert marker not in str(caught.value) and marker not in caplog.text
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
async def test_restore_count_mismatch_is_failure_not_success(tmp_path, monkeypatch):
    _, target, source_url, target_url, _, _, _, _ = _install_fakes(monkeypatch)
    target.mismatch = True
    with pytest.raises(drill.BackupDrillError, match="restore_verification_failed"):
        await drill.run_backup_restore_drill(source_url, target_url, str(tmp_path), encryption_key=secrets.token_bytes(32))


@pytest.mark.asyncio
async def test_dump_failure_is_sanitized_and_never_restores(tmp_path, monkeypatch, caplog):
    _, _, source_url, target_url, password, payload, commands, _ = _install_fakes(monkeypatch, dump_code=1)
    with pytest.raises(drill.BackupDrillError, match="dump_failed") as caught:
        await drill.run_backup_restore_drill(source_url, target_url, str(tmp_path), encryption_key=secrets.token_bytes(32))
    assert [command[0][0] for command in commands] == ["pg_dump"]
    assert password not in str(caught.value) and password not in caplog.text
    assert all(payload[:100] not in path.read_bytes() for path in tmp_path.iterdir())


def test_connection_parser_rejects_ambiguous_options_without_echoing_input():
    password = secrets.token_urlsafe(24)
    for query in ("sslmode=require&sslmode=disable", "options=unsafe", "unknown=secret"):
        with pytest.raises(drill.BackupDrillError) as caught:
            drill._connection_spec(_url("source", password) + "?" + query)
        assert password not in str(caught.value)
    with pytest.raises(drill.BackupDrillError):
        drill._connection_spec("postgresql://synthetic_user@127.0.0.1:5432/source")
