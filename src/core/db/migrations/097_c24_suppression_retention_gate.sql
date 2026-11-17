-- Containment only: preserves replay fences until their approved retention anchor is provable.
-- Sweep will report deferred targets; this does not meet a retention deadline.
CREATE OR REPLACE FUNCTION public.c24_cleanup_suppression(p_org uuid,p_conversation uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE capability_owner name;
BEGIN
 SELECT pg_get_userbyid(relowner) INTO capability_owner FROM pg_class
  WHERE oid='public.c24_suppression_cleanup_capability'::regclass;
 IF current_user IS DISTINCT FROM capability_owner THEN
  RAISE EXCEPTION 'Suppression cleanup requires the lifecycle owner' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'Suppression cleanup requires read committed isolation' USING ERRCODE='25000';
 END IF;
 -- Fail closed until retention timestamps are mapped to request conclusion.
 -- Keep caller/owner/isolation checks above and preserve the separate erasure trigger.
 RETURN false;

END $$;
REVOKE ALL ON FUNCTION public.c24_cleanup_suppression(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.c24_cleanup_suppression_audit(p_org uuid,p_audit uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE capability_owner name;
BEGIN
 SELECT pg_get_userbyid(relowner) INTO capability_owner FROM pg_class
  WHERE oid='public.c24_suppression_audit_cleanup_capability'::regclass;
 IF current_user IS DISTINCT FROM capability_owner THEN
  RAISE EXCEPTION 'Suppression audit cleanup requires the lifecycle owner' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'Suppression audit cleanup requires read committed isolation' USING ERRCODE='25000';
 END IF;
 -- Fail closed until retention timestamps are mapped to request conclusion.
 -- Keep caller/owner/isolation checks above and preserve the separate erasure trigger.
 RETURN false;

END $$;
REVOKE ALL ON FUNCTION public.c24_cleanup_suppression_audit(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;

