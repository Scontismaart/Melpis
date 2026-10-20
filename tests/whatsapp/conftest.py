import os

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
            DROP SCHEMA IF EXISTS public CASCADE;
            CREATE SCHEMA public;
        """)
        with open("src/whatsapp/schema.sql") as f:
            await conn.execute(f.read())
        with open("src/core/db/migrations/004_gdpr.sql") as f:
            await conn.execute(f.read())
        with open("src/core/db/migrations/005_gdpr_consent.sql") as f:
            await conn.execute(f.read())
        with open("src/core/db/migrations/010_dead_letter.sql") as f:
            await conn.execute(f.read())
        with open("src/core/db/migrations/012_reply_guard.sql") as f:
            await conn.execute(f.read())
        with open("src/core/db/migrations/013_webhook_idempotency.sql") as f:
            await conn.execute(f.read())
        with open("src/core/db/migrations/052_webhook_inbox.sql") as f:
            await conn.execute(f.read())
        # Core tables necessari per 014_contact_fk_strategy (bookings, reviews, booking_settings)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                organization_id UUID NOT NULL REFERENCES organizations(id),
                contact_id UUID REFERENCES contacts(id),
                nome_cliente TEXT NOT NULL,
                telefono TEXT NOT NULL DEFAULT '',
                data DATE NOT NULL,
                ora TIME NOT NULL,
                coperti INT CHECK (coperti > 0),
                note TEXT NOT NULL DEFAULT '',
                stato TEXT NOT NULL DEFAULT 'in_attesa'
                    CHECK (stato IN ('in_attesa','confermata','cancellata','no_show','completata')),
                origine TEXT NOT NULL DEFAULT 'Dashboard',
                richiede_intervento BOOLEAN NOT NULL DEFAULT FALSE,
                id_conversazione TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS booking_settings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                organization_id UUID NOT NULL REFERENCES organizations(id) UNIQUE,
                slot_minutes INT NOT NULL DEFAULT 60,
                fasce_orarie JSONB,
                capienze_orarie JSONB,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                organization_id UUID NOT NULL REFERENCES organizations(id),
                contact_id UUID REFERENCES contacts(id),
                testo TEXT NOT NULL,
                valutazione_stelle INT CHECK (valutazione_stelle BETWEEN 1 AND 5),
                fonte TEXT NOT NULL DEFAULT 'manuale',
                autore TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        with open("src/core/db/migrations/014_contact_fk_strategy.sql") as f:
            await conn.execute(f.read())
        for ddl in [
            "CREATE TABLE IF NOT EXISTS documents (id UUID PRIMARY KEY, organization_id UUID)",
            "CREATE TABLE IF NOT EXISTS document_chunks (id UUID PRIMARY KEY, document_id UUID, organization_id UUID)",
            "CREATE TABLE IF NOT EXISTS email_configs (id UUID PRIMARY KEY, organization_id UUID UNIQUE)",
            "CREATE TABLE IF NOT EXISTS usage_events (id UUID PRIMARY KEY, organization_id UUID)",
            "CREATE TABLE IF NOT EXISTS event_log (id UUID PRIMARY KEY, organization_id UUID)",
            "CREATE TABLE IF NOT EXISTS audit_log (id UUID PRIMARY KEY, organization_id UUID NOT NULL, action TEXT NOT NULL, target_table TEXT, target_id UUID, details JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ DEFAULT NOW())",
        ]:
            await conn.execute(ddl)
        with open("src/core/db/migrations/015_org_fk_strategy.sql") as f:
            await conn.execute(f.read())
        with open("src/core/db/migrations/016_org_timezone.sql") as f:
            await conn.execute(f.read())
        # Da 030_instagram_channel: qui serve solo la colonna canale su
        # conversations (get_or_create_conversation / claim_inbound_messages).
        # La tabella instagram_accounts e la sua policy RLS vivono nello
        # schema completo (tests/core/conftest.py): qui le tabelle della
        # policy non esistono in questo schema ridotto.
        await conn.execute("""
            ALTER TABLE conversations
                ADD COLUMN IF NOT EXISTS canale TEXT NOT NULL DEFAULT 'whatsapp'
                CHECK (canale IN ('whatsapp', 'instagram'))
        """)
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

