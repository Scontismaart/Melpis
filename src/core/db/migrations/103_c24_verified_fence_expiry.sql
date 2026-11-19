SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Audit inherits the same verified conclusion, so pruning a fence does not
-- start a fresh retention period for the derived technical record.
ALTER TABLE public.c24_suppression_lifecycle_audit
 ADD COLUMN IF NOT EXISTS c24_concluded_at timestamptz,
 ADD COLUMN IF NOT EXISTS c24_source_message_id uuid;
CREATE INDEX IF NOT EXISTS c24_response_unpurged_source_idx
 ON public.simulation_requests(c24_source_message_id)
 WHERE c24_source_message_id IS NOT NULL AND response IS NOT NULL AND response<>'{}'::jsonb;

-- Before removing the final local tuple, bind it to an authenticated
-- independent recovery checkpoint. Only non-content commitments are retained.
CREATE TABLE IF NOT EXISTS public.c24_receipt_prune_checkpoint_bindings (
 organization_id uuid NOT NULL,
 request_id uuid NOT NULL,
 tuple_sha256 text NOT NULL CHECK(tuple_sha256 ~ '^[0-9a-f]{64}$'),
 checkpoint_sha256 text NOT NULL CHECK(checkpoint_sha256 ~ '^[0-9a-f]{64}$'),
 checkpoint_sequence bigint NOT NULL CHECK(checkpoint_sequence>=1),
 bound_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(organization_id,request_id),
 FOREIGN KEY(organization_id,request_id)
  REFERENCES public.c24_receipt_cleanup_audit(organization_id,request_id)
);
ALTER TABLE public.c24_receipt_prune_checkpoint_bindings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.c24_receipt_prune_checkpoint_bindings
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;
DROP TRIGGER IF EXISTS c24_prune_binding_immutable ON public.c24_receipt_prune_checkpoint_bindings;
CREATE TRIGGER c24_prune_binding_immutable BEFORE UPDATE OR DELETE ON public.c24_receipt_prune_checkpoint_bindings
 FOR EACH ROW EXECUTE FUNCTION public.gdpr_immutable_record();

CREATE OR REPLACE FUNCTION public.c24_receipt_prune_digest(
 p_org uuid,p_request uuid,p_source uuid,p_conversation uuid,p_subject uuid
) RETURNS text LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT encode(sha256(convert_to(concat_ws(':','melpis.receipt-prune.v1',
  p_org::text,p_request::text,p_source::text,p_conversation::text,p_subject::text),'UTF8')),'hex')
$$;
REVOKE ALL ON FUNCTION public.c24_receipt_prune_digest(uuid,uuid,uuid,uuid,uuid)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;

