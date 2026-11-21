"""Read-only owner capture of v3 checkpoint evidence from a frozen source."""

from datetime import datetime, timezone
import os
from uuid import UUID

from src.core.gdpr.checkpoint_pages import (
    _MAX_AGGREGATE_BYTES, _MAX_RECORDS, _assert_global_uniqueness, _normalize,
    decode_receipt_prunes, read_any_checkpoint,
)
from src.core.gdpr.journal_publication import CheckpointPin, _canonical, _previous_key_args
from src.core.gdpr.restore_checkpoint import (
    RestoreBlocked, _bind_verified_receipt_prunes, _parse_receipt_prunes, _parse_ordinary_purges, _receipt_prune_digest,
)
from src.core.gdpr.backup_coverage import _backups, assess_backup_coverage

_LIMIT = 10000


async def capture_due_ordinary_bookings(conn, org, *, after=None, limit=100):
    """Select one bounded owner-only batch for signed ordinary-booking plans.

    The cursor advances over examined evidence even if a booking is held or
    lacks a complete plan. A new sweep restarts at the beginning. Selection
    never authorizes deletion; the existing checkpoint promotion and
    inventory-covered purge must succeed before any source mutation.
    """
    if (type(org) is not UUID or not org.int
            or (after is not None and (type(after) is not UUID or not after.int))
            or type(limit) is not int or not 1 <= limit <= 1000
            or not conn.is_in_transaction()
            or await conn.fetchval("SHOW transaction_isolation") != "read committed"
            or not await conn.fetchval("""SELECT current_setting('transaction_read_only')='on'
                AND EXISTS(SELECT FROM pg_class WHERE oid=to_regclass('public.ordinary_booking_completion_evidence')
                AND relowner=current_user::regrole)""")):
        raise RestoreBlocked("Ordinary booking selection requires a read-only lifecycle-owner transaction")
    if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended($1::text,606))", str(org)):
        raise RestoreBlocked("Ordinary booking selection deferred by tenant contention")
    rows = await conn.fetch("""SELECT booking_id FROM public.ordinary_booking_completion_evidence
        WHERE organization_id=$1 AND ($2::uuid IS NULL OR booking_id>$2)
        ORDER BY booking_id LIMIT $3""", org, after, limit + 1)
    batch = rows[:limit]
    due = []
    for row in batch:
        if await conn.fetchval("SELECT public.ordinary_completed_booking_retention_plan($1,$2)",
                               org, row["booking_id"]) is not None:
            due.append(UUID(str(row["booking_id"])))
    return tuple(due), UUID(str(batch[-1]["booking_id"])) if len(rows) > limit else None


