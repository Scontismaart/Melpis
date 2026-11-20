"""Operator-only v5 checkpoint objects with a bounded, authenticated page manifest.

The returned CheckpointPin is a candidate. Existing v3/v4 callers and the
authority must be upgraded before v5 can be used for source purge or restore.
This module publishes no authoritative pin and performs no database writes.
"""

from hashlib import sha256
import hmac
import json
from uuid import UUID

from cryptography.fernet import InvalidToken

from src.core.gdpr.journal_publication import (
    CheckpointPin, JournalStore, _canonical, _checkpoint_keys, _checkpoint_name,
    _previous_key_args, read_checkpoint,
)
from src.core.gdpr.restore_checkpoint import RestoreBlocked, _authenticate_checkpoint
from src.core.gdpr.restore_journal import RestoreJournalInvalid, _hex, _unique_object, _uuid


_VERSION = "c24-restore-v5"
_PAGE_VERSION = "c24-restore-v5-page"
_MANIFEST_DOMAIN = b"melpis.restore.checkpoint-v5.manifest\0"
_PAGE_DOMAIN = b"melpis.restore.checkpoint-v5.page\0"
_MAX_PAGE_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_OBJECT_BYTES = 3 * 1024 * 1024
_MAX_PAGE_RECORDS = 1000
_MAX_PAGES = 256
_MAX_RECORDS = 100_000
_MAX_AGGREGATE_BYTES = 64 * 1024 * 1024
_IDENTITIES = {
    "erasures": "operation_id",
    "stay_purges": "booking_id",
    "receipt_prunes": "request_id",
    "ordinary_booking_purges": "booking_id",
}
_SCOPE_FIELDS = {"version", "organization_id", "backup_id", "sequence", "complete"}
_DESCRIPTOR_FIELDS = {"category", "index", "count", "first", "last", "sha256", "bytes"}


def _scope(installation, org, backup, sequence):
    if (type(installation) is not UUID or not installation.int
            or type(org) is not UUID or not org.int
            or type(backup) is not str or not 1 <= len(backup) <= 128
            or type(sequence) is not int or not 0 <= sequence < 2**63):
        raise ValueError
    return {"installation_id": str(installation), "organization_id": str(org),
            "backup_id": backup, "sequence": sequence}


def _record_identity(category, item):
    if type(item) is not dict:
        raise ValueError
    return str(_uuid(item[_IDENTITIES[category]]))


def _normalized_records(category, records):
    if type(records) is not list:
        raise ValueError
    normalized = []
    for item in records:
        _record_identity(category, item)
        item = dict(item)
        if category in {"stay_purges", "ordinary_booking_purges"}:
            if type(item["request_ids"]) is not list:
                raise ValueError
            # Request IDs are sets in the v3/v4 contract. The successor uses a
            # single canonical ordering while retaining every exact UUID.
            ids = [str(_uuid(value)) for value in item["request_ids"]]
            if len(ids) != len(set(ids)):
                raise ValueError
            item["request_ids"] = sorted(ids)
        for field in ("subject_id", "operation_id", "source_message_id", "conversation_id"):
            if field in item and item[field] is not None:
                _uuid(item[field])
        normalized.append(item)
    return sorted(normalized, key=lambda item: _record_identity(category, item))


def _normalize(payload):
    if (type(payload) is not dict or set(payload) != _SCOPE_FIELDS | _IDENTITIES.keys()
            or payload["version"] != _VERSION or payload["complete"] is not True
            or any(type(payload[category]) is not list for category in _IDENTITIES)
            or sum(len(payload[category]) for category in _IDENTITIES) > _MAX_RECORDS):
        raise ValueError
    return dict(payload, **{
        category: _normalized_records(category, payload[category]) for category in _IDENTITIES
    })


