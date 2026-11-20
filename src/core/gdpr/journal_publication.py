"""Operator-only publication of authenticated encrypted erasure segments.

The returned checkpoint is a *candidate*, not an authoritative tip. The
operator must commit it in a separate trusted system after readback, serializing
advancement against the previous tip. Never discover a tip from object listing.
"""

import json
import base64
import hmac
import os
import re
import stat
import tempfile
from hashlib import sha256
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from src.core.gdpr.restore_journal import (
    JournalCheckpoint, RestoreJournalInvalid, _checkpoint, _DOMAIN,
    _MAX_BYTES, _MAX_ENTRIES, verify_erasure_segment,
)
from src.core.gdpr.restore_checkpoint import RestoreBlocked, _authenticate_checkpoint

_MAX_SEALED = 2 * 1024 * 1024
_MAX_OBJECT = 3 * 1024 * 1024
_OBJECT_NAME = re.compile(
    r"(?:(?:checkpoint|policy)-[0-9a-f-]{36}-)?[0-9a-f-]{36}-[0-9]{1,19}-[0-9a-f]{64}\.fernet")


class JournalStore(Protocol):
    def put_once(self, name: str, ciphertext: bytes) -> None: ...
    def read(self, name: str) -> bytes: ...


class LocalJournalStore:
    """Reference store in a pre-provisioned, operator-controlled private directory.

    On Windows its directory ACL must be provisioned independently. A complete
    temporary object is fsynced before atomic no-overwrite publication via link.
    """

    def __init__(self, root: Path):
        try:
            if (not root.is_absolute() or root.resolve(strict=True) != root
                    or not root.is_dir() or (os.name != "nt" and (
                        root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077))):
                raise ValueError
        except (OSError, ValueError):
            raise RestoreJournalInvalid("journal_store_unavailable") from None
        self.root = root

    def _path(self, name):
        if not isinstance(name, str) or not _OBJECT_NAME.fullmatch(name):
            raise RestoreJournalInvalid("journal_object_name_invalid")
        path = self.root / name
        # Journal names contain installation/tenant UUIDs and a digest. On
        # Windows those names can exceed MAX_PATH even in a valid private
        # directory. Use the native absolute extended-length path; the
        # canonical root and strict object name were checked above.
        if os.name == "nt":
            value = str(path)
            if not value.startswith("\\\\?\\"):
                value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
            path = Path(value)
        if path.is_symlink():
            raise RestoreJournalInvalid("journal_object_invalid")
        return path

    def put_once(self, name, ciphertext):
        path = self._path(name)
        if type(ciphertext) is not bytes or not 1 <= len(ciphertext) <= _MAX_OBJECT:
            raise RestoreJournalInvalid("journal_object_invalid")
        temp = None
        try:
            fd, temp = tempfile.mkstemp(prefix=".pending-", dir=self.root)
            with os.fdopen(fd, "wb") as output:
                output.write(ciphertext)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temp, path)
            except FileExistsError:
                # The publisher MUST authenticate the existing object on readback.
                pass
            if os.name != "nt":
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except OSError:
            raise RestoreJournalInvalid("journal_publication_failed") from None
        finally:
            if temp is not None:
                try:
                    os.unlink(temp)
                except OSError:
                    pass  # Encrypted orphan; not an authoritative named object.

    def read(self, name):
        path = self._path(name)
        try:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError
            with path.open("rb") as source:
                data = source.read(_MAX_OBJECT + 1)
            if not 1 <= len(data) <= _MAX_OBJECT:
                raise ValueError
            return data
        except (OSError, ValueError):
            raise RestoreJournalInvalid("journal_object_unavailable") from None


