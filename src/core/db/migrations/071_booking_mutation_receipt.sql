DO $stay_migration$
BEGIN
ALTER TABLE public.booking_allocation_requests
    ADD COLUMN IF NOT EXISTS mutation_hash text CHECK (mutation_hash IS NULL OR mutation_hash ~ '^[a-f0-9]{64}$');

-- Dark civil-night data. No backfill or public/client access.
CREATE TABLE IF NOT EXISTS public.booking_stays (
 organization_id uuid NOT NULL, booking_id uuid NOT NULL,
 original_subject_id uuid NOT NULL, original_conversation_id uuid NOT NULL,
 source_message_id uuid NOT NULL, arrival_date date NOT NULL, departure_date date NOT NULL,
 revision bigint NOT NULL CHECK (revision > 0),
 PRIMARY KEY (organization_id, booking_id),
 FOREIGN KEY (organization_id,booking_id) REFERENCES public.bookings(organization_id,id) ON DELETE RESTRICT,
 FOREIGN KEY (organization_id,original_subject_id) REFERENCES public.contacts(organization_id,id) ON DELETE RESTRICT,
 FOREIGN KEY (organization_id,original_conversation_id,source_message_id) REFERENCES public.messages(organization_id,conversation_id,id) ON DELETE RESTRICT,
 CHECK (isfinite(arrival_date) AND isfinite(departure_date) AND arrival_date < departure_date)
);
ALTER TABLE public.booking_stays ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_stays FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON public.booking_stays TO service_role;
DROP POLICY IF EXISTS booking_stays_deny_clients ON public.booking_stays;
CREATE POLICY booking_stays_deny_clients ON public.booking_stays AS RESTRICTIVE FOR ALL TO anon,authenticated USING(false) WITH CHECK(false);
DROP POLICY IF EXISTS booking_stays_backend ON public.booking_stays;
CREATE POLICY booking_stays_backend ON public.booking_stays FOR ALL TO service_role USING(true) WITH CHECK(true);
CREATE OR REPLACE FUNCTION public.booking_stay_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,606));
 IF EXISTS(SELECT 1 FROM public.booking_stay_purge_fences WHERE organization_id=NEW.organization_id AND booking_id=NEW.booking_id) THEN
  RAISE EXCEPTION 'Purged stay cannot be recreated' USING ERRCODE='55000'; END IF;
 IF TG_OP='UPDATE' AND (NEW.organization_id,NEW.booking_id,NEW.original_subject_id,NEW.original_conversation_id,NEW.source_message_id)
   IS DISTINCT FROM (OLD.organization_id,OLD.booking_id,OLD.original_subject_id,OLD.original_conversation_id,OLD.source_message_id) THEN
  RAISE EXCEPTION 'Stay lineage is immutable' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM public.booking_stay_purge_fences WHERE organization_id=NEW.organization_id AND booking_id=NEW.booking_id) THEN
  RAISE EXCEPTION 'Purged stay cannot be recreated' USING ERRCODE='55000'; END IF;
 IF NOT EXISTS (SELECT 1 FROM bookings b JOIN conversations c ON c.organization_id=b.organization_id AND c.id=NEW.original_conversation_id
   JOIN contacts s ON s.organization_id=b.organization_id AND s.id=NEW.original_subject_id
   JOIN messages m ON m.organization_id=b.organization_id AND m.conversation_id=c.id AND m.id=NEW.source_message_id
   WHERE b.organization_id=NEW.organization_id AND b.id=NEW.booking_id AND b.contact_id=NEW.original_subject_id
   AND b.id_conversazione=NEW.original_conversation_id::text AND c.contact_id=NEW.original_subject_id AND c.deleted_at IS NULL
   AND s.deleted_at IS NULL AND m.direction='inbound' AND m.deleted_at IS NULL AND m.c24_original_subject_id=NEW.original_subject_id)
   OR EXISTS (SELECT 1 FROM gdpr_erasure_barriers WHERE organization_id=NEW.organization_id AND (scope='organization' OR subject_id=NEW.original_subject_id)) THEN
  RAISE EXCEPTION 'Stay lineage unavailable' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_guard() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS booking_stay_guard ON public.booking_stays;
