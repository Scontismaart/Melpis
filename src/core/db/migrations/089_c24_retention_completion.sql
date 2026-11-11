-- C2.4 lifecycle completion. No role is granted new table access and no
-- retention or erasure execution is enabled by this migration.
CREATE TABLE IF NOT EXISTS public.c24_idle_copy_cleanup_capability (
 backend_pid integer NOT NULL, transaction_id bigint NOT NULL,
 organization_id uuid NOT NULL, conversation_id uuid NOT NULL, subject_id uuid NOT NULL,
 PRIMARY KEY(backend_pid,transaction_id,organization_id,conversation_id,subject_id)
);
ALTER TABLE public.c24_idle_copy_cleanup_capability ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.c24_idle_copy_cleanup_capability
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;

CREATE OR REPLACE FUNCTION public.c24_expired_proposal_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE proposal boolean; org uuid; conversation uuid; source uuid;
BEGIN
 IF TG_TABLE_NAME='messages' THEN
  proposal:=COALESCE(NEW.ai_reply_cache ? 'booking_state_proposal',false);
  org:=NEW.organization_id; conversation:=NEW.conversation_id; source:=NEW.id;
 ELSE
  proposal:=COALESCE(NEW.response ? 'booking_state_proposal',false);
  org:=NEW.organization_id; conversation:=NEW.c24_conversation_id; source:=NEW.c24_source_message_id;
 END IF;
 IF proposal AND conversation IS NOT NULL AND source IS NOT NULL AND EXISTS(
  SELECT 1 FROM public.c24_source_fences f JOIN public.c24_idle_watermarks w
   ON w.organization_id=f.organization_id AND w.conversation_id=f.conversation_id
  WHERE f.organization_id=org AND f.conversation_id=conversation
    AND f.source_message_id=source AND f.fenced_at<=w.expired_at
 ) THEN RAISE EXCEPTION 'Expired proposal cannot be restored' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_expired_proposal_guard() FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;
DROP TRIGGER IF EXISTS c24_expired_proposal_guard ON public.messages;
CREATE TRIGGER c24_expired_proposal_guard BEFORE INSERT OR UPDATE ON public.messages
 FOR EACH ROW EXECUTE FUNCTION public.c24_expired_proposal_guard();
DROP TRIGGER IF EXISTS c24_expired_proposal_guard ON public.simulation_requests;
CREATE TRIGGER c24_expired_proposal_guard BEFORE INSERT OR UPDATE ON public.simulation_requests
 FOR EACH ROW EXECUTE FUNCTION public.c24_expired_proposal_guard();

