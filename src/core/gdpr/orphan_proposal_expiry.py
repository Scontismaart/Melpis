"""Owner maintenance and quarantined replay of independently covered orphan plans."""

from datetime import datetime, timezone
import json
import os
from uuid import UUID

from src.core.gdpr.backup_coverage import _backups, assess_backup_coverage
from src.core.gdpr.checkpoint_pages import read_any_checkpoint
from src.core.gdpr.journal_publication import _canonical
from src.core.gdpr.orphan_proposal import _plan_orphan_proposal
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_policy_state import verify_policy_state


async def verify_orphan_proposal_expirations(conn, org, plans):
    available = await conn.fetchval("SELECT to_regclass('public.c24_orphan_proposal_expirations') IS NOT NULL")
    if not available:
        if plans:
            raise RestoreBlocked("Orphan proposal expiry schema is unavailable")
        return
    if len(plans) != await conn.fetchval("SELECT count(*) FROM c24_orphan_proposal_expirations WHERE organization_id=$1", org):
        raise RestoreBlocked("Orphan proposal evidence does not match the restored database")
    for plan in plans:
        if not await conn.fetchval("SELECT public.c24_verify_orphan_proposal_expiry($1,$2,$3::jsonb)",
            org, UUID(plan["source_message_id"]), _canonical(plan).decode()):
            raise RestoreBlocked("Orphan proposal expiry is incomplete")


async def expire_inventory_covered_orphan_proposals(conn, authority, store, org, inventory_bytes, *,
                                                    latest_inventory_sha256, restored_backup_id=None,
                                                    quarantine_database=None, **trust):
    """Expire verified C2.4 plans and preserve quota, receipt and source content."""
    if os.getenv("GDPR_RETENTION_EXECUTION_ENABLED") != "true":
        raise RestoreBlocked("Orphan proposal retention is not operationally enabled")
    if (type(org) is not UUID or not org.int or not conn.is_in_transaction()
            or await conn.fetchval("SHOW transaction_isolation") != "read committed"
            or await conn.fetchval("SHOW transaction_read_only") != "off"
            or not await conn.fetchval("""SELECT current_user=session_user AND EXISTS(SELECT FROM pg_class
                 WHERE oid=to_regclass('public.c24_orphan_proposal_expirations') AND relowner=current_user::regrole)""")):
        raise RestoreBlocked("Orphan proposal replay requires a writable lifecycle owner transaction")
    await conn.fetchrow("SELECT id FROM organizations WHERE id=$1 FOR NO KEY UPDATE", org)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))", str(org))
    audit_at = trust.get("audit_at") or datetime.now(timezone.utc)
    trust = dict(trust, audit_at=audit_at)
    authority.assert_paged_inventory(latest_inventory_sha256)
    tip = authority.current()
    assess_backup_coverage(authority, store, inventory_bytes, latest_inventory_sha256=latest_inventory_sha256, **trust)
    backups = list(_backups(inventory_bytes, latest_inventory_sha256, authority.installation_id, audit_at))
    backup_completed = None
    if restored_backup_id is not None or quarantine_database is not None:
        from src.core.gdpr.restore_coordinator import _assert_quarantine

        await _assert_quarantine(conn, quarantine_database)
        if not any(backup == restored_backup_id and org in organizations for backup, organizations, _, _ in backups):
            raise RestoreBlocked("Restored orphan proposal evidence does not match the inventory")
        matching = next(row for row in json.loads(inventory_bytes)["backups"] if row["backup_id"] == restored_backup_id)
        backup_completed = datetime.fromisoformat(matching["completed_at"])
    observations = {}
    selected = None
    expected = None
    for backup, organizations, _, _ in backups:
        if org not in organizations:
            continue
        pin = authority.checkpoint_pin(org, backup)
        policy_pin = authority.policy_pin(org, backup)
        observations[backup] = pin, policy_pin
        payload = read_any_checkpoint(store, authority.installation_id, org, backup, pin,
            verification_key=trust["checkpoint_verification_key"], encryption_key=trust["checkpoint_encryption_key"])
        if payload["version"] != "c24-restore-v5":
            raise RestoreBlocked("Every recoverable backup requires paged orphan proposal evidence")
        plans = payload.get("orphan_proposal_prunes", [])
        canonical = {_canonical(plan) for plan in plans}
        if expected is not None and canonical != expected:
            raise RestoreBlocked("Orphan proposal evidence differs across recoverable backups")
        expected = canonical
        policy_keys = dict(verification_key=trust["policy_verification_key"], encryption_key=trust["policy_encryption_key"])
        sealed, _ = authority.read_policy(store, policy_pin, **policy_keys)
        await verify_policy_state(conn, org, sealed, installation_id=authority.installation_id,
                                  backup_id=backup, independent_pin=policy_pin, **policy_keys)
        if selected is None or pin.sequence < selected[0].sequence:
            selected = pin, plans
    if selected is None:
        raise RestoreBlocked("No independently covered backup for orphan proposal expiry")
    pin, plans = selected
    async with conn.transaction():
        for plan in plans:
            source = UUID(plan["source_message_id"])
            if await conn.fetchval("SELECT public.c24_verify_orphan_proposal_expiry($1,$2,$3::jsonb)",
                                   org, source, _canonical(plan).decode()):
                continue
            observed = await _plan_orphan_proposal(conn, org, source, read_only=False)
            current_policy, absent_since = None, None
            if observed != plan:
                if backup_completed is not None and observed is not None and dict(observed, policy_version=plan["policy_version"]) == plan:
                    current_policy = observed["policy_version"]
                elif backup_completed is not None and observed is None and datetime.fromisoformat(plan["received_at"]) > backup_completed:
                    absent_since = backup_completed
                    current_policy = await conn.fetchval("""SELECT version FROM gdpr_policies WHERE organization_id=$1
                        AND category='booking_conversation_content' AND approved_at<=clock_timestamp()
                        ORDER BY approved_at DESC,version DESC LIMIT 1""", org)
                else:
                    raise RestoreBlocked("Current orphan proposal proof disagrees with the signed plan")
            if not await conn.fetchval("SELECT public.c24_apply_orphan_proposal_expiry($1,$2,$3::jsonb,$4,$5,$6,$7,$8)",
                    org, source, _canonical(plan).decode(), pin.sha256, pin.sequence, latest_inventory_sha256,
                    current_policy, absent_since):
                raise RestoreBlocked("Orphan proposal mutation rejected current evidence")
        await verify_orphan_proposal_expirations(conn, org, plans)
        authority.assert_paged_inventory(latest_inventory_sha256)
        if authority.current() != tip or any(
                (authority.checkpoint_pin(org, backup), authority.policy_pin(org, backup)) != pair
                for backup, pair in observations.items()):
            raise RestoreBlocked("Independent recovery evidence changed during orphan proposal expiry")
    return len(plans)