CREATE TRIGGER booking_stay_guard BEFORE INSERT OR UPDATE ON public.booking_stays FOR EACH ROW EXECUTE FUNCTION public.booking_stay_guard();
CREATE OR REPLACE FUNCTION public.booking_stay_assignment_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
 IF TG_TABLE_NAME='bookings' THEN
  IF (NEW.organization_id,NEW.contact_id,NEW.id_conversazione) IS DISTINCT FROM (OLD.organization_id,OLD.contact_id,OLD.id_conversazione)
   AND EXISTS(SELECT 1 FROM booking_stays WHERE organization_id=OLD.organization_id AND booking_id=OLD.id) THEN
   RAISE EXCEPTION 'Stay booking lineage cannot be reassigned' USING ERRCODE='55000'; END IF;
 ELSE
  IF (NEW.organization_id,NEW.contact_id,NEW.deleted_at) IS DISTINCT FROM (OLD.organization_id,OLD.contact_id,OLD.deleted_at)
   AND EXISTS(SELECT 1 FROM booking_stays WHERE organization_id=OLD.organization_id AND original_conversation_id=OLD.id) THEN
   RAISE EXCEPTION 'Stay conversation lineage cannot be reassigned' USING ERRCODE='55000'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_assignment_guard() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS booking_stay_assignment_guard ON public.bookings;
CREATE TRIGGER booking_stay_assignment_guard BEFORE UPDATE ON public.bookings FOR EACH ROW EXECUTE FUNCTION public.booking_stay_assignment_guard();
DROP TRIGGER IF EXISTS booking_stay_assignment_guard ON public.conversations;
CREATE TRIGGER booking_stay_assignment_guard BEFORE UPDATE ON public.conversations FOR EACH ROW EXECUTE FUNCTION public.booking_stay_assignment_guard();
CREATE OR REPLACE FUNCTION public.booking_stay_source_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
 IF (NEW.organization_id,NEW.conversation_id,NEW.direction) IS DISTINCT FROM (OLD.organization_id,OLD.conversation_id,OLD.direction)
  AND EXISTS(SELECT 1 FROM booking_stays WHERE organization_id=OLD.organization_id AND original_conversation_id=OLD.conversation_id AND source_message_id=OLD.id) THEN
  RAISE EXCEPTION 'Stay source lineage cannot be reassigned' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_source_guard() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS booking_stay_source_guard ON public.messages;
CREATE TRIGGER booking_stay_source_guard BEFORE UPDATE ON public.messages FOR EACH ROW EXECUTE FUNCTION public.booking_stay_source_guard();


