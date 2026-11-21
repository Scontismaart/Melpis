SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Organization erasure retains the organization row for accounting and audit.
-- Guard every subsequent membership grant with the existing tenant barrier.
CREATE OR REPLACE FUNCTION public.gdpr_guard_tenant_membership()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Tenant membership grants require read committed' USING ERRCODE='25000';
    END IF;
    -- UPDATE may already hold the membership row lock needed by erasure.
    -- Fail with a retryable lock error instead of waiting in reverse order.
    IF NOT pg_try_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,606)) THEN
        RAISE EXCEPTION 'Tenant membership scope is busy' USING ERRCODE='55P03';
    END IF;
    IF EXISTS (SELECT FROM public.gdpr_erasure_barriers
               WHERE organization_id=NEW.organization_id AND scope='organization') THEN
        RAISE EXCEPTION 'GDPR erasure barrier rejects tenant membership' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.gdpr_guard_tenant_membership()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
DROP TRIGGER IF EXISTS gdpr_guard_write ON public.organization_memberships;
CREATE TRIGGER gdpr_guard_write BEFORE INSERT OR UPDATE ON public.organization_memberships
    FOR EACH ROW EXECUTE FUNCTION public.gdpr_guard_tenant_membership();

-- A pre-114 barrier may have stale grants awaiting controller review. Deny
-- their use immediately without purging historical evidence on migration.
-- The caller can learn only whether their own tenant membership is active.
CREATE OR REPLACE FUNCTION public.current_tenant_membership_not_erased(p_org uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.organization_memberships m
        JOIN public.user_profiles p ON p.id=m.user_id
        WHERE m.organization_id=p_org AND p.auth_user_id=(SELECT auth.uid()))
      AND NOT EXISTS(SELECT 1 FROM public.gdpr_erasure_barriers b
        WHERE b.organization_id=p_org AND b.scope='organization');
$$;
REVOKE ALL ON FUNCTION public.current_tenant_membership_not_erased(uuid)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
GRANT EXECUTE ON FUNCTION public.current_tenant_membership_not_erased(uuid) TO authenticated;
DROP POLICY IF EXISTS memberships_organization_erasure_gate ON public.organization_memberships;
CREATE POLICY memberships_organization_erasure_gate ON public.organization_memberships AS RESTRICTIVE
    FOR SELECT TO authenticated USING (public.current_tenant_membership_not_erased(organization_id));

-- The hosted REST roles also have direct SELECT grants. Apply the same
-- ordinary visibility gate to source messages and their client-visible copies.
DROP POLICY IF EXISTS messages_verified_visibility ON public.messages;
CREATE POLICY messages_verified_visibility ON public.messages AS RESTRICTIVE
    FOR SELECT TO authenticated,anon USING (
        deleted_at IS NULL AND c24_received_at > statement_timestamp() - interval '60 days'
        AND c24_received_at <= clock_timestamp()
        AND EXISTS (SELECT FROM public.conversations c
            JOIN public.contacts ct ON ct.organization_id=c.organization_id AND ct.id=c.contact_id
            WHERE c.organization_id=messages.organization_id AND c.id=messages.conversation_id
              AND ct.id=messages.c24_original_subject_id));

DROP POLICY IF EXISTS event_log_verified_message_visibility ON public.event_log;
CREATE POLICY event_log_verified_message_visibility ON public.event_log AS RESTRICTIVE
    FOR SELECT TO authenticated,anon USING (
        source_table <> 'messages' OR EXISTS (SELECT FROM public.messages m
            WHERE m.organization_id=event_log.organization_id AND m.id=event_log.source_id));

DROP POLICY IF EXISTS feedback_verified_message_visibility ON public.message_feedback;
CREATE POLICY feedback_verified_message_visibility ON public.message_feedback AS RESTRICTIVE
    FOR SELECT TO authenticated,anon USING (EXISTS (SELECT FROM public.messages m
        WHERE m.organization_id=message_feedback.organization_id AND m.id=message_feedback.message_id
          AND m.conversation_id=message_feedback.conversation_id));

CREATE OR REPLACE FUNCTION public.current_auth_access_allowed()
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.auth_access_lifecycle
        WHERE auth_user_id=(SELECT auth.uid()) AND disabled_at IS NULL
          AND (provisioned_at IS NULL OR EXISTS (
              SELECT 1 FROM public.organization_memberships om
              JOIN public.user_profiles up ON up.id=om.user_id
              WHERE up.auth_user_id=(SELECT auth.uid()) AND NOT EXISTS (
                  SELECT 1 FROM public.gdpr_erasure_barriers b
                  WHERE b.organization_id=om.organization_id AND b.scope='organization'
              )
          ))
    );
$$;
REVOKE ALL ON FUNCTION public.current_auth_access_allowed() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.current_auth_access_allowed() TO authenticated;

-- A legacy committed barrier may predate membership cleanup. This exact
-- reconciliation is intentionally not run by the migration. It can be called
-- by the authorized erasure/recovery operator, after rechecking current policy.
CREATE OR REPLACE FUNCTION public.gdpr_reconcile_erased_tenant_access(p_org uuid, p_operation uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Tenant access reconciliation requires read committed' USING ERRCODE='25000';
    END IF;
    PERFORM 1 FROM public.organizations WHERE id=p_org FOR NO KEY UPDATE;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text,606));
    IF NOT EXISTS (
        SELECT 1 FROM public.gdpr_operations o
        JOIN public.gdpr_operation_steps s ON s.organization_id=o.organization_id AND s.operation_id=o.id
        JOIN public.gdpr_erasure_barriers b ON b.organization_id=o.organization_id AND b.operation_id=o.id
        WHERE o.organization_id=p_org AND o.id=p_operation AND o.kind='erase' AND o.scope='organization'
          AND o.subject_id IS NULL AND o.status IN ('partial','completed') AND s.name='local' AND s.status='confirmed'
          AND b.scope='organization' AND b.subject_id IS NULL AND b.identifier_digest IS NULL AND b.key_id IS NULL
    ) THEN
        RAISE EXCEPTION 'Exact committed organization erasure required' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.organization_memberships WHERE organization_id=p_org)
       AND NOT EXISTS(SELECT 1 FROM public.team_invitations WHERE organization_id=p_org) THEN
        RETURN true;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=p_org
          AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events e
              WHERE e.organization_id=p_org AND e.hold_id=h.hold_id AND e.kind='released')
    ) OR 5 <> (
        SELECT count(*) FROM (
            SELECT DISTINCT ON (category) category,erasure_allowed,legal_hold,valid_until
            FROM public.gdpr_policies WHERE organization_id=p_org AND approved_at<=NOW()
              AND category IN ('contact_content','transient_content','cutover','knowledge_content','organization_profile')
            ORDER BY category,approved_at DESC,version DESC
        ) p WHERE p.erasure_allowed AND NOT p.legal_hold AND p.valid_until>NOW()
    ) THEN
        RETURN false;
    END IF;
    DELETE FROM public.team_invitations WHERE organization_id=p_org;
    DELETE FROM public.organization_memberships WHERE organization_id=p_org;
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.gdpr_reconcile_erased_tenant_access(uuid,uuid)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
GRANT EXECUTE ON FUNCTION public.gdpr_reconcile_erased_tenant_access(uuid,uuid) TO service_role;
