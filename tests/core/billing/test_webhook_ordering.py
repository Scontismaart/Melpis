import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]

CUSTOMER = "cus_ordering_test"
SUBSCRIPTION = "sub_ordering_test"
PRICE = "price_ordering_test"
PERIOD_START = 1_800_000_000
PERIOD_END = PERIOD_START + 30 * 24 * 60 * 60


@pytest.fixture(autouse=True)
def mapped_monthly_plan_price(monkeypatch):
    from src.core.billing import webhook_handler
    from src.core.billing.plans import PLANS

    monkeypatch.setattr(PLANS["starter"], "stripe_price_id", PRICE)
    monkeypatch.setitem(webhook_handler.PRICE_TO_PLAN, PRICE, "starter")


async def _bound_org(repo, org_id, *, status="trialing", cursor=0, event_id=None,
                     period_start=None, period_end=None, used=0, customer=CUSTOMER,
                     subscription=SUBSCRIPTION):
    await repo.update_organization_billing(org_id, {
        "stripe_customer_id": customer,
        "subscription_id": subscription,
        "subscription_status": status,
        "current_period_start": (
            datetime.fromtimestamp(period_start, tz=timezone.utc)
            if period_start is not None else None
        ),
        "current_period_end": (
            datetime.fromtimestamp(period_end, tz=timezone.utc)
            if period_end is not None else None
        ),
        "messages_used_this_period": used,
    })
    if cursor or event_id:
        async with repo.pool.acquire() as conn:
            await conn.execute(
                "UPDATE organizations SET stripe_event_created = $2, stripe_event_id = $3 WHERE id = $1",
                org_id, cursor or None, event_id,
            )


def _paid_invoice(event_id, created, *, invoice_id="in_ordering_test",
                  subscription=SUBSCRIPTION, customer=CUSTOMER,
                  period_start=PERIOD_START, period_end=PERIOD_END,
                  price_id=PRICE, product="", amount_paid=2900,
                  currency="eur", status="paid", intent_id=None):
    data = {
        "id": invoice_id,
        "customer": customer,
        "subscription": subscription,
        "status": status,
        "amount_paid": amount_paid,
        "currency": currency,
        "period_start": period_start - 30 * 86400,
        "period_end": period_start,
        "lines": {"data": [{
            "type": "subscription",
            "amount": 2900,
            "currency": "eur",
            "subscription": subscription,
            "proration": False,
            "quantity": 1,
            "price": {"id": price_id, "recurring": {"interval": "month", "interval_count": 1}},
            "period": {"start": period_start, "end": period_end},
        }]},
    }
    if intent_id is not None:
        data["subscription_details"] = {"metadata": {"checkout_intent_id": str(intent_id)}}
    return {"id": event_id, "created": created, "type": "invoice.paid", "data": {"object": data}}


def _failed_invoice(event_id, created, *, invoice_id="in_ordering_test",
                    subscription=SUBSCRIPTION, customer=CUSTOMER):
    event = _paid_invoice(event_id, created, invoice_id=invoice_id,
                          subscription=subscription, customer=customer,
                          status="open", amount_paid=0)
    event["type"] = "invoice.payment_failed"
    return event