async def capture_committed_checkpoint(conn, authority, store, org, backup_id, *,
                                       sequence, verification_key, encryption_key,
                                       prior_pin=None, prior_keys=None, pending_requests=(),
                                       pending_ordinary_bookings=(), pending_orphan_sources=(), paged_candidate=False):
    """Capture committed erasures, stay purges and bound receipt tuples.

    A prior independently pinned checkpoint is necessary when a receipt tuple
    has already been retired locally. It may belong to a different backup of
    the same tenant. No historical tuple is reconstructed from a digest.
    Freeze source writes and independent pin advancement through publication.
    The explicit paged mode prepares evidence for the v5 candidate codec.
    """
    if type(paged_candidate) is not bool:
        raise RestoreBlocked("Checkpoint format selection invalid")
    if (type(pending_orphan_sources) not in (list, tuple) or len(pending_orphan_sources) > _LIMIT
            or any(type(value) is not UUID or not value.int for value in pending_orphan_sources)
            or len(set(pending_orphan_sources)) != len(pending_orphan_sources)
            or (pending_orphan_sources and not paged_candidate)):
        raise RestoreBlocked("Orphan proposal evidence requires an exact paged selection")
    collection_limit = _MAX_RECORDS if paged_candidate else _LIMIT
    size_limit = _MAX_AGGREGATE_BYTES if paged_candidate else 2*1024*1024
    if (type(pending_ordinary_bookings) not in (tuple,list) or len(pending_ordinary_bookings)>_LIMIT
            or any(type(value) is not UUID or not value.int for value in pending_ordinary_bookings)
            or len(set(pending_ordinary_bookings))!=len(pending_ordinary_bookings)
            or type(pending_requests) not in (tuple, list) or len(pending_requests)>_LIMIT
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
    previous_ordinary = []
    previous_orphans = []
    prior_v4 = False
    prior_args = _previous_key_args(prior_pin, prior_keys, verification_key, encryption_key)
    if prior_pin is not None:
        if (type(prior_pin) is not CheckpointPin or prior_pin.organization_id != org
                or authority.checkpoint_pin(org, prior_pin.backup_id) != prior_pin):
            raise RestoreBlocked("Prior independent checkpoint pin is not current")
        prior = read_any_checkpoint(store, authority.installation_id, org, prior_pin.backup_id, prior_pin, **prior_args)
        if prior["version"] == "c24-restore-v5" and not paged_candidate:
            raise RestoreBlocked("Checkpoint format cannot regress")
        previous_items = prior["receipt_prunes"]
        previous = [record for offset in range(0, len(previous_items), _LIMIT)
                    for record in _parse_receipt_prunes(previous_items[offset:offset+_LIMIT])]
        prior_v4 = prior["version"] in ("c24-restore-v4", "c24-restore-v5")
        previous_ordinary = prior.get("ordinary_booking_purges", [])
        previous_orphans = prior.get("orphan_proposal_prunes", [])
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
        ORDER BY b.operation_id LIMIT $2""", org, collection_limit+1)
    if (len(erasures)>collection_limit or len(erasures)!=await conn.fetchval(
            "SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1", org)):
        raise RestoreBlocked("Committed erasure evidence is incomplete or exceeds format capacity")
    fences = await conn.fetch("""SELECT booking_id,original_subject_id,purpose,operation_id,policy_version,receipt_count
        FROM booking_stay_purge_fences WHERE organization_id=$1
          AND COALESCE(to_jsonb(booking_stay_purge_fences)->>'purge_kind','stay')='stay'
        ORDER BY booking_id LIMIT $2""", org, collection_limit+1)
    if len(fences)>collection_limit:
        raise RestoreBlocked("Stay purge evidence exceeds format capacity")
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
        if stay_bytes>size_limit:
            raise RestoreBlocked("Stay purge evidence exceeds format capacity")
        stays.append(item)
    if receipt_count!=await conn.fetchval("""SELECT count(*) FROM booking_stay_tombstones t
        JOIN booking_stay_purge_fences f ON f.organization_id=t.organization_id AND f.booking_id=t.booking_id
        WHERE t.organization_id=$1 AND f.organization_id=$1
        AND COALESCE(to_jsonb(f)->>'purge_kind','stay')='stay'""", org):
        raise RestoreBlocked("Stay purge receipt evidence is incomplete")
    if await conn.fetchval("""SELECT EXISTS(SELECT FROM booking_stay_tombstones t
        WHERE t.organization_id=$1 AND NOT EXISTS(SELECT FROM booking_stay_purge_fences f
          WHERE f.organization_id=$1 AND f.booking_id=t.booking_id))""", org):
        raise RestoreBlocked("Booking purge receipt lacks a source fence")
    bindings = await conn.fetch("""SELECT b.request_id,b.tuple_sha256,b.checkpoint_sequence,
        f.source_message_id,f.conversation_id,f.subject_id
        FROM c24_receipt_prune_checkpoint_bindings b LEFT JOIN c24_source_fences f
          ON f.organization_id=b.organization_id AND f.organization_id=$1 AND f.request_id=b.request_id
        WHERE b.organization_id=$1 ORDER BY b.request_id LIMIT $2""", org, collection_limit+1)
    if len(bindings)>collection_limit:
        raise RestoreBlocked("Receipt commitment evidence exceeds format capacity")
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
    if len(prunes)>collection_limit:
        raise RestoreBlocked("Receipt commitment evidence exceeds format capacity")
    if carried:
        raise RestoreBlocked("Prior checkpoint includes receipt evidence not bound at source")
    payload = dict(version="c24-restore-v3", organization_id=str(org), backup_id=backup_id,
        sequence=sequence, complete=True, erasures=[
            dict(operation_id=str(row["operation_id"]), scope=row["scope"],
                 subject_id=str(row["subject_id"]) if row["subject_id"] else None) for row in erasures],
        stay_purges=stays, receipt_prunes=prunes)
    ordinary = []
    ordinary_bytes = 2
    if await conn.fetchval("SELECT to_regclass('public.ordinary_booking_purge_bindings') IS NOT NULL"):
        rows = conn.cursor("""SELECT booking_id,plan,checkpoint_sequence FROM ordinary_booking_purge_bindings
            WHERE organization_id=$1 ORDER BY booking_id LIMIT $2""", org, collection_limit+1, prefetch=64)
        from src.core.gdpr.operations import document
        existing = set()
        async for row in rows:
            if len(ordinary) == collection_limit:
                raise RestoreBlocked("Ordinary booking purge evidence exceeds checkpoint capacity")
            plan = document(row["plan"])
            if (row["checkpoint_sequence"]>sequence or str(row["booking_id"])!=plan["booking_id"]
                    or not await conn.fetchval("SELECT public.ordinary_verify_booking_retention_purge($1,$2,$3::jsonb)",
                                                org,row["booking_id"],_canonical(plan).decode())):
                raise RestoreBlocked("Ordinary booking committed evidence is incomplete")
            ordinary_bytes += len(_canonical(plan)) + bool(ordinary)
            if ordinary_bytes + stay_bytes > size_limit:
                raise RestoreBlocked("Booking purge evidence exceeds format capacity")
            ordinary.append(plan)
            existing.add(row["booking_id"])
        if len(ordinary) != await conn.fetchval("""SELECT count(*) FROM booking_stay_purge_fences
            WHERE organization_id=$1 AND purge_kind='ordinary'""", org):
            raise RestoreBlocked("Ordinary booking purge evidence is incomplete")
        for booking in sorted(set(pending_ordinary_bookings)-existing):
            raw = await conn.fetchval("SELECT public.ordinary_completed_booking_retention_plan($1,$2)",org,booking)
            if raw is None:
                raise RestoreBlocked("Ordinary booking lacks verified retention eligibility")
            plan = document(raw)
            ordinary_bytes += len(_canonical(plan)) + bool(ordinary)
            if ordinary_bytes + stay_bytes > size_limit:
                raise RestoreBlocked("Booking purge evidence exceeds format capacity")
            ordinary.append(plan)
    elif pending_ordinary_bookings or prior_v4:
        raise RestoreBlocked("Ordinary booking purge schema unavailable")
    if ordinary or prior_v4 or paged_candidate:
        if len(ordinary) > collection_limit:
            raise RestoreBlocked("Ordinary booking purge evidence exceeds format capacity")
        try:
            for offset in range(0, len(ordinary), _LIMIT):
                _parse_ordinary_purges(org, ordinary[offset:offset+_LIMIT])
        except (ValueError, TypeError, KeyError):
            raise RestoreBlocked("Invalid ordinary booking checkpoint evidence") from None
        if not {_canonical(plan) for plan in previous_ordinary}<={_canonical(plan) for plan in ordinary}:
            raise RestoreBlocked("Prior ordinary booking purge evidence is incomplete")
        payload.update(version="c24-restore-v5" if paged_candidate else "c24-restore-v4",
                       ordinary_booking_purges=ordinary)
    if await conn.fetchval("SELECT to_regclass('public.c24_orphan_proposal_expirations') IS NOT NULL"):
        if paged_candidate:
            from src.core.gdpr.operations import document
            from src.core.gdpr.orphan_proposal import plan_orphan_proposal_expiry
            from src.core.gdpr.orphan_proposal_expiry import verify_orphan_proposal_expirations

            orphans, known = [], set()
            rows = conn.cursor("""SELECT source_message_id,plan,checkpoint_sequence
                FROM c24_orphan_proposal_expirations WHERE organization_id=$1 ORDER BY source_message_id
                LIMIT $2""", org, collection_limit+1, prefetch=64)
            async for row in rows:
                plan = document(row["plan"])
                if (len(orphans) == collection_limit or row["checkpoint_sequence"] > sequence
                        or str(row["source_message_id"]) != plan["source_message_id"]):
                    raise RestoreBlocked("Committed orphan proposal evidence is incomplete")
                orphans.append(plan)
                known.add(row["source_message_id"])
            await verify_orphan_proposal_expirations(conn, org, orphans)
            for source in sorted(set(pending_orphan_sources) - known):
                plan = await plan_orphan_proposal_expiry(conn, org, source)
                if plan is None:
                    raise RestoreBlocked("Pending orphan proposal lacks current verified evidence")
                orphans.append(plan)
            if not {_canonical(plan) for plan in previous_orphans} <= {_canonical(plan) for plan in orphans}:
                raise RestoreBlocked("Prior orphan proposal evidence is incomplete")
            if orphans:
                payload["orphan_proposal_prunes"] = orphans
        elif await conn.fetchval("SELECT EXISTS(SELECT FROM c24_orphan_proposal_expirations WHERE organization_id=$1)", org):
            raise RestoreBlocked("Verified orphan proposal evidence requires the paged checkpoint format")
    elif pending_orphan_sources or previous_orphans:
        raise RestoreBlocked("Orphan proposal expiry schema is unavailable")
    if len(_canonical(payload))>size_limit:
        raise RestoreBlocked("Checkpoint exceeds format capacity")
    if paged_candidate:
        try:
            payload = _normalize(payload)
            _assert_global_uniqueness(payload)
        except (ValueError, TypeError, KeyError):
            raise RestoreBlocked("Paged checkpoint source evidence is invalid") from None
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
    active_inventory = authority.assert_paged_inventory(latest_inventory_sha256, required=False)
    assess_backup_coverage(authority, store, inventory_bytes, latest_inventory_sha256=latest_inventory_sha256,
                           **trust)
    backups = list(_backups(inventory_bytes, latest_inventory_sha256, authority.installation_id, audit_at))
    if authority.current() != tip:
        raise RestoreBlocked("Independent recovery evidence changed during coverage check")
    pins, policy_pins = {}, {}
    evidence, selected = None, None
    requires_paged_inventory = False
    key_args = dict(verification_key=trust["checkpoint_verification_key"],
                    encryption_key=trust["checkpoint_encryption_key"])
    for backup, orgs, _, _ in backups:
        if org not in orgs:
            continue
        pin = authority.checkpoint_pin(org, backup)
        policy = authority.policy_pin(org, backup)
        payload = read_any_checkpoint(store, authority.installation_id, org, backup, pin, **key_args)
        if payload["version"] == "c24-restore-v5":
            requires_paged_inventory = True
        policy_data, _ = authority.read_policy(
            store, policy, verification_key=trust["policy_verification_key"],
            encryption_key=trust["policy_encryption_key"])
        from src.core.gdpr.restore_policy_state import verify_policy_state
        await verify_policy_state(
            conn, org, policy_data, installation_id=authority.installation_id, backup_id=backup,
            independent_pin=policy, verification_key=trust["policy_verification_key"],
            encryption_key=trust["policy_encryption_key"])
        prunes = decode_receipt_prunes(payload)
        tuples = set(prunes)
        if evidence is not None and evidence != tuples:
            raise RestoreBlocked("Receipt commitments differ across recoverable backups")
        evidence = tuples
        pins[backup], policy_pins[backup] = pin, policy
        if selected is None or pin.sequence < selected[0].sequence:
            selected = (pin, prunes)
    if selected is None:
        raise RestoreBlocked("No independently covered backup for receipt commitments")
    if requires_paged_inventory and not active_inventory:
        raise RestoreBlocked("Paged inventory changed before receipt binding")
    pin, prunes = selected
    async with conn.transaction():
        result = await _bind_verified_receipt_prunes(
            conn, org, prunes, sequence=pin.sequence, checkpoint_sha256=pin.sha256)
        assess_backup_coverage(authority, store, inventory_bytes,
                               latest_inventory_sha256=latest_inventory_sha256, **trust)
        if (authority.current()!=tip or any(
                authority.checkpoint_pin(org, backup)!=value or authority.policy_pin(org, backup)!=policy_pins[backup]
                for backup, value in pins.items())):
            raise RestoreBlocked("Independent recovery evidence changed during binding")
        if authority.assert_paged_inventory(latest_inventory_sha256, required=requires_paged_inventory) != active_inventory:
            raise RestoreBlocked("Paged inventory changed during receipt binding")
    return result


async def purge_inventory_covered_ordinary_bookings(
    conn, authority, store, org, inventory_bytes, *, latest_inventory_sha256, **trust,
):
    """Owner-only maintenance operation for independently covered SQL109 plans.

    The operator must freeze source/authority/inventory maintenance and obtain
    the separate operational authorization before connecting to a live source.
    SQL109 rechecks the exact eligible preimage and policy on each mutation.
    """
    if os.getenv("GDPR_RETENTION_EXECUTION_ENABLED")!="true":
        raise RestoreBlocked("Ordinary retention is not operationally enabled")
    if (type(org) is not UUID or not org.int or not conn.is_in_transaction()
            or await conn.fetchval("SHOW transaction_isolation") != "read committed"
            or not await conn.fetchval("""SELECT current_setting('transaction_read_only')='off'
                AND EXISTS(SELECT FROM pg_class WHERE oid=to_regclass('public.ordinary_booking_purge_bindings')
                AND relowner=current_user::regrole)""")):
        raise RestoreBlocked("Ordinary purge requires a read committed owner transaction")
    await conn.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR NO KEY UPDATE",org)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org))
    audit_at=trust.get("audit_at") or datetime.now(timezone.utc)
    trust=dict(trust,audit_at=audit_at)
    tip=authority.current()
    active_inventory = authority.assert_paged_inventory(latest_inventory_sha256, required=False)
    assess_backup_coverage(authority,store,inventory_bytes,latest_inventory_sha256=latest_inventory_sha256,**trust)
    backups=list(_backups(inventory_bytes,latest_inventory_sha256,authority.installation_id,audit_at))
    if authority.current()!=tip:
        raise RestoreBlocked("Independent recovery evidence changed during coverage check")
    expected=None
    pins={}
    selected=None
    requires_paged_inventory = False
    for backup,orgs,_,_ in backups:
        if org not in orgs:
            continue
        pin=authority.checkpoint_pin(org,backup)
        policy=authority.policy_pin(org,backup)
        payload=read_any_checkpoint(store,authority.installation_id,org,backup,pin,
            verification_key=trust["checkpoint_verification_key"],encryption_key=trust["checkpoint_encryption_key"])
        if payload["version"] not in ("c24-restore-v4", "c24-restore-v5"):
            raise RestoreBlocked("Every relevant backup requires v4 or v5 ordinary purge coverage")
        if payload["version"] == "c24-restore-v5":
            requires_paged_inventory = True
        plans=payload["ordinary_booking_purges"]
        canonical={_canonical(plan) for plan in plans}
        if expected is not None and expected!=canonical:
            raise RestoreBlocked("Ordinary purge evidence differs across recoverable backups")
        expected=canonical
        policy_data,_=authority.read_policy(store,policy,verification_key=trust["policy_verification_key"],
                                           encryption_key=trust["policy_encryption_key"])
        from src.core.gdpr.restore_policy_state import verify_policy_state
        await verify_policy_state(conn,org,policy_data,installation_id=authority.installation_id,
            backup_id=backup,independent_pin=policy,verification_key=trust["policy_verification_key"],
            encryption_key=trust["policy_encryption_key"])
        pins[backup]=(pin,policy)
        if selected is None or pin.sequence<selected[0].sequence:
            selected=(pin,plans)
    if selected is None:
        raise RestoreBlocked("No independently covered backup for ordinary purge")
    if requires_paged_inventory and not active_inventory:
        raise RestoreBlocked("Paged inventory changed before ordinary purge")
    pin,plans=selected
    async with conn.transaction():
        for plan in plans:
            booking=UUID(plan["booking_id"])
            args=(org,booking,_canonical(plan).decode(),pin.sha256,pin.sequence,latest_inventory_sha256)
            if not await conn.fetchval("SELECT public.ordinary_bind_booking_retention_checkpoint($1,$2,$3::jsonb,$4,$5,$6)",*args):
                raise RestoreBlocked("Ordinary booking binding rejected current evidence")
            if not await conn.fetchval("SELECT public.ordinary_admin_purge_booking($1,$2,$3::jsonb,$4,$5,$6)",*args):
                raise RestoreBlocked("Ordinary booking purge rejected current evidence")
        assess_backup_coverage(authority,store,inventory_bytes,latest_inventory_sha256=latest_inventory_sha256,**trust)
        if authority.current()!=tip or any(
                (authority.checkpoint_pin(org,backup),authority.policy_pin(org,backup))!=pair
                for backup,pair in pins.items()):
            raise RestoreBlocked("Independent recovery evidence changed during purge")
        if authority.assert_paged_inventory(latest_inventory_sha256, required=requires_paged_inventory) != active_inventory:
            raise RestoreBlocked("Paged inventory changed during ordinary purge")
    return len(plans)
