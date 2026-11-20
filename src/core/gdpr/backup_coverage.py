"""Read-only capacity and coverage check against independent backup inventory."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
import re
import sqlite3

from src.core.gdpr.journal_chain_pages import scan_verified_chain
from src.core.gdpr.journal_publication import _canonical, read_checkpoint
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_journal import JournalCheckpoint, RestoreJournalInvalid, _checkpoint, _unique_object, _uuid

_GENESIS = JournalCheckpoint(0, "0" * 64)
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,128}")
_CHECKPOINT_FIELDS = ("erasures", "stay_purges", "receipt_prunes")
_MAX_INVENTORY_BYTES = 1024 * 1024
_LEGACY_SEGMENT_LIMIT = 10000
_LEGACY_ENTRY_LIMIT = 100000


def _covered_backup_evidence(authority, store, backups, tip, *, trusted_public_key,
                             journal_encryption_key, key_epochs):
    """Index verified erasures by scoped, ephemeral digest, without retaining payloads."""
    key = os.urandom(32)

    def digest(*values):
        return hmac.digest(key, _canonical(values), "sha256")

    # Verify the exact anchor against the independent index, even if the
    # corresponding backup has no post-anchor erasures.
    for anchor in {candidate[2] for candidate in backups}:
        authority.chain_page_after(anchor, tip, max_segments=1)
    index = sqlite3.connect("")
    try:
        index.execute("CREATE TABLE entries (seq INTEGER PRIMARY KEY, tenant BLOB NOT NULL, record BLOB NOT NULL)")
        index.execute("CREATE INDEX entries_scope ON entries (tenant,seq)")

        def stage(entries):
            with index:
                for row in entries:
                    index.execute("INSERT INTO entries VALUES (?, ?, ?)", (row.sequence,
                        digest(str(row.organization_id)), digest(str(row.operation_id), row.scope,
                        str(row.subject_id) if row.subject_id is not None else None)))

        proof = scan_verified_chain(store, authority, _GENESIS, tip, trusted_public_key=trusted_public_key,
                                    encryption_key=journal_encryption_key, key_epochs=key_epochs, consume=stage)
        return index, digest, proof
    except BaseException:
        index.close()
        raise


def _timestamp(value):
    if type(value) is not str:
        raise ValueError
    value = datetime.fromisoformat(value)
    if value.utcoffset() != timedelta(0):
        raise ValueError
    return value


def _backups(raw, expected_sha256, installation, audit_at):
    if (type(raw) is not bytes or not 1 <= len(raw) <= _MAX_INVENTORY_BYTES
            or type(expected_sha256) is not str or not _HEX_DIGEST.fullmatch(expected_sha256)
            or not hmac.compare_digest(sha256(raw).hexdigest(), expected_sha256)
            or type(audit_at) is not datetime or audit_at.utcoffset() != timedelta(0)):
        raise ValueError
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if (type(value) is not dict or set(value) != {"version", "installation_id", "backups"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["installation_id"] != str(installation) or type(value["backups"]) is not list
            or not 1 <= len(value["backups"]) <= 1000):
        raise ValueError
    known, manifests = set(), set()
    for row in value["backups"]:
        if type(row) is not dict or set(row) != {
                "backup_id", "manifest_sha256", "completed_at", "organizations", "anchor", "exception"}:
            raise ValueError
        backup, digest = row["backup_id"], row["manifest_sha256"]
        if (not _OPAQUE_ID.fullmatch(backup) or not _HEX_DIGEST.fullmatch(digest)
                or backup in known or digest in manifests
                or type(row["organizations"]) is not list or not 1 <= len(row["organizations"]) <= 10000
                or type(row["anchor"]) is not dict):
            raise ValueError
        orgs = {_uuid(value) for value in row["organizations"]}
        if len(orgs) != len(row["organizations"]):
            raise ValueError
        anchor = JournalCheckpoint(**row["anchor"])
        completed = _timestamp(row["completed_at"])
        if not _checkpoint(anchor) or completed > audit_at:
            raise ValueError
        exception = row["exception"]
        if exception is not None and (type(exception) is not dict
                or set(exception) != {"authority_reference", "review_by"}
                or not _OPAQUE_ID.fullmatch(exception["authority_reference"])):
            raise ValueError
        reviewed = exception is not None and _timestamp(exception["review_by"]) > audit_at
        known.add(backup)
        manifests.add(digest)
        yield backup, orgs, anchor, audit_at - completed > timedelta(days=30) and not reviewed


def assess_backup_coverage(
    authority, store, inventory_bytes, *, latest_inventory_sha256,
    trusted_public_key, journal_encryption_key, checkpoint_verification_key, checkpoint_encryption_key,
    policy_verification_key, policy_encryption_key, key_epochs=None, audit_at=None,
):
    """Verify all recovery evidence referenced by a trusted, current inventory.

    Only the independent inventory custodian can attest that the pinned
    inventory is current and complete. Each digest refers to an independently
    verified encrypted backup manifest, not a bucket listing or inferred date.
    This function reports available capacity and never deletes or rotates data.
    """
    installation = authority.installation_id
    if audit_at is None:
        audit_at = datetime.now(timezone.utc)
    index = None
    try:
        backups = list(_backups(inventory_bytes, latest_inventory_sha256, installation, audit_at))
        tip = authority.current()
        index, digest, proof = _covered_backup_evidence(authority, store, backups, tip,
            trusted_public_key=trusted_public_key, journal_encryption_key=journal_encryption_key,
            key_epochs=key_epochs)
        # This assessment guards source deletion. Keep the existing restore
        # capacity gate until the quarantined replay path also reads pages.
        if proof["segments"] > _LEGACY_SEGMENT_LIMIT or proof["entries"] > _LEGACY_ENTRY_LIMIT:
            raise RestoreBlocked("Legacy restore journal capacity exceeded")
        overdue, pins = [], {}
        bytes_remaining, entries_remaining = 2 * 1024 * 1024, 10000
        for backup, orgs, anchor, is_overdue in backups:
            authorized_scopes = {digest(str(org)) for org in orgs}
            if any(tenant not in authorized_scopes for (tenant,) in index.execute(
                    "SELECT DISTINCT tenant FROM entries WHERE seq>?", (anchor.sequence,))):
                raise RestoreBlocked("Backup anchor or journal scope is not covered")
            if is_overdue:
                overdue.append(backup)
            for org in orgs:
                pin, policy = authority.checkpoint_pin(org, backup), authority.policy_pin(org, backup)
                pins[org, backup] = pin, policy
                checkpoint = read_checkpoint(
                    store, installation, org, backup, pin, verification_key=checkpoint_verification_key,
                    encryption_key=checkpoint_encryption_key)["payload"]
                authority.read_policy(store, policy, verification_key=policy_verification_key,
                                      encryption_key=policy_encryption_key)
                actual = {digest(row["operation_id"], row["scope"], row["subject_id"])
                          for row in checkpoint["erasures"]}
                if any(record not in actual for (record,) in index.execute(
                        "SELECT record FROM entries WHERE tenant=? AND seq>?", (digest(str(org)), anchor.sequence))):
                    raise RestoreBlocked("Backup checkpoint omits signed erasure evidence")
                bytes_remaining = min(bytes_remaining, 2 * 1024 * 1024 - len(_canonical(checkpoint)))
                fields = (*_CHECKPOINT_FIELDS,"ordinary_booking_purges") if checkpoint["version"]=="c24-restore-v4" else _CHECKPOINT_FIELDS
                entries_remaining = min(entries_remaining, *(10000 - len(checkpoint[field])
                                                              for field in fields))
        if authority.current() != tip or any(
                (authority.checkpoint_pin(org, backup), authority.policy_pin(org, backup)) != evidence
                for (org, backup), evidence in pins.items()):
            raise RestoreBlocked("Recovery authority changed during coverage check")
        authority.validate_segment_schedule(tip, trusted_public_key, journal_encryption_key, key_epochs)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, RestoreJournalInvalid, sqlite3.Error):
        raise RestoreBlocked("Verified independently pinned backup inventory required") from None
    finally:
        if index is not None:
            index.close()
    return {"backups_with_verified_recovery_evidence": len(backups), "overdue_backups": tuple(sorted(overdue)),
            "backup_objects_verified": False, "checkpoint_source_commitments_verified": False,
            "minimum_checkpoint_entries_remaining": entries_remaining,
            "minimum_checkpoint_bytes_remaining": bytes_remaining,
            "source_chain_segments_remaining": _LEGACY_SEGMENT_LIMIT - proof["segments"],
            "source_chain_entries_remaining": _LEGACY_ENTRY_LIMIT - proof["entries"],
            "earliest_backup_anchor_sequence": min(anchor.sequence for _, _, anchor, _ in backups),
            "deletion_authorized": False}
