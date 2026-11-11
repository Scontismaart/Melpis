-- C2.4 hotel lifecycle completion. Runtime entry points remain owner/capability
-- gated; this migration does not enable a scheduler, worker or remote policy.

-- Existing stays are preserved as inbound lineage. Only new, explicit owner
-- bookings may omit a conversation/message source, and only as a paired NULL.
ALTER TABLE public.booking_stays
  ADD COLUMN IF NOT EXISTS source_kind text NOT NULL DEFAULT 'inbound';
ALTER TABLE public.booking_stays
  ALTER COLUMN original_conversation_id DROP NOT NULL,
  ALTER COLUMN source_message_id DROP NOT NULL;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.booking_stays'::regclass
                 AND conname='booking_stays_source_kind_shape') THEN
  ALTER TABLE public.booking_stays ADD CONSTRAINT booking_stays_source_kind_shape CHECK (
   (source_kind='inbound' AND original_conversation_id IS NOT NULL AND source_message_id IS NOT NULL)
   OR (source_kind='owner' AND original_conversation_id IS NULL AND source_message_id IS NULL));
 END IF;
END $$;

-- Persist the transaction identity of allocation receipts. The value is
-- checked by deferred stay constraints, so an allocation and its stay cannot
-- be assembled from separate transactions or replayed later.
ALTER TABLE public.booking_allocation_requests
 ADD COLUMN IF NOT EXISTS created_transaction_id bigint NOT NULL DEFAULT txid_current();

CREATE TABLE IF NOT EXISTS public.booking_stay_owner_markers (
 transaction_id bigint NOT NULL,
 backend_pid integer NOT NULL,
 organization_id uuid NOT NULL,
 booking_id uuid NOT NULL,
 subject_id uuid NOT NULL,
 request_id uuid NOT NULL,
 actor_user_id uuid NOT NULL,
 expected_action text NOT NULL CHECK (expected_action IN ('allocate','replace')),
 PRIMARY KEY(transaction_id,backend_pid,organization_id,booking_id,request_id)
);
ALTER TABLE public.booking_stay_owner_markers ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_stay_owner_markers FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;

CREATE OR REPLACE FUNCTION public.mark_booking_stay_owner_source(
 p_org uuid,p_booking uuid,p_subject uuid,p_request uuid,p_actor uuid
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE caller_role text; expected text; owner_name text;
BEGIN
 caller_role:=COALESCE(NULLIF(current_setting('role'),'none'),session_user);
 SELECT pg_get_userbyid(c.relowner) INTO STRICT owner_name
  FROM pg_class c WHERE c.oid='public.booking_stay_owner_markers'::regclass;
 IF caller_role NOT IN (owner_name,'service_role') THEN
  RAISE EXCEPTION 'Owner stay marker requires trusted backend' USING ERRCODE='42501';
 END IF;
 IF p_org IS NULL OR p_booking IS NULL OR p_subject IS NULL OR p_request IS NULL OR p_actor IS NULL THEN
  RAISE EXCEPTION 'Owner stay lineage required' USING ERRCODE='55000';
 END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(p_org::text,606));
 IF EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers e WHERE e.organization_id=p_org
   AND (e.scope='organization' OR e.subject_id=p_subject)) THEN
  RAISE EXCEPTION 'Erasure barrier rejects owner stay' USING ERRCODE='55000';
 END IF;
 PERFORM 1 FROM public.bookings b JOIN public.contacts c
   ON c.organization_id=b.organization_id AND c.id=p_subject AND c.deleted_at IS NULL
  WHERE b.organization_id=p_org AND b.id=p_booking AND b.contact_id=p_subject FOR KEY SHARE OF b,c;
 IF NOT FOUND THEN RAISE EXCEPTION 'Owner stay booking lineage unavailable' USING ERRCODE='55000'; END IF;
 IF NOT EXISTS (
  SELECT 1 FROM public.organization_memberships om
  JOIN public.user_profiles up ON up.id=om.user_id
  JOIN public.auth_access_lifecycle al ON al.auth_user_id=up.auth_user_id
  WHERE om.organization_id=p_org AND up.id=p_actor AND om.ruolo='owner'
   AND om.joined_at IS NOT NULL AND al.disabled_at IS NULL AND al.provisioned_at IS NOT NULL
 ) THEN RAISE EXCEPTION 'Active owner membership required' USING ERRCODE='42501'; END IF;
 expected:=CASE WHEN EXISTS(SELECT 1 FROM public.booking_stays s
   WHERE s.organization_id=p_org AND s.booking_id=p_booking) THEN 'replace' ELSE 'allocate' END;
 INSERT INTO public.booking_stay_owner_markers
  (transaction_id,backend_pid,organization_id,booking_id,subject_id,request_id,actor_user_id,expected_action)
 VALUES(txid_current(),pg_backend_pid(),p_org,p_booking,p_subject,p_request,p_actor,expected);
