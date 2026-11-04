-- Dark capability only: no policy approvals, worker grants, or retention activation.
-- A released hold has a separate immutable release record; an overdue review
-- remains a hold until an authorized administrator records its disposition.
CREATE TABLE IF NOT EXISTS public.gdpr_retention_holds (
    organization_id uuid NOT NULL REFERENCES public.organizations(id),
    hold_id uuid NOT NULL,
    category text NOT NULL CHECK (category ~ '^[a-z][a-z0-9_]{0,79}$'),
    scope text NOT NULL CHECK (scope IN ('organization','subject','conversation','record')),
    scope_id uuid,
    reason_code text NOT NULL CHECK (reason_code ~ '^[a-z][a-z0-9_]{0,79}$'),
    authority_reference text NOT NULL CHECK (authority_reference ~ '^[A-Za-z0-9_./:-]{1,200}$'),
    approved_by text NOT NULL CHECK (approved_by ~ '^[A-Za-z0-9_.:-]{1,100}$'),
    approved_at timestamptz NOT NULL,
    review_by timestamptz NOT NULL,
    PRIMARY KEY (organization_id, hold_id),
    CHECK ((scope = 'organization') = (scope_id IS NULL)),
    CHECK (review_by > approved_at AND review_by <= approved_at + interval '12 months')
);
CREATE TABLE IF NOT EXISTS public.gdpr_retention_hold_events (
    organization_id uuid NOT NULL,
    hold_id uuid NOT NULL,
    event_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('reviewed','released')),
    authority_reference text NOT NULL CHECK (authority_reference ~ '^[A-Za-z0-9_./:-]{1,200}$'),
    approved_by text NOT NULL CHECK (approved_by ~ '^[A-Za-z0-9_.:-]{1,100}$'),
    occurred_at timestamptz NOT NULL,
    next_review_by timestamptz,
    PRIMARY KEY (organization_id, event_id),
    FOREIGN KEY (organization_id, hold_id) REFERENCES public.gdpr_retention_holds(organization_id, hold_id),
    CHECK ((kind = 'released' AND next_review_by IS NULL) OR
           (kind = 'reviewed' AND next_review_by > occurred_at AND next_review_by <= occurred_at + interval '12 months'))
);
CREATE INDEX IF NOT EXISTS gdpr_retention_holds_scope_idx
    ON public.gdpr_retention_holds (organization_id, category, scope, scope_id);
DO $$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['gdpr_retention_holds','gdpr_retention_hold_events'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,anon,authenticated,service_role',t);
  EXECUTE format('DROP TRIGGER IF EXISTS gdpr_hold_immutable ON public.%I',t);
  EXECUTE format('CREATE TRIGGER gdpr_hold_immutable BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.gdpr_immutable_record()',t);
  EXECUTE format('DROP TRIGGER IF EXISTS gdpr_hold_policy_fence ON public.%I',t);
  EXECUTE format('CREATE TRIGGER gdpr_hold_policy_fence BEFORE INSERT ON public.%I FOR EACH ROW EXECUTE FUNCTION public.gdpr_policy_fence()',t);
 END LOOP;
END $$;

-- Serialize reviews/releases with hold creation and policy changes. A release is
-- final; a later hold requires its own separately documented authorization.
CREATE UNIQUE INDEX IF NOT EXISTS gdpr_hold_single_release
 ON public.gdpr_retention_hold_events(organization_id,hold_id) WHERE kind='released';
CREATE OR REPLACE FUNCTION public.gdpr_validate_hold_event() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE started timestamptz; last_event timestamptz;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.organization_id::text,606));
 SELECT approved_at INTO started FROM public.gdpr_retention_holds
  WHERE organization_id=NEW.organization_id AND hold_id=NEW.hold_id FOR UPDATE;
 IF NOT FOUND OR NEW.occurred_at < started OR NEW.occurred_at > clock_timestamp() THEN
  RAISE EXCEPTION 'Hold event chronology invalid' USING ERRCODE='55000';
 END IF;
 SELECT max(occurred_at) INTO last_event FROM public.gdpr_retention_hold_events
  WHERE organization_id=NEW.organization_id AND hold_id=NEW.hold_id;
 IF (last_event IS NOT NULL AND NEW.occurred_at < last_event)
    OR EXISTS (SELECT 1 FROM public.gdpr_retention_hold_events
      WHERE organization_id=NEW.organization_id AND hold_id=NEW.hold_id AND kind='released') THEN
  RAISE EXCEPTION 'Hold event already superseded' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.gdpr_validate_hold_event() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS gdpr_hold_event_validate ON public.gdpr_retention_hold_events;