-- A lifecycle owner can check globally unique source identity for historical
-- misattribution. The result is a boolean, with no foreign-tenant data returned.
CREATE OR REPLACE FUNCTION public.c24_receipt_source_copies_absent(p_org uuid,p_source uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
    WHERE oid='public.c24_receipt_prune_checkpoint_bindings'::regclass)) THEN
  RAISE EXCEPTION 'Receipt integrity check requires the lifecycle owner' USING ERRCODE='42501';
 END IF;
 RETURN EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=p_org AND m.id=p_source)
  AND NOT EXISTS(SELECT 1 FROM public.simulation_requests s WHERE s.c24_source_message_id=p_source
    AND s.response IS NOT NULL AND s.response<>'{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION public.c24_receipt_source_copies_absent(uuid,uuid)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;

CREATE OR REPLACE FUNCTION public.c24_bind_receipt_prune_checkpoint(
 p_org uuid,p_request uuid,p_source uuid,p_conversation uuid,p_subject uuid,
 p_checkpoint text,p_sequence bigint
) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE tuple_hash text;
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
    WHERE oid='public.c24_receipt_prune_checkpoint_bindings'::regclass)) THEN
  RAISE EXCEPTION 'Receipt checkpoint requires the lifecycle owner' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Receipt checkpoint requires read committed isolation' USING ERRCODE='25000'; END IF;
 IF p_org IS NULL OR p_request IS NULL OR p_source IS NULL OR p_conversation IS NULL OR p_subject IS NULL
    OR p_checkpoint IS NULL OR p_checkpoint !~ '^[0-9a-f]{64}$' OR p_sequence IS NULL OR p_sequence<1 THEN
  RETURN false;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 tuple_hash:=public.c24_receipt_prune_digest(p_org,p_request,p_source,p_conversation,p_subject);
 IF EXISTS(SELECT 1 FROM public.c24_receipt_prune_checkpoint_bindings b
    WHERE b.organization_id=p_org AND b.request_id=p_request) THEN
  RETURN EXISTS(SELECT 1 FROM public.c24_receipt_prune_checkpoint_bindings b
   WHERE b.organization_id=p_org AND b.request_id=p_request AND b.tuple_sha256=tuple_hash
   AND b.checkpoint_sequence<=p_sequence);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.c24_source_fences f
   JOIN public.messages m ON m.organization_id=f.organization_id AND m.conversation_id=f.conversation_id
    AND m.id=f.source_message_id AND m.c24_original_subject_id=f.subject_id
   JOIN public.c24_receipt_cleanup_audit a ON a.organization_id=f.organization_id AND a.request_id=f.request_id
   WHERE f.organization_id=p_org AND f.request_id=p_request AND f.source_message_id=p_source
    AND f.conversation_id=p_conversation AND f.subject_id=p_subject AND f.c24_concluded_at IS NOT NULL
    AND m.direction='inbound' AND m.wam_id IS NOT NULL AND m.content_purged_at IS NOT NULL)
   OR EXISTS(SELECT 1 FROM public.booking_conversation_requests r
      WHERE r.organization_id=p_org AND r.request_id=p_request) THEN RETURN false; END IF;
 INSERT INTO public.c24_receipt_prune_checkpoint_bindings
  (organization_id,request_id,tuple_sha256,checkpoint_sha256,checkpoint_sequence)
  VALUES(p_org,p_request,tuple_hash,p_checkpoint,p_sequence);
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.c24_bind_receipt_prune_checkpoint(uuid,uuid,uuid,uuid,uuid,text,bigint)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;

-- Retiring a source fence must not make a request ID reusable. The existing
-- immutable receipt cleanup audit preserves that identity independently.
CREATE OR REPLACE FUNCTION public.c24_fence_request() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE subj uuid;
BEGIN
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'Conversation receipt requires read committed isolation' USING ERRCODE='25000';
 END IF;
 IF EXISTS(SELECT 1 FROM public.c24_receipt_cleanup_audit a
  WHERE a.organization_id=NEW.organization_id AND a.request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'Retired request identity cannot conclude a request' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM public.messages m
  WHERE m.organization_id=NEW.organization_id AND m.conversation_id=NEW.conversation_id
   AND m.id=NEW.source_message_id AND m.content_purged_at IS NOT NULL) THEN
  RAISE EXCEPTION 'Purged source cannot conclude a request' USING ERRCODE='55000';
 END IF;
 SELECT subject_id INTO STRICT subj FROM public.booking_conversation_states
  WHERE organization_id=NEW.organization_id AND conversation_id=NEW.conversation_id;
 INSERT INTO public.c24_source_fences
  (organization_id,conversation_id,subject_id,request_id,source_message_id,c24_concluded_at)
 VALUES(NEW.organization_id,NEW.conversation_id,subj,NEW.request_id,NEW.source_message_id,NEW.c24_concluded_at);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_fence_request() FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.c24_cleanup_source_fences(
 p_org uuid,p_conversation uuid,p_request uuid
) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.gdpr_policies%ROWTYPE; conv public.conversations%ROWTYPE;
        fence_row record; changed boolean:=false; capability_owner name;
        target_conversation uuid:=p_conversation;
