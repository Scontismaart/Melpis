-- Establish server-only provenance before quota admission records can be trusted.
-- Application on a remote database requires separate approval. No tenant rows
-- are rewritten. Reapplication deliberately rotates the protocol trust epoch.
DO $migration$
DECLARE
    relation_oid oid;
    relation_name text;
    client_name text;
    backend_oid oid;
    privilege_name text;
    capability_key text;
    capability boolean;
    backend_before jsonb := '{}'::jsonb;
    select_grants jsonb := '[]'::jsonb;
    grant_row record;
    column_row record;
    marker record;
    marker_id constant uuid := '6206d423-cea9-5480-bb9b-59d757503f14';
    privileges text[] := ARRAY['INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'];
BEGIN
    -- Bound lock acquisition without changing the session after this transaction.
    PERFORM set_config('lock_timeout', '5s', true);
    IF current_setting('server_version_num')::int >= 170000 THEN
        privileges := array_append(privileges, 'MAINTAIN');
    END IF;

    -- Take both locks before inspecting ACLs or the reserved control record.
    LOCK TABLE public.messages, public.usage_events IN ACCESS EXCLUSIVE MODE;
    FOREACH relation_name IN ARRAY ARRAY['messages','usage_events'] LOOP
        relation_oid := to_regclass(format('public.%I', relation_name));
        IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = relation_oid) THEN
            RAISE EXCEPTION 'Quota provenance requires existing RLS: %', relation_name;
        END IF;
        -- Save effective backend capabilities, including inherited/PUBLIC grants.
        FOR backend_oid IN
            SELECT relowner FROM pg_class WHERE oid = relation_oid
            UNION SELECT oid FROM pg_roles WHERE rolname = 'service_role'
        LOOP
            FOREACH privilege_name IN ARRAY privileges LOOP
                capability_key := format('%s:%s:%s:table', relation_oid, backend_oid, privilege_name);
                backend_before := backend_before || jsonb_build_object(
                    capability_key, has_table_privilege(backend_oid, relation_oid, privilege_name));
            END LOOP;
            FOR column_row IN SELECT attnum FROM pg_attribute
                WHERE attrelid = relation_oid AND attnum > 0 AND NOT attisdropped
            LOOP
                FOREACH privilege_name IN ARRAY ARRAY['INSERT','UPDATE','REFERENCES'] LOOP
                    capability_key := format('%s:%s:%s:%s', relation_oid, backend_oid,
                                             privilege_name, column_row.attnum);
                    backend_before := backend_before || jsonb_build_object(
                        capability_key, has_column_privilege(backend_oid, relation_oid,
                                                             column_row.attnum, privilege_name));
                END LOOP;
            END LOOP;
        END LOOP;
        -- Preserve existing direct table/column SELECT and its grant option.
        -- REVOKE ALL on a table can also remove column SELECT privileges.
        FOR grant_row IN
            SELECT acl.grantee, acl.is_grantable
            FROM pg_class c CROSS JOIN LATERAL
                aclexplode(coalesce(c.relacl, acldefault('r',c.relowner))) acl
            WHERE c.oid = relation_oid AND acl.privilege_type = 'SELECT'
              AND (acl.grantee = 0 OR acl.grantee IN
                   (SELECT oid FROM pg_roles WHERE rolname IN ('anon','authenticated')))
        LOOP
            select_grants := select_grants || jsonb_build_array(jsonb_build_object(
                'relation', relation_name, 'grantee', grant_row.grantee,
                'grantable', grant_row.is_grantable));
        END LOOP;
        FOR grant_row IN
            SELECT a.attname, acl.grantee, acl.is_grantable
            FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) acl
            WHERE a.attrelid = relation_oid AND a.attnum > 0 AND NOT a.attisdropped
              AND acl.privilege_type = 'SELECT'
              AND (acl.grantee = 0 OR acl.grantee IN
                   (SELECT oid FROM pg_roles WHERE rolname IN ('anon','authenticated')))
        LOOP
            select_grants := select_grants || jsonb_build_array(jsonb_build_object(
                'relation', relation_name, 'column_name', grant_row.attname,
                'grantee', grant_row.grantee, 'grantable', grant_row.is_grantable));
        END LOOP;
        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.%I FROM PUBLIC, anon, authenticated',
                       relation_name);
        -- Explicitly revoke any remaining independent column write grants.
        FOR column_row IN SELECT attname FROM pg_attribute
            WHERE attrelid = relation_oid AND attnum > 0 AND NOT attisdropped
        LOOP
            EXECUTE format('REVOKE INSERT (%1$I), UPDATE (%1$I), REFERENCES (%1$I) '
                           'ON TABLE public.%2$I FROM PUBLIC, anon, authenticated',
                           column_row.attname, relation_name);
        END LOOP;
    END LOOP;

    FOR grant_row IN SELECT * FROM jsonb_to_recordset(select_grants)
        AS grants(relation text, column_name text, grantee oid, grantable boolean)
    LOOP
        client_name := CASE WHEN grant_row.grantee = 0 THEN 'PUBLIC'
            ELSE quote_ident(pg_get_userbyid(grant_row.grantee)) END;
        EXECUTE format('GRANT SELECT%s ON TABLE public.%I TO %s%s',
                       CASE WHEN grant_row.column_name IS NULL THEN ''
                           ELSE format(' (%I)', grant_row.column_name) END, grant_row.relation,
                       client_name, CASE WHEN grant_row.grantable THEN ' WITH GRANT OPTION' ELSE '' END);
    END LOOP;

    FOREACH relation_name IN ARRAY ARRAY['messages','usage_events'] LOOP
        relation_oid := to_regclass(format('public.%I', relation_name));
        FOREACH client_name IN ARRAY ARRAY['anon','authenticated'] LOOP
            FOREACH privilege_name IN ARRAY privileges LOOP
                IF has_table_privilege(client_name, relation_oid, privilege_name) THEN
                    RAISE EXCEPTION 'Residual client table privilege: %.% %',
                                    relation_name, client_name, privilege_name;
                END IF;
            END LOOP;
            FOREACH privilege_name IN ARRAY ARRAY['INSERT','UPDATE','REFERENCES'] LOOP
                IF has_any_column_privilege(client_name, relation_oid, privilege_name) THEN
                    RAISE EXCEPTION 'Residual client column privilege: %.% %',
                                    relation_name, client_name, privilege_name;
                END IF;
            END LOOP;
        END LOOP;
        FOR backend_oid IN
            SELECT relowner FROM pg_class WHERE oid = relation_oid
            UNION SELECT oid FROM pg_roles WHERE rolname = 'service_role'
        LOOP
            FOREACH privilege_name IN ARRAY privileges LOOP
                capability_key := format('%s:%s:%s:table', relation_oid, backend_oid, privilege_name);
                capability := has_table_privilege(backend_oid, relation_oid, privilege_name);
                IF backend_before->capability_key IS DISTINCT FROM to_jsonb(capability) THEN
                    RAISE EXCEPTION 'Backend table capability changed: %', relation_name;
                END IF;
            END LOOP;
            FOR column_row IN SELECT attnum FROM pg_attribute
                WHERE attrelid = relation_oid AND attnum > 0 AND NOT attisdropped
            LOOP
                FOREACH privilege_name IN ARRAY ARRAY['INSERT','UPDATE','REFERENCES'] LOOP
                    capability_key := format('%s:%s:%s:%s', relation_oid, backend_oid,
                                             privilege_name, column_row.attnum);
                    capability := has_column_privilege(backend_oid, relation_oid,
                                                       column_row.attnum, privilege_name);
                    IF backend_before->capability_key IS DISTINCT FROM to_jsonb(capability) THEN
                        RAISE EXCEPTION 'Backend column capability changed: %', relation_name;
                    END IF;
                END LOOP;
            END LOOP;
        END LOOP;
    END LOOP;

    SELECT organization_id, event_type, quantity, metadata INTO marker
        FROM public.usage_events WHERE id = marker_id FOR UPDATE;
    IF FOUND AND (
        marker.organization_id IS NOT NULL OR marker.event_type IS DISTINCT FROM 'system.quota_trust_epoch'
        OR marker.quantity IS DISTINCT FROM 0 OR marker.metadata IS NULL
        OR jsonb_typeof(marker.metadata) IS DISTINCT FROM 'object'
        OR marker.metadata->'protocol_version' IS DISTINCT FROM '1'::jsonb
        OR jsonb_typeof(marker.metadata->'trust_epoch') IS DISTINCT FROM 'string'
        OR coalesce(marker.metadata->>'trust_epoch','') !~
           '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        OR marker.metadata - 'protocol_version' - 'trust_epoch' <> '{}'::jsonb
    ) THEN
        RAISE EXCEPTION 'Reserved quota trust marker collision';
    END IF;
    INSERT INTO public.usage_events(id, organization_id, event_type, quantity, metadata)
        VALUES (marker_id, NULL, 'system.quota_trust_epoch', 0,
                jsonb_build_object('protocol_version', 1, 'trust_epoch', gen_random_uuid()::text))
        ON CONFLICT (id) DO UPDATE SET metadata = EXCLUDED.metadata;
END;
$migration$;
