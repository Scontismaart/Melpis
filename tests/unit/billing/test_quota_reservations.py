"""Pure contract tests for inactive quota reservation capabilities and guardrails."""

import hashlib
import pickle
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.core.billing.quota_reservations import (
    _CAPABILITY,
    FinalQuotaResponse,
    QuotaConflict,
    QuotaOperation,
    QuotaReservations,
    QuotaTicket,
    _period,
    validate_final_response,
)
from src.core.billing.suspension import AIGenerationBlocked
from src.core.guardrails.validator import (
    FALLBACK_STAFF_TEXT,
    applica_guardrail,
    valida_risposta,
)
from src.models.schemas import RispostaOutput


def operation(*, channel="simulator", org_id=None, source_id=None, actor_id=None, text="same payload"):
    if channel == "inbound":
        actor_id = None
    elif actor_id is None:
        actor_id = uuid4()
    return QuotaOperation(
        organization_id=org_id or uuid4(),
        source_id=source_id or uuid4(),
        channel=channel,
        payload_hash=hashlib.sha256(text.encode()).hexdigest(),
        auth_user_id=actor_id,
    )


def safe_response(text="Siamo aperti oggi.", **extra):
    return RispostaOutput(
        risposta=text, richiede_umano=False, motivo="", categoria="generico", **extra,
    )


def issued_ticket(op):
    return QuotaTicket(
        op.id, uuid4(), uuid4(), uuid4(), datetime.now(timezone.utc) + timedelta(seconds=20), _CAPABILITY,
    )


def test_final_response_uses_real_guardrails_and_applies_valid_trimming():
    op = operation()
    ticket = issued_ticket(op)
    final = validate_final_response(op, ticket, safe_response("**Siamo aperti oggi.**"))

    assert type(final) is FinalQuotaResponse
    assert final.operation_id == op.id
    assert final.payload_hash == op.payload_hash
    assert final.claim_id == ticket.claim_id
    assert final.epoch == ticket.epoch
    assert final.period_id == ticket.period_id
    assert "**" not in final.serialized
    assert "Siamo aperti oggi." in final.serialized
    assert "FinalQuotaResponse" in repr(final)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(final)


@pytest.mark.parametrize("text,context", [
    ("", None),
    ("Il servizio costa €99.", []),
])
def test_empty_or_ungrounded_price_response_is_blocked(text, context):
    op = operation()
    with pytest.raises(AIGenerationBlocked):
        validate_final_response(op, issued_ticket(op), safe_response(text), context_chunks=context)


def test_price_grounded_in_retrieved_context_passes_guardrail():
    op = operation()
    ticket = issued_ticket(op)
    final = validate_final_response(
        op, ticket, safe_response("Il servizio costa €20."),
        context_chunks=[{"content": "Prezzo aggiornato: €20."}],
    )
    assert "€20" in final.serialized


def test_json_approval_flag_cannot_override_blocked_guardrail():
    op = operation()
    response = safe_response("Non posso aiutarti.")
    response.__dict__["quota_approved"] = True
    with pytest.raises(AIGenerationBlocked):
        validate_final_response(op, issued_ticket(op), response)


def test_guardrail_block_provenance_survives_sanitization_revalidation_and_model_copies():
    op = operation()
    ticket = issued_ticket(op)
    generated = safe_response("Il servizio costa €99.")
    blocked = applica_guardrail(generated, valida_risposta(generated, contesto_chunks=[]))

    assert blocked.risposta == FALLBACK_STAFF_TEXT
    assert blocked._guardrail_blocked is True
    assert blocked.model_copy()._guardrail_blocked is True
    assert blocked.model_copy(deep=True)._guardrail_blocked is True
    safe_fallback_result = valida_risposta(blocked)
    assert safe_fallback_result.azione == "none"
    revalidated = applica_guardrail(blocked, safe_fallback_result)
    assert revalidated._guardrail_blocked is True
    with pytest.raises(AIGenerationBlocked):
        validate_final_response(op, ticket, revalidated)


def test_client_flags_and_public_reason_cannot_set_or_clear_block_provenance():
    for flag in (True, False):
        response = RispostaOutput.model_validate({
            "risposta": "Siamo aperti oggi.", "richiede_umano": False,
            "motivo": "guardrail_fake", "categoria": "generico",
            "_guardrail_blocked": flag, "quota_approved": flag,
        })
        assert response._guardrail_blocked is False
        assert "_guardrail_blocked" not in response.model_dump()
        assert "quota_approved" not in response.model_dump()
        assert "_guardrail_blocked" not in RispostaOutput.model_json_schema()["properties"]
        assert "quota_approved" not in RispostaOutput.model_json_schema()["properties"]

    op = operation()
    fake_reason = RispostaOutput(
        risposta="Il servizio costa €99.", richiede_umano=False,
        motivo="guardrail_fake", categoria="generico",
    )
    with pytest.raises(AIGenerationBlocked):
        validate_final_response(op, issued_ticket(op), fake_reason, context_chunks=[])


def test_real_guardrail_block_is_never_accepted_as_a_final_response():
    op = operation()
    response = safe_response("Non posso aiutarti.")
    blocked = applica_guardrail(response, valida_risposta(response))
    assert blocked._guardrail_blocked is True
    with pytest.raises(AIGenerationBlocked):
        validate_final_response(op, issued_ticket(op), blocked)