END $$;
REVOKE ALL ON FUNCTION public.mark_booking_stay_owner_source(uuid,uuid,uuid,uuid,uuid)
 FROM PUBLIC,anon,authenticated,melpis_c24_booking_worker,melpis_c24_retention;
GRANT EXECUTE ON FUNCTION public.mark_booking_stay_owner_source(uuid,uuid,uuid,uuid,uuid) TO service_role;

CREATE OR REPLACE FUNCTION public.booking_stay_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE booking_contact uuid; caller_marker boolean;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,606));
 IF EXISTS(SELECT 1 FROM public.booking_stay_purge_fences f
   WHERE f.organization_id=NEW.organization_id AND f.booking_id=NEW.booking_id) THEN
  RAISE EXCEPTION 'Purged stay cannot be recreated' USING ERRCODE='55000';
 END IF;
 IF TG_OP='UPDATE' AND (NEW.organization_id,NEW.booking_id,NEW.original_subject_id,
      NEW.original_conversation_id,NEW.source_message_id,NEW.source_kind)
   IS DISTINCT FROM (OLD.organization_id,OLD.booking_id,OLD.original_subject_id,
      OLD.original_conversation_id,OLD.source_message_id,OLD.source_kind) THEN
  RAISE EXCEPTION 'Stay lineage is immutable' USING ERRCODE='55000';
 END IF;
 IF NEW.source_kind NOT IN ('inbound','owner') THEN
  RAISE EXCEPTION 'Stay source kind invalid' USING ERRCODE='23514';
 END IF;
 SELECT b.contact_id INTO booking_contact FROM public.bookings b
  WHERE b.organization_id=NEW.organization_id AND b.id=NEW.booking_id FOR KEY SHARE;
 IF NOT FOUND OR booking_contact IS DISTINCT FROM NEW.original_subject_id
   OR NOT EXISTS(SELECT 1 FROM public.contacts c WHERE c.organization_id=NEW.organization_id
      AND c.id=NEW.original_subject_id AND c.deleted_at IS NULL)
   OR EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers e WHERE e.organization_id=NEW.organization_id
      AND (e.scope='organization' OR e.subject_id=NEW.original_subject_id)) THEN
  RAISE EXCEPTION 'Stay lineage unavailable' USING ERRCODE='55000';
 END IF;
 IF NEW.source_kind='inbound' THEN
  IF NEW.original_conversation_id IS NULL OR NEW.source_message_id IS NULL
   OR NOT EXISTS (SELECT 1 FROM public.bookings b
    JOIN public.conversations c ON c.organization_id=b.organization_id AND c.id=NEW.original_conversation_id
    JOIN public.messages m ON m.organization_id=b.organization_id AND m.conversation_id=c.id AND m.id=NEW.source_message_id
    WHERE b.organization_id=NEW.organization_id AND b.id=NEW.booking_id
     AND b.id_conversazione=NEW.original_conversation_id::text AND c.contact_id=NEW.original_subject_id
     AND c.deleted_at IS NULL AND m.direction='inbound' AND m.deleted_at IS NULL
     AND m.c24_original_subject_id=NEW.original_subject_id) THEN
   RAISE EXCEPTION 'Verified inbound stay lineage required' USING ERRCODE='55000';
  END IF;
 ELSE
  IF NEW.original_conversation_id IS NOT NULL OR NEW.source_message_id IS NOT NULL THEN
   RAISE EXCEPTION 'Owner stay must not claim an inbound source' USING ERRCODE='23514';
  END IF;
  SELECT EXISTS(SELECT 1 FROM public.booking_stay_owner_markers m
   WHERE m.transaction_id=txid_current() AND m.backend_pid=pg_backend_pid()
    AND m.organization_id=NEW.organization_id AND m.booking_id=NEW.booking_id
    AND m.subject_id=NEW.original_subject_id) INTO caller_marker;
  IF NOT caller_marker THEN RAISE EXCEPTION 'Owner stay authorization marker required' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_guard() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS booking_stay_guard ON public.booking_stays;
