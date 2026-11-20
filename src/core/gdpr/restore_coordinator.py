"""Verify restored primary C2.4 copies against an independent published journal.

Operator-only isolated recovery workflow. This is not a service endpoint,
never replays on its own and never enables traffic. Provider copies, current
policy reconciliation and deployment security remain separate required gates.
"""

from hashlib import sha256
import re
from uuid import UUID

from src.core.db.scoping import system_scope
from src.core.gdpr.checkpoint_pages import decode_replay_checkpoint, read_any_checkpoint, read_checkpoint_pages
from src.core.gdpr.deletion import Deletion
from src.core.gdpr.guard import ErasureBlocked
from src.core.gdpr.journal_chain_pages import scan_verified_chain
from src.core.gdpr.operations import encode, event
from src.core.gdpr.restore_checkpoint import (
    RestoreBlocked, _bind_verified_receipt_prunes,
    _verify_replayed_erasures, _verify_replayed_receipt_prunes,
)
from src.core.gdpr.restore_journal import JournalCheckpoint, RestoreJournalInvalid, _checkpoint
from src.core.gdpr.restore_policy_state import reconcile_policy_state, verify_policy_state


_QUARANTINE_NAME = re.compile(r"melpis_(?:restore_drill_[0-9a-f]{16,32}|c24_restore_dst_[0-9a-f]{12})")
_RECOVERY_REQUESTER = "gdpr:restore-replay"
_GENESIS = JournalCheckpoint(0, "0" * 64)


def _verify_anchor_index(authority, anchor, tip):
    """Check pre-anchor topology without allocating the whole independent index."""
    if not _checkpoint(anchor) or anchor.sequence > tip.sequence:
        raise RestoreJournalInvalid("journal_bounds_invalid")
    previous = _GENESIS
    while previous != anchor:
        page = authority.chain_page_after(previous, tip)
        for candidate in page:
            if candidate == anchor:
                return
            if candidate.sequence >= anchor.sequence:
                raise RestoreJournalInvalid("independent_authority_invalid")
        if not page:
            raise RestoreJournalInvalid("independent_authority_invalid")
        previous = page[-1]


@system_scope("Isolated restore: compare organization IDs with the independent backup inventory")
async def _restore_inventory(conn, expected_organizations):
    if (not conn.is_in_transaction()
            or await conn.fetchval("SHOW transaction_isolation") != "read committed"):
        raise RestoreBlocked("Quarantined read committed transaction required")
    if (type(expected_organizations) not in (tuple, list)
            or not 1 <= len(expected_organizations) <= 10000
            or any(type(org) is not UUID or not org.int for org in expected_organizations)
            or len(set(expected_organizations)) != len(expected_organizations)):
        raise RestoreBlocked("Independent organization inventory required")
    wanted = set(expected_organizations)
    actual = {row["id"] for row in await conn.fetch("SELECT id FROM public.organizations LIMIT 10001")}
    if actual != wanted:
        raise RestoreBlocked("Restored organization inventory mismatch")
    return wanted


async def _published_restore_evidence(
    conn, authority, store, *, backup_checkpoint, backup_id, expected_organizations,
    trusted_public_key, journal_encryption_key, checkpoint_verification_key,
    checkpoint_encryption_key, key_epochs=None, recovery_inventory_bytes=None,
    latest_inventory_sha256=None,
):
    wanted = await _restore_inventory(conn, expected_organizations)
    installation = authority.installation_id
    try:
        tip = authority.current()
        _verify_anchor_index(authority, backup_checkpoint, tip)
        journal = []
        scopes = {org: [] for org in wanted}

        def stage(page):
            if len(journal) + len(page) > 100000:
                raise RestoreBlocked("Restore journal entry capacity exceeded")
            for entry in page:
                if entry.organization_id not in wanted:
                    raise RestoreBlocked("Journal scope not covered by recovery inventory")
                scopes[entry.organization_id].append(entry)
            journal.extend(page)

        proof = scan_verified_chain(
            store, authority, backup_checkpoint, tip, trusted_public_key=trusted_public_key,
            encryption_key=journal_encryption_key, key_epochs=key_epochs, consume=stage, max_segments=40)
        if proof["segments"] > 10000:
            raise RestoreBlocked("Restore journal segment capacity exceeded")
        pins, checkpoints = {}, {}
        for org in sorted(wanted):
            pin = authority.checkpoint_pin(org, backup_id)
            pins[org] = pin
            payload = read_any_checkpoint(
                store, installation, org, backup_id, pin,
                verification_key=checkpoint_verification_key,
                encryption_key=checkpoint_encryption_key)
            checkpoint_entries, purges, prunes, ordinary = decode_replay_checkpoint(payload)
            scoped = scopes[org]
            signed_entries = [(entry.operation_id, entry.scope, entry.subject_id) for entry in scoped]
            if not set(signed_entries) <= set(checkpoint_entries):
                raise RestoreBlocked("Checkpoint omits authenticated erasure evidence")
            checkpoints[org] = (payload["version"], checkpoint_entries, pin.sequence, purges, prunes, ordinary,
                                payload.get("orphan_proposal_prunes", []))
        if _assert_paged_inventory_current(authority, checkpoints, latest_inventory_sha256):
            _assert_recovery_inventory(authority, recovery_inventory_bytes, latest_inventory_sha256,
                                       backup_id, wanted, backup_checkpoint)
        _assert_current(authority, backup_id, tip, pins)
    except RestoreJournalInvalid:
        raise RestoreBlocked("Authenticated independent recovery evidence required") from None
    return tip, pins, tuple(journal), scopes, checkpoints


