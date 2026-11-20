SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Durable per-source replay evidence for a narrowly verified, never-applied
-- proposal. The encrypted v5 root retains the authenticated original tuple.
CREATE TABLE IF NOT EXISTS public.c24_orphan_proposal_expirations (
 organization_id uuid NOT NULL,
 source_message_id uuid NOT NULL,
 quota_operation_id uuid NOT NULL,
 plan jsonb NOT NULL CHECK(jsonb_typeof(plan)='object'),
 checkpoint_sha256 text NOT NULL CHECK(checkpoint_sha256 ~ '^[0-9a-f]{64}$'),
 checkpoint_sequence bigint NOT NULL CHECK(checkpoint_sequence>0),
 inventory_sha256 text NOT NULL CHECK(inventory_sha256 ~ '^[0-9a-f]{64}$'),
 expired_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(organization_id,source_message_id),
 UNIQUE(organization_id,quota_operation_id)
);
ALTER TABLE public.c24_orphan_proposal_expirations ENABLE ROW LEVEL SECURITY;
CREATE UNIQUE INDEX IF NOT EXISTS c24_orphan_proposal_delivery_idx ON public.c24_orphan_proposal_expirations
 (organization_id,(plan->>'provider_message_sha256'));
REVOKE ALL ON public.c24_orphan_proposal_expirations
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;
DROP TRIGGER IF EXISTS c24_orphan_expiry_immutable ON public.c24_orphan_proposal_expirations;
CREATE TRIGGER c24_orphan_expiry_immutable BEFORE UPDATE OR DELETE ON public.c24_orphan_proposal_expirations
 FOR EACH ROW EXECUTE FUNCTION public.gdpr_immutable_record();
DROP TRIGGER IF EXISTS c24_orphan_expiry_no_truncate ON public.c24_orphan_proposal_expirations;
CREATE TRIGGER c24_orphan_expiry_no_truncate BEFORE TRUNCATE ON public.c24_orphan_proposal_expirations
 FOR EACH STATEMENT EXECUTE FUNCTION public.gdpr_immutable_record();

CREATE OR REPLACE FUNCTION public.c24_orphan_expiry_fence() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE source uuid; operation uuid; delivery_sha text;
BEGIN
 IF TG_TABLE_NAME='messages' THEN
  IF NOT COALESCE(NEW.ai_reply_cache ? 'booking_state_proposal',false) THEN RETURN NEW; END IF;
  source:=NEW.id; delivery_sha:=encode(sha256(convert_to(NEW.wam_id,'UTF8')),'hex');
 ELSIF TG_TABLE_NAME='simulation_requests' THEN
  IF NOT COALESCE(NEW.response ? 'booking_state_proposal',false) THEN RETURN NEW; END IF;
  source:=NEW.c24_source_message_id; operation:=NEW.request_id;
 ELSE
  source:=NEW.source_message_id;
 END IF;
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'Orphan proposal lineage requires read committed isolation' USING ERRCODE='25000'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,606));
 IF TG_TABLE_NAME<>'messages' THEN
  SELECT encode(sha256(convert_to(m.wam_id,'UTF8')),'hex') INTO delivery_sha
  FROM public.messages m WHERE m.organization_id=NEW.organization_id AND m.id=source;
 END IF;
 IF EXISTS(SELECT FROM public.c24_orphan_proposal_expirations f
   WHERE f.organization_id=NEW.organization_id AND (f.source_message_id=source OR f.quota_operation_id=operation
     OR f.plan->>'provider_message_sha256'=delivery_sha)) THEN
  RAISE EXCEPTION 'Expired orphan proposal cannot be reapplied' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='booking_conversation_states' THEN
  IF EXISTS(SELECT FROM public.c24_orphan_proposal_expirations f WHERE f.organization_id=NEW.organization_id
      AND f.plan->>'conversation_id'=NEW.conversation_id::text
      AND jsonb_path_exists(NEW.snapshot,'$.**.source_message_id ? (@ == $source)',
                            jsonb_build_object('source',f.source_message_id::text))) THEN
   RAISE EXCEPTION 'Expired orphan proposal cannot be reapplied' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_orphan_expiry_fence() FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;
DROP TRIGGER IF EXISTS c24_orphan_expiry_fence ON public.messages;
CREATE TRIGGER c24_orphan_expiry_fence BEFORE INSERT OR UPDATE OF id,organization_id,wam_id,ai_reply_cache ON public.messages
 FOR EACH ROW EXECUTE FUNCTION public.c24_orphan_expiry_fence();
DROP TRIGGER IF EXISTS c24_orphan_expiry_fence ON public.simulation_requests;
CREATE TRIGGER c24_orphan_expiry_fence BEFORE INSERT OR UPDATE OF organization_id,request_id,c24_source_message_id,response
 ON public.simulation_requests FOR EACH ROW EXECUTE FUNCTION public.c24_orphan_expiry_fence();
