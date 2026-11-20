"""Read-only owner capture of v3 checkpoint evidence from a frozen source."""

from datetime import datetime, timezone
from uuid import UUID

from src.core.gdpr.journal_publication import CheckpointPin, _canonical, _previous_key_args, read_checkpoint
from src.core.gdpr.restore_checkpoint import (
    RestoreBlocked, _bind_receipt_prune_checkpoint, _parse_receipt_prunes, _receipt_prune_digest,
)
from src.core.gdpr.backup_coverage import _backups, assess_backup_coverage

_LIMIT = 10000


async def capture_committed_checkpoint(conn, authority, store, org, backup_id, *,
                                       sequence, verification_key, encryption_key,
                                       prior_pin=None, prior_keys=None, pending_requests=()):
    """Capture committed erasures, stay purges and bound receipt tuples.

    A prior independently pinned checkpoint is necessary when a receipt tuple
    has already been retired locally. It may belong to a different backup of
    the same tenant. No historical tuple is reconstructed from a digest.
    Freeze source writes and independent pin advancement through publication.
    """
    if (type(pending_requests) not in (tuple, list) or len(pending_requests)>_LIMIT
            or any(type(value) is not UUID or not value.int for value in pending_requests)
            or len(set(pending_requests))!=len(pending_requests)
            or not isinstance(org, UUID) or not org.int or type(sequence) is not int
            or not 1 <= sequence < 2**63 or not isinstance(backup_id, str)
            or not 1 <= len(backup_id) <= 128 or not conn.is_in_transaction()
            or await conn.fetchval("SHOW transaction_isolation") != "read committed"
            or not await conn.fetchval("""SELECT current_setting('transaction_read_only')='on'
                AND EXISTS(SELECT FROM pg_class c
                    WHERE c.oid='public.c24_receipt_prune_checkpoint_bindings'::regclass
                    AND c.relowner=current_user::regrole)""")):
        raise RestoreBlocked("Read-only checkpoint capture requires the lifecycle owner")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org))
    if not await conn.fetchval("SELECT EXISTS(SELECT FROM organizations WHERE id=$1)", org):
        raise RestoreBlocked("Checkpoint organization unavailable")
    previous = []
    prior_args = _previous_key_args(prior_pin, prior_keys, verification_key, encryption_key)
    if prior_pin is not None:
        if (type(prior_pin) is not CheckpointPin or prior_pin.organization_id != org
                or authority.checkpoint_pin(org, prior_pin.backup_id) != prior_pin):
            raise RestoreBlocked("Prior independent checkpoint pin is not current")
        checkpoint = read_checkpoint(store, authority.installation_id, org, prior_pin.backup_id, prior_pin,
                                     **prior_args)
        previous = _parse_receipt_prunes(checkpoint["payload"]["receipt_prunes"])
    carried = {item[0]: item for item in previous}
    if await conn.fetchval("""SELECT EXISTS (SELECT FROM gdpr_operation_steps s
        WHERE s.organization_id=$1 AND s.name='local' AND s.status='confirmed'
        AND NOT EXISTS(SELECT FROM gdpr_erasure_barriers b
            WHERE b.organization_id=$1 AND b.operation_id=s.operation_id))""", org):
        raise RestoreBlocked("Confirmed erasure is missing a source barrier")
    erasures = await conn.fetch("""SELECT b.operation_id,b.scope,b.subject_id
        FROM gdpr_erasure_barriers b JOIN gdpr_operations o ON o.organization_id=b.organization_id
          AND o.id=b.operation_id AND o.kind='erase' AND o.status IN ('partial','completed') AND o.scope=b.scope
          AND o.subject_id IS NOT DISTINCT FROM b.subject_id
        JOIN gdpr_operation_steps s ON s.organization_id=b.organization_id AND s.operation_id=b.operation_id
          AND s.name='local' AND s.status='confirmed'
        WHERE b.organization_id=$1 AND o.organization_id=$1 AND s.organization_id=$1
        ORDER BY b.operation_id LIMIT $2""", org, _LIMIT+1)
    if (len(erasures)>_LIMIT or len(erasures)!=await conn.fetchval(
            "SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1", org)):
        raise RestoreBlocked("Committed erasure evidence is incomplete or exceeds v3 capacity")
    fences = await conn.fetch("""SELECT booking_id,original_subject_id,purpose,operation_id,policy_version,receipt_count
        FROM booking_stay_purge_fences WHERE organization_id=$1 ORDER BY booking_id LIMIT $2""", org, _LIMIT+1)
    if len(fences)>_LIMIT:
        raise RestoreBlocked("Stay purge evidence exceeds v3 capacity")
    stays = []
    receipt_count = 0
    stay_bytes = 2
    for fence in fences:
        booking = fence["booking_id"]
        requests = await conn.fetch("""SELECT request_id,original_subject_id,purpose,operation_id,policy_version
            FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2
            ORDER BY request_id LIMIT $3""", org, booking, _LIMIT+1)
        if (len(requests)!=fence["receipt_count"] or len(requests)>_LIMIT or any(
                tuple(row[field] for field in ("original_subject_id","purpose","operation_id","policy_version"))
                != tuple(fence[field] for field in ("original_subject_id","purpose","operation_id","policy_version"))
                for row in requests)):
            raise RestoreBlocked("Stay purge receipt evidence is incomplete")
        receipt_count += len(requests)
        item = dict(booking_id=str(booking), subject_id=str(fence["original_subject_id"]),
                          purpose=fence["purpose"], operation_id=str(fence["operation_id"]) if fence["operation_id"] else None,
                          policy_version=fence["policy_version"], request_ids=[str(row["request_id"]) for row in requests])
        stay_bytes += len(_canonical(item)) + bool(stays)
        if stay_bytes>2*1024*1024:
            raise RestoreBlocked("Stay purge evidence exceeds v3 capacity")
        stays.append(item)
    if receipt_count!=await conn.fetchval("SELECT count(*) FROM booking_stay_tombstones WHERE organization_id=$1", org):
        raise RestoreBlocked("Stay purge receipt evidence is incomplete")
    bindings = await conn.fetch("""SELECT b.request_id,b.tuple_sha256,b.checkpoint_sequence,
        f.source_message_id,f.conversation_id,f.subject_id
        FROM c24_receipt_prune_checkpoint_bindings b LEFT JOIN c24_source_fences f
          ON f.organization_id=b.organization_id AND f.organization_id=$1 AND f.request_id=b.request_id
        WHERE b.organization_id=$1 ORDER BY b.request_id LIMIT $2""", org, _LIMIT+1)
    if len(bindings)>_LIMIT:
        raise RestoreBlocked("Receipt commitment evidence exceeds v3 capacity")
    prunes = []
    bound_requests = set()
    for row in bindings:
        request = row["request_id"]
        bound_requests.add(request)
        local = (request,row["source_message_id"],row["conversation_id"],row["subject_id"])
        item = carried.pop(request, None)
        if None not in local:
            if item is not None and item != local:
                raise RestoreBlocked("Prior receipt evidence conflicts with source tuple")
            item = local
        if item is None or _receipt_prune_digest(org,item)!=row["tuple_sha256"] or sequence<row["checkpoint_sequence"]:
            raise RestoreBlocked("Required historical receipt tuple or sequence unavailable")
        prunes.append(dict(zip(("request_id","source_message_id","conversation_id","subject_id"), map(str,item))))
    for request in sorted(set(pending_requests)-bound_requests):
        row = await conn.fetchrow("""SELECT f.source_message_id,f.conversation_id,f.subject_id
            FROM c24_source_fences f JOIN messages m ON m.organization_id=f.organization_id
              AND m.conversation_id=f.conversation_id AND m.id=f.source_message_id
              AND m.c24_original_subject_id=f.subject_id AND m.organization_id=$1
            JOIN c24_receipt_cleanup_audit a ON a.organization_id=f.organization_id
              AND a.request_id=f.request_id AND a.organization_id=$1
            WHERE f.organization_id=$1 AND f.request_id=$2 AND f.c24_concluded_at IS NOT NULL
              AND m.direction='inbound' AND m.wam_id IS NOT NULL AND m.content_purged_at IS NOT NULL
              AND NOT EXISTS(SELECT FROM booking_conversation_requests r
                WHERE r.organization_id=$1 AND r.request_id=f.request_id)
              AND public.c24_receipt_source_copies_absent($1,f.source_message_id)""", org, request)
        if not row:
            raise RestoreBlocked("Pending receipt lacks verified source and cleanup evidence")
        item = (request,row["source_message_id"],row["conversation_id"],row["subject_id"])
        prior = carried.pop(request,None)
        if prior is not None and prior != item:
            raise RestoreBlocked("Prior pending receipt conflicts with source tuple")
        prunes.append(dict(zip(("request_id","source_message_id","conversation_id","subject_id"), map(str,item))))
    if len(prunes)>_LIMIT:
        raise RestoreBlocked("Receipt commitment evidence exceeds v3 capacity")
    if carried:
        raise RestoreBlocked("Prior checkpoint includes receipt evidence not bound at source")
    payload = dict(version="c24-restore-v3", organization_id=str(org), backup_id=backup_id,
        sequence=sequence, complete=True, erasures=[
            dict(operation_id=str(row["operation_id"]), scope=row["scope"],
                 subject_id=str(row["subject_id"]) if row["subject_id"] else None) for row in erasures],
        stay_purges=stays, receipt_prunes=prunes)
    if len(_canonical(payload))>2*1024*1024:
        raise RestoreBlocked("Checkpoint exceeds v3 capacity")
    if prior_pin is not None and authority.checkpoint_pin(org, prior_pin.backup_id) != prior_pin:
        raise RestoreBlocked("Prior independent checkpoint changed during capture")
    return payload


