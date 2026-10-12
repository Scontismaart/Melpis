# Project Info

## 1. Directory Tree (Filtered)
```text
C:.
├── src
│   ├── core
│   │   ├── db
│   │   │   ├── triggers.sql
│   │   │   ├── __init__.py
│   │   │   └── migrations (various .sql files)
│   │   ├── documenti
│   │   ├── email_sources
│   │   ├── gdpr
│   │   ├── guardrails
│   │   ├── inbox
│   │   ├── notifications
│   │   ├── report
│   │   ├── reviews
│   │   ├── review_sources
│   │   └── security
│   ├── data
│   ├── instagram
│   ├── logs
│   ├── models
│   └── whatsapp
├── tests
│   ├── core
│   ├── unit
│   └── whatsapp
└── web
    ├── landing page
    └── vendor
```
*(Note: Full tree is truncated here for brevity, see the project for the full list of files)*

## 2. Dependencies (`requirements.txt`)
Since this is a Python project, it uses `requirements.txt` instead of `package.json` or `pyproject.toml`.

```text
alembic==1.18.5
APScheduler==3.11.3
asyncpg==0.31.0
crewai[litellm]==1.15.4
cryptography==43.0.3
fastapi==0.139.2
google-api-python-client==2.198.0
google-auth-httplib2==0.4.0
google-auth-oauthlib==1.4.0
httpx==0.28.1
Pillow==12.3.0
pyairtable==2.3.7
pymupdf==1.28.0
pypdf==6.14.2
pytest==8.4.2
pytest-asyncio==0.26.0
python-dotenv==1.2.2
PyJWT[crypto]==2.15.1
python-multipart==0.0.32
rapidocr-onnxruntime==1.4.4
redis==6.4.0
respx==0.23.1
sentence-transformers==5.6.0
sentry-sdk==2.66.1
stripe==10.12.0
tenacity==9.1.4
testcontainers==4.14.2
uvicorn==0.51.0
weasyprint==63.1
jinja2==3.1.6
```

## 3. Database Schemas

The database schema is split mainly between `src/core/db/schema.sql` and `src/whatsapp/schema.sql`.

### Core Schema (`src/core/db/schema.sql`)
```sql
-- ============================================================
-- EXTENSION
-- ============================================================
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 1. BOOKINGS (sostituisce Airtable + _prenotazioni_demo)
-- ============================================================
CREATE TABLE IF NOT EXISTS bookings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id),
    contact_id          UUID REFERENCES contacts(id),
    nome_cliente        TEXT NOT NULL,
    telefono            TEXT NOT NULL DEFAULT '',
    data                DATE NOT NULL,
    ora                 TIME NOT NULL,
    coperti             INT CHECK (coperti > 0),
    note                TEXT NOT NULL DEFAULT '',
    stato               TEXT NOT NULL DEFAULT 'in_attesa'
                        CHECK (stato IN ('in_attesa','confermata','cancellata','no_show','completata')),
    origine             TEXT NOT NULL DEFAULT 'Dashboard',
    richiede_intervento BOOLEAN NOT NULL DEFAULT FALSE,
    id_conversazione    TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bookings_org_data ON bookings(organization_id, data);

-- ============================================================
-- 2. BOOKING_SETTINGS (sostituisce capienze orarie in RAM)
-- ============================================================
CREATE TABLE IF NOT EXISTS booking_settings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id) UNIQUE,
    slot_minutes        INT NOT NULL DEFAULT 60,
    fasce_orarie        JSONB,
    capienze_orarie     JSONB,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(organization_id)
);

-- ============================================================
-- 3. REVIEWS
-- ============================================================
CREATE TABLE IF NOT EXISTS reviews (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id),
    contact_id          UUID REFERENCES contacts(id),
    testo               TEXT NOT NULL,
    valutazione_stelle  INT CHECK (valutazione_stelle BETWEEN 1 AND 5),
    fonte               TEXT NOT NULL DEFAULT 'manuale',
    autore              TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reviews_org ON reviews(organization_id);

-- ============================================================
-- 4. DOCUMENTS (metadati)
-- ============================================================
CREATE TABLE IF NOT EXISTS documents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id),
    nome                TEXT NOT NULL,
    tipo                TEXT NOT NULL DEFAULT 'upload',
    fonte               TEXT NOT NULL DEFAULT '',
    caricato_il         TIMESTAMPTZ DEFAULT NOW(),
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_documents_org ON documents(organization_id);

-- ============================================================
-- 5. DOCUMENT_CHUNKS (pgvector, sostituisce ChromaDB)
-- ============================================================
CREATE TABLE IF NOT EXISTS document_chunks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index         INT NOT NULL,
    content             TEXT NOT NULL,
    embedding           vector(384) NOT NULL,
    metadata            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chunks_org_doc ON document_chunks(organization_id, document_id);

-- Trigger: garantisce coerenza organization_id tra chunk e documento
CREATE OR REPLACE FUNCTION check_chunk_org_consistency() RETURNS trigger AS $$
BEGIN
  IF NEW.organization_id != (SELECT organization_id FROM documents WHERE id = NEW.document_id) THEN
    RAISE EXCEPTION
      'organization_id mismatch: chunk belongs to org %, document belongs to org %',
      NEW.organization_id,
      (SELECT organization_id FROM documents WHERE id = NEW.document_id);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_chunk_org ON document_chunks;
CREATE CONSTRAINT TRIGGER trg_check_chunk_org
    AFTER INSERT OR UPDATE ON document_chunks
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_chunk_org_consistency();

-- ============================================================
-- 6. EMAIL_CONFIGS (sostituisce email_config.json)
-- ============================================================
CREATE TABLE IF NOT EXISTS email_configs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id),
    indirizzo           TEXT NOT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(organization_id, indirizzo)
);

-- ============================================================
-- 7. USAGE_EVENTS (billing)
-- ============================================================
CREATE TABLE IF NOT EXISTS usage_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id),
    event_type          TEXT NOT NULL,
    quantity            INT NOT NULL DEFAULT 1,
    metadata            JSONB NOT NULL DEFAULT '{}',
    billing_month       DATE GENERATED ALWAYS AS
                        (date_trunc('month', created_at AT TIME ZONE 'UTC')::date) STORED,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usage_events_org_month ON usage_events(organization_id, billing_month);

-- ============================================================
-- 8. EVENT_LOG (proiezione derivata da trigger, mai scritta dal codice)
-- ============================================================
CREATE TABLE IF NOT EXISTS event_log (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id),
    source_table        TEXT NOT NULL,
    source_id           UUID NOT NULL,
    tipo_evento         TEXT NOT NULL,
    priorita            TEXT NOT NULL,
    testo_originale     TEXT NOT NULL DEFAULT '',
    risposta_ai         TEXT NOT NULL DEFAULT '',
    gestito_da_ai       BOOLEAN NOT NULL DEFAULT TRUE,
    dettagli            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_log_org_created ON event_log(organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_log_org_priorita ON event_log(organization_id, priorita)
    WHERE priorita != 'bassa';
```

