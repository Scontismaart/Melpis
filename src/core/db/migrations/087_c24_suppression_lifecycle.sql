-- Privacy V1.0 lifecycle for conversation replay-suppression metadata.
-- Execution remains behind the existing owner-only, disabled-by-default
-- C2.4 retention operator; this migration does not grant an operational role.
CREATE TABLE IF NOT EXISTS public.c24_suppression_cleanup_capability (
 capability_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 backend_pid integer NOT NULL,
 transaction_id bigint NOT NULL,
 organization_id uuid NOT NULL,
 conversation_id uuid NOT NULL,
 subject_id uuid NOT NULL,
 record_kind text NOT NULL CHECK(record_kind IN ('source_fence','idle_watermark')),
 request_id uuid,
 generation bigint,
 CHECK((record_kind='source_fence' AND request_id IS NOT NULL AND generation IS NULL)
    OR (record_kind='idle_watermark' AND request_id IS NULL AND generation IS NOT NULL))
);
ALTER TABLE public.c24_suppression_cleanup_capability ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.c24_suppression_cleanup_capability FROM PUBLIC,anon,authenticated,service_role;
CREATE TABLE IF NOT EXISTS public.c24_suppression_lifecycle_audit (
 organization_id uuid NOT NULL,
 audit_id uuid NOT NULL DEFAULT gen_random_uuid(),
 subject_id uuid NOT NULL,
 conversation_id uuid NOT NULL,
 source_record_id uuid,
 operation_id uuid,
 record_kind text NOT NULL CHECK(record_kind IN ('source_fence','idle_watermark')),
 record_digest text NOT NULL CHECK(record_digest ~ '^[0-9a-f]{32}$'),
 action text NOT NULL CHECK(action IN ('retention','erasure')),
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(organization_id,audit_id),
 UNIQUE(organization_id,action,record_kind,record_digest,operation_id),
 FOREIGN KEY(organization_id,conversation_id) REFERENCES public.conversations(organization_id,id) ON DELETE CASCADE,
 FOREIGN KEY(organization_id,subject_id) REFERENCES public.contacts(organization_id,id) ON DELETE CASCADE,
 FOREIGN KEY(organization_id,operation_id) REFERENCES public.gdpr_operations(organization_id,id)
);
ALTER TABLE public.c24_suppression_lifecycle_audit ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.c24_suppression_lifecycle_audit FROM PUBLIC,anon,authenticated,service_role;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['c24_idle_watermarks','c24_source_fences','c24_suppression_lifecycle_audit'] LOOP
  EXECUTE format('DROP POLICY IF EXISTS c24_suppression_export_backend ON public.%I',t);
  EXECUTE format('CREATE POLICY c24_suppression_export_backend ON public.%I FOR SELECT TO service_role USING(true)',t);
 END LOOP;
 GRANT SELECT ON public.c24_idle_watermarks,public.c24_source_fences TO service_role;
 GRANT SELECT(organization_id,subject_id,record_kind,record_digest,action,occurred_at)
  ON public.c24_suppression_lifecycle_audit TO service_role;
END $$;
CREATE TABLE IF NOT EXISTS public.c24_suppression_audit_cleanup_capability (
 backend_pid integer NOT NULL, transaction_id bigint NOT NULL,
 organization_id uuid NOT NULL, audit_id uuid NOT NULL,
 PRIMARY KEY(backend_pid,transaction_id,organization_id,audit_id)
);
ALTER TABLE public.c24_suppression_audit_cleanup_capability ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.c24_suppression_audit_cleanup_capability FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.c24_suppression_audit_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE capability_owner name;
BEGIN
 IF TG_OP='DELETE' THEN
  SELECT pg_get_userbyid(relowner) INTO capability_owner FROM pg_class
   WHERE oid='public.c24_suppression_audit_cleanup_capability'::regclass;
  IF current_user=capability_owner AND EXISTS(SELECT 1 FROM public.c24_suppression_audit_cleanup_capability c
    WHERE c.backend_pid=pg_backend_pid() AND c.transaction_id=txid_current()
      AND c.organization_id=OLD.organization_id AND c.audit_id=OLD.audit_id) THEN RETURN OLD; END IF;
 END IF;
 RAISE EXCEPTION 'Suppression lifecycle audit is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.c24_suppression_audit_immutable() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS c24_suppression_lifecycle_audit_immutable ON public.c24_suppression_lifecycle_audit;
