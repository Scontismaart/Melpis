-- A06 capability only: no policy approval, destructive job or remote cutover.
CREATE TABLE IF NOT EXISTS gdpr_operations (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    requester TEXT NOT NULL,
    request_id UUID NOT NULL,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    kind TEXT NOT NULL CHECK (kind IN ('export','erase')),
    scope TEXT NOT NULL CHECK (scope IN ('organization','contact','account')),
    subject_id UUID,
    status TEXT NOT NULL CHECK (status IN ('requested','running','ready','blocked','partial','completed','failed','revoked','expired')),
    manifest JSONB NOT NULL DEFAULT '{}',
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, requester, request_id),
    UNIQUE (organization_id, id)
);
CREATE TABLE IF NOT EXISTS gdpr_export_chunks (
    organization_id UUID NOT NULL,
    operation_id UUID NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    ciphertext BYTEA NOT NULL CHECK (octet_length(ciphertext) <= 2097152),
    digest TEXT NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (organization_id, operation_id, sequence),
    FOREIGN KEY (organization_id, operation_id) REFERENCES gdpr_operations(organization_id,id)
);
CREATE TABLE IF NOT EXISTS gdpr_operation_steps (
    organization_id UUID NOT NULL,
    operation_id UUID NOT NULL,
    name TEXT NOT NULL CHECK (name ~ '^[a-z][a-z0-9_.-]{0,79}$'),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','uncertain','confirmed','held','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    fence BIGINT NOT NULL DEFAULT 0,
    lease_until TIMESTAMPTZ,
    evidence JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id, operation_id, name),
    FOREIGN KEY (organization_id, operation_id) REFERENCES gdpr_operations(organization_id,id)
);
-- An approved policy is an immutable operational record, never owner-editable.
-- No rows are seeded. C05 must approve the reference, scope and legal basis.
CREATE TABLE IF NOT EXISTS gdpr_policies (
    organization_id UUID NOT NULL REFERENCES organizations(id),
    category TEXT NOT NULL CHECK (category ~ '^[a-z][a-z0-9_]{0,79}$'),
    version TEXT NOT NULL CHECK (version ~ '^[A-Za-z0-9_.-]{1,80}$'),
    erasure_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    retention_days INTEGER CHECK (retention_days BETWEEN 1 AND 36500),
    legal_hold BOOLEAN NOT NULL DEFAULT TRUE,
    approval_reference TEXT NOT NULL CHECK (approval_reference ~ '^[A-Za-z0-9_./:-]{1,200}$'),
    approved_by TEXT NOT NULL CHECK (approved_by ~ '^[A-Za-z0-9_.:-]{1,100}$'),
    approved_at TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id,category,version),
    CHECK (valid_until > approved_at)
);
CREATE TABLE IF NOT EXISTS gdpr_operation_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id UUID NOT NULL,
    operation_id UUID NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type ~ '^[a-z][a-z0-9_.-]{0,79}$'),
    -- Codes, counts and digest references only. Never content or credentials.
    details JSONB NOT NULL DEFAULT '{}' CHECK (octet_length(details::text) <= 8192),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (organization_id,operation_id) REFERENCES gdpr_operations(organization_id,id)
);
CREATE INDEX IF NOT EXISTS gdpr_export_expiration ON gdpr_operations(expires_at)
    WHERE kind='export';
CREATE OR REPLACE FUNCTION gdpr_policy_fence() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.organization_id::text,606));
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS gdpr_policy_fence ON gdpr_policies;
CREATE TRIGGER gdpr_policy_fence BEFORE INSERT ON gdpr_policies
    FOR EACH ROW EXECUTE FUNCTION gdpr_policy_fence();
