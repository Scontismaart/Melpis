import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Literal, get_args, get_origin
from urllib.parse import parse_qs

import pytest
import stripe

from src.core.billing.checkout import create_durable_checkout


class _AsyncContext:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _CheckoutConnection:
    def __init__(self, organization):
        self.organization = organization

    def transaction(self):
        return _AsyncContext()

    async def fetchrow(self, query, *args):
        if "FROM organizations" in query:
            return self.organization
        return None

    async def execute(self, query, *args):
        return "INSERT 0 1"


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


class _RecordingHTTPClient:
    """Capture Stripe's already-encoded form request and return a fake Session."""

    name = "recording-in-process"

    def __init__(self):
        self.requests = []

    def request_with_retries(
        self, method, url, headers, post_data=None, *, _usage=None, **kwargs
    ):
        self.requests.append((method, url, headers, post_data))
        return (
            json.dumps({
                "id": "cs_test_contract",
                "object": "checkout.session",
                "url": "https://checkout.example.test/session",
            }),
            200,
            {"Request-Id": "req_test_contract"},
        )


def _literal_values(annotation):
    while get_origin(annotation) is not Literal:
        args = get_args(annotation)
        assert args, f"Expected an annotated Stripe literal, got {annotation!r}"
        annotation = args[0]
    return set(get_args(annotation))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subscription_status", "trial_offset", "expected_collection"),
    [
        ("trialing", timedelta(days=7), "if_required"),
        ("active", timedelta(days=7), "always"),
        ("trialing", timedelta(hours=-1), "always"),
    ],
    ids=["active-trial", "nontrial-with-stale-deadline", "expired-trial"],
)
async def test_durable_checkout_serializes_supported_payment_collection(
    monkeypatch, subscription_status, trial_offset, expected_collection
):
    payment_collection_type = stripe.checkout.Session.CreateParams.__annotations__[
        "payment_method_collection"
    ]
    assert _literal_values(payment_collection_type) == {"always", "if_required"}

    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    trial_end = now + trial_offset
    org_id = uuid.uuid4()
    connection = _CheckoutConnection({
        "id": org_id,
        "stripe_customer_id": "cus_test_contract",
        "subscription_status": subscription_status,
        "trial_end": trial_end,
        "subscription_id": None,
    })
    http_client = _RecordingHTTPClient()
    monkeypatch.setattr(stripe, "api_key", "sk_test_contract_no_network")
    monkeypatch.setattr(stripe, "default_http_client", http_client)

    async def stripe_call(method, **kwargs):
        return await asyncio.to_thread(method, **kwargs)

    result = await create_durable_checkout(
        SimpleNamespace(pool=_Pool(connection)),
        org_id,
        "checkout-sdk-contract-001",
        {
            "price_id": "price_test_contract",
            "plan": "starter",
            "interval": "monthly",
            "success_url": "https://app.example.test/success",
            "cancel_url": "https://app.example.test/cancel",
        },
        stripe,
        stripe_call,
        now_provider=lambda: now,
    )

    assert result["url"] == "https://checkout.example.test/session"
    assert len(http_client.requests) == 1
    method, url, headers, body = http_client.requests[0]
    assert method == "post"
    assert url.endswith("/v1/checkout/sessions")
    assert headers["Stripe-Version"] == "2024-06-20"

    params = parse_qs(body)
    assert params["payment_method_collection"] == [expected_collection]
    if expected_collection == "if_required":
        assert params["subscription_data[trial_end]"] == [str(int(trial_end.timestamp()))]
        assert params[
            "subscription_data[trial_settings][end_behavior][missing_payment_method]"
        ] == ["cancel"]
    else:
        assert "subscription_data[trial_end]" not in params
        assert "subscription_data[trial_settings][end_behavior][missing_payment_method]" not in params
