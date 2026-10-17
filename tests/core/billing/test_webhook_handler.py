import uuid
import json
import pytest
from datetime import datetime, timezone, timedelta

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


@pytest.fixture(autouse=True)
def configured_test_prices(monkeypatch):
    from src.core.billing.plans import PLANS
    for slug in PLANS:
        monkeypatch.setattr(PLANS[slug], "stripe_price_id", f"price_test_{slug}")
        monkeypatch.setattr(PLANS[slug], "stripe_price_id_yearly", f"price_test_{slug}_yearly")
    monkeypatch.setenv("STRIPE_ANNUAL_PRICING_ENABLED", "false")


async def _seed_checkout_intent(repo, org, event, *, price_id="price_test_starter", session_id=...):
    from uuid import uuid4

    intent_id = uuid4()
    session = event["data"]["object"]
    await repo.update_organization_billing(org["id"], {"stripe_customer_id": session["customer"]})
    session.setdefault("metadata", {})["billing_checkout_intent_id"] = str(intent_id)
    payload = {"price_id": price_id, "plan": "starter", "interval": "monthly"}
    target_session_id = session["id"] if session_id is ... else session_id
    await repo.pool.execute("""
        INSERT INTO billing_checkout_intents(id, organization_id, request_key, payload_hash, payload, session_id, expires_at)
        VALUES ($1, $2::uuid, $3, 'hash', $4::jsonb, $5, NOW() + INTERVAL '1 hour')
    """, intent_id, str(org["id"]), f"test-key-{intent_id}", json.dumps(payload), target_session_id)
    return intent_id


@pytest.fixture
def valid_checkout_event():
    return {
        "id": "evt_checkout_001",
        "created": 100,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_001",
                "client_reference_id": str(uuid.uuid4()),
                "mode": "subscription",
                "customer": "cus_test001",
                "subscription": "sub_test001",
                "status": "complete",
            }
        },
    }


@pytest.fixture
def valid_invoice_paid_event():
    return {
        "id": "evt_invoice_001",
        "created": 100,
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_test_001",
                "customer": "cus_test001",
                "subscription": "sub_test001",
                "status": "paid",
                "period_start": datetime.now(timezone.utc).timestamp(),
                "period_end": datetime.now(timezone.utc).timestamp() + 2592000,
                "lines": {
                    "data": [
                        {
                            "price": {"id": "price_test_starter"},
                            "plan": {"product": "prod_starter"},
                        }
                    ]
                },
            }
        },
    }


