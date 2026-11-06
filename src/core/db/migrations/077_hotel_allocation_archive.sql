-- Owner-only, one-stay administrative archive. No runtime caller is enabled.
-- This releases expired occupancy, preserving the booking, stay and request
-- history. A completed booking remains completed; cancelled is the existing
-- allocation-set representation for a released set.
CREATE OR REPLACE FUNCTION public.archive_completed_hotel_allocation(p_org uuid,p_booking uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE b public.bookings%ROWTYPE; s public.booking_stays%ROWTYPE;
 a public.booking_allocation_sets%ROWTYPE; e public.booking_stay_completion_evidence%ROWTYPE;
 p public.gdpr_policies%ROWTYPE; claim record; claim_count integer; observed timestamptz := clock_timestamp();
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid(
  (SELECT relowner FROM pg_class WHERE oid='public.booking_stay_completion_evidence'::regclass)
 ) THEN RAISE EXCEPTION 'Hotel archive requires the lifecycle owner' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'Hotel archive requires read committed isolation' USING ERRCODE='25000';
 END IF;
 IF p_org IS NULL OR p_booking IS NULL THEN RAISE EXCEPTION 'Archive scope required' USING ERRCODE='55000'; END IF;
 PERFORM 1 FROM public.organizations WHERE id=p_org FOR NO KEY UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Organization unavailable' USING ERRCODE='55000'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 IF EXISTS(SELECT 1 FROM public.external_booking_sync WHERE organization_id=p_org AND provider='google' AND calendar_dispatch_state='possibly_sent')
  OR EXISTS(SELECT 1 FROM public.usage_events WHERE organization_id=p_org AND ((event_type='system.provider_attempt' AND metadata->>'accounting_status'='unresolved') OR (event_type='system.quota_reservation' AND metadata->>'state'='reserved')))
  OR EXISTS(SELECT 1 FROM public.messages WHERE organization_id=p_org AND status IN ('processing','queued','sending_ambiguous','received_pending_ai'))
  OR EXISTS(SELECT 1 FROM public.meta_webhook_inbox WHERE organization_id=p_org AND status NOT IN ('completed','failed')) THEN
  RAISE EXCEPTION 'Unresolved external or in-flight work' USING ERRCODE='55000'; END IF;
 SELECT * INTO b FROM public.bookings WHERE organization_id=p_org AND id=p_booking FOR UPDATE;
 SELECT * INTO s FROM public.booking_stays WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 SELECT * INTO a FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 SELECT * INTO e FROM public.booking_stay_completion_evidence WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 IF b.id IS NULL OR s.booking_id IS NULL OR a.booking_id IS NULL OR e.booking_id IS NULL
  OR b.stato<>'completata' OR b.completata_at IS NULL OR b.no_show_at IS NOT NULL
  OR a.temporal_kind<>'civil_nights' OR a.timezone<>e.timezone
  OR (a.arrival_date,a.departure_date) IS DISTINCT FROM (s.arrival_date,s.departure_date)
  OR a.ends_at>e.completed_at OR a.ends_at>observed
  OR NOT public.hotel_completed_stay_retention_eligible(e.completed_at,e.timezone,observed) THEN
  RAISE EXCEPTION 'Completed stay archive evidence unavailable' USING ERRCODE='55000'; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org AND category='booking_stay_content'
  AND approved_at<=now() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.legal_hold OR NOT p.erasure_allowed OR p.valid_until<=now() THEN
  RAISE EXCEPTION 'Approved current policy required' USING ERRCODE='55000'; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers WHERE organization_id=p_org AND (scope='organization' OR subject_id=s.original_subject_id)) THEN
  RAISE EXCEPTION 'Erasure barrier requires separate disposition' USING ERRCODE='55000'; END IF;
 IF a.state='cancelled' AND NOT EXISTS(SELECT 1 FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking) THEN RETURN false; END IF;
 IF a.state<>'active' THEN RAISE EXCEPTION 'Active allocation required' USING ERRCODE='55000'; END IF;
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
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.archive_completed_hotel_allocation(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
