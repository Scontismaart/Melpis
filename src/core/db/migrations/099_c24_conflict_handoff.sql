SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Keep the disposition on the existing source-scoped claim. It follows its
-- source message and conversation through their existing cascading FKs.
ALTER TABLE public.booking_worker_claims
    ADD COLUMN IF NOT EXISTS handoff_queued boolean NOT NULL DEFAULT false;

-- A later begin-job upsert rotates generation. Once the trusted application
-- queues handoff, the old proposal cannot be claimed again.
CREATE OR REPLACE FUNCTION c24_private.c24_worker_preserve_handoff()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog,pg_temp
AS $$
BEGIN
    IF OLD.handoff_queued THEN
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_preserve_handoff()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
DROP TRIGGER IF EXISTS c24_worker_preserve_handoff ON public.booking_worker_claims;
CREATE TRIGGER c24_worker_preserve_handoff
BEFORE UPDATE OF generation ON public.booking_worker_claims
FOR EACH ROW EXECUTE FUNCTION c24_private.c24_worker_preserve_handoff();

CREATE OR REPLACE FUNCTION c24_private.c24_worker_next_jobs(
    p_after_message uuid,
    p_limit integer
) RETURNS TABLE(job_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
        RAISE EXCEPTION 'dedicated booking worker required' USING ERRCODE='42501';
    END IF;
    IF p_limit IS NULL OR p_limit<1 OR p_limit>100 THEN
        RAISE EXCEPTION 'invalid booking worker batch size' USING ERRCODE='22023';
    END IF;
    RETURN QUERY
    SELECT s.source_message_id
      FROM public.booking_conversation_states AS s
      JOIN public.booking_conversation_requests AS p
        ON p.organization_id=s.organization_id
       AND p.conversation_id=s.conversation_id
       AND p.state_generation=s.state_generation
       AND p.action='proposal'
       AND p.applied_revision=s.revision-1
      JOIN public.messages AS m
        ON m.organization_id=s.organization_id
       AND m.id=s.source_message_id
       AND m.direction='inbound'
       AND m.deleted_at IS NULL
      JOIN public.inbound_worker_receipts AS r
        ON r.organization_id=s.organization_id
       AND r.message_id=s.source_message_id
      WHERE s.snapshot->>'intent'='booking'
        AND jsonb_typeof(s.snapshot->'confirmation')='object'
        AND (p_after_message IS NULL OR s.source_message_id>p_after_message)
        AND NOT EXISTS (
            SELECT 1 FROM public.booking_worker_claims AS claim
             WHERE claim.organization_id=s.organization_id
               AND claim.message_id=p.source_message_id
               AND claim.handoff_queued
        )
        AND NOT EXISTS (
            SELECT 1 FROM public.bookings AS b
             WHERE b.organization_id=s.organization_id
               AND b.source_message_id=p.source_message_id::text
        )
      ORDER BY s.source_message_id
      LIMIT p_limit;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_next_jobs(uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_next_jobs(uuid,integer)
    TO melpis_c24_booking_worker;