async def bind_inventory_covered_receipts(conn, authority, store, org, inventory_bytes, *,
                                          latest_inventory_sha256, **trust):
    """Bind receipt commitments after every relevant backup has the same evidence.

    Requires the independently attested inventory and frozen source/authority
    maintenance window. This records commitments, and does not execute a purge.
    Current policy and eligibility are still enforced by the SQL retention path.
    """
    if (type(org) is not UUID or not org.int or not conn.is_in_transaction()
            or await conn.fetchval("SHOW transaction_isolation") != "read committed"
            or not await conn.fetchval("""SELECT current_setting('transaction_read_only')='off'
                AND EXISTS(SELECT FROM pg_class WHERE oid='public.c24_receipt_prune_checkpoint_bindings'::regclass
                AND relowner=current_user::regrole)""")):
        raise RestoreBlocked("Receipt binding requires a read committed owner transaction")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org))
    audit_at = trust.get("audit_at") or datetime.now(timezone.utc)
    trust = dict(trust, audit_at=audit_at)
    tip = authority.current()
    assess_backup_coverage(authority, store, inventory_bytes, latest_inventory_sha256=latest_inventory_sha256,
                           **trust)
    backups = list(_backups(inventory_bytes, latest_inventory_sha256, authority.installation_id, audit_at))
    if authority.current() != tip:
        raise RestoreBlocked("Independent recovery evidence changed during coverage check")
    pins, policy_pins = {}, {}
    evidence, selected = None, None
    key_args = dict(verification_key=trust["checkpoint_verification_key"],
                    encryption_key=trust["checkpoint_encryption_key"])
    for backup, orgs, _, _ in backups:
        if org not in orgs:
            continue
        pin = authority.checkpoint_pin(org, backup)
        policy = authority.policy_pin(org, backup)
        checkpoint = read_checkpoint(store, authority.installation_id, org, backup, pin, **key_args)
        policy_data, _ = authority.read_policy(
            store, policy, verification_key=trust["policy_verification_key"],
            encryption_key=trust["policy_encryption_key"])
        from src.core.gdpr.restore_policy_state import verify_policy_state
        await verify_policy_state(
            conn, org, policy_data, installation_id=authority.installation_id, backup_id=backup,
            independent_pin=policy, verification_key=trust["policy_verification_key"],
            encryption_key=trust["policy_encryption_key"])
        tuples = set(_parse_receipt_prunes(checkpoint["payload"]["receipt_prunes"]))
        if evidence is not None and evidence != tuples:
            raise RestoreBlocked("Receipt commitments differ across recoverable backups")
        evidence = tuples
        pins[backup], policy_pins[backup] = pin, policy
        if selected is None or pin.sequence < selected[0].sequence:
            selected = (pin, checkpoint)
    if selected is None:
        raise RestoreBlocked("No independently covered backup for receipt commitments")
    pin, checkpoint = selected
    async with conn.transaction():
        result = await _bind_receipt_prune_checkpoint(
            conn, org, checkpoint, verification_key=key_args["verification_key"],
            backup_id=pin.backup_id, minimum_sequence=pin.sequence, checkpoint_sha256=pin.sha256)
        assess_backup_coverage(authority, store, inventory_bytes,
                               latest_inventory_sha256=latest_inventory_sha256, **trust)
        if (authority.current()!=tip or any(
                authority.checkpoint_pin(org, backup)!=value or authority.policy_pin(org, backup)!=policy_pins[backup]
                for backup, value in pins.items())):
            raise RestoreBlocked("Independent recovery evidence changed during binding")
    return result