async def test_handle_checkout_completed_sets_trialing(repo, sample_org, valid_checkout_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = valid_checkout_event
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    await _seed_checkout_intent(repo, sample_org, event)
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "trialing"
    assert billing["stripe_customer_id"] == "cus_test001"
    assert result["action"] == "subscription_created"


async def test_handle_checkout_completed_sets_trial_period(repo, sample_org, valid_checkout_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = valid_checkout_event
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    await _seed_checkout_intent(repo, sample_org, event)
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["trial_start"] is not None
    assert billing["trial_end"] is not None


async def test_checkout_completed_without_durable_intent_cannot_start_trial(repo, sample_org, valid_checkout_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = valid_checkout_event
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    await repo.update_organization_billing(sample_org["id"], {"stripe_customer_id": "cus_test001"})
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result is None
    assert billing["subscription_status"] == "incomplete"
    assert billing["subscription_id"] is None


async def test_checkout_completed_unconfigured_intent_price_cannot_start_trial(repo, sample_org, valid_checkout_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = valid_checkout_event
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    await _seed_checkout_intent(repo, sample_org, event, price_id="price_not_configured")
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result is None
    assert billing["subscription_status"] == "incomplete"
    assert billing["subscription_id"] is None


async def test_checkout_completed_before_session_id_persistence_binds_session_and_starts_trial(
    repo, sample_org, valid_checkout_event
):
    """Simulates the race condition where webhook arrives before API route commits session_id."""
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = valid_checkout_event
    session_id = event["data"]["object"]["id"]
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    intent_id = await _seed_checkout_intent(repo, sample_org, event, session_id=None)

    # Verify session_id is NULL before the webhook arrives
    stored_session_before = await repo.pool.fetchval(
        "SELECT session_id FROM billing_checkout_intents WHERE id = $1", intent_id
    )
    assert stored_session_before is None

    result = await handle_stripe_webhook(event, repo, 7)
    assert result is not None
    assert result.get("action") == "subscription_created"

    # Verify session_id is now bound atomically by the webhook handler
    stored_session_after = await repo.pool.fetchval(
        "SELECT session_id FROM billing_checkout_intents WHERE id = $1", intent_id
    )
    assert stored_session_after == session_id

    # Verify subscription state is activated
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "trialing"
    assert billing["subscription_id"] == event["data"]["object"]["subscription"]


async def test_checkout_completed_with_nonexistent_intent_id_cannot_start_trial(
    repo, sample_org, valid_checkout_event
):
    from uuid import uuid4
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = valid_checkout_event
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    event["data"]["object"]["metadata"] = {"billing_checkout_intent_id": str(uuid4())}
    await repo.update_organization_billing(sample_org["id"], {"stripe_customer_id": event["data"]["object"]["customer"]})

    result = await handle_stripe_webhook(event, repo, 7)
    assert result is None
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "incomplete"
    assert billing["subscription_id"] is None


async def test_checkout_completed_with_mismatched_session_id_cannot_start_trial(
    repo, sample_org, valid_checkout_event
):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = valid_checkout_event
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    await _seed_checkout_intent(repo, sample_org, event, session_id="cs_previously_bound_different_session")

    result = await handle_stripe_webhook(event, repo, 7)
    assert result is None
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "incomplete"
    assert billing["subscription_id"] is None


async def test_checkout_completed_with_mismatched_org_id_cannot_start_trial(
    repo, sample_org, valid_checkout_event
):
    from uuid import uuid4
    from src.core.billing.webhook_handler import handle_stripe_webhook
    other_org_id = uuid4()
    await repo.pool.execute("INSERT INTO organizations(id, name) VALUES ($1, 'Other Org')", other_org_id)
    event = valid_checkout_event
    # Seed intent for other_org_id, but webhook claims sample_org["id"]
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    await _seed_checkout_intent(repo, {"id": other_org_id}, event)

    result = await handle_stripe_webhook(event, repo, 7)
    assert result is None
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "incomplete"
    assert billing["subscription_id"] is None


async def test_checkout_completed_with_expired_intent_cannot_start_trial(
    repo, sample_org, valid_checkout_event
):
    from uuid import uuid4
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = valid_checkout_event
    # The Stripe event itself was created after the intent expiry.
    event["created"] = int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp())
    intent_id = uuid4()
    session = event["data"]["object"]
    session["client_reference_id"] = str(sample_org["id"])
    session.setdefault("metadata", {})["billing_checkout_intent_id"] = str(intent_id)
    await repo.update_organization_billing(sample_org["id"], {"stripe_customer_id": session["customer"]})
    payload = {"price_id": "price_test_starter", "plan": "starter", "interval": "monthly"}
    await repo.pool.execute("""
        INSERT INTO billing_checkout_intents(id, organization_id, request_key, payload_hash, payload, session_id, expires_at)
        VALUES ($1, $2::uuid, 'expired-key-test', 'hash', $3::jsonb, $4, NOW() - INTERVAL '10 minutes')
    """, intent_id, str(sample_org["id"]), json.dumps(payload), session["id"])

    result = await handle_stripe_webhook(event, repo, 7)
    assert result is None
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "incomplete"
    assert billing["subscription_id"] is None


async def test_checkout_completed_delivered_after_intent_expiry_still_starts_trial(
    repo, sample_org, valid_checkout_event
):
    """A valid completion created before expiry may arrive after webhook retries."""
    from src.core.billing.webhook_handler import handle_stripe_webhook

    event = valid_checkout_event
    event["created"] = int((datetime.now(timezone.utc) - timedelta(minutes=2)).timestamp())
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    intent_id = await _seed_checkout_intent(repo, sample_org, event)
    expired_at = datetime.fromtimestamp(event["created"], timezone.utc) + timedelta(seconds=30)
    assert expired_at < datetime.now(timezone.utc)
    await repo.pool.execute(
        "UPDATE billing_checkout_intents SET expires_at = $2 WHERE id = $1::uuid",
        intent_id,
        expired_at,
    )

    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])

    assert result["action"] == "subscription_created"
    assert billing["subscription_status"] == "trialing"
    assert billing["subscription_id"] == event["data"]["object"]["subscription"]


async def test_checkout_completed_with_mismatched_customer_cannot_start_trial(
    repo, sample_org, valid_checkout_event
):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = valid_checkout_event
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    await _seed_checkout_intent(repo, sample_org, event)
    # Tamper customer in event so it doesn't match org's stripe_customer_id
    event["data"]["object"]["customer"] = "cus_tampered_wrong"

    result = await handle_stripe_webhook(event, repo, 7)
    assert result is not None
    assert result.get("action") == "ignored"
    assert result.get("reason") == "customer_mismatch"
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "incomplete"
    assert billing["subscription_id"] is None


async def test_handle_invoice_paid_resets_usage(repo, sample_org, valid_invoice_paid_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
    })
    await repo.increment_message_usage(sample_org["id"])
    await repo.increment_message_usage(sample_org["id"])
    event = valid_invoice_paid_event
    event["data"]["object"]["customer"] = "cus_test001"
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["messages_used_this_period"] == 0
    assert billing["subscription_status"] == "active"


