-- Dedicated database login for the dormant C2.4 worker. A password or other
-- credential is provisioned outside migrations and is never stored here.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='melpis_c24_booking_worker') THEN
  CREATE ROLE melpis_c24_booking_worker LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
      NOCREATEROLE NOREPLICATION BYPASSRLS;
  -- PostgreSQL 16+ gives a non-superuser CREATEROLE creator ADMIN OPTION on
  -- the new role. Remove only that automatic self-membership; no app role is
  -- allowed to receive the private BYPASSRLS identity.
  REVOKE melpis_c24_booking_worker FROM CURRENT_USER;
 ELSE
  -- Do not normalize an existing role or its credential state. Accept only
  -- the exact dormant role shape and fail closed on unexpected attributes.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='melpis_c24_booking_worker' AND
       (NOT rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR NOT rolbypassrls)) THEN
   RAISE EXCEPTION 'C2.4 worker role has unexpected attributes';
  END IF;
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='melpis_c24_booking_worker' AND
      (NOT rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR NOT rolbypassrls))
    OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
      WHERE r.rolname='melpis_c24_booking_worker')
    OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
      WHERE r.rolname='melpis_c24_booking_worker') THEN
  RAISE EXCEPTION 'C2.4 worker login must remain isolated';
 END IF;
END $$;

GRANT USAGE ON SCHEMA public TO melpis_c24_booking_worker;

-- Bind the worker connection to this logical database as well as to its login.
-- The random marker is provisioned per database and cannot be changed later;
-- the database OID is checked with it to reject same-cluster clones.
CREATE TABLE IF NOT EXISTS public.c24_database_instance_identity (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    identity_id uuid NOT NULL DEFAULT gen_random_uuid()
);
INSERT INTO public.c24_database_instance_identity(singleton)
VALUES (true) ON CONFLICT (singleton) DO NOTHING;
CREATE OR REPLACE FUNCTION public.c24_database_instance_identity_immutable()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'database instance identity is immutable' USING ERRCODE='42501';
END $$;
DROP TRIGGER IF EXISTS c24_database_instance_identity_immutable
 ON public.c24_database_instance_identity;
CREATE TRIGGER c24_database_instance_identity_immutable
 BEFORE UPDATE OR DELETE ON public.c24_database_instance_identity
 FOR EACH ROW EXECUTE FUNCTION public.c24_database_instance_identity_immutable();
REVOKE ALL ON public.c24_database_instance_identity FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.c24_database_instance_identity TO melpis_c24_booking_worker;
REVOKE ALL ON FUNCTION public.c24_database_instance_identity_immutable() FROM PUBLIC;

-- Narrow data access for confirmed private booking work. Tenant conditions
-- remain mandatory in application SQL; the role cannot read unrelated product
-- domains, modify catalogs, or manage accounts, billing, consent, or policy.
GRANT SELECT ON public.organizations, public.bookings, public.booking_stays,
    public.booking_settings, public.messages, public.conversations, public.contacts,
    public.booking_catalog_publications, public.booking_catalog_requests,
    public.booking_conversation_states, public.booking_conversation_requests,
    public.booking_conversation_revocations, public.booking_worker_claims,
    public.inbound_worker_operations, public.inbound_worker_receipts,
    public.meta_webhook_inbox, public.booking_sector_admission_fences,
    public.booking_allocation_sets, public.booking_resource_allocations,
    public.booking_allocation_requests, public.booking_confirmation_outbox,
    public.booking_stay_purge_fences,
    public.booking_stay_tombstones, public.gdpr_erasure_barriers,
    public.simulation_requests
TO melpis_c24_booking_worker;
GRANT SELECT ON public.usage_events TO melpis_c24_booking_worker;

-- Row-lock clauses require UPDATE privilege in PostgreSQL. Grant it only on
-- immutable key/revision columns used by FOR SHARE/UPDATE; actual mutation
-- privileges below remain limited to allocation and receipt tables.
GRANT UPDATE (id) ON public.organizations, public.messages, public.conversations,
    public.contacts, public.bookings TO melpis_c24_booking_worker;
GRANT UPDATE (organization_id) ON public.booking_settings,
    public.inbound_worker_receipts, public.inbound_worker_operations,
    public.meta_webhook_inbox TO melpis_c24_booking_worker;
GRANT UPDATE (id) ON public.usage_events TO melpis_c24_booking_worker;
GRANT UPDATE (request_id) ON public.simulation_requests TO melpis_c24_booking_worker;
GRANT UPDATE (organization_id, conversation_id) ON public.booking_conversation_states,
    public.booking_conversation_requests TO melpis_c24_booking_worker;
