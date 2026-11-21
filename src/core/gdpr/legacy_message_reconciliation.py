"""Private controller review of exact historical message attribution."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import re
from uuid import UUID

from src.core.gdpr.journal_publication import _canonical
from src.core.gdpr.restore_checkpoint import RestoreBlocked
from src.core.gdpr.restore_journal import _hex, _unique_object, _uuid


_DOMAIN = b"melpis.gdpr.legacy-message-lineage.v1\0"
_MAX_ATTESTATION_BYTES = 4096


def _lineage(payload):
    fields = {"version", "case_id", "organization_id", "message_id", "conversation_id", "original_subject_id",
              "received_at", "authority_reference", "evidence_sha256"}
    if (type(payload) is not dict or payload.keys() != fields or payload["version"] != "gdpr-legacy-message-lineage-v1"
            or type(payload["authority_reference"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", payload["authority_reference"])
            or not _hex(payload["evidence_sha256"], 64) or type(payload["received_at"]) is not str):
        raise ValueError
    identities = tuple(_uuid(payload[field]) for field in (
        "case_id", "organization_id", "message_id", "conversation_id", "original_subject_id"))
    received = datetime.fromisoformat(payload["received_at"])
    if received.utcoffset() != timedelta(0) or received.isoformat() != payload["received_at"]:
        raise ValueError
    return (*identities, received)


def attest_message_lineage(payload, *, verification_key):
    """Seal the independent controller's exact conclusion and evidence reference."""
    try:
        if type(verification_key) is not bytes or len(verification_key) < 32:
            raise ValueError
        _lineage(payload)
        raw = _canonical(payload)
        result = _canonical(dict(payload=payload, signature=hmac.digest(verification_key, _DOMAIN + raw, "sha256").hex()))
        if len(result) > _MAX_ATTESTATION_BYTES:
            raise ValueError
        return result
    except (TypeError, ValueError, OverflowError, KeyError, RecursionError):
        raise RestoreBlocked("Exact independently reviewed message lineage required") from None


def _read_attestation(raw, digest, key):
    try:
        if (type(raw) is not bytes or not 1 <= len(raw) <= _MAX_ATTESTATION_BYTES
                or not _hex(digest, 64) or not hmac.compare_digest(sha256(raw).hexdigest(), digest)):
            raise ValueError
        envelope = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(envelope) is not dict or envelope.keys() != {"payload", "signature"}
                or not hmac.compare_digest(attest_message_lineage(envelope["payload"], verification_key=key), raw)):
            raise ValueError
        return envelope["payload"], _lineage(envelope["payload"])
    except (TypeError, ValueError, OverflowError, KeyError, RecursionError):
        raise RestoreBlocked("Authenticated independent historical lineage required") from None


async def reconcile_legacy_message(conn, org, message, *, audit_at=None, attestation=None,
                                   independent_sha256=None, verification_key=None):
    """Classify one scoped record without backfill, purge or content disclosure."""
    if (type(org) is not UUID or not org.int or type(message) is not UUID or not message.int
            or not conn.is_in_transaction() or not await conn.fetchval("""SELECT current_user=session_user
                AND current_setting('transaction_read_only')='on'
                AND current_setting('transaction_isolation')='read committed'
                AND EXISTS(SELECT FROM pg_class WHERE oid='public.messages'::regclass AND relowner=current_user::regrole)""")):
        raise RestoreBlocked("Historical message review requires an exact scope and read-only owner transaction")
    if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended($1::text,606))", str(org)):
        raise RestoreBlocked("Historical message scope is busy")
    row = await conn.fetchrow("""SELECT m.conversation_id,m.c24_original_subject_id,m.c24_received_at,
            m.content_purged_at,c.contact_id,clock_timestamp() AS observed_at
        FROM public.messages m JOIN public.conversations c ON c.id=m.conversation_id AND c.organization_id=m.organization_id
        WHERE m.organization_id=$1 AND c.organization_id=$1 AND m.id=$2""", org, message)
    if row is None:
        raise RestoreBlocked("Historical message scope is unavailable")
    audit_at = row["observed_at"] if audit_at is None else audit_at
    if (type(audit_at) is not datetime or audit_at.utcoffset() != timedelta(0) or audit_at > row["observed_at"]):
        raise RestoreBlocked("Historical message review time is invalid")
    # Current conversation ownership is a corroborating check. It is never
    # treated as proof of original membership or original receipt time.
    original, received = row["c24_original_subject_id"], row["c24_received_at"]
    state = "verified_stored_lineage"
    missing = tuple(field for field, value in (("original_subject_id", original), ("received_at", received)) if value is None)
    common = dict(organization_id=str(org), message_id=str(message), deletion_authorized=False)
    if attestation is None:
        if independent_sha256 is not None or verification_key is not None:
            raise RestoreBlocked("Authenticated independent historical lineage required")
        if missing:
            return dict(common, state="contemporaneous_evidence_required", missing=missing, due_under_current_policy=None)
    else:
        payload, (case, attributed_org, attributed_message, conversation, subject, recorded) = _read_attestation(
            attestation, independent_sha256, verification_key)
        if ((attributed_org, attributed_message, conversation, subject) != (org, message, row["conversation_id"], row["contact_id"])
                or (original is not None and original != subject) or (received is not None and received != recorded)
                or recorded > audit_at or row["content_purged_at"] is not None):
            raise RestoreBlocked("Independent lineage disagrees with the stored source")
        original, received = subject, recorded
        state = "independently_attested_lineage"
        common.update(case_id=str(case), evidence_sha256=payload["evidence_sha256"])
    if original != row["contact_id"] or received > audit_at:
        raise RestoreBlocked("Stored lineage disagrees with the source scope")
    policy = await conn.fetchrow("""SELECT version,retention_days,erasure_allowed,legal_hold,valid_until
        FROM public.gdpr_policies WHERE organization_id=$1 AND category='messages_content'
          AND approved_at<=$2 ORDER BY approved_at DESC,version DESC LIMIT 1""", org, audit_at)
    held = await conn.fetchval("""SELECT EXISTS(SELECT FROM public.gdpr_retention_holds h
        WHERE h.organization_id=$1 AND h.category='messages_content'
          AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=$2)
            OR (h.scope='conversation' AND h.scope_id=$3) OR (h.scope='record' AND h.scope_id=$4))
          AND NOT EXISTS(SELECT FROM public.gdpr_retention_hold_events e
            WHERE e.organization_id=$1 AND e.hold_id=h.hold_id AND e.kind='released'))""",
        org, original, row["conversation_id"], message)
    applicable = (policy and policy["valid_until"] > audit_at and policy["erasure_allowed"] and not policy["legal_hold"]
                  and type(policy["retention_days"]) is int and 1 <= policy["retention_days"] <= 90 and not held)
    return dict(common, state=state, missing=(), received_at=received.astimezone(timezone.utc).isoformat(),
                policy_version=policy["version"] if policy else None, held=held,
                due_under_current_policy=received <= audit_at - timedelta(days=policy["retention_days"]) if applicable else None)