async def _seed_intent(repo, org_id, *, customer=CUSTOMER, price_id=PRICE,
                       subscription=None, intent_id=None, resolution=None):
    intent_id = intent_id or uuid.uuid4()
    payload = {
        "checkout_intent_id": str(intent_id),
        "stripe_customer_id": customer,
        "plan": "starter",
        "interval": "monthly",
        "price_id": price_id,
        "trial_end_unix": None,
    }
    if subscription is not None:
        payload["_subscription_id"] = subscription
    if resolution is not None:
        payload["_resolution"] = resolution
    async with repo.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO billing_checkout_intents(
                id, organization_id, request_key, payload_hash, payload,
                session_id, session_url, expires_at
            ) VALUES ($1, $2, $3, 'ordering-test', $4::jsonb, $5, $6,
                      NOW() + interval '4 days')
        """, intent_id, org_id, f"ordering-{intent_id}", json.dumps(payload),
            f"cs_{intent_id}", f"https://checkout.example.test/cs_{intent_id}")
    return intent_id


def _checkout(event_id, created, org_id, intent_id, *, session_id=None,
              customer=CUSTOMER, subscription=SUBSCRIPTION, event_type="checkout.session.completed"):
    session_id = session_id or f"cs_{intent_id}"
    return {
        "id": event_id,
        "created": created,
        "type": event_type,
        "data": {"object": {
            "id": session_id,
            "client_reference_id": str(org_id),
            "mode": "subscription",
            "customer": customer,
            "subscription": subscription,
            "status": "complete" if event_type.endswith("completed") else "expired",
            "metadata": {
                "checkout_intent_id": str(intent_id),
                "trial_end_unix": "none",
            },
        }},
    }


async def _processed(repo, event_id):
    return await repo.pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM processed_stripe_events WHERE event_id = $1)", event_id
    )


async def _event_cursor(repo, org_id):
    return await repo.pool.fetchrow(
        "SELECT stripe_event_created, stripe_event_id FROM organizations WHERE id = $1",
        org_id,
    )


async def _audit_count(repo, org_id, action):
    return await repo.pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE organization_id = $1 AND action = $2",
        org_id, action,
    )


async def _use_test_plan_price(monkeypatch):
    from src.core.billing import webhook_handler
    from src.core.billing.plans import PLANS
    monkeypatch.setattr(PLANS["starter"], "stripe_price_id", PRICE)
    monkeypatch.setitem(webhook_handler.PRICE_TO_PLAN, PRICE, "starter")


async def test_checkout_then_paid_and_paid_then_checkout_converge(repo, sample_org, monkeypatch):
    """Both event orders converge to paid entitlement without resetting twice."""
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _use_test_plan_price(monkeypatch)
    # Checkout first: an equal-created paid invoice must promote trialing to active.
    intent = await _seed_intent(repo, sample_org["id"])
    checkout = _checkout("evt_checkout_first", 300, sample_org["id"], intent)
    await handle_stripe_webhook(checkout, repo, 7)
    await repo.increment_message_usage(sample_org["id"])
    invoice = _paid_invoice("evt_paid_after_checkout", 300, intent_id=intent,
                            price_id=PRICE)
    await handle_stripe_webhook(invoice, repo, 7)
    duplicate = await handle_stripe_webhook(invoice, repo, 7)
    first = await repo.get_organization_billing(sample_org["id"])
    assert duplicate["action"] == "duplicate"
    assert first["subscription_status"] == "active"
    assert first["messages_used_this_period"] == 0
    assert first["current_period_start"] == datetime.fromtimestamp(PERIOD_START, tz=timezone.utc)

    # Paid invoice first proves the intent and binds the subscription. The later
    # equal-created Checkout only completes that same intent.
    other_id = uuid.uuid4()
    async with repo.pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, 'Reverse order')", other_id)
    reverse_customer = "cus_ordering_reverse"
    await _bound_org(repo, other_id, subscription=None, status="trialing", customer=reverse_customer)
    reverse_intent = await _seed_intent(repo, other_id, customer=reverse_customer)
    paid_first = _paid_invoice("evt_paid_first", 400, invoice_id="in_paid_first",
                               subscription="sub_paid_first", price_id=PRICE,
                               customer=reverse_customer, intent_id=reverse_intent)
    await handle_stripe_webhook(paid_first, repo, 7)
    await repo.increment_message_usage(other_id)
    checkout_after = _checkout("evt_checkout_after_paid", 400, other_id, reverse_intent,
                               customer=reverse_customer, subscription="sub_paid_first")
    checkout_result = await handle_stripe_webhook(checkout_after, repo, 7)
    second = await repo.get_organization_billing(other_id)
    assert checkout_result["action"] in {"checkout_confirmed", "ignored"}
    assert second["subscription_status"] == "active"
    assert second["messages_used_this_period"] == 1
    assert second["current_period_start"] == datetime.fromtimestamp(PERIOD_START, tz=timezone.utc)


async def test_newer_checkout_expired_does_not_block_older_paid_invoice(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="trialing")
    intent = await _seed_intent(repo, sample_org["id"])
    expired = _checkout("evt_checkout_expired_newer", 900, sample_org["id"], intent,
                        event_type="checkout.session.expired")
    await handle_stripe_webhook(expired, repo, 7)
    invoice = _paid_invoice("evt_paid_older", 700)
    result = await handle_stripe_webhook(invoice, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "subscription_activated"
    assert billing["subscription_status"] == "active"
    assert (await _event_cursor(repo, sample_org["id"]))["stripe_event_created"] == 700


@pytest.mark.parametrize("incoming_start", [PERIOD_START, PERIOD_START - 86400])
async def test_equal_or_older_paid_period_never_resets_quota(repo, sample_org, incoming_start):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    current_start = PERIOD_START + 86400
    await _bound_org(repo, sample_org["id"], status="active", cursor=500,
                     event_id="evt_cursor", period_start=current_start,
                     period_end=current_start + 30 * 86400, used=17)
    event = _paid_invoice("evt_replayed_period", 501, invoice_id=f"in_period_{incoming_start}",
                          period_start=incoming_start, period_end=incoming_start + 30 * 86400)
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["messages_used_this_period"] == 17
    assert billing["current_period_start"] == datetime.fromtimestamp(current_start, tz=timezone.utc)


@pytest.mark.parametrize("event_type", ["invoice.paid", "invoice.payment_failed"])
async def test_newer_event_for_old_paid_period_cannot_restore_past_due(repo, sample_org, event_type):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    current_start = PERIOD_START + 86400
    await _bound_org(repo, sample_org["id"], status="past_due", cursor=500,
                     event_id="evt_past_due_cursor", period_start=current_start,
                     period_end=current_start + 30 * 86400, used=17)
    event = _paid_invoice("evt_newer_but_old_period", 501,
                          invoice_id="in_old_period", period_start=PERIOD_START,
                          period_end=PERIOD_END)
    event["type"] = event_type
    if event_type == "invoice.payment_failed":
        event["data"]["object"].update(status="open", amount_paid=0)
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "ignored"
    assert result["reason"] == "old_billing_period"
    assert billing["subscription_status"] == "past_due"
    assert billing["messages_used_this_period"] == 17
    assert (await _event_cursor(repo, sample_org["id"]))["stripe_event_created"] == 500


async def test_service_line_period_wins_over_invoice_usage_lookback(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="past_due", cursor=520,
                     event_id="evt_prior_invoice", period_start=PERIOD_START,
                     period_end=PERIOD_END, used=8)
    event = _paid_invoice("evt_current_service_period", 521,
                          invoice_id="in_current_service_period")
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "subscription_activated"
    assert billing["subscription_status"] == "active"
    assert billing["messages_used_this_period"] == 8
    assert billing["current_period_start"] == datetime.fromtimestamp(PERIOD_START, tz=timezone.utc)


@pytest.mark.parametrize("invalid_line", ["missing", "proration", "unknown_price", "paginated", "annual", "multiple", "mixed_proration", "zero_service", "other_currency", "other_subscription", "quantity", "invalid_period"])
async def test_paid_invoice_without_one_valid_monthly_service_line_retries(
    repo, sample_org, invalid_line,
):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="active", used=9)
    event = _paid_invoice(f"evt_invalid_service_line_{invalid_line}", 530)
    lines = event["data"]["object"]["lines"]["data"]
    if invalid_line == "missing":
        event["data"]["object"]["lines"]["data"] = []
    elif invalid_line == "proration":
        lines[0]["proration"] = True
    elif invalid_line == "unknown_price":
        lines[0]["price"]["id"] = "price_not_configured"
    elif invalid_line == "paginated":
        event["data"]["object"]["lines"]["has_more"] = True
    elif invalid_line == "annual":
        lines[0]["price"]["recurring"]["interval"] = "year"
    elif invalid_line == "multiple":
        lines.append(dict(lines[0]))
    elif invalid_line == "mixed_proration":
        lines.append({**lines[0], "proration": True})
    elif invalid_line == "zero_service":
        lines[0]["amount"] = 0
        lines.append({"type": "invoiceitem", "amount": 2900, "currency": "eur"})
    elif invalid_line == "other_currency":
        lines[0]["currency"] = "usd"
    elif invalid_line == "other_subscription":
        lines[0]["subscription"] = "sub_other"
    elif invalid_line == "quantity":
        lines[0]["quantity"] = 2
    elif invalid_line == "invalid_period":
        lines[0]["period"]["end"] = lines[0]["period"]["start"]

    with pytest.raises(HTTPException) as exc:
        await handle_stripe_webhook(event, repo, 7)
    assert exc.value.status_code == 503
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "active"
    assert billing["messages_used_this_period"] == 9
    assert not await _processed(repo, event["id"])


async def test_checkout_after_paid_preserves_active_period_quota_and_trial(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="trialing")
    intent = await _seed_intent(repo, sample_org["id"])
    await handle_stripe_webhook(_paid_invoice("evt_paid_before_checkout", 600), repo, 7)
    await repo.increment_message_usage(sample_org["id"])
    before = await repo.get_organization_billing(sample_org["id"])
    result = await handle_stripe_webhook(
        _checkout("evt_checkout_after_paid_preserves", 601, sample_org["id"], intent), repo, 7
    )
    after = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "checkout_confirmed"
    assert after["subscription_status"] == "active"
    assert after["messages_used_this_period"] == before["messages_used_this_period"] == 1
    assert after["current_period_start"] == before["current_period_start"]
    assert after["current_period_end"] == before["current_period_end"]
    assert after["trial_start"] == before["trial_start"]
    assert after["trial_end"] == before["trial_end"]


async def test_failed_then_paid_same_invoice_and_timestamp_accepts_paid_with_audit(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="active")
    failed = _failed_invoice("evt_failed_then_paid", 800)
    await handle_stripe_webhook(failed, repo, 7)
    paid = _paid_invoice("evt_paid_same_invoice", 800)
    result = await handle_stripe_webhook(paid, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "subscription_activated"
    assert billing["subscription_status"] == "active"
    assert await _audit_count(repo, sample_org["id"], "billing.webhook.payment_failed") == 1
    assert await _audit_count(repo, sample_org["id"], "billing.webhook.subscription_activated") == 1


async def test_paid_then_failed_same_invoice_and_timestamp_keeps_active_with_audit(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="active")
    paid = _paid_invoice("evt_paid_then_failed", 801)
    await handle_stripe_webhook(paid, repo, 7)
    failed = _failed_invoice("evt_failed_same_invoice", 801)
    result = await handle_stripe_webhook(failed, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "ignored"
    assert result["reason"] == "invoice_already_paid"
    assert billing["subscription_status"] == "active"
    assert await _audit_count(repo, sample_org["id"], "billing.webhook.subscription_activated") == 1
    assert not await _processed(repo, "evt_failed_same_invoice")


async def test_different_paid_invoice_at_same_failure_cursor_is_retryable(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="active")
    await handle_stripe_webhook(_failed_invoice("evt_failure_cursor", 802), repo, 7)
    conflicting = _paid_invoice("evt_other_invoice_same_cursor", 802,
                                invoice_id="in_a_different_invoice")
    with pytest.raises(HTTPException) as exc:
        await handle_stripe_webhook(conflicting, repo, 7)
    assert exc.value.status_code == 503
    assert not await _processed(repo, "evt_other_invoice_same_cursor")
    assert (await repo.get_organization_billing(sample_org["id"]))["subscription_status"] == "past_due"


async def test_stale_and_canceled_subscription_events_cannot_reactivate(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="past_due", cursor=1_000,
                     event_id="evt_latest_failure")
    stale = _paid_invoice("evt_stale_paid", 999)
    stale_result = await handle_stripe_webhook(stale, repo, 7)
    assert stale_result["reason"] == "stale_event"
    assert (await repo.get_organization_billing(sample_org["id"]))["subscription_status"] == "past_due"

    deleted = {
        "id": "evt_subscription_deleted",
        "created": 1_001,
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": SUBSCRIPTION, "customer": CUSTOMER}},
    }
    await handle_stripe_webhook(deleted, repo, 7)
    later_paid = _paid_invoice("evt_paid_after_cancel", 1_002)
    canceled_result = await handle_stripe_webhook(later_paid, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert canceled_result["reason"] == "canceled_subscription"
    assert billing["subscription_status"] == "canceled"


async def test_unknown_subscription_paid_requires_signed_frozen_intent(repo, sample_org, monkeypatch):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _use_test_plan_price(monkeypatch)
    await _bound_org(repo, sample_org["id"], status="trialing", subscription=None)
    event = _paid_invoice("evt_unknown_sub_no_proof", 1_100, subscription="sub_unbound")
    with pytest.raises(HTTPException) as exc:
        await handle_stripe_webhook(event, repo, 7)
    assert exc.value.status_code == 503
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_id"] is None
    assert billing["subscription_status"] == "trialing"
    assert not await _processed(repo, "evt_unknown_sub_no_proof")


@pytest.mark.parametrize("mismatch", ["intent", "customer", "subscription", "price", "organization"])
async def test_unknown_subscription_paid_rejects_mismatched_intent_proof(
    repo, sample_org, other_org, monkeypatch, mismatch,
):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _use_test_plan_price(monkeypatch)
    await _bound_org(repo, sample_org["id"], status="trialing", subscription=None)
    intent = await _seed_intent(repo, sample_org["id"])
    event = _paid_invoice(f"evt_bad_proof_{mismatch}", 1_200,
                          subscription="sub_unbound", price_id=PRICE, product="",
                          intent_id=intent)
    data = event["data"]["object"]
    if mismatch == "intent":
        data["subscription_details"]["metadata"]["checkout_intent_id"] = str(uuid.uuid4())
    elif mismatch == "customer":
        async with repo.pool.acquire() as conn:
            await conn.execute("""
                UPDATE billing_checkout_intents
                SET payload = jsonb_set(payload, '{stripe_customer_id}', to_jsonb('cus_other'::text))
                WHERE id = $1::uuid
            """, intent)
    elif mismatch == "subscription":
        async with repo.pool.acquire() as conn:
            await conn.execute("""
                UPDATE billing_checkout_intents
                SET payload = jsonb_set(payload, '{_subscription_id}', to_jsonb('sub_other'::text))
                WHERE id = $1::uuid
            """, intent)
    elif mismatch == "price":
        data["lines"]["data"][0]["price"]["id"] = "price_wrong"
    elif mismatch == "organization":
        other_intent = await _seed_intent(repo, other_org["id"], intent_id=uuid.uuid4())
        data["subscription_details"]["metadata"]["checkout_intent_id"] = str(other_intent)

    with pytest.raises(HTTPException) as exc:
        await handle_stripe_webhook(event, repo, 7)
    assert exc.value.status_code == 503
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_id"] is None
    assert billing["subscription_status"] == "trialing"
    assert not await _processed(repo, event["id"])


async def test_unknown_nonpaid_invoice_cannot_bind_subscription(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="trialing", subscription=None)
    event = _paid_invoice("evt_unpaid_unknown_subscription", 1_300,
                          subscription="sub_unbound", status="open")
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["reason"] == "invoice_not_paid"
    assert billing["subscription_id"] is None
    assert billing["subscription_status"] == "trialing"
    assert not await _processed(repo, event["id"])


async def test_paid_invoice_rejects_customer_or_subscription_mismatch(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="active")
    customer_mismatch = _paid_invoice("evt_customer_mismatch", 1_400, customer="cus_wrong")
    sub_mismatch = _paid_invoice("evt_subscription_mismatch", 1_401, subscription="sub_wrong")
    for event in (customer_mismatch, sub_mismatch):
        result = await handle_stripe_webhook(event, repo, 7)
        assert result is None or result["action"] == "ignored"
        assert not await _processed(repo, event["id"])
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_id"] == SUBSCRIPTION
    assert billing["subscription_status"] == "active"


async def test_paid_invoice_after_local_checkout_expiry_is_accepted_for_bound_subscription(
    repo, sample_org, monkeypatch,
):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _use_test_plan_price(monkeypatch)
    await _bound_org(repo, sample_org["id"], status="trialing", subscription="sub_after_expiry")
    intent = await _seed_intent(repo, sample_org["id"], subscription="sub_after_expiry")
    async with repo.pool.acquire() as conn:
        await conn.execute("""
            UPDATE billing_checkout_intents SET expires_at = NOW() - interval '1 day'
            WHERE id = $1::uuid
        """, intent)
    event = _paid_invoice("evt_paid_after_intent_expiry", 1_500,
                          subscription="sub_after_expiry", price_id=PRICE)
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "subscription_activated"
    assert billing["subscription_id"] == "sub_after_expiry"
    assert billing["subscription_status"] == "active"


async def test_zero_paid_trial_invoice_does_not_grant_active_entitlement(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="trialing")
    event = _paid_invoice("evt_zero_trial_invoice", 1_600, amount_paid=0)
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result is None
    assert billing["subscription_status"] == "trialing"
    assert not await _processed(repo, event["id"])


async def test_concurrent_paid_and_failed_same_invoice_converge_without_double_grant(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="active", used=12)
    paid = _paid_invoice("evt_concurrent_paid", 1700)
    failed = _failed_invoice("evt_concurrent_failed", 1700)
    await asyncio.gather(*(handle_stripe_webhook(event, repo, 7) for event in (failed, paid, paid)))
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "active"
    assert billing["messages_used_this_period"] == 0
    assert await _audit_count(repo, sample_org["id"], "billing.webhook.subscription_activated") == 1
    await repo.increment_message_usage(sample_org["id"])
    await handle_stripe_webhook(paid, repo, 7)
    assert (await repo.get_organization_billing(sample_org["id"]))["messages_used_this_period"] == 1


async def test_concurrent_checkout_and_paid_invoice_resolve_one_intent(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], subscription=None)
    intent = await _seed_intent(repo, sample_org["id"])
    checkout = _checkout("evt_concurrent_checkout", 1800, sample_org["id"], intent)
    paid = _paid_invoice("evt_concurrent_bound_invoice", 1800, intent_id=intent)
    await asyncio.gather(*(handle_stripe_webhook(event, repo, 7) for event in (paid, checkout, paid, checkout)))
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "active"
    assert billing["subscription_id"] == SUBSCRIPTION
    assert await _audit_count(repo, sample_org["id"], "billing.webhook.subscription_activated") == 1
    payload = await repo.pool.fetchval("SELECT payload FROM billing_checkout_intents WHERE id = $1", intent)
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["_resolved"] is True
    assert payload["_subscription_id"] == SUBSCRIPTION


@pytest.mark.parametrize("initial_status", ["trialing", "incomplete"])
async def test_first_paid_period_replaces_later_provisional_checkout_time_once(repo, sample_org, initial_status):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status=initial_status,
                     period_start=PERIOD_START + 30, period_end=PERIOD_START + 7 * 86400, used=150)
    intent = await _seed_intent(repo, sample_org["id"], subscription=SUBSCRIPTION, resolution="completed")
    event = _paid_invoice("evt_initial_provider_period", 1900, intent_id=intent)
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "subscription_activated"
    assert billing["current_period_start"] == datetime.fromtimestamp(PERIOD_START, tz=timezone.utc)
    assert billing["current_period_end"] == datetime.fromtimestamp(PERIOD_END, tz=timezone.utc)
    assert billing["messages_used_this_period"] == 0
    await repo.increment_message_usage(sample_org["id"])
    await handle_stripe_webhook(event, repo, 7)
    second = _paid_invoice("evt_same_initial_period_other_invoice", 1901,
                           invoice_id="in_same_period_other", intent_id=intent)
    await handle_stripe_webhook(second, repo, 7)
    assert (await repo.get_organization_billing(sample_org["id"]))["messages_used_this_period"] == 1


@pytest.mark.parametrize("later_status", ["trialing", "incomplete"])
@pytest.mark.parametrize("retain_marker", [True, False])
async def test_paid_history_prevents_provisional_reset_after_status_changes(
    repo, sample_org, later_status, retain_marker,
):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await _bound_org(repo, sample_org["id"], status="incomplete")
    intent = await _seed_intent(repo, sample_org["id"], subscription=SUBSCRIPTION, resolution="completed")
    await handle_stripe_webhook(_paid_invoice("evt_established_paid", 2000, intent_id=intent), repo, 7)
    await _bound_org(repo, sample_org["id"], status=later_status, cursor=2000,
                     period_start=PERIOD_START, period_end=PERIOD_END, used=11)
    if not retain_marker:
        async with repo.pool.acquire() as conn:
            await conn.execute("UPDATE billing_checkout_intents SET payload = payload - '_paid_period_initialized' WHERE id = $1", intent)
    event = _paid_invoice("evt_old_paid_after_status_change", 2001,
                          invoice_id="in_old_after_change", period_start=PERIOD_START - 86400,
                          period_end=PERIOD_END - 86400, intent_id=intent)
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["reason"] == "old_billing_period"
    assert billing["subscription_status"] == later_status
    assert billing["messages_used_this_period"] == 11
    assert not await _processed(repo, event["id"])


async def test_bound_subscription_monthly_plan_change_keeps_original_intent_metadata(repo, sample_org, monkeypatch):
    from src.core.billing.plans import PLANS
    from src.core.billing.webhook_handler import handle_stripe_webhook

    monkeypatch.setattr(PLANS["pro"], "stripe_price_id", "price_monthly_upgrade")
    await _bound_org(repo, sample_org["id"], status="active", cursor=2100,
                     period_start=PERIOD_START, period_end=PERIOD_END, used=18)
    intent = await _seed_intent(repo, sample_org["id"], subscription=SUBSCRIPTION, resolution="completed")
    event = _paid_invoice("evt_monthly_plan_upgrade", 2101, invoice_id="in_monthly_upgrade",
                          period_start=PERIOD_END, period_end=PERIOD_END + 30 * 86400,
                          price_id="price_monthly_upgrade", intent_id=intent, amount_paid=6900)
    event["data"]["object"]["lines"]["data"][0]["amount"] = 6900
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "subscription_activated"
    assert billing["plan"] == "pro"
    assert billing["messages_limit"] == PLANS["pro"].messages_limit
    assert billing["messages_used_this_period"] == 0
    assert billing["current_period_start"] == datetime.fromtimestamp(PERIOD_END, tz=timezone.utc)


def _subscription_snapshot(event_id, created, *, status="active", price_id=PRICE,
                           period_start=PERIOD_START, period_end=PERIOD_END):
    return {"id": event_id, "created": created, "type": "customer.subscription.updated",
            "data": {"object": {
                "id": SUBSCRIPTION, "customer": CUSTOMER, "status": status,
                "current_period_start": period_start, "current_period_end": period_end,
                "items": {"has_more": False, "data": [{"quantity": 1, "price": {
                    "id": price_id, "currency": "eur", "unit_amount": 2900,
                    "recurring": {"interval": "month", "interval_count": 1}}}]},
            }}}


@pytest.mark.parametrize("previous", ["trialing", "incomplete", "past_due", "unpaid", "paused"])
async def test_active_snapshot_cannot_recover_rights_without_invoice(repo, sample_org, previous):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    org_id = sample_org["id"]
    await _bound_org(repo, org_id, status=previous, used=150,
                     period_start=PERIOD_START, period_end=PERIOD_END)
    event = _subscription_snapshot("evt_no_payment_proof", 400)
    cursor_before = dict(await _event_cursor(repo, org_id))
    with pytest.raises(HTTPException) as error:
        await handle_stripe_webhook(event, repo, 7)
    assert error.value.status_code == 503
    billing = await repo.get_organization_billing(org_id)
    assert billing["subscription_status"] == previous
    assert billing["messages_used_this_period"] == 150
    assert not await _processed(repo, event["id"])
    assert dict(await _event_cursor(repo, org_id)) == cursor_before


@pytest.mark.parametrize("change", ["period", "plan", "unknown_price"])
async def test_active_snapshot_cannot_create_new_paid_period_or_plan(repo, sample_org, change, monkeypatch):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    from src.core.billing.plans import PLANS
    org_id = sample_org["id"]
    await _bound_org(repo, org_id, status="active", used=121,
                     period_start=PERIOD_START, period_end=PERIOD_END)
    await repo.update_organization_billing(org_id, {"plan": "starter", "messages_limit": 500})
    event = _subscription_snapshot("evt_snapshot_new_rights", 400)
    data = event["data"]["object"]
    if change == "period":
        data["current_period_start"] = PERIOD_END
        data["current_period_end"] = PERIOD_END + 2592000
    elif change == "plan":
        monkeypatch.setattr(PLANS["pro"], "stripe_price_id", "price_verified_pro")
        data["items"]["data"][0]["price"].update(id="price_verified_pro", unit_amount=6900)
    else:
        data["items"]["data"][0]["price"]["id"] = "price_unknown"
    with pytest.raises(HTTPException) as error:
        await handle_stripe_webhook(event, repo, 7)
    assert error.value.status_code == 503
    billing = await repo.get_organization_billing(org_id)
    assert billing["plan"] == "starter"
    assert billing["messages_limit"] == 500
    assert billing["messages_used_this_period"] == 121
    assert int(billing["current_period_start"].timestamp()) == PERIOD_START
    assert not await _processed(repo, event["id"])


@pytest.mark.parametrize("previous,incoming,expected", [
    ("active", "unpaid", "unpaid"), ("active", "invented_status", "incomplete"),
    ("unpaid", "past_due", "unpaid"), ("paused", "past_due", "paused"),
    ("incomplete", "past_due", "incomplete"),
])
async def test_restrictive_unknown_catalog_never_relaxes_rights(repo, sample_org, previous, incoming, expected):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    org_id = sample_org["id"]
    await _bound_org(repo, org_id, status=previous, used=111,
                     period_start=PERIOD_START, period_end=PERIOD_END)
    event = _subscription_snapshot("evt_unknown_restrictive", 400, status=incoming, price_id="unknown")
    result = await handle_stripe_webhook(event, repo, 7)
    assert result["action"] == "subscription_updated"
    billing = await repo.get_organization_billing(org_id)
    assert billing["subscription_status"] == expected
    assert billing["messages_used_this_period"] == 111
    assert int(billing["current_period_start"].timestamp()) == PERIOD_START


async def test_unknown_status_tie_classified_by_effective_denial(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    org_id = sample_org["id"]
    await _bound_org(repo, org_id, status="active", cursor=400, event_id="evt_prior_paid", used=111,
                     period_start=PERIOD_START, period_end=PERIOD_END)
    event = _subscription_snapshot("evt_tie_unknown", 400, status="unexpected", price_id="unknown")
    assert (await handle_stripe_webhook(event, repo, 7))["action"] == "subscription_updated"
    assert (await repo.get_organization_billing(org_id))["subscription_status"] == "incomplete"
    assert (await handle_stripe_webhook(event, repo, 7))["action"] == "duplicate"


async def test_trial_snapshot_preserves_150_limit_and_usage(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    org_id = sample_org["id"]
    await _bound_org(repo, org_id, status="trialing", used=149)
    end = datetime.now(timezone.utc).replace(microsecond=0)
    from datetime import timedelta
    end += timedelta(days=2)
    await repo.update_organization_billing(org_id, {"trial_end": end, "messages_limit": 150})
    event = _subscription_snapshot("evt_trial_no_upgrade", 400, status="trialing")
    event["data"]["object"]["trial_end"] = int(end.timestamp()) + 86400
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(org_id)
    assert billing["trial_end"] == end
    assert billing["messages_limit"] == 150
    assert billing["messages_used_this_period"] == 149


@pytest.mark.parametrize("short_first", [True, False])
async def test_equal_timestamp_trial_deadlines_converge_to_shortest(repo, sample_org, short_first):
    from datetime import timedelta
    from src.core.billing.webhook_handler import handle_stripe_webhook
    org_id = sample_org["id"]
    await _bound_org(repo, org_id, status="trialing", used=149)
    original = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
    shortened = original - timedelta(days=1)
    await repo.update_organization_billing(org_id, {"trial_end": original, "messages_limit": 150})
    deadlines = [shortened, original] if short_first else [original, shortened]
    for index, deadline in enumerate(deadlines):
        event = _subscription_snapshot(f"evt_trial_tie_{index}", 400, status="trialing")
        event["data"]["object"]["trial_end"] = int(deadline.timestamp())
        await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(org_id)
    assert billing["trial_end"] == shortened
    assert billing["messages_limit"] == 150
    assert billing["messages_used_this_period"] == 149
