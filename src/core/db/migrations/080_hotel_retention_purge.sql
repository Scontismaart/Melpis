-- Owner-only retention checks use immutable completion evidence, the released
-- allocation archive, and civil time. No runtime scheduling or grant is enabled.
CREATE OR REPLACE FUNCTION public.hotel_stay_purge_eligible(p_org uuid,p_booking uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT EXISTS(SELECT 1 FROM public.booking_stays s
 JOIN public.bookings b ON b.organization_id=s.organization_id AND b.id=s.booking_id
 JOIN public.booking_allocation_sets a ON a.organization_id=s.organization_id AND a.booking_id=s.booking_id
 JOIN public.booking_stay_completion_evidence e ON e.organization_id=s.organization_id AND e.booking_id=s.booking_id
 WHERE s.organization_id=p_org AND s.booking_id=p_booking AND b.stato='completata'
 AND b.completata_at IS NOT NULL AND b.no_show_at IS NULL
 AND a.state='cancelled' AND a.temporal_kind='civil_nights' AND a.timezone=e.timezone
 AND (a.arrival_date,a.departure_date) IS NOT DISTINCT FROM (s.arrival_date,s.departure_date)
 AND a.ends_at<=e.completed_at
 AND public.hotel_completed_stay_retention_eligible(e.completed_at,e.timezone,clock_timestamp())
 AND NOT EXISTS(SELECT 1 FROM public.booking_resource_allocations r WHERE r.organization_id=p_org AND r.booking_id=p_booking)
 AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
  AND h.category='booking_stay_content' AND (h.scope='organization'
   OR (h.scope='subject' AND h.scope_id=s.original_subject_id)
   OR (h.scope='conversation' AND h.scope_id=s.original_conversation_id)
   OR (h.scope='record' AND (h.scope_id=s.booking_id OR EXISTS(SELECT 1 FROM public.booking_allocation_requests r
      WHERE r.organization_id=p_org AND r.booking_id=p_booking AND r.request_id=h.scope_id))))
  AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events v
   WHERE v.organization_id=h.organization_id AND v.hold_id=h.hold_id AND v.kind='released')))
