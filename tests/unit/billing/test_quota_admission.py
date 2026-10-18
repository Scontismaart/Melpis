"""Inactive legacy admission contract; no database provenance is simulated here."""

import copy
import json
import pickle
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from src.core.billing.quota_admission import (
    InvalidQuotaAdmission,
    LegacyQuotaAdmission,
    _issue_legacy_admission,
    assert_ai_generation_allowed_with_legacy_admission,
    assert_legacy_admission_valid,
)
from src.core.billing.suspension import AIGenerationBlocked


@pytest.fixture
def issued():
    now = datetime.now(timezone.utc)
    values = {
        "organization_id": uuid4(), "message_id": uuid4(),
        "trust_epoch": uuid4(), "claim_uuid": uuid4(),
        "period_kind": "trial", "period_anchor": now - timedelta(days=1),
        "subscription_id": None,
        "charged_ordinal": 150, "limit_at_charge": 150,
        "billed_at": now - timedelta(seconds=2),
        "processing_at": now - timedelta(seconds=1),
        "lease_expires_at": now + timedelta(seconds=29),
    }
    context = {key: value for key, value in values.items() if key not in {
        "charged_ordinal", "limit_at_charge", "lease_expires_at",
    }}
    context.update(period_end=now + timedelta(days=6), now=now)
    return values, _issue_legacy_admission(**values), context


def validate(proof, context, *, used=150, limit=150):
    assert_legacy_admission_valid(
        proof, **context, current_used=used, current_limit=limit,
    )


def billing(**changes):
    return {
        "subscription_status": "active", "messages_limit": 150,
        "messages_used_this_period": 150, **changes,
    }


def billing_for_period(context, *, status=None, **changes):
    status = status or ("trialing" if context["period_kind"] == "trial" else "active")
    period_fields = {
        "subscription_status": status,
        "subscription_id": context["subscription_id"],
        "messages_limit": 150,
        "messages_used_this_period": 150,
        "ai_accounting_blocked": False,
    }
    if status == "trialing":
        period_fields.update(trial_start=context["period_anchor"], trial_end=context["period_end"])
    elif status == "active":
        period_fields.update(
            current_period_start=context["period_anchor"], current_period_end=context["period_end"],
        )
    return {**period_fields, **changes}


def test_already_charged_150th_is_allowed_without_mutating_snapshot(issued):
    _, proof, context = issued
    snapshot = billing_for_period(context)
    original = dict(snapshot)
    assert_ai_generation_allowed_with_legacy_admission(snapshot, proof, **context)
    assert snapshot == original


@pytest.mark.parametrize("field,bad", [
    ("organization_id", "00000000-0000-0000-0000-000000000001"),
    ("message_id", None), ("trust_epoch", True), ("claim_uuid", 1.0),
    ("claim_uuid", UUID(int=0)),
    ("charged_ordinal", True), ("charged_ordinal", 1.0),
    ("charged_ordinal", 0), ("charged_ordinal", -1),
    ("charged_ordinal", 151), ("limit_at_charge", True),
    ("limit_at_charge", 150.0), ("limit_at_charge", 0),
    ("period_kind", "yearly"), ("period_kind", None),
])
def test_factory_rejects_invalid_fields(issued, field, bad):
    values, _, _ = issued
    with pytest.raises(InvalidQuotaAdmission):
        _issue_legacy_admission(**{**values, field: bad})


@pytest.mark.parametrize("field", ["period_anchor", "billed_at", "processing_at", "lease_expires_at"])
def test_factory_rejects_naive_datetimes(issued, field):
    values, _, _ = issued
    with pytest.raises(InvalidQuotaAdmission):
        _issue_legacy_admission(**{**values, field: values[field].replace(tzinfo=None)})


@pytest.mark.parametrize("seconds", [0, -1, 30.001, 31])
def test_factory_rejects_empty_or_excessive_lease(issued, seconds):
    values, _, _ = issued
    with pytest.raises(InvalidQuotaAdmission):
        _issue_legacy_admission(**{
            **values, "lease_expires_at": values["processing_at"] + timedelta(seconds=seconds),
        })


@pytest.mark.parametrize("field,relative_to", [
    ("billed_at", "period_anchor"), ("processing_at", "billed_at"),
])
def test_factory_rejects_incoherent_times(issued, field, relative_to):
    values, _, _ = issued
    with pytest.raises(InvalidQuotaAdmission):
        _issue_legacy_admission(**{
            **values, field: values[relative_to] - timedelta(microseconds=1),
        })


