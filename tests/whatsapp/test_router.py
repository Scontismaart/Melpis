import asyncpg
import hashlib
import hmac
import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI
from src.whatsapp.router import create_router
from src.whatsapp.config import AppConfig


@pytest.fixture
def app_config():
    return AppConfig(
        app_secret="test_app_secret",
        encryption_key="dGVzdC1rZXktMTIzNDU2Nzg5MDEyMzQ1Njc4OTA=",
        postgres_dsn="",
        verify_token="my_verify_token",
    )


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.get_org_by_phone_number_id = AsyncMock(return_value={
        "organization_id": uuid.uuid4(),
        "phone_number_id": "1234567890",
        "waba_id": "waba_1",
        "access_token": "encrypted_token",
        "name": "Test Org",
        "business_profile": {},
    })
    repo.get_org_by_waba_id = AsyncMock(return_value={
        "organization_id": uuid.uuid4(),
        "name": "Test Org",
    })
    repo.get_orgs_by_waba_id = AsyncMock(return_value=[{
        "organization_id": uuid.uuid4(),
        "name": "Test Org",
    }])
    repo.update_template_status = AsyncMock()
    repo.update_message_status = AsyncMock(return_value={"status": "delivered"})
    repo.update_message_status_by_wam_id = AsyncMock(return_value={"status": "delivered"})
    repo.upsert_message = AsyncMock(return_value={"id": uuid.uuid4(), "status": "received_pending_ai"})
    repo.get_or_create_contact = AsyncMock(return_value={"id": uuid.uuid4()})
    repo.get_or_create_conversation = AsyncMock(return_value={"id": uuid.uuid4()})
    repo.increment_message_usage = AsyncMock()
    # Pool mockato: fetchrow ritorna una riga (idempotenza passa come nuovo)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"wam_id": "test"})
    # acquire + transaction async context manager per _handle_inbound_message
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(return_value="INSERT 0 1")
    # GDPR ingress filtering checks the erased-subject barrier before the
    # durable inbox insert. The empty result models no barrier for these tests.
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchval = AsyncMock(return_value=False)
    mock_conn.transaction.return_value.__aenter__ = AsyncMock()
    mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    repo.pool = pool
    return repo