CREATE TRIGGER gdpr_hold_event_validate BEFORE INSERT ON public.gdpr_retention_hold_events
 FOR EACH ROW EXECUTE FUNCTION public.gdpr_validate_hold_event();

-- Idle expiry capability. These objects are deliberately not executable by
-- application or worker roles; a separately reviewed controller must invoke it.
ALTER TABLE public.messages ADD COLUMN IF NOT EXISTS c24_received_at timestamptz;
CREATE OR REPLACE FUNCTION public.c24_stamp_received_at() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='INSERT' THEN NEW.c24_received_at := clock_timestamp();
 ELSIF NEW.c24_received_at IS DISTINCT FROM OLD.c24_received_at THEN
  RAISE EXCEPTION 'Source receipt time is immutable' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_stamp_received_at() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS c24_source_received ON public.messages;
CREATE TRIGGER c24_source_received BEFORE INSERT OR UPDATE OF c24_received_at ON public.messages
 FOR EACH ROW EXECUTE FUNCTION public.c24_stamp_received_at();

CREATE TABLE IF NOT EXISTS public.c24_idle_watermarks (
 organization_id uuid NOT NULL, conversation_id uuid NOT NULL, subject_id uuid NOT NULL,
 generation bigint NOT NULL CHECK(generation>0), cutoff timestamptz NOT NULL,
 provider_timestamp bigint NOT NULL CHECK(provider_timestamp>0), expired_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(organization_id,conversation_id,generation),
 FOREIGN KEY(organization_id,conversation_id) REFERENCES public.conversations(organization_id,id) ON DELETE CASCADE,
 FOREIGN KEY(organization_id,subject_id) REFERENCES public.contacts(organization_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS public.c24_source_fences (
 organization_id uuid NOT NULL, conversation_id uuid NOT NULL, subject_id uuid NOT NULL,
 request_id uuid NOT NULL, source_message_id uuid NOT NULL, fenced_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(organization_id,request_id), UNIQUE(organization_id,conversation_id,source_message_id),
 FOREIGN KEY(organization_id,conversation_id) REFERENCES public.conversations(organization_id,id) ON DELETE CASCADE,
 FOREIGN KEY(organization_id,subject_id) REFERENCES public.contacts(organization_id,id) ON DELETE CASCADE
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['c24_idle_watermarks','c24_source_fences'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,anon,authenticated,service_role',t);
  EXECUTE format('DROP TRIGGER IF EXISTS c24_immutable ON public.%I',t);
  EXECUTE format('CREATE TRIGGER c24_immutable BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.booking_revocation_immutable()',t);
 END LOOP;
END $$;
-- Capture original subject on the receipt while the source state is locked.
-- Legacy receipts can only be backfilled while their SQL070 parent still exists.
ALTER TABLE public.booking_conversation_states ADD COLUMN IF NOT EXISTS state_generation uuid NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE public.booking_conversation_requests ADD COLUMN IF NOT EXISTS original_subject_id uuid;
ALTER TABLE public.booking_conversation_requests ADD COLUMN IF NOT EXISTS state_generation uuid;
DROP TRIGGER IF EXISTS conversation_immutable ON public.booking_conversation_requests;
UPDATE public.booking_conversation_requests r SET original_subject_id=s.subject_id,state_generation=s.state_generation
 FROM public.booking_conversation_states s
 WHERE r.organization_id=s.organization_id AND r.conversation_id=s.conversation_id
   AND (r.original_subject_id IS NULL OR r.state_generation IS NULL);
ALTER TABLE public.booking_conversation_requests ALTER COLUMN original_subject_id SET NOT NULL, ALTER COLUMN state_generation SET NOT NULL;
CREATE TRIGGER conversation_immutable BEFORE UPDATE OR DELETE ON public.booking_conversation_requests
 FOR EACH ROW EXECUTE FUNCTION public.booking_conversation_immutable();
CREATE OR REPLACE FUNCTION public.c24_stamp_receipt_subject() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE subject uuid; generation uuid;
BEGIN
 SELECT subject_id,state_generation INTO STRICT subject,generation FROM public.booking_conversation_states
 WHERE organization_id=NEW.organization_id AND conversation_id=NEW.conversation_id FOR SHARE;
 IF NEW.original_subject_id IS NOT NULL AND NEW.original_subject_id IS DISTINCT FROM subject THEN
  RAISE EXCEPTION 'Receipt subject does not match original state' USING ERRCODE='55000';
 END IF;
 NEW.original_subject_id := subject;
 NEW.state_generation := generation;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_stamp_receipt_subject() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS c24_stamp_receipt_subject ON public.booking_conversation_requests;
CREATE TRIGGER c24_stamp_receipt_subject BEFORE INSERT ON public.booking_conversation_requests
 FOR EACH ROW EXECUTE FUNCTION public.c24_stamp_receipt_subject();

-- State revisions restart after expiry. Request IDs and source IDs remain
-- unique across generations; neither relies on the lifetime of the snapshot.
DO $receipt_constraints$
DECLARE constraint_name name;
BEGIN
 FOR constraint_name IN SELECT conname FROM pg_constraint
  WHERE conrelid='public.booking_conversation_requests'::regclass AND (
   (contype='f' AND confrelid IN (
    'public.booking_conversation_states'::regclass,'public.messages'::regclass
   ))
   OR (contype='u' AND pg_get_constraintdef(oid)='UNIQUE (organization_id, conversation_id, applied_revision)')
  )
 LOOP
  EXECUTE format('ALTER TABLE public.booking_conversation_requests DROP CONSTRAINT %I',constraint_name);
 END LOOP;
END
$receipt_constraints$;

-- Source and prompt lineage are validated at insert. Losing a parent never
-- authorizes deleting the independently governed receipt.
CREATE OR REPLACE FUNCTION public.booking_conversation_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'Conversation receipt is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.booking_conversation_immutable() FROM PUBLIC,anon,authenticated,service_role;

-- Preserve already accepted sources when this additive migration is installed.
INSERT INTO public.c24_source_fences(organization_id,conversation_id,subject_id,request_id,source_message_id)
 SELECT r.organization_id,r.conversation_id,r.original_subject_id,r.request_id,r.source_message_id
 FROM public.booking_conversation_requests r
 ON CONFLICT DO NOTHING;
CREATE OR REPLACE FUNCTION public.c24_fence_request() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE subj uuid;
BEGIN
 SELECT subject_id INTO STRICT subj FROM public.booking_conversation_states
  WHERE organization_id=NEW.organization_id AND conversation_id=NEW.conversation_id;
 INSERT INTO public.c24_source_fences(organization_id,conversation_id,subject_id,request_id,source_message_id)
 VALUES(NEW.organization_id,NEW.conversation_id,subj,NEW.request_id,NEW.source_message_id);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_fence_request() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS c24_request_fence ON public.booking_conversation_requests;
CREATE TRIGGER c24_request_fence AFTER INSERT ON public.booking_conversation_requests
 FOR EACH ROW EXECUTE FUNCTION public.c24_fence_request();

CREATE OR REPLACE FUNCTION public.c24_state_activity_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE w public.c24_idle_watermarks%ROWTYPE; received timestamptz; provider text; prior_snapshot jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,606));
 PERFORM 1 FROM public.conversations WHERE organization_id=NEW.organization_id AND id=NEW.conversation_id FOR UPDATE;
 SELECT snapshot INTO prior_snapshot FROM public.booking_conversation_states
  WHERE organization_id=NEW.organization_id AND conversation_id=NEW.conversation_id;
 IF TG_OP='UPDATE' AND NEW.state_generation IS DISTINCT FROM OLD.state_generation THEN
  RAISE EXCEPTION 'State generation is immutable' USING ERRCODE='55000';
 END IF;
 IF TG_OP='UPDATE' AND NEW.source_message_id=OLD.source_message_id THEN
  NEW.updated_at := OLD.updated_at;
  IF NEW IS DISTINCT FROM OLD THEN
   RAISE EXCEPTION 'A fresh source is required' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='UPDATE' AND OLD.updated_at <= clock_timestamp()-interval '30 days' THEN
  RAISE EXCEPTION 'Expired state requires lifecycle cleanup' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=NEW.organization_id
  AND m.conversation_id=NEW.conversation_id AND m.id=NEW.source_message_id AND m.content_purged_at IS NOT NULL) THEN
  RAISE EXCEPTION 'Purged source cannot be applied' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM public.c24_source_fences f WHERE f.organization_id=NEW.organization_id
           AND f.conversation_id=NEW.conversation_id AND f.source_message_id=NEW.source_message_id) THEN
  RAISE EXCEPTION 'Source already applied' USING ERRCODE='55000';
 END IF;
 -- A purged source may only remain as provenance already materialized
 -- in this exact prior snapshot. It cannot be newly introduced by SQL.
 IF EXISTS (SELECT 1 FROM (
    SELECT value->>'source_message_id' AS source_id FROM jsonb_array_elements(NEW.snapshot->'slots')
    UNION ALL SELECT value->>'source_message_id' FROM jsonb_array_elements(COALESCE(NEW.snapshot->'superseded','[]'::jsonb))
    UNION ALL SELECT NEW.snapshot->'confirmation'->>'source_message_id' WHERE jsonb_typeof(NEW.snapshot->'confirmation')='object'
   ) refs JOIN public.messages m ON m.organization_id=NEW.organization_id
     AND m.conversation_id=NEW.conversation_id AND m.id::text=refs.source_id
   WHERE m.content_purged_at IS NOT NULL AND NOT (prior_snapshot IS NOT NULL AND EXISTS (
    SELECT 1 FROM (
     SELECT value->>'source_message_id' AS source_id FROM jsonb_array_elements(prior_snapshot->'slots')
     UNION ALL SELECT value->>'source_message_id' FROM jsonb_array_elements(COALESCE(prior_snapshot->'superseded','[]'::jsonb))
     UNION ALL SELECT prior_snapshot->'confirmation'->>'source_message_id' WHERE jsonb_typeof(prior_snapshot->'confirmation')='object'
    ) prior WHERE prior.source_id=refs.source_id))) OR
   (jsonb_typeof(NEW.snapshot->'confirmation')='object' AND EXISTS (
     SELECT 1 FROM public.messages m WHERE m.organization_id=NEW.organization_id
      AND m.conversation_id=NEW.conversation_id AND m.id::text=NEW.snapshot->'confirmation'->>'prompt_message_id'
      AND m.content_purged_at IS NOT NULL)) THEN
  RAISE EXCEPTION 'Purged provenance cannot be introduced or confirmed' USING ERRCODE='55000';
 END IF;
 SELECT * INTO w FROM public.c24_idle_watermarks WHERE organization_id=NEW.organization_id
  AND conversation_id=NEW.conversation_id ORDER BY generation DESC LIMIT 1;
 IF FOUND THEN
  IF EXISTS (SELECT 1 FROM (
    SELECT value->>'source_message_id' AS source_id FROM jsonb_array_elements(NEW.snapshot->'slots')
    UNION ALL SELECT value->>'source_message_id' FROM jsonb_array_elements(COALESCE(NEW.snapshot->'superseded','[]'::jsonb))
    UNION ALL SELECT NEW.snapshot->'confirmation'->>'source_message_id' WHERE jsonb_typeof(NEW.snapshot->'confirmation')='object'
   ) refs JOIN public.c24_source_fences f ON f.organization_id=NEW.organization_id
     AND f.conversation_id=NEW.conversation_id AND f.source_message_id::text=refs.source_id
   WHERE f.fenced_at<=w.expired_at) THEN
   RAISE EXCEPTION 'Expired source provenance cannot be restored' USING ERRCODE='55000';
  END IF;
  IF EXISTS (SELECT 1 FROM (
    SELECT value->>'source_message_id' AS source_id FROM jsonb_array_elements(NEW.snapshot->'slots')
    UNION ALL SELECT value->>'source_message_id' FROM jsonb_array_elements(COALESCE(NEW.snapshot->'superseded','[]'::jsonb))
    UNION ALL SELECT NEW.snapshot->'confirmation'->>'source_message_id' WHERE jsonb_typeof(NEW.snapshot->'confirmation')='object'
   ) refs LEFT JOIN public.messages m ON m.organization_id=NEW.organization_id
    AND m.conversation_id=NEW.conversation_id AND m.id::text=refs.source_id
   WHERE m.c24_received_at IS NULL OR m.c24_received_at<=w.cutoff OR (m.content_purged_at IS NULL AND NOT
    CASE WHEN m.content->>'timestamp' ~ '^[0-9]{1,12}$'
      THEN (m.content->>'timestamp')::bigint>extract(epoch FROM w.cutoff) ELSE false END))
   OR (jsonb_typeof(NEW.snapshot->'confirmation')='object' AND NOT EXISTS (
    SELECT 1 FROM public.messages m WHERE m.organization_id=NEW.organization_id
     AND m.conversation_id=NEW.conversation_id
     AND m.id::text=NEW.snapshot->'confirmation'->>'prompt_message_id'
     AND m.c24_received_at>w.cutoff)) THEN
   RAISE EXCEPTION 'Snapshot provenance precedes idle expiry' USING ERRCODE='55000';
  END IF;
  SELECT c24_received_at,content->>'timestamp' INTO received,provider FROM public.messages
   WHERE organization_id=NEW.organization_id AND conversation_id=NEW.conversation_id AND id=NEW.source_message_id;
  IF received IS NULL OR received<=w.cutoff OR provider IS NULL OR provider !~ '^[0-9]{1,12}$'
     OR provider::bigint<=w.provider_timestamp OR provider::bigint<>NEW.provider_timestamp
     OR provider::bigint<=extract(epoch FROM w.cutoff) THEN
   RAISE EXCEPTION 'Source precedes idle expiry' USING ERRCODE='55000';
  END IF;
 END IF;
 NEW.updated_at:=clock_timestamp(); RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_state_activity_guard() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS c24_state_activity ON public.booking_conversation_states;