DO $$
DECLARE client record; reachable record; privilege_name text; target regclass := 'public.booking_stays'::regclass; col record;
BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles b CROSS JOIN pg_roles client_role WHERE b.rolname='service_role'
   AND NOT b.rolbypassrls AND NOT b.rolsuper AND client_role.rolname IN ('anon','authenticated')
   AND pg_has_role(b.oid,client_role.oid,'MEMBER')) THEN
  RAISE EXCEPTION 'Backend/client inheritance requires review';
 END IF;
 FOR col IN SELECT attname FROM pg_attribute WHERE attrelid=target AND attnum>0 AND NOT attisdropped LOOP
  EXECUTE format('REVOKE ALL PRIVILEGES (%I) ON TABLE %s FROM PUBLIC,anon,authenticated,service_role',col.attname,target);
 END LOOP;
 FOR client IN SELECT oid FROM pg_roles WHERE rolname IN ('anon','authenticated') LOOP
  FOR reachable IN WITH RECURSIVE role_paths(oid) AS (
   SELECT client.oid UNION SELECT m.roleid FROM pg_auth_members m JOIN role_paths p ON p.oid=m.member WHERE m.set_option OR m.admin_option
  ) SELECT r.* FROM pg_roles r JOIN role_paths p ON p.oid=r.oid LOOP
   IF reachable.rolsuper OR reachable.rolbypassrls OR reachable.oid=(SELECT relowner FROM pg_class WHERE oid=target)
      OR pg_has_role(reachable.oid,(SELECT relowner FROM pg_class WHERE oid=target),'MEMBER') THEN RAISE EXCEPTION 'Privileged client role path requires review'; END IF;
   FOREACH privilege_name IN ARRAY ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
    IF has_table_privilege(reachable.oid,target,privilege_name) OR (privilege_name IN ('SELECT','INSERT','UPDATE','REFERENCES') AND has_any_column_privilege(reachable.oid,target,privilege_name)) THEN RAISE EXCEPTION 'Reachable client privilege requires review'; END IF;
   END LOOP;
  END LOOP;
  FOREACH privilege_name IN ARRAY ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
   IF has_table_privilege(client.oid,target,privilege_name) OR (privilege_name IN ('SELECT','INSERT','UPDATE','REFERENCES') AND has_any_column_privilege(client.oid,target,privilege_name)) THEN RAISE EXCEPTION 'Inherited client privilege requires review'; END IF;
  END LOOP;
 END LOOP;
 FOR reachable IN WITH RECURSIVE role_paths(oid) AS (
  SELECT oid FROM pg_roles WHERE rolname='service_role' UNION SELECT m.roleid FROM pg_auth_members m JOIN role_paths p ON p.oid=m.member WHERE m.set_option OR m.admin_option
 ) SELECT r.* FROM pg_roles r JOIN role_paths p ON p.oid=r.oid LOOP
  IF reachable.rolsuper OR reachable.oid=(SELECT relowner FROM pg_class WHERE oid=target)
   OR pg_has_role(reachable.oid,(SELECT relowner FROM pg_class WHERE oid=target),'MEMBER')
   OR (reachable.oid<>(SELECT oid FROM pg_roles WHERE rolname='service_role') AND reachable.rolbypassrls) THEN RAISE EXCEPTION 'Privileged backend role path requires review'; END IF;
  FOREACH privilege_name IN ARRAY ARRAY['DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
   IF has_table_privilege(reachable.oid,target,privilege_name) OR (privilege_name='REFERENCES' AND has_any_column_privilege(reachable.oid,target,privilege_name)) THEN RAISE EXCEPTION 'Reachable backend privilege requires review'; END IF;
  END LOOP;
 END LOOP;
END $$;


-- Private scoped administrative evidence. No application writer authority.
CREATE TABLE IF NOT EXISTS public.booking_stay_purge_markers (
 transaction_id bigint NOT NULL, organization_id uuid NOT NULL, booking_id uuid NOT NULL,
 original_subject_id uuid NOT NULL, purpose text NOT NULL CHECK(purpose IN ('erasure','retention')),
 operation_id uuid, policy_version text NOT NULL,
 PRIMARY KEY(transaction_id,organization_id,booking_id)
);
ALTER TABLE public.booking_stay_purge_markers ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_stay_purge_markers FROM PUBLIC,anon,authenticated,service_role;
CREATE TABLE IF NOT EXISTS public.booking_stay_tombstones (
 organization_id uuid NOT NULL, request_id uuid NOT NULL, booking_id uuid NOT NULL,
 original_subject_id uuid NOT NULL, purpose text NOT NULL CHECK(purpose IN ('erasure','retention')),
 operation_id uuid, policy_version text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(organization_id,request_id)
);
ALTER TABLE public.booking_stay_tombstones ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_stay_tombstones FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.booking_stay_tombstones TO service_role;
DROP POLICY IF EXISTS stay_tombstone_backend ON public.booking_stay_tombstones;
CREATE POLICY stay_tombstone_backend ON public.booking_stay_tombstones FOR SELECT TO service_role USING(true);
DROP POLICY IF EXISTS stay_tombstone_clients ON public.booking_stay_tombstones;
CREATE POLICY stay_tombstone_clients ON public.booking_stay_tombstones AS RESTRICTIVE FOR ALL TO anon,authenticated USING(false) WITH CHECK(false);

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
      AND b.stato IN ('cancellata','cancellato','rifiutata','no_show')) THEN
  RAISE EXCEPTION 'Scoped purge evidence unavailable' USING ERRCODE='55000'; END IF;
 IF marker.purpose='erasure' AND NOT EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers e JOIN public.gdpr_operations o
   ON o.organization_id=e.organization_id AND o.id=e.operation_id WHERE e.organization_id=OLD.organization_id
   AND e.operation_id=marker.operation_id AND o.kind='erase' AND o.status IN ('requested','running','blocked','partial')
   AND o.scope=e.scope AND o.subject_id IS NOT DISTINCT FROM e.subject_id
   AND (e.scope='organization' OR (e.scope='contact' AND e.subject_id=marker.original_subject_id))) THEN
  RAISE EXCEPTION 'Erasure barrier unavailable' USING ERRCODE='55000'; END IF;
 IF marker.purpose='retention' AND (policy.retention_days IS NULL OR NOT EXISTS(SELECT 1 FROM public.bookings b
   WHERE b.organization_id=OLD.organization_id AND b.id=OLD.booking_id AND b.updated_at < now()-make_interval(days=>policy.retention_days))) THEN
  RAISE EXCEPTION 'Retention age unavailable' USING ERRCODE='55000'; END IF;
 RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION public.booking_allocation_immutable() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS booking_stay_immutable_delete ON public.booking_stays;
