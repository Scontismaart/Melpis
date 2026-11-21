"""Controller-only review of an exact persisted account erasure request.

Account identity can span organizations. This review consumes no grant,
changes no membership, and certifies no provider deletion or recovery state.
"""

from uuid import UUID

from src.core.db.scoping import system_scope
from src.core.gdpr.restore_checkpoint import RestoreBlocked


@system_scope("global account dependency review restricted to an exact persisted identity; only blocker codes returned")
async def review_account_erasure(conn, organization_id, operation_id, requester):
    """Report current dependencies in a consistent owner-only read snapshot."""
    if (not all(type(value) is UUID and value.int
                for value in (organization_id, operation_id, requester))
            or not conn.is_in_transaction()
            or not await conn.fetchval("""SELECT current_user=session_user
                AND current_setting('transaction_read_only')='on'
                AND current_setting('transaction_isolation')='repeatable read'
                AND EXISTS(SELECT FROM pg_catalog.pg_class
                    WHERE oid='public.gdpr_operations'::regclass AND relowner=current_user::regrole)
                AND EXISTS(SELECT FROM pg_catalog.pg_class
                    WHERE oid='public.user_profiles'::regclass AND relowner=current_user::regrole)""")):
        raise RestoreBlocked("Account review requires an exact read-only owner snapshot")
    operation = await conn.fetchval("""SELECT EXISTS(
        SELECT FROM public.gdpr_operations
        WHERE organization_id=$1 AND id=$2 AND requester=$3 AND kind='erase'
          AND scope='account' AND subject_id IS NULL AND status='blocked'
    )""", organization_id, operation_id, str(requester))
    if not operation:
        raise RestoreBlocked("Account review scope unavailable")

    state = await conn.fetchrow("""SELECT
        EXISTS(SELECT FROM auth.users WHERE id=$1) AS auth_present,
        EXISTS(SELECT FROM public.user_profiles WHERE auth_user_id=$1) AS profile_present,
        EXISTS(SELECT FROM public.auth_access_lifecycle WHERE auth_user_id=$1) AS lifecycle_present,
        EXISTS(SELECT FROM public.organization_memberships m
            JOIN public.user_profiles p ON p.id=m.user_id
            WHERE p.auth_user_id=$1 AND m.organization_id=$2 AND m.joined_at IS NOT NULL) AS local_membership,
        EXISTS(SELECT FROM public.organization_memberships m
            JOIN public.user_profiles p ON p.id=m.user_id
            WHERE p.auth_user_id=$1 AND m.organization_id<>$2) AS shared_membership,
        EXISTS(SELECT FROM public.organization_memberships m
            JOIN public.user_profiles p ON p.id=m.user_id
            WHERE p.auth_user_id=$1 AND m.ruolo='owner') AS ownership,
        EXISTS(SELECT FROM public.conversations c
            JOIN public.user_profiles p ON p.id=c.assigned_to
            WHERE p.auth_user_id=$1) AS assignments,
        EXISTS(SELECT FROM public.team_invitations i
            JOIN public.user_profiles p ON p.id=i.invited_by
            WHERE p.auth_user_id=$1) AS invitations,
        EXISTS(SELECT FROM public.audit_log l
            JOIN public.user_profiles p ON p.id=l.user_id
            WHERE p.auth_user_id=$1) AS audit_references""", requester, organization_id)

    blockers = [
        "independent_account_controller_disposition_required",
        "auth_provider_identity_and_receipt_required",
        "account_backup_replay_and_session_revocation_required",
    ]
    for field, blocker, expected in (
        ("auth_present", "auth_identity_missing", True),
        ("profile_present", "account_profile_missing", True),
        ("lifecycle_present", "account_access_lifecycle_missing", True),
        ("local_membership", "request_tenant_membership_review_required", True),
        ("shared_membership", "shared_tenant_memberships_review_required", False),
        ("ownership", "tenant_owner_transfer_required", False),
        ("assignments", "conversation_assignment_disposition_required", False),
        ("invitations", "invitation_disposition_required", False),
        ("audit_references", "audit_retention_disposition_required", False),
    ):
        if state[field] is not expected:
            blockers.append(blocker)
    return {"state": "controller_review_required", "blockers": blockers, "deletion_authorized": False}
