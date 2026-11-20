"""Additive authenticated packing of a bounded, independently indexed prefix."""

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
from uuid import UUID

from cryptography.fernet import InvalidToken

from src.core.gdpr.backup_coverage import _backups, _GENESIS
from src.core.gdpr.journal_publication import (
    _canonical, _checkpoint_keys, _name, _resolve_key_epochs, read_segment,
)
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_journal import (
    JournalCheckpoint, RestoreJournalInvalid, _checkpoint, _hex, _unique_object,
)


_MANIFEST = "gdpr-journal-archive-v1"
_PACK = "gdpr-journal-archive-pack-v1"
_DOMAINS = {
    _MANIFEST: b"melpis.restore.journal-archive.manifest.v1\0",
    _PACK: b"melpis.restore.journal-archive.pack.v1\0",
}
_MAX_PLAINTEXT = 2 * 1024 * 1024
_MAX_SEALED = 3 * 1024 * 1024
_MAX_SEGMENT_BYTES = 2 * 1024 * 1024
_MAX_PACKS = 256
_MAX_PREFIX_SEGMENTS = 10_000
_MAX_SEGMENTS_PER_PACK = 128


@dataclass(frozen=True, slots=True)
class ArchivePin:
    """Exact manifest identity. The operator must attest it independently."""

    installation_id: UUID
    floor: JournalCheckpoint
    inventory_sha256: str
    sha256: str


def _checkpoint_payload(tip):
    return {"sequence": tip.sequence, "sha256": tip.sha256}


def _context(authority, inventory_bytes, inventory_digest, audit_at, public_key, journal_key,
             key_epochs, archive_verification_key, archive_encryption_key):
    installation = authority.installation_id
    if type(installation) is not UUID or not installation.int:
        raise ValueError
    backups = list(_backups(
        inventory_bytes, inventory_digest, installation,
        datetime.now(timezone.utc) if audit_at is None else audit_at))
    current = authority.current()
    for anchor in {row[2] for row in backups}:
        authority.chain_page_after(anchor, current, max_segments=1)
    # Overdue backups and documented exceptions remain part of the inventory
    # while they are recoverable. Sequence, rather than completion date,
    # determines the earliest independently verified recovery anchor.
    floor = min((row[2] for row in backups), key=lambda anchor: anchor.sequence)
    if floor == _GENESIS:
        raise RestoreJournalInvalid("archive_requires_nonempty_recovery_prefix")
    authority.validate_segment_schedule(current, public_key, journal_key, key_epochs)
    epochs = _resolve_key_epochs(key_epochs, public_key, journal_key, current)
    cipher = _checkpoint_keys(archive_verification_key, archive_encryption_key)
    wrapping_key = base64.urlsafe_b64decode(archive_encryption_key)
    for epoch in epochs:
        key = base64.urlsafe_b64decode(epoch.encryption_key)
        if hmac.compare_digest(key, wrapping_key) or hmac.compare_digest(key, archive_verification_key):
            raise RestoreJournalInvalid("archive_keys_must_be_distinct")
    scope = {
        "installation_id": str(installation),
        "floor": _checkpoint_payload(floor),
        "inventory_sha256": inventory_digest,
    }
    return current, floor, scope, epochs, cipher


def _prefix(authority, current, floor):
    previous, count = _GENESIS, 0
    while previous != floor:
        page = authority.chain_page_after(previous, current, max_segments=100)
        if not page:
            raise ValueError
        for tip in page:
            count += 1
            if (not _checkpoint(tip) or not previous.sequence < tip.sequence <= floor.sequence
                    or count > _MAX_PREFIX_SEGMENTS):
                raise ValueError
            yield previous, tip
            previous = tip
            if tip == floor:
                return
            if tip.sequence == floor.sequence:
                raise ValueError


def _object_name(installation, floor, digest):
    # This content-addressed shape is already accepted by both JournalStore
    # implementations. Domains distinguish archive objects from segments.
    return _name(installation, JournalCheckpoint(floor.sequence, digest))