CREATE TRIGGER c24_state_activity BEFORE INSERT OR UPDATE ON public.booking_conversation_states
 FOR EACH ROW EXECUTE FUNCTION public.c24_state_activity_guard();

CREATE OR REPLACE FUNCTION public.c24_expire_idle(p_org uuid,p_conversation uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s public.booking_conversation_states%ROWTYPE; p public.gdpr_policies%ROWTYPE; next_generation bigint;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(p_org::text,606));
 PERFORM 1 FROM public.conversations WHERE organization_id=p_org AND id=p_conversation FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org
  AND category='booking_conversation_content' AND approved_at<=clock_timestamp()
  ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed
    OR p.retention_days IS NULL OR p.retention_days<1 OR p.retention_days>30 THEN RETURN false; END IF;
 SELECT * INTO s FROM public.booking_conversation_states WHERE organization_id=p_org AND conversation_id=p_conversation FOR UPDATE;
 IF NOT FOUND OR s.updated_at>clock_timestamp()-make_interval(days=>p.retention_days) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
  AND h.category='booking_conversation_content' AND (h.scope='organization'
   OR (h.scope='subject' AND h.scope_id=s.subject_id) OR (h.scope='conversation' AND h.scope_id=p_conversation)
   OR (h.scope='record' AND h.scope_id=p_conversation))
  AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e WHERE e.organization_id=h.organization_id
   AND e.hold_id=h.hold_id AND e.kind='released')) THEN RETURN false; END IF;
 SELECT COALESCE(max(generation),0)+1 INTO next_generation FROM public.c24_idle_watermarks
  WHERE organization_id=p_org AND conversation_id=p_conversation;
 INSERT INTO public.c24_idle_watermarks(organization_id,conversation_id,subject_id,generation,cutoff,provider_timestamp)
 VALUES(p_org,p_conversation,s.subject_id,next_generation,s.updated_at+make_interval(days=>p.retention_days),s.provider_timestamp);
 DELETE FROM public.booking_conversation_states WHERE organization_id=p_org AND conversation_id=p_conversation;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.c24_expire_idle(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;

-- Content-retention tombstone. No scheduler or application role can set it.
ALTER TABLE public.messages ADD COLUMN IF NOT EXISTS content_purged_at timestamptz;
ALTER TABLE public.messages ADD COLUMN IF NOT EXISTS content_purge_cause text;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.messages'::regclass AND conname='c24_content_purge_pair') THEN
  ALTER TABLE public.messages ADD CONSTRAINT c24_content_purge_pair CHECK (
    (content_purged_at IS NULL AND content_purge_cause IS NULL) OR
    (content_purged_at IS NOT NULL AND content_purge_cause='retention'));
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.c24_content_purge_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE table_owner name;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.content_purged_at IS NOT NULL OR NEW.content_purge_cause IS NOT NULL THEN
   RAISE EXCEPTION 'Purge marker is server controlled' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
 END IF;
 IF OLD.content_purged_at IS NULL AND NEW.content_purged_at IS NOT NULL THEN
  SELECT pg_get_userbyid(relowner) INTO table_owner FROM pg_class WHERE oid='public.messages'::regclass;
  IF current_user<>table_owner OR NEW.content_purge_cause IS DISTINCT FROM 'retention' THEN
   RAISE EXCEPTION 'Purge marker requires the reviewed controller' USING ERRCODE='42501';
  END IF;
  NEW.content_purged_at:=clock_timestamp();
 ELSIF NEW.content_purged_at IS DISTINCT FROM OLD.content_purged_at
    OR NEW.content_purge_cause IS DISTINCT FROM OLD.content_purge_cause THEN
  RAISE EXCEPTION 'Purge marker is immutable' USING ERRCODE='55000';
 END IF;
 IF NEW.content_purged_at IS NOT NULL AND (NEW.content IS DISTINCT FROM '{}'::jsonb
    OR NEW.content_text IS NOT NULL OR NEW.ai_reply_cache IS NOT NULL
    OR NEW.error_details IS NOT NULL OR NEW.error_title IS NOT NULL) THEN
  RAISE EXCEPTION 'Purged source content cannot be restored' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_content_purge_guard() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS c24_content_purge_guard ON public.messages;
CREATE TRIGGER c24_content_purge_guard BEFORE INSERT OR UPDATE ON public.messages
 FOR EACH ROW EXECUTE FUNCTION public.c24_content_purge_guard();

-- Ordinary retention is distinct from semantic erasure and revocation.
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
      OR (OLD.ai_reply_cache ? 'booking_state_proposal' AND NOT COALESCE(NEW.ai_reply_cache ? 'booking_state_proposal',false) AND NEW.content_purged_at IS NULL) THEN
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
     AND m.content_purged_at IS NOT NULL AND m.deleted_at IS NULL) THEN
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

