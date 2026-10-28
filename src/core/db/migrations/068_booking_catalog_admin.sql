-- C2.2 administrative catalogue only. No booking V2 activation or remote cutover.
CREATE TABLE IF NOT EXISTS public.booking_catalog_publications (
    organization_id UUID NOT NULL REFERENCES public.booking_settings(organization_id) ON DELETE CASCADE,
    revision BIGINT NOT NULL CHECK (revision > 0),
    catalog JSONB NOT NULL CHECK (jsonb_typeof(catalog) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id, revision)
);
CREATE TABLE IF NOT EXISTS public.booking_catalog_requests (
    organization_id UUID NOT NULL REFERENCES public.booking_settings(organization_id) ON DELETE CASCADE,
    request_id UUID NOT NULL,
    actor_user_id UUID NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('draft_saved', 'published')),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    applied_revision BIGINT NOT NULL CHECK (applied_revision > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id, request_id),
    UNIQUE (organization_id, applied_revision)
);

CREATE OR REPLACE FUNCTION public.booking_catalog_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
    -- This is structural cascade support, NOT a claim that current A06 deletes
    -- booking_settings. Its configuration export/erasure exclusions remain.
    IF TG_OP = 'DELETE' AND NOT EXISTS (
        SELECT 1 FROM public.booking_settings WHERE organization_id=OLD.organization_id
    ) THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'Booking catalogue evidence is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.booking_catalog_immutable() FROM PUBLIC;

DO $$
DECLARE t TEXT; col RECORD; client RECORD; reachable RECORD; target REGCLASS; p TEXT;
BEGIN
    IF EXISTS (SELECT 1 FROM public.booking_settings bs WHERE bs.config ? 'catalog_v2'
        AND NOT EXISTS (SELECT 1 FROM public.booking_catalog_requests cr WHERE cr.organization_id=bs.organization_id)) THEN
        RAISE EXCEPTION 'Historical catalogue namespace requires review';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles backend CROSS JOIN pg_roles client
        WHERE backend.rolname='service_role' AND NOT backend.rolbypassrls AND NOT backend.rolsuper
        AND client.rolname IN ('anon','authenticated') AND pg_has_role(backend.oid,client.oid,'MEMBER')) THEN
        RAISE EXCEPTION 'Backend/client role inheritance requires review';
    END IF;
    FOREACH t IN ARRAY ARRAY['booking_catalog_publications','booking_catalog_requests'] LOOP
        target := ('public.' || t)::regclass;
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target);
        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %s FROM PUBLIC,anon,authenticated',target);
        FOR col IN SELECT attname FROM pg_attribute WHERE attrelid=target
            AND attnum>0 AND NOT attisdropped LOOP
            EXECUTE format('REVOKE ALL PRIVILEGES (%I) ON TABLE %s FROM PUBLIC,anon,authenticated',col.attname,target);
        END LOOP;
        EXECUTE format('DROP POLICY IF EXISTS catalog_deny_clients ON %s',target);
        EXECUTE format('CREATE POLICY catalog_deny_clients ON %s AS RESTRICTIVE FOR ALL TO anon,authenticated USING(false) WITH CHECK(false)',target);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
            EXECUTE format('GRANT SELECT,INSERT ON TABLE %s TO service_role',target);
            EXECUTE format('DROP POLICY IF EXISTS catalog_backend ON %s',target);
            EXECUTE format('CREATE POLICY catalog_backend ON %s FOR ALL TO service_role USING(true) WITH CHECK(true)',target);
        END IF;
        EXECUTE format('DROP TRIGGER IF EXISTS catalog_immutable ON %s',target);
        EXECUTE format('CREATE TRIGGER catalog_immutable BEFORE UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION public.booking_catalog_immutable()',target);
        EXECUTE format('DROP TRIGGER IF EXISTS gdpr_guard_write ON %s',target);
        EXECUTE format('CREATE TRIGGER gdpr_guard_write BEFORE INSERT ON %s FOR EACH ROW EXECUTE FUNCTION public.gdpr_guard_write()',target);
    END LOOP;

    -- Preserve existing member SELECT; no new client read or write grant.
    REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON public.booking_settings
        FROM PUBLIC,anon,authenticated;
    FOR col IN SELECT attname FROM pg_attribute WHERE attrelid='public.booking_settings'::regclass
        AND attnum>0 AND NOT attisdropped LOOP
        EXECUTE format('REVOKE INSERT(%I),UPDATE(%I),REFERENCES(%I) ON public.booking_settings FROM PUBLIC,anon,authenticated',col.attname,col.attname,col.attname);
    END LOOP;
    DROP POLICY IF EXISTS catalog_no_client_insert ON public.booking_settings;
    CREATE POLICY catalog_no_client_insert ON public.booking_settings AS RESTRICTIVE
        FOR INSERT TO anon,authenticated WITH CHECK(false);
    DROP POLICY IF EXISTS catalog_no_client_update ON public.booking_settings;
    CREATE POLICY catalog_no_client_update ON public.booking_settings AS RESTRICTIVE
        FOR UPDATE TO anon,authenticated USING(false) WITH CHECK(false);
    DROP POLICY IF EXISTS catalog_no_client_delete ON public.booking_settings;
    CREATE POLICY catalog_no_client_delete ON public.booking_settings AS RESTRICTIVE
        FOR DELETE TO anon,authenticated USING(false);

    -- Do not silently change unrelated inherited grants or role memberships.
    FOR client IN SELECT oid FROM pg_roles WHERE rolname IN ('anon','authenticated') LOOP
        FOR reachable IN SELECT r.* FROM pg_roles r
            WHERE r.oid=client.oid OR pg_has_role(client.oid,r.oid,'MEMBER') LOOP
            IF reachable.rolsuper OR reachable.rolbypassrls THEN
                RAISE EXCEPTION 'Privileged client role path requires review';
            END IF;
            FOREACH t IN ARRAY ARRAY['booking_catalog_publications','booking_catalog_requests','booking_settings'] LOOP
                target := ('public.'||t)::regclass;
                IF reachable.oid=(SELECT relowner FROM pg_class WHERE oid=target) THEN
                    RAISE EXCEPTION 'Client can assume catalogue table owner';
                END IF;
                FOREACH p IN ARRAY ARRAY['INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
                    IF has_table_privilege(reachable.oid,target,p) THEN
                        RAISE EXCEPTION 'Inherited client catalogue write requires review';
                    END IF;
                END LOOP;
                FOREACH p IN ARRAY ARRAY['INSERT','UPDATE','REFERENCES'] LOOP
                    IF has_any_column_privilege(reachable.oid,target,p) THEN
                        RAISE EXCEPTION 'Inherited client catalogue column write requires review';
                    END IF;
                END LOOP;
                IF t <> 'booking_settings' AND has_any_column_privilege(reachable.oid,target,'SELECT') THEN
                    RAISE EXCEPTION 'Inherited client catalogue evidence read requires review';
                END IF;
            END LOOP;
        END LOOP;
    END LOOP;
END $$;
