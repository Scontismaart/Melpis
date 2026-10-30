-- Dark allocation schema only: no runtime admission or writer integration.
CREATE EXTENSION IF NOT EXISTS btree_gist;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.bookings'::regclass AND conname='bookings_org_id_unique') THEN
  ALTER TABLE public.bookings ADD CONSTRAINT bookings_org_id_unique UNIQUE (organization_id,id);
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.booking_allocation_sets (
 organization_id uuid NOT NULL, booking_id uuid NOT NULL,
 allocation_revision bigint NOT NULL CHECK(allocation_revision>0), state text NOT NULL CHECK(state IN ('active','cancelled')),
 catalog_revision bigint NOT NULL CHECK(catalog_revision>0), service_id text NOT NULL CHECK(service_id ~ '^[a-z][a-z0-9_-]{0,63}$'), timezone text NOT NULL CHECK(length(timezone)>0),
 temporal_kind text NOT NULL CHECK(temporal_kind IN ('minutes','civil_nights')),
 starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL, arrival_date date, departure_date date,
 booking_fingerprint text NOT NULL CHECK(booking_fingerprint ~ '^[a-f0-9]{64}$'), booking_calendar_revision bigint NOT NULL CHECK(booking_calendar_revision>=0),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,booking_id),
 FOREIGN KEY(organization_id,booking_id) REFERENCES public.bookings(organization_id,id) ON DELETE CASCADE,
 FOREIGN KEY(organization_id,catalog_revision) REFERENCES public.booking_catalog_publications(organization_id,revision) ON DELETE RESTRICT,
 UNIQUE(organization_id,booking_id,state,allocation_revision,catalog_revision,starts_at,ends_at),
 CHECK(isfinite(starts_at) AND isfinite(ends_at) AND starts_at<ends_at AND date_trunc('minute',starts_at)=starts_at AND date_trunc('minute',ends_at)=ends_at),
 CHECK((temporal_kind='minutes' AND arrival_date IS NULL AND departure_date IS NULL) OR (temporal_kind='civil_nights' AND arrival_date IS NOT NULL AND departure_date IS NOT NULL AND isfinite(arrival_date) AND isfinite(departure_date) AND arrival_date<departure_date))
);
CREATE TABLE IF NOT EXISTS public.booking_resource_allocations (
 organization_id uuid NOT NULL, booking_id uuid NOT NULL, allocation_revision bigint NOT NULL CHECK(allocation_revision>0), catalog_revision bigint NOT NULL CHECK(catalog_revision>0),
 state text NOT NULL DEFAULT 'active' CHECK(state='active'), resource_id text NOT NULL CHECK(resource_id ~ '^[a-z][a-z0-9_-]{0,63}$'),
 resource_kind text NOT NULL CHECK(resource_kind IN ('operator','space','table','seat_pool','room','equipment')), exclusive boolean NOT NULL,
 quantity bigint NOT NULL CHECK(quantity>0 AND (NOT exclusive OR quantity=1)), starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
 PRIMARY KEY(organization_id,booking_id,resource_id),
 FOREIGN KEY(organization_id,booking_id,state,allocation_revision,catalog_revision,starts_at,ends_at) REFERENCES public.booking_allocation_sets(organization_id,booking_id,state,allocation_revision,catalog_revision,starts_at,ends_at) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
 CHECK(isfinite(starts_at) AND isfinite(ends_at) AND starts_at<ends_at AND date_trunc('minute',starts_at)=starts_at AND date_trunc('minute',ends_at)=ends_at),
 EXCLUDE USING gist (organization_id WITH =, resource_id WITH =, tstzrange(starts_at,ends_at,'[)') WITH &&) WHERE (exclusive)
);
CREATE INDEX IF NOT EXISTS booking_resource_allocations_lookup ON public.booking_resource_allocations(organization_id,resource_id,starts_at,ends_at);
CREATE TABLE IF NOT EXISTS public.booking_allocation_requests (
 organization_id uuid NOT NULL, request_id uuid NOT NULL, booking_id uuid NOT NULL, actor_user_id uuid NOT NULL,
 action text NOT NULL CHECK(action IN ('allocate','replace','cancel')), payload_hash text NOT NULL CHECK(payload_hash ~ '^[a-f0-9]{64}$'),
 outcome text NOT NULL CHECK(outcome IN ('allocated','replaced','cancelled','unavailable','unknown')),
 applied_allocation_revision bigint CHECK(applied_allocation_revision>0), catalog_revision bigint NOT NULL CHECK(catalog_revision>0), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,request_id),
 FOREIGN KEY(organization_id,booking_id) REFERENCES public.bookings(organization_id,id) ON DELETE CASCADE,
 FOREIGN KEY(organization_id,catalog_revision) REFERENCES public.booking_catalog_publications(organization_id,revision) ON DELETE RESTRICT,
 CHECK((outcome IN ('unavailable','unknown') AND applied_allocation_revision IS NULL) OR (applied_allocation_revision IS NOT NULL AND ((action='allocate' AND outcome='allocated') OR (action='replace' AND outcome='replaced') OR (action='cancel' AND outcome='cancelled'))))
);
CREATE OR REPLACE FUNCTION public.booking_allocation_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE direct_subject uuid; conversation_subject uuid; subject uuid; conversation_text text; conversation_uuid uuid;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,606));
 SELECT contact_id,id_conversazione INTO direct_subject,conversation_text FROM bookings WHERE organization_id=NEW.organization_id AND id=NEW.booking_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Booking scope invalid' USING ERRCODE='55000'; END IF;
 IF conversation_text IS NOT NULL AND conversation_text<>'' THEN
  BEGIN conversation_uuid := conversation_text::uuid; EXCEPTION WHEN invalid_text_representation THEN RAISE EXCEPTION 'Invalid conversation lineage' USING ERRCODE='55000'; END;
  SELECT contact_id INTO conversation_subject FROM conversations WHERE organization_id=NEW.organization_id AND id=conversation_uuid AND deleted_at IS NULL;
  IF NOT FOUND OR conversation_subject IS NULL THEN RAISE EXCEPTION 'Invalid conversation lineage' USING ERRCODE='55000'; END IF;
 END IF;
 IF direct_subject IS NOT NULL AND conversation_subject IS NOT NULL AND direct_subject<>conversation_subject THEN RAISE EXCEPTION 'Contradictory lineage' USING ERRCODE='55000'; END IF;
 subject := COALESCE(direct_subject,conversation_subject);
 IF subject IS NOT NULL AND NOT EXISTS (SELECT 1 FROM contacts WHERE organization_id=NEW.organization_id AND id=subject AND deleted_at IS NULL) THEN RAISE EXCEPTION 'Invalid subject lineage' USING ERRCODE='55000'; END IF;
 IF EXISTS (SELECT 1 FROM gdpr_erasure_barriers WHERE organization_id=NEW.organization_id AND (scope='organization' OR subject_id=subject OR subject IS NULL)) THEN RAISE EXCEPTION 'GDPR barrier rejects allocation' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_allocation_guard() FROM PUBLIC;