CREATE TRIGGER c24_suppression_lifecycle_audit_immutable BEFORE UPDATE OR DELETE ON public.c24_suppression_lifecycle_audit
 FOR EACH ROW EXECUTE FUNCTION public.c24_suppression_audit_immutable();

CREATE OR REPLACE FUNCTION public.booking_revocation_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE capability_owner name;
BEGIN
 IF TG_OP='DELETE' THEN
  SELECT pg_get_userbyid(relowner) INTO capability_owner FROM pg_class
   WHERE oid='public.c24_suppression_cleanup_capability'::regclass;
  IF current_user=capability_owner THEN
   IF TG_TABLE_NAME='c24_source_fences' THEN
    IF EXISTS(SELECT 1 FROM public.c24_suppression_cleanup_capability c
        WHERE c.backend_pid=pg_backend_pid() AND c.transaction_id=txid_current()
         AND c.organization_id=OLD.organization_id AND c.conversation_id=OLD.conversation_id
         AND c.subject_id=OLD.subject_id AND c.record_kind='source_fence' AND c.request_id=OLD.request_id) THEN
     RETURN OLD;
    END IF;
   ELSIF TG_TABLE_NAME='c24_idle_watermarks' THEN
    IF EXISTS(SELECT 1 FROM public.c24_suppression_cleanup_capability c
        WHERE c.backend_pid=pg_backend_pid() AND c.transaction_id=txid_current()
         AND c.organization_id=OLD.organization_id AND c.conversation_id=OLD.conversation_id
         AND c.subject_id=OLD.subject_id AND c.record_kind='idle_watermark' AND c.generation=OLD.generation) THEN
     RETURN OLD;
    END IF;
   END IF;
  END IF;
  IF (NOT EXISTS(SELECT 1 FROM public.conversations WHERE organization_id=OLD.organization_id AND id=OLD.conversation_id)
      OR NOT EXISTS(SELECT 1 FROM public.contacts WHERE organization_id=OLD.organization_id AND id=OLD.subject_id)) THEN
   RETURN OLD;
  END IF;
 END IF;
 RAISE EXCEPTION 'Conversation suppression evidence is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.booking_revocation_immutable() FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.c24_cleanup_suppression(p_org uuid,p_conversation uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.gdpr_policies%ROWTYPE; conv public.conversations%ROWTYPE;
        fence_row record; watermark_row record;
        changed boolean:=false; capability_owner name;
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
 SELECT * INTO conv FROM public.conversations WHERE organization_id=p_org AND id=p_conversation FOR UPDATE;
 IF NOT FOUND OR conv.contact_id IS NULL THEN RETURN false; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org AND category='technical_receipts'
  AND approved_at<=clock_timestamp() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed
    OR p.retention_days IS NULL OR p.retention_days<1 OR p.retention_days>90 THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
   AND h.category='technical_receipts' AND (h.scope='organization'
    OR (h.scope='subject' AND h.scope_id=conv.contact_id)
    OR (h.scope='conversation' AND h.scope_id=p_conversation))
   AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
    WHERE e.organization_id=h.organization_id AND e.hold_id=h.hold_id AND e.kind='released')) THEN
  RETURN false;
 END IF;

 FOR fence_row IN SELECT * FROM public.c24_source_fences
  WHERE organization_id=p_org AND conversation_id=p_conversation
   AND fenced_at<=clock_timestamp()-make_interval(days=>p.retention_days)
  ORDER BY fenced_at,request_id FOR UPDATE
 LOOP
  IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
    AND h.category='technical_receipts' AND h.scope='record' AND h.scope_id=fence_row.request_id
    AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
     WHERE e.organization_id=h.organization_id AND e.hold_id=h.hold_id AND e.kind='released')) THEN
   CONTINUE;
  END IF;
  -- The fence is the final anti-replay proof. Prune only after its request
  -- receipt is gone and the original message remains as an immutable content
  -- purge tombstone, so a duplicate provider message still hits that row.
  IF EXISTS(SELECT 1 FROM public.booking_conversation_requests r WHERE r.organization_id=p_org
       AND r.request_id=fence_row.request_id)
     OR NOT EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=p_org
       AND m.conversation_id=p_conversation AND m.id=fence_row.source_message_id
       AND m.content_purged_at IS NOT NULL) THEN
   CONTINUE;
  END IF;
  INSERT INTO public.c24_suppression_cleanup_capability VALUES
   (DEFAULT,pg_backend_pid(),txid_current(),p_org,p_conversation,fence_row.subject_id,'source_fence',fence_row.request_id,NULL);
  INSERT INTO public.c24_suppression_lifecycle_audit
   (organization_id,subject_id,conversation_id,source_record_id,record_kind,record_digest,action)
   VALUES(p_org,fence_row.subject_id,p_conversation,fence_row.request_id,'source_fence',
    md5(concat_ws(':',p_org::text,'source_fence',fence_row.request_id::text)),'retention');
  DELETE FROM public.c24_source_fences WHERE organization_id=p_org AND request_id=fence_row.request_id;
  DELETE FROM public.c24_suppression_cleanup_capability WHERE backend_pid=pg_backend_pid()
   AND transaction_id=txid_current() AND organization_id=p_org AND conversation_id=p_conversation
   AND record_kind='source_fence' AND request_id=fence_row.request_id;
  changed:=true;
 END LOOP;

 FOR watermark_row IN SELECT * FROM public.c24_idle_watermarks
  WHERE organization_id=p_org AND conversation_id=p_conversation
   AND expired_at<=clock_timestamp()-make_interval(days=>p.retention_days)
  ORDER BY generation FOR UPDATE
 LOOP
  IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
    AND h.category='technical_receipts' AND h.scope='record' AND h.scope_id=p_conversation
    AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
     WHERE e.organization_id=h.organization_id AND e.hold_id=h.hold_id AND e.kind='released')) THEN
   CONTINUE;
  END IF;
  -- Active state and unpurged sources still rely on this cutoff. Receipts are
  -- independently retained for 90 days and must be cleaned first.
  IF EXISTS(SELECT 1 FROM public.booking_conversation_states s WHERE s.organization_id=p_org
      AND s.conversation_id=p_conversation)
     OR EXISTS(SELECT 1 FROM public.booking_conversation_requests r WHERE r.organization_id=p_org
      AND r.conversation_id=p_conversation)
     OR EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=p_org
      AND m.conversation_id=p_conversation AND m.direction='inbound'
      AND m.c24_received_at<=watermark_row.cutoff AND m.content_purged_at IS NULL) THEN
   CONTINUE;
  END IF;
  INSERT INTO public.c24_suppression_cleanup_capability VALUES
   (DEFAULT,pg_backend_pid(),txid_current(),p_org,p_conversation,watermark_row.subject_id,'idle_watermark',NULL,watermark_row.generation);
  INSERT INTO public.c24_suppression_lifecycle_audit
   (organization_id,subject_id,conversation_id,record_kind,record_digest,action)
   VALUES(p_org,watermark_row.subject_id,p_conversation,'idle_watermark',
    md5(concat_ws(':',p_org::text,'idle_watermark',p_conversation::text,watermark_row.generation::text)),'retention');
  DELETE FROM public.c24_idle_watermarks WHERE organization_id=p_org AND conversation_id=p_conversation
   AND generation=watermark_row.generation;
  DELETE FROM public.c24_suppression_cleanup_capability WHERE backend_pid=pg_backend_pid()
   AND transaction_id=txid_current() AND organization_id=p_org AND conversation_id=p_conversation
   AND record_kind='idle_watermark' AND generation=watermark_row.generation;
  changed:=true;
 END LOOP;
 RETURN changed;