CREATE TABLE IF NOT EXISTS gdpr_erasure_barriers (
    organization_id UUID NOT NULL REFERENCES organizations(id),
    operation_id UUID NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('organization','contact')),
    subject_id UUID,
    identifier_digest TEXT,
    key_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id,operation_id),
    FOREIGN KEY (organization_id,operation_id) REFERENCES gdpr_operations(organization_id,id),
    CHECK ((scope='organization' AND subject_id IS NULL) OR
           (scope='contact' AND subject_id IS NOT NULL AND identifier_digest ~ '^[0-9a-f]{64}$' AND key_id IS NOT NULL))
);
-- Every writer takes a shared transaction lock; erasure takes its exclusive
-- counterpart BEFORE checking unresolved AI/outbound work and changing data.
-- A barrier is never cleared automatically by an elapsed lease or a timeout.
CREATE OR REPLACE FUNCTION gdpr_guard_write() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE item JSONB := to_jsonb(NEW); org UUID; subject UUID; msg UUID;
BEGIN
    org := NULLIF(item->>'organization_id','')::uuid;
    IF TG_TABLE_NAME='organizations' THEN
        IF TG_OP='UPDATE' AND NEW.business_profile IS NOT DISTINCT FROM OLD.business_profile
            AND NEW.name IS NOT DISTINCT FROM OLD.name THEN RETURN NEW; END IF;
        org := NEW.id;
    END IF;
    IF org IS NULL AND TG_TABLE_NAME='contact_consent_log' THEN
        SELECT organization_id INTO org FROM contacts WHERE id=NEW.contact_id;
    END IF;
    IF org IS NULL AND TG_TABLE_NAME='message_delivery_attempts' THEN
        SELECT organization_id INTO org FROM messages WHERE id=NEW.message_id;
    END IF;
    IF org IS NULL THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME='usage_events' AND item->>'event_type' <> 'system.provider_attempt' THEN RETURN NEW; END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended(org::text,606));
    IF NOT EXISTS (SELECT 1 FROM gdpr_erasure_barriers WHERE organization_id=org) THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME='contacts' THEN subject := NEW.id;
    ELSE
        subject := NULLIF(item->>'contact_id','')::uuid;
        IF subject IS NULL AND item->>'conversation_id' IS NOT NULL THEN
            SELECT contact_id INTO subject FROM conversations
                WHERE organization_id=org AND id=(item->>'conversation_id')::uuid;
        END IF;
    END IF;
    IF subject IS NULL THEN
        msg := COALESCE(NULLIF(item->>'message_id',''),
                        CASE WHEN TG_TABLE_NAME='event_log' THEN item->>'source_id' END,
                        item#>>'{metadata,context,message_id}')::uuid;
        IF msg IS NOT NULL THEN
            SELECT c.contact_id INTO subject FROM messages m JOIN conversations c
                ON c.id=m.conversation_id AND c.organization_id=org
                WHERE m.organization_id=org AND m.id=msg;
        END IF;
    END IF;
    IF subject IS NULL AND item->>'booking_id' IS NOT NULL THEN
        SELECT contact_id INTO subject FROM bookings WHERE organization_id=org AND id=(item->>'booking_id')::uuid;
    END IF;
    IF EXISTS (SELECT 1 FROM gdpr_erasure_barriers b WHERE b.organization_id=org
        AND (b.scope='organization' OR b.subject_id=subject)) THEN
        RAISE EXCEPTION 'GDPR erasure barrier rejects data production' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
DO $$ DECLARE t TEXT; BEGIN
    FOREACH t IN ARRAY ARRAY['contacts','conversations','messages','bookings','reviews',
        'documents','document_chunks','faq_cache','event_log','simulation_requests',
        'meta_webhook_inbox','booking_confirmation_outbox','outbound_dedup',
        'airtable_webhook_events','external_booking_sync','message_feedback','usage_events',
        'contact_consent_log','message_delivery_attempts','organizations','onboarding_profiles',
        'email_configs','team_invitations','booking_settings','whatsapp_accounts','instagram_accounts',
        'google_calendar_credentials','google_business_credentials','airtable_connections',
        'airtable_webhooks','airtable_field_mappings','external_booking_credentials','oauth_nonces'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS gdpr_guard_write ON %I',t);
        EXECUTE format('CREATE TRIGGER gdpr_guard_write BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION gdpr_guard_write()',t);
    END LOOP;
END $$;
CREATE OR REPLACE FUNCTION gdpr_immutable_record() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN RAISE EXCEPTION 'GDPR evidence is append-only'; END $$;
DROP TRIGGER IF EXISTS gdpr_events_immutable ON gdpr_operation_events;
CREATE TRIGGER gdpr_events_immutable BEFORE UPDATE OR DELETE ON gdpr_operation_events
    FOR EACH ROW EXECUTE FUNCTION gdpr_immutable_record();
DROP TRIGGER IF EXISTS gdpr_policy_immutable ON gdpr_policies;
CREATE TRIGGER gdpr_policy_immutable BEFORE UPDATE OR DELETE ON gdpr_policies
    FOR EACH ROW EXECUTE FUNCTION gdpr_immutable_record();
DO $$ DECLARE t TEXT; BEGIN
    FOREACH t IN ARRAY ARRAY['gdpr_operations','gdpr_export_chunks','gdpr_operation_steps','gdpr_policies','gdpr_operation_events','gdpr_erasure_barriers'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,anon,authenticated', t);
        EXECUTE format('GRANT SELECT,INSERT ON %I TO service_role', t);
    END LOOP;
    GRANT UPDATE ON gdpr_operations,gdpr_operation_steps TO service_role;
    GRANT DELETE ON gdpr_export_chunks TO service_role;
    GRANT USAGE,SELECT ON SEQUENCE gdpr_operation_events_id_seq TO service_role;
    -- Application credentials cannot approve a legal policy or rewrite evidence.
    REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON gdpr_policies FROM service_role;
    REVOKE UPDATE,DELETE,TRUNCATE ON gdpr_operation_events FROM service_role;
    -- Intake goes through the backend HMAC tombstone check, never direct clients.
    REVOKE INSERT,UPDATE,DELETE ON contacts FROM anon,authenticated;
END $$;