CREATE OR REPLACE FUNCTION public.booking_allocation_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
 IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM bookings WHERE organization_id=OLD.organization_id AND id=OLD.booking_id) THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'Allocation receipt is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.booking_allocation_immutable() FROM PUBLIC;
DO $$
DECLARE t text; c record; client record; reachable record; target regclass; p text;
BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles b CROSS JOIN pg_roles client_role WHERE b.rolname='service_role' AND NOT b.rolbypassrls AND NOT b.rolsuper AND client_role.rolname IN ('anon','authenticated') AND pg_has_role(b.oid,client_role.oid,'MEMBER')) THEN RAISE EXCEPTION 'Backend/client inheritance requires review'; END IF;
 FOREACH t IN ARRAY ARRAY['booking_allocation_sets','booking_resource_allocations','booking_allocation_requests'] LOOP
  target := ('public.'||t)::regclass;
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY',target);
  EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %s FROM PUBLIC,anon,authenticated,service_role',target);
  FOR c IN SELECT attname FROM pg_attribute WHERE attrelid=target AND attnum>0 AND NOT attisdropped LOOP
   EXECUTE format('REVOKE ALL PRIVILEGES (%I) ON TABLE %s FROM PUBLIC,anon,authenticated,service_role',c.attname,target);
  END LOOP;
  EXECUTE format('DROP POLICY IF EXISTS allocation_deny_clients ON %s',target);
  EXECUTE format('CREATE POLICY allocation_deny_clients ON %s AS RESTRICTIVE FOR ALL TO anon,authenticated USING(false) WITH CHECK(false)',target);
  EXECUTE format('DROP POLICY IF EXISTS allocation_backend ON %s',target);
  EXECUTE format('CREATE POLICY allocation_backend ON %s FOR ALL TO service_role USING(true) WITH CHECK(true)',target);
  IF t='booking_allocation_requests' THEN EXECUTE format('GRANT SELECT,INSERT ON %s TO service_role',target);
  ELSE EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON %s TO service_role',target); END IF;
  EXECUTE format('DROP TRIGGER IF EXISTS allocation_guard ON %s',target);
  EXECUTE format('CREATE TRIGGER allocation_guard BEFORE INSERT OR UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION public.booking_allocation_guard()',target);
  FOR client IN SELECT oid FROM pg_roles WHERE rolname IN ('anon','authenticated') LOOP
   FOR reachable IN SELECT r.* FROM pg_roles r WHERE r.oid=client.oid OR pg_has_role(client.oid,r.oid,'SET') LOOP
    IF reachable.rolsuper OR reachable.rolbypassrls OR reachable.oid=(SELECT relowner FROM pg_class WHERE oid=target) THEN RAISE EXCEPTION 'Privileged client role path requires review'; END IF;
    FOREACH p IN ARRAY ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
     IF has_table_privilege(reachable.oid,target,p) OR (p IN ('SELECT','INSERT','UPDATE','REFERENCES') AND has_any_column_privilege(reachable.oid,target,p)) THEN RAISE EXCEPTION 'Reachable client privilege requires review'; END IF;
    END LOOP;
   END LOOP;
   FOREACH p IN ARRAY ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
    IF has_table_privilege(client.oid,target,p) OR (p IN ('SELECT','INSERT','UPDATE','REFERENCES') AND has_any_column_privilege(client.oid,target,p)) THEN RAISE EXCEPTION 'Inherited client privilege requires review'; END IF;
   END LOOP;
  END LOOP;
  FOR reachable IN SELECT r.* FROM pg_roles r WHERE r.oid=(SELECT oid FROM pg_roles WHERE rolname='service_role') OR pg_has_role('service_role',r.oid,'SET') LOOP
   -- A migration administrator may own tables; an application backend may not.
   -- Direct BYPASSRLS is expected for Supabase service_role, not ownership/super.
   IF reachable.rolsuper OR reachable.oid=(SELECT relowner FROM pg_class WHERE oid=target)
      OR (reachable.oid<>(SELECT oid FROM pg_roles WHERE rolname='service_role') AND reachable.rolbypassrls)
   THEN RAISE EXCEPTION 'Privileged backend role path requires review'; END IF;
   FOREACH p IN ARRAY CASE WHEN t='booking_allocation_requests' THEN ARRAY['UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] ELSE ARRAY['TRUNCATE','REFERENCES','TRIGGER'] END LOOP
    IF reachable.oid<>(SELECT relowner FROM pg_class WHERE oid=target) AND (has_table_privilege(reachable.oid,target,p) OR (p IN ('UPDATE','REFERENCES') AND has_any_column_privilege(reachable.oid,target,p))) THEN RAISE EXCEPTION 'Reachable backend privilege requires review'; END IF;
   END LOOP;
  END LOOP;
  IF t='booking_allocation_requests' THEN
   FOREACH p IN ARRAY ARRAY['UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
    IF (SELECT oid FROM pg_roles WHERE rolname='service_role')<>(SELECT relowner FROM pg_class WHERE oid=target) AND (has_table_privilege('service_role',target,p) OR (p IN ('UPDATE','REFERENCES') AND has_any_column_privilege('service_role',target,p))) THEN RAISE EXCEPTION 'Inherited backend receipt privilege requires review'; END IF;
   END LOOP;
  END IF;
  FOREACH p IN ARRAY ARRAY['TRUNCATE','REFERENCES','TRIGGER'] LOOP
   IF (SELECT oid FROM pg_roles WHERE rolname='service_role')<>(SELECT relowner FROM pg_class WHERE oid=target) AND (has_table_privilege('service_role',target,p) OR (p='REFERENCES' AND has_any_column_privilege('service_role',target,p))) THEN RAISE EXCEPTION 'Inherited backend privilege requires review'; END IF;
  END LOOP;
 END LOOP;
 DROP TRIGGER IF EXISTS allocation_immutable ON public.booking_allocation_requests;
 CREATE TRIGGER allocation_immutable BEFORE UPDATE OR DELETE ON public.booking_allocation_requests FOR EACH ROW EXECUTE FUNCTION public.booking_allocation_immutable();
 DROP TRIGGER IF EXISTS allocation_no_truncate ON public.booking_allocation_requests;
 CREATE TRIGGER allocation_no_truncate BEFORE TRUNCATE ON public.booking_allocation_requests FOR EACH STATEMENT EXECUTE FUNCTION public.booking_catalog_no_truncate();
END $$;