BEGIN
 SELECT pg_get_userbyid(relowner) INTO capability_owner FROM pg_class
  WHERE oid='public.c24_suppression_cleanup_capability'::regclass;
 IF current_user IS DISTINCT FROM capability_owner THEN
  RAISE EXCEPTION 'Suppression cleanup requires the lifecycle owner' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'Suppression cleanup requires read committed isolation' USING ERRCODE='25000';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 IF target_conversation IS NULL THEN
  SELECT conversation_id INTO target_conversation FROM public.c24_source_fences
   WHERE organization_id=p_org AND request_id=p_request;
 END IF;
 SELECT * INTO conv FROM public.conversations WHERE organization_id=p_org AND id=target_conversation FOR UPDATE;
 IF NOT FOUND OR conv.contact_id IS NULL THEN RETURN false; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org AND category='technical_receipts'
  AND approved_at<=clock_timestamp() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed
    OR p.retention_days IS NULL OR p.retention_days NOT BETWEEN 1 AND 90 THEN RETURN false; END IF;

 -- SQL101 makes the exact purged source independently replay-proof.
 -- Unknown historical fences and idle watermarks remain in place.
 FOR fence_row IN SELECT f.* FROM public.c24_source_fences f
  JOIN public.messages m ON m.organization_id=f.organization_id AND m.conversation_id=f.conversation_id
    AND m.id=f.source_message_id AND m.c24_original_subject_id=f.subject_id
    AND m.content_purged_at IS NOT NULL AND m.direction='inbound' AND m.wam_id IS NOT NULL
  JOIN public.c24_receipt_cleanup_audit a ON a.organization_id=f.organization_id AND a.request_id=f.request_id
  JOIN public.c24_receipt_prune_checkpoint_bindings evidence
   ON evidence.organization_id=f.organization_id AND evidence.request_id=f.request_id
    AND evidence.tuple_sha256=public.c24_receipt_prune_digest(
      f.organization_id,f.request_id,f.source_message_id,f.conversation_id,f.subject_id)
  WHERE f.organization_id=p_org AND f.conversation_id=target_conversation AND f.subject_id=conv.contact_id
   AND (p_request IS NULL OR f.request_id=p_request)
   AND f.c24_concluded_at<=clock_timestamp()-make_interval(days=>p.retention_days)
   AND NOT EXISTS(SELECT 1 FROM public.booking_conversation_requests r
    WHERE r.organization_id=f.organization_id AND r.request_id=f.request_id)
   -- Source IDs are globally unique. Historical misattribution can block
   -- expiry, but no copy outside the verified scope is ever modified here.
   AND public.c24_receipt_source_copies_absent(f.organization_id,f.source_message_id)
   AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
    AND h.category='technical_receipts' AND (h.scope='organization'
      OR (h.scope='subject' AND h.scope_id=f.subject_id)
      OR (h.scope='conversation' AND h.scope_id=target_conversation)
      OR (h.scope='record' AND h.scope_id IN (f.request_id,f.source_message_id)))
    AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
     WHERE e.organization_id=h.organization_id AND e.hold_id=h.hold_id AND e.kind='released'))
  ORDER BY f.c24_concluded_at,f.request_id LIMIT 25 FOR UPDATE OF f
 LOOP
  INSERT INTO public.c24_suppression_cleanup_capability VALUES
   (DEFAULT,pg_backend_pid(),txid_current(),p_org,target_conversation,fence_row.subject_id,'source_fence',fence_row.request_id,NULL);
  INSERT INTO public.c24_suppression_lifecycle_audit
   (organization_id,subject_id,conversation_id,source_record_id,record_kind,record_digest,action,
    c24_concluded_at,c24_source_message_id)
   VALUES(p_org,fence_row.subject_id,target_conversation,fence_row.request_id,'source_fence',
    md5(concat_ws(':',p_org::text,'source_fence',fence_row.request_id::text)),'retention',
    fence_row.c24_concluded_at,fence_row.source_message_id);
  DELETE FROM public.c24_source_fences WHERE organization_id=p_org AND request_id=fence_row.request_id;
  DELETE FROM public.c24_suppression_cleanup_capability WHERE backend_pid=pg_backend_pid()
   AND transaction_id=txid_current() AND organization_id=p_org AND conversation_id=target_conversation
   AND record_kind='source_fence' AND request_id=fence_row.request_id;
  changed:=true;
 END LOOP;
 RETURN changed;
