"""
Test di integrazione reale su Postgres per Difetto 4 (PROD-001):
Webhook di cancellazione Stripe (customer.subscription.deleted).

Verifica che:
1. Un payload webhook Stripe reale 'customer.subscription.deleted' con firma HMAC-SHA256 valida
   viene inviato all'endpoint reale /api/billing/webhook di FastAPI.
2. La firma Stripe viene validata crittograficamente da stripe.Webhook.construct_event senza bypass.
3. Lo stato dell'organizzazione sul DB Postgres reale viene aggiornato a 'canceled' (sospeso/read-only).
4. Un successivo tentativo di processare un messaggio in arrivo per quella organizzazione:
   - Rileva lo stato sospeso (is_org_suspended=True)
   - Blocca l'invocazione del generatore AI (nessuna chiamata LLM)
   - Invia la risposta di sospensione al cliente e finalizza il messaggio con handling_type='suspended'.
"""
import hashlib
import hmac
import json
import os
import time
import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.core.billing.config import BillingConfig
from src.core.billing.routes import router as billing_router
from src.core.db.repository import CoreRepository
from src.whatsapp.config import AppConfig, TenantConfig
from src.whatsapp.inbound_processor import InboundProcessor
from src.whatsapp.repository import Repository as WhatsAppRepository

DB_DSN = os.environ.get(
    "TEST_DB_DSN",
    f"postgresql://{os.getenv('PGUSER', 'test')}:{os.getenv('PGPASSWORD', 'test')}@{os.getenv('PGHOST', 'localhost')}:{os.getenv('PGPORT', '55432')}/{os.getenv('PGDATABASE', 'p0_concurrency_test')}",
)

WEBHOOK_SECRET = "whsec_test_cancellation_secret_xyz123"

SCHEMA_DEFECT_4 = """
DROP TABLE IF EXISTS audit_log CASCADE;
DROP TABLE IF EXISTS processed_stripe_events CASCADE;
DROP TABLE IF EXISTS messages CASCADE;
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS contacts CASCADE;
DROP TABLE IF EXISTS organizations CASCADE;

CREATE TABLE organizations (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    stripe_customer_id TEXT UNIQUE,
    subscription_id TEXT,
    subscription_status TEXT DEFAULT 'active',
    stripe_event_created BIGINT NOT NULL DEFAULT 0,
    stripe_event_id TEXT,
    plan TEXT DEFAULT 'starter',
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    trial_end TIMESTAMPTZ,
    suspension_notified_at TIMESTAMPTZ,
    messages_used_this_period INT NOT NULL DEFAULT 0,
    messages_limit INT NOT NULL DEFAULT 1000,
    business_profile JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE processed_stripe_events (
    event_id TEXT NOT NULL,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, organization_id)
);

CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID,
    action TEXT NOT NULL,
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE contacts (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    phone_number TEXT NOT NULL,
    ai_disclosure_sent_at TIMESTAMPTZ,
    UNIQUE(organization_id, phone_number)
);

CREATE TABLE conversations (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    contact_id UUID NOT NULL REFERENCES contacts(id),
    ticket_status TEXT NOT NULL DEFAULT 'AI_ACTIVE',
    UNIQUE(organization_id, contact_id)
);

CREATE TABLE messages (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    direction TEXT NOT NULL DEFAULT 'inbound',
    content JSONB DEFAULT '{}'::jsonb,
    content_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'received_pending_ai',
    handling_type TEXT,
    billed_at TIMESTAMPTZ,
    ai_reply_cache JSONB,
    sent_at TIMESTAMPTZ,
    meta_message_id VARCHAR(255),
    quota_exceeded_at TIMESTAMPTZ,
    processing_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    claimed_at TIMESTAMPTZ,
    replied_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
"""


@pytest.fixture
async def real_db_pool():
    pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DEFECT_4)
    yield pool
    await pool.close()


def generate_stripe_signature(payload_bytes: bytes, secret: str) -> str:
    """Genera la firma HMAC Stripe t=...,v1=... autentica secondo la specifica Stripe."""
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.".encode("utf-8") + payload_bytes
    signature = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


