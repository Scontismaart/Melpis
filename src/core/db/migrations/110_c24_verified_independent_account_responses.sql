SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Qualify an unlinked response only when a current A04 ledger commit ties it
-- to an explicit account-channel operation. Unknown or stale provenance still
-- defers the source. The account response and accounting are left intact.
CREATE TABLE IF NOT EXISTS public.c24_account_response_receipts (
 organization_id uuid NOT NULL,
 auth_user_id uuid NOT NULL,
 request_id uuid NOT NULL,
 channel text NOT NULL CHECK (channel IN ('simulator','qa')),
 response_sha256 bytea NOT NULL CHECK(octet_length(response_sha256)=32),
 ledger_sha256 bytea NOT NULL CHECK(octet_length(ledger_sha256)=32),
 PRIMARY KEY (organization_id,request_id),
 FOREIGN KEY (organization_id,auth_user_id,request_id)
  REFERENCES public.simulation_requests (organization_id,auth_user_id,request_id) ON DELETE CASCADE
);
ALTER TABLE public.c24_account_response_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.c24_account_response_receipts FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention;
DROP TRIGGER IF EXISTS c24_account_response_receipt_immutable ON public.c24_account_response_receipts;
CREATE TRIGGER c24_account_response_receipt_immutable BEFORE UPDATE ON public.c24_account_response_receipts
 FOR EACH ROW EXECUTE FUNCTION public.gdpr_immutable_record();
-- Parent lifecycle resets may cascade to the receipt. Loss of the receipt
-- causes deferral. No runtime principal has a direct TRUNCATE grant.
DROP TRIGGER IF EXISTS c24_account_response_receipt_no_truncate ON public.c24_account_response_receipts;

