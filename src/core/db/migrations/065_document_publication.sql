-- B02: backend-owned, fenced complete-index publication. No historical opt-in.
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS knowledge_epoch BIGINT NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_key UUID;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_token UUID;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_fence BIGINT NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_lease_until TIMESTAMPTZ;
ALTER TABLE faq_cache ADD COLUMN IF NOT EXISTS knowledge_epoch BIGINT NOT NULL DEFAULT -1;
CREATE UNIQUE INDEX IF NOT EXISTS documents_index_key_org ON documents(organization_id,index_key)
    WHERE index_key IS NOT NULL;
-- Keep request identities after replacement/deletion; a delayed retry must not
-- recreate a deleted source or overwrite a later revision.
CREATE TABLE IF NOT EXISTS document_index_requests(
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    request_key UUID NOT NULL,
    document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    payload_hash TEXT NOT NULL,
    request_spec JSONB NOT NULL,
    published_revision BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(organization_id,request_key)
);
ALTER TABLE document_index_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON document_index_requests FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON document_index_requests TO service_role;
DROP TRIGGER IF EXISTS gdpr_guard_write ON document_index_requests;
CREATE TRIGGER gdpr_guard_write BEFORE INSERT OR UPDATE ON document_index_requests
    FOR EACH ROW EXECUTE FUNCTION gdpr_guard_write();

-- Membership alone cannot authorize privileged publication, entitlement or cache writes.
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON documents,document_chunks FROM PUBLIC,anon,authenticated;
REVOKE ALL ON faq_cache FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON documents,document_chunks,faq_cache TO service_role;

-- Column privileges are independent of table grants.
DO $$
DECLARE relation_name text; column_name text;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY['documents','document_chunks','faq_cache','document_index_requests'] LOOP
        FOR column_name IN SELECT attname FROM pg_attribute
            WHERE attrelid=to_regclass('public.'||relation_name) AND attnum>0 AND NOT attisdropped
        LOOP
            EXECUTE format('REVOKE INSERT(%I),UPDATE(%I),REFERENCES(%I) ON public.%I FROM PUBLIC,anon,authenticated',
                column_name,column_name,column_name,relation_name);
            IF relation_name IN ('faq_cache','document_index_requests') THEN
                EXECUTE format('REVOKE SELECT(%I) ON public.%I FROM PUBLIC,anon,authenticated',column_name,relation_name);
            END IF;
        END LOOP;
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION invalidate_document_knowledge() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE tenant UUID;
BEGIN
    tenant := CASE WHEN TG_OP='DELETE' THEN OLD.organization_id ELSE NEW.organization_id END;
    UPDATE organizations SET knowledge_epoch=knowledge_epoch+1 WHERE id=tenant;
    DELETE FROM faq_cache WHERE organization_id=tenant;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS document_knowledge_epoch ON documents;
CREATE TRIGGER document_knowledge_epoch AFTER INSERT OR UPDATE OR DELETE ON documents
    FOR EACH ROW EXECUTE FUNCTION invalidate_document_knowledge();

-- A low-level legacy chunk writer cannot silently mutate an already certified index.
CREATE OR REPLACE FUNCTION invalidate_modified_document_index() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE tenant UUID; document UUID;
BEGIN
    tenant := CASE WHEN TG_OP='DELETE' THEN OLD.organization_id ELSE NEW.organization_id END;
    document := CASE WHEN TG_OP='DELETE' THEN OLD.document_id ELSE NEW.document_id END;
    IF TG_OP='UPDATE' AND (OLD.document_id,OLD.organization_id) IS DISTINCT FROM (NEW.document_id,NEW.organization_id) THEN
        UPDATE documents SET index_verified=FALSE,stato='caricato',updated_at=NOW()
            WHERE organization_id=OLD.organization_id AND id=OLD.document_id AND index_verified;
    END IF;
    UPDATE documents SET index_verified=FALSE,stato='caricato',updated_at=NOW()
        WHERE organization_id=tenant AND id=document AND index_verified;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS document_chunk_publication_guard ON document_chunks;
CREATE TRIGGER document_chunk_publication_guard AFTER INSERT OR UPDATE OR DELETE ON document_chunks
    FOR EACH ROW EXECUTE FUNCTION invalidate_modified_document_index();

-- Typed business data has priority over imported sources. A profile edit also
-- fences any preparation based on the previous prices/hours; only a new claim
-- can certify that derived document again.
CREATE OR REPLACE FUNCTION invalidate_profile_knowledge() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
    IF OLD.business_profile IS DISTINCT FROM NEW.business_profile THEN
        DELETE FROM faq_cache WHERE organization_id=NEW.id;
        UPDATE documents SET index_verified=FALSE,stato='caricato',index_token=NULL,
            index_lease_until=NULL,index_fence=index_fence+1,updated_at=NOW()
            WHERE organization_id=NEW.id AND tipo='dati_struttura';
        UPDATE organizations SET knowledge_epoch=knowledge_epoch+1 WHERE id=NEW.id;
    END IF;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS profile_knowledge_epoch ON organizations;
CREATE TRIGGER profile_knowledge_epoch AFTER UPDATE OF business_profile ON organizations
    FOR EACH ROW EXECUTE FUNCTION invalidate_profile_knowledge();