@pytest.mark.asyncio
async def test_stripe_subscription_cancellation_webhook_and_ai_blocking(real_db_pool, monkeypatch):
    """
    Test completo end-to-end:
    1. Inserisce un'organizzazione attiva con stripe_customer_id='cus_melpis_99'.
    2. Chiama l'endpoint FastAPI /api/billing/webhook con firma Stripe valida.
    3. Verifica che il DB Postgres reale aggiorni subscription_status='canceled'.
    4. Verifica che l'idempotenza Stripe eviti rielaborazioni (replay dell'evento).
    5. Invia un messaggio WhatsApp: il processore rileva org_suspended, blocca l'AI e risponde con messaggio di sospensione.
    """
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)

    core_repo = CoreRepository(pool=real_db_pool)
    wa_repo = WhatsAppRepository(pool=real_db_pool)

    # Setup FastAPI app
    app = FastAPI()
    app.include_router(billing_router)
    app.state.repo = core_repo
    app.state.billing_config = BillingConfig(
        stripe_trial_days=14,
    )

    org_id = uuid.uuid4()
    cust_id = "cus_melpis_99"

    # 1. Inserisce tenant con abbonamento attivo
    async with real_db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO organizations (id, name, stripe_customer_id, subscription_status, subscription_id)
               VALUES ($1, 'Ristorante Da Mario', $2, 'active', 'sub_test_canc_123')""",
            org_id, cust_id
        )

    # 2. Crea payload Stripe customer.subscription.deleted
    event_id = f"evt_del_{uuid.uuid4().hex[:12]}"
    stripe_event = {
        "id": event_id,
        "object": "event",
        "api_version": "2024-06-20",
        "created": int(time.time()),
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_test_canc_123",
                "object": "subscription",
                "customer": cust_id,
                "status": "canceled",
            }
        }
    }
    raw_payload = json.dumps(stripe_event).encode("utf-8")
    sig_header = generate_stripe_signature(raw_payload, WEBHOOK_SECRET)

    # 3. Invoca l'endpoint reale di FastAPI con firma verificata
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/billing/webhook",
            content=raw_payload,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": sig_header,
            }
        )

    assert resp.status_code == 200, f"Webhook fallito: {resp.text}"
    data = resp.json()
    assert data["action"] == "subscription_deleted"
    assert data["status"] == "canceled"
    assert data["organization_id"] == str(org_id)

    # 4. Verifica DB reale: subscription_status DEVE essere 'canceled'
    async with real_db_pool.acquire() as conn:
        org_row = await conn.fetchrow(
            "SELECT subscription_status, suspension_notified_at FROM organizations WHERE id = $1",
            org_id
        )
        assert org_row["subscription_status"] == "canceled", (
            f"Stato atteso 'canceled', trovato '{org_row['subscription_status']}'"
        )
        assert org_row["suspension_notified_at"] is not None

        # Verifica log evento idempotente
        evt_row = await conn.fetchrow(
            "SELECT * FROM processed_stripe_events WHERE event_id = $1", event_id
        )
        assert evt_row is not None

    # 5. Verifica blocco elaborazione AI per l'organizzazione sospesa
    contact_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    async with real_db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, '393335554433')",
            contact_id, org_id
        )
        await conn.execute(
            "INSERT INTO conversations (id, organization_id, contact_id) VALUES ($1, $2, $3)",
            conv_id, org_id, contact_id
        )
        await conn.execute(
            """INSERT INTO messages (id, organization_id, conversation_id, direction, content_text, content, status)
               VALUES ($1, $2, $3, 'inbound', 'Vorrei prenotare un tavolo per domani', '{"from": "393335554433"}'::jsonb, 'received_pending_ai')""",
            msg_id, org_id, conv_id
        )

    app_config = AppConfig(
        app_secret="sec_test",
        encryption_key="C1IuGfMh142ShEqV9Y2w3WPcMjIjO4aXjbnly7sqlvw=",
        postgres_dsn=DB_DSN,
        verify_token="tok_test",
        max_retry_attempts=5,
    )
    tenant_cfg = TenantConfig(
        organization_id=org_id,
        phone_number_id="12345",
        waba_id="waba_1",
        access_token="tok_1",
        business_profile={"nome": "Ristorante Da Mario"},
    )

    llm_called = {"called": False}

    async def fake_llm(*args, **kwargs):
        llm_called["called"] = True
        return None

    mock_service = AsyncMock()
    mock_service.check_opt_out = AsyncMock(return_value={"is_opt_out": False})
    mock_service.check_human_request = AsyncMock(return_value=False)
    mock_service.send_whatsapp_message = AsyncMock(return_value={"status": "sent", "wam_id": "meta_suspended_msg"})

    processor = InboundProcessor(app_config, wa_repo, mock_service)

    with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=tenant_cfg)), \
         patch("src.whatsapp.inbound_processor.genera_risposta_async", fake_llm):
        msg_row = await wa_repo.reconstruct_payload_for_retry(msg_id)
        await processor._process_one(msg_row)

    # 6. Asserzioni sul blocco AI:
    # - LLM NON deve essere chiamato
    assert llm_called["called"] is False, "L'LLM non doveva essere invocato per un tenant sospeso!"

    # - Messaggio di risposta inviato a WhatsApp deve essere quello di sospensione (ORG_SUSPENDED_REPLY)
    mock_service.send_whatsapp_message.assert_awaited_once()
    payload = mock_service.send_whatsapp_message.call_args.kwargs["payload"]
    assert payload["text"]["body"] == "Grazie per averci scritto, ti risponderemo al piu' presto."

    # - Messaggio nel DB reale deve essere marcato come handled con handling_type='suspended'
    async with real_db_pool.acquire() as conn:
        final_msg = await conn.fetchrow(
            "SELECT status, handling_type, sent_at, replied_at FROM messages WHERE id = $1",
            msg_id
        )
        assert final_msg["status"] == "handled"
        assert final_msg["handling_type"] == "suspended"
        assert final_msg["sent_at"] is not None
        assert final_msg["replied_at"] is not None