CREATE TRIGGER booking_stay_guard BEFORE INSERT OR UPDATE ON public.booking_stays
 FOR EACH ROW EXECUTE FUNCTION public.booking_stay_guard();

CREATE OR REPLACE FUNCTION public.booking_stay_receipt_deferred() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s public.booking_stays%ROWTYPE; marker public.booking_stay_owner_markers%ROWTYPE;
        receipt public.booking_allocation_requests%ROWTYPE; changed boolean;
BEGIN
 SELECT * INTO s FROM public.booking_stays WHERE organization_id=NEW.organization_id AND booking_id=NEW.booking_id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF TG_OP='INSERT' THEN
  changed:=true;
 ELSE
  changed:=(NEW.arrival_date,NEW.departure_date,NEW.revision)
    IS DISTINCT FROM (OLD.arrival_date,OLD.departure_date,OLD.revision);
 END IF;
 IF NOT changed THEN RETURN NULL; END IF;
 SELECT * INTO marker FROM public.booking_stay_owner_markers m
  WHERE m.transaction_id=txid_current() AND m.backend_pid=pg_backend_pid()
   AND m.organization_id=s.organization_id AND m.booking_id=s.booking_id
   AND m.subject_id=s.original_subject_id;
 IF marker.request_id IS NOT NULL THEN
  SELECT * INTO receipt FROM public.booking_allocation_requests r
   WHERE r.organization_id=s.organization_id AND r.request_id=marker.request_id
    AND r.booking_id=s.booking_id AND r.created_transaction_id=txid_current()
    AND r.actor_kind='human' AND r.actor_user_id=marker.actor_user_id
    AND r.action=marker.expected_action
    AND r.outcome=CASE marker.expected_action WHEN 'allocate' THEN 'allocated' ELSE 'replaced' END;
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.organization_memberships om
    JOIN public.user_profiles up ON up.id=om.user_id
    JOIN public.auth_access_lifecycle al ON al.auth_user_id=up.auth_user_id
    WHERE om.organization_id=s.organization_id AND up.id=marker.actor_user_id
     AND om.ruolo='owner' AND om.joined_at IS NOT NULL
     AND al.disabled_at IS NULL AND al.provisioned_at IS NOT NULL) THEN
   RAISE EXCEPTION 'Owner allocation receipt unavailable' USING ERRCODE='55000';
  END IF;
  DELETE FROM public.booking_stay_owner_markers WHERE transaction_id=marker.transaction_id
   AND backend_pid=marker.backend_pid AND organization_id=marker.organization_id
   AND booking_id=marker.booking_id AND request_id=marker.request_id;
 ELSE
  IF s.source_kind='owner' THEN
   RAISE EXCEPTION 'Owner allocation marker unavailable' USING ERRCODE='55000';
  END IF;
  SELECT * INTO receipt FROM public.booking_allocation_requests r
   WHERE r.organization_id=s.organization_id AND r.booking_id=s.booking_id
    AND r.created_transaction_id=txid_current() AND r.actor_kind='inbound_worker'
    AND r.action=CASE WHEN TG_OP='INSERT' THEN 'allocate' ELSE 'replace' END
    AND r.outcome=CASE WHEN TG_OP='INSERT' THEN 'allocated' ELSE 'replaced' END
    AND (TG_OP='UPDATE' OR r.service_message_id=s.source_message_id)
   ORDER BY r.created_at DESC LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION 'Inbound allocation receipt unavailable' USING ERRCODE='55000'; END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.booking_allocation_sets a
   WHERE a.organization_id=s.organization_id AND a.booking_id=s.booking_id
    AND a.temporal_kind='civil_nights' AND a.state='active'
    AND (a.arrival_date,a.departure_date) IS NOT DISTINCT FROM (s.arrival_date,s.departure_date)
    AND a.allocation_revision=receipt.applied_allocation_revision
    AND a.catalog_revision=receipt.catalog_revision
    AND EXISTS(SELECT 1 FROM public.booking_resource_allocations r
      WHERE r.organization_id=a.organization_id AND r.booking_id=a.booking_id
       AND r.state='active' AND r.allocation_revision=a.allocation_revision
       AND r.catalog_revision=a.catalog_revision AND r.starts_at=a.starts_at AND r.ends_at=a.ends_at)) THEN
  RAISE EXCEPTION 'Hotel allocation receipt and stay differ' USING ERRCODE='55000';
 END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_receipt_deferred() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS booking_stay_receipt_deferred ON public.booking_stays;
