-- B02: original uploaded bytes, not pgvector storage or commercial replies.
-- No numeric plan/trial limits are configured by this migration.
-- Historical provenance is unknown: never infer original size from chunk text
-- or metadata.bytes (which also represented manual text, FAQ and web imports).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_bytes BIGINT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_sha256 TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_file_bytes BIGINT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_file_sha256 TEXT;
-- Pending replacement identity is separate from the last published revision.
-- It lives with the document, never in the durable deleted-request tombstone.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_intent JSONB;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='documents_file_evidence' AND conrelid='public.documents'::regclass) THEN
ALTER TABLE documents ADD CONSTRAINT documents_file_evidence CHECK (
    (file_bytes IS NULL AND file_sha256 IS NULL)
    OR (file_bytes IS NOT NULL AND file_bytes BETWEEN 0 AND 20971520 AND (
        (file_bytes = 0 AND file_sha256 IS NULL)
        OR (file_bytes > 0 AND file_sha256 IS NOT NULL AND file_sha256 ~ '^[0-9a-f]{64}$')))
);
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='documents_pending_file_evidence' AND conrelid='public.documents'::regclass) THEN
ALTER TABLE documents ADD CONSTRAINT documents_pending_file_evidence CHECK (
    (index_file_bytes IS NULL AND index_file_sha256 IS NULL)
    OR (index_file_bytes IS NOT NULL AND index_file_bytes BETWEEN 0 AND 20971520 AND (
        (index_file_bytes = 0 AND index_file_sha256 IS NULL)
        OR (index_file_bytes > 0 AND index_file_sha256 IS NOT NULL AND index_file_sha256 ~ '^[0-9a-f]{64}$')))
);
END IF;
END $$;

-- Table-level writes are already revoked by 065; also deny independent column grants.
REVOKE INSERT(file_bytes,file_sha256,index_file_bytes,index_file_sha256,index_intent),
       UPDATE(file_bytes,file_sha256,index_file_bytes,index_file_sha256,index_intent),
       REFERENCES(file_bytes,file_sha256,index_file_bytes,index_file_sha256,index_intent)
    ON documents FROM PUBLIC,anon,authenticated;

-- A mutation of a certified source also revokes its in-flight replacement.
-- The canonical publisher first marks its own row unverified inside the same
-- transaction, so its complete atomic chunk swap does not revoke itself.
CREATE OR REPLACE FUNCTION invalidate_modified_document_index() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE tenant UUID; document UUID;
BEGIN
    tenant := CASE WHEN TG_OP='DELETE' THEN OLD.organization_id ELSE NEW.organization_id END;
    document := CASE WHEN TG_OP='DELETE' THEN OLD.document_id ELSE NEW.document_id END;
    IF TG_OP='UPDATE' AND (OLD.document_id,OLD.organization_id) IS DISTINCT FROM (NEW.document_id,NEW.organization_id) THEN
        UPDATE documents SET index_verified=FALSE,stato='caricato',index_token=NULL,
            index_lease_until=NULL,index_intent=NULL,index_fence=index_fence+1,
            index_file_bytes=NULL,index_file_sha256=NULL,updated_at=NOW()
            WHERE organization_id=OLD.organization_id AND id=OLD.document_id AND index_verified;
    END IF;
    UPDATE documents SET index_verified=FALSE,stato='caricato',index_token=NULL,
        index_lease_until=NULL,index_intent=NULL,index_fence=index_fence+1,
        index_file_bytes=NULL,index_file_sha256=NULL,updated_at=NOW()
        WHERE organization_id=tenant AND id=document AND index_verified;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION invalidate_profile_knowledge() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
    IF OLD.business_profile IS DISTINCT FROM NEW.business_profile THEN
        DELETE FROM faq_cache WHERE organization_id=NEW.id;
        UPDATE documents SET index_verified=FALSE,stato='caricato',index_token=NULL,
            index_lease_until=NULL,index_intent=NULL,index_fence=index_fence+1,
            index_file_bytes=NULL,index_file_sha256=NULL,updated_at=NOW()
            WHERE organization_id=NEW.id AND
                (tipo='dati_struttura' OR index_intent->>'tipo'='dati_struttura');
        UPDATE organizations SET knowledge_epoch=knowledge_epoch+1 WHERE id=NEW.id;
    END IF;
    RETURN NULL;
END $$;
