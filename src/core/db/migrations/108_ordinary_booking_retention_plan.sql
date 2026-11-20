SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Owner-only review of a deliberately narrow manual-booking subset. Produce
-- the exact candidate scope for a future independently authenticated journal,
-- while neither retiring data nor granting authority to a purge worker.
CREATE OR REPLACE FUNCTION public.ordinary_completed_booking_retention_plan(p_org uuid,p_booking uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
 b public.bookings%ROWTYPE;
 e public.ordinary_booking_completion_evidence%ROWTYPE;
 a public.booking_allocation_sets%ROWTYPE;
 policy_version text;
 requests uuid[];
 claim_count integer;
BEGIN
 IF current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
   WHERE oid='public.ordinary_booking_completion_evidence'::regclass)) THEN
  RAISE EXCEPTION 'Ordinary booking review requires the lifecycle owner' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_read_only')<>'on' OR current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Ordinary booking review requires a read-only read committed transaction' USING ERRCODE='25000';
 END IF;
 IF p_org IS NULL OR p_booking IS NULL OR NOT pg_try_advisory_xact_lock(hashtextextended(p_org::text,606)) THEN
  RETURN NULL;
 END IF;
 IF NOT public.ordinary_completed_booking_retention_eligible(p_org,p_booking) THEN RETURN NULL; END IF;

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
    OR a.timezone<>e.timezone OR a.booking_calendar_revision<>b.calendar_revision THEN
  RETURN NULL;
 END IF;

 -- No source-linked booking, provider sync or payment history is inferred
 -- safe. These checks intentionally defer any structurally known dependent
 -- copy, instead of deleting account, usage, audit or provider evidence.
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
       AND (strpos(i.payload::text,p_booking::text)>0 OR strpos(i.request_key,p_booking::text)>0)) THEN
  RETURN NULL;
 END IF;

 SELECT count(*) INTO claim_count FROM public.booking_resource_allocations r
  WHERE r.organization_id=p_org AND r.booking_id=p_booking;
 IF claim_count NOT BETWEEN 1 AND 128 OR EXISTS(SELECT 1 FROM public.booking_resource_allocations r
   WHERE r.organization_id=p_org AND r.booking_id=p_booking
    AND (r.state,r.allocation_revision,r.catalog_revision,r.starts_at,r.ends_at)
     IS DISTINCT FROM (a.state,a.allocation_revision,a.catalog_revision,a.starts_at,a.ends_at)) THEN
  RETURN NULL;
 END IF;
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
          AND v.hold_id=h.hold_id AND v.kind='released')) THEN
  RETURN NULL;
 END IF;

 -- The caller must retain these exact IDs and immutable evidence in the
 -- recovery authority, recheck all dependencies under locks at execution,
 -- and cover older recoverable backups before permitting any actual purge.
 RETURN jsonb_build_object('kind','ordinary_managed_booking_candidate_v1','organization_id',p_org,
  'booking_id',p_booking,'subject_id',e.subject_id,'policy_version',policy_version,
  'completed_at',to_char(e.completed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'timezone',e.timezone,'allocation_revision',a.allocation_revision,'request_ids',requests,
  'deletion_authorized',false);
END $$;
REVOKE ALL ON FUNCTION public.ordinary_completed_booking_retention_plan(uuid,uuid)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