CREATE CONSTRAINT TRIGGER booking_stay_receipt_deferred
 AFTER INSERT OR UPDATE ON public.booking_stays DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION public.booking_stay_receipt_deferred();

-- An erasure first scrubs the contact identifier, then soft-deletes its
-- conversation. Preserve immutable tenant/contact lineage for hotel evidence,
-- while allowing only that one-way deletion after the subject is tombstoned.
CREATE OR REPLACE FUNCTION public.booking_stay_assignment_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_TABLE_NAME='bookings' THEN
  IF (NEW.organization_id,NEW.contact_id,NEW.id_conversazione)
    IS DISTINCT FROM (OLD.organization_id,OLD.contact_id,OLD.id_conversazione)
    AND EXISTS(SELECT 1 FROM public.booking_stays s
      WHERE s.organization_id=OLD.organization_id AND s.booking_id=OLD.id) THEN
   RAISE EXCEPTION 'Stay booking lineage cannot be reassigned' USING ERRCODE='55000';
  END IF;
 ELSE
  IF (NEW.organization_id,NEW.contact_id) IS DISTINCT FROM (OLD.organization_id,OLD.contact_id)
    AND EXISTS(SELECT 1 FROM public.booking_stays s
      WHERE s.organization_id=OLD.organization_id AND s.original_conversation_id=OLD.id) THEN
   RAISE EXCEPTION 'Stay conversation lineage cannot be reassigned' USING ERRCODE='55000';
  END IF;
  IF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at
    AND EXISTS(SELECT 1 FROM public.booking_stays s
      WHERE s.organization_id=OLD.organization_id AND s.original_conversation_id=OLD.id) THEN
   IF OLD.deleted_at IS NOT NULL OR NEW.deleted_at IS NULL
    OR (NEW.organization_id,NEW.contact_id) IS DISTINCT FROM (OLD.organization_id,OLD.contact_id)
    OR NOT EXISTS(SELECT 1 FROM public.contacts c WHERE c.organization_id=OLD.organization_id
      AND c.id=OLD.contact_id AND c.deleted_at IS NOT NULL
      AND c.phone_number='erased:'||c.id::text) THEN
    RAISE EXCEPTION 'Stay conversation deletion requires tombstoned subject' USING ERRCODE='55000';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_assignment_guard() FROM PUBLIC,anon,authenticated,service_role;