END $$;
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
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org AND category='technical_receipts'
  AND approved_at<=clock_timestamp() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed
   OR p.retention_days IS NULL OR p.retention_days<1 OR p.retention_days>90
   OR a.occurred_at>clock_timestamp()-make_interval(days=>p.retention_days) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
   AND h.category='technical_receipts' AND (h.scope='organization'
     OR (h.scope='subject' AND h.scope_id=a.subject_id)
     OR (h.scope='conversation' AND h.scope_id=a.conversation_id)
     OR (h.scope='record' AND h.scope_id IN (p_audit,a.source_record_id,a.conversation_id)))
   AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
    WHERE e.organization_id=h.organization_id AND e.hold_id=h.hold_id AND e.kind='released')) THEN RETURN false; END IF;
 INSERT INTO public.c24_suppression_audit_cleanup_capability VALUES(pg_backend_pid(),txid_current(),p_org,p_audit);
 DELETE FROM public.c24_suppression_lifecycle_audit WHERE organization_id=p_org AND audit_id=p_audit;
 DELETE FROM public.c24_suppression_audit_cleanup_capability WHERE backend_pid=pg_backend_pid()
   AND transaction_id=txid_current() AND organization_id=p_org AND audit_id=p_audit;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.c24_cleanup_suppression_audit(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;

-- Erasure removes the suppression metadata only after a validated A06 barrier.
CREATE OR REPLACE FUNCTION public.c24_erase_suppression_at_barrier() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE row_data record; capability_owner name; p public.gdpr_policies%ROWTYPE;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.gdpr_operations o WHERE o.organization_id=NEW.organization_id
   AND o.id=NEW.operation_id AND o.kind='erase' AND o.scope=NEW.scope
   AND o.subject_id IS NOT DISTINCT FROM NEW.subject_id) THEN
  RAISE EXCEPTION 'Suppression erasure barrier does not match operation' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Suppression erasure requires read committed isolation' USING ERRCODE='25000';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.organization_id::text,606));
 IF NOT EXISTS(SELECT 1 FROM public.c24_source_fences f WHERE f.organization_id=NEW.organization_id
       AND (NEW.scope='organization' OR f.subject_id=NEW.subject_id))
    AND NOT EXISTS(SELECT 1 FROM public.c24_idle_watermarks w WHERE w.organization_id=NEW.organization_id
       AND (NEW.scope='organization' OR w.subject_id=NEW.subject_id)) THEN RETURN NEW; END IF;
 SELECT pg_get_userbyid(relowner) INTO capability_owner FROM pg_class
  WHERE oid='public.c24_suppression_cleanup_capability'::regclass;
 IF COALESCE(NULLIF(current_setting('role'),'none'),session_user::text) IS DISTINCT FROM capability_owner::text THEN
  RAISE EXCEPTION 'Suppression erasure requires the lifecycle owner' USING ERRCODE='42501';
 END IF;
 IF NEW.scope='contact' AND EXISTS(SELECT 1 FROM public.contacts c WHERE c.organization_id=NEW.organization_id
   AND c.id=NEW.subject_id AND (c.deleted_at IS NULL OR c.phone_number IS DISTINCT FROM 'erased:'||c.id::text)) THEN
  RAISE EXCEPTION 'Erasure contact is not redacted' USING ERRCODE='42501';
 END IF;
 IF NEW.scope='organization' AND EXISTS(SELECT 1 FROM public.contacts c WHERE c.organization_id=NEW.organization_id
   AND (c.deleted_at IS NULL OR c.phone_number IS DISTINCT FROM 'erased:'||c.id::text)) THEN
  RAISE EXCEPTION 'Erasure organization contacts are not redacted' USING ERRCODE='42501';
 END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=NEW.organization_id
  AND category='technical_receipts' AND approved_at<=clock_timestamp()
  ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed THEN
  RAISE EXCEPTION 'Suppression erasure policy required' USING ERRCODE='55000';
 END IF;
 FOR row_data IN SELECT * FROM public.c24_source_fences f WHERE f.organization_id=NEW.organization_id
   AND (NEW.scope='organization' OR f.subject_id=NEW.subject_id) FOR UPDATE
 LOOP
  IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=NEW.organization_id
    AND h.category='technical_receipts' AND (h.scope='organization'
     OR (h.scope='subject' AND h.scope_id=row_data.subject_id)
     OR (h.scope='conversation' AND h.scope_id=row_data.conversation_id)
     OR (h.scope='record' AND h.scope_id=row_data.request_id))
    AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
     WHERE e.organization_id=h.organization_id AND e.hold_id=h.hold_id AND e.kind='released')) THEN
   RAISE EXCEPTION 'Suppression erasure held' USING ERRCODE='55000';
  END IF;
  INSERT INTO public.c24_suppression_cleanup_capability VALUES
   (DEFAULT,pg_backend_pid(),txid_current(),NEW.organization_id,row_data.conversation_id,row_data.subject_id,'source_fence',row_data.request_id,NULL);
  INSERT INTO public.c24_suppression_lifecycle_audit
   (organization_id,subject_id,conversation_id,source_record_id,operation_id,record_kind,record_digest,action)
   VALUES(NEW.organization_id,row_data.subject_id,row_data.conversation_id,row_data.request_id,NEW.operation_id,
    'source_fence',md5(concat_ws(':',NEW.organization_id::text,'source_fence',row_data.request_id::text)),'erasure');
  DELETE FROM public.c24_source_fences WHERE organization_id=NEW.organization_id AND request_id=row_data.request_id;
  DELETE FROM public.c24_suppression_cleanup_capability WHERE backend_pid=pg_backend_pid()
   AND transaction_id=txid_current() AND organization_id=NEW.organization_id
   AND record_kind='source_fence' AND request_id=row_data.request_id;
 END LOOP;
 FOR row_data IN SELECT * FROM public.c24_idle_watermarks w WHERE w.organization_id=NEW.organization_id
   AND (NEW.scope='organization' OR w.subject_id=NEW.subject_id) FOR UPDATE
 LOOP
  IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=NEW.organization_id
    AND h.category='technical_receipts' AND (h.scope='organization'
     OR (h.scope='subject' AND h.scope_id=row_data.subject_id)
     OR (h.scope='conversation' AND h.scope_id=row_data.conversation_id)
     OR (h.scope='record' AND h.scope_id=row_data.conversation_id))
    AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
     WHERE e.organization_id=h.organization_id AND e.hold_id=h.hold_id AND e.kind='released')) THEN
   RAISE EXCEPTION 'Suppression erasure held' USING ERRCODE='55000';
  END IF;
  INSERT INTO public.c24_suppression_cleanup_capability VALUES
   (DEFAULT,pg_backend_pid(),txid_current(),NEW.organization_id,row_data.conversation_id,row_data.subject_id,'idle_watermark',NULL,row_data.generation);
  INSERT INTO public.c24_suppression_lifecycle_audit
   (organization_id,subject_id,conversation_id,operation_id,record_kind,record_digest,action)
   VALUES(NEW.organization_id,row_data.subject_id,row_data.conversation_id,NEW.operation_id,'idle_watermark',
    md5(concat_ws(':',NEW.organization_id::text,'idle_watermark',row_data.conversation_id::text,row_data.generation::text)),'erasure');
  DELETE FROM public.c24_idle_watermarks WHERE organization_id=NEW.organization_id
   AND conversation_id=row_data.conversation_id AND generation=row_data.generation;
  DELETE FROM public.c24_suppression_cleanup_capability WHERE backend_pid=pg_backend_pid()
   AND transaction_id=txid_current() AND organization_id=NEW.organization_id
   AND record_kind='idle_watermark' AND generation=row_data.generation;
 END LOOP;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_erase_suppression_at_barrier() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS c24_erase_suppression_at_barrier ON public.gdpr_erasure_barriers;