CREATE OR REPLACE FUNCTION public.booking_state_parent_lifecycle() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(OLD.organization_id::text,606));
 IF TG_TABLE_NAME='conversations' THEN
  IF TG_OP='UPDATE' AND (NEW.organization_id IS DISTINCT FROM OLD.organization_id OR NEW.contact_id IS DISTINCT FROM OLD.contact_id)
     AND (EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=OLD.organization_id AND m.conversation_id=OLD.id)
       OR EXISTS(SELECT 1 FROM public.booking_conversation_states s WHERE s.organization_id=OLD.organization_id AND s.conversation_id=OLD.id)
       OR EXISTS(SELECT 1 FROM public.booking_conversation_revocations r WHERE r.organization_id=OLD.organization_id AND r.conversation_id=OLD.id)) THEN
   RAISE EXCEPTION 'Conversation subject lineage is immutable once messages exist' USING ERRCODE='55000';
  END IF;
 ELSIF TG_TABLE_NAME='messages' THEN
  -- Serialize invalidation with the repository's conversation-first scope.
  -- A conflicting lock order may abort a transaction, which fails closed.
  PERFORM 1 FROM public.conversations WHERE organization_id=OLD.organization_id AND id=OLD.conversation_id FOR UPDATE;
  IF TG_OP='UPDATE' AND (NEW.organization_id IS DISTINCT FROM OLD.organization_id OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id) THEN
   RAISE EXCEPTION 'Message lineage is immutable' USING ERRCODE='55000';
  END IF;
  IF TG_OP='UPDATE' AND OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL THEN
   RAISE EXCEPTION 'Message resurrection is forbidden' USING ERRCODE='55000';
  END IF;
  IF TG_OP='DELETE' OR (OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL)
      OR (OLD.ai_reply_cache ? 'booking_state_proposal' AND NOT COALESCE(NEW.ai_reply_cache ? 'booking_state_proposal',false) AND NEW.content_purged_at IS NULL
        AND NOT EXISTS(SELECT 1 FROM public.c24_idle_copy_cleanup_capability c
          WHERE c.backend_pid=pg_backend_pid() AND c.transaction_id=txid_current()
            AND c.organization_id=OLD.organization_id AND c.conversation_id=OLD.conversation_id
            AND c.subject_id=OLD.c24_original_subject_id)) THEN
   INSERT INTO public.booking_conversation_revocations(organization_id,conversation_id,subject_id,reason)
    SELECT c.organization_id,c.id,c.contact_id,CASE WHEN TG_OP='DELETE' OR NEW.deleted_at IS NOT NULL THEN 'message_redacted' ELSE 'proposal_removed' END
    FROM public.conversations c WHERE c.organization_id=OLD.organization_id AND c.id=OLD.conversation_id AND c.contact_id IS NOT NULL
    ON CONFLICT DO NOTHING;
   DELETE FROM public.booking_conversation_states WHERE organization_id=OLD.organization_id AND conversation_id=OLD.conversation_id;
  END IF;
 ELSIF TG_TABLE_NAME='contacts' THEN
  PERFORM 1 FROM public.conversations WHERE organization_id=OLD.organization_id AND contact_id=OLD.id ORDER BY id FOR UPDATE;
  IF TG_OP='UPDATE' AND OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL THEN
   RAISE EXCEPTION 'Contact resurrection is forbidden' USING ERRCODE='55000';
  END IF;
  IF TG_OP='DELETE' OR (OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL)
      OR (NEW.consent_status='withdrawn' AND OLD.consent_status IS DISTINCT FROM 'withdrawn') THEN
   INSERT INTO public.booking_conversation_revocations(organization_id,conversation_id,subject_id,reason)
    SELECT c.organization_id,c.id,c.contact_id,CASE WHEN TG_OP='DELETE' OR NEW.deleted_at IS NOT NULL THEN 'contact_erased' ELSE 'consent_withdrawn' END
    FROM public.conversations c WHERE c.organization_id=OLD.organization_id AND c.contact_id=OLD.id
    ON CONFLICT DO NOTHING;
   DELETE FROM public.booking_conversation_states WHERE organization_id=OLD.organization_id AND subject_id=OLD.id;
  END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_state_parent_lifecycle() FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.booking_simulation_lifecycle() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(OLD.organization_id::text,606));
 IF TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=OLD.organization_id
   AND m.conversation_id=OLD.c24_conversation_id AND m.id=OLD.c24_source_message_id
   AND m.content_purged_at IS NOT NULL) AND NEW.response IS DISTINCT FROM '{}'::jsonb THEN
  RAISE EXCEPTION 'Purged canonical response cannot be restored' USING ERRCODE='55000';
 END IF;
 IF TG_OP='UPDATE' THEN
  IF OLD.c24_subject_id IS NOT NULL AND
     (NEW.c24_subject_id IS DISTINCT FROM OLD.c24_subject_id OR NEW.c24_conversation_id IS DISTINCT FROM OLD.c24_conversation_id
      OR NEW.c24_source_message_id IS DISTINCT FROM OLD.c24_source_message_id OR NEW.organization_id IS DISTINCT FROM OLD.organization_id) THEN
   RAISE EXCEPTION 'Canonical response subject lineage is immutable' USING ERRCODE='55000';
  END IF;
  IF NEW.c24_subject_id IS NOT NULL AND NOT EXISTS(
     SELECT 1 FROM public.messages m WHERE m.organization_id=NEW.organization_id AND m.id=NEW.c24_source_message_id
       AND m.conversation_id=NEW.c24_conversation_id AND m.c24_original_subject_id=NEW.c24_subject_id AND m.deleted_at IS NULL)
     AND OLD.c24_subject_id IS NULL THEN
   RAISE EXCEPTION 'Canonical response subject lineage unverified' USING ERRCODE='55000';
  END IF;
 END IF;
 IF OLD.c24_subject_id IS NOT NULL AND OLD.response ? 'booking_state_proposal'
    AND (TG_OP='DELETE' OR NOT COALESCE(NEW.response ? 'booking_state_proposal',false))
    AND NOT EXISTS (SELECT 1 FROM public.messages m WHERE m.organization_id=OLD.organization_id
     AND m.conversation_id=OLD.c24_conversation_id AND m.id=OLD.c24_source_message_id
     AND m.content_purged_at IS NOT NULL AND m.deleted_at IS NULL)
    AND NOT EXISTS(SELECT 1 FROM public.c24_idle_copy_cleanup_capability c
      WHERE c.backend_pid=pg_backend_pid() AND c.transaction_id=txid_current()
        AND c.organization_id=OLD.organization_id AND c.conversation_id=OLD.c24_conversation_id
        AND c.subject_id=OLD.c24_subject_id) THEN
  PERFORM 1 FROM public.conversations WHERE organization_id=OLD.organization_id AND id=OLD.c24_conversation_id FOR UPDATE;
  INSERT INTO public.booking_conversation_revocations(organization_id,conversation_id,subject_id,reason)
   SELECT c.organization_id,c.id,OLD.c24_subject_id,'proposal_removed'
   FROM public.conversations c WHERE c.organization_id=OLD.organization_id AND c.id=OLD.c24_conversation_id
   ON CONFLICT DO NOTHING;
  DELETE FROM public.booking_conversation_states WHERE organization_id=OLD.organization_id AND conversation_id=OLD.c24_conversation_id;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_simulation_lifecycle() FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.c24_expire_idle(p_org uuid,p_conversation uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s public.booking_conversation_states%ROWTYPE; p public.gdpr_policies%ROWTYPE; next_generation bigint; activity timestamptz;
BEGIN
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'Content retention requires read committed isolation' USING ERRCODE='25000';
 END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(p_org::text,606));
 PERFORM 1 FROM public.conversations WHERE organization_id=p_org AND id=p_conversation FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org
  AND category='booking_conversation_content' AND approved_at<=clock_timestamp()
  ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed
    OR p.retention_days IS NULL OR p.retention_days<1 OR p.retention_days>30 THEN RETURN false; END IF;
 SELECT * INTO s FROM public.booking_conversation_states WHERE organization_id=p_org AND conversation_id=p_conversation FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 -- The last applied source's server-owned receive time, not the time at which
 -- processing happened, is the retention anchor. Do not guess old lineage.
 SELECT m.c24_received_at INTO activity FROM public.messages m
  WHERE m.organization_id=p_org AND m.conversation_id=p_conversation
    AND m.id=s.source_message_id AND m.direction='inbound'
    AND m.c24_original_subject_id=s.subject_id;
 IF activity IS NULL OR activity>clock_timestamp()
    OR activity>clock_timestamp()-make_interval(days=>p.retention_days) THEN RETURN false; END IF;
 -- Unattributed proposals may belong to this subject. They require explicit
 -- reconciliation and must not be silently treated as deleted.
 IF EXISTS(SELECT 1 FROM public.simulation_requests r WHERE r.organization_id=p_org
   AND COALESCE(r.response ? 'booking_state_proposal',false) AND r.c24_subject_id IS NULL) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=p_org AND m.conversation_id=p_conversation
   AND COALESCE(m.ai_reply_cache ? 'booking_state_proposal',false)
   AND (m.c24_original_subject_id IS DISTINCT FROM s.subject_id OR m.c24_received_at IS NULL)) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.simulation_requests r LEFT JOIN public.messages m
   ON m.organization_id=r.organization_id AND m.conversation_id=r.c24_conversation_id
    AND m.id=r.c24_source_message_id AND m.c24_original_subject_id=r.c24_subject_id
   WHERE r.organization_id=p_org AND r.c24_conversation_id=p_conversation AND r.c24_subject_id=s.subject_id
    AND COALESCE(r.response ? 'booking_state_proposal',false)
    AND (m.id IS NULL OR m.c24_received_at IS NULL)) THEN RETURN false; END IF;
 -- Do not expire an older generation while a newer relevant request is in
 -- progress or has a proposal waiting to be applied.
 IF EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=p_org AND m.conversation_id=p_conversation
   AND m.direction='inbound' AND m.c24_original_subject_id=s.subject_id AND m.c24_received_at>activity
   AND m.c24_received_at<=activity+make_interval(days=>p.retention_days)
   AND (m.status IN ('received_pending_ai','processing')
     OR COALESCE(m.ai_reply_cache ? 'booking_state_proposal',false)
     OR EXISTS(SELECT 1 FROM public.simulation_requests r WHERE r.organization_id=p_org
       AND r.c24_conversation_id=p_conversation AND r.c24_subject_id=s.subject_id
       AND r.c24_source_message_id=m.id AND COALESCE(r.response ? 'booking_state_proposal',false))))
 THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
  AND h.category='booking_conversation_content' AND (h.scope='organization'
   OR (h.scope='subject' AND h.scope_id=s.subject_id) OR (h.scope='conversation' AND h.scope_id=p_conversation)
   OR (h.scope='record' AND h.scope_id=p_conversation))
  AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e WHERE e.organization_id=h.organization_id
   AND e.hold_id=h.hold_id AND e.kind='released')) THEN RETURN false; END IF;
 SELECT COALESCE(max(generation),0)+1 INTO next_generation FROM public.c24_idle_watermarks
  WHERE organization_id=p_org AND conversation_id=p_conversation;
 -- Fence every verified old proposal source before destroying either copy.
 -- The unique source key makes concurrent/replayed insertion idempotent.
 INSERT INTO public.c24_source_fences(organization_id,conversation_id,subject_id,request_id,source_message_id)
 SELECT p_org,p_conversation,s.subject_id,gen_random_uuid(),m.id FROM public.messages m
 WHERE m.organization_id=p_org AND m.conversation_id=p_conversation
   AND m.c24_original_subject_id=s.subject_id AND m.c24_received_at<=activity
   AND (COALESCE(m.ai_reply_cache ? 'booking_state_proposal',false)
     OR EXISTS(SELECT 1 FROM public.simulation_requests r WHERE r.organization_id=p_org
       AND r.c24_conversation_id=p_conversation AND r.c24_subject_id=s.subject_id
       AND r.c24_source_message_id=m.id AND COALESCE(r.response ? 'booking_state_proposal',false)))
 ON CONFLICT DO NOTHING;
 IF EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=p_org AND m.conversation_id=p_conversation
   AND m.c24_original_subject_id=s.subject_id AND m.c24_received_at<=activity
   AND (COALESCE(m.ai_reply_cache ? 'booking_state_proposal',false)
     OR EXISTS(SELECT 1 FROM public.simulation_requests r WHERE r.organization_id=p_org
       AND r.c24_conversation_id=p_conversation AND r.c24_subject_id=s.subject_id
       AND r.c24_source_message_id=m.id AND COALESCE(r.response ? 'booking_state_proposal',false)))
   AND NOT EXISTS(SELECT 1 FROM public.c24_source_fences f WHERE f.organization_id=p_org
     AND f.conversation_id=p_conversation AND f.subject_id=s.subject_id AND f.source_message_id=m.id))
 THEN RAISE EXCEPTION 'Expired proposal fence unavailable' USING ERRCODE='55000'; END IF;
 INSERT INTO public.c24_idle_watermarks(organization_id,conversation_id,subject_id,generation,cutoff,provider_timestamp)
 VALUES(p_org,p_conversation,s.subject_id,next_generation,activity+make_interval(days=>p.retention_days),s.provider_timestamp);
 INSERT INTO public.c24_idle_copy_cleanup_capability
 VALUES(pg_backend_pid(),txid_current(),p_org,p_conversation,s.subject_id);
 UPDATE public.messages m SET ai_reply_cache=m.ai_reply_cache-'booking_state_proposal'
 WHERE m.organization_id=p_org AND m.conversation_id=p_conversation
   AND m.c24_original_subject_id=s.subject_id AND m.c24_received_at<=activity
   AND COALESCE(m.ai_reply_cache ? 'booking_state_proposal',false);
 UPDATE public.simulation_requests r SET response=r.response-'booking_state_proposal'
 FROM public.messages m WHERE r.organization_id=p_org AND r.c24_conversation_id=p_conversation
   AND r.c24_subject_id=s.subject_id AND m.organization_id=p_org AND m.conversation_id=p_conversation
   AND m.id=r.c24_source_message_id AND m.c24_original_subject_id=s.subject_id
   AND m.c24_received_at<=activity AND COALESCE(r.response ? 'booking_state_proposal',false);
 DELETE FROM public.booking_conversation_states WHERE organization_id=p_org AND conversation_id=p_conversation;
 DELETE FROM public.c24_idle_copy_cleanup_capability
 WHERE backend_pid=pg_backend_pid() AND transaction_id=txid_current()
   AND organization_id=p_org AND conversation_id=p_conversation AND subject_id=s.subject_id;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.c24_expire_idle(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;


-- Raw, completed webhook envelopes are a distinct copy of message content.
-- Their completion time is stamped by the server; old unknown timestamps are
-- never backfilled from writable metadata.
ALTER TABLE public.meta_webhook_inbox ADD COLUMN IF NOT EXISTS c24_completed_at timestamptz;
ALTER TABLE public.meta_webhook_inbox ADD COLUMN IF NOT EXISTS c24_payload_purged_at timestamptz;
CREATE TABLE IF NOT EXISTS public.c24_inbox_cleanup_capability (
 backend_pid integer NOT NULL, transaction_id bigint NOT NULL,
 organization_id uuid NOT NULL, inbox_id uuid NOT NULL,
 PRIMARY KEY(backend_pid,transaction_id,organization_id,inbox_id)
);
ALTER TABLE public.c24_inbox_cleanup_capability ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.c24_inbox_cleanup_capability
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;

CREATE OR REPLACE FUNCTION public.c24_inbox_lifecycle_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.c24_completed_at IS NOT NULL OR NEW.c24_payload_purged_at IS NOT NULL THEN
   RAISE EXCEPTION 'Inbox lifecycle markers are server controlled' USING ERRCODE='55000';
  END IF;
  IF NEW.status='completed' THEN NEW.c24_completed_at:=clock_timestamp(); END IF;
 ELSE
  IF OLD.status='completed' AND NEW.status IS DISTINCT FROM OLD.status THEN
   RAISE EXCEPTION 'Completed inbox cannot be reopened' USING ERRCODE='55000';
  END IF;
  IF OLD.status<>'completed' AND NEW.status='completed' THEN
   IF NEW.c24_completed_at IS DISTINCT FROM OLD.c24_completed_at OR OLD.c24_completed_at IS NOT NULL THEN
    RAISE EXCEPTION 'Inbox completion time is immutable' USING ERRCODE='55000';
   END IF;
   NEW.c24_completed_at:=clock_timestamp();
  ELSIF NEW.c24_completed_at IS DISTINCT FROM OLD.c24_completed_at THEN
   RAISE EXCEPTION 'Inbox completion time is immutable' USING ERRCODE='55000';
  END IF;
  IF OLD.c24_payload_purged_at IS NULL AND NEW.c24_payload_purged_at IS NOT NULL THEN
   IF NOT EXISTS(SELECT 1 FROM public.c24_inbox_cleanup_capability c
    WHERE c.backend_pid=pg_backend_pid() AND c.transaction_id=txid_current()
     AND c.organization_id=OLD.organization_id AND c.inbox_id=OLD.id) THEN
    RAISE EXCEPTION 'Inbox cleanup capability required' USING ERRCODE='42501';
   END IF;
   NEW.c24_payload_purged_at:=clock_timestamp();
  ELSIF NEW.c24_payload_purged_at IS DISTINCT FROM OLD.c24_payload_purged_at THEN
   RAISE EXCEPTION 'Inbox purge marker is immutable' USING ERRCODE='55000';
  END IF;
 END IF;
 IF NEW.c24_payload_purged_at IS NOT NULL AND
    (NEW.status<>'completed' OR NEW.payload IS DISTINCT FROM '{}'::jsonb OR NEW.last_error IS NOT NULL) THEN
  RAISE EXCEPTION 'Purged inbox content cannot be restored' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_inbox_lifecycle_guard() FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;
DROP TRIGGER IF EXISTS c24_inbox_lifecycle_guard ON public.meta_webhook_inbox;
CREATE TRIGGER c24_inbox_lifecycle_guard BEFORE INSERT OR UPDATE ON public.meta_webhook_inbox
 FOR EACH ROW EXECUTE FUNCTION public.c24_inbox_lifecycle_guard();

CREATE OR REPLACE FUNCTION public.c24_purge_inbox(p_org uuid,p_inbox uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE i public.meta_webhook_inbox%ROWTYPE; p public.gdpr_policies%ROWTYPE;
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
   WHERE oid='public.c24_inbox_cleanup_capability'::regclass)) THEN
  RAISE EXCEPTION 'Inbox cleanup requires lifecycle owner' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Inbox cleanup requires read committed isolation' USING ERRCODE='25000'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 SELECT * INTO i FROM public.meta_webhook_inbox WHERE organization_id=p_org AND id=p_inbox FOR UPDATE;
 IF NOT FOUND OR i.status<>'completed' OR i.c24_completed_at IS NULL OR i.c24_payload_purged_at IS NOT NULL
   OR (i.payload='{}'::jsonb AND i.last_error IS NULL) THEN RETURN false; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org AND category='messages_content'
  AND approved_at<=clock_timestamp() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed
   OR p.retention_days IS NULL OR p.retention_days<1 OR p.retention_days>90
   OR i.c24_completed_at>clock_timestamp()-make_interval(days=>p.retention_days) THEN RETURN false; END IF;
 -- An envelope can contain several subjects; any applicable organization hold
 -- or in-flight message leaves it untouched until it can be safely reviewed.
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
   AND h.category='messages_content'
   AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
    WHERE e.organization_id=h.organization_id AND e.hold_id=h.hold_id AND e.kind='released'))
  OR EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=p_org
    AND m.status IN ('processing','queued','sending_ambiguous','received_pending_ai'))
  OR EXISTS(SELECT 1 FROM public.usage_events WHERE organization_id=p_org
    AND ((event_type='system.provider_attempt' AND metadata->>'accounting_status'='unresolved')
      OR (event_type='system.quota_reservation' AND metadata->>'state'='reserved'))) THEN RETURN false; END IF;
 INSERT INTO public.c24_inbox_cleanup_capability VALUES(pg_backend_pid(),txid_current(),p_org,p_inbox);
 UPDATE public.meta_webhook_inbox SET payload='{}'::jsonb,last_error=NULL,c24_payload_purged_at=clock_timestamp()
  WHERE organization_id=p_org AND id=p_inbox;
 DELETE FROM public.c24_inbox_cleanup_capability WHERE backend_pid=pg_backend_pid()
  AND transaction_id=txid_current() AND organization_id=p_org AND inbox_id=p_inbox;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.c24_purge_inbox(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;

-- c24_received_at is written by SQL074 on INSERT for both directions and is
-- immutable to app callers. Outbound retention therefore uses server receipt
-- time, never created_at or any client timestamp.
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
 IF NOT FOUND OR m.conversation_id IS DISTINCT FROM conv OR m.deleted_at IS NOT NULL
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

-- A private, persistent keyset cursor prevents one held tenant or a restart
-- from starving all later tenants. The wrapper remains the only executable
-- entry point for the dedicated retention operator.
CREATE TABLE IF NOT EXISTS public.c24_retention_sweep_cursor (
 scan_name text PRIMARY KEY CHECK(scan_name IN ('retention','hotel_erasure')),
 phase smallint NOT NULL DEFAULT 0 CHECK(phase BETWEEN 0 AND 8),
 last_organization_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000'::uuid,
 last_record_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000'::uuid
);
ALTER TABLE public.c24_retention_sweep_cursor ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.c24_retention_sweep_cursor
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;
INSERT INTO public.c24_retention_sweep_cursor(scan_name) VALUES('retention'),('hotel_erasure')
 ON CONFLICT(scan_name) DO NOTHING;

CREATE INDEX IF NOT EXISTS c24_retention_receipt_due_idx
 ON public.booking_conversation_requests(organization_id,c24_concluded_at,request_id)
 WHERE c24_concluded_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS c24_retention_inbox_due_idx
 ON public.meta_webhook_inbox(organization_id,c24_completed_at,id)
 WHERE status='completed' AND c24_payload_purged_at IS NULL;
CREATE INDEX IF NOT EXISTS c24_retention_suppression_due_idx
 ON public.c24_source_fences(organization_id,fenced_at,conversation_id);
CREATE INDEX IF NOT EXISTS c24_retention_watermark_due_idx
 ON public.c24_idle_watermarks(organization_id,expired_at,conversation_id);
CREATE INDEX IF NOT EXISTS c24_retention_suppression_audit_due_idx
 ON public.c24_suppression_lifecycle_audit(organization_id,occurred_at,audit_id);

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
      AND p.retention_days BETWEEN 1 AND 90 AND m.deleted_at IS NULL
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

-- Hotel retention is a two-phase, bounded scan: first release occupancy after
-- verified checkout, then purge archived stays after twelve civil months.
-- The persistent cursor is independent from message retention and the purge
-- function remains final authority for holds, lineage and policy.
CREATE OR REPLACE FUNCTION public.c24_hotel_retention_sweep() RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE cursor_row public.c24_retention_sweep_cursor%ROWTYPE; target record;
        attempted integer; pass integer; applied boolean; safe boolean:=true;
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
   WHERE oid='public.c24_retention_sweep_cursor'::regclass))
   OR NOT public.c24_retention_operator_allowed() THEN
  RAISE EXCEPTION 'Dedicated retention operator required' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Hotel retention sweep requires read committed isolation' USING ERRCODE='25000'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('c24-hotel-retention-sweep',606));
 SELECT * INTO STRICT cursor_row FROM public.c24_retention_sweep_cursor
  WHERE scan_name='hotel_erasure' FOR UPDATE;
 FOR pass IN 1..2 LOOP
  attempted:=0;
  FOR target IN
   WITH policy AS MATERIALIZED (
    SELECT DISTINCT ON (organization_id) organization_id,version,retention_days,
      erasure_allowed,legal_hold,valid_until
    FROM public.gdpr_policies WHERE category='booking_stay_content'
      AND approved_at<=clock_timestamp()
    ORDER BY organization_id,approved_at DESC,version DESC
   ), candidates AS (
    SELECT 1::smallint AS phase,s.organization_id,s.booking_id AS record_id,p.version AS policy_version
    FROM public.booking_stays s
    JOIN public.bookings b ON b.organization_id=s.organization_id AND b.id=s.booking_id
    JOIN public.booking_allocation_sets a ON a.organization_id=s.organization_id AND a.booking_id=s.booking_id
    JOIN public.booking_stay_completion_evidence e ON e.organization_id=s.organization_id AND e.booking_id=s.booking_id
    JOIN policy p ON p.organization_id=s.organization_id
    WHERE b.stato='completata' AND b.completata_at IS NOT NULL AND b.no_show_at IS NULL
      AND a.state='active' AND a.temporal_kind='civil_nights' AND a.timezone=e.timezone
      AND (a.arrival_date,a.departure_date) IS NOT DISTINCT FROM (s.arrival_date,s.departure_date)
      AND a.ends_at<=e.completed_at
      AND a.ends_at<=clock_timestamp()
      AND p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_holds h
       WHERE h.organization_id=s.organization_id AND h.category='booking_stay_content'
        AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=s.original_subject_id)
          OR (h.scope='conversation' AND h.scope_id=s.original_conversation_id)
          OR (h.scope='record' AND h.scope_id=s.booking_id))
        AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events v
          WHERE v.organization_id=h.organization_id AND v.hold_id=h.hold_id AND v.kind='released'))
    UNION ALL
    SELECT 2::smallint,s.organization_id,s.booking_id,p.version
    FROM public.booking_stays s JOIN policy p ON p.organization_id=s.organization_id
    WHERE public.hotel_stay_purge_eligible(s.organization_id,s.booking_id)
      AND p.valid_until>clock_timestamp() AND NOT p.legal_hold AND p.erasure_allowed
      AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_holds h
       WHERE h.organization_id=s.organization_id AND h.category='booking_stay_content'
        AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=s.original_subject_id)
          OR (h.scope='conversation' AND h.scope_id=s.original_conversation_id)
          OR (h.scope='record' AND h.scope_id=s.booking_id))
        AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events v
          WHERE v.organization_id=h.organization_id AND v.hold_id=h.hold_id AND v.kind='released'))
   )
   SELECT phase,organization_id,record_id,policy_version FROM candidates
    WHERE (phase,organization_id,record_id)>
          (cursor_row.phase,cursor_row.last_organization_id,cursor_row.last_record_id)
    ORDER BY phase,organization_id,record_id LIMIT 100
  LOOP
   attempted:=attempted+1;
   BEGIN
    IF target.phase=1 THEN
     applied:=public.archive_completed_hotel_allocation(target.organization_id,target.record_id);
    ELSE
     PERFORM public.booking_stay_admin_purge(
       target.organization_id,target.record_id,'retention',NULL,target.policy_version);
     applied:=true;
    END IF;
    IF NOT COALESCE(applied,false) THEN safe:=false; END IF;
   EXCEPTION WHEN object_not_in_prerequisite_state THEN
    -- Unknown, held or otherwise non-terminal external state is deferred;
    -- a later sweep can retry after verified resolution.
    safe:=false;
   END;
   UPDATE public.c24_retention_sweep_cursor SET phase=target.phase,
    last_organization_id=target.organization_id,last_record_id=target.record_id
    WHERE scan_name='hotel_erasure';
  END LOOP;
  IF attempted>0 THEN RETURN safe; END IF;
  EXIT WHEN cursor_row.phase=0;
  UPDATE public.c24_retention_sweep_cursor SET phase=0,
    last_organization_id='00000000-0000-0000-0000-000000000000'::uuid,
    last_record_id='00000000-0000-0000-0000-000000000000'::uuid WHERE scan_name='hotel_erasure';
  SELECT * INTO STRICT cursor_row FROM public.c24_retention_sweep_cursor
   WHERE scan_name='hotel_erasure' FOR UPDATE;
 END LOOP;
 RETURN safe;