-- Checkouts release capacity as soon as completion and the scheduled end are
-- proven. The 12 civil-month rule applies only to content purge.
CREATE OR REPLACE FUNCTION public.archive_completed_hotel_allocation(p_org uuid,p_booking uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE b public.bookings%ROWTYPE; s public.booking_stays%ROWTYPE; a public.booking_allocation_sets%ROWTYPE;
 e public.booking_stay_completion_evidence%ROWTYPE; p public.gdpr_policies%ROWTYPE;
 claim record; claim_count integer; observed timestamptz:=clock_timestamp();
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
   WHERE oid='public.booking_stay_completion_evidence'::regclass)) THEN
  RAISE EXCEPTION 'Hotel archive requires lifecycle owner' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Hotel archive requires read committed isolation' USING ERRCODE='25000';
 END IF;
 IF p_org IS NULL OR p_booking IS NULL THEN RAISE EXCEPTION 'Archive scope required' USING ERRCODE='55000'; END IF;
 PERFORM 1 FROM public.organizations WHERE id=p_org FOR NO KEY UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Organization unavailable' USING ERRCODE='55000'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 IF EXISTS(SELECT 1 FROM public.external_booking_sync WHERE organization_id=p_org
    AND internal_booking_id=p_booking AND calendar_dispatch_state='possibly_sent')
  OR EXISTS(SELECT 1 FROM public.usage_events WHERE organization_id=p_org
    AND ((event_type='system.provider_attempt' AND metadata->>'accounting_status'='unresolved')
      OR (event_type='system.quota_reservation' AND metadata->>'state'='reserved')))
  OR EXISTS(SELECT 1 FROM public.messages WHERE organization_id=p_org
    AND status IN ('processing','queued','sending_ambiguous','received_pending_ai'))
  OR EXISTS(SELECT 1 FROM public.meta_webhook_inbox WHERE organization_id=p_org
    AND status NOT IN ('completed','failed')) THEN
  RAISE EXCEPTION 'Unresolved external or in-flight work' USING ERRCODE='55000';
 END IF;
 SELECT * INTO b FROM public.bookings WHERE organization_id=p_org AND id=p_booking FOR UPDATE;
 SELECT * INTO s FROM public.booking_stays WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 SELECT * INTO a FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 SELECT * INTO e FROM public.booking_stay_completion_evidence WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 IF b.id IS NULL OR s.booking_id IS NULL OR a.booking_id IS NULL OR e.booking_id IS NULL
  OR b.stato<>'completata' OR b.completata_at IS NULL OR b.no_show_at IS NOT NULL
  OR a.temporal_kind<>'civil_nights' OR a.timezone<>e.timezone
  OR (a.arrival_date,a.departure_date) IS DISTINCT FROM (s.arrival_date,s.departure_date)
  OR a.ends_at>e.completed_at OR a.ends_at>observed THEN
  RAISE EXCEPTION 'Completed stay archive evidence unavailable' USING ERRCODE='55000';
 END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org
  AND category='booking_stay_content' AND approved_at<=observed
  ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=observed OR p.legal_hold OR NOT p.erasure_allowed THEN
  RAISE EXCEPTION 'Approved current hotel policy required' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers e0 WHERE e0.organization_id=p_org
   AND (e0.scope='organization' OR e0.subject_id=s.original_subject_id))
  OR EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
   AND h.category='booking_stay_content'
   AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=s.original_subject_id)
     OR (h.scope='conversation' AND h.scope_id=s.original_conversation_id)
     OR (h.scope='record' AND h.scope_id=p_booking))
   AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events v
     WHERE v.organization_id=h.organization_id AND v.hold_id=h.hold_id AND v.kind='released')) THEN
  RAISE EXCEPTION 'Hotel lifecycle hold or barrier active' USING ERRCODE='55000';
 END IF;
 IF a.state='cancelled' AND NOT EXISTS(SELECT 1 FROM public.booking_resource_allocations
   WHERE organization_id=p_org AND booking_id=p_booking) THEN RETURN false; END IF;
 IF a.state<>'active' THEN RAISE EXCEPTION 'Active hotel allocation required' USING ERRCODE='55000'; END IF;
 SELECT count(*) INTO claim_count FROM public.booking_resource_allocations
  WHERE organization_id=p_org AND booking_id=p_booking;
 IF claim_count<1 OR claim_count>128 THEN RAISE EXCEPTION 'Bounded allocation claims required' USING ERRCODE='55000'; END IF;
 FOR claim IN SELECT * FROM public.booking_resource_allocations
   WHERE organization_id=p_org AND booking_id=p_booking ORDER BY resource_id FOR UPDATE LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended('allocation-resource|'||p_org::text||'|'||claim.resource_id,725));
  IF (claim.state,claim.allocation_revision,claim.catalog_revision,claim.starts_at,claim.ends_at)
   IS DISTINCT FROM ('active',a.allocation_revision,a.catalog_revision,a.starts_at,a.ends_at) THEN
   RAISE EXCEPTION 'Hotel allocation claims changed' USING ERRCODE='55000';
  END IF;
 END LOOP;
 DELETE FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking;
 UPDATE public.booking_allocation_sets SET state='cancelled',allocation_revision=allocation_revision+1,updated_at=observed
  WHERE organization_id=p_org AND booking_id=p_booking AND allocation_revision=a.allocation_revision;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.archive_completed_hotel_allocation(uuid,uuid)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