async def test_invoice_paid_converts_exhausted_trial_to_paid_quota(repo, sample_org, valid_invoice_paid_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
        "subscription_status": "trialing",
        "messages_limit": 150,
        "messages_used_this_period": 150,
    })
    event = valid_invoice_paid_event
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "active"
    assert billing["messages_limit"] == 500
    assert billing["messages_used_this_period"] == 0

    duplicate = await handle_stripe_webhook(event, repo, 7)
    assert duplicate["action"] == "duplicate"
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["messages_limit"] == 500
    assert billing["messages_used_this_period"] == 0


async def test_invoice_paid_with_unknown_price_does_not_activate_or_reset_quota(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    now = datetime.now(timezone.utc)
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_unknown_invoice",
        "subscription_id": "sub_unknown_invoice",
        "subscription_status": "past_due",
        "plan": "pro",
        "messages_limit": 2000,
        "messages_used_this_period": 2000,
        "current_period_start": now,
        "current_period_end": now.replace(year=now.year + 1),
    })
    event = {
        "id": "evt_invoice_unknown_price", "created": 300, "type": "invoice.paid",
        "data": {"object": {
            "customer": "cus_unknown_invoice", "subscription": "sub_unknown_invoice",
            "status": "paid", "period_start": (now + timedelta(days=40)).timestamp(),
            "period_end": (now + timedelta(days=70)).timestamp(),
            "lines": {"data": [{"price": {"id": "price_not_configured"}, "plan": {"product": "prod_starter"}}]},
        }},
    }
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "invoice_ignored_unconfigured_price"
    assert billing["subscription_status"] == "past_due"
    assert billing["plan"] == "pro"
    assert billing["messages_used_this_period"] == 2000
    assert billing["current_period_start"] == now


async def test_subscription_updated_unknown_price_does_not_grant_or_reset(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    now = datetime.now(timezone.utc)
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_unknown_sub", "subscription_id": "sub_unknown_sub",
        "subscription_status": "past_due", "plan": "starter", "messages_limit": 500,
        "messages_used_this_period": 23, "current_period_start": now,
    })
    event = {
        "id": "evt_sub_unknown_price", "created": 300, "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_unknown_sub", "customer": "cus_unknown_sub", "status": "active",
            "items": {"data": [{"price": {"id": "price_not_configured", "product": "prod_pro"}}]},
            "current_period_start": (now + timedelta(days=40)).timestamp(),
            "current_period_end": (now + timedelta(days=70)).timestamp(),
        }},
    }
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert result["action"] == "subscription_ignored_unconfigured_price"
    assert billing["subscription_status"] == "past_due"
    assert billing["plan"] == "starter"
    assert billing["messages_used_this_period"] == 23
    assert billing["current_period_start"] == now


async def test_subscription_updated_accepts_configured_annual_price_when_enabled(repo, sample_org, monkeypatch):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    monkeypatch.setenv("STRIPE_ANNUAL_PRICING_ENABLED", "true")
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_annual", "subscription_id": "sub_annual",
        "subscription_status": "past_due", "plan": "starter", "messages_limit": 500,
        "messages_used_this_period": 8,
    })
    now = datetime.now(timezone.utc)
    event = {
        "id": "evt_sub_annual_valid", "created": 300, "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_annual", "customer": "cus_annual", "status": "active",
            "items": {"data": [{"price": {"id": "price_test_pro_yearly"}}]},
            "current_period_start": (now + timedelta(days=40)).timestamp(),
            "current_period_end": (now + timedelta(days=405)).timestamp(),
        }},
    }
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "active"
    assert billing["plan"] == "pro"
    assert billing["messages_limit"] == 2000
    assert billing["messages_used_this_period"] == 0


async def test_handle_invoice_paid_sets_period(repo, sample_org, valid_invoice_paid_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
    })
    event = valid_invoice_paid_event
    event["data"]["object"]["customer"] = "cus_test001"
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["current_period_start"] is not None
    assert billing["current_period_end"] is not None


