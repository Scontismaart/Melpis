"""Real Stripe-signature route through billing persistence on the dedicated fixture DB."""

import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from starlette.requests import Request


def _dedicated_b04_dsn_configured() -> bool:
    raw = os.getenv("TEST_DB_DSN", "")
    if os.getenv("CI", "").lower() == "true":
        # GitHub Actions provides an isolated postgres service fixture. Require
        # both platform markers and its exact non-production DSN; never accept
        # an arbitrary CI/staging/prod connection string here.
        return (
            os.getenv("GITHUB_ACTIONS", "").lower() == "true"
            and raw == "postgresql://postgres:test@localhost:5432/test"
        )
    try:
        parsed = urlsplit(raw)
        return (
            parsed.scheme in {"postgres", "postgresql"}
            and parsed.hostname == "127.0.0.1"
            and parsed.port == 55495
            and parsed.path == "/melpis_c04_offline_20261025"
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("reset_db"),
    pytest.mark.skipif(
        not _dedicated_b04_dsn_configured(),
        reason="requires TEST_DB_DSN=127.0.0.1:55495/melpis_c04_offline_20261025",
    ),
]


def _paid_invoice_event(*, customer: str, subscription: str, price_id: str) -> dict:
    period_start = 1_800_000_000
    period_end = period_start + 30 * 24 * 60 * 60
    return {
        "id": "evt_b04_signed_paid_invoice",
        "created": 1_800_000_100,
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_b04_signed_paid_invoice",
                "customer": customer,
                "subscription": subscription,
                "status": "paid",
                "amount_paid": 2900,
                "currency": "eur",
                "period_start": period_start - 30 * 24 * 60 * 60,
                "period_end": period_start,
                "lines": {
                    "has_more": False,
                    "data": [
                        {
                            "type": "subscription",
                            "amount": 2900,
                            "currency": "eur",
                            "subscription": subscription,
                            "proration": False,
                            "quantity": 1,
                            "price": {
                                "id": price_id,
                                "recurring": {"interval": "month", "interval_count": 1},
                            },
                            "period": {"start": period_start, "end": period_end},
                        }
                    ],
                },
            }
        },
    }


def _signed_request(body: bytes, secret: str, repo) -> Request:
    timestamp = int(time.time())
    digest = hmac.new(
        secret.encode("utf-8"), str(timestamp).encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    signature = f"t={timestamp},v1={digest}"
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/billing/webhook",
        "headers": [(b"stripe-signature", signature.encode("ascii"))],
        "app": SimpleNamespace(
            state=SimpleNamespace(
                repo=repo,
                billing_config=SimpleNamespace(stripe_trial_days=7),
            )
        ),
    }
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


async def test_signed_paid_invoice_activates_one_tenant_and_duplicate_is_idempotent(
    monkeypatch, repo, sample_org,
):
    from src.core.billing.plans import PLANS
    from src.core.billing.routes import billing_webhook
    from src.core.billing.webhook_handler import PRICE_TO_PLAN

    secret = "whsec_b04_offline_synthetic"
    price_id = "price_b04_offline_starter"
    customer = "cus_b04_offline"
    subscription = "sub_b04_offline"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(PLANS["starter"], "stripe_price_id", price_id)
    monkeypatch.setitem(PRICE_TO_PLAN, price_id, "starter")
    await repo.update_organization_billing(sample_org["id"], {
        "stripe_customer_id": customer,
        "subscription_id": subscription,
        "subscription_status": "trialing",
        "plan": "pro",
        "messages_limit": 2000,
        "messages_used_this_period": 19,
    })

    event = _paid_invoice_event(customer=customer, subscription=subscription, price_id=price_id)
    body = json.dumps(event, separators=(",", ":")).encode("utf-8")

    result = await billing_webhook(_signed_request(body, secret, repo))
    billing = await repo.get_organization_billing(sample_org["id"])

    assert result["action"] == "subscription_activated"
    assert str(result["organization_id"]) == str(sample_org["id"])
    assert billing["plan"] == "starter"
    assert billing["subscription_status"] == "active"
    assert billing["messages_limit"] == 500
    assert billing["messages_used_this_period"] == 0

    duplicate = await billing_webhook(_signed_request(body, secret, repo))
    after_duplicate = await repo.get_organization_billing(sample_org["id"])
    assert duplicate["action"] == "duplicate"
    assert after_duplicate["messages_used_this_period"] == 0