def _assert_global_uniqueness(payload):
    bookings, purge_requests, receipt_sources = set(), set(), set()
    for category in _IDENTITIES:
        last = None
        for item in payload[category]:
            identity = _record_identity(category, item)
            if last is not None and identity <= last:
                raise ValueError
            last = identity
            if category == "receipt_prunes":
                source = str(_uuid(item["source_message_id"]))
                if source in receipt_sources:
                    raise ValueError
                receipt_sources.add(source)
            if category in {"stay_purges", "ordinary_booking_purges"}:
                requests = item["request_ids"]
                if (identity in bookings or requests != sorted(set(requests))
                        or purge_requests.intersection(requests)):
                    raise ValueError
                bookings.add(identity)
                purge_requests.update(requests)


def _check_page_records(org, scope, category, records, verification_key):
    # Reuse all strict v4 evidence field/cross-field validation on each small
    # page. Global identity checks additionally cover conflicts between pages
    # and between stay and ordinary-booking categories.
    payload = {
        "version": "c24-restore-v4", "organization_id": str(org),
        "backup_id": scope["backup_id"], "sequence": scope["sequence"], "complete": True,
        **{name: [] for name in _IDENTITIES},
    }
    payload[category] = records
    raw = _canonical(payload)
    _authenticate_checkpoint(
        org, {"payload": payload, "signature": hmac.new(verification_key, raw, sha256).hexdigest()},
        verification_key=verification_key, backup_id=scope["backup_id"],
        minimum_sequence=scope["sequence"], checkpoint_sha256=sha256(raw).hexdigest(),
    )
    if _normalized_records(category, records) != records:
        raise ValueError


def _page(scope, category, index, records):
    return dict(scope, version=_PAGE_VERSION, category=category, index=index, records=records)


def _pages(org, scope, payload, verification_key):
    pages, total_bytes = [], 0
    record_bytes_total = 0
    for category in _IDENTITIES:
        index, records = 0, []
        size = len(_canonical(_page(scope, category, index, [])))
        for item in payload[category]:
            record_bytes = len(_canonical(item)) + 1
            record_bytes_total += record_bytes
            if record_bytes_total > _MAX_AGGREGATE_BYTES:
                raise ValueError
            if records and (len(records) >= _MAX_PAGE_RECORDS or size + record_bytes > _MAX_PAGE_BYTES):
                if len(pages) >= _MAX_PAGES:
                    raise ValueError
                pages.append(_page(scope, category, index, records))
                index += 1
                records = []
                size = len(_canonical(_page(scope, category, index, [])))
            if size + record_bytes > _MAX_PAGE_BYTES:
                raise ValueError
            size += record_bytes
            records.append(item)
        if records:
            if len(pages) >= _MAX_PAGES:
                raise ValueError
            pages.append(_page(scope, category, index, records))
    result = []
    for page in pages:
        records = page["records"]
        _check_page_records(org, scope, page["category"], records, verification_key)
        raw = _canonical(page)
        total_bytes += len(raw)
        if len(raw) > _MAX_PAGE_BYTES or total_bytes > _MAX_AGGREGATE_BYTES:
            raise ValueError
        descriptor = {
            "category": page["category"], "index": page["index"], "count": len(records),
            "first": _record_identity(page["category"], records[0]),
            "last": _record_identity(page["category"], records[-1]),
            "sha256": sha256(raw).hexdigest(), "bytes": len(raw),
        }
        result.append((descriptor, page))
    return result


def _object_name(installation, pin, digest):
    # Both the existing local and R2 JournalStore permit this grammar.
    # Pages share the checkpoint namespace but are distinguished by their
    # content-addressed SHA and separate version/HMAC domain.
    return _checkpoint_name(installation, CheckpointPin(
        pin.organization_id, pin.backup_id, pin.sequence, digest))


def _read_object(store, name, digest, domain, max_bytes, verification_key, cipher):
    sealed = store.read(name)
    if type(sealed) is not bytes or not 1 <= len(sealed) <= _MAX_OBJECT_BYTES:
        raise ValueError
    envelope = json.loads(cipher.decrypt(sealed), object_pairs_hook=_unique_object)
    if type(envelope) is not dict or set(envelope) != {"payload", "signature"}:
        raise ValueError
    payload, signature = envelope["payload"], envelope["signature"]
    if type(payload) is not dict or not _hex(signature, 64):
        raise ValueError
    raw = _canonical(payload)
    if (len(raw) > max_bytes or not hmac.compare_digest(sha256(raw).hexdigest(), digest)
            or not hmac.compare_digest(hmac.new(verification_key, domain + raw, sha256).hexdigest(), signature)):
        raise ValueError
    return payload, len(raw)