CREATE TRIGGER c24_erase_suppression_at_barrier AFTER INSERT ON public.gdpr_erasure_barriers
 FOR EACH ROW EXECUTE FUNCTION public.c24_erase_suppression_at_barrier();

CREATE OR REPLACE FUNCTION public.c24_run_retention_target(p_kind text,p_org uuid,p_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.c24_retention_operator_allowed() THEN
  RAISE EXCEPTION 'Dedicated retention operator required' USING ERRCODE='42501';
 END IF;
 CASE p_kind
  WHEN 'message' THEN RETURN public.c24_purge_source(p_org,p_id);
  WHEN 'conversation' THEN RETURN public.c24_expire_idle(p_org,p_id);
  WHEN 'receipt' THEN RETURN public.c24_cleanup_receipt(p_org,p_id);
  WHEN 'suppression' THEN RETURN public.c24_cleanup_suppression(p_org,p_id);
  WHEN 'suppression_audit' THEN RETURN public.c24_cleanup_suppression_audit(p_org,p_id);
  ELSE RAISE EXCEPTION 'Invalid retention target kind' USING ERRCODE='22023';
 END CASE;
END $$;
REVOKE ALL ON FUNCTION public.c24_run_retention_target(text,uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.c24_run_retention_target(text,uuid,uuid) TO melpis_c24_retention;
