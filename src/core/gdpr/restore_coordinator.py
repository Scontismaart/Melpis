"""Verify restored primary C2.4 copies against an independent published journal.

Operator-only isolated recovery workflow. This is not a service endpoint,
never replays on its own and never enables traffic. Provider copies, current
policy reconciliation and deployment security remain separate required gates.
"""

from hashlib import sha256
import re
from uuid import UUID

from src.core.db.scoping import system_scope
from src.core.gdpr.deletion import Deletion
from src.core.gdpr.guard import ErasureBlocked
from src.core.gdpr.journal_publication import read_chain, read_checkpoint
from src.core.gdpr.operations import encode, event
from src.core.gdpr.restore_checkpoint import (
    RestoreBlocked, _authenticate_checkpoint, _bind_receipt_prune_checkpoint,
    _verify_replayed_erasures, _verify_replayed_receipt_prunes,
)
from src.core.gdpr.restore_journal import RestoreJournalInvalid
from src.core.gdpr.restore_policy_state import reconcile_policy_state, verify_policy_state


_QUARANTINE_NAME = re.compile(r"melpis_(?:restore_drill_[0-9a-f]{16,32}|c24_restore_dst_[0-9a-f]{12})")
_RECOVERY_REQUESTER = "gdpr:restore-replay"


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
    checkpoint_encryption_key, key_epochs=None,
):
    wanted = await _restore_inventory(conn, expected_organizations)
    installation = authority.installation_id
    try:
        tip = authority.current()
        authority.validate_segment_schedule(tip, trusted_public_key, journal_encryption_key, key_epochs)
        index = authority.chain_after(backup_checkpoint)
        journal = read_chain(
            store, installation, backup_checkpoint, index, tip,
            trusted_public_key=trusted_public_key, encryption_key=journal_encryption_key,
            key_epochs=key_epochs)
        if any(entry.organization_id not in wanted for entry in journal):
            raise RestoreBlocked("Journal scope not covered by recovery inventory")
        scopes = {org: [] for org in wanted}
        for entry in journal:
            scopes[entry.organization_id].append(entry)
        pins, checkpoints = {}, {}
        for org in sorted(wanted):
            pin = authority.checkpoint_pin(org, backup_id)
            pins[org] = pin
            checkpoint = read_checkpoint(
                store, installation, org, backup_id, pin,
                verification_key=checkpoint_verification_key,
                encryption_key=checkpoint_encryption_key)
            _, checkpoint_entries, sequence, purges, prunes = _authenticate_checkpoint(
                org, checkpoint, verification_key=checkpoint_verification_key,
                backup_id=backup_id, minimum_sequence=pin.sequence, checkpoint_sha256=pin.sha256)
            scoped = scopes[org]
            signed_entries = [(entry.operation_id, entry.scope, entry.subject_id) for entry in scoped]
            if not set(signed_entries) <= set(checkpoint_entries):
                raise RestoreBlocked("Checkpoint omits authenticated erasure evidence")
            checkpoints[org] = (checkpoint, checkpoint_entries, sequence, purges, prunes)
        _assert_current(authority, backup_id, tip, pins)
    except RestoreJournalInvalid:
        raise RestoreBlocked("Authenticated independent recovery evidence required") from None
    return tip, pins, journal, scopes, checkpoints


def _assert_current(authority, backup_id, tip, pins):
    if authority.current() != tip or any(
            authority.checkpoint_pin(org, backup_id) != pin for org, pin in pins.items()):
        raise RestoreBlocked("Independent recovery evidence changed during verification")


async def verify_primary_published_restore(conn, authority, store, **trust):
    """Require every restored tenant and the exact current independent evidence."""
    tip, pins, journal, scopes, checkpoints = await _published_restore_evidence(
        conn, authority, store, **trust)
    for org, (_, entries, sequence, purges, prunes) in checkpoints.items():
        await _verify_replayed_erasures(conn, org, entries, sequence, purges=purges, receipt_prunes=prunes)
        scoped = scopes[org]
        await _verify_replayed_erasures(
            conn, org, [(entry.operation_id, entry.scope, entry.subject_id) for entry in scoped],
            tip.sequence, purges=purges, receipt_prunes=prunes,
            fingerprints={entry.operation_id: (entry.identifier_digest, entry.key_id) for entry in scoped})
    _assert_current(authority, trust["backup_id"], tip, pins)
    return {"verified_organizations": len(checkpoints), "verified_erasure_entries": len(journal),
            "sequence": tip.sequence, "scope": "primary_c24_only",
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


async def _replay_receipt_prunes(conn, org, checkpoint, prunes, pin, verification_key):
    if not prunes:
        return
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org))
    for request, source, _, _ in prunes:
        # PostgreSQL functions check the current verified scope, clock, safe
        # copy lifecycle and holds. Final proof requires actual postconditions.
        await conn.fetchval("SELECT public.c24_purge_source($1,$2)", org, source)
        await conn.fetchval("SELECT public.c24_cleanup_receipt($1,$2)", org, request)
    await _bind_receipt_prune_checkpoint(
        conn, org, checkpoint, verification_key=verification_key, backup_id=pin.backup_id,
        minimum_sequence=pin.sequence, checkpoint_sha256=pin.sha256)
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


async def replay_primary_published_erasures(
    pool, authority, store, *, quarantine_database, policy_verification_key,
    policy_encryption_key, **trust,
):
    """Replay only authenticated entries on a dedicated database under current policy.

    The isolated pool, permitted operator and independently retained policy pins
    are prerequisites. Each local erasure commits atomically and is retryable.
    This workflow has no provider dispatch or traffic controls.
    """
    policy_args = dict(verification_key=policy_verification_key, encryption_key=policy_encryption_key)
    backup_id = trust["backup_id"]
    async with pool.acquire() as conn, conn.transaction():
        await _assert_quarantine(conn, quarantine_database)
        tip, pins, journal, scopes, checkpoints = await _published_restore_evidence(conn, authority, store, **trust)
        policy_state = {}
        for org in sorted(scopes):
            pin = authority.policy_pin(org, backup_id)
            ciphertext, _ = authority.read_policy(store, pin, **policy_args)
            policy_state[org] = (pin, ciphertext)
            await reconcile_policy_state(
                conn, org, ciphertext, installation_id=authority.installation_id,
                backup_id=backup_id, independent_pin=pin, **policy_args)
        _assert_policy_current(authority, backup_id, policy_state)
    for entry in journal:
        _assert_current(authority, backup_id, tip, pins)
        _assert_policy_current(authority, backup_id, policy_state)
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
        _assert_policy_current(authority, backup_id, policy_state)
        for org, (pin, ciphertext) in policy_state.items():
            await verify_policy_state(
                conn, org, ciphertext, installation_id=authority.installation_id,
                backup_id=backup_id, independent_pin=pin, **policy_args)
            checkpoint, _, _, purges, prunes = checkpoints[org]
            await _replay_stay_retention(conn, org, purges)
            await _replay_receipt_prunes(
                conn, org, checkpoint, prunes, pins[org], trust["checkpoint_verification_key"])
        result = await verify_primary_published_restore(conn, authority, store, **trust)
        _assert_policy_current(authority, backup_id, policy_state)
    return dict(result, current_policy_reconciliation_required=False)


def _assert_policy_current(authority, backup_id, policy_state):
    if any(authority.policy_pin(org, backup_id) != pin
           for org, (pin, _) in policy_state.items()):
        raise RestoreBlocked("Independent policy evidence changed during verification")