CREATE TRIGGER booking_stay_immutable_delete BEFORE DELETE ON public.booking_stays FOR EACH ROW EXECUTE FUNCTION public.booking_allocation_immutable();


CREATE TABLE IF NOT EXISTS public.booking_stay_purge_fences (
 organization_id uuid NOT NULL, booking_id uuid NOT NULL, original_subject_id uuid NOT NULL,
 purpose text NOT NULL CHECK(purpose IN ('erasure','retention')), operation_id uuid, policy_version text NOT NULL, receipt_count integer NOT NULL CHECK(receipt_count>=0),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(organization_id,booking_id)
);
ALTER TABLE public.booking_stay_purge_fences ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_stay_purge_fences FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.booking_stay_purge_fences TO service_role;
DROP POLICY IF EXISTS stay_fence_backend ON public.booking_stay_purge_fences;
CREATE POLICY stay_fence_backend ON public.booking_stay_purge_fences FOR SELECT TO service_role USING(true);
DROP POLICY IF EXISTS stay_fence_clients ON public.booking_stay_purge_fences;
CREATE POLICY stay_fence_clients ON public.booking_stay_purge_fences AS RESTRICTIVE FOR ALL TO anon,authenticated USING(false) WITH CHECK(false);
CREATE TABLE IF NOT EXISTS public.booking_stay_purge_audit (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, organization_id uuid NOT NULL, booking_id uuid NOT NULL,
 purpose text NOT NULL CHECK(purpose IN ('erasure','retention')), operation_id uuid, policy_version text NOT NULL, receipt_count integer NOT NULL,
 session_principal name NOT NULL, transaction_id bigint NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.booking_stay_purge_audit ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_stay_purge_audit FROM PUBLIC,anon,authenticated,service_role;
CREATE OR REPLACE FUNCTION public.booking_stay_purge_audit_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN RAISE EXCEPTION 'Purge audit is immutable' USING ERRCODE='42501'; END $$;
REVOKE ALL ON FUNCTION public.booking_stay_purge_audit_immutable() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS booking_stay_purge_audit_immutable ON public.booking_stay_purge_audit;
CREATE TRIGGER booking_stay_purge_audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.booking_stay_purge_audit
 FOR EACH STATEMENT EXECUTE FUNCTION public.booking_stay_purge_audit_immutable();

CREATE OR REPLACE FUNCTION public.booking_stay_admin_purge(p_org uuid,p_booking uuid,p_purpose text,p_operation uuid,p_policy_version text)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE stay public.booking_stays%ROWTYPE; booking public.bookings%ROWTYPE; pol public.gdpr_policies%ROWTYPE; cat text; count_deleted integer;
BEGIN
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
 IF stay.booking_id IS NULL OR booking.id IS NULL OR booking.stato NOT IN ('cancellata','cancellato','rifiutata','no_show')
   OR EXISTS(SELECT 1 FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking)
   OR EXISTS(SELECT 1 FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking AND state='active') THEN
  RAISE EXCEPTION 'Terminal released stay required' USING ERRCODE='55000'; END IF;
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
 IF p_purpose='retention' AND (pol.retention_days IS NULL OR booking.updated_at>=now()-make_interval(days=>pol.retention_days)) THEN
  RAISE EXCEPTION 'Approved retention age required' USING ERRCODE='55000'; END IF;
 SELECT count(*) INTO count_deleted FROM public.booking_allocation_requests WHERE organization_id=p_org AND booking_id=p_booking;
 INSERT INTO public.booking_stay_purge_fences(organization_id,booking_id,original_subject_id,purpose,operation_id,policy_version,receipt_count) VALUES(p_org,p_booking,stay.original_subject_id,p_purpose,p_operation,p_policy_version,count_deleted);
 INSERT INTO public.booking_stay_tombstones(organization_id,request_id,booking_id,original_subject_id,purpose,operation_id,policy_version)
   SELECT organization_id,request_id,booking_id,stay.original_subject_id,p_purpose,p_operation,p_policy_version FROM public.booking_allocation_requests WHERE organization_id=p_org AND booking_id=p_booking;
 INSERT INTO public.booking_stay_purge_markers VALUES(txid_current(),p_org,p_booking,stay.original_subject_id,p_purpose,p_operation,p_policy_version);
 DELETE FROM public.booking_allocation_requests WHERE organization_id=p_org AND booking_id=p_booking;
 GET DIAGNOSTICS count_deleted = ROW_COUNT;
 DELETE FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking AND state='cancelled';
 DELETE FROM public.booking_stays WHERE organization_id=p_org AND booking_id=p_booking;
 DELETE FROM public.booking_stay_purge_markers WHERE transaction_id=txid_current() AND organization_id=p_org AND booking_id=p_booking;
 INSERT INTO public.booking_stay_purge_audit(organization_id,booking_id,purpose,operation_id,policy_version,receipt_count,session_principal,transaction_id)
 VALUES(p_org,p_booking,p_purpose,p_operation,p_policy_version,count_deleted,session_user,txid_current());
 RETURN count_deleted;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_admin_purge(uuid,uuid,text,uuid,text) FROM PUBLIC,anon,authenticated,service_role;


DO $$
DECLARE t text; target regclass; r record; p text; client record; fn regprocedure := 'public.booking_stay_admin_purge(uuid,uuid,text,uuid,text)'::regprocedure;
BEGIN
 FOREACH t IN ARRAY ARRAY['booking_stay_purge_markers','booking_stay_purge_fences','booking_stay_tombstones','booking_stay_purge_audit'] LOOP
  target := ('public.'||t)::regclass;
  FOR r IN WITH RECURSIVE role_paths(oid) AS (SELECT oid FROM pg_roles WHERE rolname IN ('service_role','anon','authenticated')
   UNION SELECT m.roleid FROM pg_auth_members m JOIN role_paths rp ON rp.oid=m.member WHERE m.set_option OR m.admin_option)
   SELECT roles.* FROM pg_roles roles JOIN role_paths rp ON rp.oid=roles.oid LOOP
   IF r.rolsuper OR r.oid=(SELECT relowner FROM pg_class WHERE oid=target)
      OR pg_has_role(r.oid,(SELECT relowner FROM pg_class WHERE oid=target),'MEMBER')
      OR (r.rolbypassrls AND r.rolname<>'service_role') THEN RAISE EXCEPTION 'Privileged purge role path requires review'; END IF;
   FOREACH p IN ARRAY ARRAY['INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
    IF has_table_privilege(r.oid,target,p) OR (p IN ('INSERT','UPDATE','REFERENCES') AND has_any_column_privilege(r.oid,target,p)) THEN
     RAISE EXCEPTION 'Purge evidence mutation privilege requires review'; END IF;
   END LOOP;
   IF t IN ('booking_stay_purge_markers','booking_stay_purge_audit') AND (has_table_privilege(r.oid,target,'SELECT') OR has_any_column_privilege(r.oid,target,'SELECT')) THEN
    RAISE EXCEPTION 'Private purge evidence read privilege requires review'; END IF;
  END LOOP;
  FOR client IN SELECT oid FROM pg_roles WHERE rolname IN ('anon','authenticated') LOOP
   IF has_table_privilege(client.oid,target,'SELECT') OR has_any_column_privilege(client.oid,target,'SELECT') THEN RAISE EXCEPTION 'Client purge evidence privilege requires review'; END IF;
  END LOOP;
 END LOOP;
 FOR r IN WITH RECURSIVE role_paths(oid) AS (SELECT oid FROM pg_roles WHERE rolname IN ('service_role','anon','authenticated')
  UNION SELECT m.roleid FROM pg_auth_members m JOIN role_paths rp ON rp.oid=m.member WHERE m.set_option OR m.admin_option)
  SELECT roles.* FROM pg_roles roles JOIN role_paths rp ON rp.oid=roles.oid LOOP
  IF r.oid=(SELECT proowner FROM pg_proc WHERE oid=fn) OR pg_has_role(r.oid,(SELECT proowner FROM pg_proc WHERE oid=fn),'MEMBER')
     OR has_function_privilege(r.oid,fn,'EXECUTE') THEN RAISE EXCEPTION 'Purge execution privilege requires review'; END IF;
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION public.booking_allocation_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE direct_subject uuid; conversation_subject uuid; subject uuid; conversation_text text; conversation_uuid uuid;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,606));
 IF EXISTS(SELECT 1 FROM public.booking_stay_purge_fences WHERE organization_id=NEW.organization_id AND booking_id=NEW.booking_id) THEN
  RAISE EXCEPTION 'Purged booking cannot be recreated' USING ERRCODE='55000';
 END IF;
 IF TG_TABLE_NAME='booking_allocation_requests' THEN
  IF EXISTS(SELECT 1 FROM public.booking_stay_tombstones WHERE organization_id=NEW.organization_id AND request_id=NEW.request_id) THEN
   RAISE EXCEPTION 'Purged request cannot be recreated' USING ERRCODE='55000';
  END IF;
 END IF;
 SELECT contact_id,id_conversazione INTO direct_subject,conversation_text FROM bookings WHERE organization_id=NEW.organization_id AND id=NEW.booking_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Booking scope invalid' USING ERRCODE='55000'; END IF;
 IF conversation_text IS NOT NULL AND conversation_text<>'' THEN
  BEGIN conversation_uuid := conversation_text::uuid; EXCEPTION WHEN invalid_text_representation THEN RAISE EXCEPTION 'Invalid conversation lineage' USING ERRCODE='55000'; END;
  SELECT contact_id INTO conversation_subject FROM conversations WHERE organization_id=NEW.organization_id AND id=conversation_uuid AND deleted_at IS NULL;
  IF NOT FOUND OR conversation_subject IS NULL THEN RAISE EXCEPTION 'Invalid conversation lineage' USING ERRCODE='55000'; END IF;
 END IF;
 IF direct_subject IS NOT NULL AND conversation_subject IS NOT NULL AND direct_subject<>conversation_subject THEN RAISE EXCEPTION 'Contradictory lineage' USING ERRCODE='55000'; END IF;
 subject := COALESCE(direct_subject,conversation_subject);
 IF subject IS NOT NULL AND NOT EXISTS (SELECT 1 FROM contacts WHERE organization_id=NEW.organization_id AND id=subject AND deleted_at IS NULL) THEN RAISE EXCEPTION 'Invalid subject lineage' USING ERRCODE='55000'; END IF;
 IF EXISTS (SELECT 1 FROM gdpr_erasure_barriers WHERE organization_id=NEW.organization_id AND (scope='organization' OR subject_id=subject OR subject IS NULL)) THEN RAISE EXCEPTION 'GDPR barrier rejects allocation' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_allocation_guard() FROM PUBLIC,anon,authenticated,service_role;

END;
$stay_migration$;