@pytest.mark.parametrize("field", ["organization_id", "message_id", "trust_epoch", "claim_uuid"])
def test_proof_is_bound_to_exact_db_scope_and_claim(issued, field):
    _, proof, context = issued
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, {**context, field: uuid4()})


@pytest.mark.parametrize("field", ["period_anchor", "billed_at", "processing_at"])
def test_proof_rejects_changed_database_timestamps(issued, field):
    _, proof, context = issued
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, {**context, field: context[field] + timedelta(microseconds=1)})


def test_trial_charge_does_not_become_paid_charge(issued):
    _, proof, context = issued
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, {**context, "period_kind": "paid"})


@pytest.mark.parametrize("subscription_id", [None, "", "sub_", " sub_123", "sub_123 ", "cus_123", True, 123])
def test_paid_period_requires_canonical_subscription_id(issued, subscription_id):
    values, _, _ = issued
    with pytest.raises(InvalidQuotaAdmission):
        _issue_legacy_admission(**{
            **values, "period_kind": "paid", "subscription_id": subscription_id,
        })


def test_paid_period_binds_subscription_even_with_identical_anchor(issued):
    values, _, context = issued
    proof = _issue_legacy_admission(**{
        **values, "period_kind": "paid", "subscription_id": "sub_123ABC",
    })
    context = {**context, "period_kind": "paid", "subscription_id": "sub_123ABC"}
    validate(proof, context)
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, {**context, "subscription_id": "sub_456DEF"})
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, {**context, "subscription_id": None})


def test_trial_without_subscription_cannot_adopt_checkout_subscription(issued):
    _, proof, context = issued
    validate(proof, context)
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, {**context, "subscription_id": "sub_123ABC"})


def test_trial_with_subscription_is_bound_to_it(issued):
    values, _, context = issued
    proof = _issue_legacy_admission(**{**values, "subscription_id": "sub_123ABC"})
    validate(proof, {**context, "subscription_id": "sub_123ABC"})
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, context)


@pytest.mark.parametrize("point", ["lease_end", "before_claim", "period_end"])
def test_expired_or_future_claim_never_authorizes(issued, point):
    _, proof, context = issued
    now = {
        "lease_end": proof.lease_expires_at,
        "before_claim": proof.processing_at - timedelta(microseconds=1),
        "period_end": context["period_end"],
    }[point]
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, {**context, "now": now})


def test_shortened_period_can_expire_proof_without_changing_identity(issued):
    _, proof, context = issued
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, {**context, "period_end": context["now"]})
    validate(proof, {**context, "period_end": context["period_end"] + timedelta(days=1)})


def test_lease_cannot_outlive_current_period_end(issued):
    _, proof, context = issued
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, {**context, "period_end": context["now"] + timedelta(seconds=1)})
    validate(proof, {**context, "period_end": proof.lease_expires_at})


@pytest.mark.parametrize("used,limit", [
    (151, 150), (150, 149), (149, 150), (True, 150), (150, True),
    (150.0, 150), (150, 150.0), (-1, 150), (150, 0), (None, 150),
])
def test_invalid_counter_or_reduced_limit_cannot_reuse_proof(issued, used, limit):
    _, proof, context = issued
    with pytest.raises(InvalidQuotaAdmission):
        validate(proof, context, used=used, limit=limit)


def test_upgrade_does_not_invalidate_existing_ordinal(issued):
    _, proof, context = issued
    validate(proof, context, used=151, limit=200)


@pytest.mark.parametrize("changes", [
    {"subscription_status": status} for status in (
        "past_due", "unpaid", "canceled", "paused", "incomplete", "unknown", None,
    )
] + [
    {"ai_accounting_blocked": True},
    {"subscription_status": "trialing", "trial_end": None},
    {"subscription_status": "trialing", "trial_end": datetime(2000, 1, 1, tzinfo=timezone.utc)},
])
def test_valid_proof_never_overrides_entitlement_or_accounting(issued, changes):
    _, proof, context = issued
    with pytest.raises(AIGenerationBlocked):
        assert_ai_generation_allowed_with_legacy_admission(billing(**changes), proof, **context)


def test_valid_trial_still_uses_original_deadline(issued):
    _, proof, context = issued
    snapshot = billing_for_period(context)
    assert_ai_generation_allowed_with_legacy_admission(snapshot, proof, **context)
    assert snapshot["trial_end"] == context["period_end"]


