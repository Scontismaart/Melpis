"""
Test reale su Postgres (senza mock del DB) per Difetto 2:
Bug nella cache AI (perdita metadati / richiede_umano).

Verifica che:
1. La colonna ai_reply_cache e' JSONB e persiste l'oggetto {"text": ..., "richiede_umano": true, ...}.
2. Se un worker crasha dopo aver generato la risposta con richiede_umano=true ma prima dell'invio/escalation:
3. Il worker successivo (retry) legge la cache JSONB dal DB reale, preserva richiede_umano=True,
   non richiama l'LLM, instrada all'operatore umano (escalate_to_human -> ticket_status='PENDING_STAFF'),
   invia la risposta a Meta e finalizza il messaggio come 'escalated'.
"""
import os
import uuid
import json
import pytest
import asyncpg
from unittest.mock import AsyncMock, patch

from src.whatsapp.repository import Repository
from src.whatsapp.inbound_processor import InboundProcessor
from src.whatsapp.config import AppConfig, TenantConfig
from src.models.schemas import RispostaOutput

DB_DSN = os.environ.get(
    "TEST_DB_DSN",
    f"postgresql://{os.getenv('PGUSER', 'test')}:{os.getenv('PGPASSWORD', 'test')}@{os.getenv('PGHOST', 'localhost')}:{os.getenv('PGPORT', '55432')}/{os.getenv('PGDATABASE', 'p0_concurrency_test')}",
)

SCHEMA_DEFECT_2 = """
DROP TABLE IF EXISTS bookings CASCADE;
DROP TABLE IF EXISTS messages CASCADE;
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS contacts CASCADE;
DROP TABLE IF EXISTS user_profiles CASCADE;
DROP TABLE IF EXISTS event_log CASCADE;
DROP TABLE IF EXISTS organizations CASCADE;
DROP TABLE IF EXISTS usage_events CASCADE;
DROP TABLE IF EXISTS governance_outbox CASCADE;
DROP TABLE IF EXISTS outbound_dedup CASCADE;

CREATE TABLE organizations (
    id UUID PRIMARY KEY,
    name TEXT DEFAULT 'Test Org',
    subscription_status TEXT DEFAULT 'trialing',
    subscription_id TEXT,
    trial_start TIMESTAMPTZ DEFAULT now() - interval '1 day',
    trial_end TIMESTAMPTZ DEFAULT now() + interval '7 days',
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    messages_used_this_period INT NOT NULL DEFAULT 0,
    messages_limit INT NOT NULL DEFAULT 1000,
    ai_accounting_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    sla_minutes INT NOT NULL DEFAULT 15,
    business_profile JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY,
    nome TEXT,
    email TEXT
);

CREATE TABLE IF NOT EXISTS event_log (
    id UUID PRIMARY KEY,
    organization_id UUID,
    priorita TEXT,
    dettagli JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE bookings (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    source_message_id VARCHAR(255)
);

CREATE TABLE contacts (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    phone_number TEXT NOT NULL,
    ai_disclosure_sent_at TIMESTAMPTZ,
    consent_status TEXT DEFAULT 'granted',
    updated_at TIMESTAMPTZ DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    UNIQUE(organization_id, phone_number)
);

CREATE TABLE conversations (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    contact_id UUID NOT NULL REFERENCES contacts(id),
    canale TEXT NOT NULL DEFAULT 'whatsapp',
    ticket_status TEXT NOT NULL DEFAULT 'AI_ACTIVE',
    pending_staff_at TIMESTAMPTZ,
    claimed_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    assigned_to UUID,
    version INT NOT NULL DEFAULT 1,
    last_message_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    UNIQUE(organization_id, contact_id)
);

CREATE TABLE messages (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    direction TEXT NOT NULL DEFAULT 'inbound',
    message_type TEXT NOT NULL DEFAULT 'text',
    content JSONB DEFAULT '{}'::jsonb,
    content_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'received_pending_ai',
    handling_type TEXT,
    billed_at TIMESTAMPTZ,
    ai_reply_cache JSONB,
    ai_reply_generated_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    meta_message_id VARCHAR(255),
    quota_exceeded_at TIMESTAMPTZ,
    processing_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    claimed_at TIMESTAMPTZ,
    replied_at TIMESTAMPTZ,
    dead_letter_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE outbound_dedup (
    message_id UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    response_text TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE usage_events (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE governance_outbox (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    event_kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);
"""


