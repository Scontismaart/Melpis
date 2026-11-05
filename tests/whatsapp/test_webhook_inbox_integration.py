import hashlib
import hmac
import json
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.workers.webhook_inbox_worker import WebhookInboxWorker
from src.whatsapp.router import create_router


pytestmark = pytest.mark.asyncio


def _payload(phone_number_id: str):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "waba-inbox-test",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "39000000000",
                        "phone_number_id": phone_number_id,
                    },
                    "contacts": [{
                        "profile": {"name": "Inbox Test"},
                        "wa_id": "39000000001",
                    }],
                    "messages": [{
                        "from": "39000000001",
                        "id": "wamid.durable.inbox.1",
                        "timestamp": "1712345678",
                        "type": "text",
                        "text": {"body": "Ciao"},
                    }],
                },
            }],
        }],
    }


@pytest.mark.parametrize("booking_worker_enabled", [False, True])
async def test_ack_commits_inbox_before_worker_creates_ai_queue(pg_pool, repo, app_config, monkeypatch, booking_worker_enabled):
    if booking_worker_enabled:
        monkeypatch.setenv("C24_PRIVATE_BOOKING_WORKER_ENABLED", "true")
    else:
        monkeypatch.delenv("C24_PRIVATE_BOOKING_WORKER_ENABLED", raising=False)
    org_id = uuid.uuid4()
    phone_number_id = "987654321001"
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO organizations (id, name) VALUES ($1, 'Inbox Test Org')",
            org_id,
        )
        await conn.execute(
            """INSERT INTO whatsapp_accounts
               (id, organization_id, phone_number_id, waba_id, access_token)
               VALUES ($1, $2, $3, 'waba-inbox-test', 'encrypted-test-token')""",
            uuid.uuid4(), org_id, phone_number_id,
        )

    app = FastAPI()
    app.include_router(create_router(app_config, repo))
    payload = _payload(phone_number_id)
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(
        app_config.app_secret.encode(), body, hashlib.sha256
    ).hexdigest()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
        )

    assert response.status_code == 200
    async with pg_pool.acquire() as conn:
        inbox = await conn.fetchrow(
            "SELECT * FROM meta_webhook_inbox WHERE organization_id = $1",
            org_id,
        )
        queued_messages = await conn.fetchval(
            "SELECT COUNT(*) FROM messages WHERE organization_id = $1", org_id
        )
    assert inbox is not None
    assert inbox["status"] == "pending"
    assert inbox["channel"] == "whatsapp"
    assert queued_messages == 0

    assert await WebhookInboxWorker(repo).process_next_batch() == 1

    async with pg_pool.acquire() as conn:
        inbox_status = await conn.fetchval(
            "SELECT status FROM meta_webhook_inbox WHERE id = $1", inbox["id"]
        )
        message = await conn.fetchrow(
            "SELECT * FROM messages WHERE organization_id = $1", org_id
        )
        receipt = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM inbound_worker_receipts
            WHERE organization_id=$1 AND inbox_id=$2 AND message_id=$3)""",
            org_id, inbox["id"], message["id"],
        )
    assert inbox_status == "completed"
    assert message["wam_id"] == "wamid.durable.inbox.1"
    assert message["status"] == "received_pending_ai"
    assert receipt is booking_worker_enabled