-- Local controller only. Caller must be a separately authorized owner; no
-- worker grant or scheduler is installed. Unmapped raw inbox/copies block.
CREATE OR REPLACE FUNCTION public.c24_purge_source(p_org uuid,p_message uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE m public.messages%ROWTYPE; p public.gdpr_policies%ROWTYPE; conv uuid; subj uuid;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 SELECT conversation_id INTO conv FROM public.messages WHERE organization_id=p_org AND id=p_message;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM 1 FROM public.conversations WHERE organization_id=p_org AND id=conv FOR UPDATE;
 SELECT * INTO m FROM public.messages WHERE organization_id=p_org AND id=p_message FOR UPDATE;
 IF NOT FOUND OR m.conversation_id IS DISTINCT FROM conv OR m.deleted_at IS NOT NULL
    OR m.content_purged_at IS NOT NULL OR m.direction<>'inbound' OR m.status<>'handled'
    OR m.created_at IS NULL OR m.c24_original_subject_id IS NULL THEN RETURN false; END IF;
 SELECT contact_id INTO subj FROM public.conversations WHERE organization_id=p_org AND id=conv;
 IF subj IS DISTINCT FROM m.c24_original_subject_id THEN RETURN false; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org AND category='messages_content'
  AND approved_at<=clock_timestamp() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed
    OR p.retention_days IS NULL OR p.retention_days<1 OR p.retention_days>90
    OR m.created_at>clock_timestamp()-make_interval(days=>p.retention_days) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
  AND h.category='messages_content' AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=subj)
   OR (h.scope='conversation' AND h.scope_id=conv) OR (h.scope='record' AND h.scope_id=p_message))
  AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e WHERE e.organization_id=h.organization_id
   AND e.hold_id=h.hold_id AND e.kind='released')) THEN RETURN false; END IF;
 -- Unknown-lineage inbox, active generation, delivery, or canonical copies
 -- make even an otherwise terminal source ineligible, never guessed away.
 IF EXISTS(SELECT 1 FROM public.meta_webhook_inbox WHERE organization_id=p_org
       AND (payload IS DISTINCT FROM '{}'::jsonb OR last_error IS NOT NULL))
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

