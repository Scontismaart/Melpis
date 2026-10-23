import os
import glob

os.environ.setdefault("TC_HOST", "localhost")

import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import asyncpg
import pytest

CI = os.getenv("CI")

_dsn = os.getenv(
    "TEST_DB_DSN",
    f"postgresql://{os.getenv('PGUSER','test')}"
    f":{os.getenv('PGPASSWORD','test')}"
    f"@{os.getenv('PGHOST','localhost')}"
    f":{os.getenv('PGPORT','55432')}"
    f"/{os.getenv('PGDATABASE','p0_concurrency_test')}"
)

@pytest.fixture(scope="session")
def postgres_container():
    class _FakeContainer:
        @staticmethod
        def get_connection_url():
            return _dsn
    return _FakeContainer()


@pytest.fixture
async def pg_pool(postgres_container):
    dsn = postgres_container.get_connection_url().replace("+psycopg2", "")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE SCHEMA IF NOT EXISTS auth;
            CREATE TABLE IF NOT EXISTS auth.users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email TEXT
            );
            CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid AS $$
                SELECT NULL::uuid
            $$ LANGUAGE sql STABLE;
            CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb AS $$
                SELECT '{}'::jsonb
            $$ LANGUAGE sql STABLE;
            DO $$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
                    CREATE ROLE anon;
                END IF;
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
                    CREATE ROLE authenticated;
                END IF;
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'service_role') THEN
                    CREATE ROLE service_role;
                END IF;
            END $$;
            DROP SCHEMA IF EXISTS public CASCADE;
            CREATE SCHEMA public;
            GRANT USAGE ON SCHEMA public TO service_role;
        """)
        with open("src/whatsapp/schema.sql", encoding="utf-8") as f:
            await conn.execute(f.read())
        with open("src/core/db/schema.sql", encoding="utf-8") as f:
            await conn.execute(f.read())
        with open("src/core/db/triggers.sql", encoding="utf-8") as f:
            await conn.execute(f.read())
        # Keep the WhatsApp tests on the same complete schema/migration chain
        # as core tests, including migration 063's durable erasure barrier.
        for sql_path in sorted(glob.glob("src/core/db/migrations/0*.sql")):
            with open(sql_path, encoding="utf-8") as f:
                sql = f.read()
            try:
                await conn.execute(sql)
            except asyncpg.UndefinedObjectError:
                if "024_hnsw" in sql_path:
                    continue
                raise
    yield pool
    await pool.close()


@pytest.fixture(autouse=True, scope="session")
def _set_env():
    os.environ.setdefault("ENCRYPTION_KEY", "C1IuGfMh142ShEqV9Y2w3WPcMjIjO4aXjbnly7sqlvw=")


@pytest.fixture(autouse=True)
async def reset_db(pg_pool):
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            TRUNCATE TABLE
                contact_consent_log, message_delivery_attempts,
                messages, conversations, contacts, whatsapp_templates,
                whatsapp_accounts, organizations,
                reviews, bookings, booking_settings,
                webhook_idempotency, meta_webhook_inbox
            CASCADE
        """)


@pytest.fixture
async def repo(pg_pool):
    from src.whatsapp.repository import Repository
    return Repository(pool=pg_pool)


@pytest.fixture
def status_webhook_fixture():
    return {
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
                        "id": "wamid.example",
                        "status": "delivered",
                        "timestamp": "1712345678",
                        "recipient_id": "391234567890",
                        "biz_opaque_callback_data": "msg-uuid-here",
                    }],
                },
            }],
        }],
    }


@pytest.fixture
def message_webhook_fixture():
    return {
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
                        "profile": {"name": "Mario Rossi"},
                        "wa_id": "391234567890",
                    }],
                    "messages": [{
                        "from": "391234567890",
                        "id": "wamid.inbound.1",
                        "timestamp": "1712345678",
                        "type": "text",
                        "text": {"body": "Ciao, vorrei prenotare"},
                    }],
                },
            }],
        }],
    }


@pytest.fixture
def button_reply_fixture():
    return {
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
                    "messages": [{
                        "from": "391234567890",
                        "id": "wamid.inbound.2",
                        "timestamp": "1712345679",
                        "type": "interactive",
                        "interactive": {
                            "type": "button_reply",
                            "button_reply": {
                                "id": "unsubscribe_confirm",
                                "title": "Annulla iscrizione",
                            },
                        },
                    }],
                },
            }],
        }],
    }


@pytest.fixture
def template_status_fixture():
    return {
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
                    "reason": None,
                },
            }],
        }],
    }


@pytest.fixture
def app_config():
    from src.whatsapp.config import AppConfig
    return AppConfig(
        app_secret="test_app_secret_123",
        encryption_key="C1IuGfMh142ShEqV9Y2w3WPcMjIjO4aXjbnly7sqlvw=",
        postgres_dsn="postgresql://test:test@localhost:5432/test",
        verify_token="test_verify_token",
        max_retry_attempts=5,
    )


@pytest.fixture
def sample_msg():
    return {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "content": {"from": "391234567890"},
        "content_text": "Ciao",
        "message_type": "text",
    }


@pytest.fixture
def fake_tenant_config():
    from src.whatsapp.config import TenantConfig
    return TenantConfig(
        organization_id=uuid.uuid4(),
        phone_number_id="123456",
        waba_id="waba1",
        access_token="decrypted-token",
        business_profile={"nome": "Trattoria Test", "orari": "12-15"},
    )


@pytest.fixture
def mock_service():
    service = AsyncMock()
    service.check_opt_out = AsyncMock(return_value={"is_opt_out": False, "confidence": "low"})
    service.fast_path_match = AsyncMock(return_value=None)
    service.check_human_request = AsyncMock(return_value=False)
    service.send_whatsapp_message = AsyncMock(return_value={"status": "sent", "wam_id": "wamid_12345"})
    service.MessageUsageExceeded = Exception
    return service


@pytest.fixture
def mock_repo(sample_msg):
    repo = AsyncMock()
    repo.claim_inbound_messages = AsyncMock(return_value=[sample_msg])
    repo.reap_stale_claims = AsyncMock(return_value=[])
    repo.try_mark_replied = AsyncMock(return_value={"id": sample_msg["id"], "status": "handled", "replied_at": datetime.now()})
    repo.update_heartbeat = AsyncMock()
    repo.get_org_subscription_state = AsyncMock(return_value={
        "subscription_status": "active", "trial_end": None,
        "ai_accounting_blocked": False,
    })
    repo.get_or_create_contact = AsyncMock(return_value={"id": uuid.uuid4()})
    repo.mark_ai_disclosure_sent = AsyncMock(return_value=True)
    repo.faq_cache_lookup = AsyncMock(return_value=None)
    repo.faq_cache_store = AsyncMock(return_value=None)
    repo.pool = MagicMock()
    repo.claim_message_for_processing = AsyncMock(return_value={
        "status": "claimed",
        "ai_reply_cache": None,
    })
    repo.get_outbound_dedup = AsyncMock(return_value=None)
    repo.save_outbound_dedup = AsyncMock()
    repo.save_ai_reply = AsyncMock()
    repo.check_booking_exists = AsyncMock(return_value=False)
    repo.mark_message_sent = AsyncMock()
    repo.escalate_to_human = AsyncMock(return_value={"id": str(uuid.uuid4()), "ticket_status": "PENDING_STAFF"})
    repo.record_usage = AsyncMock()
    repo.get_org_business_profile = AsyncMock(return_value={})
    return repo