def _assert_recovery_inventory(authority, raw, digest, backup_id, organizations, anchor):
    from datetime import datetime, timezone
    from src.core.gdpr.backup_coverage import _backups

    try:
        inventory = list(_backups(raw, digest, authority.installation_id, datetime.now(timezone.utc)))
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        raise RestoreBlocked("Independent backup inventory required for restore replay") from None
    if not any(backup == backup_id and orgs == organizations and checkpoint == anchor
               for backup, orgs, checkpoint, _ in inventory):
        raise RestoreBlocked("Restored backup does not match independent inventory")
    return inventory


def _assert_current(authority, backup_id, tip, pins):
    if authority.current() != tip or any(
            authority.checkpoint_pin(org, backup_id) != pin for org, pin in pins.items()):
        raise RestoreBlocked("Independent recovery evidence changed during verification")


async def verify_primary_published_restore(conn, authority, store, **trust):
    """Require every restored tenant and the exact current independent evidence."""
    tip, pins, journal, scopes, checkpoints = await _published_restore_evidence(
        conn, authority, store, **trust)
    await _verify_published_postconditions(conn, tip, scopes, checkpoints)
    _assert_current(authority, trust["backup_id"], tip, pins)
    _assert_paged_inventory_current(authority, checkpoints, trust.get("latest_inventory_sha256"))
    return {"verified_organizations": len(checkpoints), "verified_erasure_entries": len(journal),
            "sequence": tip.sequence, "scope": "primary_c24_only",
            "traffic_authorized": False, "external_copies_certified": False,
            "current_policy_reconciliation_required": True}


def _assert_paged_inventory_current(authority, checkpoints, digest):
    return authority.assert_paged_inventory(digest, required=any(data[0] == "c24-restore-v5" for data in checkpoints.values()))


async def _verify_published_postconditions(conn, tip, scopes, checkpoints):
    from src.core.gdpr.orphan_proposal_expiry import verify_orphan_proposal_expirations

    for org, (version, entries, sequence, purges, prunes, ordinary, orphans) in checkpoints.items():
        await verify_orphan_proposal_expirations(conn, org, orphans)
        await _verify_replayed_erasures(conn, org, entries, sequence, purges=purges, receipt_prunes=prunes,
                                       ordinary_purges=ordinary, require_committed=version == "c24-restore-v5")
        scoped = scopes[org]
        await _verify_replayed_erasures(
            conn, org, [(entry.operation_id, entry.scope, entry.subject_id) for entry in scoped],
            tip.sequence, purges=purges, receipt_prunes=prunes,
            ordinary_purges=ordinary,
            fingerprints={entry.operation_id: (entry.identifier_digest, entry.key_id) for entry in scoped})