CREATE OR REPLACE FUNCTION public.c24_purged_copy_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE msg uuid; org uuid; purged boolean;
BEGIN
 msg := NEW.message_id;
 SELECT organization_id, content_purged_at IS NOT NULL INTO org,purged
  FROM public.messages WHERE id=msg;
 IF purged THEN
  IF TG_TABLE_NAME='outbound_dedup' THEN
   IF NEW.organization_id IS DISTINCT FROM org OR NEW.response_text IS NOT NULL THEN
    RAISE EXCEPTION 'Purged outbound content cannot be restored' USING ERRCODE='55000';
   END IF;
  ELSE
   IF NEW.status NOT IN ('sent','delivered','read','completed') OR NEW.error_details IS NOT NULL THEN
    RAISE EXCEPTION 'Purged source cannot be retried' USING ERRCODE='55000';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_purged_copy_guard() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS c24_purged_copy_guard ON public.outbound_dedup;
CREATE TRIGGER c24_purged_copy_guard BEFORE INSERT OR UPDATE ON public.outbound_dedup
 FOR EACH ROW EXECUTE FUNCTION public.c24_purged_copy_guard();
DROP TRIGGER IF EXISTS c24_purged_copy_guard ON public.message_delivery_attempts;
CREATE TRIGGER c24_purged_copy_guard BEFORE INSERT OR UPDATE ON public.message_delivery_attempts
 FOR EACH ROW EXECUTE FUNCTION public.c24_purged_copy_guard();

