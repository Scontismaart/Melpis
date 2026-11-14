-- The private worker is dormant by default and has no direct database data
-- privileges. Supabase's ordinary postgres role can create this NOLOGIN role
-- with CREATEROLE; the worker obtains data only through the RPC surface added
-- in migration 093. Never use BYPASSRLS or an application-role membership.
DO $$
DECLARE worker_role oid;
BEGIN
 SELECT oid INTO worker_role FROM pg_roles WHERE rolname='melpis_c24_booking_worker';
 IF worker_role IS NULL THEN
  CREATE ROLE melpis_c24_booking_worker NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB
   NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL;
 ELSE
  -- Supabase's managed postgres login has CREATEROLE but cannot ALTER every
  -- protected role attribute. Never try to rewrite attributes it cannot
  -- grant: verify them first, then fail closed if the existing role is unsafe.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE oid=worker_role AND
      (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
   RAISE EXCEPTION 'existing C2.4 worker role has unsafe privileged attributes';
  END IF;
  ALTER ROLE melpis_c24_booking_worker NOLOGIN NOINHERIT PASSWORD NULL;
 END IF;
 SELECT oid INTO worker_role FROM pg_roles WHERE rolname='melpis_c24_booking_worker';
 IF EXISTS (SELECT 1 FROM pg_roles WHERE oid=worker_role AND
      (rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
    OR EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member=worker_role)
    OR EXISTS (SELECT 1 FROM pg_auth_members m
      JOIN pg_roles member ON member.oid=m.member
      JOIN pg_roles grantor ON grantor.oid=m.grantor
      WHERE m.roleid=worker_role AND NOT (
       member.rolname='postgres' AND grantor.rolname IN ('supabase_admin','postgres')
       AND m.admin_option AND NOT m.inherit_option AND NOT m.set_option))
    OR EXISTS (SELECT 1 FROM pg_class c WHERE c.relowner=worker_role)
    OR EXISTS (SELECT 1 FROM pg_proc p WHERE p.proowner=worker_role)
    OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspowner=worker_role) THEN
  RAISE EXCEPTION 'C2.4 worker identity has an unsafe attribute, membership, or owned object';
 END IF;
END $$;

-- Do not expose public objects to the worker. Subsequent migrations grant no
-- table/view/sequence access; only the isolated c24_private RPC schema is used.
REVOKE ALL ON SCHEMA public FROM melpis_c24_booking_worker;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM melpis_c24_booking_worker;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM melpis_c24_booking_worker;
REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA public FROM melpis_c24_booking_worker;

-- Bind the worker connection to this logical database as well as to its login.
-- The random marker is provisioned per database and cannot be changed later;
-- the database OID is checked with it to reject same-cluster clones.
CREATE TABLE IF NOT EXISTS public.c24_database_instance_identity (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    identity_id uuid NOT NULL DEFAULT gen_random_uuid()
);
ALTER TABLE public.c24_database_instance_identity ENABLE ROW LEVEL SECURITY;
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
REVOKE ALL ON FUNCTION public.c24_database_instance_identity_immutable() FROM PUBLIC;

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
DECLARE owner_role text; active_role text;
BEGIN
 IF NEW.actor_kind='inbound_worker' THEN
  owner_role := pg_get_userbyid((SELECT relowner FROM pg_class
       WHERE oid='public.inbound_worker_operations'::regclass));
  active_role := COALESCE(NULLIF(current_setting('role'), 'none'), session_user);
  IF NOT ((session_user=owner_role AND active_role=owner_role)
       OR (session_user='melpis_c24_booking_worker'
           AND active_role='melpis_c24_booking_worker')) THEN
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
DECLARE owner_role text; active_role text;
BEGIN
 IF NEW.actor_kind='inbound_worker' THEN
  owner_role := pg_get_userbyid((SELECT relowner FROM pg_class
       WHERE oid='public.inbound_worker_operations'::regclass));
  active_role := COALESCE(NULLIF(current_setting('role'), 'none'), session_user);
  IF NOT ((session_user=owner_role AND active_role=owner_role)
       OR (session_user='melpis_c24_booking_worker'
           AND active_role='melpis_c24_booking_worker')) THEN
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
DECLARE owner_role text; active_role text;
BEGIN
 owner_role := pg_get_userbyid((SELECT relowner FROM pg_class
       WHERE oid='public.booking_sector_admission_fences'::regclass));
 active_role := COALESCE(NULLIF(current_setting('role'), 'none'), session_user);
 IF NOT ((session_user=owner_role AND active_role=owner_role)
      OR (session_user='melpis_c24_booking_worker'
          AND active_role='melpis_c24_booking_worker')) THEN
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