def _read_object(store, name, digest, version, key, cipher):
    sealed = store.read(name)
    if type(sealed) is not bytes or not 1 <= len(sealed) <= _MAX_SEALED:
        raise ValueError
    envelope = json.loads(cipher.decrypt(sealed), object_pairs_hook=_unique_object)
    if type(envelope) is not dict or set(envelope) != {"payload", "signature"}:
        raise ValueError
    payload, signature = envelope["payload"], envelope["signature"]
    if type(payload) is not dict or payload.get("version") != version or not _hex(signature, 64):
        raise ValueError
    raw = _canonical(payload)
    if (not 1 <= len(raw) <= _MAX_PLAINTEXT
            or not hmac.compare_digest(sha256(raw).hexdigest(), digest)
            or not hmac.compare_digest(hmac.new(key, _DOMAINS[version] + raw, sha256).hexdigest(), signature)):
        raise ValueError
    return payload


def _publish_object(store, installation, floor, payload, key, cipher):
    raw = _canonical(payload)
    if not 1 <= len(raw) <= _MAX_PLAINTEXT:
        raise ValueError
    digest = sha256(raw).hexdigest()
    version = payload["version"]
    signature = hmac.new(key, _DOMAINS[version] + raw, sha256).hexdigest()
    sealed = cipher.encrypt(_canonical({"payload": payload, "signature": signature}))
    if len(sealed) > _MAX_SEALED:
        raise ValueError
    name = _object_name(installation, floor, digest)
    store.put_once(name, sealed)
    if _read_object(store, name, digest, version, key, cipher) != payload:
        raise ValueError
    return digest


class _SingleCiphertext:
    """Give the existing segment verifier exactly the bytes destined for the pack."""

    def __init__(self, name, ciphertext):
        self._name = name
        self._ciphertext = ciphertext

    def read(self, name):
        if name != self._name:
            raise ValueError
        return self._ciphertext


def _check_segment(installation, previous, tip, ciphertext, epoch):
    if type(ciphertext) is not bytes or not 1 <= len(ciphertext) <= _MAX_SEGMENT_BYTES:
        raise ValueError
    single = _SingleCiphertext(_name(installation, tip), ciphertext)
    read_segment(single, installation, previous, tip, trusted_public_key=epoch.public_key,
                 encryption_key=epoch.encryption_key)


def _epoch_for(epochs, index, previous, tip):
    while index + 1 < len(epochs) and epochs[index + 1].starts_after.sequence <= previous.sequence:
        index += 1
    boundary = epochs[index].starts_after
    if boundary.sequence == previous.sequence and boundary != previous:
        raise ValueError
    if index + 1 < len(epochs):
        following = epochs[index + 1].starts_after
        if following.sequence <= tip.sequence and following != tip:
            raise ValueError
    return index


def publish_archive_prefix(source, archive, authority, inventory_bytes, *, latest_inventory_sha256,
                           trusted_public_key, journal_encryption_key, archive_verification_key,
                           archive_encryption_key, key_epochs=None, audit_at=None,
                           max_segments_per_pack=_MAX_SEGMENTS_PER_PACK):
    """Publish immutable packs first. Return a candidate only after exact readback."""
    try:
        if type(max_segments_per_pack) is not int or not 1 <= max_segments_per_pack <= _MAX_SEGMENTS_PER_PACK:
            raise ValueError
        key = archive_verification_key
        current, floor, scope, epochs, cipher = _context(
            authority, inventory_bytes, latest_inventory_sha256, audit_at, trusted_public_key,
            journal_encryption_key, key_epochs, key, archive_encryption_key)
        installation = authority.installation_id
        descriptors, records, epoch_index = [], [], 0

        def pack():
            return dict(scope, version=_PACK, index=len(descriptors), segments=records)

        def flush():
            if len(descriptors) >= _MAX_PACKS:
                raise ValueError
            digest = _publish_object(archive, installation, floor, pack(), key, cipher)
            descriptors.append({"index": len(descriptors), "count": len(records),
                                "sha256": digest})
            records.clear()

        size = len(_canonical(pack()))
        for previous, tip in _prefix(authority, current, floor):
            ciphertext = source.read(_name(installation, tip))
            epoch_index = _epoch_for(epochs, epoch_index, previous, tip)
            _check_segment(installation, previous, tip, ciphertext, epochs[epoch_index])
            row = dict(_checkpoint_payload(tip), ciphertext=base64.b64encode(ciphertext).decode("ascii"))
            row_size = len(_canonical(row)) + 1
            if records and (len(records) >= max_segments_per_pack or size + row_size > _MAX_PLAINTEXT):
                flush()
                size = len(_canonical(pack()))
            if size + row_size > _MAX_PLAINTEXT:
                raise ValueError
            records.append(row)
            size += row_size
        if records:
            flush()
        if not descriptors or authority.current() != current:
            raise ValueError
        authority.validate_segment_schedule(current, trusted_public_key, journal_encryption_key, key_epochs)
        manifest = dict(scope, version=_MANIFEST, packs=descriptors)
        digest = _publish_object(archive, installation, floor, manifest, key, cipher)
        pin = ArchivePin(installation, floor, latest_inventory_sha256, digest)
        open_archived_journal(
            source, archive, authority, inventory_bytes, pin,
            latest_inventory_sha256=latest_inventory_sha256, audit_at=audit_at,
            trusted_public_key=trusted_public_key, journal_encryption_key=journal_encryption_key,
            key_epochs=key_epochs, archive_verification_key=key, archive_encryption_key=archive_encryption_key)
        if authority.current() != current:
            raise ValueError
        return pin
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, OSError, InvalidToken):
        raise RestoreJournalInvalid("journal_archive_publication_failed") from None