GRANT UPDATE (revision) ON public.booking_stays TO melpis_c24_booking_worker;

-- PostgreSQL requires UPDATE privilege for SELECT ... FOR UPDATE/SHARE. The
-- worker receives only column-level lock privileges; a trigger rejects every
-- attempted data UPDATE on these read-only evidence/parent tables. This keeps
-- row-lock semantics without granting a usable mutation capability.
CREATE OR REPLACE FUNCTION public.c24_private_worker_reject_update()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF session_user='melpis_c24_booking_worker' THEN
  RAISE EXCEPTION 'private booking worker cannot update protected rows' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.c24_private_worker_reject_update() FROM PUBLIC;
DO $$
DECLARE rel text;
BEGIN
 FOREACH rel IN ARRAY ARRAY[
  'organizations','messages','conversations','contacts','bookings','booking_settings',
  'inbound_worker_receipts','inbound_worker_operations','meta_webhook_inbox','usage_events',
  'simulation_requests','booking_conversation_states','booking_conversation_requests','booking_stays'
 ] LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS c24_private_worker_readonly_update ON public.%I',rel);
  EXECUTE format('CREATE TRIGGER c24_private_worker_readonly_update BEFORE UPDATE ON public.%I '
    'FOR EACH ROW EXECUTE FUNCTION public.c24_private_worker_reject_update()',rel);
 END LOOP;
END $$;

GRANT INSERT, UPDATE ON public.booking_worker_claims TO melpis_c24_booking_worker;
GRANT INSERT ON public.bookings, public.booking_stays, public.booking_allocation_sets,
    public.booking_resource_allocations, public.booking_allocation_requests,
    public.booking_confirmation_outbox, public.messages,
    public.message_delivery_attempts TO melpis_c24_booking_worker;
GRANT UPDATE, DELETE ON public.booking_allocation_sets, public.booking_resource_allocations
    TO melpis_c24_booking_worker;

GRANT EXECUTE ON FUNCTION public.booking_sector_mark_worker_insert(uuid,uuid)
    TO melpis_c24_booking_worker;

-- Booking writes queue Calendar synchronization through a narrow definer
-- trigger; the worker receives no direct credential or sync-outbox privileges.
CREATE OR REPLACE FUNCTION public.calendar_queue_booking() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE mode text; link record; action text;
BEGIN
 IF TG_OP='UPDATE' AND NEW.calendar_revision=OLD.calendar_revision THEN RETURN NEW; END IF;
 SELECT config#>>'{a05,authority}' INTO mode FROM public.booking_settings
  WHERE organization_id=NEW.organization_id;
 IF mode NOT IN ('internal_calendar_sync','google_authoritative') OR mode IS NULL THEN
  IF NEW.stato IN ('cancellata','cancellato','rifiutata','no_show') THEN
   UPDATE public.external_booking_sync SET calendar_action='delete',calendar_desired_revision=NEW.calendar_revision,
    sync_status=CASE WHEN sync_status IN ('processing','uncertain') THEN sync_status ELSE 'pending' END,
    updated_at=NOW() WHERE organization_id=NEW.organization_id AND internal_booking_id=NEW.id AND provider='google';
  ELSE
   UPDATE public.external_booking_sync SET sync_status=CASE WHEN calendar_dispatch_state='possibly_sent'
    THEN 'uncertain' ELSE 'blocked' END,sync_error='authority_changed',updated_at=NOW()
    WHERE organization_id=NEW.organization_id AND internal_booking_id=NEW.id AND provider='google';
  END IF;
  RETURN NEW;
 END IF;
 SELECT connection_epoch,calendar_id,connection_status,sync_enabled INTO link
  FROM public.google_calendar_credentials WHERE organization_id=NEW.organization_id;
 action := CASE WHEN NEW.stato IN ('cancellata','cancellato','rifiutata','no_show') THEN 'delete'
  WHEN NEW.calendar_confirm_pending THEN 'authoritative' ELSE 'upsert' END;
 INSERT INTO public.external_booking_sync(id,organization_id,idempotency_key,internal_booking_id,
  provider,sync_status,calendar_action,calendar_desired_revision,
  calendar_connection_epoch,calendar_id,calendar_authoritative)
 VALUES(gen_random_uuid(),NEW.organization_id,'google-booking:'||NEW.organization_id||':'||NEW.id,NEW.id,
  'google',CASE WHEN link.connection_status='connected' AND link.sync_enabled THEN 'pending' ELSE 'blocked' END,
  action,NEW.calendar_revision,link.connection_epoch,link.calendar_id,mode='google_authoritative')
 ON CONFLICT(organization_id,idempotency_key) DO UPDATE SET
  calendar_action=EXCLUDED.calendar_action,calendar_desired_revision=EXCLUDED.calendar_desired_revision,
  calendar_authoritative=EXCLUDED.calendar_authoritative,
  sync_status=CASE WHEN public.external_booking_sync.sync_status IN ('processing','uncertain')
   THEN public.external_booking_sync.sync_status ELSE EXCLUDED.sync_status END,
  updated_at=NOW();
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.calendar_queue_booking() FROM PUBLIC,anon,authenticated,service_role;