async def verify_paged_restore_candidates(conn, authority, store, *, quarantine_database,
                                         candidates, **trust):
    """Compare candidate v5 evidence with a reconciled, quarantined database.

    Each candidate must extend the current independent legacy pin. This
    verifies restored postconditions under that pin and the paged successor
    without changing the database, promoting authority or authorizing traffic.
    """
    from src.core.gdpr.orphan_proposal_expiry import verify_orphan_proposal_expirations

    await _assert_quarantine(conn, quarantine_database)
    if not conn.is_in_transaction() or await conn.fetchval("SHOW transaction_read_only") != "on":
        raise RestoreBlocked("Paged restore candidate verification requires a read-only transaction")
    tip, pins, _, scopes, checkpoints = await _published_restore_evidence(conn, authority, store, **trust)
    if type(candidates) is not dict or candidates.keys() != pins.keys():
        raise RestoreBlocked("Paged checkpoint candidates must cover every restored organization")
    await _verify_published_postconditions(conn, tip, scopes, checkpoints)
    keys = dict(verification_key=trust["checkpoint_verification_key"],
                encryption_key=trust["checkpoint_encryption_key"])
    for org, pin in pins.items():
        candidate = candidates[org]
        prepared = authority.prepare_paged_checkpoint(store, pin, candidate, **keys)
        if prepared.expected_tip != tip or prepared.expected_pin != pin:
            raise RestoreBlocked("Independent recovery evidence changed during candidate verification")
        payload = read_checkpoint_pages(store, authority.installation_id, org, trust["backup_id"], candidate, **keys)
        await verify_orphan_proposal_expirations(conn, org, payload.get("orphan_proposal_prunes", []))
        erasures, stays, receipts, ordinary = decode_replay_checkpoint(payload)
        await _verify_replayed_erasures(conn, org, erasures, candidate.sequence, purges=stays,
                                       receipt_prunes=receipts, ordinary_purges=ordinary, require_committed=True)
    _assert_current(authority, trust["backup_id"], tip, pins)
    await _assert_quarantine(conn, quarantine_database)
    return {"verified_paged_candidates": len(candidates), "authority_advanced": False,
            "traffic_authorized": False, "external_copies_certified": False,
            "current_policy_reconciliation_required": True}


async def _assert_quarantine(conn, expected_database):
    if type(expected_database) is not str or not _QUARANTINE_NAME.fullmatch(expected_database):
        raise RestoreBlocked("Dedicated quarantine database required")
    safe = await conn.fetchval("""SELECT current_database()=$1
        AND current_user=pg_get_userbyid(d.datdba)
        AND NOT EXISTS (
            SELECT FROM aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a
            WHERE a.privilege_type='CONNECT' AND a.grantee<>d.datdba
        ) AND NOT EXISTS (
            SELECT FROM pg_stat_activity a WHERE a.datid=d.oid
                AND a.backend_type='client backend' AND a.pid<>pg_backend_pid()
        ) FROM pg_database d WHERE d.datname=current_database()""", expected_database)
    if safe is not True:
        raise RestoreBlocked("Database quarantine could not be verified")


async def _materialize_recovery_request(conn, entry):
    org, operation = entry.organization_id, entry.operation_id
    await conn.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR NO KEY UPDATE", org)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org))
    existing = await conn.fetchrow("""SELECT requester,kind,scope,subject_id
        FROM gdpr_operations WHERE organization_id=$1 AND id=$2 FOR UPDATE""", org, operation)
    if existing:
        if (existing["kind"], existing["scope"], existing["subject_id"]) != (
                "erase", entry.scope, entry.subject_id):
            raise RestoreBlocked("Restored operation identity conflicts with journal")
        return existing["requester"]
    # This row records recovery now, with an explicit recovery actor and signed
    # operation ID. It makes no claim about the original requester or dates.
    request_hash = sha256(encode(["erase", entry.scope, str(entry.subject_id)]).encode()).hexdigest()
    await conn.execute("""INSERT INTO gdpr_operations
        (id,organization_id,requester,request_id,request_hash,kind,scope,subject_id,status)
        VALUES($1,$2,$3,$1,$4,'erase',$5,$6,'requested')""",
        operation, org, _RECOVERY_REQUESTER, request_hash, entry.scope, entry.subject_id)
    await event(conn, org, operation, "erase.recovery_registered")
    return _RECOVERY_REQUESTER


async def _replay_receipt_prunes(conn, org, prunes, pin):
    if not prunes:
        return
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org))
    for request, source, _, _ in prunes:
        # PostgreSQL functions check the current verified scope, clock, safe
        # copy lifecycle and holds. Final proof requires actual postconditions.
        await conn.fetchval("SELECT public.c24_purge_source($1,$2)", org, source)
        await conn.fetchval("SELECT public.c24_cleanup_receipt($1,$2)", org, request)
    await _bind_verified_receipt_prunes(conn, org, prunes, sequence=pin.sequence, checkpoint_sha256=pin.sha256)
    for request, source, conversation, subject in prunes:
        await conn.fetchval("SELECT public.c24_cleanup_source_fences($1,$2,$3)", org, conversation, request)
        audit_ids = await conn.fetch("""SELECT audit_id FROM c24_suppression_lifecycle_audit
            WHERE organization_id=$1 AND source_record_id=$2 AND c24_source_message_id=$3
              AND conversation_id=$4 AND subject_id=$5 AND action='retention' AND record_kind='source_fence'""",
            org, request, source, conversation, subject)
        for row in audit_ids:
            await conn.fetchval("SELECT public.c24_cleanup_suppression_audit($1,$2)", org, row["audit_id"])
    await _verify_replayed_receipt_prunes(conn, org, prunes)