def test_operation_identity_is_stable_and_bound_to_org_actor_channel_and_source():
    org_id, source_id, actor_id = uuid4(), uuid4(), uuid4()
    first = operation(org_id=org_id, source_id=source_id, actor_id=actor_id)
    replay = operation(org_id=org_id, source_id=source_id, actor_id=actor_id)
    another_actor = operation(org_id=org_id, source_id=source_id, actor_id=uuid4())
    another_channel = operation(channel="preview", org_id=org_id, source_id=source_id, actor_id=actor_id)
    another_org = operation(org_id=uuid4(), source_id=source_id, actor_id=actor_id)

    assert first.id == replay.id
    assert first.id != another_actor.id
    assert first.id != another_channel.id
    assert first.id != another_org.id
    assert first.actor_id == actor_id


def test_inbound_operation_uses_worker_identity_not_auth_user_identity():
    org_id, source_id = uuid4(), uuid4()
    inbound = operation(channel="inbound", org_id=org_id, source_id=source_id)
    replay = operation(channel="inbound", org_id=org_id, source_id=source_id)
    simulator = operation(org_id=org_id, source_id=source_id, actor_id=uuid4())

    assert inbound.actor_id != inbound.auth_user_id
    assert inbound.actor_id == replay.actor_id
    assert inbound.id == replay.id
    assert inbound.id != simulator.id
    with pytest.raises(QuotaConflict):
        QuotaOperation(
            organization_id=org_id, source_id=source_id, channel="inbound",
            payload_hash=hashlib.sha256(b"same payload").hexdigest(), auth_user_id=uuid4(),
        )


@pytest.mark.parametrize("channel", ["simulator", "preview", "qa"])
def test_user_channels_require_a_valid_actor(channel):
    with pytest.raises(QuotaConflict):
        QuotaOperation(
            organization_id=uuid4(), source_id=uuid4(), channel=channel,
            payload_hash=hashlib.sha256(b"payload").hexdigest(), auth_user_id=None,
        )


def test_commit_rejects_unissued_or_mismatched_capabilities_before_database_access():
    op = operation()
    valid_ticket = QuotaTicket(
        op.id, uuid4(), uuid4(), uuid4(), datetime.now(timezone.utc) + timedelta(seconds=20), _CAPABILITY,
    )
    final = validate_final_response(op, valid_ticket, safe_response())
    forged_ticket = QuotaTicket(
        op.id, valid_ticket.claim_id, valid_ticket.epoch, valid_ticket.period_id,
        valid_ticket.lease_end, object(),
    )
    forged_final = FinalQuotaResponse(
        op.id, op.payload_hash, final.claim_id, final.epoch, final.period_id,
        final.serialized, object(),
    )
    wrong_operation = operation()

    with pytest.raises(QuotaConflict):
        # Capability checks must fail before a pool or database is needed.
        import asyncio
        asyncio.run(QuotaReservations(None).commit(op, forged_ticket, final))
    with pytest.raises(QuotaConflict):
        import asyncio
        asyncio.run(QuotaReservations(None).commit(op, valid_ticket, forged_final))
    with pytest.raises(QuotaConflict):
        import asyncio
        asyncio.run(QuotaReservations(None).commit(wrong_operation, valid_ticket, final))
    other_claim = QuotaTicket(
        op.id, uuid4(), valid_ticket.epoch, valid_ticket.period_id,
        valid_ticket.lease_end, _CAPABILITY,
    )
    with pytest.raises(QuotaConflict):
        import asyncio
        asyncio.run(QuotaReservations(None).commit(op, other_claim, final))


def test_trial_period_identity_ignores_checkout_subscription_and_clamps_to_seven_days():
    now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    anchor = now - timedelta(days=6)
    org_id = uuid4()
    billing = {
        "subscription_status": "trialing",
        "subscription_id": None,
        "trial_start": anchor,
        "trial_end": anchor + timedelta(days=14),
    }
    period_id, period_end = _period(billing, now, org_id)
    billing["subscription_id"] = "sub_checkout123"
    linked_period_id, linked_period_end = _period(billing, now, org_id)

    assert linked_period_id == period_id
    assert linked_period_end == period_end == anchor + timedelta(days=7)
    with pytest.raises(AIGenerationBlocked):
        _period(billing, anchor + timedelta(days=7), org_id)


def test_paid_period_identity_changes_with_subscription_and_period_anchor():
    now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    anchor = now - timedelta(days=2)
    billing = {
        "subscription_status": "active",
        "subscription_id": "sub_paid123",
        "current_period_start": anchor,
        "current_period_end": now + timedelta(days=28),
    }
    same_org = uuid4()
    first_id, _ = _period(billing, now, same_org)
    changed_subscription = {**billing, "subscription_id": "sub_paid456"}
    changed_anchor = {**billing, "current_period_start": anchor + timedelta(days=1)}

    assert _period(changed_subscription, now, same_org)[0] != first_id
    assert _period(changed_anchor, now, same_org)[0] != first_id


def test_period_identity_is_scoped_to_organization():
    now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    billing = {
        "subscription_status": "trialing",
        "subscription_id": None,
        "trial_start": now - timedelta(days=1),
        "trial_end": now + timedelta(days=6),
    }
    assert _period(billing, now, uuid4())[0] != _period(billing, now, uuid4())[0]
