-- Capability JSON is transient authority material. Keep its hash and lineage
-- tombstone, but scrub customer content immediately on success and after expiry.
ALTER TABLE c24_private.worker_capabilities
    ALTER COLUMN payload DROP NOT NULL;
ALTER TABLE c24_private.worker_capabilities
    DROP CONSTRAINT IF EXISTS worker_capabilities_payload_check;
ALTER TABLE c24_private.worker_capabilities
    DROP CONSTRAINT IF EXISTS worker_capabilities_payload_retention_shape;
ALTER TABLE c24_private.worker_capabilities
    ADD CONSTRAINT worker_capabilities_payload_retention_shape
    CHECK (payload IS NULL OR jsonb_typeof(payload)='object');

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scrub_completed_payload()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
BEGIN
    IF OLD.completed_at IS NULL AND NEW.completed_at IS NOT NULL THEN
        NEW.payload := NULL;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scrub_completed_payload()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
DROP TRIGGER IF EXISTS c24_worker_scrub_completed_payload
    ON c24_private.worker_capabilities;
CREATE TRIGGER c24_worker_scrub_completed_payload
    BEFORE UPDATE OF completed_at ON c24_private.worker_capabilities
    FOR EACH ROW EXECUTE FUNCTION c24_private.c24_worker_scrub_completed_payload();

-- The trigger covers future successful finalizations. Scrub payloads retained
-- by rows that completed before this migration while preserving replay hashes
-- and their organization/source/claim/generation lineage.
UPDATE c24_private.worker_capabilities
   SET payload=NULL
 WHERE completed_at IS NOT NULL AND payload IS NOT NULL;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scrub_expired_capabilities(p_limit integer)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE scrubbed integer;
BEGIN
    IF p_limit IS NULL OR p_limit<1 OR p_limit>500 THEN
        RAISE EXCEPTION 'invalid capability scrub batch size' USING ERRCODE='22023';
    END IF;
    WITH expired AS MATERIALIZED (
        SELECT capability_id
          FROM c24_private.worker_capabilities
         WHERE payload IS NOT NULL AND consumed_at IS NULL
           AND completed_at IS NULL AND expires_at<=clock_timestamp()
         ORDER BY expires_at,capability_id
         LIMIT p_limit
         FOR UPDATE SKIP LOCKED
    )
    UPDATE c24_private.worker_capabilities AS capability
       SET payload=NULL
      FROM expired
     WHERE capability.capability_id=expired.capability_id
       AND capability.payload IS NOT NULL
       AND capability.consumed_at IS NULL
       AND capability.completed_at IS NULL
       AND capability.expires_at<=clock_timestamp();
    GET DIAGNOSTICS scrubbed=ROW_COUNT;
    RETURN scrubbed;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scrub_expired_capabilities(integer)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scrub_expired_capabilities(integer)
    TO postgres;

COMMENT ON FUNCTION c24_private.c24_worker_scrub_expired_capabilities(integer) IS
    'Bounded erasure of expired, unused worker capability payloads; retains unique replay tombstones and hashes.';
