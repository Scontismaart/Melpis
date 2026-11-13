-- Private booking worker uses capability-scoped, tenant-filtered views.
-- No table, sequence, or public-schema data privileges are granted to the
-- worker. SECURITY DEFINER helpers are callable only by the dedicated login
-- and only while a DB-derived job claim exists in the current transaction.

-- Repair the old 086 profile before exposing the private bridge. This is
-- intentionally fail-closed: role memberships other than Supabase's exact
-- non-inheritable platform administration edge, or pre-existing ownership,
-- require manual reconciliation rather than implicit privilege cleanup.
-- On Supabase PostgreSQL the managed `postgres` role is not a superuser and
-- cannot ALTER ROLE attributes that it cannot itself grant. 086 creates this
-- role with the safe defaults; here we only reaffirm attributes the platform
-- operator may change, then the assertions below fail closed if any protected
-- flag is unsafe.
ALTER ROLE melpis_c24_booking_worker NOLOGIN NOINHERIT PASSWORD NULL;
REVOKE ALL ON SCHEMA public FROM melpis_c24_booking_worker;
-- Name resolution for explicitly qualified catalog probes is required by the
-- private lifecycle/runtime. USAGE exposes no relation data; table, column,
-- sequence, and routine ACLs remain separately revoked and audited.
GRANT USAGE ON SCHEMA public TO melpis_c24_booking_worker;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM melpis_c24_booking_worker;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM melpis_c24_booking_worker;
REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA public FROM melpis_c24_booking_worker;

DO $$
DECLARE worker oid := 'melpis_c24_booking_worker'::regrole;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE oid=worker AND
        (rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb OR rolcreaterole
         OR rolreplication OR rolbypassrls)) THEN
        RAISE EXCEPTION 'private booking worker role flags are unsafe';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=worker) THEN
        RAISE EXCEPTION 'private booking worker inherits an unexpected role';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles member ON member.oid=m.member
        JOIN pg_roles grantor ON grantor.oid=m.grantor
        WHERE m.roleid=worker AND NOT (
            member.rolname='postgres'
            AND grantor.rolname IN ('supabase_admin','postgres')
            AND m.admin_option AND NOT m.inherit_option AND NOT m.set_option
        )
    ) THEN
        RAISE EXCEPTION 'private booking worker has a non-platform membership';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relowner=worker)
       OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner=worker)
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner=worker) THEN
        RAISE EXCEPTION 'private booking worker owns database objects';
    END IF;
    IF has_schema_privilege(worker,'public','CREATE') THEN
        RAISE EXCEPTION 'private booking worker can create objects in public';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f','S')
          AND (has_table_privilege(worker,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            OR (c.relkind='S' AND has_sequence_privilege(worker,c.oid,'USAGE,SELECT,UPDATE')))
    ) THEN
        RAISE EXCEPTION 'public objects expose effective data privileges to the worker';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef
          AND has_function_privilege(worker,p.oid,'EXECUTE')
    ) THEN
        RAISE EXCEPTION 'public SECURITY DEFINER function is executable by the worker';
    END IF;
END;
$$;



CREATE SCHEMA IF NOT EXISTS c24_private;
REVOKE ALL ON SCHEMA c24_private FROM PUBLIC,anon,authenticated,service_role,
    melpis_c24_booking_worker;
REVOKE ALL ON ALL TABLES IN SCHEMA c24_private
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA c24_private
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA c24_private
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
GRANT USAGE ON SCHEMA c24_private TO melpis_c24_booking_worker;
REVOKE CREATE ON SCHEMA c24_private FROM melpis_c24_booking_worker;

-- A trusted application proof is exchanged for this one-use capability before
-- the private SQL worker can see any booking context. The worker has no table
-- privileges and cannot mint or read capabilities; only the application
-- database owner may issue one. Expired unused rows remain auditable and are
-- never treated as evidence that an A03 provider attempt cost zero.
CREATE TABLE IF NOT EXISTS c24_private.worker_capabilities (
    capability_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    message_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    booking_id uuid NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    issued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    consumed_backend_pid integer,
    consumed_transaction_id xid8,
    completed_at timestamptz,
    UNIQUE (organization_id,message_id,claim_id,generation),
    CONSTRAINT worker_capabilities_expiry_bound
        CHECK (expires_at>issued_at AND expires_at<=issued_at+interval '5 minutes'),
    CHECK ((consumed_at IS NULL AND consumed_backend_pid IS NULL AND consumed_transaction_id IS NULL)
        OR (consumed_at IS NOT NULL AND consumed_backend_pid IS NOT NULL AND consumed_transaction_id IS NOT NULL))
);
-- The worker renews a live fence up to its five-minute per-generation ceiling.
-- Replace the original short issuance bound on reruns as well, while keeping
-- the database-enforced ceiling aligned with the lease function's hard cap.
ALTER TABLE c24_private.worker_capabilities
    DROP CONSTRAINT IF EXISTS worker_capabilities_check;
ALTER TABLE c24_private.worker_capabilities
    DROP CONSTRAINT IF EXISTS worker_capabilities_expiry_bound;
ALTER TABLE c24_private.worker_capabilities
    ADD CONSTRAINT worker_capabilities_expiry_bound
    CHECK (expires_at>issued_at AND expires_at<=issued_at+interval '5 minutes');
ALTER TABLE c24_private.worker_capabilities ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON c24_private.worker_capabilities
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

-- A committed capability row is invalid: the transaction id and backend id
-- must match the current transaction. finish_job removes it before commit;
-- crashes roll it back with the job claim. Stale rows from interrupted
-- clients contain only UUID lineage and are pruned on the next claim.
CREATE TABLE IF NOT EXISTS c24_private.worker_context (
    backend_pid integer NOT NULL,
    transaction_id xid8 NOT NULL,
    organization_id uuid NOT NULL,
    message_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    booking_id uuid,
    confirmation_message_id uuid,
    prompt_message_id uuid,
    inbox_id uuid,
    confirmation_inbox_id uuid,
    operation_id uuid,
    claim_id uuid NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    capability_id uuid,
    job_kind text NOT NULL DEFAULT 'confirmation' CHECK (job_kind IN ('confirmation','a05')),
    lease_until timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (backend_pid, transaction_id)
);

-- A claim has a hard per-generation ceiling. Heartbeats can extend only a live
-- fence and never let an expired/stale worker reserve the same source forever.
ALTER TABLE public.booking_worker_claims
    ADD COLUMN IF NOT EXISTS claim_started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    ADD COLUMN IF NOT EXISTS max_lease_until timestamptz NOT NULL DEFAULT clock_timestamp()+interval '5 minutes';
ALTER TABLE c24_private.worker_context ENABLE ROW LEVEL SECURITY;
ALTER TABLE c24_private.worker_context NO FORCE ROW LEVEL SECURITY;
ALTER TABLE c24_private.worker_context ADD COLUMN IF NOT EXISTS booking_id uuid;
ALTER TABLE c24_private.worker_context ADD COLUMN IF NOT EXISTS confirmation_message_id uuid;
ALTER TABLE c24_private.worker_context ADD COLUMN IF NOT EXISTS prompt_message_id uuid;
ALTER TABLE c24_private.worker_context ADD COLUMN IF NOT EXISTS inbox_id uuid;
ALTER TABLE c24_private.worker_context ADD COLUMN IF NOT EXISTS confirmation_inbox_id uuid;
ALTER TABLE c24_private.worker_context ADD COLUMN IF NOT EXISTS operation_id uuid;
ALTER TABLE c24_private.worker_context ADD COLUMN IF NOT EXISTS capability_id uuid;
ALTER TABLE c24_private.worker_context ADD COLUMN IF NOT EXISTS job_kind text NOT NULL DEFAULT 'confirmation';
REVOKE ALL ON c24_private.worker_context FROM PUBLIC, melpis_c24_booking_worker;

-- Read the immutable global quota marker without requiring UPDATE privilege on
-- usage_events (SELECT FOR SHARE does require it). The private worker can call
-- this only after consuming a live, transaction-bound capability; callers can
-- neither choose a marker ID nor read any other ledger data through this RPC.
CREATE OR REPLACE FUNCTION c24_private.c24_quota_trust_epoch()
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE metadata jsonb; epoch_text text; epoch uuid; marker_count integer;
BEGIN
    IF session_user='postgres' AND current_user='postgres' THEN
        NULL;
    ELSIF session_user='melpis_c24_booking_worker' AND EXISTS (
        SELECT 1 FROM c24_private.worker_context AS context
        JOIN public.booking_worker_claims AS claim
          ON claim.organization_id=context.organization_id
         AND claim.message_id=context.message_id
         AND claim.claim_id=context.claim_id
         AND claim.generation=context.generation
         AND claim.lease_until=context.lease_until
        JOIN c24_private.worker_capabilities AS capability
          ON capability.capability_id=context.capability_id
         AND capability.organization_id=context.organization_id
         AND capability.message_id=context.message_id
         AND capability.claim_id=context.claim_id
         AND capability.generation=context.generation
         AND capability.consumed_backend_pid=pg_backend_pid()
         AND capability.consumed_transaction_id=pg_current_xact_id()
         AND capability.completed_at IS NULL
        WHERE context.backend_pid=pg_backend_pid()
          AND context.transaction_id=pg_current_xact_id()
          AND context.lease_until>clock_timestamp()
          AND claim.lease_until>clock_timestamp()
    ) THEN
        NULL;
    ELSE
        RAISE EXCEPTION 'quota trust marker unavailable' USING ERRCODE='42501';
    END IF;

    SELECT count(*) INTO marker_count FROM pg_class AS relation
      JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
     WHERE namespace.nspname='public' AND relation.relname IN ('messages','usage_events')
       AND relation.relrowsecurity;
    IF marker_count<>2 OR EXISTS (
        SELECT 1 FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
        CROSS JOIN (VALUES ('anon'),('authenticated')) AS principal(name)
        CROSS JOIN unnest(ARRAY['INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']::text[]) AS privilege(privilege_name)
        WHERE namespace.nspname='public' AND relation.relname IN ('messages','usage_events')
          AND has_table_privilege(principal.name,relation.oid,privilege.privilege_name)
    ) OR EXISTS (
        SELECT 1 FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
        CROSS JOIN (VALUES ('anon'),('authenticated')) AS principal(name)
        CROSS JOIN (VALUES ('INSERT'),('UPDATE'),('REFERENCES')) AS privilege(privilege_name)
        WHERE namespace.nspname='public' AND relation.relname IN ('messages','usage_events')
          AND has_any_column_privilege(principal.name,relation.oid,privilege.privilege_name)
    ) OR EXISTS (
        SELECT 1 FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
        CROSS JOIN (VALUES ('anon'),('authenticated')) AS principal(name)
        CROSS JOIN (VALUES ('INSERT'),('UPDATE'),('REFERENCES')) AS privilege(privilege_name)
        WHERE namespace.nspname='public' AND relation.relname IN ('messages','usage_events')
          AND EXISTS (
              SELECT 1 FROM pg_attribute AS attribute
               WHERE attribute.attrelid=relation.oid AND attribute.attnum>0
                 AND NOT attribute.attisdropped
                 AND has_column_privilege(principal.name,relation.oid,attribute.attnum,
                                          privilege.privilege_name)
          )
    ) THEN
        RAISE EXCEPTION 'quota trust marker unavailable' USING ERRCODE='42501';
    END IF;

    SELECT row.metadata INTO metadata FROM public.usage_events AS row
     WHERE row.id='6206d423-cea9-5480-bb9b-59d757503f14'::uuid
       AND row.organization_id IS NULL
       AND row.event_type='system.quota_trust_epoch' AND row.quantity=0
     FOR SHARE;
    IF NOT FOUND OR metadata IS NULL OR jsonb_typeof(metadata)<>'object'
       OR metadata - 'protocol_version' - 'trust_epoch'<>'{}'::jsonb
       OR metadata->'protocol_version'<>'1'::jsonb
       OR jsonb_typeof(metadata->'trust_epoch')<>'string' THEN
        RAISE EXCEPTION 'quota trust marker unavailable' USING ERRCODE='42501';
    END IF;
    epoch_text:=metadata->>'trust_epoch';
    IF epoch_text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RAISE EXCEPTION 'quota trust marker unavailable' USING ERRCODE='42501';
    END IF;
    epoch:=epoch_text::uuid;
    IF epoch='00000000-0000-0000-0000-000000000000'::uuid OR epoch::text<>epoch_text THEN
        RAISE EXCEPTION 'quota trust marker unavailable' USING ERRCODE='42501';
    END IF;
    RETURN epoch;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_quota_trust_epoch()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
GRANT EXECUTE ON FUNCTION c24_private.c24_quota_trust_epoch()
    TO postgres,melpis_c24_booking_worker;

DO $$
DECLARE context_owner oid;
BEGIN
    SELECT c.relowner INTO context_owner
      FROM pg_class c WHERE c.oid='c24_private.worker_context'::regclass;
    IF (SELECT nspowner FROM pg_namespace WHERE nspname='c24_private')<>context_owner
       OR context_owner='melpis_c24_booking_worker'::regrole
       OR context_owner IN ('anon'::regrole,'authenticated'::regrole,'service_role'::regrole) THEN
        RAISE EXCEPTION 'private worker schema/context owner is not a trusted lifecycle role';
    END IF;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid='c24_private.worker_context'::regclass)
       OR (SELECT relforcerowsecurity FROM pg_class WHERE oid='c24_private.worker_context'::regclass) THEN
        RAISE EXCEPTION 'private worker context must use owner-bypass RLS without forced owner filtering';
    END IF;
END;
$$;

