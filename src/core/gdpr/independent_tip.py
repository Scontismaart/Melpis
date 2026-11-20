"""Durable operator-side tip authority, deliberately outside the journal store.

The file belongs on the independent operator's protected, backed-up medium;
opening a copy from the application/database/bucket cannot establish currency.
Never bootstrap a missing production authority after a loss.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat
from uuid import UUID

from src.core.gdpr.journal_publication import (
    CheckpointPin, _checkpoint_name, _require_cumulative, read_checkpoint, read_segment,
)
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_journal import JournalCheckpoint, RestoreJournalInvalid, _checkpoint
from src.core.gdpr.restore_policy_state import PolicyStatePin, _authenticate as authenticate_policy_state


_GENESIS = JournalCheckpoint(0, "0" * 64)


class IndependentTipStore:
    """Single protected authority with serialized compare-and-set advancement."""

    def __init__(self, path: Path, installation_id: UUID):
        self._validate_location(path, installation_id)
        self._path = path
        self.installation_id = installation_id
        # Do not create on open or silently heal a missing/corrupt authority.
        self.current()

    @staticmethod
    def _validate_location(path, installation_id):
        try:
            if (type(installation_id) is not UUID or not installation_id.int
                    or not isinstance(path, Path) or not path.is_absolute()
                    or path.parent.resolve(strict=True) != path.parent
                    or path.is_symlink()
                    or (os.name != "nt" and (
                        path.parent.stat().st_uid != os.geteuid()
                        or path.parent.stat().st_mode & 0o077))):
                raise ValueError
            if path.exists() and (not stat.S_ISREG(path.lstat().st_mode)
                                  or (os.name != "nt" and (
                                      path.stat().st_uid != os.geteuid()
                                      or path.stat().st_mode & 0o077))):
                raise ValueError
        except (OSError, ValueError):
            raise RestoreJournalInvalid("independent_authority_unavailable") from None

    @classmethod
    def initialize(cls, path: Path, installation_id: UUID):
        """Explicit commissioning only, never an implicit recovery fallback."""
        cls._validate_location(path, installation_id)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA synchronous=FULL")
                with connection:
                    connection.execute("CREATE TABLE authority (id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, digest TEXT NOT NULL)")
                    connection.execute("INSERT INTO authority VALUES (?, ?, ?)", (
                        str(installation_id), _GENESIS.sequence, _GENESIS.sha256))
                    connection.execute("""CREATE TABLE segments (
                        sequence INTEGER PRIMARY KEY, digest TEXT NOT NULL,
                        previous_sequence INTEGER NOT NULL, previous_digest TEXT NOT NULL)""")
                    connection.execute("""CREATE TABLE operations (
                        org TEXT NOT NULL, operation TEXT NOT NULL, PRIMARY KEY(org, operation))""")
                    connection.execute("""CREATE TABLE checkpoints (
                        org TEXT NOT NULL, backup TEXT NOT NULL, sequence INTEGER NOT NULL,
                        digest TEXT NOT NULL, PRIMARY KEY(org, backup))""")
                    connection.execute("""CREATE TABLE policy_pins (
                        org TEXT NOT NULL, backup TEXT NOT NULL, sequence INTEGER NOT NULL,
                        digest TEXT NOT NULL, PRIMARY KEY(org, backup))""")
            finally:
                connection.close()
            if os.name != "nt":
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except (OSError, sqlite3.Error):
            raise RestoreJournalInvalid("independent_authority_initialization_failed") from None
        return cls(path, installation_id)

    @contextmanager
    def _connection(self):
        self._validate_location(self._path, self.installation_id)
        conn = None
        try:
            conn = sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True, timeout=5)
            conn.execute("PRAGMA synchronous=FULL")
            with conn:
                yield conn
        except (OSError, sqlite3.Error):
            raise RestoreJournalInvalid("independent_authority_unavailable") from None
        finally:
            if conn is not None:
                conn.close()

    def _current(self, conn):
        rows = conn.execute("SELECT id, sequence, digest FROM authority").fetchall()
        if len(rows) != 1 or rows[0][0] != str(self.installation_id):
            raise RestoreJournalInvalid("independent_authority_invalid")
        tip = JournalCheckpoint(*rows[0][1:])
        if not _checkpoint(tip):
            raise RestoreJournalInvalid("independent_authority_invalid")
        return tip

    def current(self):
        with self._connection() as conn:
            return self._current(conn)

    def chain_after(self, backup_checkpoint):
        """Return the complete index from an exact backup anchor through current."""
        with self._connection() as conn:
            conn.execute("BEGIN")
            current = self._current(conn)
            prev, result, found = _GENESIS, [], backup_checkpoint == _GENESIS
            for sequence, digest, previous_sequence, previous_digest in conn.execute(
                    "SELECT sequence, digest, previous_sequence, previous_digest FROM segments ORDER BY sequence"):
                tip = JournalCheckpoint(sequence, digest)
                if (not _checkpoint(tip) or previous_sequence != prev.sequence
                        or previous_digest != prev.sha256 or sequence <= prev.sequence):
                    raise RestoreJournalInvalid("independent_authority_invalid")
                if found:
                    result.append(tip)
                elif backup_checkpoint == tip:
                    found = True
                prev = tip
            if not found or prev != current:
                raise RestoreJournalInvalid("independent_authority_invalid")
            return tuple(result)

    def advance(self, store, previous_tip, candidate, *, trusted_public_key, encryption_key):
        """Advance only after exact readback, with atomic CAS and uniqueness."""
        entries = read_segment(store, self.installation_id, previous_tip, candidate,
                               trusted_public_key=trusted_public_key, encryption_key=encryption_key)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._current(conn)
            if current != previous_tip:
                if current == candidate and conn.execute(
                        "SELECT 1 FROM segments WHERE sequence=? AND digest=? AND previous_sequence=? AND previous_digest=?",
                        (candidate.sequence, candidate.sha256, previous_tip.sequence,
                         previous_tip.sha256)).fetchone():
                    return current
                raise RestoreJournalInvalid("independent_authority_conflict")
            for entry in entries:
                conn.execute("INSERT INTO operations VALUES (?, ?)",
                             (str(entry.organization_id), str(entry.operation_id)))
            conn.execute("INSERT INTO segments VALUES (?, ?, ?, ?)",
                         (candidate.sequence, candidate.sha256, previous_tip.sequence, previous_tip.sha256))
            conn.execute("UPDATE authority SET sequence=?, digest=? WHERE id=?",
                         (candidate.sequence, candidate.sha256, str(self.installation_id)))
        return candidate

    def _checkpoint(self, conn, org, backup):
        row = conn.execute(
            "SELECT sequence, digest FROM checkpoints WHERE org=? AND backup=?",
            (str(org), backup)).fetchone()
        if row is None:
            return None
        pin = CheckpointPin(org, backup, *row)
        _checkpoint_name(self.installation_id, pin)
        return pin

    def checkpoint_pin(self, organization_id, backup_id):
        with self._connection() as conn:
            self._current(conn)
            pin = self._checkpoint(conn, organization_id, backup_id)
            if pin is None:
                raise RestoreBlocked("Independent checkpoint pin unavailable")
            return pin

    def advance_checkpoint(self, store, previous_pin, candidate, *, verification_key, encryption_key):
        """Commit exact, cumulative checkpoint pin via CAS after verified readback."""
        _checkpoint_name(self.installation_id, candidate)
        org, backup = candidate.organization_id, candidate.backup_id
        args = dict(verification_key=verification_key, encryption_key=encryption_key)
        current_payload = read_checkpoint(
            store, self.installation_id, org, backup, candidate, **args)["payload"]
        if previous_pin is not None:
            previous_payload = read_checkpoint(
                store, self.installation_id, org, backup, previous_pin, **args)["payload"]
            _require_cumulative(previous_payload, previous_pin, current_payload, candidate)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._current(conn)
            current = self._checkpoint(conn, org, backup)
            if current != previous_pin:
                if current == candidate:
                    return current
                raise RestoreBlocked("Independent checkpoint pin conflict")
            conn.execute("""INSERT INTO checkpoints VALUES (?, ?, ?, ?)
                ON CONFLICT(org, backup) DO UPDATE SET sequence=excluded.sequence, digest=excluded.digest""",
                (str(org), backup, candidate.sequence, candidate.sha256))
        return candidate

    def _policy_name(self, pin):
        if type(pin) is not PolicyStatePin or pin.installation_id != self.installation_id:
            raise RestoreBlocked("Independent policy pin required")
        pin_as_checkpoint = CheckpointPin(pin.organization_id, pin.backup_id, pin.sequence, pin.sha256)
        return _checkpoint_name(self.installation_id, pin_as_checkpoint).replace("checkpoint-", "policy-", 1)

    def _policy_pin(self, conn, org, backup):
        row = conn.execute(
            "SELECT sequence, digest FROM policy_pins WHERE org=? AND backup=?",
            (str(org), backup)).fetchone()
        if row is None:
            return None
        pin = PolicyStatePin(self.installation_id, org, backup, *row)
        self._policy_name(pin)
        return pin

    def policy_pin(self, org, backup):
        with self._connection() as conn:
            self._current(conn)
            pin = self._policy_pin(conn, org, backup)
            if pin is None:
                raise RestoreBlocked("Independent policy pin unavailable")
            return pin

    def read_policy(self, store, pin, *, verification_key, encryption_key):
        sealed = store.read(self._policy_name(pin))
        data = authenticate_policy_state(
            pin.organization_id, sealed, installation_id=self.installation_id, backup_id=pin.backup_id,
            independent_pin=pin, verification_key=verification_key, encryption_key=encryption_key)
        return sealed, data

    def publish_policy(self, store, previous_pin, candidate, sealed, *, verification_key, encryption_key):
        """Publish exact readback and atomically pin append-only policy state."""
        name = self._policy_name(candidate)
        org, backup = candidate.organization_id, candidate.backup_id
        args = dict(verification_key=verification_key, encryption_key=encryption_key)
        authenticate_policy_state(
            org, sealed, installation_id=self.installation_id, backup_id=backup,
            independent_pin=candidate, **args)
        store.put_once(name, sealed)
        _, (layout, latest) = self.read_policy(store, candidate, **args)
        if previous_pin is not None:
            if (type(previous_pin) is not PolicyStatePin
                    or (previous_pin.organization_id, previous_pin.backup_id) != (org, backup)
                    or candidate.sequence < previous_pin.sequence
                    or (candidate.sequence == previous_pin.sequence and candidate != previous_pin)):
                raise RestoreBlocked("Policy evidence sequence cannot regress")
            _, (old_layout, old_rows) = self.read_policy(store, previous_pin, **args)
            if old_layout != layout or any(
                    latest[table].get(key) != row for table, records in old_rows.items()
                    for key, row in records.items()):
                raise RestoreBlocked("Policy evidence omits or changes prior decisions")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._current(conn)
            current = self._policy_pin(conn, org, backup)
            if current != previous_pin:
                if current == candidate:
                    return current
                raise RestoreBlocked("Independent policy pin conflict")
            conn.execute("""INSERT INTO policy_pins VALUES (?, ?, ?, ?)
                ON CONFLICT(org, backup) DO UPDATE SET sequence=excluded.sequence, digest=excluded.digest""",
                (str(org), backup, candidate.sequence, candidate.sha256))
        return candidate