-- Receipt cleanup is a dark, owner-only capability. Legacy created_at values
-- were writable by callers and are not evidence of request conclusion.
ALTER TABLE public.booking_conversation_requests ADD COLUMN IF NOT EXISTS c24_concluded_at timestamptz;
CREATE OR REPLACE FUNCTION public.c24_stamp_receipt_conclusion() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 NEW.c24_concluded_at := clock_timestamp();
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_stamp_receipt_conclusion() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS c24_stamp_receipt_conclusion ON public.booking_conversation_requests;
CREATE TRIGGER c24_stamp_receipt_conclusion BEFORE INSERT ON public.booking_conversation_requests
 FOR EACH ROW EXECUTE FUNCTION public.c24_stamp_receipt_conclusion();

-- This transactional capability is private to the database owner. It cannot be
-- forged with caller-controlled session settings and rolls back with the delete.
CREATE TABLE IF NOT EXISTS public.c24_receipt_cleanup_capability (
 backend_pid integer NOT NULL, transaction_id bigint NOT NULL,
 organization_id uuid NOT NULL, request_id uuid NOT NULL,
 PRIMARY KEY(backend_pid,transaction_id,organization_id,request_id)
);
ALTER TABLE public.c24_receipt_cleanup_capability ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.c24_receipt_cleanup_capability FROM PUBLIC,anon,authenticated,service_role;
CREATE TABLE IF NOT EXISTS public.c24_receipt_cleanup_audit (
 organization_id uuid NOT NULL, request_id uuid NOT NULL,
 cleaned_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(organization_id,request_id)
);
ALTER TABLE public.c24_receipt_cleanup_audit ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.c24_receipt_cleanup_audit FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS c24_receipt_cleanup_audit_immutable ON public.c24_receipt_cleanup_audit;
CREATE TRIGGER c24_receipt_cleanup_audit_immutable BEFORE UPDATE OR DELETE ON public.c24_receipt_cleanup_audit
 FOR EACH ROW EXECUTE FUNCTION public.gdpr_immutable_record();

