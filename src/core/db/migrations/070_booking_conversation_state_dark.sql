-- DRAFT: local synthetic tests only. C05/A06 retention/lifecycle GO is pending.
-- This file does not connect a worker, enable a policy, or change an old writer.
DO $conversation_state_migration$
BEGIN
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.conversations'::regclass AND conname='conversation_state_org_id_unique') THEN
  ALTER TABLE public.conversations ADD CONSTRAINT conversation_state_org_id_unique UNIQUE(organization_id,id);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='public.messages'::regclass AND conname='conversation_state_message_scope_unique') THEN
  ALTER TABLE public.messages ADD CONSTRAINT conversation_state_message_scope_unique UNIQUE(organization_id,conversation_id,id);
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.booking_conversation_states (
 organization_id uuid NOT NULL, conversation_id uuid NOT NULL,
 revision bigint NOT NULL CHECK(revision>0), catalog_revision bigint NOT NULL CHECK(catalog_revision>0),
 source_message_id uuid NOT NULL, provider_timestamp bigint NOT NULL CHECK(provider_timestamp>0),
 snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text)<=65536),
 snapshot_hash text NOT NULL CHECK(snapshot_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(organization_id,conversation_id),
 FOREIGN KEY(organization_id,conversation_id) REFERENCES public.conversations(organization_id,id) ON DELETE CASCADE,
 FOREIGN KEY(organization_id,conversation_id,source_message_id) REFERENCES public.messages(organization_id,conversation_id,id) ON DELETE CASCADE,
 FOREIGN KEY(organization_id,catalog_revision) REFERENCES public.booking_catalog_publications(organization_id,revision) ON DELETE RESTRICT,
 CHECK(snapshot->'runtime_enabled'='false'::jsonb AND snapshot->'confirmation_authorized'='false'::jsonb)
);
CREATE TABLE IF NOT EXISTS public.booking_conversation_requests (
 organization_id uuid NOT NULL, request_id uuid NOT NULL, conversation_id uuid NOT NULL,
 source_message_id uuid NOT NULL, actor_user_id uuid NOT NULL,
 action text NOT NULL CHECK(action IN ('proposal','confirmation')),
 payload_hash text NOT NULL CHECK(payload_hash ~ '^[a-f0-9]{64}$'),
 applied_revision bigint NOT NULL CHECK(applied_revision>0),
 canonical_response_hash text CHECK(canonical_response_hash ~ '^[a-f0-9]{64}$'),
 prompt_message_id uuid, prompt_body_hash text CHECK(prompt_body_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(organization_id,request_id),
 UNIQUE(organization_id,conversation_id,source_message_id),
 UNIQUE(organization_id,conversation_id,applied_revision),
 FOREIGN KEY(organization_id,conversation_id) REFERENCES public.booking_conversation_states(organization_id,conversation_id) ON DELETE CASCADE,
 FOREIGN KEY(organization_id,conversation_id,source_message_id) REFERENCES public.messages(organization_id,conversation_id,id) ON DELETE CASCADE,
 FOREIGN KEY(organization_id,conversation_id,prompt_message_id) REFERENCES public.messages(organization_id,conversation_id,id) ON DELETE CASCADE,
 CHECK((action='proposal' AND canonical_response_hash IS NOT NULL AND prompt_message_id IS NULL AND prompt_body_hash IS NULL)
    OR (action='confirmation' AND canonical_response_hash IS NULL AND prompt_message_id IS NOT NULL AND prompt_body_hash IS NOT NULL))
);
CREATE OR REPLACE FUNCTION public.booking_conversation_scope_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE subject uuid; item jsonb; k text; v jsonb; published jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,606));
 SELECT c.contact_id INTO subject FROM conversations c JOIN contacts p ON p.id=c.contact_id AND p.organization_id=c.organization_id
 WHERE c.organization_id=NEW.organization_id AND c.id=NEW.conversation_id AND c.deleted_at IS NULL
   AND p.deleted_at IS NULL AND p.consent_status IS DISTINCT FROM 'withdrawn';
 IF NOT FOUND OR EXISTS(SELECT 1 FROM gdpr_erasure_barriers WHERE organization_id=NEW.organization_id AND (scope='organization' OR subject_id=subject)) THEN
  RAISE EXCEPTION 'Conversation scope or GDPR barrier rejects metadata' USING ERRCODE='55000';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM messages WHERE organization_id=NEW.organization_id AND conversation_id=NEW.conversation_id AND id=NEW.source_message_id AND direction='inbound' AND deleted_at IS NULL) THEN
  RAISE EXCEPTION 'Conversation source scope invalid' USING ERRCODE='55000';
 END IF;
 IF TG_TABLE_NAME='booking_conversation_states' THEN
  IF NEW.snapshot->>'organization_id' IS DISTINCT FROM NEW.organization_id::text
     OR EXISTS(SELECT 1 FROM jsonb_object_keys(NEW.snapshot) AS snapshot_keys(key_name) WHERE snapshot_keys.key_name NOT IN
       ('version','organization_id','conversation_id','revision','catalog_revision','intent','slots',
        'missing_fields','ambiguous_fields','superseded','derived','handoff_reason','confirmation',
        'last_source_message_id','last_payload_hash','runtime_enabled','confirmation_authorized'))
     OR NEW.snapshot->>'conversation_id' IS DISTINCT FROM NEW.conversation_id::text
     OR NEW.snapshot->>'revision' IS DISTINCT FROM NEW.revision::text
     OR NEW.snapshot->>'catalog_revision' IS DISTINCT FROM NEW.catalog_revision::text
     OR NEW.snapshot->>'last_source_message_id' IS DISTINCT FROM NEW.source_message_id::text
     OR NEW.snapshot->'runtime_enabled' IS DISTINCT FROM 'false'::jsonb
     OR NEW.snapshot->'confirmation_authorized' IS DISTINCT FROM 'false'::jsonb
     OR jsonb_typeof(NEW.snapshot->'slots') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'Conversation snapshot scope invalid' USING ERRCODE='55000';
  END IF;
  SELECT catalog INTO published FROM booking_catalog_publications WHERE organization_id=NEW.organization_id AND revision=NEW.catalog_revision;
  FOR item IN SELECT value FROM jsonb_array_elements(NEW.snapshot->'slots') LOOP
   k:=item->>'field'; v:=item->'value';
   IF EXISTS(SELECT 1 FROM jsonb_object_keys(item) sk WHERE sk NOT IN
       ('field','value','origin','source_message_id','source_revision','catalog_revision','derived_from'))
      OR item->>'origin' NOT IN ('ai_extracted','customer_provided','backend_derived') THEN
    RAISE EXCEPTION 'Unreviewed slot metadata forbidden' USING ERRCODE='55000';
   END IF;
   IF k IN ('customer_name','contact_phone','contact_email') THEN
    IF jsonb_typeof(v) IS DISTINCT FROM 'boolean' THEN RAISE EXCEPTION 'Raw identity metadata forbidden' USING ERRCODE='55000'; END IF;
   ELSIF k IN ('party_size','guest_count','room_count','stay_nights') THEN
    IF jsonb_typeof(v) IS DISTINCT FROM 'number' OR v::text !~ '^[1-9][0-9]{0,6}$' OR (v::text)::numeric>1000000 THEN RAISE EXCEPTION 'Invalid administrative quantity' USING ERRCODE='55000'; END IF;
   ELSIF k IN ('appointment_date','arrival_date','departure_date') THEN
    IF jsonb_typeof(v) IS DISTINCT FROM 'string' OR (item->>'value') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RAISE EXCEPTION 'Invalid administrative date' USING ERRCODE='55000'; END IF;
   ELSIF k='appointment_time' THEN
    IF jsonb_typeof(v) IS DISTINCT FROM 'string' OR (item->>'value') !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' THEN RAISE EXCEPTION 'Invalid administrative time' USING ERRCODE='55000'; END IF;
   ELSIF k='service_id' THEN
    IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(published->'services') s WHERE s->>'service_id'=item->>'value') THEN RAISE EXCEPTION 'Service outside catalog' USING ERRCODE='55000'; END IF;
   ELSIF k='resource_id' THEN
    IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(published->'resources') r WHERE r->>'resource_id'=item->>'value') THEN RAISE EXCEPTION 'Resource outside catalog' USING ERRCODE='55000'; END IF;
   ELSE RAISE EXCEPTION 'Unsupported metadata field' USING ERRCODE='55000';
   END IF;
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_conversation_scope_guard() FROM PUBLIC;
CREATE OR REPLACE FUNCTION public.booking_conversation_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
 IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM booking_conversation_states WHERE organization_id=OLD.organization_id AND conversation_id=OLD.conversation_id) THEN RETURN OLD; END IF;
 IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM messages WHERE organization_id=OLD.organization_id AND conversation_id=OLD.conversation_id AND id=OLD.source_message_id) THEN RETURN OLD; END IF;
 IF TG_OP='DELETE' AND OLD.prompt_message_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM messages WHERE organization_id=OLD.organization_id AND conversation_id=OLD.conversation_id AND id=OLD.prompt_message_id) THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'Conversation receipt is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.booking_conversation_immutable() FROM PUBLIC;
