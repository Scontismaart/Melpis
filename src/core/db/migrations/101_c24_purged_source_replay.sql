SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- A durable source tombstone must protect linked copies independently of
-- the separately governed source fence and idle watermark.
CREATE OR REPLACE FUNCTION public.c24_expired_proposal_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE proposal boolean; org uuid; conversation uuid; source uuid;
BEGIN
 IF TG_TABLE_NAME='messages' THEN
  IF TG_OP='UPDATE' AND OLD.content_purged_at IS NOT NULL AND OLD.direction='inbound'
   AND (NEW.id IS DISTINCT FROM OLD.id OR NEW.direction IS DISTINCT FROM OLD.direction
      OR NEW.wam_id IS DISTINCT FROM OLD.wam_id) THEN
   RAISE EXCEPTION 'Purged inbound delivery identity is immutable' USING ERRCODE='55000';
  END IF;
  proposal:=COALESCE(NEW.ai_reply_cache ? 'booking_state_proposal',false);
  org:=NEW.organization_id; conversation:=NEW.conversation_id; source:=NEW.id;
 ELSE
  proposal:=COALESCE(NEW.response ? 'booking_state_proposal',false);
  org:=NEW.organization_id; conversation:=NEW.c24_conversation_id; source:=NEW.c24_source_message_id;
 END IF;
 IF conversation IS NULL OR source IS NULL THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='simulation_requests' THEN
  IF NEW.response IS NULL OR NEW.response = '{}'::jsonb THEN RETURN NEW; END IF;
  -- Purge takes the exclusive tenant lock before checking canonical copies.
  -- A stale transaction snapshot cannot establish current source status.
  IF current_setting('transaction_isolation') <> 'read committed' THEN
   RAISE EXCEPTION 'Canonical response requires read committed isolation' USING ERRCODE='25000';
  END IF;
  PERFORM pg_advisory_xact_lock_shared(hashtextextended(org::text,606));
  IF EXISTS(SELECT 1 FROM public.messages m
   WHERE m.organization_id=org AND m.conversation_id=conversation AND m.id=source
    AND (m.content_purged_at IS NOT NULL OR m.deleted_at IS NOT NULL)) THEN
   RAISE EXCEPTION 'Purged canonical response cannot be restored' USING ERRCODE='55000';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.messages m
   WHERE m.organization_id=org AND m.conversation_id=conversation AND m.id=source
    AND m.c24_original_subject_id=NEW.c24_subject_id AND m.direction='inbound') THEN
   RAISE EXCEPTION 'Canonical response subject lineage unverified' USING ERRCODE='55000';
  END IF;
 END IF;
 IF proposal AND EXISTS(
  SELECT 1 FROM public.c24_source_fences f JOIN public.c24_idle_watermarks w
   ON w.organization_id=f.organization_id AND w.conversation_id=f.conversation_id
  WHERE f.organization_id=org AND f.conversation_id=conversation
    AND f.source_message_id=source AND f.fenced_at<=w.expired_at
 ) THEN RAISE EXCEPTION 'Expired proposal cannot be restored' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_expired_proposal_guard() FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;
DROP TRIGGER IF EXISTS c24_expired_proposal_guard ON public.simulation_requests;
CREATE TRIGGER c24_expired_proposal_guard
 BEFORE INSERT OR UPDATE OF organization_id,response,c24_conversation_id,c24_source_message_id,c24_subject_id
 ON public.simulation_requests FOR EACH ROW EXECUTE FUNCTION public.c24_expired_proposal_guard();

CREATE OR REPLACE FUNCTION public.c24_fence_request() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE subj uuid;
BEGIN
 -- conversation_scope_guard already holds the shared tenant lock. Refuse
 -- stale snapshots and verify live source evidence before deriving a fence.
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'Conversation receipt requires read committed isolation' USING ERRCODE='25000';
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