async def _replay_stay_retention(conn, org, purges):
    """Reapply exactly signed retention purges with current lifecycle checks."""
    if not purges:
        return
    await conn.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR NO KEY UPDATE", org)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org))
    for booking, subject, purpose, operation, version, requests in purges:
        if purpose != "retention":
            continue  # The signed erasure path is handled by Deletion.advance.
        if await conn.fetchval("""SELECT EXISTS(SELECT FROM booking_stay_purge_fences
            WHERE organization_id=$1 AND booking_id=$2)""", org, booking):
            continue  # The final verifier requires exact matching evidence and absence.
        original_subject = await conn.fetchval("""SELECT original_subject_id FROM booking_stays
            WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE""", org, booking)
        restored_requests = await conn.fetch("""SELECT request_id FROM booking_allocation_requests
            WHERE organization_id=$1 AND booking_id=$2""", org, booking)
        if original_subject != subject or {row["request_id"] for row in restored_requests} != requests:
            raise RestoreBlocked("Restored stay lineage does not match authenticated purge")
        await conn.fetchval("SELECT public.archive_completed_hotel_allocation($1,$2)", org, booking)
        await conn.fetchval("SELECT public.booking_stay_admin_purge($1,$2,'retention',NULL,$3)",
                            org, booking, version)


def _current_restore_policies(authority, store, scopes, backup_id, inventory, policy_args):
    """Authenticate and compare current policy and hold state across recoverable backups."""
    current = {}
    for org in sorted(scopes):
        relevant = [candidate for candidate, orgs, _, _ in inventory if org in orgs] if inventory else [backup_id]
        expected = None
        for candidate in relevant:
            pin = authority.policy_pin(org, candidate)
            sealed, policy = authority.read_policy(store, pin, **policy_args)
            if expected is not None and policy != expected:
                raise RestoreBlocked("Current policies or legal holds differ across recoverable backups")
            expected = policy
            current[org, candidate] = (pin, sealed)
    return current