async def inventory_quarantined_messages(conn, org, *, case_id, review_by, authority_reference,
                                         after=None, limit=100):
    """Return a bounded tenant-specific review queue without reading message content.

    The owner retains this in the restricted case channel and reconciles each
    exact record against independently held evidence. Eligibility remains
    blocked until the necessary lineage has been established.
    """
    if (type(org) is not UUID or not org.int
            or type(case_id) is not UUID or not case_id.int
            or type(review_by) is not datetime or review_by.utcoffset() != timedelta(0)
            or type(authority_reference) is not str
            or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", authority_reference)
            or (after is not None and (type(after) is not UUID or not after.int))
            or type(limit) is not int or not 1 <= limit <= 1000
            or not conn.is_in_transaction() or not await conn.fetchval("""
                SELECT current_user=session_user
                  AND current_setting('transaction_read_only')='on'
                  AND current_setting('transaction_isolation')='read committed'
                  AND EXISTS(SELECT FROM pg_class WHERE oid='public.messages'::regclass
                             AND relowner=current_user::regrole)
            """)):
        raise RestoreBlocked("Quarantine inventory requires exact scope and a read-only owner transaction")
    if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended($1::text,606))", str(org)):
        raise RestoreBlocked("Historical message scope is busy")
    observed_at = await conn.fetchval("SELECT clock_timestamp()")
    rows = await conn.fetch("""
        SELECT m.id,m.c24_original_subject_id IS NULL AS subject_missing,
               m.c24_received_at IS NULL AS receipt_missing,
               c.id IS NULL AS conversation_missing,
               ct.id IS NULL AS contact_missing,
               (m.c24_original_subject_id IS NOT NULL AND ct.id IS NOT NULL
                AND m.c24_original_subject_id IS DISTINCT FROM ct.id) AS subject_conflict,
               m.c24_received_at>$4 AS future_receipt
        FROM public.messages m
        LEFT JOIN public.conversations c ON c.id=m.conversation_id AND c.organization_id=$1
        LEFT JOIN public.contacts ct ON ct.id=c.contact_id AND ct.organization_id=$1
        WHERE m.organization_id=$1 AND ($2::uuid IS NULL OR m.id>$2::uuid)
          AND (m.c24_original_subject_id IS NULL OR m.c24_received_at IS NULL
               OR ct.id IS NULL OR m.c24_original_subject_id IS DISTINCT FROM ct.id
               OR m.c24_received_at>$4)
        ORDER BY m.id LIMIT $3
    """, org, after, limit+1, observed_at)
    records = [
        {"message_id": str(row["id"]), "reasons": [
            name for name in ("subject_missing", "receipt_missing", "conversation_missing",
                              "contact_missing", "subject_conflict", "future_receipt")
            if row[name]
        ]}
        for row in rows[:limit]
    ]
    report = {"organization_id": str(org), "case_id": str(case_id),
              "authority_reference": authority_reference,
              "state": "controller_reconciliation_required",
              "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
              "review_by": review_by.isoformat(), "review_overdue": review_by <= observed_at,
              "deletion_authorized": False, "records": records,
              "next": records[-1]["message_id"] if len(rows) > limit else None}
    return dict(report, report_sha256=sha256(_canonical(report)).hexdigest())
