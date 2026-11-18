SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Anchor each scan to a fixed clock horizon and skip the two suppression
-- phases deferred by SQL097. Neither change widens retention eligibility:
-- each target function rechecks current policy, holds, activity and lineage.
ALTER TABLE public.c24_retention_sweep_cursor
 ADD COLUMN IF NOT EXISTS scan_cutoff_at timestamptz;

CREATE OR REPLACE FUNCTION public.c24_retention_sweep() RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE cursor_row public.c24_retention_sweep_cursor%ROWTYPE; target record;
        attempted integer; pass integer; applied boolean; safe boolean:=true;
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
   WHERE oid='public.c24_retention_sweep_cursor'::regclass))
   OR NOT public.c24_retention_operator_allowed() THEN
  RAISE EXCEPTION 'Dedicated retention operator required' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Retention sweep requires read committed isolation' USING ERRCODE='25000'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('c24-retention-sweep',606));
 SELECT * INTO STRICT cursor_row FROM public.c24_retention_sweep_cursor
  WHERE scan_name='retention' FOR UPDATE;
 -- Retention dates select eligible records. Suppression event dates can
 -- only signal work deferred by SQL097; they authorize no mutation.
 IF EXISTS(
  WITH policy AS MATERIALIZED (
   SELECT DISTINCT ON (organization_id) organization_id,retention_days,
     erasure_allowed,legal_hold,valid_until
   FROM public.gdpr_policies
   WHERE approved_at<=clock_timestamp() AND category='technical_receipts'
   ORDER BY organization_id,approved_at DESC,version DESC
  )
  SELECT 1 FROM policy p
   WHERE p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
    AND p.retention_days BETWEEN 1 AND 90
    AND (EXISTS(SELECT 1 FROM public.c24_source_fences f
          WHERE f.organization_id=p.organization_id
           AND f.fenced_at<=clock_timestamp()-make_interval(days=>p.retention_days))
      OR EXISTS(SELECT 1 FROM public.c24_idle_watermarks w
          WHERE w.organization_id=p.organization_id
           AND w.expired_at<=clock_timestamp()-make_interval(days=>p.retention_days))
      OR EXISTS(SELECT 1 FROM public.c24_suppression_lifecycle_audit a
          WHERE a.organization_id=p.organization_id
           AND a.occurred_at<=clock_timestamp()-make_interval(days=>p.retention_days)))
 ) THEN safe:=false; END IF;
 -- Reset any pre-098 scan. Hotel erasure has a different cursor and protocol.
 IF cursor_row.scan_cutoff_at IS NULL OR cursor_row.phase>4 THEN
  UPDATE public.c24_retention_sweep_cursor SET phase=0,
   last_organization_id='00000000-0000-0000-0000-000000000000'::uuid,
   last_record_id='00000000-0000-0000-0000-000000000000'::uuid,
   scan_cutoff_at=clock_timestamp() WHERE scan_name='retention' RETURNING * INTO cursor_row;
 END IF;
 FOR pass IN 1..2 LOOP
  attempted:=0;
  FOR target IN
   WITH policy AS MATERIALIZED (
    SELECT DISTINCT ON (organization_id,category) organization_id,category,retention_days,
      erasure_allowed,legal_hold,valid_until
    FROM public.gdpr_policies WHERE approved_at<=clock_timestamp()
      AND category IN ('messages_content','booking_conversation_content','technical_receipts')
    ORDER BY organization_id,category,approved_at DESC,version DESC
   ), candidates AS (
    SELECT 1::smallint AS phase,i.organization_id,i.id AS record_id
     FROM public.meta_webhook_inbox i JOIN policy p ON p.organization_id=i.organization_id
      AND p.category='messages_content'
     WHERE p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND p.retention_days BETWEEN 1 AND 90 AND i.status='completed'
      AND i.c24_completed_at IS NOT NULL AND i.c24_payload_purged_at IS NULL
      AND (i.payload IS DISTINCT FROM '{}'::jsonb OR i.last_error IS NOT NULL)
      AND i.c24_completed_at<=cursor_row.scan_cutoff_at-make_interval(days=>p.retention_days)
    UNION ALL
    SELECT 2::smallint,m.organization_id,m.id FROM public.messages m JOIN policy p
      ON p.organization_id=m.organization_id AND p.category='messages_content'
     WHERE p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND p.retention_days BETWEEN 1 AND 90
      AND m.content_purged_at IS NULL AND m.c24_received_at IS NOT NULL
      AND m.c24_original_subject_id IS NOT NULL
      AND ((m.direction='inbound' AND m.status='handled') OR
           (m.direction='outbound' AND m.status IN ('sent','delivered','read','failed')))
      AND m.c24_received_at<=cursor_row.scan_cutoff_at-make_interval(days=>p.retention_days)
    UNION ALL
    SELECT 3::smallint,s.organization_id,s.conversation_id FROM public.booking_conversation_states s
     JOIN public.messages m ON m.organization_id=s.organization_id AND m.conversation_id=s.conversation_id
      AND m.id=s.source_message_id AND m.c24_original_subject_id=s.subject_id AND m.direction='inbound'
     JOIN policy p ON p.organization_id=s.organization_id AND p.category='booking_conversation_content'
     WHERE p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND p.retention_days BETWEEN 1 AND 30 AND m.c24_received_at IS NOT NULL
      AND m.c24_received_at<=cursor_row.scan_cutoff_at-make_interval(days=>p.retention_days)
    UNION ALL
    SELECT 4::smallint,r.organization_id,r.request_id FROM public.booking_conversation_requests r
     JOIN policy p ON p.organization_id=r.organization_id AND p.category='technical_receipts'
     WHERE p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND p.retention_days BETWEEN 1 AND 90 AND r.c24_concluded_at IS NOT NULL
      AND r.c24_concluded_at<=cursor_row.scan_cutoff_at-make_interval(days=>p.retention_days)
   )
   SELECT phase,organization_id,record_id FROM candidates
    WHERE (phase,organization_id,record_id)>
          (cursor_row.phase,cursor_row.last_organization_id,cursor_row.last_record_id)
    ORDER BY phase,organization_id,record_id LIMIT 100
  LOOP
   attempted:=attempted+1;
   BEGIN
    CASE target.phase
     WHEN 1 THEN applied:=public.c24_purge_inbox(target.organization_id,target.record_id);
     WHEN 2 THEN applied:=public.c24_purge_source(target.organization_id,target.record_id);
     WHEN 3 THEN applied:=public.c24_expire_idle(target.organization_id,target.record_id);
     WHEN 4 THEN applied:=public.c24_cleanup_receipt(target.organization_id,target.record_id);
    END CASE;
    IF NOT COALESCE(applied,false) THEN safe:=false; END IF;
   EXCEPTION WHEN object_not_in_prerequisite_state THEN
    -- A held/uncertain record is deferred. Other database failures propagate.
    safe:=false;
   END;
   UPDATE public.c24_retention_sweep_cursor SET phase=target.phase,
    last_organization_id=target.organization_id,last_record_id=target.record_id
    WHERE scan_name='retention';
  END LOOP;
  IF attempted>0 THEN RETURN safe; END IF;
  -- Start a fresh horizon only after reaching the end of the prior scan,
  -- including the empty-scan case. Otherwise new work could be missed.
  UPDATE public.c24_retention_sweep_cursor SET phase=0,
    last_organization_id='00000000-0000-0000-0000-000000000000'::uuid,
    last_record_id='00000000-0000-0000-0000-000000000000'::uuid,
    scan_cutoff_at=clock_timestamp() WHERE scan_name='retention' RETURNING * INTO cursor_row;
 END LOOP;
 UPDATE public.c24_retention_sweep_cursor SET scan_cutoff_at=NULL WHERE scan_name='retention';
 RETURN safe;
END $$;
REVOKE ALL ON FUNCTION public.c24_retention_sweep()
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;
