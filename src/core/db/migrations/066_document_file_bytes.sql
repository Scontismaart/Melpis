-- B02: original uploaded bytes, not pgvector storage or commercial replies.
-- No numeric plan/trial limits are configured by this migration.
-- Historical provenance is unknown: never infer original size from chunk text
-- or metadata.bytes (which also represented manual text, FAQ and web imports).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_bytes BIGINT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_sha256 TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_file_bytes BIGINT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_file_sha256 TEXT;

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
REVOKE INSERT(file_bytes,file_sha256,index_file_bytes,index_file_sha256),
       UPDATE(file_bytes,file_sha256,index_file_bytes,index_file_sha256),
       REFERENCES(file_bytes,file_sha256,index_file_bytes,index_file_sha256)
    ON documents FROM PUBLIC,anon,authenticated;