class R2JournalStore:
    """Operator-injected S3 client; restricted to the dedicated EU journal bucket.

    No bucket creation, listing, deletion, credential discovery or tip operation.
    The caller must configure TLS and bucket-scoped credentials separately.
    """

    _BUCKET = "melpis-staging-gdpr-journal"
    _ENDPOINT = re.compile(r"https://[0-9a-f]{32}\.eu\.r2\.cloudflarestorage\.com/?")

    def __init__(self, client):
        endpoint = getattr(getattr(client, "meta", None), "endpoint_url", None)
        if not isinstance(endpoint, str) or not self._ENDPOINT.fullmatch(endpoint):
            raise RestoreJournalInvalid("journal_r2_endpoint_invalid")
        self._client = client

    @staticmethod
    def _validate_name(name):
        if not isinstance(name, str) or not _OBJECT_NAME.fullmatch(name):
            raise RestoreJournalInvalid("journal_object_name_invalid")

    def put_once(self, name, ciphertext):
        self._validate_name(name)
        if type(ciphertext) is not bytes or not 1 <= len(ciphertext) <= _MAX_OBJECT:
            raise RestoreJournalInvalid("journal_object_invalid")
        try:
            self._client.put_object(
                Bucket=self._BUCKET, Key=name, Body=ciphertext,
                IfNoneMatch="*", ContentType="application/octet-stream")
        except Exception:
            # A lost response and a precondition failure may both mean that an
            # immutable object exists. Only the mandatory exact authenticated
            # readback at the publication layer may resolve this uncertainty.
            pass

    def read(self, name):
        self._validate_name(name)
        response = None
        try:
            response = self._client.get_object(Bucket=self._BUCKET, Key=name)
            size = response["ContentLength"]
            if type(size) is not int or not 1 <= size <= _MAX_OBJECT:
                raise ValueError
            data = response["Body"].read(size + 1)
            if type(data) is not bytes or len(data) != size:
                raise ValueError
            return data
        except Exception:
            raise RestoreJournalInvalid("journal_object_unavailable") from None
        finally:
            if response is not None:
                try:
                    response["Body"].close()
                except Exception:
                    pass


def _name(installation_id, tip):
    if type(installation_id) is not UUID or not installation_id.int or not _checkpoint(tip):
        raise RestoreJournalInvalid("journal_bounds_invalid")
    return f"{installation_id}-{tip.sequence}-{tip.sha256}.fernet"


