SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Share the existing booking/request replay fences with an explicit kind.
-- Historical hotel records retain their original meaning. New ordinary
-- evidence requires the independently authenticated v4 checkpoint path.
ALTER TABLE public.booking_stay_purge_fences ADD COLUMN IF NOT EXISTS purge_kind text NOT NULL DEFAULT 'stay';
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint WHERE conrelid='public.booking_stay_purge_fences'::regclass
   AND conname='booking_purge_kind_scope') THEN
  ALTER TABLE public.booking_stay_purge_fences ADD CONSTRAINT booking_purge_kind_scope CHECK (
   purge_kind IN ('stay','ordinary') AND (purge_kind<>'ordinary' OR (purpose='retention' AND operation_id IS NULL)));
 END IF;
END $$;

-- The owner binds the exact independently authenticated v4 plan and inventory
-- in the same transaction that consumes it. No application principal has
-- authority to create, alter, inspect or remove these recovery commitments.
CREATE TABLE IF NOT EXISTS public.ordinary_booking_purge_bindings (
 organization_id uuid NOT NULL,
 booking_id uuid NOT NULL,
 plan jsonb NOT NULL CHECK(jsonb_typeof(plan)='object'),
 checkpoint_sha256 text NOT NULL CHECK(checkpoint_sha256 ~ '^[0-9a-f]{64}$'),
 checkpoint_sequence bigint NOT NULL CHECK(checkpoint_sequence>=1),
 inventory_sha256 text NOT NULL CHECK(inventory_sha256 ~ '^[0-9a-f]{64}$'),
 bound_transaction_id bigint NOT NULL,
 bound_backend_pid integer NOT NULL,
 bound_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 session_principal name NOT NULL DEFAULT session_user,
 PRIMARY KEY(organization_id,booking_id),
 FOREIGN KEY(organization_id,booking_id) REFERENCES public.booking_stay_purge_fences(organization_id,booking_id)
  DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE public.ordinary_booking_purge_bindings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ordinary_booking_purge_bindings
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;
DROP TRIGGER IF EXISTS ordinary_purge_binding_immutable ON public.ordinary_booking_purge_bindings;
CREATE TRIGGER ordinary_purge_binding_immutable BEFORE UPDATE OR DELETE ON public.ordinary_booking_purge_bindings
 FOR EACH ROW EXECUTE FUNCTION public.gdpr_immutable_record();
DROP TRIGGER IF EXISTS ordinary_purge_binding_no_truncate ON public.ordinary_booking_purge_bindings;
CREATE TRIGGER ordinary_purge_binding_no_truncate BEFORE TRUNCATE ON public.ordinary_booking_purge_bindings
 FOR EACH STATEMENT EXECUTE FUNCTION public.gdpr_immutable_record();

-- The shared replay rows for ordinary bookings are immutable even through
-- the lifecycle owner. Their only insert path is the freshly bound plan.
-- Stay rows preserve the preexisting hotel lifecycle and grant behavior.
CREATE OR REPLACE FUNCTION public.ordinary_purge_fence_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='TRUNCATE' THEN
  IF EXISTS(SELECT 1 FROM public.ordinary_booking_purge_bindings) THEN
   RAISE EXCEPTION 'Ordinary booking replay evidence is immutable' USING ERRCODE='42501'; END IF;
  RETURN NULL;
 END IF;
 IF TG_OP IN ('UPDATE','DELETE') AND OLD.purge_kind='ordinary' THEN
  RAISE EXCEPTION 'Ordinary booking replay evidence is immutable' USING ERRCODE='42501'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF NEW.purge_kind='ordinary' THEN
  IF TG_OP<>'INSERT' OR NOT EXISTS(SELECT 1 FROM public.ordinary_booking_purge_bindings b
       WHERE b.organization_id=NEW.organization_id AND b.booking_id=NEW.booking_id
        AND b.bound_transaction_id=txid_current() AND b.bound_backend_pid=pg_backend_pid()
        AND b.plan->>'subject_id'=NEW.original_subject_id::text AND b.plan->>'policy_version'=NEW.policy_version
        AND jsonb_array_length(b.plan->'request_ids')=NEW.receipt_count
        AND NEW.purpose='retention' AND NEW.operation_id IS NULL) THEN
   RAISE EXCEPTION 'Ordinary booking replay evidence requires the current bound plan' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.ordinary_purge_fence_guard()
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;
DROP TRIGGER IF EXISTS ordinary_purge_fence_guard ON public.booking_stay_purge_fences;
CREATE TRIGGER ordinary_purge_fence_guard BEFORE INSERT OR UPDATE OR DELETE ON public.booking_stay_purge_fences
 FOR EACH ROW EXECUTE FUNCTION public.ordinary_purge_fence_guard();
DROP TRIGGER IF EXISTS ordinary_purge_fence_no_truncate ON public.booking_stay_purge_fences;
CREATE TRIGGER ordinary_purge_fence_no_truncate BEFORE TRUNCATE ON public.booking_stay_purge_fences
 FOR EACH STATEMENT EXECUTE FUNCTION public.ordinary_purge_fence_guard();

CREATE OR REPLACE FUNCTION public.ordinary_purge_tombstone_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='TRUNCATE' THEN
  IF EXISTS(SELECT 1 FROM public.ordinary_booking_purge_bindings) THEN
   RAISE EXCEPTION 'Ordinary request replay evidence is immutable' USING ERRCODE='42501'; END IF;
  RETURN NULL;
 END IF;
 IF TG_OP IN ('UPDATE','DELETE') AND EXISTS(SELECT 1 FROM public.ordinary_booking_purge_bindings b
       WHERE b.organization_id=OLD.organization_id AND b.booking_id=OLD.booking_id) THEN
  RAISE EXCEPTION 'Ordinary request replay evidence is immutable' USING ERRCODE='42501'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF EXISTS(SELECT 1 FROM public.ordinary_booking_purge_bindings b
       WHERE b.organization_id=NEW.organization_id AND b.booking_id=NEW.booking_id) THEN
  IF TG_OP<>'INSERT' OR NOT EXISTS(SELECT 1 FROM public.ordinary_booking_purge_bindings b
       JOIN public.booking_stay_purge_fences f ON f.organization_id=b.organization_id AND f.booking_id=b.booking_id
       WHERE b.organization_id=NEW.organization_id AND b.booking_id=NEW.booking_id AND f.purge_kind='ordinary'
        AND b.bound_transaction_id=txid_current() AND b.bound_backend_pid=pg_backend_pid()
        AND b.plan->>'subject_id'=NEW.original_subject_id::text AND b.plan->>'policy_version'=NEW.policy_version
        AND b.plan->'request_ids' ? NEW.request_id::text AND NEW.purpose='retention' AND NEW.operation_id IS NULL) THEN
   RAISE EXCEPTION 'Ordinary request replay evidence requires the current bound plan' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.ordinary_purge_tombstone_guard()
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;
DROP TRIGGER IF EXISTS ordinary_purge_tombstone_guard ON public.booking_stay_tombstones;
CREATE TRIGGER ordinary_purge_tombstone_guard BEFORE INSERT OR UPDATE OR DELETE ON public.booking_stay_tombstones
 FOR EACH ROW EXECUTE FUNCTION public.ordinary_purge_tombstone_guard();
DROP TRIGGER IF EXISTS ordinary_purge_tombstone_no_truncate ON public.booking_stay_tombstones;
CREATE TRIGGER ordinary_purge_tombstone_no_truncate BEFORE TRUNCATE ON public.booking_stay_tombstones
 FOR EACH STATEMENT EXECUTE FUNCTION public.ordinary_purge_tombstone_guard();

-- The SQL108 planner body is shared with the transactional lifecycle. The
-- public planning entry point retains its read-only transaction requirement.
CREATE OR REPLACE FUNCTION public.ordinary_booking_retention_plan_locked(p_org uuid,p_booking uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE b public.bookings%ROWTYPE; e public.ordinary_booking_completion_evidence%ROWTYPE;
 a public.booking_allocation_sets%ROWTYPE; policy_version text; requests uuid[]; claim_count integer;
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
   WHERE oid='public.ordinary_booking_completion_evidence'::regclass)) THEN
  RAISE EXCEPTION 'Ordinary booking review requires the lifecycle owner' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Ordinary booking review requires a read committed transaction' USING ERRCODE='25000'; END IF;
 IF p_org IS NULL OR p_booking IS NULL OR NOT pg_try_advisory_xact_lock(hashtextextended(p_org::text,606))
    OR NOT public.ordinary_completed_booking_retention_eligible(p_org,p_booking) THEN RETURN NULL; END IF;
 SELECT * INTO b FROM public.bookings WHERE organization_id=p_org AND id=p_booking;
 SELECT * INTO e FROM public.ordinary_booking_completion_evidence WHERE organization_id=p_org AND booking_id=p_booking;
 SELECT * INTO a FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking;
 SELECT version INTO policy_version FROM public.gdpr_policies WHERE organization_id=p_org
  AND category='completed_bookings_and_stays' AND approved_at<=clock_timestamp()
  ORDER BY approved_at DESC,version DESC LIMIT 1;
 IF b.id IS NULL OR e.booking_id IS NULL OR a.booking_id IS NULL OR policy_version IS NULL
    OR b.origine<>'Dashboard' OR b.source_message_id IS NOT NULL OR b.id_conversazione IS NOT NULL
    OR e.conversation_id IS NOT NULL OR b.richiede_intervento
    OR b.richiede_deposito OR b.payment_status<>'none' OR b.payment_link<>''
    OR b.payment_link_created_at IS NOT NULL OR b.deposit_amount_minor IS NOT NULL
    OR b.deposit_currency IS NOT NULL OR b.deposit_session_id IS NOT NULL
    OR b.google_event_id IS NOT NULL OR b.calendar_confirm_pending
    OR b.reminder_status<>'none' OR b.reminder_sent_at IS NOT NULL OR b.reminder_responded_at IS NOT NULL
    OR a.temporal_kind<>'minutes' OR a.state<>'active' OR a.ends_at>e.completed_at
    OR a.timezone<>e.timezone OR a.booking_calendar_revision<>b.calendar_revision THEN RETURN NULL; END IF;

 IF EXISTS(SELECT 1 FROM public.external_booking_sync x WHERE x.organization_id=p_org AND x.internal_booking_id=p_booking)
    OR EXISTS(SELECT 1 FROM public.booking_confirmation_outbox o WHERE o.organization_id=p_org AND o.booking_id=p_booking)
    OR EXISTS(SELECT 1 FROM c24_private.worker_capabilities w WHERE w.organization_id=p_org AND w.booking_id=p_booking)
    OR EXISTS(SELECT 1 FROM c24_private.worker_context w WHERE w.organization_id=p_org AND w.booking_id=p_booking)
    OR EXISTS(SELECT 1 FROM public.booking_stay_purge_fences f WHERE f.organization_id=p_org AND f.booking_id=p_booking)
    OR EXISTS(SELECT 1 FROM public.messages m WHERE m.organization_id=p_org
       AND (strpos(m.idempotency_key,p_booking::text)>0 OR m.status IN ('processing','queued','sending_ambiguous','received_pending_ai')))
    OR EXISTS(SELECT 1 FROM public.meta_webhook_inbox i WHERE i.organization_id=p_org AND i.status NOT IN ('completed','failed'))
    OR EXISTS(SELECT 1 FROM public.usage_events u WHERE u.organization_id=p_org
      AND (strpos(u.metadata::text,p_booking::text)>0
       OR (u.event_type='system.provider_attempt' AND u.metadata->>'accounting_status'='unresolved')
       OR (u.event_type='system.quota_reservation' AND u.metadata->>'state'='reserved')))
    OR EXISTS(SELECT 1 FROM public.event_log log WHERE log.organization_id=p_org
       AND (log.source_id=p_booking OR strpos(log.dettagli::text,p_booking::text)>0))
    OR EXISTS(SELECT 1 FROM public.audit_log log WHERE log.organization_id=p_org
       AND (log.target_id=p_booking OR strpos(log.details::text,p_booking::text)>0))
    OR EXISTS(SELECT 1 FROM public.governance_outbox o WHERE o.organization_id=p_org AND strpos(o.payload::text,p_booking::text)>0)
    OR EXISTS(SELECT 1 FROM public.billing_checkout_intents i WHERE i.organization_id=p_org
       AND (strpos(i.payload::text,p_booking::text)>0 OR strpos(i.request_key,p_booking::text)>0)) THEN RETURN NULL; END IF;

 SELECT count(*) INTO claim_count FROM public.booking_resource_allocations r WHERE r.organization_id=p_org AND r.booking_id=p_booking;
 IF claim_count NOT BETWEEN 1 AND 128 OR EXISTS(SELECT 1 FROM public.booking_resource_allocations r
   WHERE r.organization_id=p_org AND r.booking_id=p_booking
    AND (r.state,r.allocation_revision,r.catalog_revision,r.starts_at,r.ends_at)
     IS DISTINCT FROM (a.state,a.allocation_revision,a.catalog_revision,a.starts_at,a.ends_at)) THEN RETURN NULL; END IF;
 SELECT array_agg(q.request_id ORDER BY q.request_id) INTO requests FROM (
  SELECT r.request_id FROM public.booking_allocation_requests r
   WHERE r.organization_id=p_org AND r.booking_id=p_booking ORDER BY r.request_id LIMIT 1001) q;
 IF requests IS NULL OR cardinality(requests) NOT BETWEEN 1 AND 1000
    OR EXISTS(SELECT 1 FROM public.booking_allocation_requests r
       WHERE r.organization_id=p_org AND r.booking_id=p_booking
        AND (r.actor_kind<>'human' OR r.outcome='unknown' OR r.service_inbox_id IS NOT NULL
             OR r.service_message_id IS NOT NULL OR r.service_operation_id IS NOT NULL))
    OR (SELECT count(*) FROM public.booking_allocation_requests r
       WHERE r.organization_id=p_org AND r.booking_id=p_booking AND r.action='replace' AND r.outcome='replaced'
        AND r.mutation_hash='4efaf69010ad344b83ca9cb9d9f206f54629aa5e356c79c015e959ddb5599eb6'
        AND r.created_at=e.completed_at AND r.applied_allocation_revision=a.allocation_revision
        AND r.catalog_revision=a.catalog_revision)<>1
    OR EXISTS(SELECT 1 FROM public.booking_stay_tombstones t WHERE t.organization_id=p_org AND t.request_id=ANY(requests))
    OR EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
       AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=e.subject_id)
            OR (h.scope='record' AND (h.scope_id=p_booking OR h.scope_id=ANY(requests))))
       AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events v WHERE v.organization_id=p_org
          AND v.hold_id=h.hold_id AND v.kind='released')) THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('kind','ordinary_managed_booking_candidate_v1','organization_id',p_org,
  'booking_id',p_booking,'subject_id',e.subject_id,'policy_version',policy_version,
  'completed_at',to_char(e.completed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'timezone',e.timezone,'allocation_revision',a.allocation_revision,'request_ids',requests,
  'deletion_authorized',false);
END $$;
REVOKE ALL ON FUNCTION public.ordinary_booking_retention_plan_locked(uuid,uuid)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;
CREATE OR REPLACE FUNCTION public.ordinary_completed_booking_retention_plan(p_org uuid,p_booking uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF current_setting('transaction_read_only')<>'on' OR current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Ordinary booking review requires a read-only read committed transaction' USING ERRCODE='25000'; END IF;
 RETURN public.ordinary_booking_retention_plan_locked(p_org,p_booking);
END $$;
REVOKE ALL ON FUNCTION public.ordinary_completed_booking_retention_plan(uuid,uuid)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;

-- Exact durable binding and postconditions, used by v4 verification and by
-- the deferred commit constraint. Audit and accounting rows are never deleted.
CREATE OR REPLACE FUNCTION public.ordinary_verify_booking_retention_purge(p_org uuid,p_booking uuid,p_plan jsonb)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT EXISTS(SELECT 1 FROM public.ordinary_booking_purge_bindings b
   JOIN public.booking_stay_purge_fences f ON f.organization_id=b.organization_id AND f.booking_id=b.booking_id
   WHERE b.organization_id=p_org AND b.booking_id=p_booking AND b.plan=p_plan
     AND f.purge_kind='ordinary' AND f.purpose='retention' AND f.operation_id IS NULL
     AND f.original_subject_id::text=b.plan->>'subject_id' AND f.policy_version=b.plan->>'policy_version'
     AND f.receipt_count=jsonb_array_length(b.plan->'request_ids')
     AND (SELECT COALESCE(jsonb_agg(t.request_id ORDER BY t.request_id),'[]'::jsonb)
       FROM public.booking_stay_tombstones t WHERE t.organization_id=p_org AND t.booking_id=p_booking)=b.plan->'request_ids'
     AND NOT EXISTS(SELECT 1 FROM public.booking_stay_tombstones t WHERE t.organization_id=p_org AND t.booking_id=p_booking
       AND (t.original_subject_id,t.purpose,t.operation_id,t.policy_version)
          IS DISTINCT FROM (f.original_subject_id,f.purpose,f.operation_id,f.policy_version)))
  AND NOT EXISTS(SELECT 1 FROM public.bookings WHERE organization_id=p_org AND id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM public.ordinary_booking_completion_evidence WHERE organization_id=p_org AND booking_id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM public.booking_stays WHERE organization_id=p_org AND booking_id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM public.booking_stay_completion_evidence WHERE organization_id=p_org AND booking_id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM public.booking_allocation_requests WHERE organization_id=p_org AND booking_id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM public.booking_confirmation_outbox WHERE organization_id=p_org AND booking_id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM public.external_booking_sync WHERE organization_id=p_org AND internal_booking_id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM c24_private.worker_capabilities WHERE organization_id=p_org AND booking_id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM c24_private.worker_context WHERE organization_id=p_org AND booking_id=p_booking)
  AND NOT EXISTS(SELECT 1 FROM public.messages WHERE organization_id=p_org AND strpos(idempotency_key,p_booking::text)>0);
$$;
REVOKE ALL ON FUNCTION public.ordinary_verify_booking_retention_purge(uuid,uuid,jsonb)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION public.ordinary_require_committed_purge() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.ordinary_verify_booking_retention_purge(NEW.organization_id,NEW.booking_id,NEW.plan) THEN
  RAISE EXCEPTION 'Ordinary checkpoint binding was not atomically consumed' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.ordinary_require_committed_purge()
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;
DROP TRIGGER IF EXISTS ordinary_checkpoint_binding_consumed ON public.ordinary_booking_purge_bindings;
CREATE CONSTRAINT TRIGGER ordinary_checkpoint_binding_consumed AFTER INSERT ON public.ordinary_booking_purge_bindings
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.ordinary_require_committed_purge();

-- Keep completion evidence immutable outside the exact owner-bound purge.
-- The transaction identity is captured by the private binding; the fence must
-- already exist before the evidence can be released.
CREATE OR REPLACE FUNCTION public.ordinary_booking_completion_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='DELETE' AND current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID))
   AND EXISTS(SELECT 1 FROM public.ordinary_booking_purge_bindings b JOIN public.booking_stay_purge_fences f
       ON f.organization_id=b.organization_id AND f.booking_id=b.booking_id
     WHERE b.organization_id=OLD.organization_id AND b.booking_id=OLD.booking_id
       AND b.bound_transaction_id=txid_current() AND b.bound_backend_pid=pg_backend_pid()
       AND b.plan->>'subject_id'=OLD.subject_id::text AND f.purge_kind='ordinary'
       AND f.original_subject_id=OLD.subject_id AND f.policy_version=b.plan->>'policy_version') THEN RETURN OLD; END IF;
 IF TG_OP='TRUNCATE' AND current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID))
     AND NOT EXISTS(SELECT 1 FROM public.ordinary_booking_completion_evidence) THEN RETURN NULL; END IF;
 RAISE EXCEPTION 'Ordinary booking completion evidence is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.ordinary_booking_completion_immutable()
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION public.ordinary_bind_booking_retention_checkpoint(
 p_org uuid,p_booking uuid,p_plan jsonb,p_checkpoint_sha256 text,p_sequence bigint,p_inventory_sha256 text
) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE bound public.ordinary_booking_purge_bindings%ROWTYPE;
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
   WHERE oid='public.ordinary_booking_purge_bindings'::regclass)) THEN
  RAISE EXCEPTION 'Ordinary checkpoint requires the lifecycle owner' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' OR current_setting('transaction_read_only')<>'off' THEN
  RAISE EXCEPTION 'Ordinary checkpoint requires a write read committed transaction' USING ERRCODE='25000'; END IF;
 IF p_org IS NULL OR p_booking IS NULL OR p_plan IS NULL OR p_sequence IS NULL OR p_sequence<1
    OR p_checkpoint_sha256 IS NULL OR p_checkpoint_sha256 !~ '^[0-9a-f]{64}$'
    OR p_inventory_sha256 IS NULL OR p_inventory_sha256 !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
 PERFORM 1 FROM public.organizations WHERE id=p_org FOR NO KEY UPDATE NOWAIT;
 IF NOT FOUND OR NOT pg_try_advisory_xact_lock(hashtextextended(p_org::text,606)) THEN RETURN false; END IF;
 SELECT * INTO bound FROM public.ordinary_booking_purge_bindings WHERE organization_id=p_org AND booking_id=p_booking;
 IF FOUND THEN
  IF bound.plan IS DISTINCT FROM p_plan THEN RETURN false; END IF;
  IF public.ordinary_verify_booking_retention_purge(p_org,p_booking,p_plan) THEN RETURN true; END IF;
  RETURN bound.bound_transaction_id=txid_current() AND bound.bound_backend_pid=pg_backend_pid()
     AND (bound.checkpoint_sha256,bound.checkpoint_sequence,bound.inventory_sha256)
       IS NOT DISTINCT FROM (p_checkpoint_sha256,p_sequence,p_inventory_sha256)
     AND public.ordinary_booking_retention_plan_locked(p_org,p_booking) IS NOT DISTINCT FROM p_plan;
 END IF;
 IF public.ordinary_booking_retention_plan_locked(p_org,p_booking) IS DISTINCT FROM p_plan THEN RETURN false; END IF;
 INSERT INTO public.ordinary_booking_purge_bindings(organization_id,booking_id,plan,checkpoint_sha256,
   checkpoint_sequence,inventory_sha256,bound_transaction_id,bound_backend_pid)
 VALUES(p_org,p_booking,p_plan,p_checkpoint_sha256,p_sequence,p_inventory_sha256,txid_current(),pg_backend_pid());
 RETURN true;
