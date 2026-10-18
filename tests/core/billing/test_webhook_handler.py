import json
import uuid
import pytest
from datetime import datetime, timedelta, timezone

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


@pytest.fixture(autouse=True)
def _mapped_monthly_plan_price(monkeypatch):
    from src.core.billing import webhook_handler
    from src.core.billing.plans import PLANS

    test_price = "price_test_monthly"
    monkeypatch.setattr(PLANS["starter"], "stripe_price_id", test_price)
    monkeypatch.setitem(webhook_handler.PRICE_TO_PLAN, test_price, "starter")
    for slug in PLANS:
        monkeypatch.setitem(webhook_handler.PRODUCT_TO_PLAN, f"prod_{slug}", slug)


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


async def _seed_checkout_intent(repo, organization_id, event, trial_end_unix=None):
    intent_id = uuid.uuid4()
    session = event["data"]["object"]
    session_id = session["id"]
    session["metadata"] = {
        "checkout_intent_id": str(intent_id),
        "trial_end_unix": "none" if trial_end_unix is None else str(trial_end_unix),
    }
    payload = {
        "checkout_intent_id": str(intent_id),
        "trial_end_unix": trial_end_unix,
        "stripe_customer_id": session["customer"],
        "plan": "starter",
        "interval": "monthly",
        "price_id": "price_test_monthly",
    }
    async with repo.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO billing_checkout_intents(
                id, organization_id, request_key, payload_hash, payload,
                session_id, session_url, expires_at
            ) VALUES ($1, $2, $3, 'test', $4::jsonb, $5, $6, NOW() + interval '4 days')
        """, intent_id, organization_id, f"test-checkout-{intent_id}",
            json.dumps(payload), session_id, f"https://checkout.example.test/{session_id}")
    return intent_id


@pytest.fixture
def valid_invoice_paid_event():
    service_start = int(datetime.now(timezone.utc).timestamp())
    service_end = service_start + 2592000
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
                "amount_paid": 2900,
                "currency": "eur",
                "period_start": service_start - 2592000,
                "period_end": service_start,
                "lines": {
                    "data": [
                        {
                            "type": "subscription",
                            "amount": 2900,
                            "currency": "eur",
                            "subscription": "sub_test001",
                            "proration": False,
                            "quantity": 1,
                            "price": {
                                "id": "price_test_monthly",
                                "recurring": {"interval": "month", "interval_count": 1},
                            },
                            "period": {"start": service_start, "end": service_end},
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
    await _seed_checkout_intent(repo, sample_org["id"], event)
    result = await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["subscription_status"] == "trialing"
    assert billing["stripe_customer_id"] == "cus_test001"
    assert result["action"] == "subscription_created"


async def test_handle_checkout_completed_sets_trial_period(repo, sample_org, valid_checkout_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    event = valid_checkout_event
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    await _seed_checkout_intent(repo, sample_org["id"], event)
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["trial_start"] is not None
    assert billing["trial_end"] is not None


async def test_checkout_webhook_keeps_original_23_hour_trial_deadline(repo, sample_org, valid_checkout_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook

    original_deadline = datetime.now(timezone.utc) + timedelta(hours=23)
    expected_deadline = datetime.fromtimestamp(int(original_deadline.timestamp()), tz=timezone.utc)
    async with repo.pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET subscription_status = 'trialing', trial_end = $1 WHERE id = $2",
            original_deadline,
            sample_org["id"],
        )
    event = valid_checkout_event
    event["data"]["object"]["client_reference_id"] = str(sample_org["id"])
    await _seed_checkout_intent(
        repo, sample_org["id"], event, int(original_deadline.timestamp())
    )

    await handle_stripe_webhook(event, repo, 7)

    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["trial_end"] == expected_deadline


async def test_handle_invoice_paid_resets_usage(repo, sample_org, valid_invoice_paid_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
        "subscription_id": "sub_test001",
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
        "subscription_id": "sub_test001",
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


async def test_handle_invoice_paid_sets_period(repo, sample_org, valid_invoice_paid_event):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
        "subscription_id": "sub_test001",
    })
    event = valid_invoice_paid_event
    event["data"]["object"]["customer"] = "cus_test001"
    await handle_stripe_webhook(event, repo, 7)
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["current_period_start"] is not None
    assert billing["current_period_end"] is not None


async def test_subscription_product_fallback_cannot_upgrade_plan(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
        "subscription_id": "sub_test001",
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
                    "data": [{"price": {"id": ""}, "plan": {"product": "prod_pro"}}]
                },
                "current_period_start": datetime.now(timezone.utc).timestamp(),
                "current_period_end": datetime.now(timezone.utc).timestamp() + 2592000,
            }
        },
    }
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await handle_stripe_webhook(event, repo, 7)
    assert error.value.status_code == 503
    billing = await repo.get_organization_billing(sample_org["id"])
    assert billing["plan"] == "starter"
    assert billing["messages_limit"] == 500
    assert not await repo.pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM processed_stripe_events WHERE event_id = $1)", event["id"]
    )


async def test_handle_subscription_deleted_sets_canceled(repo, sample_org):
    from src.core.billing.webhook_handler import handle_stripe_webhook
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test001",
        "subscription_id": "sub_test001",
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
        "subscription_id": "sub_test001",
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
        "subscription_id": "sub_test001",
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
    reactivated_intent = await _seed_checkout_intent(repo, sample_org["id"], checkout)
    await handle_stripe_webhook(checkout, repo, 7)
    invoice_paid = {
        "id": "evt_invoice_reactivate_100",
        "created": 102,
        "type": "invoice.paid",
        "data": {"object": {
            "id": "in_reactivate_100",
            "subscription_details": {"metadata": {"checkout_intent_id": str(reactivated_intent)}},
            "customer": "cus_test001",
            "subscription": "sub_test002",
            "status": "paid",
            "amount_paid": 2900,
            "currency": "eur",
            "period_start": int(datetime.now(timezone.utc).timestamp()) - 2592000,
            "period_end": int(datetime.now(timezone.utc).timestamp()),
            "lines": {"data": [{
                "type": "subscription",
                "amount": 2900,
                "currency": "eur",
                "subscription": "sub_test002",
                "proration": False,
                "quantity": 1,
                "price": {
                    "id": "price_test_monthly",
                    "recurring": {"interval": "month", "interval_count": 1},
                },
                "period": {
                    "start": int(datetime.now(timezone.utc).timestamp()),
                    "end": int(datetime.now(timezone.utc).timestamp()) + 2592000,
                },
            }]},
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
        "subscription_id": "sub_test001",
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
    fixed_end = fixed_start + timedelta(days=30)

    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": "cus_test_guard",
        "subscription_id": "sub_guard_001",
        "plan": "starter",
        "subscription_status": "active",
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
                    "items": {"has_more": False, "data": [{"quantity": 1, "price": {
                        "id": "price_test_monthly", "currency": "eur", "unit_amount": 2900,
                        "recurring": {"interval": "month", "interval_count": 1}}}]},
                    "current_period_start": int(fixed_start.timestamp()),
                    "current_period_end": int(fixed_end.timestamp()),
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
        "subscription_id": "sub_x",
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
        "subscription_id": "sub_atomic",
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