END $$;
REVOKE ALL ON FUNCTION public.c24_hotel_retention_sweep()
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;

CREATE OR REPLACE FUNCTION public.c24_run_retention_target(p_kind text,p_org uuid,p_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.c24_retention_operator_allowed() THEN
  RAISE EXCEPTION 'Dedicated retention operator required' USING ERRCODE='42501'; END IF;
 CASE p_kind
  WHEN 'inbox' THEN RETURN public.c24_purge_inbox(p_org,p_id);
  WHEN 'message' THEN RETURN public.c24_purge_source(p_org,p_id);
  WHEN 'conversation' THEN RETURN public.c24_expire_idle(p_org,p_id);
  WHEN 'receipt' THEN RETURN public.c24_cleanup_receipt(p_org,p_id);
  WHEN 'suppression' THEN RETURN public.c24_cleanup_suppression(p_org,p_id);
  WHEN 'suppression_audit' THEN RETURN public.c24_cleanup_suppression_audit(p_org,p_id);
  WHEN 'sweep' THEN
   IF p_org IS DISTINCT FROM '00000000-0000-0000-0000-000000000000'::uuid
     OR p_id IS DISTINCT FROM '00000000-0000-0000-0000-000000000000'::uuid THEN
    RAISE EXCEPTION 'Invalid sweep scope' USING ERRCODE='22023'; END IF;
   RETURN public.c24_retention_sweep();
  WHEN 'hotel_sweep' THEN
   IF p_org IS DISTINCT FROM '00000000-0000-0000-0000-000000000000'::uuid
     OR p_id IS DISTINCT FROM '00000000-0000-0000-0000-000000000000'::uuid THEN
    RAISE EXCEPTION 'Invalid hotel sweep scope' USING ERRCODE='22023'; END IF;
   RETURN public.c24_hotel_retention_sweep();
  ELSE RAISE EXCEPTION 'Invalid retention target kind' USING ERRCODE='22023';
 END CASE;
END $$;
REVOKE ALL ON FUNCTION public.c24_run_retention_target(text,uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.c24_run_retention_target(text,uuid,uuid) TO melpis_c24_retention;