EXCEPTION WHEN lock_not_available THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION public.ordinary_bind_booking_retention_checkpoint(uuid,uuid,jsonb,text,bigint,text)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;

-- This owner-only prototype consumes a freshly authenticated exact binding.
-- Its caller must freeze source/backup administration and validate every
-- independently retained v4 checkpoint. SQL alone cannot attest that store.
CREATE OR REPLACE FUNCTION public.ordinary_admin_purge_booking(
 p_org uuid,p_booking uuid,p_plan jsonb,p_checkpoint_sha256 text,p_sequence bigint,p_inventory_sha256 text
) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE bound public.ordinary_booking_purge_bindings%ROWTYPE; claim record; requests uuid[]; subj uuid;
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
   WHERE oid='public.ordinary_booking_purge_bindings'::regclass)) THEN
  RAISE EXCEPTION 'Ordinary booking purge requires the lifecycle owner' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' OR current_setting('transaction_read_only')<>'off' THEN
  RAISE EXCEPTION 'Ordinary booking purge requires a write read committed transaction' USING ERRCODE='25000'; END IF;
 IF p_org IS NULL OR p_booking IS NULL OR p_plan IS NULL OR p_sequence IS NULL OR p_sequence<1
    OR p_checkpoint_sha256 IS NULL OR p_checkpoint_sha256 !~ '^[0-9a-f]{64}$'
    OR p_inventory_sha256 IS NULL OR p_inventory_sha256 !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
 PERFORM 1 FROM public.organizations WHERE id=p_org FOR NO KEY UPDATE NOWAIT;
 IF NOT FOUND OR NOT pg_try_advisory_xact_lock(hashtextextended(p_org::text,606)) THEN RETURN false; END IF;
 SELECT * INTO bound FROM public.ordinary_booking_purge_bindings WHERE organization_id=p_org AND booking_id=p_booking;
 IF NOT FOUND OR bound.plan IS DISTINCT FROM p_plan THEN RETURN false; END IF;
 IF public.ordinary_verify_booking_retention_purge(p_org,p_booking,p_plan) THEN RETURN true; END IF;
 IF bound.bound_transaction_id<>txid_current() OR bound.bound_backend_pid<>pg_backend_pid()
    OR (bound.checkpoint_sha256,bound.checkpoint_sequence,bound.inventory_sha256)
       IS DISTINCT FROM (p_checkpoint_sha256,p_sequence,p_inventory_sha256) THEN RETURN false; END IF;
 PERFORM 1 FROM public.bookings WHERE organization_id=p_org AND id=p_booking FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM 1 FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking FOR UPDATE NOWAIT;
 IF NOT FOUND THEN RETURN false; END IF;
 -- The bounded check precedes claim iteration, and the exact plan is rechecked
 -- after all relevant locks. Claim/resource lock order matches the allocator.
 IF public.ordinary_booking_retention_plan_locked(p_org,p_booking) IS DISTINCT FROM p_plan THEN RETURN false; END IF;
 FOR claim IN SELECT resource_id FROM public.booking_resource_allocations
   WHERE organization_id=p_org AND booking_id=p_booking ORDER BY resource_id FOR UPDATE NOWAIT LOOP
  IF NOT pg_try_advisory_xact_lock(hashtextextended('allocation-resource|'||p_org::text||'|'||claim.resource_id,725)) THEN
   RETURN false; END IF;
 END LOOP;
 IF public.ordinary_booking_retention_plan_locked(p_org,p_booking) IS DISTINCT FROM p_plan THEN RETURN false; END IF;
 SELECT array_agg(r.request_id ORDER BY r.request_id) INTO requests FROM public.booking_allocation_requests r
    WHERE r.organization_id=p_org AND r.booking_id=p_booking;
 subj:=(p_plan->>'subject_id')::uuid;
 INSERT INTO public.booking_stay_purge_fences(organization_id,booking_id,original_subject_id,purpose,operation_id,
   policy_version,receipt_count,purge_kind)
 VALUES(p_org,p_booking,subj,'retention',NULL,p_plan->>'policy_version',cardinality(requests),'ordinary');
 INSERT INTO public.booking_stay_tombstones(organization_id,request_id,booking_id,original_subject_id,purpose,operation_id,policy_version)
 SELECT p_org,request_id,p_booking,subj,'retention',NULL,p_plan->>'policy_version' FROM unnest(requests) AS request_id;
 DELETE FROM public.booking_resource_allocations WHERE organization_id=p_org AND booking_id=p_booking;
 DELETE FROM public.booking_allocation_sets WHERE organization_id=p_org AND booking_id=p_booking;
 DELETE FROM public.ordinary_booking_completion_evidence WHERE organization_id=p_org AND booking_id=p_booking;
 -- Receipt cascade remains under the shared immutable trigger: it allows
 -- deletion only when the parent booking is gone. Both identities are fenced.
 DELETE FROM public.bookings WHERE organization_id=p_org AND id=p_booking;
 IF NOT public.ordinary_verify_booking_retention_purge(p_org,p_booking,p_plan) THEN
  RAISE EXCEPTION 'Ordinary retention postconditions not met' USING ERRCODE='55000'; END IF;
 RETURN true;
EXCEPTION WHEN lock_not_available THEN RETURN false;
END $$;
REVOKE ALL ON FUNCTION public.ordinary_admin_purge_booking(uuid,uuid,jsonb,text,bigint,text)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;
