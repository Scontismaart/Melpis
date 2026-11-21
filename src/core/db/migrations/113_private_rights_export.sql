SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Narrow, purpose-specific access. The application role never receives table
-- grants on private lifecycle evidence. The authenticated owner and MFA are
-- established by the API, and the persisted running export and joined owner
-- membership are independently checked here against the same tenant.
CREATE OR REPLACE FUNCTION public.gdpr_private_export_records(
    p_org uuid, p_operation uuid, p_requester text, p_category text
) RETURNS TABLE(record text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path='' AS $$
DECLARE export_scope text; export_subject uuid;
BEGIN
    SELECT o.scope,o.subject_id INTO export_scope,export_subject
    FROM public.gdpr_operations o
    JOIN public.user_profiles p ON p.auth_user_id::text=o.requester
    JOIN public.organization_memberships membership ON membership.user_id=p.id
      AND membership.organization_id=o.organization_id
    JOIN public.auth_access_lifecycle access ON access.auth_user_id=p.auth_user_id
    WHERE o.organization_id=p_org AND o.id=p_operation AND o.requester=p_requester
      AND o.kind='export' AND o.status='running'
      AND membership.ruolo='owner' AND membership.joined_at IS NOT NULL
      AND access.disabled_at IS NULL
      AND (o.scope='organization' AND o.subject_id IS NULL OR
           o.scope='contact' AND EXISTS(SELECT 1 FROM public.contacts c
              WHERE c.organization_id=p_org AND c.id=o.subject_id));
    IF NOT FOUND OR p_category IS NULL OR p_category NOT IN (
       'booking_stay_completion_evidence','ordinary_booking_completion_evidence','inbound_worker_receipts'
    ) THEN
        RETURN;
    END IF;

    -- The initial null record identifies a permitted scope even when there
    -- are zero verified records. No row at all means the scope is unavailable.
    record := NULL;
    RETURN NEXT;
    IF p_category='booking_stay_completion_evidence' THEN
        RETURN QUERY
        SELECT pg_catalog.json_build_object(
            'booking_id',e.booking_id,'completed_at',e.completed_at,'timezone',e.timezone)::text
        FROM public.booking_stay_completion_evidence e
        JOIN public.booking_stays s ON s.organization_id=e.organization_id AND s.booking_id=e.booking_id
        JOIN public.bookings b ON b.organization_id=e.organization_id AND b.id=e.booking_id
        WHERE e.organization_id=p_org
          AND b.contact_id=s.original_subject_id
          AND (export_scope='organization' OR s.original_subject_id=export_subject)
        ORDER BY e.booking_id LIMIT 50001;
    ELSIF p_category='ordinary_booking_completion_evidence' THEN
        RETURN QUERY
        SELECT pg_catalog.json_build_object(
            'booking_id',e.booking_id,'completed_at',e.completed_at,'timezone',e.timezone)::text
        FROM public.ordinary_booking_completion_evidence e
        JOIN public.contacts c ON c.organization_id=e.organization_id AND c.id=e.subject_id
        JOIN public.bookings b ON b.organization_id=e.organization_id AND b.id=e.booking_id
        WHERE e.organization_id=p_org
          AND b.contact_id=e.subject_id
          AND NULLIF(b.id_conversazione,'') IS NOT DISTINCT FROM e.conversation_id::text
          AND (e.conversation_id IS NULL OR EXISTS(SELECT 1 FROM public.conversations conv
              WHERE conv.organization_id=p_org AND conv.id=e.conversation_id AND conv.contact_id=e.subject_id))
          AND (export_scope='organization' OR e.subject_id=export_subject)
        ORDER BY e.booking_id LIMIT 50001;
    ELSE
        RETURN QUERY
        SELECT pg_catalog.json_build_object(
            'message_id',r.message_id,'received_at',m.c24_received_at,
            'completed_at',CASE WHEN i.status='completed' THEN i.c24_completed_at END)::text
        FROM public.inbound_worker_receipts r
        JOIN public.messages m ON m.organization_id=r.organization_id AND m.id=r.message_id
        JOIN public.conversations c ON c.organization_id=m.organization_id AND c.id=m.conversation_id
        JOIN public.meta_webhook_inbox i ON i.organization_id=r.organization_id AND i.id=r.inbox_id
        WHERE r.organization_id=p_org
          AND i.channel IN ('whatsapp','instagram')
          AND m.direction='inbound' AND m.c24_received_at IS NOT NULL
          AND m.c24_original_subject_id=c.contact_id
          AND (export_scope='organization' OR m.c24_original_subject_id=export_subject)
        ORDER BY r.message_id,r.inbox_id LIMIT 50001;
    END IF;
END $$;

REVOKE ALL ON FUNCTION public.gdpr_private_export_records(uuid,uuid,text,text)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
GRANT EXECUTE ON FUNCTION public.gdpr_private_export_records(uuid,uuid,text,text) TO service_role;