@pytest.fixture
def app(app_config, mock_repo):
    app = FastAPI()
    router = create_router(app_config, mock_repo)
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _sign_body(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestRouter:
    def test_get_verify_success(self, client):
        resp = client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=my_verify_token&hub.challenge=123456789")
        assert resp.status_code == 200
        assert resp.text == "123456789"

    def test_get_verify_fail(self, client):
        resp = client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong_token&hub.challenge=123456789")
        assert resp.status_code == 403

    def test_post_status_update(self, client, app_config):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "391234567890",
                            "phone_number_id": "1234567890",
                        },
                        "statuses": [{
                            "id": "wamid.test",
                            "status": "delivered",
                            "timestamp": "1712345678",
                            "recipient_id": "391234567890",
                        }],
                    },
                }],
            }],
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        resp = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
        assert resp.status_code == 200

    def test_post_status_update_bad_signature(self, client, app_config):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{"field": "messages", "value": {"metadata": {"phone_number_id": "1234567890"}, "statuses": [{"id": "wamid.test", "status": "delivered", "timestamp": "1712345678", "recipient_id": "391234567890"}]}}],
            }],
        }
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=invalid",
            },
        )
        assert resp.status_code == 403

    def test_post_inbound_message(self, client, app_config):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "391234567890",
                            "phone_number_id": "1234567890",
                        },
                        "contacts": [{
                            "profile": {"name": "Mario"},
                            "wa_id": "391234567890",
                        }],
                        "messages": [{
                            "from": "391234567890",
                            "id": "wamid.inbound.test",
                            "timestamp": "1712345678",
                            "type": "text",
                            "text": {"body": "Ciao"},
                        }],
                    },
                }],
            }],
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        resp = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
        assert resp.status_code == 200

    def test_post_payload_oversize(self, client, app_config):
        body = b"A" * (6 * 1024 * 1024)
        resp = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=anything",
            },
        )
        assert resp.status_code == 413

    def test_post_hmac_rejection_log(self, client, app_config, caplog):
        caplog.set_level("WARNING")
        payload = {"object": "whatsapp_business_account", "entry": []}
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=invalid",
            },
        )
        assert resp.status_code == 403
        assert any("webhook_hmac_rejected" in r.message for r in caplog.records)

    def test_post_timestamp_replay(self, client, app_config):
        payload = {"object": "whatsapp_business_account", "entry": []}
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        past_ts = 1000000  # molto vecchio
        resp = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-Timestamp": str(past_ts),
            },
        )
        assert resp.status_code == 403

    def test_post_timestamp_invalid(self, client, app_config):
        payload = {"object": "whatsapp_business_account", "entry": []}
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        resp = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-Timestamp": "not-a-number",
            },
        )
        assert resp.status_code == 400

    def test_post_status_non_uuid_callback_fallback(self, client, app_config, mock_repo, caplog):
        caplog.set_level("WARNING")
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"display_phone_number": "391234567890", "phone_number_id": "1234567890"},
                        "statuses": [
                            {"id": "wamid.bad.callback", "status": "delivered", "timestamp": "1712345678", "recipient_id": "391234567890", "biz_opaque_callback_data": "not-a-uuid"},
                            {"id": "wamid.good.callback", "status": "read", "timestamp": "1712345679", "recipient_id": "391234567890", "biz_opaque_callback_data": str(uuid.uuid4())},
                        ],
                    },
                }],
            }],
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        resp = client.post("/webhooks/whatsapp", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        assert resp.status_code == 200
        # ACK path only authenticates, resolves tenant and persists the raw
        # event. Status validation/reconciliation belongs to the inbox worker.
        mock_repo.update_message_status_by_wam_id.assert_not_awaited()
        mock_repo.update_message_status.assert_not_awaited()
        conn = mock_repo.pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.await_count == 1
        assert conn.execute.await_args.args[3] == "whatsapp"

    def test_post_status_idempotent_skip(self, client, app_config, mock_repo, monkeypatch):
        call_count = [0]
        async def mock_dedup(*a, **kw):
            call_count[0] += 1
            return call_count[0] == 1
        monkeypatch.setattr("src.whatsapp.router.dedup_check", mock_dedup)
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"display_phone_number": "391234567890", "phone_number_id": "1234567890"},
                        "statuses": [{"id": "wamid.dup", "status": "delivered", "timestamp": "1712345678", "recipient_id": "391234567890"}],
                    },
                }],
            }],
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        headers = {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
        resp1 = client.post("/webhooks/whatsapp", content=body, headers=headers)
        assert resp1.status_code == 200
        resp2 = client.post("/webhooks/whatsapp", content=body, headers=headers)
        assert resp2.status_code == 200
        conn = mock_repo.pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.await_count == 2
        mock_repo.update_message_status_by_wam_id.assert_not_awaited()

    def test_post_inbound_idempotent_skip(self, client, app_config, mock_repo, monkeypatch):
        call_count = [0]
        async def mock_dedup(*a, **kw):
            call_count[0] += 1
            return call_count[0] == 1
        monkeypatch.setattr("src.whatsapp.router.dedup_check", mock_dedup)
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"display_phone_number": "391234567890", "phone_number_id": "1234567890"},
                        "contacts": [{"profile": {"name": "Mario"}, "wa_id": "391234567890"}],
                        "messages": [{"from": "391234567890", "id": "wamid.inbound.dup", "timestamp": "1712345678", "type": "text", "text": {"body": "Ciao"}}],
                    },
                }],
            }],
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        headers = {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
        resp1 = client.post("/webhooks/whatsapp", content=body, headers=headers)
        assert resp1.status_code == 200
        resp2 = client.post("/webhooks/whatsapp", content=body, headers=headers)
        assert resp2.status_code == 200
        conn = mock_repo.pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.await_count == 2
        mock_repo.upsert_message.assert_not_awaited()

    def test_post_batch_db_down_500(self, app, app_config, mock_repo):
        from starlette.testclient import TestClient
        conn = mock_repo.pool.acquire.return_value.__aenter__.return_value
        conn.execute.side_effect = asyncpg.InsufficientResourcesError("connection pool exhausted")
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"display_phone_number": "391234567890", "phone_number_id": "1234567890"},
                        "statuses": [{"id": "wamid.test", "status": "delivered", "timestamp": "1712345678", "recipient_id": "391234567890"}],
                    },
                }],
            }],
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/webhooks/whatsapp", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        assert resp.status_code == 500

    def test_post_contacts_empty_log(self, client, app_config, mock_repo, caplog):
        caplog.set_level("WARNING")
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"display_phone_number": "391234567890", "phone_number_id": "1234567890"},
                        "contacts": [],
                        "messages": [{"from": "391234567890", "id": "wamid.inbound.nocontact", "timestamp": "1712345678", "type": "text", "text": {"body": "Ciao"}}],
                    },
                }],
            }],
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        resp = client.post("/webhooks/whatsapp", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        assert resp.status_code == 200
        # Contacts are interpreted only after durable acceptance.
        assert not any("event=contacts_empty" in r.message for r in caplog.records)
        mock_repo.upsert_message.assert_not_awaited()
        conn = mock_repo.pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.await_count == 1

    def test_post_status_sequence_dedup_correct(self, client, app_config, mock_repo):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"display_phone_number": "391234567890", "phone_number_id": "1234567890"},
                        "statuses": [
                            {"id": "wamid.seq", "status": "sent", "timestamp": "1712345676", "recipient_id": "391234567890"},
                            {"id": "wamid.seq", "status": "delivered", "timestamp": "1712345677", "recipient_id": "391234567890"},
                            {"id": "wamid.seq", "status": "read", "timestamp": "1712345678", "recipient_id": "391234567890"},
                        ],
                    },
                }],
            }],
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        resp = client.post("/webhooks/whatsapp", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        assert resp.status_code == 200
        mock_repo.update_message_status_by_wam_id.assert_not_awaited()
        conn = mock_repo.pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.await_count == 1

    def test_post_batch_mixed_valid_invalid(self, client, app_config, mock_repo, caplog):
        caplog.set_level("WARNING")
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"display_phone_number": "391234567890", "phone_number_id": "1234567890"},
                        "statuses": [{"id": "wamid.bad", "status": "delivered", "timestamp": "1712345678", "recipient_id": "391234567890", "biz_opaque_callback_data": "not-a-uuid"}],
                        "messages": [{"from": "391234567890", "id": "wamid.good", "timestamp": "1712345678", "type": "text", "text": {"body": "Prenoto"}}],
                    },
                }],
            }],
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        resp = client.post("/webhooks/whatsapp", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        assert resp.status_code == 200
        assert not any("webhook_invalid_callback_data" in r.message for r in caplog.records)
        mock_repo.upsert_message.assert_not_awaited()
        conn = mock_repo.pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.await_count == 1

    def test_post_template_status(self, client, app_config):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "field": "message_template_status_update",
                    "value": {
                        "messaging_product": "whatsapp",
                        "message_template_id": 12345,
                        "message_template_name": "promo_welcome",
                        "message_template_language": "it",
                        "message_template_status": "APPROVED",
                        "event": "UPDATE",
                    },
                }],
            }],
        }
        body = json.dumps(payload).encode()
        sig = _sign_body(body, app_config.app_secret)
        resp = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
        assert resp.status_code == 200