-- Keep ordinary API/service roles outside the dedicated worker capability.
DO $$ BEGIN
 IF pg_has_role('service_role','melpis_c24_booking_worker','MEMBER')
    OR pg_has_role('anon','melpis_c24_booking_worker','MEMBER')
    OR pg_has_role('authenticated','melpis_c24_booking_worker','MEMBER') THEN
  RAISE EXCEPTION 'Application roles must not inherit the private booking worker identity';
 END IF;
END $$;

-- Permit the isolated service login to present worker provenance without
-- transferring table ownership away from the application/database owner.
CREATE OR REPLACE FUNCTION public.booking_allocation_validate_worker_source()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE active_role text;
BEGIN
 IF NEW.actor_kind='inbound_worker' THEN
  active_role := COALESCE(NULLIF(current_setting('role'),'none'),session_user);
  IF active_role IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
       WHERE oid='public.inbound_worker_operations'::regclass))
     AND active_role IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
   RAISE EXCEPTION 'Worker receipt requires dedicated identity' USING ERRCODE='42501';
  END IF;
  PERFORM 1 FROM public.inbound_worker_operations
   WHERE organization_id=NEW.organization_id AND inbox_id=NEW.service_inbox_id
     AND message_id=NEW.service_message_id AND operation_id=NEW.service_operation_id FOR KEY SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Invalid worker source' USING ERRCODE='23503'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_allocation_validate_worker_source() FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.booking_conversation_validate_worker_source()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE active_role text;
BEGIN
 IF NEW.actor_kind='inbound_worker' THEN
  active_role := COALESCE(NULLIF(current_setting('role'),'none'),session_user);
  IF active_role IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
       WHERE oid='public.inbound_worker_operations'::regclass))
     AND active_role IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
   RAISE EXCEPTION 'Worker receipt requires dedicated identity' USING ERRCODE='42501';
  END IF;
  PERFORM 1 FROM public.inbound_worker_receipts r
   JOIN public.meta_webhook_inbox i ON i.id=r.inbox_id AND i.organization_id=r.organization_id
   WHERE r.organization_id=NEW.organization_id AND r.inbox_id=NEW.service_inbox_id
     AND r.message_id=NEW.source_message_id AND i.channel='whatsapp' AND i.status='completed'
   FOR SHARE OF r,i;
  IF NOT FOUND THEN RAISE EXCEPTION 'Invalid worker ingress source' USING ERRCODE='23503'; END IF;
  IF NEW.action='proposal' THEN
   PERFORM 1 FROM public.inbound_worker_operations o
    WHERE o.organization_id=NEW.organization_id AND o.inbox_id=NEW.service_inbox_id
      AND o.message_id=NEW.source_message_id AND o.operation_id=NEW.service_operation_id FOR SHARE;
   IF NOT FOUND THEN RAISE EXCEPTION 'Invalid worker operation source' USING ERRCODE='23503'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_conversation_validate_worker_source() FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.booking_sector_mark_worker_insert(p_org uuid,p_booking uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE active_role text;
BEGIN
 active_role := COALESCE(NULLIF(current_setting('role'),'none'),session_user);
 IF active_role IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
       WHERE oid='public.booking_sector_admission_fences'::regclass))
    AND active_role IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
  RAISE EXCEPTION 'dedicated booking worker required' USING ERRCODE='42501';
 END IF;
 PERFORM public.booking_sector_lock_epoch(p_org);
 PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,884));
 IF NOT EXISTS(SELECT 1 FROM public.booking_sector_admission_fences WHERE organization_id=p_org) THEN
  RAISE EXCEPTION 'admission fence unavailable';
 END IF;
 INSERT INTO public.booking_sector_admission_markers VALUES(txid_current(),p_org,p_booking);
END $$;
REVOKE ALL ON FUNCTION public.booking_sector_mark_worker_insert(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.booking_sector_mark_worker_insert(uuid,uuid) TO melpis_c24_booking_worker;
