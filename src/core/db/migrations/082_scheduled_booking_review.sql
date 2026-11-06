-- The scheduler can mark an elapsed booking for human review. It cannot
-- choose a status, change capacity, read customer data, or send a message.
DO $scheduled_booking_review$
BEGIN
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'melpis_booking_scheduler') THEN
        CREATE ROLE melpis_booking_scheduler NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'melpis_booking_scheduler'
               AND (rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
       OR pg_has_role('service_role', 'melpis_booking_scheduler', 'MEMBER')
       OR pg_has_role('anon', 'melpis_booking_scheduler', 'MEMBER')
       OR pg_has_role('authenticated', 'melpis_booking_scheduler', 'MEMBER')
       OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                  WHERE r.rolname = 'melpis_booking_scheduler')
       OR has_any_column_privilege('melpis_booking_scheduler', 'public.bookings', 'SELECT')
       OR has_table_privilege('melpis_booking_scheduler', 'public.booking_allocation_requests', 'INSERT') THEN
        RAISE EXCEPTION 'Scheduled booking review requires an isolated principal';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.booking_scheduled_review_role()
RETURNS boolean LANGUAGE sql STABLE SET search_path = pg_catalog, public AS $$
    SELECT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = COALESCE(NULLIF(current_setting('role'), 'none'), session_user)
          AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
          AND pg_has_role(oid, 'melpis_booking_scheduler', 'USAGE')
          AND NOT has_any_column_privilege(oid, 'public.bookings', 'SELECT,UPDATE,INSERT,REFERENCES')
          AND NOT has_any_column_privilege(oid, 'public.contacts', 'SELECT,UPDATE,INSERT,REFERENCES')
          AND NOT has_table_privilege(oid, 'public.booking_allocation_requests', 'INSERT')
    )
$$;
REVOKE ALL ON FUNCTION public.booking_scheduled_review_role() FROM PUBLIC;

ALTER TABLE public.booking_allocation_requests
    DROP CONSTRAINT IF EXISTS booking_allocation_requests_actor_shape,
    ADD CONSTRAINT booking_allocation_requests_actor_shape CHECK (
        (actor_kind = 'human' AND actor_user_id IS NOT NULL AND service_inbox_id IS NULL
         AND service_message_id IS NULL AND service_operation_id IS NULL)
        OR (actor_kind = 'inbound_worker' AND actor_user_id IS NULL AND service_inbox_id IS NOT NULL
            AND service_message_id IS NOT NULL AND service_operation_id IS NOT NULL)
        OR (actor_kind = 'scheduled_review' AND actor_user_id IS NULL AND service_inbox_id IS NULL
            AND service_message_id IS NULL AND service_operation_id IS NULL
            AND action = 'replace' AND outcome = 'replaced' AND mutation_hash IS NOT NULL)
    );

CREATE OR REPLACE FUNCTION public.booking_allocation_validate_scheduled_review()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF NOT public.booking_scheduled_review_role() OR NOT EXISTS (
           SELECT 1 FROM public.bookings b
           JOIN public.booking_allocation_sets a ON a.organization_id = b.organization_id AND a.booking_id = b.id
           WHERE b.organization_id = NEW.organization_id AND b.id = NEW.booking_id
             AND b.stato = 'da_verificare' AND b.completata_at IS NULL AND b.no_show_at IS NULL
             AND a.state = 'active' AND a.ends_at <= clock_timestamp()
             AND a.allocation_revision = NEW.applied_allocation_revision
             AND a.catalog_revision = NEW.catalog_revision AND a.booking_calendar_revision = b.calendar_revision
       ) THEN
        RAISE EXCEPTION 'Scheduled review receipt lacks authorized postimage' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_allocation_validate_scheduled_review() FROM PUBLIC;
DROP TRIGGER IF EXISTS booking_allocation_validate_scheduled_review ON public.booking_allocation_requests;
CREATE TRIGGER booking_allocation_validate_scheduled_review BEFORE INSERT ON public.booking_allocation_requests
    FOR EACH ROW WHEN (NEW.actor_kind = 'scheduled_review')
    EXECUTE FUNCTION public.booking_allocation_validate_scheduled_review();