-- ACL/audit block appended from the reviewed SQL069 role-path protocol below.
DO $$
DECLARE t text; c record; client record; reachable record; target regclass; p text;
BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles b CROSS JOIN pg_roles client_role WHERE b.rolname='service_role' AND NOT b.rolbypassrls AND NOT b.rolsuper AND client_role.rolname IN ('anon','authenticated') AND pg_has_role(b.oid,client_role.oid,'MEMBER')) THEN RAISE EXCEPTION 'Backend/client inheritance requires review'; END IF;
 FOREACH t IN ARRAY ARRAY['booking_conversation_states','booking_conversation_requests'] LOOP
  target := ('public.'||t)::regclass;
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY',target);
  EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %s FROM PUBLIC,anon,authenticated,service_role',target);
  FOR c IN SELECT attname FROM pg_attribute WHERE attrelid=target AND attnum>0 AND NOT attisdropped LOOP
   EXECUTE format('REVOKE ALL PRIVILEGES (%I) ON TABLE %s FROM PUBLIC,anon,authenticated,service_role',c.attname,target);
  END LOOP;
  EXECUTE format('DROP POLICY IF EXISTS conversation_deny_clients ON %s',target);
  EXECUTE format('CREATE POLICY conversation_deny_clients ON %s AS RESTRICTIVE FOR ALL TO anon,authenticated USING(false) WITH CHECK(false)',target);
  EXECUTE format('DROP POLICY IF EXISTS conversation_backend ON %s',target);
  EXECUTE format('CREATE POLICY conversation_backend ON %s FOR ALL TO service_role USING(true) WITH CHECK(true)',target);
  IF t='booking_conversation_requests' THEN EXECUTE format('GRANT SELECT,INSERT ON %s TO service_role',target);
  ELSE EXECUTE format('GRANT SELECT,INSERT,UPDATE ON %s TO service_role',target); END IF;
  EXECUTE format('DROP TRIGGER IF EXISTS conversation_scope_guard ON %s',target);
  EXECUTE format('CREATE TRIGGER conversation_scope_guard BEFORE INSERT OR UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION public.booking_conversation_scope_guard()',target);
  FOR client IN SELECT oid FROM pg_roles WHERE rolname IN ('anon','authenticated') LOOP
   FOR reachable IN WITH RECURSIVE role_paths(oid) AS (
    SELECT client.oid UNION SELECT m.roleid FROM pg_auth_members m JOIN role_paths p ON p.oid=m.member WHERE m.set_option OR m.admin_option
   ) SELECT r.* FROM pg_roles r JOIN role_paths p ON p.oid=r.oid LOOP
    IF reachable.rolsuper OR reachable.rolbypassrls OR reachable.oid=(SELECT relowner FROM pg_class WHERE oid=target) THEN RAISE EXCEPTION 'Privileged client role path requires review'; END IF;
    FOREACH p IN ARRAY ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
     IF has_table_privilege(reachable.oid,target,p) OR (p IN ('SELECT','INSERT','UPDATE','REFERENCES') AND has_any_column_privilege(reachable.oid,target,p)) THEN RAISE EXCEPTION 'Reachable client privilege requires review'; END IF;
    END LOOP;
   END LOOP;
   FOREACH p IN ARRAY ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
    IF has_table_privilege(client.oid,target,p) OR (p IN ('SELECT','INSERT','UPDATE','REFERENCES') AND has_any_column_privilege(client.oid,target,p)) THEN RAISE EXCEPTION 'Inherited client privilege requires review'; END IF;
   END LOOP;
  END LOOP;
  FOR reachable IN WITH RECURSIVE role_paths(oid) AS (
   SELECT oid FROM pg_roles WHERE rolname='service_role' UNION SELECT m.roleid FROM pg_auth_members m JOIN role_paths p ON p.oid=m.member WHERE m.set_option OR m.admin_option
  ) SELECT r.* FROM pg_roles r JOIN role_paths p ON p.oid=r.oid LOOP
   -- A migration administrator may own tables; an application backend may not.
   -- Direct BYPASSRLS is expected for Supabase service_role, not ownership/super.
   IF reachable.rolsuper OR reachable.oid=(SELECT relowner FROM pg_class WHERE oid=target)
      OR (reachable.oid<>(SELECT oid FROM pg_roles WHERE rolname='service_role') AND reachable.rolbypassrls)
   THEN RAISE EXCEPTION 'Privileged backend role path requires review'; END IF;
   FOREACH p IN ARRAY CASE WHEN t='booking_conversation_requests' THEN ARRAY['UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] ELSE ARRAY['DELETE','TRUNCATE','REFERENCES','TRIGGER'] END LOOP
    IF reachable.oid<>(SELECT relowner FROM pg_class WHERE oid=target) AND (has_table_privilege(reachable.oid,target,p) OR (p IN ('UPDATE','REFERENCES') AND has_any_column_privilege(reachable.oid,target,p))) THEN RAISE EXCEPTION 'Reachable backend privilege requires review'; END IF;
   END LOOP;
  END LOOP;
  IF t='booking_conversation_requests' THEN
   FOREACH p IN ARRAY ARRAY['UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
    IF (SELECT oid FROM pg_roles WHERE rolname='service_role')<>(SELECT relowner FROM pg_class WHERE oid=target) AND (has_table_privilege('service_role',target,p) OR (p IN ('UPDATE','REFERENCES') AND has_any_column_privilege('service_role',target,p))) THEN RAISE EXCEPTION 'Inherited backend receipt privilege requires review'; END IF;
   END LOOP;
  END IF;
  FOREACH p IN ARRAY ARRAY['TRUNCATE','REFERENCES','TRIGGER'] LOOP
   IF (SELECT oid FROM pg_roles WHERE rolname='service_role')<>(SELECT relowner FROM pg_class WHERE oid=target) AND (has_table_privilege('service_role',target,p) OR (p='REFERENCES' AND has_any_column_privilege('service_role',target,p))) THEN RAISE EXCEPTION 'Inherited backend privilege requires review'; END IF;
  END LOOP;
 END LOOP;

 DROP TRIGGER IF EXISTS conversation_immutable ON public.booking_conversation_requests;
 CREATE TRIGGER conversation_immutable BEFORE UPDATE OR DELETE ON public.booking_conversation_requests FOR EACH ROW EXECUTE FUNCTION public.booking_conversation_immutable();
 DROP TRIGGER IF EXISTS conversation_no_truncate ON public.booking_conversation_requests;
 CREATE TRIGGER conversation_no_truncate BEFORE TRUNCATE ON public.booking_conversation_requests FOR EACH STATEMENT EXECUTE FUNCTION public.booking_catalog_no_truncate();
END $$;
END;
$conversation_state_migration$;