### WhatsApp Schema (`src/whatsapp/schema.sql`)
```sql
CREATE TABLE IF NOT EXISTS organizations (
    id              UUID PRIMARY KEY,
    name            TEXT NOT NULL,
    business_profile JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatsapp_accounts (
    id              UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    phone_number_id TEXT NOT NULL UNIQUE,
    waba_id         TEXT NOT NULL,
    access_token    TEXT NOT NULL,
    verify_token    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_whatsapp_accounts_org ON whatsapp_accounts(organization_id);

CREATE TABLE IF NOT EXISTS contacts (
    id              UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    phone_number    TEXT NOT NULL,
    marketing_opt_out BOOLEAN NOT NULL DEFAULT FALSE,
    opted_out_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(organization_id, phone_number)
);
CREATE INDEX IF NOT EXISTS idx_contacts_org ON contacts(organization_id);

CREATE TABLE IF NOT EXISTS conversations (
    id              UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    contact_id      UUID NOT NULL REFERENCES contacts(id),
    status          TEXT NOT NULL DEFAULT 'active',
    last_message_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(organization_id, contact_id)
);
CREATE INDEX IF NOT EXISTS idx_conversations_org ON conversations(organization_id);

CREATE TABLE IF NOT EXISTS messages (
    id              UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    wam_id          TEXT UNIQUE,
    direction       TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    message_type    TEXT NOT NULL,
    content         JSONB NOT NULL,
    content_text    TEXT,
    status          TEXT NOT NULL,
    handling_type   TEXT,
    error_code      TEXT,
    error_title     TEXT,
    error_details   JSONB,
    sent_at         TIMESTAMPTZ,
    delivered_at    TIMESTAMPTZ,
    read_at         TIMESTAMPTZ,
    claimed_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT chk_status_by_direction CHECK (
        (direction = 'inbound'  AND status IN ('received_pending_ai','processing','handled'))
        OR
        (direction = 'outbound' AND status IN ('queued','processing','sent','delivered','read','failed','sending_ambiguous'))
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_wam_id ON messages(wam_id) WHERE wam_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_messages_org_created ON messages(organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_org_status ON messages(organization_id, status) WHERE status != 'read';
CREATE INDEX IF NOT EXISTS idx_messages_org_inbound_pending ON messages(organization_id)
    WHERE direction='inbound' AND status='received_pending_ai';
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS message_delivery_attempts (
    id              UUID PRIMARY KEY,
    message_id      UUID NOT NULL REFERENCES messages(id),
    attempt_number  INT NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'pending',
    next_retry_at   TIMESTAMPTZ,
    error_details   JSONB,
    claimed_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_delivery_attempts_message ON message_delivery_attempts(message_id);
CREATE INDEX IF NOT EXISTS idx_delivery_attempts_retry
    ON message_delivery_attempts(status, next_retry_at)
    WHERE status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_attempt_per_message
    ON message_delivery_attempts(message_id)
    WHERE status IN ('pending', 'processing');

CREATE TABLE IF NOT EXISTS contact_consent_log (
    id              UUID PRIMARY KEY,
    contact_id      UUID NOT NULL REFERENCES contacts(id),
    event_type      TEXT NOT NULL CHECK (event_type IN ('opt_out', 'opt_in')),
    method          TEXT NOT NULL CHECK (method IN ('keyword_match', 'button_reply', 'manual_staff')),
    triggering_message_id UUID REFERENCES messages(id),
    matched_text    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_consent_log_contact ON contact_consent_log(contact_id);

CREATE TABLE IF NOT EXISTS whatsapp_templates (
    id              UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    name            TEXT NOT NULL,
    language        TEXT NOT NULL,
    category        TEXT NOT NULL CHECK (category IN ('MARKETING', 'UTILITY', 'AUTHENTICATION')),
    status          TEXT NOT NULL DEFAULT 'PENDING',
    components      JSONB NOT NULL,
    rejected_reason TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(organization_id, name, language)
);
CREATE INDEX IF NOT EXISTS idx_templates_org ON whatsapp_templates(organization_id);
```
