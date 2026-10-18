"""Internal proof of a legacy inbound charge; not an A04 reservation protocol.

Only the repository may issue a proof, after verifying a server-owned ledger
and the current claim in the database. The Python capability prevents JSON
reconstruction; it does not establish database provenance by itself. There is
no active caller until the ACL/epoch prerequisite and repository are integrated.
"""

import re
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import NoReturn
from uuid import UUID

from src.core.billing.suspension import (
    AIGenerationBlocked,
    assert_ai_generation_allowed,
)

_ISSUER_CAPABILITY = object()
_MAX_LEASE = timedelta(seconds=30)


class InvalidQuotaAdmission(AIGenerationBlocked):
    """A fixed, non-sensitive admission validation error."""


def _deny() -> NoReturn:
    raise InvalidQuotaAdmission("Legacy quota admission is not valid")


def _uuid(value: object) -> bool:
    return type(value) is UUID and value.int != 0


def _time(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _positive_integer(value: object) -> bool:
    return type(value) is int and value > 0


@dataclass(frozen=True, slots=True, init=False, repr=False)
class LegacyQuotaAdmission:
    organization_id: UUID
    message_id: UUID
    trust_epoch: UUID
    claim_uuid: UUID
    period_kind: str
    period_anchor: datetime
    subscription_id: str | None
    charged_ordinal: int
    limit_at_charge: int
    billed_at: datetime
    processing_at: datetime
    lease_expires_at: datetime
    _capability: object

    def __init__(self, *args, **kwargs):
        raise TypeError("Legacy admissions are issued by the repository")

    def __repr__(self) -> str:
        return "LegacyQuotaAdmission(<server-issued>)"

    def __copy__(self):
        raise TypeError("Legacy admissions cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("Legacy admissions cannot be copied")

    def __reduce_ex__(self, protocol):
        raise TypeError("Legacy admissions cannot be serialized")


def _valid_fields(values: dict) -> bool:
    if not all(_uuid(values[name]) for name in (
        "organization_id", "message_id", "trust_epoch", "claim_uuid",
    )):
        return False
    if type(values["period_kind"]) is not str or values["period_kind"] not in {"trial", "paid"}:
        return False
    subscription_id = values["subscription_id"]
    if subscription_id is None:
        if values["period_kind"] == "paid":
            return False
    elif type(subscription_id) is not str or re.fullmatch(r"sub_[A-Za-z0-9]+", subscription_id) is None:
        return False
    if not all(_time(values[name]) for name in (
        "period_anchor", "billed_at", "processing_at", "lease_expires_at",
    )):
        return False
    if not all(_positive_integer(values[name]) for name in ("charged_ordinal", "limit_at_charge")):
        return False
    return (
        values["charged_ordinal"] <= values["limit_at_charge"]
        and values["period_anchor"] <= values["billed_at"] <= values["processing_at"]
        and timedelta(0) < values["lease_expires_at"] - values["processing_at"] <= _MAX_LEASE
    )


def _issue_legacy_admission(
    *, organization_id: UUID, message_id: UUID, trust_epoch: UUID, claim_uuid: UUID,
    period_kind: str, period_anchor: datetime, subscription_id: str | None, charged_ordinal: int,
    limit_at_charge: int, billed_at: datetime, processing_at: datetime,
    lease_expires_at: datetime,
) -> LegacyQuotaAdmission:
    """Repository-only factory AFTER fresh DB provenance/claim verification.

    Do not call this with API data or infer period/ordinal from billed_at. An
    existing historical charge without a trusted ledger cannot produce proof.
    """
    values = locals()
    if not _valid_fields(values):
        _deny()
    proof = object.__new__(LegacyQuotaAdmission)
    for name, value in values.items():
        object.__setattr__(proof, name, value)
    object.__setattr__(proof, "_capability", _ISSUER_CAPABILITY)
    return proof


def assert_legacy_admission_valid(
    admission: LegacyQuotaAdmission, *, organization_id: UUID, message_id: UUID,
    trust_epoch: UUID, claim_uuid: UUID, period_kind: str, period_anchor: datetime,
    subscription_id: str | None,
    billed_at: datetime, processing_at: datetime, period_end: datetime,
    current_used: int, current_limit: int, now: datetime,
) -> None:
    """Check proof against a freshly verified, server-owned database context.

    This validates quota compatibility only, never identity, consent or billing
    entitlement. Callers still require the normal generation authorization.
    The caller must re-read ledger/claim; this function performs no DB query.
    """
    if type(admission) is not LegacyQuotaAdmission:
        _deny()
    try:
        values = {field.name: getattr(admission, field.name) for field in fields(admission)}
    except AttributeError:
        _deny()
    if values["_capability"] is not _ISSUER_CAPABILITY or not _valid_fields(values):
        _deny()
    expected = {
        "organization_id": organization_id, "message_id": message_id,
        "trust_epoch": trust_epoch, "claim_uuid": claim_uuid,
        "period_kind": period_kind, "period_anchor": period_anchor,
        "subscription_id": subscription_id,
        "billed_at": billed_at, "processing_at": processing_at,
    }
    if not all(_uuid(expected[name]) for name in (
        "organization_id", "message_id", "trust_epoch", "claim_uuid",
    )):
        _deny()
    if not all(_time(value) for value in (period_anchor, billed_at, processing_at, period_end, now)):
        _deny()
    if any(values[name] != value for name, value in expected.items()):
        _deny()
    if (type(current_used) is not int or current_used < 0
            or not _positive_integer(current_limit)):
        _deny()
    if not (
        admission.processing_at <= now < admission.lease_expires_at
        and admission.period_anchor <= now < period_end
        and admission.lease_expires_at <= period_end
        and admission.charged_ordinal <= current_used <= current_limit
        and admission.charged_ordinal <= current_limit
    ):
        _deny()


def assert_ai_generation_allowed_with_legacy_admission(
    billing: dict | None, admission: LegacyQuotaAdmission, **verified_context,
) -> None:
    """Keep normal billing policy; recognize one ALREADY charged legacy input.

    A local copy subtracts one only for the equality boundary after proof is
    fully checked. This does not refund, reserve, increment or mutate anything.
    The original helper runs first and may deny. Only after proof validation
    can the equality boundary be retried with a local compatibility snapshot;
    the unchanged helper rechecks every non-quota restriction on that snapshot.
    Catching its initial denial does not authorize generation. No caller is
    activated here.
    """
    blocked = None
    try:
        assert_ai_generation_allowed(billing)
    except AIGenerationBlocked as exc:
        blocked = exc
    if not isinstance(billing, dict):
        if blocked is not None:
            raise blocked
        _deny()
    status = billing.get("subscription_status")
    if status not in {"active", "trialing"}:
        if blocked is not None:
            raise blocked
        _deny()
    trial = status == "trialing"
    anchor = billing.get("trial_start" if trial else "current_period_start")
    end = billing.get("trial_end" if trial else "current_period_end")
    if (not _time(anchor) or not _time(end)
            or verified_context.get("period_kind") != ("trial" if trial else "paid")
            or anchor != verified_context.get("period_anchor")
            or end != verified_context.get("period_end")
            or billing.get("subscription_id") != verified_context.get("subscription_id")):
        _deny()
    used = billing.get("messages_used_this_period")
    limit = billing.get("messages_limit")
    assert_legacy_admission_valid(
        admission, current_used=used, current_limit=limit, **verified_context,
    )
    if blocked is None:
        return
    if used != limit:
        raise blocked
    compatibility_snapshot = dict(billing)
    compatibility_snapshot["messages_used_this_period"] = used - 1
    assert_ai_generation_allowed(compatibility_snapshot)