-- Only the trusted application database login may mint an exact, short-lived
-- effect capability. The SQL worker can consume a bearer token once, but can
-- neither enumerate/mint capabilities nor alter their payload.
CREATE OR REPLACE FUNCTION c24_private.c24_worker_issue_capability(
    p_organization_id uuid,p_message_id uuid,p_claim_id uuid,p_generation bigint,
    p_booking_id uuid,p_payload jsonb,p_payload_hash text,p_ttl_seconds integer
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE existing c24_private.worker_capabilities%ROWTYPE; token uuid; claim public.booking_worker_claims%ROWTYPE;
        expected_payload_hash text;
BEGIN
    IF session_user IS DISTINCT FROM 'postgres' OR current_user IS DISTINCT FROM 'postgres' THEN
        RAISE EXCEPTION 'trusted application database identity required' USING ERRCODE='42501';
    END IF;
    IF p_organization_id IS NULL OR p_message_id IS NULL OR p_claim_id IS NULL
       OR p_generation IS NULL OR p_generation<1 OR p_booking_id IS NULL
       OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object'
       OR octet_length(p_payload::text)>262144
       OR p_payload_hash IS NULL OR p_payload_hash !~ '^[a-f0-9]{64}$'
       OR p_ttl_seconds IS NULL OR p_ttl_seconds<1 OR p_ttl_seconds>30
       OR p_payload->>'version' IS DISTINCT FROM '1'
       OR (p_payload->>'job_kind' IS DISTINCT FROM 'a05'
           AND p_payload->>'job_kind' IS DISTINCT FROM 'confirmation')
       OR p_payload->>'organization_id' IS DISTINCT FROM p_organization_id::text
       OR p_payload->>'message_id' IS DISTINCT FROM p_message_id::text
       OR p_payload->>'source_message_id' IS DISTINCT FROM p_message_id::text
       OR p_payload->>'claim_id' IS DISTINCT FROM p_claim_id::text
       OR p_payload->>'generation' IS DISTINCT FROM p_generation::text
       OR p_payload->>'booking_id' IS DISTINCT FROM p_booking_id::text
       OR p_payload->>'conversation_id' IS NULL
       OR p_payload->>'subject_id' IS NULL
       OR p_payload->>'operation_id' IS NULL
       OR p_payload->>'inbox_id' IS NULL
       OR NOT (p_payload ? 'stay')
       OR NOT (p_payload->'effects' ? 'outbound_message')
       OR NOT (p_payload->'effects' ? 'confirmation_outbox')
       OR NOT (p_payload->'effects' ? 'calendar')
       OR jsonb_typeof(p_payload->'booking') IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_payload->'allocation') IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_payload->'allocation'->'request') IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_payload->'allocation'->'input') IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_payload->'allocation'->'set') IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_payload->'allocation'->'resources') IS DISTINCT FROM 'array'
       OR jsonb_typeof(p_payload->'effects') IS DISTINCT FROM 'object'
       OR p_payload->'booking'->>'id' IS DISTINCT FROM p_booking_id::text
       OR p_payload->'booking'->>'organization_id' IS DISTINCT FROM p_organization_id::text
       OR p_payload->'booking'->>'source_message_id' IS DISTINCT FROM p_message_id::text
       OR p_payload->'booking'->>'id_conversazione' IS DISTINCT FROM p_payload->>'conversation_id'
       OR p_payload->'booking'->>'contact_id' IS DISTINCT FROM p_payload->>'subject_id'
       OR p_payload->'allocation'->'request'->>'booking_id' IS DISTINCT FROM p_booking_id::text
       OR p_payload->'allocation'->'input'->>'booking_id' IS DISTINCT FROM p_booking_id::text
       OR p_payload->'allocation'->'request'->>'organization_id' IS DISTINCT FROM p_organization_id::text
       OR p_payload->'allocation'->'request'->>'service_message_id' IS DISTINCT FROM p_message_id::text
       OR p_payload->'allocation'->'request'->>'service_inbox_id' IS DISTINCT FROM p_payload->>'inbox_id'
       OR p_payload->'allocation'->'request'->>'service_operation_id' IS DISTINCT FROM p_payload->>'operation_id'
       OR p_payload->'allocation'->'request'->>'payload_hash' IS DISTINCT FROM p_payload->'allocation'->>'semantic_hash'
       OR p_payload->'allocation'->'input'->>'expected_catalog_revision'
            IS DISTINCT FROM p_payload->'allocation'->'set'->>'catalog_revision'
       OR p_payload->'allocation'->'input'->>'action' IS DISTINCT FROM 'allocate'
       OR p_payload->'allocation'->'input'->>'expected_allocation_revision' IS DISTINCT FROM '0'
       OR p_payload->'allocation'->'input'->>'service_id' IS DISTINCT FROM p_payload->'allocation'->'set'->>'service_id'
       OR p_payload->'allocation'->'request'->>'action' IS DISTINCT FROM 'allocate'
       OR p_payload->'allocation'->'request'->>'actor_kind' IS DISTINCT FROM 'inbound_worker'
       OR p_payload->'allocation'->'request'->>'outcome' IS DISTINCT FROM 'allocated'
       OR p_payload->'allocation'->'request'->>'applied_allocation_revision' IS DISTINCT FROM '1'
       OR p_payload->'allocation'->'set'->>'booking_id' IS DISTINCT FROM p_booking_id::text
       OR p_payload->'allocation'->'set'->>'state' IS DISTINCT FROM 'active'
       OR p_payload->'allocation'->'set'->>'booking_calendar_revision'
            IS DISTINCT FROM p_payload->'booking'->>'calendar_revision'
       OR jsonb_array_length(p_payload->'allocation'->'resources')=0
       OR jsonb_array_length(p_payload->'allocation'->'resources') <>
          (SELECT count(DISTINCT resource.value->>'resource_id')
             FROM jsonb_array_elements(p_payload->'allocation'->'resources') AS resource(value))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(p_payload->'allocation'->'resources') AS resource(value)
           WHERE resource.value->>'organization_id' IS DISTINCT FROM p_organization_id::text
              OR resource.value->>'booking_id' IS DISTINCT FROM p_booking_id::text
              OR resource.value->>'state' IS DISTINCT FROM 'active'
              OR resource.value->>'catalog_revision' IS DISTINCT FROM p_payload->'allocation'->'set'->>'catalog_revision'
              OR resource.value->>'allocation_revision' IS DISTINCT FROM p_payload->'allocation'->'set'->>'allocation_revision'
              OR resource.value->>'starts_at' IS DISTINCT FROM p_payload->'allocation'->'set'->>'starts_at'
              OR resource.value->>'ends_at' IS DISTINCT FROM p_payload->'allocation'->'set'->>'ends_at')
       OR (p_payload->'stay' IS NOT NULL AND p_payload->'stay'<>'null'::jsonb
           AND (jsonb_typeof(p_payload->'stay') IS DISTINCT FROM 'object'
             OR p_payload->'stay'->>'organization_id' IS DISTINCT FROM p_organization_id::text
             OR p_payload->'stay'->>'booking_id' IS DISTINCT FROM p_booking_id::text))
       OR ((p_payload->'effects'->'outbound_message'='null'::jsonb)
           <> (p_payload->'effects'->'confirmation_outbox'='null'::jsonb))
       OR p_payload->'booking'->>'stato' NOT IN ('confermata','in_attesa')
       OR (p_payload->'booking'->>'richiede_intervento')::boolean <>
            (p_payload->'booking'->>'calendar_confirm_pending')::boolean
       OR (p_payload->'booking'->>'stato'='in_attesa') <>
            (p_payload->'booking'->>'calendar_confirm_pending')::boolean
       OR ((p_payload->'booking'->>'calendar_confirm_pending')::boolean
           AND (jsonb_typeof(p_payload->'effects'->'calendar') IS DISTINCT FROM 'object'
             OR (p_payload->'effects'->'calendar'->>'authoritative') IS DISTINCT FROM 'true'))
       OR (NOT (p_payload->'booking'->>'calendar_confirm_pending')::boolean
           AND jsonb_typeof(p_payload->'effects'->'calendar')='object'
           AND (p_payload->'effects'->'calendar'->>'authoritative')='true')
       OR (jsonb_typeof(p_payload->'effects'->'calendar')='object'
           AND (p_payload->'effects'->'calendar'->>'required') IS DISTINCT FROM 'true')
       OR (p_payload->'booking'->>'calendar_confirm_pending')='true'
           AND jsonb_typeof(p_payload->'effects'->'calendar') IS DISTINCT FROM 'object'
       OR (jsonb_typeof(p_payload->'effects'->'calendar')='object'
           AND (p_payload->'effects'->'calendar'->>'idempotency_key') IS DISTINCT FROM
                 ('google-booking:'||p_organization_id::text||':'||p_booking_id::text))
       OR (jsonb_typeof(p_payload->'effects'->'calendar')='object'
           AND (p_payload->'effects'->'calendar'->>'desired_revision') IS DISTINCT FROM
                 p_payload->'booking'->>'calendar_revision')
       OR (jsonb_typeof(p_payload->'effects'->'calendar')='object'
           AND (p_payload->'effects'->'calendar'->>'action') IS DISTINCT FROM
                 CASE WHEN (p_payload->'booking'->>'calendar_confirm_pending')::boolean
                      THEN 'authoritative' ELSE 'upsert' END)
       OR (jsonb_typeof(p_payload->'effects'->'calendar')='object'
           AND (p_payload->'effects'->'calendar'->>'authoritative') IS DISTINCT FROM
                 p_payload->'booking'->>'calendar_confirm_pending')
       OR (jsonb_typeof(p_payload->'effects'->'calendar')='object'
           AND (p_payload->'effects'->'calendar'->>'sync_status') NOT IN ('pending','blocked')) THEN
        RAISE EXCEPTION 'invalid private booking capability payload' USING ERRCODE='22023';
    END IF;
    SELECT * INTO claim FROM public.booking_worker_claims
     WHERE organization_id=p_organization_id AND message_id=p_message_id
       AND claim_id=p_claim_id AND generation=p_generation
       AND lease_until>clock_timestamp()
     FOR UPDATE;
    IF NOT FOUND OR claim.conversation_id IS DISTINCT FROM NULLIF(p_payload->>'conversation_id','')::uuid
       OR claim.lease_until IS DISTINCT FROM NULLIF(p_payload->>'lease_until','')::timestamptz
       OR p_payload->>'source_message_id' IS DISTINCT FROM p_message_id::text
       OR p_payload->>'subject_id' IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM public.conversations AS c
           JOIN public.contacts AS subject
             ON subject.organization_id=c.organization_id AND subject.id=c.contact_id
           JOIN public.messages AS source
             ON source.organization_id=c.organization_id AND source.conversation_id=c.id
            AND source.id=p_message_id AND source.direction='inbound' AND source.deleted_at IS NULL
           WHERE c.organization_id=p_organization_id AND c.id=claim.conversation_id
             AND c.contact_id=NULLIF(p_payload->>'subject_id','')::uuid AND c.deleted_at IS NULL
             AND source.c24_original_subject_id=c.contact_id AND subject.deleted_at IS NULL
             AND NOT EXISTS (SELECT 1 FROM public.gdpr_erasure_barriers AS e
                 WHERE e.organization_id=p_organization_id
                   AND (e.scope='organization' OR e.subject_id=subject.id))
       ) OR NOT EXISTS (
           SELECT 1 FROM public.inbound_worker_operations AS op
           JOIN public.inbound_worker_receipts AS receipt
             ON receipt.organization_id=op.organization_id AND receipt.inbox_id=op.inbox_id
            AND receipt.message_id=op.message_id
           JOIN public.meta_webhook_inbox AS inbox
             ON inbox.organization_id=receipt.organization_id AND inbox.id=receipt.inbox_id
           JOIN public.usage_events AS event
             ON event.organization_id=op.organization_id AND event.id=op.operation_id
           JOIN public.simulation_requests AS request
             ON request.organization_id=op.organization_id
            AND request.request_id=op.operation_id
            JOIN public.auth_access_lifecycle AS access
              ON access.auth_user_id=request.auth_user_id AND access.disabled_at IS NULL
            JOIN public.user_profiles AS profile
              ON profile.auth_user_id=request.auth_user_id
            JOIN public.organization_memberships AS membership
              ON membership.organization_id=op.organization_id
             AND membership.user_id=profile.id AND membership.joined_at IS NOT NULL
             AND membership.ruolo IN ('owner','manager','staff')
           WHERE op.organization_id=p_organization_id AND op.message_id=p_message_id
             AND op.operation_id=NULLIF(p_payload->>'operation_id','')::uuid
             AND inbox.channel='whatsapp' AND inbox.status='completed'
             AND event.event_type='system.quota_reservation' AND event.quantity=0
             AND event.metadata->>'channel'='inbound' AND event.metadata->>'state'='committed'
             AND request.status='completed' AND request.response IS NOT NULL
       ) THEN
        RAISE EXCEPTION 'current persisted worker claim and source required' USING ERRCODE='42501';
    END IF;
    expected_payload_hash := encode(pg_catalog.sha256(convert_to(p_payload::text,'UTF8')),'hex');
    IF p_payload_hash IS DISTINCT FROM expected_payload_hash THEN
        RAISE EXCEPTION 'private booking capability payload hash mismatch' USING ERRCODE='22023';
    END IF;
    SELECT * INTO existing FROM c24_private.worker_capabilities
     WHERE organization_id=p_organization_id AND message_id=p_message_id
       AND claim_id=p_claim_id AND generation=p_generation
     FOR UPDATE;
    IF FOUND THEN
        IF existing.payload_hash=p_payload_hash AND existing.expires_at>clock_timestamp()
           AND existing.consumed_at IS NULL THEN RETURN existing.capability_id; END IF;
        RAISE EXCEPTION 'claim capability already issued or expired; acquire a new fenced claim'
            USING ERRCODE='40001';
    END IF;
    token := gen_random_uuid();
    INSERT INTO c24_private.worker_capabilities
        (capability_id,organization_id,message_id,claim_id,generation,booking_id,
         payload,payload_hash,expires_at)
    VALUES(token,p_organization_id,p_message_id,p_claim_id,p_generation,p_booking_id,
           p_payload,p_payload_hash,LEAST(clock_timestamp()+make_interval(secs=>p_ttl_seconds),claim.lease_until));
    RETURN token;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_issue_capability(uuid,uuid,uuid,bigint,uuid,jsonb,text,integer)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_issue_capability(uuid,uuid,uuid,bigint,uuid,jsonb,text,integer)
    TO postgres;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_consume_capability(p_capability_id uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE cap c24_private.worker_capabilities%ROWTYPE; claim public.booking_worker_claims%ROWTYPE;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
        RAISE EXCEPTION 'dedicated booking worker required' USING ERRCODE='42501';
    END IF;
    IF EXISTS (SELECT 1 FROM c24_private.worker_context
               WHERE backend_pid=pg_backend_pid() AND transaction_id=pg_current_xact_id()) THEN
        RAISE EXCEPTION 'worker transaction already has a capability' USING ERRCODE='42501';
    END IF;
    SELECT * INTO cap FROM c24_private.worker_capabilities
     WHERE capability_id=p_capability_id AND consumed_at IS NULL
       AND expires_at>clock_timestamp()
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'unavailable or consumed booking capability' USING ERRCODE='42501'; END IF;
    SELECT * INTO claim FROM public.booking_worker_claims
     WHERE organization_id=cap.organization_id AND message_id=cap.message_id
       AND claim_id=cap.claim_id AND generation=cap.generation
       AND lease_until>clock_timestamp()
     FOR UPDATE;
    IF NOT FOUND OR claim.conversation_id IS DISTINCT FROM NULLIF(cap.payload->>'conversation_id','')::uuid
       OR cap.payload->>'booking_id' IS DISTINCT FROM cap.booking_id::text
       OR cap.payload->>'message_id' IS DISTINCT FROM cap.message_id::text THEN
        RAISE EXCEPTION 'booking capability claim is stale or mismatched' USING ERRCODE='40001';
    END IF;
    UPDATE c24_private.worker_capabilities
       SET consumed_at=clock_timestamp(),consumed_backend_pid=pg_backend_pid(),
           consumed_transaction_id=pg_current_xact_id()
     WHERE capability_id=cap.capability_id AND consumed_at IS NULL;
    IF NOT FOUND THEN RAISE EXCEPTION 'booking capability was concurrently consumed' USING ERRCODE='40001'; END IF;
    INSERT INTO c24_private.worker_context
        (backend_pid,transaction_id,organization_id,message_id,conversation_id,booking_id,
         confirmation_message_id,prompt_message_id,inbox_id,confirmation_inbox_id,operation_id,
         claim_id,generation,capability_id,job_kind,lease_until)
    VALUES(pg_backend_pid(),pg_current_xact_id(),cap.organization_id,cap.message_id,claim.conversation_id,
           NULLIF(cap.payload->>'booking_id','')::uuid,
           NULLIF(cap.payload->>'confirmation_message_id','')::uuid,
           NULLIF(cap.payload->>'prompt_message_id','')::uuid,
           NULLIF(cap.payload->>'inbox_id','')::uuid,
           NULLIF(cap.payload->>'confirmation_inbox_id','')::uuid,
           NULLIF(cap.payload->>'operation_id','')::uuid,
           cap.claim_id,cap.generation,cap.capability_id,
           cap.payload->>'job_kind',claim.lease_until);
    RETURN cap.payload;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_consume_capability(uuid)
    FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_consume_capability(uuid)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows(
    p_organization_id uuid,
    p_effective_role text
) RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker'
       OR p_effective_role IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
        RETURN false;
    END IF;
    RETURN EXISTS (
        SELECT 1
          FROM c24_private.worker_context AS ctx
          JOIN public.booking_worker_claims AS claim
            ON claim.organization_id=ctx.organization_id
           AND claim.message_id=ctx.message_id
           AND claim.claim_id=ctx.claim_id
           AND claim.generation=ctx.generation
           AND claim.lease_until=ctx.lease_until
         WHERE ctx.backend_pid=pg_backend_pid()
           AND ctx.transaction_id=pg_current_xact_id()
           AND ctx.organization_id=p_organization_id
           AND ctx.lease_until>clock_timestamp()
    );
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows(uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_message(
    p_message_id uuid,
    p_effective_role text
) RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
DECLARE message_org uuid;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker'
       OR p_effective_role IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
        RETURN false;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM c24_private.worker_context AS ctx
        JOIN public.booking_worker_claims AS claim
          ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
         AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
         AND claim.lease_until=ctx.lease_until
        WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
          AND p_message_id IN (ctx.message_id,ctx.confirmation_message_id,ctx.prompt_message_id)
          AND ctx.lease_until>clock_timestamp()
    );
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_message(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_message(uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_claim_source(
    p_message_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (SELECT 1 FROM c24_private.worker_context AS ctx
         JOIN public.booking_worker_claims AS claim
           ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
          AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
          AND claim.lease_until=ctx.lease_until
         WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
           AND ctx.message_id=p_message_id AND ctx.lease_until>clock_timestamp())
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_claim_source(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_claim_source(uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_operation(
    p_organization_id uuid,p_operation_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (SELECT 1 FROM c24_private.worker_context AS ctx
         JOIN public.booking_worker_claims AS claim
           ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
          AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
          AND claim.lease_until=ctx.lease_until
         WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
           AND ctx.organization_id=p_organization_id AND ctx.operation_id=p_operation_id
           AND ctx.lease_until>clock_timestamp())
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_operation(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_operation(uuid,uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_simulation_request(
    p_organization_id uuid,p_actor_id uuid,p_request_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
         SELECT 1 FROM public.simulation_requests AS request
         JOIN public.auth_access_lifecycle AS access
           ON access.auth_user_id=request.auth_user_id AND access.disabled_at IS NULL
         JOIN public.user_profiles AS profile
           ON profile.auth_user_id=request.auth_user_id
         JOIN public.organization_memberships AS membership
           ON membership.organization_id=request.organization_id
          AND membership.user_id=profile.id AND membership.joined_at IS NOT NULL
          AND membership.ruolo IN ('owner','manager','staff')
         JOIN c24_private.worker_context AS ctx
           ON ctx.organization_id=request.organization_id
          AND ctx.operation_id=request.request_id
         JOIN public.booking_worker_claims AS claim
           ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
          AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
          AND claim.lease_until=ctx.lease_until
         WHERE request.organization_id=p_organization_id
           AND request.auth_user_id=p_actor_id AND request.request_id=p_request_id
           AND request.status='completed' AND request.response IS NOT NULL
           AND ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
           AND ctx.lease_until>clock_timestamp()
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_simulation_request(uuid,uuid,uuid,text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_simulation_request(uuid,uuid,uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_request(
    p_organization_id uuid,p_source_message_id uuid,p_prompt_message_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (SELECT 1 FROM c24_private.worker_context AS ctx
         JOIN public.booking_worker_claims AS claim
           ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
          AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
          AND claim.lease_until=ctx.lease_until
         WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
           AND ctx.organization_id=p_organization_id
           AND p_source_message_id IN (ctx.message_id,ctx.confirmation_message_id)
           AND (p_prompt_message_id IS NULL OR p_prompt_message_id=ctx.prompt_message_id)
           AND ctx.lease_until>clock_timestamp())
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_request(uuid,uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_request(uuid,uuid,uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_conversation(
    p_organization_id uuid,p_conversation_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
           SELECT 1 FROM c24_private.worker_context AS ctx
           JOIN public.booking_worker_claims AS claim
             ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
            AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
            AND claim.lease_until=ctx.lease_until
           WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
             AND ctx.organization_id=p_organization_id
             AND ctx.conversation_id=p_conversation_id AND ctx.lease_until>clock_timestamp()
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_conversation(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_conversation(uuid,uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_contact(
    p_organization_id uuid,p_contact_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE conversation uuid;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker'
       OR p_effective_role IS DISTINCT FROM 'melpis_c24_booking_worker' THEN RETURN false; END IF;
    SELECT ctx.conversation_id INTO conversation FROM c24_private.worker_context AS ctx
    JOIN public.booking_worker_claims AS claim
      ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
     AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
     AND claim.lease_until=ctx.lease_until
    WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
      AND ctx.organization_id=p_organization_id AND ctx.lease_until>clock_timestamp();
    IF conversation IS NULL THEN RETURN false; END IF;
    RETURN EXISTS(SELECT 1 FROM public.conversations AS c
        WHERE c.organization_id=p_organization_id AND c.id=conversation
          AND c.contact_id=p_contact_id AND c.deleted_at IS NULL);
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_contact(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_contact(uuid,uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_inbox(
    p_organization_id uuid,p_inbox_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
           SELECT 1 FROM c24_private.worker_context AS ctx
           JOIN public.booking_worker_claims AS claim
             ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
            AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
            AND claim.lease_until=ctx.lease_until
           JOIN public.inbound_worker_receipts AS receipt
             ON receipt.organization_id=ctx.organization_id
            AND receipt.message_id IN (ctx.message_id,ctx.confirmation_message_id)
            AND receipt.inbox_id=p_inbox_id
           WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
             AND ctx.organization_id=p_organization_id
             AND p_inbox_id IN (ctx.inbox_id,ctx.confirmation_inbox_id)
             AND ctx.lease_until>clock_timestamp()
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_inbox(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_inbox(uuid,uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_booking(
    p_organization_id uuid,p_booking_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
           SELECT 1 FROM c24_private.worker_context AS ctx
           JOIN public.booking_worker_claims AS claim
             ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
            AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
            AND claim.lease_until=ctx.lease_until
           JOIN public.bookings AS booking
             ON booking.organization_id=ctx.organization_id AND booking.id=p_booking_id
            AND booking.source_message_id=ctx.message_id::text
            AND booking.id_conversazione=ctx.conversation_id::text
           WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
             AND ctx.organization_id=p_organization_id AND ctx.lease_until>clock_timestamp()
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_booking(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_booking(uuid,uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_bound_booking(
    p_organization_id uuid,p_booking_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
           SELECT 1 FROM c24_private.worker_context AS ctx
           JOIN public.booking_worker_claims AS claim
             ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
            AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
            AND claim.lease_until=ctx.lease_until
           JOIN public.bookings AS booking
             ON booking.organization_id=ctx.organization_id AND booking.id=p_booking_id
            AND booking.source_message_id=ctx.message_id::text
            AND booking.id_conversazione=ctx.conversation_id::text
           WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
             AND ctx.organization_id=p_organization_id AND ctx.booking_id=p_booking_id
             AND ctx.lease_until>clock_timestamp()
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_bound_booking(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_bound_booking(uuid,uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_claim_source_allows(
    p_organization_id uuid,p_source_message text,p_conversation text,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
           SELECT 1 FROM c24_private.worker_context AS ctx
           JOIN public.booking_worker_claims AS claim
             ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
            AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
            AND claim.lease_until=ctx.lease_until
           WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
             AND ctx.organization_id=p_organization_id
             AND ctx.message_id::text=p_source_message
             AND ctx.conversation_id::text=p_conversation
             AND ctx.lease_until>clock_timestamp()
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_claim_source_allows(uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_claim_source_allows(uuid,text,text,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_claim_booking_insert_allows(
    p_organization_id uuid,p_booking_id uuid,p_source_message text,
    p_conversation text,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
           SELECT 1 FROM c24_private.worker_context AS ctx
           JOIN public.booking_worker_claims AS claim
             ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
            AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
            AND claim.lease_until=ctx.lease_until
           WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
             AND ctx.organization_id=p_organization_id AND ctx.booking_id=p_booking_id
             AND ctx.message_id::text=p_source_message
             AND ctx.conversation_id::text=p_conversation
             AND ctx.lease_until>clock_timestamp()
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_claim_booking_insert_allows(uuid,uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_claim_booking_insert_allows(uuid,uuid,text,text,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_outbound_booking_message_allows(
    p_organization_id uuid,p_conversation_id uuid,p_idempotency_key text,
    p_recipient text,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
           SELECT 1 FROM c24_private.worker_context AS ctx
           JOIN public.booking_worker_claims AS claim
             ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
            AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
            AND claim.lease_until=ctx.lease_until
           JOIN public.bookings AS booking
             ON booking.organization_id=ctx.organization_id
            AND booking.source_message_id=ctx.message_id::text
            AND booking.id_conversazione=ctx.conversation_id::text
           JOIN public.contacts AS contact
             ON contact.organization_id=booking.organization_id
            AND contact.id=booking.contact_id
           WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
             AND ctx.organization_id=p_organization_id
             AND ctx.booking_id=booking.id
             AND ctx.conversation_id=p_conversation_id
             AND p_recipient IS NOT NULL AND p_recipient=contact.phone_number
             AND p_idempotency_key='booking-confirm:'||ctx.organization_id::text||':'||ctx.message_id::text
             AND ctx.lease_until>clock_timestamp()
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_outbound_booking_message_allows(uuid,uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_outbound_booking_message_allows(uuid,uuid,text,text,text)
    TO melpis_c24_booking_worker;

-- Only relations with a direct organization key are surfaced. The one
-- message-delivery table without such a key is filtered through its message.
DO $$
DECLARE item record; scope_predicate text;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
          ('organizations','id'),
          ('bookings','organization_id'),('booking_stays','organization_id'),
          ('booking_settings','organization_id'),('messages','organization_id'),
          ('conversations','organization_id'),('contacts','organization_id'),
          ('booking_catalog_publications','organization_id'),('booking_catalog_requests','organization_id'),
          ('booking_conversation_states','organization_id'),('booking_conversation_requests','organization_id'),
          ('booking_conversation_revocations','organization_id'),('booking_worker_claims','organization_id'),
          ('inbound_worker_operations','organization_id'),('inbound_worker_receipts','organization_id'),
          ('booking_sector_admission_fences','organization_id'),
          ('booking_sector_admission_epochs','organization_id'),('booking_allocation_sets','organization_id'),
          ('booking_resource_allocations','organization_id'),('booking_allocation_requests','organization_id'),
          ('booking_confirmation_outbox','organization_id'),('booking_stay_purge_fences','organization_id'),
          ('booking_stay_tombstones','organization_id'),('gdpr_erasure_barriers','organization_id'),
          ('gdpr_retention_holds','organization_id'),('usage_events','organization_id'),
          ('c24_idle_watermarks','organization_id'),
          ('c24_source_fences','organization_id')
        ) AS relations(relation_name,scope_column)
    LOOP
        IF to_regclass(format('public.%I',item.relation_name)) IS NULL THEN
            RAISE EXCEPTION 'Required private worker relation is missing: %', item.relation_name;
        END IF;
        scope_predicate := CASE item.relation_name
          WHEN 'messages' THEN
            'c24_private.c24_worker_scope_allows_message(base.id,current_user::text)'
          WHEN 'conversations' THEN
            'c24_private.c24_worker_scope_allows_conversation(base.organization_id,base.id,current_user::text)'
          WHEN 'contacts' THEN
            'c24_private.c24_worker_scope_allows_contact(base.organization_id,base.id,current_user::text)'
          WHEN 'meta_webhook_inbox' THEN
            'c24_private.c24_worker_scope_allows_inbox(base.organization_id,base.id,current_user::text)'
          WHEN 'booking_worker_claims' THEN
            'c24_private.c24_worker_scope_allows_claim_source(base.message_id,current_user::text)'
          WHEN 'inbound_worker_operations' THEN
            'c24_private.c24_worker_scope_allows_operation(base.organization_id,base.operation_id,current_user::text)'
          WHEN 'inbound_worker_receipts' THEN
            'c24_private.c24_worker_scope_allows_message(base.message_id,current_user::text)'
          WHEN 'booking_conversation_states' THEN
            'c24_private.c24_worker_scope_allows_conversation(base.organization_id,base.conversation_id,current_user::text)'
          WHEN 'booking_conversation_requests' THEN
            'c24_private.c24_worker_scope_allows_request(base.organization_id,base.source_message_id,base.prompt_message_id,current_user::text)'
          WHEN 'booking_conversation_revocations' THEN
            'c24_private.c24_worker_scope_allows_conversation(base.organization_id,base.conversation_id,current_user::text)'
          WHEN 'c24_idle_watermarks' THEN
            'c24_private.c24_worker_scope_allows_conversation(base.organization_id,base.conversation_id,current_user::text)'
          WHEN 'c24_source_fences' THEN
            'c24_private.c24_worker_scope_allows_conversation(base.organization_id,base.conversation_id,current_user::text)'
          WHEN 'bookings' THEN
            'c24_private.c24_worker_scope_allows_booking(base.organization_id,base.id,current_user::text)'
          WHEN 'booking_stays' THEN
            'c24_private.c24_worker_scope_allows_bound_booking(base.organization_id,base.booking_id,current_user::text)'
          WHEN 'booking_allocation_sets' THEN
            'c24_private.c24_worker_scope_allows_bound_booking(base.organization_id,base.booking_id,current_user::text)'
          WHEN 'booking_resource_allocations' THEN
            'c24_private.c24_worker_scope_allows_bound_booking(base.organization_id,base.booking_id,current_user::text)'
          WHEN 'booking_allocation_requests' THEN
            'c24_private.c24_worker_scope_allows_bound_booking(base.organization_id,base.booking_id,current_user::text)'
          WHEN 'booking_confirmation_outbox' THEN
            'c24_private.c24_worker_scope_allows_bound_booking(base.organization_id,base.booking_id,current_user::text)'
          WHEN 'booking_stay_purge_fences' THEN
            'c24_private.c24_worker_scope_allows_bound_booking(base.organization_id,base.booking_id,current_user::text)'
          WHEN 'booking_stay_tombstones' THEN
            'c24_private.c24_worker_scope_allows_bound_booking(base.organization_id,base.booking_id,current_user::text)'
          WHEN 'usage_events' THEN
            'c24_private.c24_worker_scope_allows_operation(base.organization_id,base.id,current_user::text)'
          ELSE format('c24_private.c24_worker_scope_allows(base.%I,current_user::text)',item.scope_column)
        END;
        EXECUTE format(
          'CREATE OR REPLACE VIEW c24_private.%I WITH (security_barrier=true) AS '
          'SELECT base.* FROM public.%I AS base '
          'WHERE %s '
          'WITH CASCADED CHECK OPTION',
          item.relation_name,item.relation_name,scope_predicate
        );
        EXECUTE format('REVOKE ALL ON c24_private.%I FROM PUBLIC',item.relation_name);
        EXECUTE format('GRANT SELECT ON c24_private.%I TO melpis_c24_booking_worker',item.relation_name);
    END LOOP;

    IF to_regclass('public.message_delivery_attempts') IS NULL THEN
        RAISE EXCEPTION 'Required private worker relation is missing: message_delivery_attempts';
    END IF;
    EXECUTE 'CREATE OR REPLACE VIEW c24_private.message_delivery_attempts '
      'WITH (security_barrier=true) AS '
      'SELECT d.* FROM public.message_delivery_attempts AS d '
      'WHERE c24_private.c24_worker_scope_allows_message(d.message_id,current_user::text) '
      'WITH CASCADED CHECK OPTION';
    REVOKE ALL ON c24_private.message_delivery_attempts FROM PUBLIC;
    GRANT SELECT ON c24_private.message_delivery_attempts TO melpis_c24_booking_worker;

    -- Ingress payloads contain customer content and provider data. The worker
    -- only needs the identity and terminal channel/status proof for its claim.
    IF to_regclass('public.meta_webhook_inbox') IS NULL THEN
        RAISE EXCEPTION 'Required private worker relation is missing: meta_webhook_inbox';
    END IF;
    DROP VIEW IF EXISTS c24_private.meta_webhook_inbox;
    EXECUTE 'CREATE VIEW c24_private.meta_webhook_inbox WITH (security_barrier=true) AS '
      'SELECT i.id,i.organization_id,i.channel,i.status FROM public.meta_webhook_inbox AS i '
      'WHERE c24_private.c24_worker_scope_allows_inbox(i.organization_id,i.id,current_user::text) '
      'WITH CASCADED CHECK OPTION';
    REVOKE ALL ON c24_private.meta_webhook_inbox FROM PUBLIC,anon,authenticated,service_role;
    GRANT SELECT ON c24_private.meta_webhook_inbox TO melpis_c24_booking_worker;

    -- A04 canonical-response proof needs only the durable receipt columns.
    -- Keep the operation ID, fixed inbound actor, tenant, backend/transaction,
    -- claim generation and live lease bound by the scope helper.
    IF to_regclass('public.simulation_requests') IS NULL THEN
        RAISE EXCEPTION 'Required private worker relation is missing: simulation_requests';
    END IF;
    DROP VIEW IF EXISTS c24_private.simulation_requests;
    EXECUTE 'CREATE VIEW c24_private.simulation_requests WITH (security_barrier=true) AS '
      'SELECT s.organization_id,s.auth_user_id,s.request_id,s.payload_hash,s.claim_token,s.status,s.response '
      'FROM public.simulation_requests AS s '
      'WHERE c24_private.c24_worker_scope_allows_simulation_request('
      's.organization_id,s.auth_user_id,s.request_id,current_user::text) '
      'WITH CASCADED CHECK OPTION';
    REVOKE ALL ON c24_private.simulation_requests FROM PUBLIC,anon,authenticated,service_role;
    GRANT SELECT ON c24_private.simulation_requests TO melpis_c24_booking_worker;
END;
$$;

-- Capacity/fingerprint checks need same-tenant occupancy but never names,
-- phones, notes, customer text, or arbitrary booking rows.
CREATE OR REPLACE VIEW c24_private.booking_availability WITH (security_barrier=true) AS
SELECT b.organization_id,b.id,b.data,b.ora,b.coperti,b.stato,b.contact_id,
       b.id_conversazione,b.calendar_revision,b.completata_at,b.no_show_at
  FROM public.bookings AS b
 WHERE c24_private.c24_worker_scope_allows(b.organization_id,current_user::text);
REVOKE ALL ON c24_private.booking_availability FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON c24_private.booking_availability TO melpis_c24_booking_worker;

-- Conflict checks must see every active reservation in this tenant, not only
-- the booking currently bound to the worker claim.  This view exposes just the
-- fields needed for deterministic overlap/fingerprint checks and is still
-- fenced to the current tenant capability.
CREATE OR REPLACE VIEW c24_private.booking_resource_availability
WITH (security_barrier=true) AS
SELECT a.organization_id,a.booking_id,a.resource_id,a.resource_kind,a.exclusive,
       a.quantity,a.starts_at,a.ends_at,s.booking_fingerprint,
       b.data,b.ora,b.coperti,b.stato,b.contact_id,b.id_conversazione,
       b.calendar_revision,b.completata_at,b.no_show_at
  FROM public.booking_resource_allocations AS a
  JOIN public.booking_allocation_sets AS s
    ON s.organization_id=a.organization_id AND s.booking_id=a.booking_id
  JOIN public.bookings AS b
    ON b.organization_id=a.organization_id AND b.id=a.booking_id
 WHERE c24_private.c24_worker_scope_allows(a.organization_id,current_user::text)
   AND c24_private.c24_worker_scope_allows(b.organization_id,current_user::text);
REVOKE ALL ON c24_private.booking_resource_availability
    FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON c24_private.booking_resource_availability
    TO melpis_c24_booking_worker;

-- Separate write views bind each permitted side effect to the active claim.
CREATE OR REPLACE VIEW c24_private.worker_booking_inserts WITH (security_barrier=true) AS
SELECT b.* FROM public.bookings AS b
WHERE c24_private.c24_worker_claim_booking_insert_allows(
    b.organization_id,b.id,b.source_message_id,b.id_conversazione,current_user::text)
WITH CASCADED CHECK OPTION;
CREATE OR REPLACE VIEW c24_private.worker_booking_stays_inserts WITH (security_barrier=true) AS
SELECT s.* FROM public.booking_stays AS s
WHERE c24_private.c24_worker_claim_source_allows(
        s.organization_id,s.source_message_id::text,s.original_conversation_id::text,current_user::text)
  AND c24_private.c24_worker_scope_allows_bound_booking(
        s.organization_id,s.booking_id,current_user::text)
WITH CASCADED CHECK OPTION;
CREATE OR REPLACE VIEW c24_private.worker_allocation_sets WITH (security_barrier=true) AS
SELECT a.* FROM public.booking_allocation_sets AS a
WHERE c24_private.c24_worker_scope_allows_bound_booking(
    a.organization_id,a.booking_id,current_user::text)
WITH CASCADED CHECK OPTION;
CREATE OR REPLACE VIEW c24_private.worker_resource_allocations WITH (security_barrier=true) AS
SELECT a.* FROM public.booking_resource_allocations AS a
WHERE c24_private.c24_worker_scope_allows_bound_booking(
    a.organization_id,a.booking_id,current_user::text)
WITH CASCADED CHECK OPTION;
CREATE OR REPLACE VIEW c24_private.worker_allocation_requests WITH (security_barrier=true) AS
SELECT r.* FROM public.booking_allocation_requests AS r
WHERE c24_private.c24_worker_scope_allows_bound_booking(
        r.organization_id,r.booking_id,current_user::text)
  AND c24_private.c24_worker_scope_allows_message(
        r.service_message_id,current_user::text)
WITH CASCADED CHECK OPTION;
CREATE OR REPLACE VIEW c24_private.worker_confirmation_outbox WITH (security_barrier=true) AS
SELECT o.* FROM public.booking_confirmation_outbox AS o
WHERE c24_private.c24_worker_scope_allows_bound_booking(
        o.organization_id,o.booking_id,current_user::text)
  AND c24_private.c24_worker_scope_allows_message(
        o.inbound_message_id,current_user::text)
WITH CASCADED CHECK OPTION;
CREATE OR REPLACE VIEW c24_private.worker_outbound_messages WITH (security_barrier=true) AS
SELECT m.* FROM public.messages AS m
WHERE m.direction='outbound' AND m.handling_type='booking_confirmation'
  AND c24_private.c24_worker_outbound_booking_message_allows(
      m.organization_id,m.conversation_id,m.idempotency_key,m.content->>'to',current_user::text)
WITH CASCADED CHECK OPTION;

DO $$
BEGIN
    REVOKE ALL ON c24_private.worker_booking_inserts,
        c24_private.worker_booking_stays_inserts,c24_private.worker_allocation_sets,
        c24_private.worker_resource_allocations,c24_private.worker_allocation_requests,
        c24_private.worker_confirmation_outbox,c24_private.worker_outbound_messages
        FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
    -- Direct row writes remain disabled until the application-minted, exact
    -- payload capability is complete and verified across all side effects.
    -- Do not treat a job claim alone as authorization to create a booking.
END;
$$;

-- PostgreSQL requires UPDATE privilege for row-lock clauses. Grant only the
-- historical lock columns on read views, and deny direct worker UPDATEs in a
-- base-table BEFORE trigger. SECURITY DEFINER lifecycle functions can still
-- atomically maintain their protected claim rows. Actual DML is granted only
-- on the write views.
CREATE OR REPLACE FUNCTION public.c24_private_worker_reject_update()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
    IF session_user='melpis_c24_booking_worker'
       AND current_user='melpis_c24_booking_worker' THEN
        RAISE EXCEPTION 'private booking worker cannot update protected rows' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.c24_private_worker_reject_update()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

DO $$
DECLARE rel text;
BEGIN
    FOREACH rel IN ARRAY ARRAY[
      'organizations','messages','conversations','contacts','bookings','booking_settings',
      'inbound_worker_receipts','inbound_worker_operations','meta_webhook_inbox','usage_events',
      'simulation_requests','booking_conversation_states','booking_conversation_requests','booking_stays',
      'booking_worker_claims'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS c24_private_worker_readonly_update ON public.%I',rel);
        EXECUTE format('CREATE TRIGGER c24_private_worker_readonly_update BEFORE UPDATE ON public.%I '
          'FOR EACH ROW EXECUTE FUNCTION public.c24_private_worker_reject_update()',rel);
    END LOOP;
END;
$$;

GRANT UPDATE (id) ON c24_private.organizations,c24_private.messages,
    c24_private.conversations,c24_private.contacts,c24_private.bookings
    TO melpis_c24_booking_worker;
GRANT UPDATE (organization_id) ON c24_private.booking_settings,
    c24_private.inbound_worker_receipts,c24_private.inbound_worker_operations,
    c24_private.meta_webhook_inbox TO melpis_c24_booking_worker;
GRANT UPDATE (id) ON c24_private.usage_events TO melpis_c24_booking_worker;
GRANT UPDATE (claim_id) ON c24_private.booking_worker_claims TO melpis_c24_booking_worker;
GRANT UPDATE (request_id) ON c24_private.simulation_requests TO melpis_c24_booking_worker;
GRANT UPDATE (organization_id,conversation_id) ON c24_private.booking_conversation_states,
    c24_private.booking_conversation_requests TO melpis_c24_booking_worker;
GRANT UPDATE (revision) ON c24_private.booking_stays TO melpis_c24_booking_worker;

-- Readiness returns only the database identity marker and OID, not tenant data.
CREATE OR REPLACE FUNCTION c24_private.c24_worker_readiness()
RETURNS TABLE(identity_id uuid,database_oid oid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
        RAISE EXCEPTION 'dedicated booking worker required' USING ERRCODE='42501';
    END IF;
    RETURN QUERY SELECT i.identity_id,d.oid
      FROM public.c24_database_instance_identity AS i
      JOIN pg_database AS d ON d.datname=current_database()
     WHERE i.singleton=true;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_readiness() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_readiness() TO melpis_c24_booking_worker;

-- Polling is bounded and returns only source-message UUIDs. Python continues
-- to perform the existing canonical proof and allocation calculations.
CREATE OR REPLACE FUNCTION c24_private.c24_worker_next_jobs(
    p_after_message uuid,
    p_limit integer
) RETURNS TABLE(job_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
        RAISE EXCEPTION 'dedicated booking worker required' USING ERRCODE='42501';
    END IF;
    IF p_limit IS NULL OR p_limit<1 OR p_limit>100 THEN
        RAISE EXCEPTION 'invalid booking worker batch size' USING ERRCODE='22023';
    END IF;
    RETURN QUERY
    SELECT s.source_message_id
      FROM public.booking_conversation_states AS s
      JOIN public.booking_conversation_requests AS p
        ON p.organization_id=s.organization_id
       AND p.conversation_id=s.conversation_id
       AND p.state_generation=s.state_generation
       AND p.action='proposal'
       AND p.applied_revision=s.revision-1
      JOIN public.messages AS m
        ON m.organization_id=s.organization_id
       AND m.id=s.source_message_id
       AND m.direction='inbound'
       AND m.deleted_at IS NULL
      JOIN public.inbound_worker_receipts AS r
        ON r.organization_id=s.organization_id
       AND r.message_id=s.source_message_id
      WHERE s.snapshot->>'intent'='booking'
        AND jsonb_typeof(s.snapshot->'confirmation')='object'
        AND (p_after_message IS NULL OR s.source_message_id>p_after_message)
        AND NOT EXISTS (
            SELECT 1 FROM public.bookings AS b
             WHERE b.organization_id=s.organization_id
               AND b.source_message_id=p.source_message_id::text
        )
      ORDER BY s.source_message_id
      LIMIT p_limit;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_next_jobs(uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_next_jobs(uuid,integer)
    TO melpis_c24_booking_worker;

-- Claim acquisition and capability creation are one transaction. The input
-- is only a persisted job selector; organization, conversation, proposal
-- source, claim source, and A04 provenance are all derived from the database.
DROP FUNCTION IF EXISTS c24_private.c24_worker_begin_job(uuid);
DROP FUNCTION IF EXISTS c24_private.c24_worker_begin_job(uuid,text);
CREATE OR REPLACE FUNCTION c24_private.c24_worker_begin_job(
    p_message_id uuid,
    p_job_kind text DEFAULT 'confirmation'
)
RETURNS TABLE(
    organization_id uuid,
    conversation_id uuid,
    claim_id uuid,
    generation bigint,
    lease_until timestamptz,
    status text,
    booking_id uuid,
    claim_message_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
DECLARE
    v_org uuid;
    v_conversation uuid;
    v_claim_message uuid;
    v_operation uuid;
    v_inbox uuid;
    v_confirmation_message uuid;
    v_prompt_message uuid;
    v_confirmation_inbox uuid;
    v_claim uuid := gen_random_uuid();
    v_generation bigint;
    v_claim_started timestamptz := clock_timestamp();
    v_lease timestamptz := v_claim_started+interval '30 seconds';
    v_max_lease timestamptz := v_claim_started+interval '5 minutes';
    v_existing uuid;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
        RAISE EXCEPTION 'dedicated booking worker required' USING ERRCODE='42501';
    END IF;
    IF p_message_id IS NULL THEN
        RAISE EXCEPTION 'invalid booking job' USING ERRCODE='22023';
    END IF;

    IF p_message_id IS NULL THEN
        RAISE EXCEPTION 'invalid booking job' USING ERRCODE='22023';
    END IF;
    IF p_job_kind IS NULL OR p_job_kind NOT IN ('confirmation','a05') THEN
        RAISE EXCEPTION 'invalid booking job kind' USING ERRCODE='22023';
    END IF;

    -- A05 is the immediate post-A04 path. The exact A04 proposal must already
    -- have been applied to the current structured state by the inbound worker.
    IF p_job_kind='a05' THEN
        SELECT s.organization_id,s.conversation_id,p.source_message_id,p.service_operation_id
          INTO v_org,v_conversation,v_claim_message,v_operation
          FROM public.booking_conversation_states AS s
          JOIN public.booking_conversation_requests AS p
            ON p.organization_id=s.organization_id AND p.conversation_id=s.conversation_id
           AND p.state_generation=s.state_generation AND p.action='proposal'
           AND p.applied_revision=s.revision AND p.source_message_id=p_message_id
          JOIN public.messages AS m
            ON m.organization_id=p.organization_id AND m.conversation_id=p.conversation_id
           AND m.id=p.source_message_id AND m.direction='inbound' AND m.deleted_at IS NULL
          JOIN public.conversations AS c
            ON c.organization_id=s.organization_id AND c.id=s.conversation_id
           AND c.contact_id=s.subject_id AND c.deleted_at IS NULL AND c.canale='whatsapp'
           AND c.status='active' AND c.ticket_status='AI_ACTIVE'
         WHERE s.source_message_id=p_message_id AND s.snapshot->>'intent'='booking'
           AND p.actor_kind='inbound_worker' AND p.service_operation_id IS NOT NULL
           AND p.canonical_response_hash IS NOT NULL
           AND m.c24_original_subject_id=c.contact_id
         LIMIT 1;
        IF v_org IS NOT NULL THEN v_confirmation_message := p_message_id; END IF;
    END IF;

    IF v_org IS NULL AND p_job_kind='confirmation' THEN
        -- A customer confirmation is a different message from the A04
        -- proposal. Validate both current state and immutable request rows,
        -- then derive the original proposal source to claim and account.
        SELECT s.organization_id,s.conversation_id,p.source_message_id,p.service_operation_id,
               confirmation.source_message_id,confirmation.prompt_message_id,confirmation_receipt.inbox_id
          INTO v_org,v_conversation,v_claim_message,v_operation,
               v_confirmation_message,v_prompt_message,v_confirmation_inbox
          FROM public.booking_conversation_states AS s
          JOIN public.booking_conversation_requests AS confirmation
            ON confirmation.organization_id=s.organization_id
           AND confirmation.conversation_id=s.conversation_id
           AND confirmation.state_generation=s.state_generation
           AND confirmation.source_message_id=p_message_id
           AND confirmation.action='confirmation'
           AND confirmation.applied_revision=s.revision
           AND confirmation.actor_kind='inbound_worker'
           AND confirmation.service_operation_id IS NULL
          JOIN public.inbound_worker_receipts AS confirmation_receipt
            ON confirmation_receipt.organization_id=confirmation.organization_id
           AND confirmation_receipt.inbox_id=confirmation.service_inbox_id
           AND confirmation_receipt.message_id=confirmation.source_message_id
          JOIN public.meta_webhook_inbox AS confirmation_inbox
            ON confirmation_inbox.organization_id=confirmation_receipt.organization_id
           AND confirmation_inbox.id=confirmation_receipt.inbox_id
           AND confirmation_inbox.status='completed' AND confirmation_inbox.channel='whatsapp'
          JOIN public.booking_conversation_requests AS p
            ON p.organization_id=s.organization_id AND p.conversation_id=s.conversation_id
           AND p.state_generation=s.state_generation AND p.action='proposal'
           AND p.applied_revision=s.revision-1
          JOIN public.messages AS source
            ON source.organization_id=p.organization_id AND source.conversation_id=p.conversation_id
           AND source.id=p.source_message_id AND source.direction='inbound' AND source.deleted_at IS NULL
          JOIN public.conversations AS c
            ON c.organization_id=s.organization_id AND c.id=s.conversation_id
           AND c.contact_id=s.subject_id AND c.deleted_at IS NULL AND c.canale='whatsapp'
           AND c.status='active' AND c.ticket_status='AI_ACTIVE'
         WHERE s.source_message_id=p_message_id AND s.snapshot->>'intent'='booking'
           AND jsonb_typeof(s.snapshot->'confirmation')='object'
           AND p.actor_kind='inbound_worker' AND p.service_operation_id IS NOT NULL
           AND p.canonical_response_hash IS NOT NULL
           AND source.c24_original_subject_id=c.contact_id
         LIMIT 1;
    END IF;

    IF v_org IS NULL OR v_claim_message IS NULL OR v_operation IS NULL THEN
        RAISE EXCEPTION 'persisted booking proposal and confirmation state required' USING ERRCODE='42501';
    END IF;

    -- A04 is attributed to the original AI proposal, never to a later
    -- confirmation message. All identifiers come from the persisted receipt.
    SELECT o.inbox_id INTO v_inbox
      FROM public.inbound_worker_operations AS o
      JOIN public.inbound_worker_receipts AS r
        ON r.organization_id=o.organization_id AND r.inbox_id=o.inbox_id
       AND r.message_id=o.message_id
      JOIN public.meta_webhook_inbox AS i
        ON i.organization_id=r.organization_id AND i.id=r.inbox_id
      JOIN public.usage_events AS u
        ON u.organization_id=o.organization_id AND u.id=o.operation_id
     WHERE o.organization_id=v_org AND o.message_id=v_claim_message
       AND o.operation_id=v_operation AND i.status='completed' AND i.channel='whatsapp'
       AND u.event_type='system.quota_reservation' AND u.quantity=0
       AND u.metadata->>'channel'='inbound' AND u.metadata->>'state'='committed';
    IF NOT FOUND OR v_inbox IS NULL THEN
        RAISE EXCEPTION 'committed A04 and inbound provenance required' USING ERRCODE='42501';
    END IF;

    -- A retry is complete only when all durable booking effects agree.
    SELECT b.id INTO v_existing
      FROM public.bookings AS b
      JOIN public.booking_allocation_requests AS r
        ON r.organization_id=b.organization_id AND r.booking_id=b.id
      JOIN public.booking_allocation_sets AS a
        ON a.organization_id=b.organization_id AND a.booking_id=b.id
      JOIN public.booking_confirmation_outbox AS o
        ON o.organization_id=b.organization_id AND o.booking_id=b.id
       AND o.booking_revision=b.calendar_revision
     WHERE b.organization_id=v_org AND b.source_message_id=v_claim_message::text
       AND b.stato='confermata'
       AND r.actor_kind='inbound_worker' AND r.action='allocate' AND r.outcome='allocated'
       AND a.state='active' AND a.booking_calendar_revision=b.calendar_revision
       AND EXISTS (SELECT 1 FROM public.booking_resource_allocations AS x
                    WHERE x.organization_id=v_org AND x.booking_id=b.id)
     LIMIT 1;
    IF v_existing IS NOT NULL THEN
        RETURN QUERY SELECT v_org,v_conversation,NULL::uuid,0::bigint,NULL::timestamptz,
            'completed',v_existing,v_claim_message;
        RETURN;
    END IF;

    DELETE FROM c24_private.worker_context
     WHERE created_at<clock_timestamp()-interval '1 hour';

    INSERT INTO public.booking_worker_claims
        (organization_id,message_id,conversation_id,claim_id,generation,lease_until,claim_started_at,max_lease_until)
    VALUES(v_org,v_claim_message,v_conversation,v_claim,1,v_lease,v_claim_started,v_max_lease)
    ON CONFLICT ON CONSTRAINT booking_worker_claims_pkey DO UPDATE SET
        claim_id=EXCLUDED.claim_id,
        generation=public.booking_worker_claims.generation+1,
        lease_until=EXCLUDED.lease_until,
        claim_started_at=EXCLUDED.claim_started_at,
        max_lease_until=EXCLUDED.max_lease_until
    WHERE public.booking_worker_claims.lease_until<=clock_timestamp()
    RETURNING public.booking_worker_claims.generation INTO v_generation;

    IF v_generation IS NULL THEN
        RETURN QUERY SELECT v_org,v_conversation,NULL::uuid,0::bigint,NULL::timestamptz,
            'busy',NULL::uuid,v_claim_message;
        RETURN;
    END IF;

    -- Claim acquisition alone grants no private relation access. The trusted
    -- application must revalidate the exact persisted A04/booking evidence and
    -- issue a one-use capability; the worker consumes that token in its own
    -- transaction to create the fenced context.
    RETURN QUERY SELECT v_org,v_conversation,v_claim,v_generation,v_lease,
        'claimed',NULL::uuid,v_claim_message;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_begin_job(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_begin_job(uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_renew_job(
    p_organization_id uuid,p_message_id uuid,p_claim_id uuid,
    p_generation bigint,p_lease_seconds integer
) RETURNS timestamptz
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE ctx c24_private.worker_context%ROWTYPE; org uuid; new_lease timestamptz;
        active_capability boolean := false;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker'
       OR p_organization_id IS NULL OR p_message_id IS NULL OR p_claim_id IS NULL
       OR p_generation IS NULL OR p_generation<1
       OR p_lease_seconds IS NULL OR p_lease_seconds<1 OR p_lease_seconds>300 THEN
        RAISE EXCEPTION 'invalid private booking heartbeat' USING ERRCODE='42501';
    END IF;
    SELECT * INTO ctx FROM c24_private.worker_context AS context
     WHERE context.backend_pid=pg_backend_pid()
       AND context.transaction_id=pg_current_xact_id()
       AND context.organization_id=p_organization_id
       AND context.message_id=p_message_id AND context.claim_id=p_claim_id
       AND context.generation=p_generation
     FOR UPDATE;
    IF FOUND THEN
        org:=p_organization_id;
        UPDATE public.booking_worker_claims AS claim
           SET lease_until=LEAST(clock_timestamp()+make_interval(secs=>p_lease_seconds),claim.max_lease_until)
         WHERE claim.organization_id=org AND claim.message_id=p_message_id
           AND claim.claim_id=p_claim_id AND claim.generation=p_generation
           AND claim.lease_until>clock_timestamp() AND claim.max_lease_until>clock_timestamp()
        RETURNING claim.lease_until INTO new_lease;
        IF NOT FOUND OR new_lease<=clock_timestamp() THEN
            RAISE EXCEPTION 'private booking claim lost or reached its maximum lifetime' USING ERRCODE='40001';
        END IF;
        UPDATE c24_private.worker_context SET lease_until=new_lease
         WHERE backend_pid=pg_backend_pid() AND transaction_id=pg_current_xact_id()
           AND capability_id=ctx.capability_id;
        UPDATE c24_private.worker_capabilities
           SET expires_at=LEAST(new_lease,clock_timestamp()+interval '5 minutes')
         WHERE capability_id=ctx.capability_id
           AND consumed_backend_pid=pg_backend_pid()
           AND consumed_transaction_id=pg_current_xact_id()
           AND consumed_at IS NOT NULL AND completed_at IS NULL
           AND expires_at>clock_timestamp();
        IF NOT FOUND THEN RAISE EXCEPTION 'consumed booking capability expired' USING ERRCODE='40001'; END IF;
        RETURN new_lease;
    END IF;

    UPDATE public.booking_worker_claims AS claim
       SET lease_until=LEAST(clock_timestamp()+make_interval(secs=>p_lease_seconds),claim.max_lease_until)
     WHERE claim.organization_id=p_organization_id AND claim.message_id=p_message_id AND claim.claim_id=p_claim_id
       AND claim.generation=p_generation AND claim.lease_until>clock_timestamp()
       AND claim.max_lease_until>clock_timestamp()
    RETURNING claim.organization_id,claim.lease_until INTO org,new_lease;
    IF NOT FOUND OR new_lease<=clock_timestamp() THEN
        RAISE EXCEPTION 'private booking claim lost or reached its maximum lifetime' USING ERRCODE='40001';
    END IF;
    UPDATE c24_private.worker_capabilities
       SET expires_at=LEAST(new_lease,clock_timestamp()+interval '5 minutes')
     WHERE organization_id=org AND message_id=p_message_id AND claim_id=p_claim_id
       AND generation=p_generation AND consumed_at IS NULL AND completed_at IS NULL
       AND expires_at>clock_timestamp();
    RETURN new_lease;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_renew_job(uuid,uuid,uuid,bigint,integer)
    FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_renew_job(uuid,uuid,uuid,bigint,integer)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_finish_job()
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
        RAISE EXCEPTION 'dedicated booking worker required' USING ERRCODE='42501';
    END IF;
    DELETE FROM c24_private.worker_context
     WHERE backend_pid=pg_backend_pid()
       AND transaction_id=pg_current_xact_id();
    RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_finish_job() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_finish_job()
    TO melpis_c24_booking_worker;

-- Internal effects stay behind transaction- and identity-checked wrappers.
CREATE OR REPLACE FUNCTION c24_private.c24_worker_mark_sector_insert(
    p_organization_id uuid,p_booking_id uuid
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
DECLARE bound_booking uuid;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker'
       OR NOT c24_private.c24_worker_scope_allows(p_organization_id,'melpis_c24_booking_worker') THEN
        RAISE EXCEPTION 'scoped booking worker required' USING ERRCODE='42501';
    END IF;
    SELECT ctx.booking_id INTO bound_booking FROM c24_private.worker_context AS ctx
     WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
       AND ctx.organization_id=p_organization_id AND ctx.lease_until>clock_timestamp()
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'booking claim context unavailable' USING ERRCODE='42501'; END IF;
    IF bound_booking IS NOT NULL AND bound_booking<>p_booking_id THEN
        RAISE EXCEPTION 'claim already bound to another booking' USING ERRCODE='42501';
    END IF;
    IF bound_booking IS NULL THEN
        UPDATE c24_private.worker_context SET booking_id=p_booking_id
         WHERE backend_pid=pg_backend_pid() AND transaction_id=pg_current_xact_id()
           AND organization_id=p_organization_id AND booking_id IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'booking claim binding lost' USING ERRCODE='40001'; END IF;
    END IF;
    -- Capability consumption pre-binds booking_id. The one-use admission
    -- marker is still required before an INSERT under the sector fence.
    IF EXISTS(SELECT 1 FROM public.booking_sector_admission_fences
              WHERE organization_id=p_organization_id) THEN
        PERFORM public.booking_sector_mark_worker_insert(p_organization_id,p_booking_id);
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_mark_sector_insert(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_mark_sector_insert(uuid,uuid)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_attach_calendar_source(
    p_organization_id uuid,p_booking_id uuid,p_source_message_id uuid
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker'
       OR NOT c24_private.c24_worker_scope_allows_bound_booking(
           p_organization_id,p_booking_id,'melpis_c24_booking_worker')
       OR NOT c24_private.c24_worker_scope_allows_message(
           p_source_message_id,'melpis_c24_booking_worker') THEN
        RAISE EXCEPTION 'scoped booking worker required' USING ERRCODE='42501';
    END IF;
    RETURN public.c24_attach_calendar_inbound_source(
        p_organization_id,p_booking_id,p_source_message_id);
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_attach_calendar_source(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_attach_calendar_source(uuid,uuid,uuid)
    TO melpis_c24_booking_worker;

-- 092 grants its narrow legacy function to the worker; remove that direct
-- call path. Only the scoped wrapper above remains executable by the worker.
REVOKE ALL ON FUNCTION public.c24_attach_calendar_inbound_source(uuid,uuid,uuid)
    FROM melpis_c24_booking_worker;
REVOKE ALL ON FUNCTION public.booking_sector_mark_worker_insert(uuid,uuid)
    FROM melpis_c24_booking_worker;

-- Three invoker trigger helpers read tenant tables before view checks can
-- complete. Keep their validation logic intact but run it as the trusted
-- lifecycle owner. The dedicated worker must prove its live transaction
-- capability before any helper reads a tenant row.
CREATE OR REPLACE FUNCTION public.booking_allocation_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
DECLARE direct_subject uuid; conversation_subject uuid; subject uuid;
        conversation_text text; conversation_uuid uuid;
BEGIN
    IF session_user='melpis_c24_booking_worker'
       AND NOT c24_private.c24_worker_scope_allows(
           NEW.organization_id,'melpis_c24_booking_worker') THEN
        RAISE EXCEPTION 'scoped booking worker required' USING ERRCODE='42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(NEW.organization_id::text,606));
    IF EXISTS(SELECT 1 FROM public.booking_stay_purge_fences
              WHERE organization_id=NEW.organization_id AND booking_id=NEW.booking_id) THEN
        RAISE EXCEPTION 'Purged booking cannot be recreated' USING ERRCODE='55000';
    END IF;
    IF TG_TABLE_NAME='booking_allocation_requests' THEN
        IF EXISTS(SELECT 1 FROM public.booking_stay_tombstones
                  WHERE organization_id=NEW.organization_id AND request_id=NEW.request_id) THEN
            RAISE EXCEPTION 'Purged request cannot be recreated' USING ERRCODE='55000';
        END IF;
    END IF;
    SELECT b.contact_id,b.id_conversazione
      INTO direct_subject,conversation_text
      FROM public.bookings AS b
     WHERE b.organization_id=NEW.organization_id AND b.id=NEW.booking_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'Booking scope invalid' USING ERRCODE='55000'; END IF;
    IF conversation_text IS NOT NULL AND conversation_text<>'' THEN
        BEGIN conversation_uuid := conversation_text::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'Invalid conversation lineage' USING ERRCODE='55000';
        END;
        SELECT c.contact_id INTO conversation_subject
          FROM public.conversations AS c
         WHERE c.organization_id=NEW.organization_id AND c.id=conversation_uuid
           AND c.deleted_at IS NULL;
        IF NOT FOUND OR conversation_subject IS NULL THEN
            RAISE EXCEPTION 'Invalid conversation lineage' USING ERRCODE='55000';
        END IF;
    END IF;
    IF direct_subject IS NOT NULL AND conversation_subject IS NOT NULL
       AND direct_subject<>conversation_subject THEN
        RAISE EXCEPTION 'Contradictory lineage' USING ERRCODE='55000';
    END IF;
    subject := COALESCE(direct_subject,conversation_subject);
    IF subject IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.contacts AS c
         WHERE c.organization_id=NEW.organization_id AND c.id=subject AND c.deleted_at IS NULL
    ) THEN RAISE EXCEPTION 'Invalid subject lineage' USING ERRCODE='55000'; END IF;
    IF EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers AS b
              WHERE b.organization_id=NEW.organization_id
                AND (b.scope='organization' OR b.subject_id=subject OR subject IS NULL)) THEN
        RAISE EXCEPTION 'GDPR barrier rejects allocation' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.booking_allocation_guard()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION public.calendar_track_booking() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
DECLARE mode text; link record; action text; verified boolean := false;
BEGIN
    IF session_user='melpis_c24_booking_worker'
       AND NOT c24_private.c24_worker_scope_allows(
           NEW.organization_id,'melpis_c24_booking_worker') THEN
        RAISE EXCEPTION 'scoped booking worker required' USING ERRCODE='42501';
    END IF;
    SELECT s.config#>>'{a05,authority}' INTO mode
      FROM public.booking_settings AS s WHERE s.organization_id=NEW.organization_id;
    IF mode IS DISTINCT FROM 'google_authoritative' THEN NEW.calendar_confirm_pending := false; END IF;
    IF TG_OP='UPDATE' THEN
        IF ROW(NEW.data,NEW.ora,NEW.coperti,NEW.nome_cliente,NEW.telefono,NEW.note)
           IS DISTINCT FROM ROW(OLD.data,OLD.ora,OLD.coperti,OLD.nome_cliente,OLD.telefono,OLD.note)
           OR NEW.stato IN ('cancellata','cancellato','rifiutata','no_show') THEN
            NEW.calendar_confirm_pending := false;
        END IF;
        IF ROW(NEW.data,NEW.ora,NEW.coperti,NEW.nome_cliente,NEW.telefono,NEW.note,
               NEW.stato,NEW.calendar_confirm_pending)
           IS NOT DISTINCT FROM ROW(OLD.data,OLD.ora,OLD.coperti,OLD.nome_cliente,OLD.telefono,OLD.note,
                                    OLD.stato,OLD.calendar_confirm_pending) THEN RETURN NEW; END IF;
        IF mode='google_authoritative' AND NEW.stato='confermata' AND OLD.calendar_confirm_pending
           AND NOT NEW.calendar_confirm_pending
           AND ROW(NEW.data,NEW.ora,NEW.coperti,NEW.nome_cliente,NEW.telefono,NEW.note)
               IS NOT DISTINCT FROM ROW(OLD.data,OLD.ora,OLD.coperti,OLD.nome_cliente,OLD.telefono,OLD.note) THEN
            SELECT EXISTS(
                SELECT 1 FROM public.external_booking_sync AS s
                JOIN public.google_calendar_credentials AS c ON c.organization_id=s.organization_id
                WHERE s.organization_id=NEW.organization_id AND s.internal_booking_id=NEW.id
                  AND s.provider='google' AND s.sync_status='succeeded'
                  AND s.calendar_applied_revision=OLD.calendar_revision
                  AND s.calendar_connection_epoch=c.connection_epoch
                  AND c.connection_status='connected' AND c.sync_enabled
                  AND s.calendar_dispatch_state='verified' AND s.calendar_authoritative
                  AND s.calendar_availability_at>clock_timestamp()-INTERVAL '30 seconds'
                  AND s.calendar_availability_at<=clock_timestamp()
                  AND s.calendar_verified_at>clock_timestamp()-INTERVAL '30 seconds'
            ) INTO verified;
            IF verified THEN RETURN NEW; END IF;
        END IF;
        NEW.calendar_revision := OLD.calendar_revision+1;
    ELSE NEW.calendar_revision := 1;
    END IF;
    IF mode='google_authoritative' AND NEW.stato='confermata' THEN
        RAISE EXCEPTION 'Calendar authority requires verified finalization' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.calendar_track_booking()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION public.booking_message_original_subject() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
BEGIN
    IF session_user='melpis_c24_booking_worker'
       AND NOT c24_private.c24_worker_scope_allows(
           NEW.organization_id,'melpis_c24_booking_worker') THEN
        RAISE EXCEPTION 'scoped booking worker required' USING ERRCODE='42501';
    END IF;
    IF TG_OP='INSERT' THEN
        SELECT c.contact_id INTO NEW.c24_original_subject_id
          FROM public.conversations AS c
         WHERE c.organization_id=NEW.organization_id AND c.id=NEW.conversation_id
         FOR SHARE;
    ELSIF NEW.c24_original_subject_id IS DISTINCT FROM OLD.c24_original_subject_id THEN
        RAISE EXCEPTION 'Original subject evidence is immutable' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.booking_message_original_subject()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

-- This legacy invoker trigger resolves booking_stays on message UPDATE.
-- Its worker branch uses the already-granted, read-only scope helpers and
-- returns; the later worker-only base trigger rejects every UPDATE. Normal
-- callers retain the original immutability rule.
CREATE OR REPLACE FUNCTION public.booking_stay_source_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp
AS $$
BEGIN
    IF session_user='melpis_c24_booking_worker' THEN
        IF NOT c24_private.c24_worker_scope_allows_message(
            OLD.id,'melpis_c24_booking_worker')
           OR NOT c24_private.c24_worker_scope_allows_conversation(
            OLD.organization_id,OLD.conversation_id::uuid,'melpis_c24_booking_worker') THEN
            RAISE EXCEPTION 'scoped booking worker required' USING ERRCODE='42501';
        END IF;
        RETURN NEW;
    END IF;
    IF (NEW.organization_id,NEW.conversation_id,NEW.direction)
       IS DISTINCT FROM (OLD.organization_id,OLD.conversation_id,OLD.direction)
       AND EXISTS(
           SELECT 1 FROM public.booking_stays
            WHERE organization_id=OLD.organization_id
              AND original_conversation_id=OLD.conversation_id
              AND source_message_id=OLD.id
       ) THEN
        RAISE EXCEPTION 'Stay source lineage cannot be reassigned' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.booking_stay_source_guard()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

-- GDPR's shared trigger remains SECURITY INVOKER for all existing writers.
-- The dedicated worker branch delegates only the same erasure-barrier check
-- to a non-exposed helper after proving organization/message capability.
CREATE OR REPLACE FUNCTION c24_private.c24_worker_gdpr_guard(
    p_table_name text,p_operation text,p_item jsonb,p_old_item jsonb
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog,pg_temp
AS $$
DECLARE item jsonb := p_item; org uuid; subject uuid; msg uuid;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker'
       OR p_table_name NOT IN ('organizations','bookings','booking_stays','messages',
            'message_delivery_attempts','booking_allocation_sets','booking_resource_allocations',
            'booking_allocation_requests','booking_confirmation_outbox')
       OR p_operation NOT IN ('INSERT','UPDATE') THEN
        RAISE EXCEPTION 'scoped booking worker required' USING ERRCODE='42501';
    END IF;
    IF p_table_name='message_delivery_attempts' THEN
        msg := NULLIF(item->>'message_id','')::uuid;
        IF msg IS NULL OR NOT c24_private.c24_worker_scope_allows_message(
            msg,'melpis_c24_booking_worker') THEN
            RAISE EXCEPTION 'scoped booking worker required' USING ERRCODE='42501';
        END IF;
        SELECT m.organization_id INTO org FROM public.messages AS m WHERE m.id=msg;
    ELSIF p_table_name='organizations' THEN
        org := NULLIF(item->>'id','')::uuid;
    ELSE
        org := NULLIF(item->>'organization_id','')::uuid;
    END IF;
    IF org IS NULL OR NOT c24_private.c24_worker_scope_allows(
        org,'melpis_c24_booking_worker') THEN
        RAISE EXCEPTION 'scoped booking worker required' USING ERRCODE='42501';
    END IF;
    IF p_table_name='usage_events' AND item->>'event_type' <> 'system.provider_attempt' THEN RETURN; END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(org::text,606));
    IF NOT EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers AS b WHERE b.organization_id=org) THEN
        RETURN;
    END IF;
    IF p_table_name='contacts' THEN subject := NULLIF(item->>'id','')::uuid;
    ELSE
        subject := NULLIF(item->>'contact_id','')::uuid;
        IF subject IS NULL AND item->>'conversation_id' IS NOT NULL THEN
            SELECT c.contact_id INTO subject FROM public.conversations AS c
             WHERE c.organization_id=org AND c.id=(item->>'conversation_id')::uuid;
        END IF;
    END IF;
    IF subject IS NULL THEN
        msg := COALESCE(NULLIF(item->>'message_id',''),
                        CASE WHEN p_table_name='event_log' THEN item->>'source_id' END,
                        item#>>'{metadata,context,message_id}')::uuid;
        IF msg IS NOT NULL THEN
            SELECT c.contact_id INTO subject
              FROM public.messages AS m JOIN public.conversations AS c
                ON c.id=m.conversation_id AND c.organization_id=org
             WHERE m.organization_id=org AND m.id=msg;
        END IF;
    END IF;
    IF subject IS NULL AND item->>'booking_id' IS NOT NULL THEN
        SELECT b.contact_id INTO subject FROM public.bookings AS b
         WHERE b.organization_id=org AND b.id=(item->>'booking_id')::uuid;
    END IF;
    IF EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers AS b
              WHERE b.organization_id=org AND (b.scope='organization' OR b.subject_id=subject)) THEN
        RAISE EXCEPTION 'GDPR erasure barrier rejects data production' USING ERRCODE='55000';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_gdpr_guard(text,text,jsonb,jsonb)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_gdpr_guard(text,text,jsonb,jsonb)
    TO melpis_c24_booking_worker;

-- The hotel stay trigger is a privileged lookup. For the private worker it
-- accepts only the stay row attached to the current fenced claim; the legacy
-- service-role validation below remains intact.
CREATE OR REPLACE FUNCTION c24_private.c24_worker_stay_insert_guard(
    p_organization_id uuid,p_booking_id uuid,p_subject_id uuid,
    p_conversation_id uuid,p_source_message_id uuid,p_source_kind text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE claim_message uuid; claim_conversation uuid;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker'
       OR p_organization_id IS NULL OR p_booking_id IS NULL OR p_subject_id IS NULL
       OR p_conversation_id IS NULL OR p_source_message_id IS NULL
       OR p_source_kind IS DISTINCT FROM 'inbound' THEN
        RAISE EXCEPTION 'scoped booking worker required' USING ERRCODE='42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(
        pg_catalog.hashtextextended(p_organization_id::text,606));
    SELECT ctx.message_id,ctx.conversation_id
      INTO claim_message,claim_conversation
      FROM c24_private.worker_context AS ctx
      JOIN public.booking_worker_claims AS claim
        ON claim.organization_id=ctx.organization_id
       AND claim.message_id=ctx.message_id
       AND claim.claim_id=ctx.claim_id
       AND claim.generation=ctx.generation
       AND claim.lease_until=ctx.lease_until
     WHERE ctx.backend_pid=pg_catalog.pg_backend_pid()
       AND ctx.transaction_id=pg_catalog.pg_current_xact_id()
       AND ctx.organization_id=p_organization_id
       AND ctx.booking_id=p_booking_id
       AND ctx.lease_until>pg_catalog.clock_timestamp()
     FOR UPDATE OF claim;
    IF NOT FOUND OR claim_message<>p_source_message_id
       OR claim_conversation<>p_conversation_id THEN
        RAISE EXCEPTION 'booking stay is outside the active claim' USING ERRCODE='42501';
    END IF;
    IF EXISTS(SELECT 1 FROM public.booking_stay_purge_fences AS f
              WHERE f.organization_id=p_organization_id AND f.booking_id=p_booking_id)
       OR EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers AS e
              WHERE e.organization_id=p_organization_id
                AND (e.scope='organization' OR e.subject_id=p_subject_id)) THEN
        RAISE EXCEPTION 'booking stay is blocked by purge or erasure evidence' USING ERRCODE='55000';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.bookings AS b
        JOIN public.conversations AS c
          ON c.organization_id=b.organization_id AND c.id=p_conversation_id
        JOIN public.contacts AS s
          ON s.organization_id=b.organization_id AND s.id=p_subject_id
        JOIN public.messages AS m
          ON m.organization_id=b.organization_id AND m.conversation_id=c.id
         AND m.id=p_source_message_id
        WHERE b.organization_id=p_organization_id AND b.id=p_booking_id
          AND b.contact_id=p_subject_id AND b.id_conversazione=p_conversation_id::text
          AND c.contact_id=p_subject_id AND c.deleted_at IS NULL
          AND s.deleted_at IS NULL AND m.direction='inbound' AND m.deleted_at IS NULL
          AND m.c24_original_subject_id=p_subject_id
    ) THEN
        RAISE EXCEPTION 'verified inbound stay lineage required' USING ERRCODE='55000';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_stay_insert_guard(
    uuid,uuid,uuid,uuid,uuid,text) FROM PUBLIC,anon,authenticated,service_role,
    melpis_c24_booking_worker;

-- Preserve the migration-091 service-role checks verbatim after a new worker
-- branch. Every relation is qualified and pg_temp is last in the safe path.
CREATE OR REPLACE FUNCTION public.booking_stay_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE booking_contact uuid; caller_marker boolean;
BEGIN
 IF session_user='melpis_c24_booking_worker' THEN
  PERFORM c24_private.c24_worker_stay_insert_guard(
    NEW.organization_id,NEW.booking_id,NEW.original_subject_id,
    NEW.original_conversation_id,NEW.source_message_id,NEW.source_kind);
  RETURN NEW;
 END IF;
 PERFORM pg_catalog.pg_advisory_xact_lock_shared(
   pg_catalog.hashtextextended(NEW.organization_id::text,606));
 IF EXISTS(SELECT 1 FROM public.booking_stay_purge_fences f
   WHERE f.organization_id=NEW.organization_id AND f.booking_id=NEW.booking_id) THEN
  RAISE EXCEPTION 'Purged stay cannot be recreated' USING ERRCODE='55000';
 END IF;
 IF TG_OP='UPDATE' AND (NEW.organization_id,NEW.booking_id,NEW.original_subject_id,
      NEW.original_conversation_id,NEW.source_message_id,NEW.source_kind)
   IS DISTINCT FROM (OLD.organization_id,OLD.booking_id,OLD.original_subject_id,
      OLD.original_conversation_id,OLD.source_message_id,OLD.source_kind) THEN
  RAISE EXCEPTION 'Stay lineage is immutable' USING ERRCODE='55000';
 END IF;
 IF NEW.source_kind NOT IN ('inbound','owner') THEN
  RAISE EXCEPTION 'Stay source kind invalid' USING ERRCODE='23514';
 END IF;
 SELECT b.contact_id INTO booking_contact FROM public.bookings b
  WHERE b.organization_id=NEW.organization_id AND b.id=NEW.booking_id FOR KEY SHARE;
 IF NOT FOUND OR booking_contact IS DISTINCT FROM NEW.original_subject_id
   OR NOT EXISTS(SELECT 1 FROM public.contacts c WHERE c.organization_id=NEW.organization_id
      AND c.id=NEW.original_subject_id AND c.deleted_at IS NULL)
   OR EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers e WHERE e.organization_id=NEW.organization_id
      AND (e.scope='organization' OR e.subject_id=NEW.original_subject_id)) THEN
  RAISE EXCEPTION 'Stay lineage unavailable' USING ERRCODE='55000';
 END IF;
 IF NEW.source_kind='inbound' THEN
  IF NEW.original_conversation_id IS NULL OR NEW.source_message_id IS NULL
   OR NOT EXISTS (SELECT 1 FROM public.bookings b
    JOIN public.conversations c ON c.organization_id=b.organization_id AND c.id=NEW.original_conversation_id
    JOIN public.messages m ON m.organization_id=b.organization_id AND m.conversation_id=c.id AND m.id=NEW.source_message_id
    WHERE b.organization_id=NEW.organization_id AND b.id=NEW.booking_id
     AND b.id_conversazione=NEW.original_conversation_id::text AND c.contact_id=NEW.original_subject_id
     AND c.deleted_at IS NULL AND m.direction='inbound' AND m.deleted_at IS NULL
     AND m.c24_original_subject_id=NEW.original_subject_id) THEN
   RAISE EXCEPTION 'Verified inbound stay lineage required' USING ERRCODE='55000';
  END IF;
 ELSE
  IF NEW.original_conversation_id IS NOT NULL OR NEW.source_message_id IS NOT NULL THEN
   RAISE EXCEPTION 'Owner stay must not claim an inbound source' USING ERRCODE='23514';
  END IF;
  SELECT EXISTS(SELECT 1 FROM public.booking_stay_owner_markers m
   WHERE m.transaction_id=pg_catalog.txid_current() AND m.backend_pid=pg_catalog.pg_backend_pid()
    AND m.organization_id=NEW.organization_id AND m.booking_id=NEW.booking_id
    AND m.subject_id=NEW.original_subject_id) INTO caller_marker;
  IF NOT caller_marker THEN RAISE EXCEPTION 'Owner stay authorization marker required' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_guard()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION public.gdpr_guard_write() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE item jsonb := to_jsonb(NEW); org uuid; subject uuid; msg uuid;
BEGIN
    IF session_user='melpis_c24_booking_worker' THEN
        PERFORM c24_private.c24_worker_gdpr_guard(
            TG_TABLE_NAME,TG_OP,item,CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END);
        RETURN NEW;
    END IF;
    org := NULLIF(item->>'organization_id','')::uuid;
    IF TG_TABLE_NAME='organizations' THEN
        IF TG_OP='UPDATE' AND NEW.business_profile IS NOT DISTINCT FROM OLD.business_profile
            AND NEW.name IS NOT DISTINCT FROM OLD.name THEN RETURN NEW; END IF;
        org := NEW.id;
    END IF;
    IF org IS NULL AND TG_TABLE_NAME='contact_consent_log' THEN
        SELECT organization_id INTO org FROM contacts WHERE id=NEW.contact_id;
    END IF;
    IF org IS NULL AND TG_TABLE_NAME='message_delivery_attempts' THEN
        SELECT organization_id INTO org FROM messages WHERE id=NEW.message_id;
    END IF;
    IF org IS NULL THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME='usage_events' AND item->>'event_type' <> 'system.provider_attempt' THEN RETURN NEW; END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended(org::text,606));
    IF NOT EXISTS (SELECT 1 FROM gdpr_erasure_barriers WHERE organization_id=org) THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME='contacts' THEN subject := NEW.id;
    ELSE
        subject := NULLIF(item->>'contact_id','')::uuid;
        IF subject IS NULL AND item->>'conversation_id' IS NOT NULL THEN
            SELECT contact_id INTO subject FROM conversations
                WHERE organization_id=org AND id=(item->>'conversation_id')::uuid;
        END IF;
    END IF;
    IF subject IS NULL THEN
        msg := COALESCE(NULLIF(item->>'message_id',''),
                        CASE WHEN TG_TABLE_NAME='event_log' THEN item->>'source_id' END,
                        item#>>'{metadata,context,message_id}')::uuid;
        IF msg IS NOT NULL THEN
            SELECT c.contact_id INTO subject FROM messages m JOIN conversations c
                ON c.id=m.conversation_id AND c.organization_id=org
                WHERE m.organization_id=org AND m.id=msg;
        END IF;
    END IF;
    IF subject IS NULL AND item->>'booking_id' IS NOT NULL THEN
        SELECT contact_id INTO subject FROM bookings WHERE organization_id=org AND id=(item->>'booking_id')::uuid;
    END IF;
    IF EXISTS (SELECT 1 FROM gdpr_erasure_barriers b WHERE b.organization_id=org
        AND (b.scope='organization' OR b.subject_id=subject)) THEN
        RAISE EXCEPTION 'GDPR erasure barrier rejects data production' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;

DO $$
DECLARE context_owner oid; owner_name name; fn regprocedure;
BEGIN
    SELECT relowner INTO context_owner
      FROM pg_class WHERE oid='c24_private.worker_context'::regclass;
    owner_name := pg_get_userbyid(context_owner);
    FOREACH fn IN ARRAY ARRAY[
        'public.booking_allocation_guard()'::regprocedure,
        'public.calendar_track_booking()'::regprocedure,
        'public.booking_message_original_subject()'::regprocedure,
        'public.booking_stay_guard()'::regprocedure,
        'c24_private.c24_worker_stay_insert_guard(uuid,uuid,uuid,uuid,uuid,text)'::regprocedure,
        'c24_private.c24_worker_gdpr_guard(text,text,jsonb,jsonb)'::regprocedure
    ] LOOP
        IF (SELECT proowner FROM pg_proc WHERE oid=fn)<>context_owner THEN
            EXECUTE format('ALTER FUNCTION %s OWNER TO %I',fn,owner_name);
        END IF;
        IF (SELECT proowner FROM pg_proc WHERE oid=fn)<>context_owner
           OR NOT (SELECT prosecdef FROM pg_proc WHERE oid=fn) THEN
            RAISE EXCEPTION 'private worker trigger helper owner/security mismatch: %',fn;
        END IF;
    END LOOP;
END;
$$;

-- Audit effective privileges after every migration, including inherited
-- PUBLIC grants. The worker may access only c24_private views and wrappers.
DO $$
DECLARE worker oid := 'melpis_c24_booking_worker'::regrole;
DECLARE context_owner oid;
BEGIN
    SELECT relowner INTO context_owner
      FROM pg_class WHERE oid='c24_private.worker_context'::regclass;
    IF has_schema_privilege(worker,'c24_private','CREATE') THEN
        RAISE EXCEPTION 'worker can create objects in the private schema';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_namespace AS n
         WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
           AND n.nspname NOT LIKE 'pg_temp_%' AND n.nspname NOT LIKE 'pg_toast_temp_%'
           AND has_schema_privilege(worker,n.oid,'CREATE')
    ) THEN
        RAISE EXCEPTION 'worker can create objects in a non-system schema';
    END IF;
    -- Effective relation/sequence ACLs are checked across every non-system
    -- schema, including privileges inherited from PUBLIC and role memberships.
    IF EXISTS (
        SELECT 1 FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace
         WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
           AND n.nspname NOT LIKE 'pg_temp_%' AND n.nspname NOT LIKE 'pg_toast_temp_%'
           AND c.relkind IN ('r','p','v','m','f','S')
           AND (
             (c.relkind='S' AND has_sequence_privilege(worker,c.oid,'USAGE,SELECT,UPDATE'))
              OR (current_setting('server_version_num')::integer>=170000
                  AND CASE WHEN current_setting('server_version_num')::integer>=170000
                           THEN has_table_privilege(worker,c.oid,'MAINTAIN') ELSE false END)
             OR (c.relkind<>'S' AND (
               has_table_privilege(worker,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
               OR EXISTS (SELECT 1 FROM pg_attribute AS a
                    WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
                      AND (has_column_privilege(worker,c.oid,a.attnum,'SELECT')
                        OR has_column_privilege(worker,c.oid,a.attnum,'INSERT')
                        OR has_column_privilege(worker,c.oid,a.attnum,'UPDATE')
                        OR has_column_privilege(worker,c.oid,a.attnum,'REFERENCES')))
             ))
           )
           AND (n.nspname<>'c24_private' OR c.relkind<>'v' OR c.relname NOT IN (
             'organizations','bookings','booking_availability','booking_stays','booking_settings',
             'messages','conversations','contacts','booking_catalog_publications',
             'booking_catalog_requests','booking_conversation_states','booking_conversation_requests',
             'booking_conversation_revocations','booking_worker_claims','inbound_worker_operations',
             'inbound_worker_receipts','meta_webhook_inbox','booking_sector_admission_fences',
             'booking_sector_admission_epochs','booking_allocation_sets','booking_resource_allocations',
             'booking_allocation_requests','booking_confirmation_outbox','booking_stay_purge_fences',
             'booking_stay_tombstones','gdpr_erasure_barriers','gdpr_retention_holds','usage_events',
             'c24_idle_watermarks','c24_source_fences','message_delivery_attempts',
             'worker_booking_inserts','worker_booking_stays_inserts','worker_allocation_sets',
             'worker_resource_allocations','worker_allocation_requests','worker_confirmation_outbox',
              'worker_outbound_messages','simulation_requests','booking_resource_availability'
           ))
    ) THEN
        RAISE EXCEPTION 'worker has effective data privileges outside allowlisted private views';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid=p.pronamespace
         WHERE p.prosecdef
           AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
           AND n.nspname NOT LIKE 'pg_temp_%' AND n.nspname NOT LIKE 'pg_toast_temp_%'
           AND has_schema_privilege(worker,n.oid,'USAGE')
           AND has_function_privilege(worker,p.oid,'EXECUTE')
           AND NOT (
             n.nspname='c24_private' AND p.oid IN (
               to_regprocedure('c24_private.c24_worker_readiness()'),
               to_regprocedure('c24_private.c24_quota_trust_epoch()'),
               to_regprocedure('c24_private.c24_worker_next_jobs(uuid,integer)'),
               to_regprocedure('c24_private.c24_worker_begin_job(uuid,text)'),
                        to_regprocedure('c24_private.c24_worker_renew_job(uuid,uuid,uuid,bigint,integer)'),
               to_regprocedure('c24_private.c24_worker_consume_capability(uuid)'),
               to_regprocedure('c24_private.c24_worker_finish_job()'),
               to_regprocedure('c24_private.c24_worker_scope_allows(uuid,text)'),
               to_regprocedure('c24_private.c24_worker_scope_allows_message(uuid,text)'),
               to_regprocedure('c24_private.c24_worker_scope_allows_claim_source(uuid,text)'),
               to_regprocedure('c24_private.c24_worker_scope_allows_operation(uuid,uuid,text)'),
               to_regprocedure('c24_private.c24_worker_scope_allows_request(uuid,uuid,uuid,text)'),
               to_regprocedure('c24_private.c24_worker_scope_allows_conversation(uuid,uuid,text)'),
               to_regprocedure('c24_private.c24_worker_scope_allows_contact(uuid,uuid,text)'),
               to_regprocedure('c24_private.c24_worker_scope_allows_inbox(uuid,uuid,text)'),
               to_regprocedure('c24_private.c24_worker_scope_allows_booking(uuid,uuid,text)'),
               to_regprocedure('c24_private.c24_worker_scope_allows_bound_booking(uuid,uuid,text)'),
               to_regprocedure('c24_private.c24_worker_scope_allows_simulation_request(uuid,uuid,uuid,text)'),
               to_regprocedure('c24_private.c24_worker_claim_source_allows(uuid,text,text,text)'),
               to_regprocedure('c24_private.c24_worker_claim_booking_insert_allows(uuid,uuid,text,text,text)'),
               to_regprocedure('c24_private.c24_worker_outbound_booking_message_allows(uuid,uuid,text,text,text)'),
               to_regprocedure('c24_private.c24_worker_mark_sector_insert(uuid,uuid)'),
               to_regprocedure('c24_private.c24_worker_attach_calendar_source(uuid,uuid,uuid)'),
               to_regprocedure('c24_private.c24_worker_gdpr_guard(text,text,jsonb,jsonb)')
             )
           )
    ) THEN
        RAISE EXCEPTION 'worker can execute an unexpected SECURITY DEFINER routine';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid=p.pronamespace
        WHERE n.nspname='c24_private' AND p.prosecdef AND p.proowner<>context_owner
    ) THEN
        RAISE EXCEPTION 'private SECURITY DEFINER function owner differs from trusted context owner';
    END IF;
END;
$$;

-- A worker transaction may write only rows explicitly present in the immutable
-- application-issued payload. The capability is bound to backend, transaction,
-- claim generation, and the still-live lease; a queue claim by itself cannot
-- authorize any effect.
CREATE OR REPLACE FUNCTION c24_private.c24_worker_effect_matches(
    p_effect text,p_row jsonb
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE cap c24_private.worker_capabilities%ROWTYPE; expected jsonb; actual jsonb;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker'
       OR current_user IS DISTINCT FROM 'postgres'
       OR p_row IS NULL OR jsonb_typeof(p_row)<>'object' THEN RETURN false; END IF;
    SELECT capability.* INTO cap
      FROM c24_private.worker_context AS ctx
      JOIN c24_private.worker_capabilities AS capability
        ON capability.capability_id=ctx.capability_id
       AND capability.organization_id=ctx.organization_id
       AND capability.message_id=ctx.message_id
       AND capability.claim_id=ctx.claim_id
       AND capability.generation=ctx.generation
      JOIN public.booking_worker_claims AS claim
        ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
       AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
       AND claim.lease_until=ctx.lease_until
     WHERE ctx.backend_pid=pg_backend_pid()
       AND ctx.transaction_id=pg_current_xact_id()
       AND ctx.lease_until>clock_timestamp()
       AND capability.consumed_backend_pid=pg_backend_pid()
       AND capability.consumed_transaction_id=pg_current_xact_id()
       AND capability.consumed_at IS NOT NULL AND capability.completed_at IS NULL
       AND capability.payload_hash=encode(pg_catalog.sha256(convert_to(capability.payload::text,'UTF8')),'hex')
     FOR UPDATE OF capability,claim;
    IF NOT FOUND THEN RETURN false; END IF;

    CASE p_effect
      WHEN 'booking' THEN
        expected := cap.payload->'booking';
        actual := jsonb_build_object(
          'id',p_row->'id','organization_id',p_row->'organization_id',
          'contact_id',p_row->'contact_id','id_conversazione',p_row->'id_conversazione',
          'nome_cliente',p_row->'nome_cliente','telefono',p_row->'telefono',
          'data',p_row->'data','ora',p_row->'ora','coperti',p_row->'coperti',
          'note',p_row->'note','stato',p_row->'stato','origine',p_row->'origine',
          'source_message_id',p_row->'source_message_id',
          'calendar_confirm_pending',p_row->'calendar_confirm_pending',
          'richiede_intervento',p_row->'richiede_intervento',
          'calendar_revision',p_row->'calendar_revision',
          'completata_at',p_row->'completata_at','no_show_at',p_row->'no_show_at');
      WHEN 'stay' THEN
        expected := cap.payload->'stay';
        actual := jsonb_build_object(
          'organization_id',p_row->'organization_id','booking_id',p_row->'booking_id',
          'original_subject_id',p_row->'original_subject_id',
          'original_conversation_id',p_row->'original_conversation_id',
          'source_message_id',p_row->'source_message_id','arrival_date',p_row->'arrival_date',
          'departure_date',p_row->'departure_date','revision',p_row->'revision',
          'source_kind',p_row->'source_kind');
      WHEN 'allocation_request' THEN
        expected := cap.payload->'allocation'->'request';
        actual := jsonb_build_object(
          'organization_id',p_row->'organization_id','request_id',p_row->'request_id',
          'booking_id',p_row->'booking_id','actor_user_id',p_row->'actor_user_id',
          'action',p_row->'action','payload_hash',p_row->'payload_hash','outcome',p_row->'outcome',
          'applied_allocation_revision',p_row->'applied_allocation_revision',
          'catalog_revision',p_row->'catalog_revision','mutation_hash',p_row->'mutation_hash',
          'actor_kind',p_row->'actor_kind','service_inbox_id',p_row->'service_inbox_id',
          'service_message_id',p_row->'service_message_id','service_operation_id',p_row->'service_operation_id');
      WHEN 'allocation_set' THEN
        expected := cap.payload->'allocation'->'set';
        actual := jsonb_build_object(
          'organization_id',p_row->'organization_id','booking_id',p_row->'booking_id',
          'allocation_revision',p_row->'allocation_revision','state',p_row->'state',
          'catalog_revision',p_row->'catalog_revision','service_id',p_row->'service_id',
          'timezone',p_row->'timezone','temporal_kind',p_row->'temporal_kind',
          'starts_at',p_row->'starts_at','ends_at',p_row->'ends_at',
          'arrival_date',p_row->'arrival_date','departure_date',p_row->'departure_date',
          'booking_fingerprint',p_row->'booking_fingerprint',
          'booking_calendar_revision',p_row->'booking_calendar_revision');
      WHEN 'resource_allocation' THEN
        actual := jsonb_build_object(
          'organization_id',p_row->'organization_id','booking_id',p_row->'booking_id',
          'allocation_revision',p_row->'allocation_revision','catalog_revision',p_row->'catalog_revision',
          'state',p_row->'state','resource_id',p_row->'resource_id','resource_kind',p_row->'resource_kind',
          'exclusive',p_row->'exclusive','quantity',p_row->'quantity',
          'starts_at',p_row->'starts_at','ends_at',p_row->'ends_at');
        expected := cap.payload->'allocation'->'resources';
        RETURN EXISTS (SELECT 1 FROM jsonb_array_elements(expected) AS effect(value)
                       WHERE effect.value=actual);
      WHEN 'outbound_message' THEN
        expected := cap.payload->'effects'->'outbound_message';
        actual := jsonb_build_object(
          'id',p_row->'id','organization_id',p_row->'organization_id',
          'conversation_id',p_row->'conversation_id','wam_id',p_row->'wam_id',
          'direction',p_row->'direction',
          'message_type',p_row->'message_type','content',p_row->'content',
          'content_text',p_row->'content_text','status',p_row->'status',
          'handling_type',p_row->'handling_type','idempotency_key',p_row->'idempotency_key');
      WHEN 'confirmation_outbox' THEN
        expected := cap.payload->'effects'->'confirmation_outbox';
        actual := jsonb_build_object(
          'organization_id',p_row->'organization_id','booking_id',p_row->'booking_id',
          'inbound_message_id',p_row->'inbound_message_id',
          'outbound_message_id',p_row->'outbound_message_id',
          'booking_revision',p_row->'booking_revision','status',p_row->'status');
      ELSE
        RETURN false;
    END CASE;
    RETURN expected IS NOT NULL AND expected<>'null'::jsonb AND actual=expected;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_effect_matches(text,jsonb)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION public.c24_worker_capability_effect_guard()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE effect_kind text;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker' THEN RETURN NEW; END IF;
    effect_kind := CASE TG_TABLE_NAME
      WHEN 'bookings' THEN 'booking'
      WHEN 'booking_stays' THEN 'stay'
      WHEN 'booking_allocation_requests' THEN 'allocation_request'
      WHEN 'booking_allocation_sets' THEN 'allocation_set'
      WHEN 'booking_resource_allocations' THEN 'resource_allocation'
      WHEN 'booking_confirmation_outbox' THEN 'confirmation_outbox'
      WHEN 'messages' THEN 'outbound_message'
      ELSE NULL END;
    IF effect_kind IS NULL OR NOT c24_private.c24_worker_effect_matches(effect_kind,to_jsonb(NEW)) THEN
        RAISE EXCEPTION 'effect is outside the issued booking capability' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.c24_worker_capability_effect_guard()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;

DO $$
DECLARE relation_name text;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
      'bookings','booking_stays','booking_allocation_requests','booking_allocation_sets',
      'booking_resource_allocations','booking_confirmation_outbox','messages'
    ] LOOP
      EXECUTE format('DROP TRIGGER IF EXISTS c24_worker_capability_effect_guard ON public.%I',relation_name);
      EXECUTE format('DROP TRIGGER IF EXISTS c24_worker_capability_effect_guard_after_insert ON public.%I',relation_name);
      -- Observe the final row after all BEFORE normalizers (notably
      -- calendar_track_booking) have populated revision-bound columns. An
      -- exception from this AFTER trigger still aborts the same transaction.
      EXECUTE format('CREATE TRIGGER c24_worker_capability_effect_guard_after_insert AFTER INSERT ON public.%I '
        'FOR EACH ROW EXECUTE FUNCTION public.c24_worker_capability_effect_guard()',relation_name);
    END LOOP;
END;
$$;

-- Write grants below are INSERT-only. Each write is checked against the
-- immutable, consumed capability by the base-table trigger above; no UPDATE or
-- DELETE writer is exposed to the private worker. The one SELECT grant is only
-- for the capability-scoped outbound-message view used for idempotent enqueue.
GRANT INSERT ON c24_private.worker_booking_inserts,
    c24_private.worker_booking_stays_inserts,c24_private.worker_allocation_sets,
    c24_private.worker_resource_allocations,c24_private.worker_allocation_requests,
    c24_private.worker_confirmation_outbox,c24_private.worker_outbound_messages
    TO melpis_c24_booking_worker;
GRANT SELECT ON c24_private.worker_outbound_messages TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_finish_job()
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $$
DECLARE ctx c24_private.worker_context%ROWTYPE; cap c24_private.worker_capabilities%ROWTYPE;
        expected_count integer; actual_count integer; effect jsonb; booking_row public.bookings%ROWTYPE;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker'
       OR current_user IS DISTINCT FROM 'postgres' THEN
        RAISE EXCEPTION 'dedicated booking worker required' USING ERRCODE='42501';
    END IF;
    SELECT * INTO ctx FROM c24_private.worker_context AS context
     WHERE context.backend_pid=pg_backend_pid()
       AND context.transaction_id=pg_current_xact_id()
       AND context.lease_until>clock_timestamp()
     FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    PERFORM 1 FROM public.booking_worker_claims AS claim
     WHERE claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
       AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
       AND claim.lease_until=ctx.lease_until AND claim.lease_until>clock_timestamp()
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'live booking capability unavailable' USING ERRCODE='40001';
    END IF;
    SELECT * INTO cap FROM c24_private.worker_capabilities AS capability
     WHERE capability.capability_id=ctx.capability_id
       AND capability.organization_id=ctx.organization_id
       AND capability.message_id=ctx.message_id
       AND capability.claim_id=ctx.claim_id
       AND capability.generation=ctx.generation
       AND capability.consumed_backend_pid=pg_backend_pid()
       AND capability.consumed_transaction_id=pg_current_xact_id()
       AND capability.consumed_at IS NOT NULL AND capability.completed_at IS NULL
     FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    IF cap.payload_hash<>encode(pg_catalog.sha256(convert_to(cap.payload::text,'UTF8')),'hex') THEN
        RAISE EXCEPTION 'live booking capability unavailable' USING ERRCODE='40001';
    END IF;
    SELECT * INTO booking_row FROM public.bookings
     WHERE organization_id=ctx.organization_id AND id=ctx.booking_id;
    IF NOT FOUND OR NOT c24_private.c24_worker_effect_matches('booking',to_jsonb(booking_row)) THEN
        RAISE EXCEPTION 'booking effect missing or changed' USING ERRCODE='40001';
    END IF;
    IF cap.payload->'stay' IS NULL OR cap.payload->'stay'='null'::jsonb THEN
        IF EXISTS(SELECT 1 FROM public.booking_stays WHERE organization_id=ctx.organization_id AND booking_id=ctx.booking_id) THEN
            RAISE EXCEPTION 'unexpected stay effect' USING ERRCODE='40001';
        END IF;
    ELSE
        SELECT count(*) INTO actual_count FROM public.booking_stays s
         WHERE s.organization_id=ctx.organization_id AND s.booking_id=ctx.booking_id
           AND c24_private.c24_worker_effect_matches('stay',to_jsonb(s));
        IF actual_count<>1 THEN RAISE EXCEPTION 'stay effect missing or changed' USING ERRCODE='40001'; END IF;
    END IF;
    SELECT count(*) INTO actual_count FROM public.booking_allocation_requests r
     WHERE r.organization_id=ctx.organization_id AND r.booking_id=ctx.booking_id
       AND c24_private.c24_worker_effect_matches('allocation_request',to_jsonb(r));
    IF actual_count<>1 THEN RAISE EXCEPTION 'allocation receipt missing or changed' USING ERRCODE='40001'; END IF;
    SELECT count(*) INTO actual_count FROM public.booking_allocation_sets a
     WHERE a.organization_id=ctx.organization_id AND a.booking_id=ctx.booking_id
       AND c24_private.c24_worker_effect_matches('allocation_set',to_jsonb(a));
    IF actual_count<>1 THEN RAISE EXCEPTION 'allocation set missing or changed' USING ERRCODE='40001'; END IF;
    expected_count:=jsonb_array_length(cap.payload->'allocation'->'resources');
    SELECT count(*) INTO actual_count FROM public.booking_resource_allocations r
     WHERE r.organization_id=ctx.organization_id AND r.booking_id=ctx.booking_id
       AND c24_private.c24_worker_effect_matches('resource_allocation',to_jsonb(r));
    IF actual_count<>expected_count THEN RAISE EXCEPTION 'resource allocation set incomplete' USING ERRCODE='40001'; END IF;
    IF cap.payload->'effects'->'confirmation_outbox' IS NULL
       OR cap.payload->'effects'->'confirmation_outbox'='null'::jsonb THEN
        IF EXISTS(SELECT 1 FROM public.booking_confirmation_outbox WHERE organization_id=ctx.organization_id AND booking_id=ctx.booking_id)
           OR EXISTS(SELECT 1 FROM public.messages WHERE organization_id=ctx.organization_id
               AND handling_type='booking_confirmation' AND idempotency_key='booking-confirm:'||ctx.organization_id::text||':'||ctx.message_id::text) THEN
            RAISE EXCEPTION 'unexpected customer confirmation effect' USING ERRCODE='40001';
        END IF;
    ELSE
        SELECT count(*) INTO actual_count FROM public.messages m
         WHERE m.organization_id=ctx.organization_id AND m.id=(cap.payload->'effects'->'outbound_message'->>'id')::uuid
           AND c24_private.c24_worker_effect_matches('outbound_message',to_jsonb(m));
        IF actual_count<>1 THEN RAISE EXCEPTION 'outbound confirmation missing or changed' USING ERRCODE='40001'; END IF;
        SELECT count(*) INTO actual_count FROM public.booking_confirmation_outbox o
         WHERE o.organization_id=ctx.organization_id AND o.booking_id=ctx.booking_id
           AND c24_private.c24_worker_effect_matches('confirmation_outbox',to_jsonb(o));
        IF actual_count<>1 THEN RAISE EXCEPTION 'confirmation outbox missing or changed' USING ERRCODE='40001'; END IF;
    END IF;
    IF COALESCE(cap.payload->'effects'->'calendar'->>'required','false')::boolean THEN
        effect:=cap.payload->'effects'->'calendar';
        IF NOT EXISTS(SELECT 1 FROM public.external_booking_sync s
            WHERE s.organization_id=ctx.organization_id AND s.internal_booking_id=ctx.booking_id
              AND s.provider='google'
              AND s.idempotency_key=effect->>'idempotency_key'
              AND s.calendar_action=effect->>'action'
              AND s.calendar_desired_revision=(effect->>'desired_revision')::bigint
              AND s.calendar_authoritative=(effect->>'authoritative')::boolean
              AND s.sync_status=effect->>'sync_status'
              AND s.calendar_connection_epoch IS NOT DISTINCT FROM NULLIF(effect->>'connection_epoch','')::uuid
              AND s.calendar_id IS NOT DISTINCT FROM effect->>'calendar_id') THEN
            RAISE EXCEPTION 'Calendar synchronization effect missing or changed' USING ERRCODE='40001';
        END IF;
    ELSIF EXISTS(SELECT 1 FROM public.external_booking_sync s
        WHERE s.organization_id=ctx.organization_id
          AND s.idempotency_key='google-booking:'||ctx.organization_id::text||':'||ctx.booking_id::text) THEN
        RAISE EXCEPTION 'unexpected Calendar synchronization effect' USING ERRCODE='40001';
    END IF;
    UPDATE c24_private.worker_capabilities SET completed_at=clock_timestamp()
     WHERE capability_id=cap.capability_id AND consumed_transaction_id=pg_current_xact_id()
       AND completed_at IS NULL;
    IF NOT FOUND THEN RAISE EXCEPTION 'capability completion lost' USING ERRCODE='40001'; END IF;
    DELETE FROM c24_private.worker_context
     WHERE backend_pid=pg_backend_pid() AND transaction_id=pg_current_xact_id()
       AND capability_id=cap.capability_id;
    RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_finish_job() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_finish_job() TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_capability_must_finish()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE cap_id uuid;
BEGIN
    cap_id:=COALESCE(NEW.capability_id,OLD.capability_id);
    IF EXISTS(SELECT 1 FROM c24_private.worker_context WHERE capability_id=cap_id)
       OR NOT EXISTS(SELECT 1 FROM c24_private.worker_capabilities
          WHERE capability_id=cap_id AND completed_at IS NOT NULL
            AND consumed_transaction_id=pg_current_xact_id()) THEN
        RAISE EXCEPTION 'consumed worker capability must finalize all effects before commit' USING ERRCODE='40001';
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_capability_must_finish()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker;
DROP TRIGGER IF EXISTS c24_worker_capability_must_finish ON c24_private.worker_context;
CREATE CONSTRAINT TRIGGER c24_worker_capability_must_finish
AFTER INSERT ON c24_private.worker_context DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION c24_private.c24_worker_capability_must_finish();