@pytest.fixture
async def real_db_pool():
    pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DEFECT_2)
    yield pool
    await pool.close()


@pytest.mark.asyncio
async def test_ai_cache_jsonb_preserves_richiede_umano_across_worker_crash_and_retry(real_db_pool):
    """
    Test di integrazione reale:
    1. Worker 1 processa il messaggio -> LLM ritorna richiede_umano=True.
    2. Salva in ai_reply_cache la struttura JSONB: {"text": ..., "richiede_umano": true}.
    3. Simula crash del worker prima dell'invio (messaggio resta con sent_at=NULL, replied_at=NULL).
    4. Worker 2 (retry) claima il messaggio dallo stesso Postgres reale.
    5. Verifica che Worker 2:
       - Riconosce ai_reply_cache presente (NON richiama l'LLM: mock LLM count == 0 nel 2° run)
       - Estrae richiede_umano=True dalla cache JSONB
       - Esegue l'escalation a umano sul DB reale (ticket_status -> 'PENDING_STAFF')
       - Invia a Meta
       - Marca il messaggio come handled / escalated.
    """
    repo = Repository(pool=real_db_pool)
    app_config = AppConfig(
        app_secret="sec_test",
        encryption_key="C1IuGfMh142ShEqV9Y2w3WPcMjIjO4aXjbnly7sqlvw=",
        postgres_dsn=DB_DSN,
        verify_token="tok_test",
        max_retry_attempts=5,
    )

    org_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    # Inserimento dati iniziali nel Postgres reale
    async with real_db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO organizations (id, name, subscription_status, trial_start,
                    trial_end, messages_limit)
               VALUES ($1, 'Ristorante P0', 'trialing', now() - interval '1 day',
                    now() + interval '7 days', 100)""",
            org_id
        )
        await conn.execute(
            "INSERT INTO contacts (id, organization_id, phone_number) VALUES ($1, $2, '393339998877')",
            contact_id, org_id
        )
        await conn.execute(
            "INSERT INTO conversations (id, organization_id, contact_id, ticket_status) VALUES ($1, $2, $3, 'AI_ACTIVE')",
            conv_id, org_id, contact_id
        )
        await conn.execute(
            """INSERT INTO messages (id, organization_id, conversation_id, direction, content_text, content, status)
               VALUES ($1, $2, $3, 'inbound', 'Vorrei parlare con il responsabile per un evento privato', '{"from": "393339998877"}'::jsonb, 'received_pending_ai')""",
            msg_id, org_id, conv_id
        )

    tenant_cfg = TenantConfig(
        organization_id=org_id,
        phone_number_id="12345",
        waba_id="waba_1",
        access_token="tok_1",
        business_profile={"nome": "Ristorante P0"},
    )

    # --- FASE 1: Worker 1 esegue e simula CRASH prima dell'invio ---
    llm_call_count = {"count": 0}

    async def fake_llm(*args, **kwargs):
        llm_call_count["count"] += 1
        return RispostaOutput(
            risposta="Ti metto in contatto con il responsabile eventi dello staff.",
            richiede_umano=True,
            motivo="richiesta_evento_privato",
            categoria="escalation",
        )

    # Mock del servizio WhatsApp: crasha durante _send_ai_reply al primo run
    service_worker1 = AsyncMock()
    service_worker1.check_opt_out = AsyncMock(return_value={"is_opt_out": False})
    service_worker1.check_human_request = AsyncMock(return_value=False)
    service_worker1.fast_path_match = AsyncMock(return_value=None)
    service_worker1.send_whatsapp_message = AsyncMock(side_effect=RuntimeError("Worker 1 CRASH before Meta ack!"))

    processor1 = InboundProcessor(app_config, repo, service_worker1)

    with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=tenant_cfg)), \
         patch("src.whatsapp.inbound_processor.genera_risposta_async", fake_llm), \
         patch("src.whatsapp.inbound_processor.recupera_contesto_documenti", AsyncMock(return_value=AsyncMock(testo="", chunks=[]))):
        try:
            # Exercise the real queue claim: A03 only admits messages whose
            # worker claim set status='processing'. Reconstructing an unclaimed
            # inbound row bypassed that production precondition.
            msg_row = (await repo.claim_inbound_messages(limit=1))[0]
            await processor1._process_one(msg_row)
        except RuntimeError:
            pass  # Crash simulato

    assert llm_call_count["count"] == 1, "L'LLM doveva essere chiamato al 1° tentativo"

    # Verifica stato DB dopo il crash:
    # - billed_at DEVE essere popolato
    # - ai_reply_cache DEVE essere JSONB con richiede_umano = true
    # - l'escalation DB è autorevole anche se la courtesy delivery è incerta:
    #   replied_at chiude l'inbound per impedire un secondo invio.
    async with real_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT billed_at, ai_reply_cache, sent_at, replied_at, status FROM messages WHERE id = $1",
            msg_id
        )
        assert row["billed_at"] is not None
        assert row["sent_at"] is None
        assert row["replied_at"] is not None
        cache_in_db = row["ai_reply_cache"]
        if isinstance(cache_in_db, str):
            cache_in_db = json.loads(cache_in_db)
        assert isinstance(cache_in_db, dict), f"Atteso dict JSONB, trovato {type(cache_in_db)}"
        assert cache_in_db["richiede_umano"] is True
        assert "responsabile eventi" in cache_in_db["text"]

    # --- FASE 2: Worker 2 (RETRY) riparte ---
    service_worker2 = AsyncMock()
    service_worker2.check_opt_out = AsyncMock(return_value={"is_opt_out": False})
    service_worker2.check_human_request = AsyncMock(return_value=False)
    service_worker2.fast_path_match = AsyncMock(return_value=None)
    service_worker2.send_whatsapp_message = AsyncMock(return_value={"status": "sent", "wam_id": "meta_wamid_999"})

    processor2 = InboundProcessor(app_config, repo, service_worker2)

    with patch("src.whatsapp.inbound_processor.load_tenant_config", AsyncMock(return_value=tenant_cfg)), \
         patch("src.whatsapp.inbound_processor.genera_risposta_async", fake_llm):
        # Reset processing_at per simulare reap_stale_claims del supervisor
        async with real_db_pool.acquire() as conn:
            await conn.execute("UPDATE messages SET processing_at = NULL WHERE id = $1", msg_id)

        msg_row_retry = await repo.reconstruct_payload_for_retry(msg_id)
        await processor2._process_one(msg_row_retry)

    # 1. Verifica che l'LLM NON sia stato rigenerato (costo risparmiato)
    assert llm_call_count["count"] == 1, "L'LLM NON deve essere richiamato nel retry: doveva riusare la cache JSONB!"

    # 2. Il retry non deve reinviare una courtesy dall'esito ambiguo.
    service_worker2.send_whatsapp_message.assert_not_awaited()

    # 3. Verifica sul DB Postgres reale che:
    #    a) L'escalation a operatore umano sia avvenuta (conversations.ticket_status == 'PENDING_STAFF')
    #    b) Il messaggio sia marcato handled con handling_type == 'escalated'
    #    c) nessun provider id inventato sia stato salvato
    async with real_db_pool.acquire() as conn:
        conv_row = await conn.fetchrow("SELECT ticket_status, pending_staff_at FROM conversations WHERE id = $1", conv_id)
        assert conv_row["ticket_status"] == "PENDING_STAFF", (
            f"Il retry ha perso richiede_umano! ticket_status e' '{conv_row['ticket_status']}', atteso 'PENDING_STAFF'"
        )
        assert conv_row["pending_staff_at"] is not None

        final_msg = await conn.fetchrow(
            "SELECT status, handling_type, sent_at, meta_message_id, replied_at FROM messages WHERE id = $1",
            msg_id
        )
        assert final_msg["status"] == "handled"
        assert final_msg["handling_type"] == "escalated"
        assert final_msg["sent_at"] is None
        assert final_msg["meta_message_id"] is None
        assert final_msg["replied_at"] is not None
