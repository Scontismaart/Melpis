-- Allow the dedicated booking worker to attach source correlation to the
-- authoritative Calendar queue without granting access to credentials or the
-- external sync ledger.
CREATE OR REPLACE FUNCTION public.c24_attach_calendar_inbound_source(
    p_organization_id uuid,
    p_booking_id uuid,
    p_source_message_id uuid
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
DECLARE
    booking_revision bigint;
BEGIN
    IF session_user IS DISTINCT FROM 'melpis_c24_booking_worker' THEN
        RAISE EXCEPTION 'dedicated booking worker required' USING ERRCODE='42501';
    END IF;

    SELECT b.calendar_revision INTO booking_revision
      FROM public.bookings b
      JOIN public.conversations c
        ON c.organization_id=b.organization_id
       AND c.id::text=b.id_conversazione
       AND c.contact_id=b.contact_id
      JOIN public.messages m
        ON m.organization_id=b.organization_id
       AND m.conversation_id=c.id
       AND m.id=p_source_message_id
      JOIN public.booking_settings bs
        ON bs.organization_id=b.organization_id
       AND bs.config#>>'{a05,authority}'='google_authoritative'
     WHERE b.organization_id=p_organization_id
       AND b.id=p_booking_id
       AND b.contact_id IS NOT NULL
       AND b.source_message_id=p_source_message_id::text
       AND b.stato='in_attesa'
       AND b.calendar_confirm_pending
       AND b.richiede_intervento
       AND c.status='active'
       AND c.deleted_at IS NULL
       AND c.canale='whatsapp'
       AND m.direction='inbound'
       AND m.deleted_at IS NULL
       AND m.c24_original_subject_id=b.contact_id;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM public.inbound_worker_operations o
          JOIN public.inbound_worker_receipts r
            ON r.organization_id=o.organization_id
           AND r.inbox_id=o.inbox_id
           AND r.message_id=o.message_id
          JOIN public.meta_webhook_inbox i
            ON i.organization_id=r.organization_id
           AND i.id=r.inbox_id
          JOIN public.usage_events u
            ON u.organization_id=o.organization_id
           AND u.id=o.operation_id
         WHERE o.organization_id=p_organization_id
           AND o.message_id=p_source_message_id
           AND i.channel='whatsapp'
           AND i.status='completed'
           AND u.event_type='system.quota_reservation'
           AND u.quantity=0
           AND u.metadata->>'channel'='inbound'
           AND u.metadata->>'state'='committed'
    ) THEN
        RETURN false;
    END IF;

    UPDATE public.external_booking_sync s
       SET calendar_inbound_message_id=p_source_message_id,
           calendar_notification_channel='whatsapp',
           updated_at=clock_timestamp()
     WHERE s.organization_id=p_organization_id
       AND s.internal_booking_id=p_booking_id
       AND s.provider='google'
       AND s.calendar_authoritative
       AND s.calendar_action='authoritative'
       AND s.calendar_desired_revision=booking_revision
       AND (s.calendar_inbound_message_id IS NULL
            OR s.calendar_inbound_message_id=p_source_message_id)
       AND (s.calendar_notification_channel IS NULL
            OR s.calendar_notification_channel='whatsapp');

    RETURN FOUND;
END;
$$;

REVOKE ALL ON FUNCTION public.c24_attach_calendar_inbound_source(uuid,uuid,uuid)
    FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.c24_attach_calendar_inbound_source(uuid,uuid,uuid)
    TO melpis_c24_booking_worker;