-- Full hotel content purge. The established function signature is retained for
-- SQL089's bounded operator and C05's barrier-bound erasure controller.
CREATE OR REPLACE FUNCTION public.booking_stay_admin_purge(
 p_org uuid,p_booking uuid,p_purpose text,p_operation uuid,p_policy_version text
) RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.booking_stays%ROWTYPE; b public.bookings%ROWTYPE; pol public.gdpr_policies%ROWTYPE;
 cat text; receipt_count integer; a public.booking_allocation_sets%ROWTYPE; completion public.booking_stay_completion_evidence%ROWTYPE;
 claim record; claim_count integer; observed timestamptz:=clock_timestamp();
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
   WHERE oid='public.booking_stay_purge_markers'::regclass)) THEN
  RAISE EXCEPTION 'Hotel purge requires lifecycle owner' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Hotel purge requires read committed isolation' USING ERRCODE='25000'; END IF;
 IF p_purpose NOT IN ('erasure','retention') OR (p_purpose='erasure' AND p_operation IS NULL)
  OR (p_purpose='retention' AND p_operation IS NOT NULL) OR p_policy_version IS NULL THEN
  RAISE EXCEPTION 'Invalid hotel purge scope' USING ERRCODE='55000'; END IF;
 PERFORM 1 FROM public.organizations WHERE id=p_org FOR NO KEY UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Organization unavailable' USING ERRCODE='55000'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 IF p_purpose='erasure' THEN
  PERFORM 1 FROM public.gdpr_operations WHERE organization_id=p_org AND id=p_operation
   AND kind='erase' AND status IN ('requested','running','blocked','partial') FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Erasure operation unavailable' USING ERRCODE='55000'; END IF;
 END IF;
 SELECT * INTO b FROM public.bookings WHERE organization_id=p_org AND id=p_booking FOR UPDATE;
 SELECT * INTO s FROM public.booking_stays WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 IF b.id IS NULL OR s.booking_id IS NULL
   OR b.stato NOT IN ('cancellata','cancellato','rifiutata','no_show','completata') THEN
  RAISE EXCEPTION 'Terminal hotel stay required' USING ERRCODE='55000'; END IF;
 FOR cat IN SELECT unnest(CASE WHEN p_purpose='erasure'
     THEN ARRAY['booking_stay_content','contact_content','cutover'] ELSE ARRAY['booking_stay_content'] END) LOOP
  SELECT * INTO pol FROM public.gdpr_policies WHERE organization_id=p_org AND category=cat AND approved_at<=observed
   ORDER BY approved_at DESC,version DESC LIMIT 1;
  IF NOT FOUND OR pol.legal_hold OR NOT pol.erasure_allowed OR pol.valid_until<=observed
    OR (cat='booking_stay_content' AND pol.version<>p_policy_version) THEN
  RAISE EXCEPTION 'Approved current policy required' USING ERRCODE='55000'; END IF;
 END LOOP;
 SELECT * INTO pol FROM public.gdpr_policies WHERE organization_id=p_org
  AND category='booking_stay_content' AND approved_at<=observed ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF p_purpose='erasure' AND NOT EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers e
   JOIN public.gdpr_operations o ON o.organization_id=e.organization_id AND o.id=e.operation_id
   WHERE e.organization_id=p_org AND e.operation_id=p_operation AND o.kind='erase'
    AND o.status IN ('requested','running','blocked','partial') AND o.scope=e.scope
    AND o.subject_id IS NOT DISTINCT FROM e.subject_id
    AND (e.scope='organization' OR (e.scope='contact' AND e.subject_id=s.original_subject_id))) THEN
  RAISE EXCEPTION 'Scoped hotel erasure barrier required' USING ERRCODE='55000'; END IF;
 IF p_purpose='retention' AND NOT public.hotel_stay_purge_eligible(p_org,p_booking) THEN
  RAISE EXCEPTION 'Completed archived stay retention evidence required' USING ERRCODE='55000'; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
  AND ((h.category='booking_stay_content'
    AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=s.original_subject_id)
     OR (s.original_conversation_id IS NOT NULL AND h.scope='conversation' AND h.scope_id=s.original_conversation_id)
     OR (h.scope='record' AND (h.scope_id=p_booking OR EXISTS(SELECT 1 FROM public.booking_allocation_requests r
        WHERE r.organization_id=p_org AND r.booking_id=p_booking AND r.request_id=h.scope_id)))))
   OR (h.category='messages_content'
    AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=s.original_subject_id)
     OR (s.original_conversation_id IS NOT NULL AND h.scope='conversation' AND h.scope_id=s.original_conversation_id)
     OR (h.scope='record' AND EXISTS(SELECT 1 FROM public.booking_confirmation_outbox o
        WHERE o.organization_id=p_org AND o.booking_id=p_booking
         AND h.scope_id IN (o.inbound_message_id,o.outbound_message_id))))))
  AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events v
   WHERE v.organization_id=h.organization_id AND v.hold_id=h.hold_id AND v.kind='released')) THEN
  RAISE EXCEPTION 'Hotel stay content is held' USING ERRCODE='55000'; END IF;
 -- Provider state is scoped to this booking. Unknown or possibly-sent work is
 -- never guessed away; a successful verified delete is terminal evidence.
 IF EXISTS(SELECT 1 FROM public.external_booking_sync x WHERE x.organization_id=p_org
   AND x.internal_booking_id=p_booking AND NOT (x.provider='google' AND (
    (x.calendar_dispatch_state='not_sent' AND x.sync_status='blocked' AND x.external_booking_id IS NULL)
    OR (x.calendar_action='delete' AND x.sync_status='succeeded'
      AND x.calendar_dispatch_state='verified' AND x.calendar_verified_at IS NOT NULL)))) THEN
  RAISE EXCEPTION 'Hotel Calendar outcome is unresolved' USING ERRCODE='55000'; END IF;
 IF EXISTS(SELECT 1 FROM public.booking_confirmation_outbox o
   LEFT JOIN public.messages m ON m.organization_id=o.organization_id AND m.id=o.outbound_message_id
   WHERE o.organization_id=p_org AND o.booking_id=p_booking
    AND (o.status<>'scheduled' OR m.id IS NULL OR m.status NOT IN ('sent','delivered','read')
      OR EXISTS(SELECT 1 FROM public.message_delivery_attempts d WHERE d.message_id=m.id
        AND d.status NOT IN ('sent','delivered','read','completed')))) THEN
  RAISE EXCEPTION 'Hotel confirmation delivery is unresolved' USING ERRCODE='55000'; END IF;
 IF EXISTS(SELECT 1 FROM public.usage_events WHERE organization_id=p_org
   AND ((event_type='system.provider_attempt' AND metadata->>'accounting_status'='unresolved')
     OR (event_type='system.quota_reservation' AND metadata->>'state'='reserved')))
  OR EXISTS(SELECT 1 FROM public.messages WHERE organization_id=p_org
   AND status IN ('processing','queued','sending_ambiguous','received_pending_ai'))
  OR EXISTS(SELECT 1 FROM public.meta_webhook_inbox WHERE organization_id=p_org
   AND status NOT IN ('completed','failed')) THEN
  RAISE EXCEPTION 'Tenant has unresolved provider or in-flight work' USING ERRCODE='55000'; END IF;
 SELECT * INTO a FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 IF p_purpose='erasure' AND a.state='active' THEN
  SELECT * INTO completion FROM public.booking_stay_completion_evidence
   WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
  IF b.stato<>'completata' OR b.completata_at IS NULL OR b.no_show_at IS NOT NULL
   OR completion.booking_id IS NULL OR a.temporal_kind<>'civil_nights' OR a.timezone<>completion.timezone
   OR (a.arrival_date,a.departure_date) IS DISTINCT FROM (s.arrival_date,s.departure_date)
   OR a.ends_at>completion.completed_at OR a.ends_at>observed THEN
   RAISE EXCEPTION 'Completed hotel release evidence unavailable' USING ERRCODE='55000'; END IF;
  SELECT count(*) INTO claim_count FROM public.booking_resource_allocations
   WHERE organization_id=p_org AND booking_id=p_booking;
  IF claim_count<1 OR claim_count>128 THEN RAISE EXCEPTION 'Bounded hotel claims required' USING ERRCODE='55000'; END IF;
  FOR claim IN SELECT * FROM public.booking_resource_allocations
    WHERE organization_id=p_org AND booking_id=p_booking ORDER BY resource_id FOR UPDATE LOOP
   PERFORM pg_advisory_xact_lock(hashtextextended('allocation-resource|'||p_org::text||'|'||claim.resource_id,725));
   IF (claim.state,claim.allocation_revision,claim.catalog_revision,claim.starts_at,claim.ends_at)
    IS DISTINCT FROM ('active',a.allocation_revision,a.catalog_revision,a.starts_at,a.ends_at) THEN
    RAISE EXCEPTION 'Hotel claims changed' USING ERRCODE='55000'; END IF;
  END LOOP;
  DELETE FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking;
  DELETE FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking
   AND allocation_revision=a.allocation_revision;
 END IF;
 IF EXISTS(SELECT 1 FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking)
  OR EXISTS(SELECT 1 FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking AND state='active') THEN
  RAISE EXCEPTION 'Released hotel allocation required' USING ERRCODE='55000'; END IF;
 SELECT count(*) INTO receipt_count FROM public.booking_allocation_requests
  WHERE organization_id=p_org AND booking_id=p_booking;
 INSERT INTO public.booking_stay_purge_fences
  (organization_id,booking_id,original_subject_id,purpose,operation_id,policy_version,receipt_count)
 VALUES(p_org,p_booking,s.original_subject_id,p_purpose,p_operation,p_policy_version,receipt_count);
 INSERT INTO public.booking_stay_tombstones
  (organization_id,request_id,booking_id,original_subject_id,purpose,operation_id,policy_version)
  SELECT organization_id,request_id,booking_id,s.original_subject_id,p_purpose,p_operation,p_policy_version
   FROM public.booking_allocation_requests WHERE organization_id=p_org AND booking_id=p_booking;
 INSERT INTO public.booking_stay_purge_markers
  VALUES(txid_current(),p_org,p_booking,s.original_subject_id,p_purpose,p_operation,p_policy_version);
 DELETE FROM public.booking_allocation_requests WHERE organization_id=p_org AND booking_id=p_booking;
 DELETE FROM public.booking_confirmation_outbox WHERE organization_id=p_org AND booking_id=p_booking;
 DELETE FROM public.external_booking_sync WHERE organization_id=p_org AND internal_booking_id=p_booking;
 DELETE FROM public.booking_stays WHERE organization_id=p_org AND booking_id=p_booking;
 DELETE FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking;
 INSERT INTO public.booking_completion_erasure_markers VALUES(txid_current(),p_org,p_booking);
 DELETE FROM public.booking_stay_completion_evidence WHERE organization_id=p_org AND booking_id=p_booking;
 DELETE FROM public.booking_completion_erasure_markers
  WHERE transaction_id=txid_current() AND organization_id=p_org AND booking_id=p_booking;
 -- The booking row contains customer name, phone, notes and dates. Delete it
 -- only after all scoped checks and durable anti-replay fences are in place.
 DELETE FROM public.bookings WHERE organization_id=p_org AND id=p_booking;
 DELETE FROM public.booking_stay_purge_markers WHERE transaction_id=txid_current()
  AND organization_id=p_org AND booking_id=p_booking;
 INSERT INTO public.booking_stay_purge_audit
  (organization_id,booking_id,purpose,operation_id,policy_version,receipt_count,session_principal,transaction_id)
 VALUES(p_org,p_booking,p_purpose,p_operation,p_policy_version,receipt_count,session_user,txid_current());
 RETURN receipt_count;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_admin_purge(uuid,uuid,text,uuid,text)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

