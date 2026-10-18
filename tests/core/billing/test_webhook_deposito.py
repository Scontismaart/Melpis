from datetime import date, time
import json
import uuid
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


async def test_webhook_payment_mode_updates_booking(repo, sample_org):
    b = await repo.create_booking(organization_id=sample_org["id"], nome_cliente="Mario",
        data=date(2026, 8, 1), ora=time(20, 0), coperti=4, richiede_deposito=True)
    await repo.update_booking_payment(sample_org["id"], b["id"], "pending")
    await repo.pool.execute("""
        UPDATE bookings SET deposit_amount_minor = 1000, deposit_currency = 'eur',
            deposit_session_id = 'cs_test_abc123'
        WHERE id = $1 AND organization_id = $2
    """, b["id"], sample_org["id"])
    event = {
        "id": "evt_deposit_001",
        "created": 100,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "mode": "payment",
                "metadata": {
                    "booking_id": str(b["id"]),
                    "organization_id": str(sample_org["id"]),
                },
                "id": "cs_test_abc123",
                "payment_status": "paid",
                "amount_total": 1000,
                "currency": "eur",
            }
        }
    }
    from src.core.billing.webhook_handler import handle_stripe_webhook
    result = await handle_stripe_webhook(event, repo, 7)
    assert result is not None
    updated = await repo.get_booking(sample_org["id"], b["id"])
    assert updated["payment_status"] == "paid"


async def test_webhook_payment_mode_fail_closed_without_org(repo, sample_org):
    """Metadata.organization_id assente: NESSUN update sul booking (fail-closed)."""
    b = await repo.create_booking(organization_id=sample_org["id"], nome_cliente="Mario",
        data=date(2026, 8, 1), ora=time(20, 0), coperti=4, richiede_deposito=True)
    await repo.update_booking_payment(sample_org["id"], b["id"], "pending")
    event = {
        "type": "checkout.session.completed",
        "id": "evt_noorg_001",
        "created": 100,
        "data": {
            "object": {
                "mode": "payment",
                "metadata": {"booking_id": str(b["id"])},
                "id": "cs_test_noorg",
            }
        }
    }
    from src.core.billing.webhook_handler import handle_stripe_webhook
    result = await handle_stripe_webhook(event, repo, 7)
    assert result is None
    updated = await repo.get_booking(sample_org["id"], b["id"])
    assert updated["payment_status"] == "pending"


async def test_webhook_payment_mode_fail_closed_malformed_org(repo, sample_org):
    """Metadata.organization_id non-UUID: NESSUN update sul booking (fail-closed)."""
    b = await repo.create_booking(organization_id=sample_org["id"], nome_cliente="Mario",
        data=date(2026, 8, 1), ora=time(20, 0), coperti=4, richiede_deposito=True)
    await repo.update_booking_payment(sample_org["id"], b["id"], "pending")
    event = {
        "type": "checkout.session.completed",
        "id": "evt_badorg_001",
        "created": 100,
        "data": {
            "object": {
                "mode": "payment",
                "metadata": {"booking_id": str(b["id"]), "organization_id": "not-a-uuid"},
                "id": "cs_test_badorg",
            }
        }
    }
    from src.core.billing.webhook_handler import handle_stripe_webhook
    result = await handle_stripe_webhook(event, repo, 7)
    assert result is None
    updated = await repo.get_booking(sample_org["id"], b["id"])
    assert updated["payment_status"] == "pending"


async def test_webhook_subscription_mode_unaffected(repo, sample_org):
    """Subscription events require and honor the durable checkout binding."""
    intent_id = uuid.uuid4()
    frozen_intent = {
        "checkout_intent_id": str(intent_id),
        "trial_end_unix": None,
        "stripe_customer_id": "cus_test_123",
    }
    await repo.pool.execute("""
        INSERT INTO billing_checkout_intents(
            id, organization_id, request_key, payload_hash, payload, expires_at
        ) VALUES ($1, $2, $3, $4, $5::jsonb, NOW() + interval '1 hour')
    """, intent_id, sample_org["id"], f"test-checkout-{intent_id}", "a" * 64,
        json.dumps(frozen_intent))
    event = {
        "id": "evt_subscription_001",
        "created": 100,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "mode": "subscription",
                "metadata": {
                    "checkout_intent_id": str(intent_id),
                    "trial_end_unix": "none",
                },
                "id": "cs_test_sub_123",
                "client_reference_id": str(sample_org["id"]),
                "subscription": "sub_test_123",
                "customer": "cus_test_123",
            }
        }
    }
    from src.core.billing.webhook_handler import handle_stripe_webhook
    result = await handle_stripe_webhook(event, repo, 7)
    # Subscription mode, no booking match — returns a subscription result
    assert result is not None
    assert result.get("action") == "subscription_created"