def publish_segment(store: JournalStore, installation_id, previous_tip, entries, *, signer, encryption_key):
    """Sign minimal operator-verified entries, publish once and verify readback."""
    if (not _checkpoint(previous_tip) or type(installation_id) is not UUID
            or not installation_id.int or type(entries) is not list
            or not 1 <= len(entries) <= _MAX_ENTRIES
            or not isinstance(signer, Ed25519PrivateKey)):
        raise RestoreJournalInvalid("journal_bounds_invalid")
    try:
        if any(type(record) is not dict or "sequence" in record for record in entries):
            raise ValueError
        payload = json.dumps(
            {"version": 1, "installation_id": str(installation_id),
             "previous_sha256": previous_tip.sha256,
             "entries": [dict(record, sequence=sequence)
                         for sequence, record in enumerate(entries, previous_tip.sequence + 1)]},
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()
        if len(payload) > _MAX_BYTES:
            raise ValueError
        if hmac.compare_digest(signer.private_bytes_raw(), base64.urlsafe_b64decode(encryption_key)):
            raise ValueError
        tip = JournalCheckpoint(previous_tip.sequence + len(entries), sha256(payload).hexdigest())
        signature = signer.sign(_DOMAIN + payload)
        verify_erasure_segment(payload, signature, installation_id=installation_id,
                               trusted_public_key=signer.public_key(),
                               backup_checkpoint=previous_tip, independent_tip=tip)
        sealed = Fernet(encryption_key).encrypt(signature + payload)
    except (ValueError, TypeError, RecursionError):
        raise RestoreJournalInvalid("journal_record_invalid") from None
    store.put_once(_name(installation_id, tip), sealed)
    read_segment(store, installation_id, previous_tip, tip,
                 trusted_public_key=signer.public_key(), encryption_key=encryption_key)
    return tip


def read_segment(store: JournalStore, installation_id, previous_tip, independent_tip, *,
                 trusted_public_key, encryption_key):
    """Authenticate the precise named object; never select an older valid artifact."""
    sealed = store.read(_name(installation_id, independent_tip))
    try:
        if type(sealed) is not bytes or not 1 <= len(sealed) <= _MAX_SEALED:
            raise ValueError
        plain = Fernet(encryption_key).decrypt(sealed)
        if not 64 < len(plain) <= 64 + _MAX_BYTES:
            raise ValueError
    except (ValueError, TypeError, InvalidToken):
        raise RestoreJournalInvalid("journal_object_invalid") from None
    return verify_erasure_segment(
        plain[64:], plain[:64], installation_id=installation_id,
        trusted_public_key=trusted_public_key, backup_checkpoint=previous_tip,
        independent_tip=independent_tip,
    )


@dataclass(frozen=True, slots=True)
class SegmentKeyEpoch:
    """Keys independently approved to verify segments after one exact boundary."""

    starts_after: JournalCheckpoint
    public_key: Ed25519PublicKey
    encryption_key: bytes = field(repr=False)


def read_chain(store, installation_id, backup_checkpoint, segment_tips, independent_tip,
               *, trusted_public_key, encryption_key, key_epochs=None):
    """Verify complete, contiguous coverage to the independently supplied tip.

    The operator supplies the ordered segment index. Missing index entries,
    duplicated operation IDs or stale history never constitute complete evidence.
    """
    if (type(installation_id) is not UUID or not installation_id.int
            or not _checkpoint(backup_checkpoint) or not _checkpoint(independent_tip)
            or type(segment_tips) not in (tuple, list) or len(segment_tips) > 10000
            or independent_tip.sequence < backup_checkpoint.sequence):
        raise RestoreJournalInvalid("journal_bounds_invalid")
    epochs = _resolve_key_epochs(key_epochs, trusted_public_key, encryption_key, independent_tip)
    epoch_index = 0
    while (epoch_index + 1 < len(epochs)
           and epochs[epoch_index + 1].starts_after.sequence <= backup_checkpoint.sequence):
        epoch_index += 1
    if (epochs[epoch_index].starts_after.sequence == backup_checkpoint.sequence
            and epochs[epoch_index].starts_after != backup_checkpoint):
        raise RestoreJournalInvalid("journal_key_boundary_mismatch")
    previous, entries, seen = backup_checkpoint, [], set()
    for tip in segment_tips:
        if not _checkpoint(tip):
            raise RestoreJournalInvalid("journal_bounds_invalid")
        epoch = epochs[epoch_index]
        next_epoch = epochs[epoch_index + 1] if epoch_index + 1 < len(epochs) else None
        if next_epoch is not None and tip.sequence >= next_epoch.starts_after.sequence:
            if tip != next_epoch.starts_after:
                raise RestoreJournalInvalid("journal_key_boundary_mismatch")
            epoch_index += 1
        result = read_segment(store, installation_id, previous, tip,
                              trusted_public_key=epoch.public_key,
                              encryption_key=epoch.encryption_key)
        for entry in result:
            identity = (entry.organization_id, entry.operation_id)
            if identity in seen or len(entries) >= 100000:
                raise RestoreJournalInvalid("journal_chain_invalid")
            seen.add(identity)
            entries.append(entry)
        previous = tip
    if previous != independent_tip:
        raise RestoreJournalInvalid("journal_chain_incomplete")
    return tuple(entries)


def _resolve_key_epochs(key_epochs, public_key, encryption_key, independent_tip):
    if key_epochs is None:
        return (SegmentKeyEpoch(JournalCheckpoint(0, "0" * 64), public_key, encryption_key),)
    if type(key_epochs) not in (tuple, list) or not 1 <= len(key_epochs) <= 1000:
        raise RestoreJournalInvalid("journal_key_epoch_invalid")
    last = -1
    for epoch in key_epochs:
        if (type(epoch) is not SegmentKeyEpoch or not _checkpoint(epoch.starts_after)
                or not last < epoch.starts_after.sequence <= independent_tip.sequence
                or (last == -1 and epoch.starts_after.sequence != 0)
                or not isinstance(epoch.public_key, Ed25519PublicKey)):
            raise RestoreJournalInvalid("journal_key_epoch_invalid")
        try:
            Fernet(epoch.encryption_key)
        except (TypeError, ValueError):
            raise RestoreJournalInvalid("journal_key_epoch_invalid") from None
        last = epoch.starts_after.sequence
    return tuple(key_epochs)


@dataclass(frozen=True, slots=True)
class CheckpointPin:
    organization_id: UUID
    backup_id: str
    sequence: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ArtifactKeyPair:
    verification_key: bytes = field(repr=False)
    encryption_key: bytes = field(repr=False)


def _previous_key_args(previous_pin, previous_keys, verification_key, encryption_key):
    if previous_keys is None:
        return dict(verification_key=verification_key, encryption_key=encryption_key)
    if previous_pin is None or type(previous_keys) is not ArtifactKeyPair:
        raise RestoreBlocked("Previous artifact pin and keys required")
    return dict(verification_key=previous_keys.verification_key, encryption_key=previous_keys.encryption_key)


def _checkpoint_name(installation_id, pin):
    if (type(installation_id) is not UUID or not installation_id.int
            or type(pin) is not CheckpointPin or type(pin.organization_id) is not UUID
            or not pin.organization_id.int or type(pin.backup_id) is not str
            or not 1 <= len(pin.backup_id) <= 128
            or type(pin.sequence) is not int or not 0 <= pin.sequence < 2**63
            or not isinstance(pin.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", pin.sha256)):
        raise RestoreBlocked("Independent checkpoint pin required")
    return f"checkpoint-{installation_id}-{pin.organization_id}-{pin.sequence}-{pin.sha256}.fernet"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _checkpoint_keys(verification_key, encryption_key):
    if (type(verification_key) is not bytes or len(verification_key) < 32
            or hmac.compare_digest(verification_key, base64.urlsafe_b64decode(encryption_key))):
        raise ValueError
    return Fernet(encryption_key)


def _require_cumulative(previous, previous_pin, current, pin):
    if pin.sequence < previous_pin.sequence or (
            pin.sequence == previous_pin.sequence and pin != previous_pin):
        raise RestoreBlocked("Checkpoint sequence cannot regress")
    for field in ("erasures", "stay_purges", "receipt_prunes"):
        if not {_canonical(item) for item in previous[field]} <= {
                _canonical(item) for item in current[field]}:
            raise RestoreBlocked("Checkpoint omits prior evidence")


def publish_checkpoint(store, installation_id, payload, *, verification_key, encryption_key,
                       previous_pin=None, previous_keys=None):
    """Authenticate a complete v3 checkpoint from operator-reconciled evidence."""
    try:
        if payload["version"] != "c24-restore-v3":
            raise ValueError
        cipher = _checkpoint_keys(verification_key, encryption_key)
        raw = _canonical(payload)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError
        pin = CheckpointPin(UUID(payload["organization_id"]), payload["backup_id"],
                            payload["sequence"], sha256(raw).hexdigest())
        checkpoint = {"payload": payload, "signature": hmac.new(
            verification_key, raw, sha256).hexdigest()}
        _authenticate_checkpoint(pin.organization_id, checkpoint,
                                 verification_key=verification_key, backup_id=pin.backup_id,
                                 minimum_sequence=pin.sequence, checkpoint_sha256=pin.sha256)
        previous_args = _previous_key_args(previous_pin, previous_keys, verification_key, encryption_key)
        if previous_pin is not None:
            previous = read_checkpoint(
                store, installation_id, pin.organization_id, pin.backup_id, previous_pin,
                **previous_args)["payload"]
            _require_cumulative(previous, previous_pin, payload, pin)
        sealed = cipher.encrypt(_canonical(checkpoint))
        if len(sealed) > _MAX_OBJECT:
            raise ValueError
        store.put_once(_checkpoint_name(installation_id, pin), sealed)
    except (KeyError, ValueError, TypeError, RecursionError):
        raise RestoreBlocked("Checkpoint publication failed") from None
    read_checkpoint(store, installation_id, pin.organization_id, pin.backup_id, pin,
                    verification_key=verification_key, encryption_key=encryption_key)
    return pin


def read_checkpoint(store, installation_id, organization_id, backup_id, pin, *,
                    verification_key, encryption_key):
    """Validate exact independent pin, authentication and encrypted readback."""
    from src.core.gdpr.restore_journal import _unique_object

    try:
        name = _checkpoint_name(installation_id, pin)
        if pin.organization_id != organization_id or pin.backup_id != backup_id:
            raise ValueError
        cipher = _checkpoint_keys(verification_key, encryption_key)
        sealed = store.read(name)
        if type(sealed) is not bytes or not 1 <= len(sealed) <= _MAX_OBJECT:
            raise ValueError
        checkpoint = json.loads(cipher.decrypt(sealed), object_pairs_hook=_unique_object)
        _, _, sequence, _, prunes = _authenticate_checkpoint(
            organization_id, checkpoint, verification_key=verification_key, backup_id=backup_id,
            minimum_sequence=pin.sequence, checkpoint_sha256=pin.sha256)
        if sequence != pin.sequence or prunes is None:
            raise ValueError
    except (ValueError, TypeError, InvalidToken, RecursionError):
        raise RestoreBlocked("Independent checkpoint unavailable") from None
    return checkpoint