def _load_pack(archive, installation, floor, scope, descriptor, key, cipher):
    digest = descriptor["sha256"]
    pack = _read_object(archive, _object_name(installation, floor, digest), digest, _PACK, key, cipher)
    if (set(pack) != scope.keys() | {"version", "index", "segments"}
            or any(pack[field] != value for field, value in scope.items())
            or type(pack["index"]) is not int or pack["index"] != descriptor["index"]
            or type(pack["segments"]) is not list or len(pack["segments"]) != descriptor["count"]):
        raise ValueError
    return pack["segments"]


def _segment_ciphertext(record):
    if type(record) is not dict or set(record) != {"sequence", "sha256", "ciphertext"}:
        raise ValueError
    tip = JournalCheckpoint(record["sequence"], record["sha256"])
    if not _checkpoint(tip) or type(record["ciphertext"]) is not str:
        raise ValueError
    data = base64.b64decode(record["ciphertext"], validate=True)
    if base64.b64encode(data).decode("ascii") != record["ciphertext"]:
        raise ValueError
    return tip, data


class ArchivedJournalStore:
    """Read-only JournalStore view using a separately attested prefix."""

    def __init__(self, source, archive, pin, scope, index, key, cipher):
        self._source = source
        self._archive = archive
        self._pin = pin
        self._scope = scope
        self._index = index
        self._key = key
        self._cipher = cipher
        self._assert_current = None

    def put_once(self, name, ciphertext):
        raise RestoreJournalInvalid("journal_archive_is_read_only")

    def read(self, name):
        if self._assert_current is not None:
            self._assert_current()
        if name not in self._index:
            return self._source.read(name)
        descriptor, offset, expected = self._index[name]
        try:
            rows = _load_pack(self._archive, self._pin.installation_id, self._pin.floor,
                              self._scope, descriptor, self._key, self._cipher)
            tip, data = _segment_ciphertext(rows[offset])
            if tip != expected:
                raise ValueError
            return data
        except (KeyError, TypeError, ValueError, IndexError, RecursionError, OSError, InvalidToken):
            raise RestoreJournalInvalid("archived_journal_segment_unavailable") from None


def assert_registered_archive_reader(authority, store):
    """Keep authoritative readers on the exact registered archive after activation."""
    from src.core.gdpr.independent_tip import IndependentTipStore

    if not isinstance(authority, IndependentTipStore):
        return  # Non-persistent verifier adapters have no archive registry.
    _, _, protected, current = authority.archive_state()
    if current is None:
        return
    if type(store) is not ArchivedJournalStore or store._pin != current or store._assert_current is None:
        raise RestoreJournalInvalid("active_archive_reader_required")
    authority.assert_archive_pin(current, protected)
    store._assert_current()