def _put_readback(store, name, payload, digest, domain, max_bytes, verification_key, cipher):
    raw = _canonical(payload)
    envelope = {"payload": payload, "signature": hmac.new(verification_key, domain + raw, sha256).hexdigest()}
    store.put_once(name, cipher.encrypt(_canonical(envelope)))
    stored, _ = _read_object(store, name, digest, domain, max_bytes, verification_key, cipher)
    if stored != payload:
        raise ValueError


def _manifest_descriptors(manifest, scope):
    if (set(manifest) != scope.keys() | {"version", "complete", "counts", "pages"}
            or any(manifest[key] != value for key, value in scope.items())
            or type(manifest["sequence"]) is not int
            or manifest["version"] != _VERSION or manifest["complete"] is not True
            or type(manifest["counts"]) is not dict or set(manifest["counts"]) != _IDENTITIES.keys()
            or any(type(count) is not int or count < 0 for count in manifest["counts"].values())
            or sum(manifest["counts"].values()) > _MAX_RECORDS
            or type(manifest["pages"]) is not list or len(manifest["pages"]) > _MAX_PAGES):
        raise ValueError
    counts = {category: 0 for category in _IDENTITIES}
    category_index = {category: index for index, category in enumerate(_IDENTITIES)}
    page_indices = dict(counts)
    last_order, last_ids, seen, total_bytes = None, {}, set(), 0
    for descriptor in manifest["pages"]:
        if type(descriptor) is not dict or set(descriptor) != _DESCRIPTOR_FIELDS:
            raise ValueError
        category = descriptor["category"]
        if (category not in counts or type(descriptor["index"]) is not int
                or descriptor["index"] != page_indices[category]
                or not _hex(descriptor["sha256"], 64) or descriptor["sha256"] in seen
                or type(descriptor["count"]) is not int or not 1 <= descriptor["count"] <= _MAX_PAGE_RECORDS
                or type(descriptor["bytes"]) is not int or not 1 <= descriptor["bytes"] <= _MAX_PAGE_BYTES):
            raise ValueError
        first, last = str(_uuid(descriptor["first"])), str(_uuid(descriptor["last"]))
        order = (category_index[category], descriptor["index"])
        if (first > last or (category in last_ids and first <= last_ids[category])
                or (last_order is not None and order <= last_order)):
            raise ValueError
        last_order = order
        last_ids[category] = last
        seen.add(descriptor["sha256"])
        counts[category] += descriptor["count"]
        page_indices[category] += 1
        total_bytes += descriptor["bytes"]
    if counts != manifest["counts"] or total_bytes > _MAX_AGGREGATE_BYTES:
        raise ValueError
    return manifest["pages"]


def read_checkpoint_pages(store: JournalStore, installation, org, backup, pin, *,
                          verification_key, encryption_key):
    """Authenticate the exact pinned manifest and every page before returning evidence."""
    try:
        name = _checkpoint_name(installation, pin)
        if (pin.organization_id, pin.backup_id) != (org, backup):
            raise ValueError
        scope = _scope(installation, org, backup, pin.sequence)
        cipher = _checkpoint_keys(verification_key, encryption_key)
        manifest, _ = _read_object(
            store, name, pin.sha256, _MANIFEST_DOMAIN, _MAX_MANIFEST_BYTES, verification_key, cipher)
        descriptors = _manifest_descriptors(manifest, scope)
        payload = {"version": _VERSION, "complete": True, "organization_id": str(org),
                   "backup_id": backup, "sequence": pin.sequence, **{name: [] for name in _IDENTITIES}}
        for descriptor in descriptors:
            category, index, digest = descriptor["category"], descriptor["index"], descriptor["sha256"]
            page, length = _read_object(
                store, _object_name(installation, pin, digest), digest, _PAGE_DOMAIN,
                _MAX_PAGE_BYTES, verification_key, cipher)
            if (set(page) != scope.keys() | {"version", "category", "index", "records"}
                    or any(page[key] != value for key, value in scope.items())
                    or type(page["sequence"]) is not int or page["version"] != _PAGE_VERSION
                    or page["category"] != category or type(page["index"]) is not int
                    or page["index"] != index or length != descriptor["bytes"]
                    or type(page["records"]) is not list or len(page["records"]) != descriptor["count"]
                    or _record_identity(category, page["records"][0]) != descriptor["first"]
                    or _record_identity(category, page["records"][-1]) != descriptor["last"]):
                raise ValueError
            _check_page_records(org, scope, category, page["records"], verification_key)
            payload[category].extend(page["records"])
        _assert_global_uniqueness(payload)
        return payload
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError, InvalidToken, RestoreJournalInvalid):
        raise RestoreBlocked("Authenticated paged checkpoint unavailable") from None


