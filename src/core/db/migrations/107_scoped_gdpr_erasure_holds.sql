SET lock_timeout = '5s';
SET statement_timeout = '5min';

DO $hold_migration$
BEGIN
-- The backend may check only a persisted erase operation. Hold rows, review
-- identities and reasons remain private to the lifecycle owner.
CREATE OR REPLACE FUNCTION public.gdpr_erasure_hold_check(p_org uuid, p_operation uuid)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    targeted_scope text;
    targeted_subject uuid;
    h record;
    holder_subjects uuid[];
    unresolved boolean := false;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Erasure hold check requires read committed' USING ERRCODE = '25000';
    END IF;
    -- Check that the operation exists before taking a lock for the supplied
    -- organization. Read its scope again after any wait for a hold commit.
    PERFORM 1 FROM public.gdpr_operations o
      WHERE o.organization_id=p_org AND o.id=p_operation AND o.kind='erase';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Scoped erasure operation required' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
    SELECT o.scope,o.subject_id INTO targeted_scope,targeted_subject
      FROM public.gdpr_operations o
      WHERE o.organization_id=p_org AND o.id=p_operation AND o.kind='erase'
        AND o.scope IN ('organization','contact')
        AND o.status IN ('requested','blocked','running')
      FOR UPDATE;
    IF NOT FOUND OR ((targeted_scope='contact') <> (targeted_subject IS NOT NULL)) THEN
        RAISE EXCEPTION 'Pending scoped erasure operation required' USING ERRCODE = '42501';
    END IF;

    FOR h IN
        SELECT hold.category,hold.scope,hold.scope_id
          FROM public.gdpr_retention_holds hold
          WHERE hold.organization_id=p_org
            AND NOT EXISTS (
                SELECT 1 FROM public.gdpr_retention_hold_events e
                  WHERE e.organization_id=hold.organization_id AND e.hold_id=hold.hold_id
                    AND e.kind='released'
            )
    LOOP
        IF targeted_scope='organization' OR h.scope='organization' THEN RETURN 'held'; END IF;

        -- All candidates must be scoped to the exact tenant. Inconsistent
        -- links (including historical rows with no subject) are never treated
        -- as evidence that the held record belongs to a different person.
        SELECT array_agg(DISTINCT subjects.subject_id) INTO holder_subjects FROM (
            SELECT c.id AS subject_id FROM public.contacts c
              WHERE h.scope='subject' AND c.organization_id=p_org AND c.id=h.scope_id
            UNION ALL
            SELECT c.contact_id FROM public.conversations c
              WHERE c.organization_id=p_org AND c.id=h.scope_id
                AND (h.scope='conversation'
                  OR (h.scope='record' AND h.category='booking_conversation_content'))
            UNION ALL
            SELECT c.contact_id FROM public.messages m
              JOIN public.conversations c ON c.organization_id=m.organization_id AND c.id=m.conversation_id
              WHERE h.scope='record' AND h.category IN ('messages_content','transient_content')
                AND m.organization_id=p_org AND m.id=h.scope_id
            UNION ALL
            SELECT m.c24_original_subject_id FROM public.messages m
              WHERE h.scope='record' AND h.category IN ('messages_content','transient_content')
                AND m.organization_id=p_org AND m.id=h.scope_id AND m.c24_original_subject_id IS NOT NULL
            UNION ALL
            SELECT r.c24_subject_id FROM public.simulation_requests r
              WHERE h.scope='record' AND h.category IN ('booking_conversation_content','transient_content')
                AND r.organization_id=p_org AND r.request_id=h.scope_id
            UNION ALL
            SELECT r.original_subject_id FROM public.booking_conversation_requests r
              WHERE h.scope='record' AND h.category='technical_receipts'
                AND r.organization_id=p_org AND r.request_id=h.scope_id
            UNION ALL
            SELECT f.subject_id FROM public.c24_source_fences f
              WHERE h.scope='record' AND h.category='technical_receipts' AND f.organization_id=p_org
                AND h.scope_id IN (f.request_id,f.source_message_id)
            UNION ALL
            SELECT w.subject_id FROM public.c24_idle_watermarks w
              WHERE h.scope='record' AND h.category='technical_receipts'
                AND w.organization_id=p_org AND w.conversation_id=h.scope_id
            UNION ALL
            SELECT c.id FROM public.contacts c
              WHERE h.scope='record' AND h.category='contact_content'
                AND c.organization_id=p_org AND c.id=h.scope_id
            UNION ALL
            SELECT r.contact_id FROM public.reviews r
              WHERE h.scope='record' AND h.category='contact_content'
                AND r.organization_id=p_org AND r.id=h.scope_id
            UNION ALL
            SELECT b.contact_id FROM public.bookings b
              WHERE h.scope='record' AND h.category IN (
                  'contact_content','completed_bookings_and_stays','booking_stay_content')
                AND b.organization_id=p_org AND b.id=h.scope_id
            UNION ALL
            SELECT s.original_subject_id FROM public.booking_stays s
              WHERE h.scope='record' AND h.category IN (
                  'contact_content','completed_bookings_and_stays','booking_stay_content')
                AND s.organization_id=p_org AND s.booking_id=h.scope_id
            UNION ALL
            SELECT s.original_subject_id FROM public.booking_stays s
              JOIN public.booking_allocation_requests a
                  ON a.organization_id=s.organization_id AND a.booking_id=s.booking_id
              WHERE h.scope='record' AND h.category='booking_stay_content'
                AND s.organization_id=p_org AND a.request_id=h.scope_id
        ) subjects;
        IF targeted_subject=ANY(holder_subjects) THEN RETURN 'held'; END IF;
        IF holder_subjects IS NULL OR cardinality(holder_subjects) <> 1
          OR holder_subjects[1] IS NULL THEN
            unresolved := true;
        ELSIF h.category IN ('booking_conversation_content','simulator_responses') THEN
            -- Per-process simulator histories lack verified individual
            -- lineage and are cleared at organization scope after erasure.
            unresolved := true;
        ELSIF h.category IN ('messages_content','transient_content','contact_content') AND EXISTS (
            SELECT 1 FROM public.meta_webhook_inbox i
              WHERE i.organization_id=p_org AND i.status='completed'
                AND (i.payload <> '{}'::jsonb OR i.last_error IS NOT NULL)
        ) THEN
            -- A raw multi-subject envelope and its diagnostics may be changed
            -- by contact erasure. An unrelated relational hold does not prove
            -- that all copies in those envelopes can safely be modified.
            unresolved := true;
        END IF;
    END LOOP;
    RETURN CASE WHEN unresolved THEN 'unverified' ELSE 'clear' END;
END;
$$;
REVOKE ALL ON FUNCTION public.gdpr_erasure_hold_check(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.gdpr_erasure_hold_check(uuid,uuid) TO service_role;
END
$hold_migration$;
