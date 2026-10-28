-- Booking credentials/config are backend-only, including encrypted blobs.
-- Local candidate: remote application requires a separate approved cutover.
-- No schema-wide/default privilege changes and no new backend capabilities.
DO $migration$
DECLARE
    relation_oid oid := 'public.external_booking_credentials'::regclass;
    owner_oid oid;
    role_row record;
    column_row record;
    backend_oid oid;
    privilege_name text;
    capability_key text;
    before_capabilities jsonb := '{}'::jsonb;
    table_privileges text[] := ARRAY['SELECT','INSERT','UPDATE','DELETE',
                                    'TRUNCATE','REFERENCES','TRIGGER'];
BEGIN
    PERFORM set_config('lock_timeout', '5s', true);
    LOCK TABLE public.external_booking_credentials IN ACCESS EXCLUSIVE MODE;
    SELECT relowner INTO owner_oid FROM pg_class WHERE oid = relation_oid;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = relation_oid) THEN
        RAISE EXCEPTION 'Booking credential hardening requires existing RLS';
    END IF;
    IF current_setting('server_version_num')::int >= 170000 THEN
        table_privileges := array_append(table_privileges, 'MAINTAIN');
    END IF;

    -- Preserve the existing owner/operational role contract, not an assumed
    -- service_role deployment. API and scheduler use DATABASE_URL via asyncpg.
    FOR backend_oid IN
        SELECT owner_oid UNION SELECT oid FROM pg_roles WHERE rolname = 'service_role'
    LOOP
        IF backend_oid <> owner_oid
           AND NOT (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE oid = backend_oid)
           AND (has_table_privilege(backend_oid, relation_oid, 'SELECT,INSERT,UPDATE,DELETE')
                OR has_any_column_privilege(backend_oid, relation_oid, 'SELECT,INSERT,UPDATE')) THEN
            RAISE EXCEPTION 'Backend row access requires role-contract review before cutover';
        END IF;
        FOREACH privilege_name IN ARRAY table_privileges LOOP
            capability_key := format('%s:table:%s', backend_oid, privilege_name);
            before_capabilities := before_capabilities || jsonb_build_object(
                capability_key, has_table_privilege(backend_oid, relation_oid, privilege_name));
        END LOOP;
        FOR column_row IN SELECT attnum FROM pg_attribute
            WHERE attrelid = relation_oid AND attnum > 0 AND NOT attisdropped
        LOOP
            FOREACH privilege_name IN ARRAY ARRAY['SELECT','INSERT','UPDATE','REFERENCES'] LOOP
                capability_key := format('%s:%s:%s', backend_oid, column_row.attnum, privilege_name);
                before_capabilities := before_capabilities || jsonb_build_object(
                    capability_key, has_column_privilege(backend_oid, relation_oid,
                                                         column_row.attnum, privilege_name));
            END LOOP;
        END LOOP;
    END LOOP;

    REVOKE ALL PRIVILEGES ON TABLE public.external_booking_credentials
        FROM PUBLIC, anon, authenticated;
    -- Column grants are independent: remove reads as well as writes/references.
    FOR column_row IN SELECT attname FROM pg_attribute
        WHERE attrelid = relation_oid AND attnum > 0 AND NOT attisdropped
    LOOP
        EXECUTE format('REVOKE ALL PRIVILEGES (%I) ON TABLE '
                       'public.external_booking_credentials FROM PUBLIC, anon, authenticated',
                       column_row.attname);
    END LOOP;

    -- Membership in another role may retain privileges, or permit SET ROLE.
    -- Abort atomically instead of changing unrelated role grants/relationships.
    FOR role_row IN
        SELECT DISTINCT r.oid, r.rolname, r.rolsuper, r.rolbypassrls
        FROM pg_roles r CROSS JOIN pg_roles client
        WHERE client.rolname IN ('anon','authenticated')
          AND (r.oid = client.oid OR pg_has_role(client.oid, r.oid, 'MEMBER'))
    LOOP
        IF role_row.rolsuper OR role_row.rolbypassrls OR role_row.oid = owner_oid THEN
            RAISE EXCEPTION 'Privileged client role relationship requires review: %', role_row.rolname;
        END IF;
        FOREACH privilege_name IN ARRAY table_privileges LOOP
            IF has_table_privilege(role_row.oid, relation_oid, privilege_name) THEN
                RAISE EXCEPTION 'Residual client table privilege requires review: % %',
                                role_row.rolname, privilege_name;
            END IF;
        END LOOP;
        FOREACH privilege_name IN ARRAY ARRAY['SELECT','INSERT','UPDATE','REFERENCES'] LOOP
            IF has_any_column_privilege(role_row.oid, relation_oid, privilege_name) THEN
                RAISE EXCEPTION 'Residual client column privilege requires review: % %',
                                role_row.rolname, privilege_name;
            END IF;
        END LOOP;
    END LOOP;

    DROP POLICY IF EXISTS external_booking_credentials_org_member
        ON public.external_booking_credentials;
    DROP POLICY IF EXISTS external_booking_credentials_deny_clients
        ON public.external_booking_credentials;
    CREATE POLICY external_booking_credentials_deny_clients
        ON public.external_booking_credentials AS RESTRICTIVE FOR ALL
        TO anon, authenticated USING (false) WITH CHECK (false);

    FOR backend_oid IN
        SELECT owner_oid UNION SELECT oid FROM pg_roles WHERE rolname = 'service_role'
    LOOP
        FOREACH privilege_name IN ARRAY table_privileges LOOP
            capability_key := format('%s:table:%s', backend_oid, privilege_name);
            IF before_capabilities->capability_key IS DISTINCT FROM
               to_jsonb(has_table_privilege(backend_oid, relation_oid, privilege_name)) THEN
                RAISE EXCEPTION 'Backend table capability changed; review before cutover';
            END IF;
        END LOOP;
        FOR column_row IN SELECT attnum FROM pg_attribute
            WHERE attrelid = relation_oid AND attnum > 0 AND NOT attisdropped
        LOOP
            FOREACH privilege_name IN ARRAY ARRAY['SELECT','INSERT','UPDATE','REFERENCES'] LOOP
                capability_key := format('%s:%s:%s', backend_oid, column_row.attnum, privilege_name);
                IF before_capabilities->capability_key IS DISTINCT FROM
                   to_jsonb(has_column_privilege(backend_oid, relation_oid,
                                                column_row.attnum, privilege_name)) THEN
                    RAISE EXCEPTION 'Backend column capability changed; review before cutover';
                END IF;
            END LOOP;
        END LOOP;
    END LOOP;
END
$migration$;