def _check_previous(store, installation, pin, payload, previous_pin, previous_args):
    if previous_pin is None:
        return
    if (type(previous_pin) is not CheckpointPin
            or (previous_pin.organization_id, previous_pin.backup_id) != (pin.organization_id, pin.backup_id)
            or pin.sequence < previous_pin.sequence
            or (pin.sequence == previous_pin.sequence and pin != previous_pin)):
        raise ValueError
    try:
        previous = read_checkpoint_pages(
            store, installation, pin.organization_id, pin.backup_id, previous_pin, **previous_args)
    except RestoreBlocked:
        # Legacy objects require their original HMAC and exact external pin.
        # Neither format can be accepted as the other, even after corruption.
        previous = read_checkpoint(
            store, installation, pin.organization_id, pin.backup_id, previous_pin, **previous_args)["payload"]
    for category, identity in _IDENTITIES.items():
        old = _normalized_records(category, previous.get(category, []))
        current = {item[identity]: _canonical(item) for item in payload[category]}
        if any(current.get(item[identity]) != _canonical(item) for item in old):
            raise RestoreBlocked("Paged checkpoint omits prior evidence")


def publish_checkpoint_pages(store: JournalStore, installation, payload, *,
                             verification_key, encryption_key, previous_pin=None, previous_keys=None):
    """Publish pages first and return a candidate manifest pin after full readback."""
    try:
        cipher = _checkpoint_keys(verification_key, encryption_key)
        payload = _normalize(payload)
        org = _uuid(payload["organization_id"])
        scope = _scope(installation, org, payload["backup_id"], payload["sequence"])
        _assert_global_uniqueness(payload)
        pages = _pages(org, scope, payload, verification_key)
        manifest = dict(scope, version=_VERSION, complete=True,
                        counts={category: len(payload[category]) for category in _IDENTITIES},
                        pages=[descriptor for descriptor, _ in pages])
        raw = _canonical(manifest)
        if len(raw) > _MAX_MANIFEST_BYTES:
            raise ValueError
        pin = CheckpointPin(org, payload["backup_id"], payload["sequence"], sha256(raw).hexdigest())
        previous_args = _previous_key_args(previous_pin, previous_keys, verification_key, encryption_key)
        _check_previous(store, installation, pin, payload, previous_pin, previous_args)
        _manifest_descriptors(manifest, scope)
        for descriptor, page in pages:
            digest = descriptor["sha256"]
            _put_readback(store, _object_name(installation, pin, digest), page, digest,
                          _PAGE_DOMAIN, _MAX_PAGE_BYTES, verification_key, cipher)
        _put_readback(store, _checkpoint_name(installation, pin), manifest, pin.sha256,
                      _MANIFEST_DOMAIN, _MAX_MANIFEST_BYTES, verification_key, cipher)
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError, InvalidToken, RestoreJournalInvalid):
        raise RestoreBlocked("Paged checkpoint publication failed") from None
    if read_checkpoint_pages(store, installation, org, pin.backup_id, pin,
                             verification_key=verification_key, encryption_key=encryption_key) != payload:
        raise RestoreBlocked("Paged checkpoint readback failed")
    return pin
