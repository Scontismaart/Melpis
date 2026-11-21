SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- A deterministic, byte-length-framed digest preserves the existing global
-- tuple identity without keeping raw provider IDs or arbitrary status text.
-- This is pseudonymized replay evidence, not anonymous data. It does not
-- establish tenant/subject scope or a request-conclusion timestamp.
CREATE OR REPLACE FUNCTION public.webhook_receipt_key_sha256(text,text,text)
RETURNS bytea LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog AS $$
    SELECT sha256(
        int4send(octet_length(convert_to($1,'UTF8'))) || convert_to($1,'UTF8') ||
        int4send(octet_length(convert_to($2,'UTF8'))) || convert_to($2,'UTF8') ||
        int4send(octet_length(convert_to($3,'UTF8'))) || convert_to($3,'UTF8')
    );
$$;
REVOKE ALL ON FUNCTION public.webhook_receipt_key_sha256(text,text,text)
    FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.webhook_receipt_key_sha256(text,text,text) TO service_role;

-- Upgrade atomically, retaining the original receipt timestamps and every
-- dedup identity. A collision/constraint failure aborts the entire upgrade.
-- Coordinate the application cutover: old workers cannot write to the new
-- schema; new workers cannot write to the old schema. No raw-ID fallback.
DO $$
BEGIN
    LOCK TABLE public.webhook_idempotency IN ACCESS EXCLUSIVE MODE;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name='webhook_idempotency'
                 AND column_name='wam_id') THEN
        ALTER TABLE public.webhook_idempotency ADD COLUMN receipt_key_sha256 bytea;
        UPDATE public.webhook_idempotency
        SET receipt_key_sha256=public.webhook_receipt_key_sha256(wam_id,resource_type,status_value);
        ALTER TABLE public.webhook_idempotency
            DROP CONSTRAINT webhook_idempotency_pkey,
            ALTER COLUMN receipt_key_sha256 SET NOT NULL,
            ADD CONSTRAINT webhook_idempotency_pkey PRIMARY KEY (receipt_key_sha256),
            ADD CONSTRAINT webhook_receipt_key_length CHECK (octet_length(receipt_key_sha256)=32),
            DROP COLUMN wam_id,
            DROP COLUMN resource_type,
            DROP COLUMN status_value;
    END IF;
END $$;

-- No expiry is based on created_at. A scoped conclusion, applicable holds,
-- and independent replay protection across recoverable backups remain required.
RESET lock_timeout;
RESET statement_timeout;