async def replay_primary_published_erasures(
    pool, authority, store, *, quarantine_database, policy_verification_key,
    policy_encryption_key, ordinary_inventory_bytes=None, latest_inventory_sha256=None, **trust,
):
    """Replay only authenticated entries on a dedicated database under current policy.

    The isolated pool, permitted operator and independently retained policy pins
    are prerequisites. Each local erasure commits atomically and is retryable.
    This workflow has no provider dispatch or traffic controls.
    """
    policy_args = dict(verification_key=policy_verification_key, encryption_key=policy_encryption_key)
    backup_id = trust["backup_id"]
    evidence_trust = dict(trust, recovery_inventory_bytes=ordinary_inventory_bytes,
                          latest_inventory_sha256=latest_inventory_sha256)
    active_at_start = authority.assert_paged_inventory(latest_inventory_sha256, required=False)

    def assert_active_inventory():
        if _assert_paged_inventory_current(authority, checkpoints, latest_inventory_sha256) != active_at_start:
            raise RestoreBlocked("Paged inventory activation changed during replay")

    async with pool.acquire() as conn, conn.transaction():
        await _assert_quarantine(conn, quarantine_database)
        tip, pins, journal, scopes, checkpoints = await _published_restore_evidence(conn, authority, store, **evidence_trust)
        inventory = None
        assert_active_inventory()
        if active_at_start:
            inventory = _assert_recovery_inventory(authority, ordinary_inventory_bytes, latest_inventory_sha256,
                backup_id, set(scopes), trust["backup_checkpoint"])
        policy_state = _current_restore_policies(authority, store, scopes, backup_id, inventory, policy_args)
        for org in sorted(scopes):
            pin, ciphertext = policy_state[org, backup_id]
            await reconcile_policy_state(
                conn, org, ciphertext, installation_id=authority.installation_id,
                backup_id=backup_id, independent_pin=pin, **policy_args)
        _assert_policy_current(authority, policy_state)
        _assert_current(authority, backup_id, tip, pins)
        assert_active_inventory()
        # A known proposal can be expired before a later signed subject
        # erasure removes its preimage. The same current policy and hold gate
        # applies to both operations.
        for org in scopes:
            if not checkpoints[org][-1]:
                continue
            from src.core.gdpr.orphan_proposal_expiry import expire_inventory_covered_orphan_proposals

            await expire_inventory_covered_orphan_proposals(conn, authority, store, org, ordinary_inventory_bytes,
                latest_inventory_sha256=latest_inventory_sha256,
                restored_backup_id=backup_id, quarantine_database=quarantine_database,
                trusted_public_key=trust["trusted_public_key"], journal_encryption_key=trust["journal_encryption_key"],
                checkpoint_verification_key=trust["checkpoint_verification_key"],
                checkpoint_encryption_key=trust["checkpoint_encryption_key"],
                policy_verification_key=policy_verification_key, policy_encryption_key=policy_encryption_key,
                key_epochs=trust.get("key_epochs"))
        _assert_policy_current(authority, policy_state)
        _assert_current(authority, backup_id, tip, pins)
        assert_active_inventory()
    for entry in journal:
        _assert_current(authority, backup_id, tip, pins)
        _assert_policy_current(authority, policy_state)
        assert_active_inventory()
        async with pool.acquire() as conn, conn.transaction():
            await _assert_quarantine(conn, quarantine_database)
            requester = await _materialize_recovery_request(conn, entry)
        try:
            await Deletion(pool).advance(
                entry.organization_id, entry.operation_id, requester, journal_evidence=entry)
        except (ValueError, ErasureBlocked):
            raise RestoreBlocked("Authenticated erasure could not be reconciled") from None
        async with pool.acquire() as conn:
            confirmed = await conn.fetchval("""SELECT EXISTS(SELECT FROM gdpr_operation_steps
                WHERE organization_id=$1 AND operation_id=$2 AND name='local' AND status='confirmed')""",
                entry.organization_id, entry.operation_id)
            if not confirmed:
                raise RestoreBlocked("Authenticated erasure blocked by current policy or evidence")
    async with pool.acquire() as conn, conn.transaction():
        await _assert_quarantine(conn, quarantine_database)
        _assert_current(authority, backup_id, tip, pins)
        _assert_policy_current(authority, policy_state)
        assert_active_inventory()
        for org in scopes:
            pin, ciphertext = policy_state[org, backup_id]
            await verify_policy_state(
                conn, org, ciphertext, installation_id=authority.installation_id,
                backup_id=backup_id, independent_pin=pin, **policy_args)
            _, _, _, purges, prunes, ordinary, _ = checkpoints[org]
            await _replay_stay_retention(conn, org, purges)
            await _replay_receipt_prunes(conn, org, prunes, pins[org])
            if ordinary:
                from src.core.gdpr.source_checkpoint import purge_inventory_covered_ordinary_bookings
                _assert_recovery_inventory(authority, ordinary_inventory_bytes, latest_inventory_sha256,
                                           backup_id, set(trust["expected_organizations"]), trust["backup_checkpoint"])
                await purge_inventory_covered_ordinary_bookings(conn,authority,store,org,ordinary_inventory_bytes,
                    latest_inventory_sha256=latest_inventory_sha256,
                    trusted_public_key=trust["trusted_public_key"],journal_encryption_key=trust["journal_encryption_key"],
                    checkpoint_verification_key=trust["checkpoint_verification_key"],
                    checkpoint_encryption_key=trust["checkpoint_encryption_key"],
                    policy_verification_key=policy_verification_key,policy_encryption_key=policy_encryption_key,
                    key_epochs=trust.get("key_epochs"))
        result = await verify_primary_published_restore(conn, authority, store, **evidence_trust)
        _assert_current(authority, backup_id, tip, pins)
        authority.validate_segment_schedule(tip, trust["trusted_public_key"], trust["journal_encryption_key"],
                                            trust.get("key_epochs"))
        _assert_policy_current(authority, policy_state)
        assert_active_inventory()
    return dict(result, current_policy_reconciliation_required=False)


def _assert_policy_current(authority, policy_state):
    if any(authority.policy_pin(org, backup_id) != pin
           for (org, backup_id), (pin, _) in policy_state.items()):
        raise RestoreBlocked("Independent policy evidence changed during verification")