async def test_handle_subscription_updated_changes_plan(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
        "plan": "starter",
        "messages_limit": 500,
    })
    event = {
        "id": "evt_sub_upd_001",
        "created": 100,
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_test001",
                "customer": "cus_test001",
                "status": "active",
                "items": {
                    "data": [{"price": {"id": "price_test_pro"}}]
                },
                "current_period_start": datetime.now(timezone.utc).timestamp(),
                "current_period_end": datetime.now(timezone.utc).timestamp() + 2592000,
            }
        },
    }
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["plan"] == "pro"
    assert billing["messages_limit"] == 2000


async def test_handle_subscription_deleted_sets_canceled(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
        "subscription_status": "active",
    })
    event = {
        "id": "evt_sub_del_001",
        "created": 100,
        "type": "subscription.deleted",
        "data": {
            "object": {
                "id": "sub_test001",
                "customer": "cus_test001",
            }
        },
    }
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "canceled"
    assert result["suspension_notice"] is True
    assert billing["suspension_notified_at"] is not None


async def test_handle_subscription_deleted_no_second_notice(repo, sample_org):
    """Un secondo subscription.deleted (stessa org) non deve rilanciare la
    notifica: la colonna suspension_notified_at e' gia' valorizzata."""
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
        "subscription_status": "active",
    })
    event = {
        "id": "evt_sub_del_002",
        "created": 100,
        "type": "subscription.deleted",
        "data": {"object": {"id": "sub_test001", "customer": "cus_test001"}},
    }
    result1 = await handle_stripe_webhook(event, repo, 7)
    assert result1["suspension_notice"] is True
    event2 = {**event, "id": "evt_sub_del_003", "created": 101}
    result2 = await handle_stripe_webhook(event2, repo, 7)
    assert result2["action"] == "subscription_deleted"
    assert result2["suspension_notice"] is False


async def test_reactivation_resets_notification_then_resuspend(repo, sample_org):
    """Riattivazione -> reset suspension_notified_at; una nuova cancellazione
    deve poter notificare di nuovo (niente 'notificato per sempre')."""
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
        "subscription_status": "active",
    })
    sub_del = {
        "id": "evt_sub_del_100",
        "created": 100,
        "type": "subscription.deleted",
        "data": {"object": {"id": "sub_test001", "customer": "cus_test001"}},
    }
    result1 = await handle_stripe_webhook(sub_del, repo, 7)
    assert result1["suspension_notice"] is True

    checkout = {
        "id": "evt_checkout_reactivate_100",
        "created": 101,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_reactivate_100",
                "mode": "subscription",
                "client_reference_id": str(sample_org["id"]),
                "customer": "cus_test001",
                "subscription": "sub_test002",
            }
        },
    }
    await _seed_checkout_intent(repo, sample_org, checkout)
    await handle_stripe_webhook(checkout, repo, 7)
    invoice_paid = {
        "id": "evt_invoice_reactivate_100",
        "created": 102,
        "type": "invoice.paid",
        "data": {"object": {
            "id": "in_reactivate_100",
            "customer": "cus_test001",
            "subscription": "sub_test002",
            "status": "paid",
            "period_start": datetime.now(timezone.utc).timestamp(),
            "period_end": datetime.now(timezone.utc).timestamp() + 2592000,
            "lines": {"data": [{"price": {"id": "price_test_starter"}}]},
        }},
    }
    await handle_stripe_webhook(invoice_paid, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "active"
    assert billing["suspension_notified_at"] is None

    sub_del2 = {
        **sub_del,
        "id": "evt_sub_del_101",
        "created": 103,
        "data": {"object": {"id": "sub_test002", "customer": "cus_test001"}},
    }
    result2 = await handle_stripe_webhook(sub_del2, repo, 7)
    assert result2["suspension_notice"] is True


async def test_handle_invoice_payment_failed_sets_past_due(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
        "subscription_status": "active",
    })
    event = {
        "id": "evt_pay_fail_001",
        "created": 100,
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": "in_fail_001",
                "customer": "cus_test001",
                "subscription": "sub_test001",
            }
        },
    }
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "past_due"