END $$;
REVOKE ALL ON FUNCTION public.c24_cleanup_source_fences(uuid,uuid,uuid)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;

-- Keep existing explicit conversation targets; the scheduler addresses each
-- source fence independently, including blocked siblings in a conversation.
CREATE OR REPLACE FUNCTION public.c24_cleanup_suppression(p_org uuid,p_conversation uuid) RETURNS boolean
LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT public.c24_cleanup_source_fences(p_org,p_conversation,NULL::uuid)
$$;
REVOKE ALL ON FUNCTION public.c24_cleanup_suppression(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.c24_cleanup_suppression_audit(p_org uuid,p_audit uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.gdpr_policies%ROWTYPE; a public.c24_suppression_lifecycle_audit%ROWTYPE;
        capability_owner name;
BEGIN
 SELECT pg_get_userbyid(relowner) INTO capability_owner FROM pg_class
  WHERE oid='public.c24_suppression_audit_cleanup_capability'::regclass;
 IF current_user IS DISTINCT FROM capability_owner THEN
  RAISE EXCEPTION 'Suppression audit cleanup requires the lifecycle owner' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Suppression audit cleanup requires read committed isolation' USING ERRCODE='25000';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 SELECT * INTO a FROM public.c24_suppression_lifecycle_audit
  WHERE organization_id=p_org AND audit_id=p_audit FOR UPDATE;
 IF NOT FOUND OR a.c24_concluded_at IS NULL OR a.record_kind<>'source_fence' OR a.action<>'retention'
    OR a.source_record_id IS NULL OR a.c24_source_message_id IS NULL THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.c24_receipt_prune_checkpoint_bindings b
   WHERE b.organization_id=p_org AND b.request_id=a.source_record_id
    AND b.tuple_sha256=public.c24_receipt_prune_digest(p_org,a.source_record_id,a.c24_source_message_id,
      a.conversation_id,a.subject_id)) THEN RETURN false; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org AND category='technical_receipts'
  AND approved_at<=clock_timestamp() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed
   OR p.retention_days IS NULL OR p.retention_days NOT BETWEEN 1 AND 90
   OR a.c24_concluded_at>clock_timestamp()-make_interval(days=>p.retention_days) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
   AND h.category='technical_receipts' AND (h.scope='organization'
     OR (h.scope='subject' AND h.scope_id=a.subject_id)
     OR (h.scope='conversation' AND h.scope_id=a.conversation_id)
     OR (h.scope='record' AND h.scope_id IN (p_audit,a.source_record_id,a.conversation_id,a.c24_source_message_id)))
   AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
    WHERE e.organization_id=h.organization_id AND e.hold_id=h.hold_id AND e.kind='released')) THEN RETURN false; END IF;
 INSERT INTO public.c24_suppression_audit_cleanup_capability VALUES(pg_backend_pid(),txid_current(),p_org,p_audit);
 DELETE FROM public.c24_suppression_lifecycle_audit WHERE organization_id=p_org AND audit_id=p_audit;
 DELETE FROM public.c24_suppression_audit_cleanup_capability WHERE backend_pid=pg_backend_pid()
   AND transaction_id=txid_current() AND organization_id=p_org AND audit_id=p_audit;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.c24_cleanup_suppression_audit(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