def open_archived_journal(source, archive, authority, inventory_bytes, independently_attested_pin, *,
                          latest_inventory_sha256, trusted_public_key, journal_encryption_key,
                          archive_verification_key, archive_encryption_key, key_epochs=None, audit_at=None):
    """Authenticate the exact pinned archive against current inventory and authority."""
    try:
        key = archive_verification_key
        current, floor, scope, epochs, cipher = _context(
            authority, inventory_bytes, latest_inventory_sha256, audit_at, trusted_public_key,
            journal_encryption_key, key_epochs, key, archive_encryption_key)
        installation = authority.installation_id
        pin = independently_attested_pin
        if (type(pin) is not ArchivePin or not _checkpoint(pin.floor)
                or (pin.installation_id, pin.floor, pin.inventory_sha256)
                != (installation, floor, latest_inventory_sha256) or not _hex(pin.sha256, 64)):
            raise ValueError
        manifest = _read_object(archive, _object_name(installation, floor, pin.sha256),
                                pin.sha256, _MANIFEST, key, cipher)
        if (set(manifest) != scope.keys() | {"version", "packs"}
                or any(manifest[field] != value for field, value in scope.items())
                or type(manifest["packs"]) is not list or not 1 <= len(manifest["packs"]) <= _MAX_PACKS):
            raise ValueError
        expected = _prefix(authority, current, floor)
        index, seen, epoch_index = {}, set(), 0
        for ordinal, descriptor in enumerate(manifest["packs"]):
            if (type(descriptor) is not dict or set(descriptor) != {"index", "count", "sha256"}
                    or type(descriptor["index"]) is not int or descriptor["index"] != ordinal
                    or not _hex(descriptor["sha256"], 64) or descriptor["sha256"] in seen
                    or type(descriptor["count"]) is not int or not 1 <= descriptor["count"] <= _MAX_SEGMENTS_PER_PACK):
                raise ValueError
            seen.add(descriptor["sha256"])
            for offset, row in enumerate(_load_pack(
                    archive, installation, floor, scope, descriptor, key, cipher)):
                previous, tip = next(expected)
                packed_tip, ciphertext = _segment_ciphertext(row)
                if packed_tip != tip:
                    raise ValueError
                epoch_index = _epoch_for(epochs, epoch_index, previous, tip)
                _check_segment(installation, previous, tip, ciphertext, epochs[epoch_index])
                index[_name(installation, tip)] = (descriptor, offset, tip)
        if next(expected, None) is not None or authority.current() != current:
            raise ValueError
        authority.validate_segment_schedule(current, trusted_public_key, journal_encryption_key, key_epochs)
        return ArchivedJournalStore(source, archive, pin, scope, index, key, cipher)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError,
            StopIteration, OSError, InvalidToken):
        raise RestoreJournalInvalid("authenticated_journal_archive_required") from None


def _open_registered_archive(source, archive, authority, inventory_bytes, latest_inventory_sha256,
                             *, repack, **trust):
    try:
        expected = authority.archive_state()
        _, _, protected_digest, pin = expected
        if (pin is None or not _hex(protected_digest, 64) or protected_digest != latest_inventory_sha256
                or (not repack and pin.inventory_sha256 != protected_digest)):
            raise RestoreJournalInvalid("current_independent_archive_required")
        reader = open_archived_journal(source, archive, authority, inventory_bytes, pin,
                                      latest_inventory_sha256=pin.inventory_sha256, **trust)
        if authority.archive_state() != expected:
            raise RestoreJournalInvalid("independent_archive_authority_changed")
        reader._assert_current = lambda: authority.assert_archive_pin(pin, protected_digest)
        return reader
    except RestoreBlocked:
        raise RestoreJournalInvalid("current_independent_archive_required") from None


def open_active_archived_journal(source, archive, authority, inventory_bytes, *, latest_inventory_sha256, **trust):
    """Open only the current independently registered archive for this inventory."""
    return _open_registered_archive(source, archive, authority, inventory_bytes, latest_inventory_sha256,
                                    repack=False, **trust)


def open_archived_journal_for_repack(source, archive, authority, prior_inventory_bytes, *,
                                    latest_inventory_sha256, **trust):
    """Read the last registered prefix during operator repacking of a fresh inventory."""
    return _open_registered_archive(source, archive, authority, prior_inventory_bytes, latest_inventory_sha256,
                                    repack=True, **trust)
