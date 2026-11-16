SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Replace the SQL089 source purge and sweep so terminal soft-deleted
-- messages remain eligible. Evidence of hiding a row is not evidence that
-- its content or copies were destroyed. The existing purge marker, verified
-- server receipt time, lineage, policy, hold, and activity checks still apply.

CREATE OR REPLACE FUNCTION public.c24_purge_source(p_org uuid,p_message uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE m public.messages%ROWTYPE; p public.gdpr_policies%ROWTYPE; conv uuid; subj uuid;
BEGIN
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'Content retention requires read committed isolation' USING ERRCODE='25000';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 SELECT conversation_id INTO conv FROM public.messages WHERE organization_id=p_org AND id=p_message;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM 1 FROM public.conversations WHERE organization_id=p_org AND id=conv FOR UPDATE;
 SELECT * INTO m FROM public.messages WHERE organization_id=p_org AND id=p_message FOR UPDATE;
 IF NOT FOUND OR m.conversation_id IS DISTINCT FROM conv
    OR m.content_purged_at IS NOT NULL OR m.c24_received_at IS NULL OR m.c24_original_subject_id IS NULL
    OR (m.direction='inbound' AND (m.status<>'handled' OR m.wam_id IS NULL))
    OR (m.direction='outbound' AND m.status NOT IN ('sent','delivered','read','failed'))
    OR m.direction NOT IN ('inbound','outbound') THEN RETURN false; END IF;
 SELECT contact_id INTO subj FROM public.conversations WHERE organization_id=p_org AND id=conv;
 IF subj IS DISTINCT FROM m.c24_original_subject_id THEN RETURN false; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org AND category='messages_content'
  AND approved_at<=clock_timestamp() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed
    OR p.retention_days IS NULL OR p.retention_days<1 OR p.retention_days>90
    OR m.c24_received_at>clock_timestamp()-make_interval(days=>p.retention_days) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
  AND h.category='messages_content' AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=subj)
   OR (h.scope='conversation' AND h.scope_id=conv) OR (h.scope='record' AND h.scope_id=p_message))
  AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e WHERE e.organization_id=h.organization_id
   AND e.hold_id=h.hold_id AND e.kind='released')) THEN RETURN false; END IF;
 -- Unknown-lineage inbox, active generation, delivery, or canonical copies
 -- make even an otherwise terminal source ineligible, never guessed away.
 IF EXISTS(SELECT 1 FROM public.meta_webhook_inbox i WHERE i.organization_id=p_org
       AND (i.payload IS DISTINCT FROM '{}'::jsonb OR i.last_error IS NOT NULL)
       AND (EXISTS(SELECT 1 FROM public.inbound_worker_receipts r WHERE r.organization_id=p_org
          AND r.inbox_id=i.id AND r.message_id=p_message)
         OR (m.wam_id IS NOT NULL AND jsonb_path_exists(i.payload,
             '$.** ? (@.id == $v || @.mid == $v)',
             jsonb_build_object('v',to_jsonb(CASE WHEN left(m.wam_id,3)='ig:'
                  THEN substr(m.wam_id,4) ELSE m.wam_id END))))))
    OR EXISTS(SELECT 1 FROM public.messages WHERE organization_id=p_org AND conversation_id=conv
       AND status IN ('processing','queued','sending_ambiguous','received_pending_ai'))
    OR EXISTS(SELECT 1 FROM public.message_delivery_attempts a JOIN public.messages x ON x.id=a.message_id
       WHERE x.organization_id=p_org AND x.conversation_id=conv AND a.status NOT IN ('sent','delivered','read','completed'))
    OR EXISTS(SELECT 1 FROM public.usage_events WHERE organization_id=p_org
       AND ((event_type='system.provider_attempt' AND metadata->>'accounting_status'='unresolved')
         OR (event_type='system.quota_reservation' AND metadata->>'state'='reserved')))
    OR EXISTS(SELECT 1 FROM public.simulation_requests WHERE organization_id=p_org
       AND (status='reserved' OR (response IS NOT NULL AND c24_source_message_id IS NULL)))
    OR (m.billed_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.simulation_requests
       WHERE organization_id=p_org AND c24_source_message_id=p_message
        AND c24_conversation_id=conv AND c24_subject_id=subj AND status='completed')) THEN RETURN false; END IF;
 UPDATE public.messages SET content_purged_at=clock_timestamp(),content_purge_cause='retention',
  content='{}'::jsonb,content_text=NULL,ai_reply_cache=NULL,error_title=NULL,error_details=NULL
  WHERE organization_id=p_org AND id=p_message;
 UPDATE public.simulation_requests SET response='{}'::jsonb
  WHERE organization_id=p_org AND c24_source_message_id=p_message AND c24_conversation_id=conv
   AND c24_subject_id=subj AND status='completed';
 UPDATE public.event_log SET testo_originale='',risposta_ai='',dettagli='{}'::jsonb
  WHERE organization_id=p_org AND source_id=p_message;
 UPDATE public.message_delivery_attempts SET error_details=NULL WHERE message_id=p_message;
 UPDATE public.outbound_dedup SET response_text=NULL WHERE organization_id=p_org AND message_id=p_message;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.c24_purge_source(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;

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
      AND i.c24_completed_at<=clock_timestamp()-make_interval(days=>p.retention_days)
    UNION ALL
    SELECT 2::smallint,m.organization_id,m.id FROM public.messages m JOIN policy p
      ON p.organization_id=m.organization_id AND p.category='messages_content'
     WHERE p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND p.retention_days BETWEEN 1 AND 90
      AND m.content_purged_at IS NULL AND m.c24_received_at IS NOT NULL
      AND m.c24_original_subject_id IS NOT NULL
      AND ((m.direction='inbound' AND m.status='handled') OR
           (m.direction='outbound' AND m.status IN ('sent','delivered','read','failed')))
      AND m.c24_received_at<=clock_timestamp()-make_interval(days=>p.retention_days)
    UNION ALL
    SELECT 3::smallint,s.organization_id,s.conversation_id FROM public.booking_conversation_states s
     JOIN public.messages m ON m.organization_id=s.organization_id AND m.conversation_id=s.conversation_id
      AND m.id=s.source_message_id AND m.c24_original_subject_id=s.subject_id AND m.direction='inbound'
     JOIN policy p ON p.organization_id=s.organization_id AND p.category='booking_conversation_content'
     WHERE p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND p.retention_days BETWEEN 1 AND 30 AND m.c24_received_at IS NOT NULL
      AND m.c24_received_at<=clock_timestamp()-make_interval(days=>p.retention_days)
    UNION ALL
    SELECT 4::smallint,r.organization_id,r.request_id FROM public.booking_conversation_requests r
     JOIN policy p ON p.organization_id=r.organization_id AND p.category='technical_receipts'
     WHERE p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND p.retention_days BETWEEN 1 AND 90 AND r.c24_concluded_at IS NOT NULL
      AND r.c24_concluded_at<=clock_timestamp()-make_interval(days=>p.retention_days)
    UNION ALL
    SELECT DISTINCT 5::smallint,x.organization_id,x.conversation_id FROM (
      SELECT organization_id,conversation_id,fenced_at AS occurred_at FROM public.c24_source_fences
      UNION ALL SELECT organization_id,conversation_id,expired_at FROM public.c24_idle_watermarks
     ) x JOIN policy p ON p.organization_id=x.organization_id AND p.category='technical_receipts'
     WHERE p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND p.retention_days BETWEEN 1 AND 90
      AND x.occurred_at<=clock_timestamp()-make_interval(days=>p.retention_days)
    UNION ALL
    SELECT 6::smallint,a.organization_id,a.audit_id FROM public.c24_suppression_lifecycle_audit a
     JOIN policy p ON p.organization_id=a.organization_id AND p.category='technical_receipts'
     WHERE p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND p.retention_days BETWEEN 1 AND 90
      AND a.occurred_at<=clock_timestamp()-make_interval(days=>p.retention_days)
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
     WHEN 5 THEN applied:=public.c24_cleanup_suppression(target.organization_id,target.record_id);
     WHEN 6 THEN applied:=public.c24_cleanup_suppression_audit(target.organization_id,target.record_id);
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
  EXIT WHEN cursor_row.phase=0;
  UPDATE public.c24_retention_sweep_cursor SET phase=0,
    last_organization_id='00000000-0000-0000-0000-000000000000'::uuid,
    last_record_id='00000000-0000-0000-0000-000000000000'::uuid WHERE scan_name='retention';
  SELECT * INTO STRICT cursor_row FROM public.c24_retention_sweep_cursor
   WHERE scan_name='retention' FOR UPDATE;
 END LOOP;
 RETURN safe;
END $$;
REVOKE ALL ON FUNCTION public.c24_retention_sweep()
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;
