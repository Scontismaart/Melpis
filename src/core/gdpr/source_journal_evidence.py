"""Capture missing journal entries from a frozen authoritative installation."""

from uuid import UUID

from src.core.gdpr.journal_archive import assert_registered_archive_reader
from src.core.gdpr.journal_publication import read_chain
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_coordinator import _restore_inventory
from src.core.gdpr.restore_journal import JournalCheckpoint, RestoreJournalInvalid

_GENESIS = JournalCheckpoint(0, "0" * 64)
_MAX_ENTRIES = 100000


async def capture_erasure_journal_delta(
    conn, authority, store, *, expected_organizations, trusted_public_key, journal_encryption_key,
    key_epochs=None,
):
    """Verify prior evidence and return a bounded, ordered append-only delta.

    Run on the source as the authorized database owner in a read-only,
    read-committed transaction. Keep source erasure and policy work frozen
    through the operator's publication window and compare-and-set. This
    function never signs or stores data and does not alter the source.
    """
    wanted = await _restore_inventory(conn, expected_organizations)
    if not await conn.fetchval("""SELECT current_setting('transaction_read_only')='on'
        AND EXISTS(SELECT FROM pg_class c WHERE c.oid='public.gdpr_erasure_barriers'::regclass
            AND c.relowner=current_user::regrole)"""):
        raise RestoreBlocked("Read-only source authority required")
    for org in sorted(wanted):
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org))
    if await _restore_inventory(conn, expected_organizations) != wanted:
        raise RestoreBlocked("Source organization inventory changed")
    try:
        assert_registered_archive_reader(authority, store)
        tip = authority.current()
        authority.validate_segment_schedule(tip, trusted_public_key, journal_encryption_key, key_epochs)
        previous = read_chain(
            store, authority.installation_id, _GENESIS, authority.chain_after(_GENESIS), tip,
            trusted_public_key=trusted_public_key, encryption_key=journal_encryption_key,
            key_epochs=key_epochs)
    except RestoreJournalInvalid:
        raise RestoreBlocked("Current signed journal unavailable") from None
    previous_by_org = {org: {} for org in wanted}
    for entry in previous:
        if entry.organization_id not in wanted:
            raise RestoreBlocked("Previously journaled organization not in source inventory")
        previous_by_org[entry.organization_id][entry.operation_id] = (
            entry.scope, entry.subject_id, entry.identifier_digest, entry.key_id)
    delta = []
    scanned = 0
    for org in sorted(wanted):
        if await conn.fetchval("""SELECT EXISTS (
            SELECT FROM gdpr_operation_steps s
            WHERE s.organization_id=$1 AND s.name='local' AND s.status='confirmed'
            AND NOT EXISTS(SELECT FROM gdpr_erasure_barriers b
                WHERE b.organization_id=$1 AND b.operation_id=s.operation_id))""", org):
            raise RestoreBlocked("Confirmed erasure is missing a source barrier")
        rows = await conn.fetch("""SELECT b.operation_id,b.scope,b.subject_id,b.identifier_digest,b.key_id,
                o.kind,o.status AS operation_status,o.scope AS operation_scope,o.subject_id AS operation_subject,
                s.status AS local_status,e.first_id,e.event_count
            FROM gdpr_erasure_barriers b
            JOIN gdpr_operations o ON o.organization_id=b.organization_id AND o.id=b.operation_id
            LEFT JOIN gdpr_operation_steps s ON s.organization_id=b.organization_id
                AND s.organization_id=$1 AND s.operation_id=b.operation_id AND s.name='local'
            LEFT JOIN LATERAL (
                SELECT min(id) AS first_id,count(id) AS event_count
                FROM gdpr_operation_events
                WHERE organization_id=$1 AND operation_id=b.operation_id AND event_type='erase.local_committed'
            ) e ON true
            WHERE b.organization_id=$1 AND o.organization_id=$1
            ORDER BY e.first_id,b.operation_id LIMIT $2""", org, _MAX_ENTRIES - scanned + 1)
        scanned += len(rows)
        if scanned > _MAX_ENTRIES:
            raise RestoreBlocked("Source journal reconciliation limit exceeded")
        prior = previous_by_org[org]
        seen = []
        seen_unpublished = False
        for row in rows:
            operation = row["operation_id"]
            material = (row["scope"], row["subject_id"], row["identifier_digest"], row["key_id"])
            if (row["kind"] != "erase" or row["operation_status"] not in {"partial", "completed"}
                    or row["operation_scope"] != row["scope"] or row["operation_subject"] != row["subject_id"]
                    or row["local_status"] != "confirmed" or row["event_count"] != 1):
                raise RestoreBlocked("Source erasure lineage is incomplete")
            if operation in prior:
                if seen_unpublished or prior[operation] != material:
                    raise RestoreBlocked("Source history disagrees with published journal")
                seen.append(operation)
                continue
            seen_unpublished = True
            delta.append((row["first_id"], {
                "organization_id": str(org), "operation_id": str(operation), "scope": material[0],
                "subject_id": str(material[1]) if isinstance(material[1], UUID) else None,
                "identifier_digest": material[2], "key_id": material[3],
            }))
        if seen != list(prior):
            raise RestoreBlocked("Published erasures do not match authoritative source order")
    try:
        assert_registered_archive_reader(authority, store)
    except RestoreJournalInvalid:
        raise RestoreBlocked("Current signed journal unavailable") from None
    if authority.current() != tip:
        raise RestoreBlocked("Independent journal changed during source capture")
    return tip, tuple(record for _, record in sorted(delta, key=lambda item: item[0]))
