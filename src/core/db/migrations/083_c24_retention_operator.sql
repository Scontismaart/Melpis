-- Dedicated, explicitly provisioned retention operator; no application role gains access.
-- Supabase grants PUBLIC SELECT on these monitoring views by default. Retention
-- operators must not inherit cross-tenant query telemetry; preserve owner and
-- explicit dashboard grants while removing only PUBLIC SELECT.
DO $monitoring_acl$
DECLARE
    extension_oid oid;
    relation_name text;
    relation_oid oid;
BEGIN
    SELECT e.oid INTO extension_oid
      FROM pg_extension e
      JOIN pg_namespace n ON n.oid=e.extnamespace
     WHERE e.extname='pg_stat_statements' AND n.nspname='extensions';
    IF NOT FOUND THEN
        RETURN;
    END IF;

    FOREACH relation_name IN ARRAY ARRAY['pg_stat_statements','pg_stat_statements_info'] LOOP
        relation_oid := to_regclass(format('extensions.%I',relation_name));
        IF relation_oid IS NULL THEN
            CONTINUE;
        END IF;
        IF NOT EXISTS (
            SELECT 1
              FROM pg_class c
              JOIN pg_depend d ON d.classid='pg_class'::regclass AND d.objid=c.oid
             WHERE c.oid=relation_oid
               AND c.relkind='v'
               AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
               AND d.refclassid='pg_extension'::regclass
               AND d.refobjid=extension_oid
               AND d.deptype='e'
        ) THEN
            RAISE EXCEPTION 'Unexpected pg_stat_statements extension object or owner';
        END IF;
        EXECUTE format('REVOKE SELECT ON TABLE extensions.%I FROM PUBLIC',relation_name);
    END LOOP;
END
$monitoring_acl$;

DO $$
DECLARE retention_role oid;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='melpis_c24_retention') THEN
  CREATE ROLE melpis_c24_retention NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 SELECT oid INTO STRICT retention_role FROM pg_roles WHERE rolname='melpis_c24_retention';
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='melpis_c24_retention' AND
   (rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
   OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname='melpis_c24_retention')
   OR pg_has_role('service_role',retention_role,'MEMBER')
   OR pg_has_role('anon',retention_role,'MEMBER')
   OR pg_has_role('authenticated',retention_role,'MEMBER')
   OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
      AND has_schema_privilege(retention_role,n.oid,'CREATE'))
   OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f')
       AND (has_table_privilege(retention_role,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
         OR has_any_column_privilege(retention_role,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
   OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND p.prosecdef
       AND NOT ((p.proname='c24_retention_operator_allowed'
                 AND pg_get_function_identity_arguments(p.oid)='')
                OR (p.proname='c24_run_retention_target'
                 AND pg_get_function_identity_arguments(p.oid)='p_kind text, p_org uuid, p_id uuid'))
       AND has_function_privilege(retention_role,p.oid,'EXECUTE')) THEN
  RAISE EXCEPTION 'Retention role must be isolated';
 END IF;
 IF current_user <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='public.c24_receipt_cleanup_capability'::regclass)) THEN
  RAISE EXCEPTION 'Retention wrapper must be installed by the lifecycle owner';
 END IF;
END $$;

CREATE OR REPLACE FUNCTION public.c24_retention_operator_allowed() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT EXISTS (SELECT 1 FROM pg_roles r
  WHERE r.rolname=COALESCE(NULLIF(current_setting('role'),'none'),session_user)
   AND session_user <> 'service_role'
   AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls)
   AND pg_has_role(r.oid,'melpis_c24_retention','USAGE')
   AND pg_has_role(session_user,'melpis_c24_retention','MEMBER')
   AND NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
    AND has_schema_privilege(session_user,n.oid,'CREATE'))
   AND NOT EXISTS (SELECT 1 FROM pg_roles granted
    WHERE granted.oid <> session_user::regrole AND granted.oid <> 'melpis_c24_retention'::regrole
      AND pg_has_role(session_user,granted.oid,'MEMBER'))
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f')
      AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
   AND NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND p.prosecdef
      AND p.oid IS DISTINCT FROM to_regprocedure('public.c24_retention_operator_allowed()')
      AND p.oid IS DISTINCT FROM to_regprocedure('public.c24_run_retention_target(text,uuid,uuid)')
      AND has_function_privilege(session_user,p.oid,'EXECUTE')))
$$;
REVOKE ALL ON FUNCTION public.c24_retention_operator_allowed() FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.c24_retention_operator_allowed() TO melpis_c24_retention;

CREATE OR REPLACE FUNCTION public.c24_run_retention_target(p_kind text,p_org uuid,p_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.c24_retention_operator_allowed() THEN
  RAISE EXCEPTION 'Dedicated retention operator required' USING ERRCODE='42501';
 END IF;
 CASE p_kind
  WHEN 'message' THEN RETURN public.c24_purge_source(p_org,p_id);
  WHEN 'conversation' THEN RETURN public.c24_expire_idle(p_org,p_id);
  WHEN 'receipt' THEN RETURN public.c24_cleanup_receipt(p_org,p_id);
  ELSE RAISE EXCEPTION 'Invalid retention target kind' USING ERRCODE='22023';
 END CASE;
END $$;
REVOKE ALL ON FUNCTION public.c24_run_retention_target(text,uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
GRANT USAGE ON SCHEMA public TO melpis_c24_retention;
GRANT EXECUTE ON FUNCTION public.c24_run_retention_target(text,uuid,uuid) TO melpis_c24_retention;
DO $$
BEGIN
 IF pg_get_userbyid((SELECT proowner FROM pg_proc WHERE oid='public.c24_run_retention_target(text,uuid,uuid)'::regprocedure))
    <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='public.c24_receipt_cleanup_capability'::regclass)) THEN
  RAISE EXCEPTION 'Retention wrapper owner differs from lifecycle owner';
 END IF;
END $$;