DROP TRIGGER IF EXISTS c24_orphan_expiry_fence ON public.booking_conversation_states;
CREATE TRIGGER c24_orphan_expiry_fence BEFORE INSERT OR UPDATE OF organization_id,source_message_id,snapshot
 ON public.booking_conversation_states FOR EACH ROW EXECUTE FUNCTION public.c24_orphan_expiry_fence();
DROP TRIGGER IF EXISTS c24_orphan_expiry_fence ON public.booking_conversation_requests;
CREATE TRIGGER c24_orphan_expiry_fence BEFORE INSERT OR UPDATE OF organization_id,source_message_id
 ON public.booking_conversation_requests FOR EACH ROW EXECUTE FUNCTION public.c24_orphan_expiry_fence();

CREATE OR REPLACE FUNCTION public.c24_verify_orphan_proposal_expiry(p_org uuid,p_source uuid,p_plan jsonb)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT EXISTS(SELECT FROM public.c24_orphan_proposal_expirations f
   WHERE f.organization_id=p_org AND f.source_message_id=p_source AND f.plan=p_plan
     AND NOT EXISTS(SELECT FROM public.messages WHERE organization_id=p_org AND id=p_source
       AND COALESCE(ai_reply_cache ? 'booking_state_proposal',false))
     AND NOT EXISTS(SELECT FROM public.messages WHERE organization_id=p_org
       AND COALESCE(ai_reply_cache ? 'booking_state_proposal',false)
       AND encode(sha256(convert_to(wam_id,'UTF8')),'hex')=f.plan->>'provider_message_sha256')
     AND NOT EXISTS(SELECT FROM public.simulation_requests WHERE organization_id=p_org
       AND (c24_source_message_id=p_source OR request_id=f.quota_operation_id)
       AND COALESCE(response ? 'booking_state_proposal',false))
     AND NOT EXISTS(SELECT FROM public.booking_conversation_requests WHERE organization_id=p_org AND source_message_id=p_source)
     AND NOT EXISTS(SELECT FROM public.booking_conversation_states WHERE organization_id=p_org
       AND (source_message_id=p_source OR jsonb_path_exists(snapshot,'$.**.source_message_id ? (@ == $source)',
                                          jsonb_build_object('source',p_source::text)))));
$$;
REVOKE ALL ON FUNCTION public.c24_verify_orphan_proposal_expiry(uuid,uuid,jsonb)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION public.c24_apply_orphan_proposal_expiry(
 p_org uuid,p_source uuid,p_plan jsonb,p_checkpoint_sha text,p_sequence bigint,p_inventory_sha text,
 p_current_policy text DEFAULT NULL,p_backup_completed timestamptz DEFAULT NULL
) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE conversation uuid; subject uuid; operation uuid; receipt timestamptz; affected integer;
        current_policy public.gdpr_policies%ROWTYPE; proposal jsonb;
