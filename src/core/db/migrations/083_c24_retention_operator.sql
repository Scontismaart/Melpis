-- Dedicated, explicitly provisioned retention operator; no application role gains access.
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
   OR pg_has_role('authenticated',retention_role,'MEMBER') THEN
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
   AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls)
   AND pg_has_role(r.oid,'melpis_c24_retention','USAGE')
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
      AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))))
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