-- Same canonical fingerprint as BookingAllocationRepository, limited to
-- live bookings without completion/no-show timestamps or free-form lineage.
CREATE OR REPLACE FUNCTION public.booking_scheduled_review_fingerprint(b public.bookings)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, public SET DateStyle = 'ISO, YMD' AS $$
    SELECT encode(sha256(convert_to(
        '{"calendar_revision":' || coalesce(to_json(b.calendar_revision)::text, 'null') ||
        ',"completata_at":null,"contact_id":' || coalesce(to_json(b.contact_id)::text, 'null') ||
        ',"coperti":' || coalesce(to_json(b.coperti)::text, 'null') ||
        ',"data":' || coalesce(to_json(b.data)::text, 'null') ||
        ',"id_conversazione":' || coalesce(to_json(b.id_conversazione)::text, 'null') ||
        ',"no_show_at":null,"ora":' || coalesce(to_json(b.ora)::text, 'null') ||
        ',"stato":' || coalesce(to_json(b.stato)::text, 'null') || '}', 'UTF8')), 'hex')
$$;
REVOKE ALL ON FUNCTION public.booking_scheduled_review_fingerprint(public.bookings) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.list_scheduled_booking_reviews(p_after_org uuid, p_after_booking uuid)
RETURNS TABLE (organization_id uuid, booking_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT b.organization_id, b.id FROM public.bookings b
    JOIN public.booking_allocation_sets a ON a.organization_id = b.organization_id AND a.booking_id = b.id
    JOIN public.organizations o ON o.id = b.organization_id
    WHERE public.booking_scheduled_review_role()
      AND o.subscription_status NOT IN ('canceled', 'incomplete', 'past_due')
      AND b.stato = 'confermata' AND b.completata_at IS NULL AND b.no_show_at IS NULL
      AND a.state = 'active' AND a.ends_at <= statement_timestamp()
      AND ((p_after_org IS NULL AND p_after_booking IS NULL)
           OR (b.organization_id, b.id) > (p_after_org, p_after_booking))
    ORDER BY b.organization_id, b.id
    LIMIT 200
$$;

CREATE OR REPLACE FUNCTION public.apply_scheduled_booking_review(p_org uuid, p_booking uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    before_booking public.bookings%ROWTYPE;
    after_booking public.bookings%ROWTYPE;
    allocation public.booking_allocation_sets%ROWTYPE;
    subject uuid;
    conversation_subject uuid;
    settings jsonb;
    resource text;
    receipt_hash text;
    request_key uuid;
BEGIN
    IF NOT public.booking_scheduled_review_role() THEN
        RAISE EXCEPTION 'Scheduled review requires its dedicated role' USING ERRCODE = '42501';
    END IF;
    PERFORM 1 FROM public.organizations WHERE id = p_org
        AND subscription_status NOT IN ('canceled', 'incomplete', 'past_due') FOR SHARE;
    IF NOT FOUND THEN RETURN false; END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended(p_org::text, 606));

    SELECT * INTO before_booking FROM public.bookings WHERE organization_id = p_org AND id = p_booking;
    IF NOT FOUND OR before_booking.stato <> 'confermata' OR before_booking.completata_at IS NOT NULL
       OR before_booking.no_show_at IS NOT NULL THEN RETURN false; END IF;
    subject := before_booking.contact_id;
    IF nullif(before_booking.id_conversazione, '') IS NOT NULL THEN
        IF before_booking.id_conversazione !~* '^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$' THEN
            RETURN false;
        END IF;
        SELECT contact_id INTO conversation_subject FROM public.conversations
        WHERE organization_id = p_org AND id = before_booking.id_conversazione::uuid
          AND deleted_at IS NULL FOR UPDATE;
        IF NOT FOUND OR conversation_subject IS NULL
           OR (subject IS NOT NULL AND subject <> conversation_subject) THEN RETURN false; END IF;
        subject := conversation_subject;
    END IF;
    IF subject IS NOT NULL THEN
        PERFORM 1 FROM public.contacts WHERE organization_id = p_org AND id = subject AND deleted_at IS NULL FOR SHARE;
        IF NOT FOUND THEN RETURN false; END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM public.gdpr_erasure_barriers WHERE organization_id = p_org
               AND (scope = 'organization' OR subject IS NULL OR subject_id = subject))
       OR EXISTS (SELECT 1 FROM public.booking_stay_purge_fences WHERE organization_id = p_org AND booking_id = p_booking) THEN
        RETURN false;
    END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtext('booking-capacity|' || p_org::text));
    SELECT config INTO settings FROM public.booking_settings WHERE organization_id = p_org FOR SHARE;
    IF NOT FOUND OR jsonb_typeof(settings) <> 'object'
       OR (settings ? 'a05' AND jsonb_typeof(settings->'a05') <> 'object')
       OR (settings->'a05' ? 'authority' AND jsonb_typeof(settings#>'{a05,authority}') <> 'string')
       OR coalesce(settings#>>'{a05,authority}', 'internal') NOT IN ('internal', 'internal_calendar_sync') THEN
        RETURN false;
    END IF;
    SELECT * INTO after_booking FROM public.bookings WHERE organization_id = p_org AND id = p_booking FOR UPDATE;
    IF NOT FOUND OR after_booking IS DISTINCT FROM before_booking THEN RETURN false; END IF;
    SELECT * INTO allocation FROM public.booking_allocation_sets
    WHERE organization_id = p_org AND booking_id = p_booking FOR UPDATE;
    IF NOT FOUND OR allocation.state <> 'active' OR allocation.ends_at > clock_timestamp()
       OR allocation.booking_calendar_revision <> before_booking.calendar_revision
       OR allocation.booking_fingerprint <> public.booking_scheduled_review_fingerprint(before_booking)
       OR NOT EXISTS (SELECT 1 FROM public.booking_resource_allocations
                      WHERE organization_id = p_org AND booking_id = p_booking) THEN RETURN false; END IF;

    receipt_hash := encode(sha256(convert_to('scheduled-review:v1|' || p_org::text || '|' || p_booking::text || '|'
                    || allocation.allocation_revision::text || '|' || allocation.booking_fingerprint, 'UTF8')), 'hex');
    request_key := substr(receipt_hash, 1, 32)::uuid;
    PERFORM pg_advisory_xact_lock(hashtextextended('allocation-request|' || p_org::text || '|' || request_key::text, 723));
    FOR resource IN SELECT resource_id FROM public.booking_resource_allocations
                    WHERE organization_id = p_org AND booking_id = p_booking ORDER BY resource_id
    LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended('allocation-resource|' || p_org::text || '|' || resource, 725));
    END LOOP;
    UPDATE public.bookings SET stato = 'da_verificare', updated_at = NOW()
    WHERE organization_id = p_org AND id = p_booking RETURNING * INTO after_booking;
    IF after_booking.stato <> 'da_verificare' OR after_booking.calendar_revision <> before_booking.calendar_revision + 1
       OR (after_booking.data, after_booking.ora, after_booking.coperti, after_booking.contact_id,
           after_booking.id_conversazione, after_booking.completata_at, after_booking.no_show_at)
          IS DISTINCT FROM (before_booking.data, before_booking.ora, before_booking.coperti, before_booking.contact_id,
                            before_booking.id_conversazione, before_booking.completata_at, before_booking.no_show_at) THEN
        RAISE EXCEPTION 'Scheduled review changed unexpected booking fields' USING ERRCODE = '55000';
    END IF;
    UPDATE public.booking_allocation_sets
    SET allocation_revision = allocation.allocation_revision + 1,
        booking_fingerprint = public.booking_scheduled_review_fingerprint(after_booking),
        booking_calendar_revision = after_booking.calendar_revision, updated_at = NOW()
    WHERE organization_id = p_org AND booking_id = p_booking;
    UPDATE public.booking_resource_allocations SET allocation_revision = allocation.allocation_revision + 1
    WHERE organization_id = p_org AND booking_id = p_booking;
    INSERT INTO public.booking_allocation_requests (
        organization_id, request_id, booking_id, actor_user_id, actor_kind, action, payload_hash, outcome,
        applied_allocation_revision, catalog_revision, mutation_hash
    ) VALUES (
        p_org, request_key, p_booking, NULL, 'scheduled_review', 'replace', receipt_hash, 'replaced',
        allocation.allocation_revision + 1, allocation.catalog_revision, receipt_hash
    );
    RETURN true;
END $$;

REVOKE ALL ON FUNCTION public.list_scheduled_booking_reviews(uuid, uuid),
    public.apply_scheduled_booking_review(uuid, uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA public TO melpis_booking_scheduler;
GRANT EXECUTE ON FUNCTION public.list_scheduled_booking_reviews(uuid, uuid),
    public.apply_scheduled_booking_review(uuid, uuid) TO melpis_booking_scheduler;
END;
$scheduled_booking_review$;
