"""Bounded source reconciliation against the complete independently pinned journal."""

from heapq import heappush, heapreplace
import hmac
import os
import sqlite3
from uuid import UUID

from src.core.gdpr.journal_chain_pages import scan_verified_chain
from src.core.gdpr.journal_publication import _canonical
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_coordinator import _restore_inventory
from src.core.gdpr.restore_journal import RestoreJournalInvalid, _hex
from src.core.gdpr.source_journal_evidence import _GENESIS


def _material(scope, subject, identifier, key_id):
    return _canonical([scope, str(subject) if subject is not None else None, identifier, key_id])


def _valid_material(scope, subject, identifier, key_id):
    if scope == "contact":
        return type(subject) is UUID and bool(subject.int) and _hex(identifier, 64) and _hex(key_id, 16)
    return scope == "organization" and subject is None and identifier is None and key_id is None


async def capture_erasure_journal_delta_paged(
    conn, authority, store, *, expected_organizations, trusted_public_key, journal_encryption_key,
    key_epochs=None, max_entries=1000, max_segments=40,
):
    """Return the exact current tip, the next publishable segment and remaining count.

    An authorized owner must freeze source writes and authority advancement
    through capture and publication. Every prior operation is reconciled,
    even when the unpublished portion is larger than one segment.
    """
    if type(max_entries) is not int or not 1 <= max_entries <= 1000:
        raise RestoreBlocked("Source journal delta size is invalid")
    wanted = await _restore_inventory(conn, expected_organizations)
    if not await conn.fetchval("""SELECT current_setting('transaction_read_only')='on'
        AND EXISTS(SELECT FROM pg_class WHERE oid='public.gdpr_erasure_barriers'::regclass
          AND relowner=current_user::regrole)"""):
        raise RestoreBlocked("Read-only source authority required")
    for org in sorted(wanted):
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org))
    inventory = tuple(sorted(wanted))
    if await _restore_inventory(conn, inventory) != wanted:
        raise RestoreBlocked("Source organization inventory changed")

    secret = os.urandom(32)

    def identity(org, operation):
        return hmac.digest(secret, b"identity\0" + org.bytes + operation.bytes, "sha256")

    def fingerprint(org, operation, scope, subject, identifier, key_id):
        return hmac.digest(secret, b"material\0" + org.bytes + operation.bytes
                           + _material(scope, subject, identifier, key_id), "sha256")

    index = sqlite3.connect("")
    try:
        index.execute("CREATE TABLE evidence (identity BLOB PRIMARY KEY, fingerprint BLOB NOT NULL, sequence INTEGER NOT NULL) WITHOUT ROWID")

        def stage(entries):
            with index:
                for entry in entries:
                    if entry.organization_id not in wanted:
                        raise RestoreBlocked("Previously journaled organization not in source inventory")
                    index.execute("INSERT INTO evidence VALUES (?, ?, ?)", (
                        identity(entry.organization_id, entry.operation_id),
                        fingerprint(entry.organization_id, entry.operation_id, entry.scope,
                                    entry.subject_id, entry.identifier_digest, entry.key_id), entry.sequence))

        tip = authority.current()
        proof = scan_verified_chain(store, authority, _GENESIS, tip, trusted_public_key=trusted_public_key,
                                    encryption_key=journal_encryption_key, key_epochs=key_epochs,
                                    max_segments=max_segments, consume=stage)
        matching, unpublished, next_segment = 0, 0, []
        for org in inventory:
            if await conn.fetchval("""SELECT EXISTS(SELECT FROM gdpr_operation_steps s
                WHERE s.organization_id=$1 AND s.name='local' AND s.status='confirmed'
                  AND NOT EXISTS(SELECT FROM gdpr_erasure_barriers b
                    WHERE b.organization_id=$1 AND b.operation_id=s.operation_id))""", org):
                raise RestoreBlocked("Confirmed erasure is missing a source barrier")
            last_sequence, seen_unpublished = 0, False
            rows = conn.cursor("""SELECT b.operation_id,b.scope,b.subject_id,b.identifier_digest,b.key_id,
                    o.kind,o.status AS operation_status,o.scope AS operation_scope,
                    o.subject_id AS operation_subject,s.status AS local_status,e.first_id,e.event_count
                FROM gdpr_erasure_barriers b
                LEFT JOIN gdpr_operations o ON o.organization_id=b.organization_id AND o.organization_id=$1
                    AND o.id=b.operation_id
                LEFT JOIN gdpr_operation_steps s ON s.organization_id=b.organization_id AND s.organization_id=$1
                    AND s.operation_id=b.operation_id AND s.name='local'
                LEFT JOIN LATERAL (SELECT min(id) AS first_id,count(id) AS event_count
                    FROM gdpr_operation_events WHERE organization_id=$1 AND operation_id=b.operation_id
                      AND event_type='erase.local_committed') e ON true
                WHERE b.organization_id=$1 ORDER BY e.first_id,b.operation_id""", org, prefetch=128)
            async for row in rows:
                operation = row["operation_id"]
                scope, subject = row["scope"], row["subject_id"]
                identifier, key_id = row["identifier_digest"], row["key_id"]
                event_id = row["first_id"]
                if (row["kind"] != "erase" or row["operation_status"] not in {"partial", "completed"}
                        or row["operation_scope"] != scope or row["operation_subject"] != subject
                        or row["local_status"] != "confirmed" or row["event_count"] != 1
                        or type(operation) is not UUID or not operation.int
                        or type(event_id) is not int or event_id < 1):
                    raise RestoreBlocked("Source erasure lineage is incomplete")
                prior = index.execute("SELECT fingerprint,sequence FROM evidence WHERE identity=?",
                                      (identity(org, operation),)).fetchone()
                if prior is not None:
                    if (seen_unpublished or prior[1] <= last_sequence or not hmac.compare_digest(
                            prior[0], fingerprint(org, operation, scope, subject, identifier, key_id))):
                        raise RestoreBlocked("Source history disagrees with published journal")
                    matching += 1
                    last_sequence = prior[1]
                    continue
                seen_unpublished = True
                unpublished += 1
                if not _valid_material(scope, subject, identifier, key_id):
                    raise RestoreBlocked("Unpublished source history cannot be signed")
                if len(next_segment) == max_entries and event_id >= -next_segment[0][0]:
                    continue
                record = dict(organization_id=str(org), operation_id=str(operation), scope=scope,
                              subject_id=str(subject) if subject is not None else None,
                              identifier_digest=identifier, key_id=key_id)
                # The global event ID is unique. Select the oldest unpublished
                # events across tenants with a heap bounded by one segment.
                if len(next_segment) == max_entries:
                    heapreplace(next_segment, (-event_id, record))
                else:
                    heappush(next_segment, (-event_id, record))
        if matching != proof["entries"]:
            raise RestoreBlocked("Published erasures do not match authoritative source order")
        if await _restore_inventory(conn, inventory) != wanted:
            raise RestoreBlocked("Source organization inventory changed")
        authority.validate_segment_schedule(tip, trusted_public_key, journal_encryption_key, key_epochs)
        if authority.current() != tip:
            raise RestoreBlocked("Independent journal changed during source capture")
        batch = tuple(record for _, record in sorted(next_segment, reverse=True))
        return tip, batch, unpublished - len(batch)
    except (RestoreJournalInvalid, sqlite3.Error):
        raise RestoreBlocked("Current signed journal unavailable") from None
    finally:
        index.close()
