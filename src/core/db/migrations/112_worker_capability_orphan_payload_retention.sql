-- Transient capability authority is bounded by the existing expiry. SQL093
-- consumes a capability and creates its context in the same transaction;
-- the deferred constraint prevents that transaction committing unfinished.
-- A previously persisted orphan with no matching context cannot resume the
-- private worker. Preserve its immutable proof and scrub only the payload.
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
        SELECT capability.capability_id
          FROM c24_private.worker_capabilities AS capability
         WHERE capability.payload IS NOT NULL AND capability.completed_at IS NULL
           AND capability.expires_at<=clock_timestamp()
           AND NOT EXISTS (
               SELECT 1 FROM c24_private.worker_context AS context
               WHERE context.capability_id=capability.capability_id
                  OR (context.backend_pid=capability.consumed_backend_pid
                      AND context.transaction_id=capability.consumed_transaction_id))
         ORDER BY capability.expires_at,capability.capability_id
         LIMIT p_limit
         FOR UPDATE OF capability SKIP LOCKED
    )
    UPDATE c24_private.worker_capabilities AS capability
       SET payload=NULL
      FROM expired
     WHERE capability.capability_id=expired.capability_id
       AND capability.payload IS NOT NULL
       AND capability.completed_at IS NULL
       AND capability.expires_at<=clock_timestamp()
       AND NOT EXISTS (
           SELECT 1 FROM c24_private.worker_context AS context
           WHERE context.capability_id=capability.capability_id
              OR (context.backend_pid=capability.consumed_backend_pid
                  AND context.transaction_id=capability.consumed_transaction_id));
    GET DIAGNOSTICS scrubbed=ROW_COUNT;
    RETURN scrubbed;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scrub_expired_capabilities(integer)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scrub_expired_capabilities(integer)
    TO postgres;

COMMENT ON FUNCTION c24_private.c24_worker_scrub_expired_capabilities(integer) IS
    'Bounded scrubbing of expired orphan or unused worker capability payloads; protects every matching context and preserves lineage and replay hashes.';
