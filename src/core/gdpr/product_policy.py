"""Founder-approved product limits, never authorization to run destructive jobs.

Tenant instructions, effective immutable DB records, holds and environment GO
remain separate. Unsupported schedules cannot be guessed from these limits.
"""
from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta


POLICY_VERSION = "melpis-privacy-v1.0"
APPROVAL_STATUS = "founder_approved_product_standard"


@dataclass(frozen=True, slots=True)
class ProductCategory:
    category: str
    purpose: str
    anchor: str
    maximum: int | None
    unit: str
    access: str
    export: str
    erasure: str
    ordinary_availability_days: int | None = None


PRODUCT_CATEGORIES = (
    ProductCategory("messages_content", "requested_conversation_and_booking", "message_created_at",
                    90, "days", "authorized_tenant_staff", "verified_subject_curated",
                    "primary_content_and_derived_copies", 60),
    ProductCategory("booking_conversation_content", "current_booking_request", "last_pertinent_activity",
                    30, "days", "authorized_tenant_staff", "original_subject_curated",
                    "state_and_copies_with_replay_fencing"),
    ProductCategory("technical_receipts", "idempotency_and_error_prevention", "request_concluded_at",
                    90, "days", "scoped_backend_and_authorized_support", "minimized_verified_scope",
                    "minimize_and_remove_personal_evidence_without_counter_reset"),
    ProductCategory("completed_bookings_and_stays", "operational_booking_archive", "booking_or_stay_concluded_at",
                    12, "calendar_months", "authorized_tenant_staff", "original_subject_curated",
                    "no_raw_conversation_text_separate_legal_records"),
    ProductCategory("backups", "isolated_disaster_recovery", "backup_created_at",
                    30, "days", "restricted_administration_encrypted", "not_in_ordinary_export",
                    "rotation_and_independent_post_backup_erasure_reconciliation"),
    ProductCategory("financial_records", "specific_documented_legal_obligation", "obligation_specific",
                    None, "documented_obligation", "restricted_financial_roles", "verified_scope",
                    "category_specific_obligation_no_raw_message_or_counter_reset"),
    ProductCategory("security_evidence", "documented_security_and_support", "documented_purpose_specific",
                    None, "documented_obligation", "authorized_traced_support", "minimized_verified_scope",
                    "bounded_necessity_review_no_indefinite_personal_content_audit"),
)


def retention_days_allowed(category: str, days: object) -> bool:
    """Validate only currently mapped worker categories; never silently clamp.

In particular simulator rows need a verified classification before automatic
retention; finance, backups and calendar-month archives are not day schedules.
"""
    if type(days) is not int:
        return False
    rule = next((r for r in PRODUCT_CATEGORIES if r.category == category), None)
    return bool(rule and category in {"messages_content", "booking_conversation_content"}
                and rule.maximum is not None and 1 <= days <= rule.maximum)


def product_deadline(category: str, anchor: datetime) -> datetime:
    """Calendar contract for planning/tests, not a cleanup authorization."""
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("timezone_aware_anchor_required")
    rule = next((r for r in PRODUCT_CATEGORIES if r.category == category), None)
    if rule is None or rule.maximum is None:
        raise ValueError("documented_category_schedule_required")
    if rule.unit == "days":
        return anchor + timedelta(days=rule.maximum)
    month = anchor.year * 12 + anchor.month - 1 + rule.maximum
    year, month = divmod(month, 12)
    month += 1
    return anchor.replace(year=year, month=month,
                          day=min(anchor.day, monthrange(year, month)[1]))


def product_policy_payload(*, execution_flag: bool) -> dict:
    """Public product metadata only; no tenant policy or enforcement inference."""
    return {
        "policy_version": POLICY_VERSION,
        "product_approval": APPROVAL_STATUS,
        "enforcement_status": "not_certified_operational_go_required",
        "automatic_erasure_enabled": execution_flag,
        "automatic_erasure_enabled_scope": "legacy_environment_flag_only_not_authorization",
        "approval_gate": "effective tenant instructions, policy registry, environment verification and operational GO",
        "retention_days": None,
        "purge_after_days": None,
        "legacy_days_scope": "effective_tenant_schedule_not_certified",
        "product_retention_days": 60,
        "product_purge_after_days": 90,
        "simulator_response_days": None,
        "simulator_history_idle_hours": 24,
        "tenant_overrides": "versioned_no_arbitrary_extension",
        "legal_hold": "documented_reason_minimal_scope_authority_expiry_or_review",
        "health_sector_activation": "blocked_until_specific_requirements_verified",
        "categories": [asdict(rule) for rule in PRODUCT_CATEGORIES],
    }
