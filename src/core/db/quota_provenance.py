"""Fresh database verification for the inactive legacy admission contract.

No application entry point calls this module yet. Historical billed_at values
are never promoted into an admission, and this module cannot create a charge.
"""

import json
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from src.core.billing.quota_admission import (
    InvalidQuotaAdmission,
    _issue_legacy_admission,
    assert_ai_generation_allowed_with_legacy_admission,
)
from src.core.db.scoping import system_scope

TRUST_MARKER_ID = UUID("6206d423-cea9-5480-bb9b-59d757503f14")
_PROTOCOL_FIELDS = frozenset({
    "protocol_version", "organization_id", "message_id", "trust_epoch", "claim_uuid",
    "period_kind", "period_anchor", "subscription_id", "charged_ordinal",
    "limit_at_charge", "billed_at", "processing_at", "lease_expires_at",
})


def legacy_admission_id(organization_id: UUID, message_id: UUID) -> UUID:
    if type(organization_id) is not UUID or type(message_id) is not UUID:
        raise InvalidQuotaAdmission("Legacy quota admission is not valid")
    return uuid5(NAMESPACE_URL, f"melpis:legacy-quota-admission:v1:{organization_id}:{message_id}")


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("Invalid protocol record")
    return value


@system_scope("Read only the fixed global quota trust marker and ACL metadata; no tenant rows")
async def require_quota_trust_epoch(conn) -> UUID:
    """Require current server-only ACLs and the exact post-hardening marker.

    The caller must hold a transaction while verifying the tenant admission.
    No fallback, marker creation, role change or permission repair is allowed.
    """
    writes = ["INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]
    if int(await conn.fetchval("SHOW server_version_num")) >= 170000:
        writes.append("MAINTAIN")
    safe = await conn.fetchval(
        """SELECT count(*) = 2 AND bool_and(c.relrowsecurity)
              AND NOT EXISTS (
                SELECT 1 FROM pg_class t JOIN pg_namespace s ON s.oid=t.relnamespace
                CROSS JOIN (VALUES ('anon'),('authenticated')) roles(name)
                CROSS JOIN unnest($1::text[]) p(privilege)
                WHERE s.nspname='public' AND t.relname IN ('messages','usage_events')
                  AND has_table_privilege(roles.name, t.oid, p.privilege))
              AND NOT EXISTS (
                SELECT 1 FROM pg_class t JOIN pg_namespace s ON s.oid=t.relnamespace
                CROSS JOIN (VALUES ('anon'),('authenticated')) roles(name)
                CROSS JOIN (VALUES ('INSERT'),('UPDATE'),('REFERENCES')) p(privilege)
                WHERE s.nspname='public' AND t.relname IN ('messages','usage_events')
                  AND has_any_column_privilege(roles.name, t.oid, p.privilege))
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname IN ('messages','usage_events')""",
        writes,
    )
    if safe is not True:
        raise InvalidQuotaAdmission("Quota provenance is unavailable")
    row = await conn.fetchrow(
        """SELECT organization_id, event_type, quantity, metadata
             FROM public.usage_events
            WHERE id='6206d423-cea9-5480-bb9b-59d757503f14'::uuid
              AND organization_id IS NULL
              AND event_type='system.quota_trust_epoch' AND quantity=0
            FOR SHARE""",
    )
    try:
        if (not row or row["organization_id"] is not None
                or row["event_type"] != "system.quota_trust_epoch" or row["quantity"] != 0):
            raise ValueError("Invalid control row")
        metadata = _object(row["metadata"])
        if (set(metadata) != {"protocol_version", "trust_epoch"}
                or type(metadata["protocol_version"]) is not int
                or metadata["protocol_version"] != 1
                or type(metadata["trust_epoch"]) is not str):
            raise ValueError("Invalid control metadata")
        epoch = UUID(metadata["trust_epoch"])
        if epoch.int == 0 or str(epoch) != metadata["trust_epoch"]:
            raise ValueError("Invalid epoch")
        return epoch
    except (ValueError, TypeError, KeyError):
        raise InvalidQuotaAdmission("Quota provenance is unavailable") from None


async def load_legacy_admission(
    conn, *, organization_id: UUID, message_id: UUID, claim_uuid: UUID,
):
    """Verify one scoped ledger entry and its current message/billing context.

    Returns internal Python proof, billing snapshot and validation context.
    This performs no writes and must never be exposed as an API response.
    Re-read before semantic/provider work; a past verification is not a lease
    renewal. A persisted response must be reused, not regenerated.
    """
    entry_id = legacy_admission_id(organization_id, message_id)
    if type(claim_uuid) is not UUID or claim_uuid.int == 0:
        raise InvalidQuotaAdmission("Legacy quota admission is not valid")
    async with conn.transaction():
        epoch = await require_quota_trust_epoch(conn)
        row = await conn.fetchrow(
            """SELECT u.event_type, u.quantity, u.metadata,
                      m.billed_at, m.processing_at, m.ai_reply_cache, m.sent_at,
                      m.replied_at, m.quota_exceeded_at, m.deleted_at, m.direction, m.status,
                      o.subscription_status, o.subscription_id,
                      o.trial_start, o.trial_end, o.current_period_start, o.current_period_end,
                      o.messages_limit, o.messages_used_this_period,
                      o.ai_accounting_blocked
                 FROM public.usage_events u
                 JOIN public.messages m ON m.id=$2 AND m.organization_id=$1
                 JOIN public.organizations o ON o.id=$1
                WHERE u.id=$3 AND u.organization_id=$1
                FOR SHARE OF u,m,o""", organization_id, message_id, entry_id,
        )
        try:
            if (not row or row["event_type"] != "legacy_quota_admission" or row["quantity"] != 0
                    or row["direction"] != "inbound" or row["status"] != "processing"
                    or any(row[key] is not None for key in
                           ("ai_reply_cache", "sent_at", "replied_at", "quota_exceeded_at", "deleted_at"))):
                raise ValueError("Admission is not usable for new generation")
            values = _object(row["metadata"])
            if (set(values) != _PROTOCOL_FIELDS
                    or type(values["protocol_version"]) is not int
                    or values["protocol_version"] != 1):
                raise ValueError("Unknown protocol")
            values = {k: v for k, v in values.items() if k != "protocol_version"}
            for key in ("organization_id", "message_id", "trust_epoch", "claim_uuid"):
                if type(values[key]) is not str:
                    raise ValueError("Invalid identifier")
                values[key] = UUID(values[key])
            for key in ("period_anchor", "billed_at", "processing_at", "lease_expires_at"):
                if type(values[key]) is not str:
                    raise ValueError("Invalid timestamp")
                values[key] = datetime.fromisoformat(values[key])
            proof = _issue_legacy_admission(**values)
            billing = {key: row[key] for key in (
                "subscription_status", "subscription_id", "trial_start", "trial_end",
                "current_period_start", "current_period_end", "messages_limit",
                "messages_used_this_period", "ai_accounting_blocked",
            )}
            trial = row["subscription_status"] == "trialing"
            context = {
                "organization_id": organization_id, "message_id": message_id,
                # A stale worker must not adopt a replacement worker's nonce.
                "trust_epoch": epoch, "claim_uuid": claim_uuid,
                "period_kind": "trial" if trial else "paid",
                "period_anchor": row["trial_start"] if trial else row["current_period_start"],
                "subscription_id": row["subscription_id"],
                "billed_at": row["billed_at"], "processing_at": row["processing_at"],
                "period_end": row["trial_end"] if trial else row["current_period_end"],
                # Read after row locks were acquired, not before a lock wait.
                "now": await conn.fetchval("SELECT clock_timestamp()"),
            }
            assert_ai_generation_allowed_with_legacy_admission(billing, proof, **context)
            return proof, billing, context
        except (ValueError, TypeError, KeyError, AttributeError):
            raise InvalidQuotaAdmission("Legacy quota admission is not valid") from None