CREATE OR REPLACE FUNCTION public.booking_conversation_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='DELETE' AND EXISTS (SELECT 1 FROM public.c24_receipt_cleanup_capability c
   WHERE c.backend_pid=pg_backend_pid() AND c.transaction_id=txid_current()
    AND c.organization_id=OLD.organization_id AND c.request_id=OLD.request_id) THEN
  RETURN OLD;
 END IF;
 RAISE EXCEPTION 'Conversation receipt is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.booking_conversation_immutable() FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.c24_cleanup_receipt(p_org uuid,p_request uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE r public.booking_conversation_requests%ROWTYPE; p public.gdpr_policies%ROWTYPE;
BEGIN
 -- The same exclusive tenant lock serializes policy and hold changes.
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
 SELECT * INTO r FROM public.booking_conversation_requests
  WHERE organization_id=p_org AND request_id=p_request FOR UPDATE;
 IF NOT FOUND OR r.c24_concluded_at IS NULL THEN RETURN false; END IF;
 SELECT * INTO p FROM public.gdpr_policies WHERE organization_id=p_org AND category='technical_receipts'
  AND approved_at<=clock_timestamp() ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF NOT FOUND OR p.valid_until<=clock_timestamp() OR p.legal_hold OR NOT p.erasure_allowed
    OR p.retention_days IS NULL OR p.retention_days<1 OR p.retention_days>90
    OR r.c24_concluded_at>clock_timestamp()-make_interval(days=>p.retention_days) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
  AND h.category='technical_receipts' AND (h.scope='organization'
   OR (h.scope='subject' AND h.scope_id=r.original_subject_id)
   OR (h.scope='conversation' AND h.scope_id=r.conversation_id)
   OR (h.scope='record' AND h.scope_id=r.request_id))
  AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
   WHERE e.organization_id=h.organization_id AND e.hold_id=h.hold_id AND e.kind='released')) THEN
  RETURN false;
 END IF;
 INSERT INTO public.c24_receipt_cleanup_capability VALUES(pg_backend_pid(),txid_current(),p_org,p_request);
 DELETE FROM public.booking_conversation_requests WHERE organization_id=p_org AND request_id=p_request;
 DELETE FROM public.c24_receipt_cleanup_capability WHERE backend_pid=pg_backend_pid()
  AND transaction_id=txid_current() AND organization_id=p_org AND request_id=p_request;
 INSERT INTO public.c24_receipt_cleanup_audit(organization_id,request_id) VALUES(p_org,p_request);
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.c24_cleanup_receipt(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