async def test_handle_unknown_event_does_nothing(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = {"id": "evt_unknown_001", "created": 100, "type": "unknown.event", "data": {"object": {}}}
    result = await handle_stripe_webhook(event, repo, 7)
    assert result is None


async def test_subscription_updated_same_period_does_not_reset_usage(repo, sample_org):
    """Fix C: subscription.updated con lo stesso current_period_start (es.
    cambio metodo pagamento, toggle cancel_at_period_end) non deve azzerare
    la quota messaggi gia' usata nel ciclo corrente."""
    from src.core.billing.webhook_handler import handle_stripe_webhook

    fixed_start = datetime.now(timezone.utc).replace(microsecond=0)
    fixed_end = fixed_start.replace(year=fixed_start.year + (1 if fixed_start.month == 12 else 0))

    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test_guard",
        "plan": "starter",
        "messages_limit": 500,
        "current_period_start": fixed_start,
        "current_period_end": fixed_end,
    })
    await repo.increment_message_usage(sample_org["id"])
    await repo.increment_message_usage(sample_org["id"])
    await repo.increment_message_usage(sample_org["id"])

    def make_event(evt_id, created):
        return {
            "id": evt_id,
            "created": created,
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_guard_001",
                    "customer": "cus_test_guard",
                    "status": "active",
                    "items": {"data": [{"price": {"id": "price_test_starter"}}]},
                    "current_period_start": fixed_start.timestamp(),
                    "current_period_end": fixed_end.timestamp(),
                }
            },
        }

    # Prima chiamata: stesso periodo di quello gia' salvato -> nessun reset.
    await handle_stripe_webhook(make_event("evt_guard_001", 100), repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["messages_used_this_period"] == 3

    # Seconda chiamata (evento diverso, stesso periodo) -> ancora nessun reset.
    await handle_stripe_webhook(make_event("evt_guard_002", 101), repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["messages_used_this_period"] == 3


async def test_webhook_dedup_uses_real_event_id_not_object_id(repo, sample_org):
    """Fix B: due eventi DIVERSI (evt id diversi) che referenziano lo stesso
    oggetto Stripe (stessa subscription) devono essere processati entrambi,
    non deduplicati per errore sull'id dell'oggetto annidato."""
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test_dedup",
        "subscription_status": "active",
    })

    event1 = {
        "id": "evt_dedup_AAA",
        "created": 100,
        "type": "invoice.payment_failed",
        "data": {"object": {"id": "in_same_object", "customer": "cus_test_dedup", "subscription": "sub_x"}},
    }
    result1 = await handle_stripe_webhook(event1, repo, 7)
    assert result1["action"] == "payment_failed"

    # Stesso oggetto annidato (stesso "in_same_object"), ma evento Stripe
    # diverso: deve essere processato di nuovo, non scartato come duplicato.
    event2 = {
        "id": "evt_dedup_BBB",
        "created": 101,
        "type": "invoice.payment_failed",
        "data": {"object": {"id": "in_same_object", "customer": "cus_test_dedup", "subscription": "sub_x"}},
    }
    result2 = await handle_stripe_webhook(event2, repo, 7)
    assert result2["action"] == "payment_failed"  # non "duplicate"

    # Vero retry dello STESSO evento -> quello si' va deduplicato.
    result3 = await handle_stripe_webhook(event2, repo, 7)
    assert result3["action"] == "duplicate"


async def test_stripe_atomic_rollback_on_failure(repo, sample_org):
    """Se un handler solleva eccezione a meta' transazione, l'evento NON
    deve essere registrato come processato (rollback completo)."""
    from src.core.billing.webhook_handler import handle_stripe_webhook

    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_atomic",
        "subscription_status": "active",
    })

    event = {
        "id": "evt_atomic_fail",
        "created": 100,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_atomic_fail",
                "client_reference_id": str(sample_org["id"]),
                "mode": "subscription",
                "customer": "cus_atomic",
                "subscription": None,  # Forzera' return None, nessuna write
            }
        },
    }

    # mode=subscription ma subscription=None -> ritorna None senza write DB
    result = await handle_stripe_webhook(event, repo, 7)
    assert result is None

    # L'evento NON deve essere stato registrato: se riproposto, deve
    # essere processato di nuovo (non "duplicate").
    result2 = await handle_stripe_webhook(event, repo, 7)
    assert result2 is None

    # Verifica che l'event_id non sia in processed_stripe_events
    row = await repo.pool.fetchrow(
        "SELECT 1 FROM processed_stripe_events WHERE event_id = $1",
        "evt_atomic_fail",
    )
    assert row is None


async def test_handle_checkout_completed_without_subscription(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = {
        "id": "evt_no_sub_001",
        "created": 100,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_no_sub_001",
                "client_reference_id": str(sample_org["id"]),
                "mode": "setup",
                "customer": "cus_test002",
                "subscription": None,
                "status": "complete",
            }
        },
    }
    result = await handle_stripe_webhook(event, repo, 7)
    assert result is None
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "incomplete"