$$;
REVOKE ALL ON FUNCTION public.hotel_stay_purge_eligible(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.booking_allocation_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE marker public.booking_stay_purge_markers%ROWTYPE; policy public.gdpr_policies%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM public.bookings WHERE organization_id=OLD.organization_id AND id=OLD.booking_id) THEN RETURN OLD; END IF;
 IF TG_OP<>'DELETE' THEN RAISE EXCEPTION 'Allocation receipt is immutable' USING ERRCODE='42501'; END IF;
 SELECT * INTO marker FROM public.booking_stay_purge_markers WHERE transaction_id=txid_current()
   AND organization_id=OLD.organization_id AND booking_id=OLD.booking_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Allocation receipt is immutable' USING ERRCODE='42501'; END IF;
 SELECT * INTO policy FROM public.gdpr_policies WHERE organization_id=OLD.organization_id
   AND category='booking_stay_content' AND approved_at<=now() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR policy.version<>marker.policy_version OR policy.legal_hold OR NOT policy.erasure_allowed OR policy.valid_until<=now()
   OR EXISTS(SELECT 1 FROM public.booking_resource_allocations WHERE organization_id=OLD.organization_id AND booking_id=OLD.booking_id)
   OR EXISTS(SELECT 1 FROM public.booking_allocation_sets WHERE organization_id=OLD.organization_id AND booking_id=OLD.booking_id AND state='active')
   OR NOT EXISTS(SELECT 1 FROM public.booking_stays s JOIN public.bookings b ON b.organization_id=s.organization_id AND b.id=s.booking_id
      WHERE s.organization_id=OLD.organization_id AND s.booking_id=OLD.booking_id AND s.original_subject_id=marker.original_subject_id
      AND ((marker.purpose='erasure' AND b.stato IN ('cancellata','cancellato','rifiutata','no_show','completata')) OR (marker.purpose='retention' AND public.hotel_stay_purge_eligible(OLD.organization_id,OLD.booking_id)))) THEN
  RAISE EXCEPTION 'Scoped purge evidence unavailable' USING ERRCODE='55000'; END IF;
 IF marker.purpose='erasure' AND NOT EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers e JOIN public.gdpr_operations o
   ON o.organization_id=e.organization_id AND o.id=e.operation_id WHERE e.organization_id=OLD.organization_id
   AND e.operation_id=marker.operation_id AND o.kind='erase' AND o.status IN ('requested','running','blocked','partial')
   AND o.scope=e.scope AND o.subject_id IS NOT DISTINCT FROM e.subject_id
   AND (e.scope='organization' OR (e.scope='contact' AND e.subject_id=marker.original_subject_id))) THEN
  RAISE EXCEPTION 'Erasure barrier unavailable' USING ERRCODE='55000'; END IF;
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION public.booking_allocation_immutable() FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.booking_stay_admin_purge(p_org uuid,p_booking uuid,p_purpose text,p_operation uuid,p_policy_version text)
RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE stay public.booking_stays%ROWTYPE; booking public.bookings%ROWTYPE; pol public.gdpr_policies%ROWTYPE; cat text; count_deleted integer;
 a public.booking_allocation_sets%ROWTYPE; e public.booking_stay_completion_evidence%ROWTYPE;
 claim record; claim_count integer; observed timestamptz := clock_timestamp();
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='public.booking_stay_purge_markers'::regclass)) THEN RAISE EXCEPTION 'Hotel purge requires lifecycle owner' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation') <> 'read committed' THEN RAISE EXCEPTION 'Hotel purge requires read committed isolation' USING ERRCODE='25000'; END IF;
 IF p_purpose NOT IN ('erasure','retention') OR (p_purpose='erasure' AND p_operation IS NULL)
  OR (p_purpose='retention' AND p_operation IS NOT NULL) THEN RAISE EXCEPTION 'Invalid purge scope' USING ERRCODE='55000'; END IF;
 PERFORM 1 FROM public.organizations WHERE id=p_org FOR NO KEY UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Organization unavailable' USING ERRCODE='55000'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 IF p_purpose='erasure' THEN
  PERFORM 1 FROM public.gdpr_operations WHERE organization_id=p_org AND id=p_operation
   AND kind='erase' AND status IN ('requested','running','blocked','partial') FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Erasure operation unavailable' USING ERRCODE='55000'; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM public.external_booking_sync WHERE organization_id=p_org AND provider='google' AND calendar_dispatch_state='possibly_sent')
  OR EXISTS(SELECT 1 FROM public.usage_events WHERE organization_id=p_org AND ((event_type='system.provider_attempt' AND metadata->>'accounting_status'='unresolved') OR (event_type='system.quota_reservation' AND metadata->>'state'='reserved')))
  OR EXISTS(SELECT 1 FROM public.messages WHERE organization_id=p_org AND status IN ('processing','queued','sending_ambiguous','received_pending_ai'))
  OR EXISTS(SELECT 1 FROM public.meta_webhook_inbox WHERE organization_id=p_org AND status NOT IN ('completed','failed')) THEN
  RAISE EXCEPTION 'Unresolved external or in-flight work' USING ERRCODE='55000'; END IF;
 SELECT * INTO booking FROM public.bookings WHERE organization_id=p_org AND id=p_booking FOR UPDATE;
 SELECT * INTO stay FROM public.booking_stays WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 IF stay.booking_id IS NULL OR booking.id IS NULL OR (p_purpose='erasure' AND booking.stato NOT IN ('cancellata','cancellato','rifiutata','no_show','completata'))
 THEN
  RAISE EXCEPTION 'Terminal stay required' USING ERRCODE='55000'; END IF;
 FOR cat IN SELECT unnest(CASE WHEN p_purpose='erasure' THEN ARRAY['booking_stay_content','contact_content','cutover'] ELSE ARRAY['booking_stay_content'] END) LOOP
  SELECT * INTO pol FROM public.gdpr_policies WHERE organization_id=p_org AND category=cat AND approved_at<=now()
    ORDER BY approved_at DESC,version DESC LIMIT 1;
  IF NOT FOUND OR pol.legal_hold OR NOT pol.erasure_allowed OR pol.valid_until<=now()
    OR (cat='booking_stay_content' AND pol.version<>p_policy_version) THEN RAISE EXCEPTION 'Approved current policy required' USING ERRCODE='55000'; END IF;
 END LOOP;
 SELECT * INTO pol FROM public.gdpr_policies WHERE organization_id=p_org AND category='booking_stay_content' AND approved_at<=now() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF p_purpose='erasure' AND NOT EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers e JOIN public.gdpr_operations o
   ON o.organization_id=e.organization_id AND o.id=e.operation_id WHERE e.organization_id=p_org AND e.operation_id=p_operation
   AND o.kind='erase' AND o.status IN ('requested','running','blocked','partial')
   AND o.scope=e.scope AND o.subject_id IS NOT DISTINCT FROM e.subject_id
   AND (e.scope='organization' OR (e.scope='contact' AND e.subject_id=stay.original_subject_id))) THEN
  RAISE EXCEPTION 'Scoped erasure barrier required' USING ERRCODE='55000'; END IF;
 IF p_purpose='retention' AND NOT public.hotel_stay_purge_eligible(p_org,p_booking) THEN
  RAISE EXCEPTION 'Completed archived stay retention evidence required' USING ERRCODE='55000'; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
  AND h.category='booking_stay_content' AND (h.scope='organization'
   OR (h.scope='subject' AND h.scope_id=stay.original_subject_id)
   OR (h.scope='conversation' AND h.scope_id=stay.original_conversation_id)
   OR (h.scope='record' AND (h.scope_id=p_booking OR EXISTS(SELECT 1 FROM public.booking_allocation_requests r
      WHERE r.organization_id=p_org AND r.booking_id=p_booking AND r.request_id=h.scope_id))))
  AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events v
   WHERE v.organization_id=h.organization_id AND v.hold_id=h.hold_id AND v.kind='released')) THEN
  RAISE EXCEPTION 'Stay content held' USING ERRCODE='55000'; END IF;
 -- Erasure may release a completed, ended stay in this same transaction,
 -- after the operation, policy and all hold checks have succeeded. Retention
 -- still requires a separately archived stay and its twelve-month evidence.
 SELECT * INTO a FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 IF p_purpose='erasure' AND a.state='active' THEN
  SELECT * INTO e FROM public.booking_stay_completion_evidence WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
  IF booking.stato<>'completata' OR booking.completata_at IS NULL OR booking.no_show_at IS NOT NULL
   OR e.booking_id IS NULL OR a.temporal_kind<>'civil_nights' OR a.timezone<>e.timezone
   OR (a.arrival_date,a.departure_date) IS DISTINCT FROM (stay.arrival_date,stay.departure_date)
   OR a.ends_at>e.completed_at OR a.ends_at>observed THEN
   RAISE EXCEPTION 'Completed stay release evidence unavailable' USING ERRCODE='55000'; END IF;
  SELECT count(*) INTO claim_count FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking;
  IF claim_count<1 OR claim_count>128 THEN RAISE EXCEPTION 'Bounded allocation claims required' USING ERRCODE='55000'; END IF;
  FOR claim IN SELECT * FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking ORDER BY resource_id FOR UPDATE LOOP
   PERFORM pg_advisory_xact_lock(hashtextextended('allocation-resource|'||p_org::text||'|'||claim.resource_id,725));
   IF (claim.state,claim.allocation_revision,claim.catalog_revision,claim.starts_at,claim.ends_at)
    IS DISTINCT FROM ('active',a.allocation_revision,a.catalog_revision,a.starts_at,a.ends_at) THEN
    RAISE EXCEPTION 'Allocation claims changed' USING ERRCODE='55000'; END IF;
  END LOOP;
  DELETE FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking;
  UPDATE public.booking_allocation_sets SET state='cancelled',allocation_revision=allocation_revision+1,updated_at=observed
   WHERE organization_id=p_org AND booking_id=p_booking;
 END IF;
 IF EXISTS(SELECT 1 FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking)
  OR EXISTS(SELECT 1 FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking AND state='active') THEN
  RAISE EXCEPTION 'Terminal released stay required' USING ERRCODE='55000'; END IF;
 SELECT count(*) INTO count_deleted FROM public.booking_allocation_requests WHERE organization_id=p_org AND booking_id=p_booking;
 INSERT INTO public.booking_stay_purge_fences(organization_id,booking_id,original_subject_id,purpose,operation_id,policy_version,receipt_count) VALUES(p_org,p_booking,stay.original_subject_id,p_purpose,p_operation,p_policy_version,count_deleted);
 INSERT INTO public.booking_stay_tombstones(organization_id,request_id,booking_id,original_subject_id,purpose,operation_id,policy_version)
   SELECT organization_id,request_id,booking_id,stay.original_subject_id,p_purpose,p_operation,p_policy_version FROM public.booking_allocation_requests WHERE organization_id=p_org AND booking_id=p_booking;
 INSERT INTO public.booking_stay_purge_markers VALUES(txid_current(),p_org,p_booking,stay.original_subject_id,p_purpose,p_operation,p_policy_version);
 DELETE FROM public.booking_allocation_requests WHERE organization_id=p_org AND booking_id=p_booking;
 GET DIAGNOSTICS count_deleted = ROW_COUNT;
 DELETE FROM public.booking_stays WHERE organization_id=p_org AND booking_id=p_booking;
 DELETE FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking AND state='cancelled';
 IF p_purpose IN ('retention','erasure') THEN
  INSERT INTO public.booking_completion_erasure_markers VALUES(txid_current(),p_org,p_booking);
  DELETE FROM public.booking_stay_completion_evidence WHERE organization_id=p_org AND booking_id=p_booking;
  DELETE FROM public.booking_completion_erasure_markers WHERE transaction_id=txid_current() AND organization_id=p_org AND booking_id=p_booking;
 END IF;
 DELETE FROM public.booking_stay_purge_markers WHERE transaction_id=txid_current() AND organization_id=p_org AND booking_id=p_booking;
 INSERT INTO public.booking_stay_purge_audit(organization_id,booking_id,purpose,operation_id,policy_version,receipt_count,session_principal,transaction_id)
 VALUES(p_org,p_booking,p_purpose,p_operation,p_policy_version,count_deleted,session_user,txid_current());
 RETURN count_deleted;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_admin_purge(uuid,uuid,text,uuid,text) FROM PUBLIC,anon,authenticated,service_role;