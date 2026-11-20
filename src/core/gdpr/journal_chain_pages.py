"""Bounded, authenticated scan foundation for a future recovery floor.

The input anchor is independently attested by the caller. This scans only its
suffix, not the earlier chain, and grants neither mutation nor traffic access.
"""

import hmac
import os
import sqlite3

from src.core.gdpr.journal_publication import _resolve_key_epochs, read_chain
from src.core.gdpr.restore_journal import RestoreJournalInvalid, _checkpoint


def scan_verified_chain(store, authority, anchor, expected_tip, *, trusted_public_key,
                        encryption_key, consume, key_epochs=None, max_segments=100):
    """Scan to exact tip, one bounded page at a time, with global uniqueness.

    `consume` must be read-only or stage in a quarantined transaction which is
    rolled back on any exception. Nothing is certified until this returns.
    The temporary on-disk uniqueness index contains random-keyed digests only;
    no raw organization, operation or customer identifiers are persisted.
    """
    if (not _checkpoint(anchor) or not _checkpoint(expected_tip)
            or anchor.sequence > expected_tip.sequence or not callable(consume)
            or type(max_segments) is not int or not 1 <= max_segments <= 100):
        raise RestoreJournalInvalid("journal_bounds_invalid")
    authority.validate_segment_schedule(expected_tip, trusted_public_key, encryption_key, key_epochs)
    epochs = _resolve_key_epochs(key_epochs, trusted_public_key, encryption_key, expected_tip)
    # SQLite's private temporary database is discarded on close. The key stays
    # in memory and is not shared with the independent signing/custody keys.
    scratch_key = os.urandom(32)
    index = sqlite3.connect("")
    previous, segment_count, entry_count = anchor, 0, 0
    try:
        index.execute("CREATE TABLE identities (digest BLOB PRIMARY KEY) WITHOUT ROWID")
        while True:
            page = authority.chain_page_after(previous, expected_tip, max_segments=max_segments)
            if not page:
                if previous != expected_tip:
                    raise RestoreJournalInvalid("journal_chain_incomplete")
                break
            page_tip = page[-1]
            if (not _checkpoint(page_tip) or not previous.sequence < page_tip.sequence <= expected_tip.sequence
                    or len(page) > max_segments):
                raise RestoreJournalInvalid("journal_chain_invalid")
            page_epochs = tuple(epoch for epoch in epochs if epoch.starts_after.sequence <= page_tip.sequence)
            entries = read_chain(store, authority.installation_id, previous, page, page_tip,
                                 trusted_public_key=trusted_public_key, encryption_key=encryption_key,
                                 key_epochs=page_epochs)
            with index:
                for entry in entries:
                    identity = hmac.digest(scratch_key, entry.organization_id.bytes + entry.operation_id.bytes, "sha256")
                    index.execute("INSERT INTO identities VALUES (?)", (identity,))
            consume(entries)
            segment_count += len(page)
            entry_count += len(entries)
            previous = page_tip
        authority.validate_segment_schedule(expected_tip, trusted_public_key, encryption_key, key_epochs)
        if authority.current() != expected_tip:
            raise RestoreJournalInvalid("independent_authority_conflict")
    except sqlite3.Error:
        raise RestoreJournalInvalid("journal_chain_invalid") from None
    finally:
        index.close()
    return {"segments": segment_count, "entries": entry_count, "traffic_authorized": False}