def test_valid_paid_period_binds_subscription_and_period(issued):
    values, _, context = issued
    subscription_id = "sub_123ABC"
    paid_values = {
        **values, "period_kind": "paid", "subscription_id": subscription_id,
    }
    paid_context = {
        **context, "period_kind": "paid", "subscription_id": subscription_id,
    }
    proof = _issue_legacy_admission(**paid_values)
    snapshot = billing_for_period(paid_context)
    assert_ai_generation_allowed_with_legacy_admission(snapshot, proof, **paid_context)


def test_trial_proof_cannot_authorize_active_period(issued):
    _, proof, context = issued
    active = billing_for_period(context, status="active", subscription_id="sub_123ABC")
    with pytest.raises(InvalidQuotaAdmission):
        assert_ai_generation_allowed_with_legacy_admission(active, proof, **context)


def test_paid_proof_cannot_authorize_trial_period(issued):
    values, _, context = issued
    paid_context = {**context, "period_kind": "paid", "subscription_id": "sub_123ABC"}
    proof = _issue_legacy_admission(**{
        **values, "period_kind": "paid", "subscription_id": "sub_123ABC",
    })
    trialing = billing_for_period(paid_context, status="trialing")
    with pytest.raises(InvalidQuotaAdmission):
        assert_ai_generation_allowed_with_legacy_admission(trialing, proof, **paid_context)


@pytest.mark.parametrize("field", ["trial_start", "trial_end"])
def test_trial_proof_rejects_changed_billing_period(issued, field):
    _, proof, context = issued
    snapshot = billing_for_period(context)
    snapshot[field] += timedelta(seconds=1)
    with pytest.raises(InvalidQuotaAdmission):
        assert_ai_generation_allowed_with_legacy_admission(snapshot, proof, **context)


def test_trial_proof_rejects_changed_subscription_binding(issued):
    _, proof, context = issued
    snapshot = billing_for_period(context, subscription_id="sub_changed123")
    with pytest.raises(InvalidQuotaAdmission):
        assert_ai_generation_allowed_with_legacy_admission(snapshot, proof, **context)


@pytest.mark.parametrize("field", ["trial_start", "trial_end"])
def test_missing_trial_period_field_fails_closed(issued, field):
    _, proof, context = issued
    snapshot = billing_for_period(context)
    snapshot.pop(field)
    with pytest.raises(InvalidQuotaAdmission):
        assert_ai_generation_allowed_with_legacy_admission(snapshot, proof, **context)


@pytest.mark.parametrize("snapshot", [None, {}, {"messages_used_this_period": 150}])
def test_missing_billing_never_uses_default_entitlement(issued, snapshot):
    _, proof, context = issued
    with pytest.raises(AIGenerationBlocked):
        assert_ai_generation_allowed_with_legacy_admission(snapshot, proof, **context)


def test_proof_is_required_even_when_current_quota_is_available(issued):
    _, _, context = issued
    with pytest.raises(InvalidQuotaAdmission):
        assert_ai_generation_allowed_with_legacy_admission(
            billing(messages_used_this_period=149), {}, **context,
        )


def test_proof_cannot_be_publicly_constructed_or_modified(issued):
    values, proof, _ = issued
    with pytest.raises(TypeError):
        LegacyQuotaAdmission(**values)
    with pytest.raises(FrozenInstanceError):
        proof.charged_ordinal = 1
    with pytest.raises(TypeError):
        replace(proof, charged_ordinal=1)


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps, json.dumps])
def test_serialization_and_copy_cannot_recreate_capability(issued, operation):
    _, proof, _ = issued
    with pytest.raises(TypeError):
        operation(proof)


def test_dict_json_and_object_with_fake_capability_are_rejected(issued):
    values, _, context = issued
    json_object = json.loads(json.dumps(values, default=str))
    forged = object.__new__(LegacyQuotaAdmission)
    for name, value in {**values, "_capability": object()}.items():
        object.__setattr__(forged, name, value)
    for candidate in (values, json_object, forged, object.__new__(LegacyQuotaAdmission)):
        with pytest.raises(InvalidQuotaAdmission):
            validate(candidate, context)


def test_repr_does_not_expose_identifiers(issued):
    _, proof, _ = issued
    assert str(proof.organization_id) not in repr(proof)
    assert str(proof.claim_uuid) not in repr(proof)