BEGIN
 IF current_user<>session_user OR current_setting('transaction_isolation')<>'read committed'
    OR NOT EXISTS(SELECT FROM pg_class WHERE oid='public.c24_orphan_proposal_expirations'::regclass
      AND relowner=current_user::regrole) THEN RETURN false; END IF;
 IF p_plan->>'kind' IS DISTINCT FROM 'known_source_orphan_proposal_candidate_v1'
    OR p_plan->>'organization_id' IS DISTINCT FROM p_org::text OR p_plan->>'source_message_id' IS DISTINCT FROM p_source::text
    OR p_plan->'deletion_authorized' IS DISTINCT FROM 'false'::jsonb
    OR NOT COALESCE(p_plan->>'provider_message_sha256' ~ '^[0-9a-f]{64}$',false)
    OR NOT COALESCE(p_checkpoint_sha ~ '^[0-9a-f]{64}$' AND p_inventory_sha ~ '^[0-9a-f]{64}$' AND p_sequence>0,false)
 THEN RETURN false; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 IF public.c24_verify_orphan_proposal_expiry(p_org,p_source,p_plan) THEN RETURN true; END IF;
 IF EXISTS(SELECT FROM public.c24_orphan_proposal_expirations WHERE organization_id=p_org AND source_message_id=p_source)
 THEN RETURN false; END IF;
 conversation:=(p_plan->>'conversation_id')::uuid;
 subject:=(p_plan->>'subject_id')::uuid;
 operation:=(p_plan->>'quota_operation_id')::uuid;
 receipt:=(p_plan->>'received_at')::timestamptz;
 SELECT * INTO current_policy FROM public.gdpr_policies WHERE organization_id=p_org
  AND category='booking_conversation_content' AND approved_at<=clock_timestamp()
  ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR current_policy.version IS DISTINCT FROM COALESCE(p_current_policy,p_plan->>'policy_version')
    OR current_policy.valid_until<=clock_timestamp() OR current_policy.legal_hold OR NOT current_policy.erasure_allowed
    OR NOT COALESCE(current_policy.retention_days BETWEEN 1 AND 30 AND receipt<=clock_timestamp()-make_interval(days=>current_policy.retention_days),false)
    OR EXISTS(SELECT FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
          AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=subject)
            OR (h.scope='conversation' AND h.scope_id=conversation) OR (h.scope='record' AND h.scope_id IN(p_source,operation)))
          AND NOT EXISTS(SELECT FROM public.gdpr_retention_hold_events e WHERE e.organization_id=p_org
            AND e.hold_id=h.hold_id AND e.kind='released'))
 THEN RETURN false; END IF;
 IF p_backup_completed IS NOT NULL THEN
  -- The independent manifest proves that this source was received after the
  -- entire backup completed. No missing row alone qualifies for a no-op.
  IF receipt<=p_backup_completed
     OR EXISTS(SELECT FROM public.messages WHERE organization_id=p_org AND (id=p_source
         OR encode(sha256(convert_to(wam_id,'UTF8')),'hex')=p_plan->>'provider_message_sha256'))
     OR EXISTS(SELECT FROM public.simulation_requests WHERE organization_id=p_org
       AND (c24_source_message_id=p_source OR request_id=operation)) THEN RETURN false; END IF;
  INSERT INTO public.c24_orphan_proposal_expirations
   (organization_id,source_message_id,quota_operation_id,plan,checkpoint_sha256,checkpoint_sequence,inventory_sha256)
  VALUES(p_org,p_source,operation,p_plan,p_checkpoint_sha,p_sequence,p_inventory_sha);
  RETURN public.c24_verify_orphan_proposal_expiry(p_org,p_source,p_plan);
 END IF;
 IF EXISTS(SELECT FROM public.booking_conversation_states WHERE organization_id=p_org AND conversation_id=conversation)
   OR EXISTS(SELECT FROM public.booking_conversation_requests WHERE organization_id=p_org AND conversation_id=conversation)
 THEN RETURN false; END IF;
 SELECT ai_reply_cache INTO proposal FROM public.messages WHERE organization_id=p_org AND id=p_source
  AND conversation_id=conversation AND c24_original_subject_id=subject AND c24_received_at=receipt
  AND direction='inbound' AND status='handled' AND billed_at IS NOT NULL AND replied_at IS NOT NULL
  AND encode(sha256(convert_to(wam_id,'UTF8')),'hex')=p_plan->>'provider_message_sha256'
  AND content_purged_at IS NULL AND deleted_at IS NULL AND COALESCE(ai_reply_cache ? 'booking_state_proposal',false) FOR UPDATE;
 IF NOT FOUND OR NOT EXISTS(SELECT FROM public.usage_events WHERE organization_id=p_org AND id=operation
   AND event_type='system.quota_reservation' AND quantity=0 AND metadata->>'channel'='inbound'
   AND metadata->>'state'='committed' AND metadata->>'response_hash'=p_plan->>'response_hash') THEN RETURN false; END IF;
 INSERT INTO public.c24_orphan_proposal_expirations
  (organization_id,source_message_id,quota_operation_id,plan,checkpoint_sha256,checkpoint_sequence,inventory_sha256)
 VALUES(p_org,p_source,operation,p_plan,p_checkpoint_sha,p_sequence,p_inventory_sha);
 INSERT INTO public.c24_idle_copy_cleanup_capability VALUES(pg_backend_pid(),txid_current(),p_org,conversation,subject);
 UPDATE public.messages SET ai_reply_cache=ai_reply_cache-'booking_state_proposal'
  WHERE organization_id=p_org AND id=p_source;
 UPDATE public.simulation_requests SET response=response-'booking_state_proposal'
  WHERE organization_id=p_org AND request_id=operation AND c24_source_message_id=p_source
    AND c24_conversation_id=conversation AND c24_subject_id=subject AND status='completed' AND response=proposal;
 GET DIAGNOSTICS affected=ROW_COUNT;
 DELETE FROM public.c24_idle_copy_cleanup_capability WHERE backend_pid=pg_backend_pid()
  AND transaction_id=txid_current() AND organization_id=p_org AND conversation_id=conversation AND subject_id=subject;
 IF affected<>1 OR NOT public.c24_verify_orphan_proposal_expiry(p_org,p_source,p_plan) THEN
  RAISE EXCEPTION 'Orphan proposal expiry could not verify exact copies' USING ERRCODE='55000'; END IF;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.c24_apply_orphan_proposal_expiry(uuid,uuid,jsonb,text,bigint,text,text,timestamptz)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;