-- Called in the same transaction as the trusted A04 account commit after
-- verifying the deterministic operation, current trust epoch and response hash.
-- The two server-side digests guard the exact stored binary evidence from later drift.
CREATE OR REPLACE FUNCTION public.c24_record_account_response_receipt(
 p_org uuid,p_actor uuid,p_request uuid,p_channel text,p_claim uuid,p_epoch uuid,p_response_sha text
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF p_channel NOT IN ('simulator','qa') OR p_actor='c7a07dfd-ee70-534e-a33d-86defd371f38'::uuid
    OR p_response_sha !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
 INSERT INTO public.c24_account_response_receipts
  (organization_id,auth_user_id,request_id,channel,response_sha256,ledger_sha256)
 SELECT p_org,p_actor,p_request,p_channel,sha256(convert_to(r.response::text,'UTF8')),
        sha256(convert_to(u.metadata::text,'UTF8'))
 FROM public.simulation_requests r JOIN public.usage_events u
   ON u.organization_id=r.organization_id AND u.id=r.request_id
 WHERE r.organization_id=p_org AND u.organization_id=p_org AND r.auth_user_id=p_actor AND r.request_id=p_request
   AND r.xmin=pg_current_xact_id()::xid AND u.xmin=pg_current_xact_id()::xid
   AND r.status='completed' AND r.claim_token=p_claim AND r.c24_source_message_id IS NULL
   AND r.c24_subject_id IS NULL AND r.c24_conversation_id IS NULL AND jsonb_typeof(r.response)='object'
   AND u.event_type='system.quota_reservation' AND u.quantity=0 AND u.metadata->'version'='1'::jsonb
   AND u.metadata->>'state'='committed' AND u.metadata->>'claim_id'=p_claim::text
   AND u.metadata->>'channel'=p_channel AND u.metadata->>'epoch'=p_epoch::text
   AND u.metadata->>'payload_hash'=r.payload_hash AND u.metadata->>'response_hash'=p_response_sha
 ON CONFLICT (organization_id,request_id) DO NOTHING;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION public.c24_record_account_response_receipt(uuid,uuid,uuid,text,uuid,uuid,text)
 FROM PUBLIC,anon,authenticated,melpis_c24_retention,melpis_c24_booking_worker;
GRANT EXECUTE ON FUNCTION public.c24_record_account_response_receipt(uuid,uuid,uuid,text,uuid,uuid,text) TO service_role;

CREATE OR REPLACE FUNCTION public.c24_account_response_evidence_protected()
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT count(*)=4 AND bool_and(c.relrowsecurity) AND NOT bool_or(EXISTS(
   SELECT 1 FROM (VALUES('anon'),('authenticated')) principal(name)
   CROSS JOIN (VALUES('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('REFERENCES'),('TRIGGER')) access(kind)
   WHERE has_table_privilege(principal.name,c.oid,access.kind)
      OR CASE WHEN access.kind IN ('INSERT','UPDATE','REFERENCES')
              THEN has_any_column_privilege(principal.name,c.oid,access.kind) ELSE false END
      OR CASE WHEN current_setting('server_version_num')::integer>=170000
              THEN has_table_privilege(principal.name,c.oid,'MAINTAIN') ELSE false END))
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname IN ('messages','usage_events','simulation_requests','c24_account_response_receipts');
$$;
REVOKE ALL ON FUNCTION public.c24_account_response_evidence_protected()
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_retention,melpis_c24_booking_worker;

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
 IF NOT FOUND OR m.conversation_id IS DISTINCT FROM conv
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
    OR EXISTS(SELECT 1 FROM public.simulation_requests r WHERE r.organization_id=p_org
       AND (r.status='reserved' OR (r.response IS NOT NULL AND r.c24_source_message_id IS NULL
         AND NOT EXISTS(SELECT 1 FROM public.c24_account_response_receipts proof JOIN public.usage_events u
            ON u.organization_id=proof.organization_id AND u.id=proof.request_id
           JOIN public.usage_events marker
            ON marker.id='6206d423-cea9-5480-bb9b-59d757503f14'::uuid
           AND marker.organization_id IS NULL AND marker.event_type='system.quota_trust_epoch' AND marker.quantity=0
           AND marker.metadata->'protocol_version'='1'::jsonb
           AND marker.metadata - 'protocol_version' - 'trust_epoch'='{}'::jsonb
           AND marker.metadata->>'trust_epoch' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
           AND marker.metadata->>'trust_epoch'<>'00000000-0000-0000-0000-000000000000'
          WHERE proof.organization_id=p_org AND proof.request_id=r.request_id AND proof.auth_user_id=r.auth_user_id
            AND public.c24_account_response_evidence_protected()
            AND proof.response_sha256=sha256(convert_to(r.response::text,'UTF8'))
            AND proof.ledger_sha256=sha256(convert_to(u.metadata::text,'UTF8'))
            AND u.organization_id=p_org AND u.event_type='system.quota_reservation'
            AND u.quantity=0 AND u.metadata->'version'='1'::jsonb AND u.metadata->>'state'='committed'
            AND u.metadata->>'channel'=proof.channel AND u.metadata->'epoch'=marker.metadata->'trust_epoch'
            AND u.metadata->>'payload_hash'=r.payload_hash AND u.metadata->>'claim_id'=r.claim_token::text
            AND u.metadata->>'response_hash' ~ '^[0-9a-f]{64}$'
            AND r.status='completed' AND r.completed_at<=clock_timestamp()
            AND r.c24_conversation_id IS NULL AND r.c24_subject_id IS NULL
            AND r.auth_user_id<>'c7a07dfd-ee70-534e-a33d-86defd371f38'::uuid
            AND jsonb_typeof(r.response)='object' AND NOT(r.response ? 'booking_state_proposal')
            AND NOT EXISTS(SELECT 1 FROM public.simulation_requests x WHERE x.organization_id=p_org
              AND x.request_id=r.request_id AND x.auth_user_id<>r.auth_user_id)))))
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
 UPDATE public.message_delivery_attempts a SET error_details=NULL
  WHERE a.message_id=p_message AND EXISTS(SELECT 1 FROM public.messages x WHERE x.organization_id=p_org AND x.id=a.message_id);
 UPDATE public.outbound_dedup SET response_text=NULL WHERE organization_id=p_org AND message_id=p_message;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.c24_purge_source(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
