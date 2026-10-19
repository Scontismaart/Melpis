import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.core.billing.routes import billing_webhook


def _request(body: bytes, signature: str) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/billing/webhook",
        "headers": [(b"stripe-signature", signature.encode("ascii"))],
        "app": SimpleNamespace(state=SimpleNamespace(repo=object(), billing_config=SimpleNamespace(stripe_trial_days=7))),
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _signature(body: bytes, secret: str, timestamp: int) -> str:
    signed_payload = str(timestamp).encode() + b"." + body
    digest = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


@pytest.mark.asyncio
async def test_bad_stripe_signature_is_rejected(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    body = b'{"id":"evt_test","type":"invoice.created","data":{"object":{}}}'
    timestamp = int(time.time())

    with pytest.raises(HTTPException) as exc:
        await billing_webhook(_request(body, f"t={timestamp},v1=invalid"))

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid signature"


@pytest.mark.asyncio
async def test_valid_signed_unsupported_event_is_accepted_and_ignored(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    body = json.dumps({"id": "evt_test", "type": "invoice.created", "data": {"object": {}}}, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    request = _request(body, _signature(body, "whsec_test", int(timestamp)))

    async def ignored(event, repo, trial_days):
        assert event["type"] == "invoice.created"
        assert repo is request.app.state.repo
        assert trial_days == 7
        return None

    monkeypatch.setattr("src.core.billing.routes.handle_stripe_webhook", ignored)
    assert await billing_webhook(request) == {"status": "ignored"}