-- Deletion.advance uses this narrow bridge under its existing erasure
-- execution gate. It cannot select an arbitrary subject or bypass the policy,
-- hold, terminal-state, in-flight, or provider checks in the owner-only purge.
CREATE OR REPLACE FUNCTION public.c24_purge_hotel_for_erasure(
 p_org uuid,p_booking uuid,p_operation uuid,p_policy_version text
) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE caller_role text; op public.gdpr_operations%ROWTYPE; stay public.booking_stays%ROWTYPE;
BEGIN
 caller_role:=COALESCE(NULLIF(current_setting('role'),'none'),session_user);
 IF caller_role<>'service_role' THEN
  RAISE EXCEPTION 'Erasure lifecycle service required' USING ERRCODE='42501';
 END IF;
 SELECT * INTO op FROM public.gdpr_operations WHERE organization_id=p_org AND id=p_operation
  AND kind='erase' AND status IN ('requested','running','blocked','partial') FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Active erasure operation required' USING ERRCODE='55000'; END IF;
 SELECT * INTO stay FROM public.booking_stays WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers e
  WHERE e.organization_id=p_org AND e.operation_id=p_operation
   AND e.scope=op.scope AND e.subject_id IS NOT DISTINCT FROM op.subject_id
   AND ((op.scope='organization' AND op.subject_id IS NULL)
     OR (op.scope='contact' AND op.subject_id=stay.original_subject_id))) THEN
  RAISE EXCEPTION 'Exact hotel erasure barrier required' USING ERRCODE='55000';
 END IF;
 RETURN public.booking_stay_admin_purge(p_org,p_booking,'erasure',p_operation,p_policy_version);
END $$;
REVOKE ALL ON FUNCTION public.c24_purge_hotel_for_erasure(uuid,uuid,uuid,text)
 FROM PUBLIC,anon,authenticated,melpis_c24_booking_worker,melpis_c24_retention;
GRANT EXECUTE ON FUNCTION public.c24_purge_hotel_for_erasure(uuid,uuid,uuid,text) TO service_role;

-- A UUID fenced as purged cannot be reintroduced as a booking without its
-- allocation/stay companion and bypass all higher-level replay protection.
CREATE OR REPLACE FUNCTION public.booking_stay_purge_recreate_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,606));
 IF EXISTS(SELECT 1 FROM public.booking_stay_purge_fences f
   WHERE f.organization_id=NEW.organization_id AND f.booking_id=NEW.id) THEN
  RAISE EXCEPTION 'Purged booking cannot be recreated' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_purge_recreate_guard() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS booking_stay_purge_recreate_guard ON public.bookings;
CREATE TRIGGER booking_stay_purge_recreate_guard BEFORE INSERT ON public.bookings
 FOR EACH ROW EXECUTE FUNCTION public.booking_stay_purge_recreate_guard();

