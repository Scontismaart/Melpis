SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Historical provenance is reviewed against an exact inventory by the database
-- owner. This migration deliberately does not infer, enroll, or alter any
-- historical booking. In particular, contactless manual guests remain valid.
CREATE TABLE IF NOT EXISTS public.booking_provenance_quarantine (
    organization_id uuid NOT NULL,
    booking_id uuid NOT NULL,
    review_reference text NOT NULL CHECK (review_reference ~ '^[a-zA-Z0-9._/-]{1,128}$'),
    reason_code text NOT NULL CHECK (reason_code = 'historical_provenance_unverified'),
    reviewed_at timestamptz NOT NULL CHECK (isfinite(reviewed_at)),
    review_due_at timestamptz NOT NULL CHECK (isfinite(review_due_at)
        AND review_due_at > reviewed_at AND review_due_at <= reviewed_at + interval '90 days'),
    PRIMARY KEY (organization_id, booking_id)
);
-- Parent existence is checked under lock on enrollment; evidence deliberately
-- survives reviewed release and subsequent authorized parent removal.
ALTER TABLE public.booking_provenance_quarantine ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_provenance_quarantine
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
GRANT SELECT ON public.booking_provenance_quarantine TO service_role;
DROP POLICY IF EXISTS booking_quarantine_backend_read ON public.booking_provenance_quarantine;
CREATE POLICY booking_quarantine_backend_read ON public.booking_provenance_quarantine
    FOR SELECT TO service_role USING (true);
DROP POLICY IF EXISTS booking_quarantine_deny_clients ON public.booking_provenance_quarantine;
CREATE POLICY booking_quarantine_deny_clients ON public.booking_provenance_quarantine
    AS RESTRICTIVE FOR ALL TO anon,authenticated USING (false) WITH CHECK (false);

CREATE TABLE IF NOT EXISTS public.booking_provenance_quarantine_releases (
    organization_id uuid NOT NULL,
    booking_id uuid NOT NULL,
    review_reference text NOT NULL CHECK (review_reference ~ '^[a-zA-Z0-9._/-]{1,128}$'),
    reviewed_at timestamptz NOT NULL CHECK (isfinite(reviewed_at)),
    PRIMARY KEY (organization_id, booking_id),
    FOREIGN KEY (organization_id,booking_id)
        REFERENCES public.booking_provenance_quarantine(organization_id,booking_id)
);
ALTER TABLE public.booking_provenance_quarantine_releases ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_provenance_quarantine_releases
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
GRANT SELECT ON public.booking_provenance_quarantine_releases TO service_role;
DROP POLICY IF EXISTS booking_quarantine_release_backend_read ON public.booking_provenance_quarantine_releases;
CREATE POLICY booking_quarantine_release_backend_read ON public.booking_provenance_quarantine_releases
    FOR SELECT TO service_role USING (true);
DROP POLICY IF EXISTS booking_quarantine_release_deny_clients ON public.booking_provenance_quarantine_releases;
CREATE POLICY booking_quarantine_release_deny_clients ON public.booking_provenance_quarantine_releases
    AS RESTRICTIVE FOR ALL TO anon,authenticated USING (false) WITH CHECK (false);

-- An enrollment transaction must serialize with existing booking mutations.
-- Exact scope is checked both by the parent lookup and the composite FK.
CREATE OR REPLACE FUNCTION public.booking_provenance_enrollment_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Booking provenance quarantine is immutable' USING ERRCODE='42501';
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Booking provenance enrollment requires read committed' USING ERRCODE='25000';
    END IF;
    IF NEW.reviewed_at > clock_timestamp() THEN
        RAISE EXCEPTION 'Booking provenance review time invalid' USING ERRCODE='22023';
    END IF;
    -- Serialize with delivery and GDPR guards, without waiting in reverse
    -- order if an active writer already has the tenant or booking lock.
    IF NOT pg_try_advisory_xact_lock(hashtextextended(NEW.organization_id::text,606)) THEN
        RAISE EXCEPTION 'Booking provenance tenant scope busy' USING ERRCODE='55P03';
    END IF;
    -- Do not change the booking or fire booking lifecycle triggers.
    PERFORM 1 FROM public.bookings b
      WHERE b.organization_id=NEW.organization_id AND b.id=NEW.booking_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Booking provenance scope invalid' USING ERRCODE='23503';
    END IF;
    IF TG_TABLE_NAME='booking_provenance_quarantine_releases' AND NOT EXISTS (
        SELECT 1 FROM public.booking_provenance_quarantine q
        WHERE q.organization_id=NEW.organization_id AND q.booking_id=NEW.booking_id
          AND NEW.reviewed_at>q.reviewed_at AND NEW.review_reference<>q.review_reference
    ) THEN
        RAISE EXCEPTION 'Separate authorized release review required' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_provenance_enrollment_guard()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
DROP TRIGGER IF EXISTS booking_provenance_enrollment_guard ON public.booking_provenance_quarantine;
CREATE TRIGGER booking_provenance_enrollment_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.booking_provenance_quarantine
    FOR EACH ROW EXECUTE FUNCTION public.booking_provenance_enrollment_guard();
CREATE OR REPLACE FUNCTION public.booking_provenance_revoke_exports()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
    -- A prior snapshot may include this booking. Invalidate its download
    -- authorization, without deleting source records or export chunks. This
    -- fires only on an actual new enrollment, not a duplicate replay.
    UPDATE public.gdpr_operations SET status='revoked',updated_at=NOW()
    WHERE organization_id=NEW.organization_id AND kind='export'
      AND status IN ('requested','running','ready');
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_provenance_revoke_exports()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
DROP TRIGGER IF EXISTS booking_provenance_revoke_exports ON public.booking_provenance_quarantine;
CREATE TRIGGER booking_provenance_revoke_exports AFTER INSERT ON public.booking_provenance_quarantine
    FOR EACH ROW EXECUTE FUNCTION public.booking_provenance_revoke_exports();
DROP TRIGGER IF EXISTS booking_provenance_release_guard ON public.booking_provenance_quarantine_releases;
CREATE TRIGGER booking_provenance_release_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.booking_provenance_quarantine_releases
    FOR EACH ROW EXECUTE FUNCTION public.booking_provenance_enrollment_guard();
DROP TRIGGER IF EXISTS booking_provenance_no_truncate ON public.booking_provenance_quarantine;
CREATE TRIGGER booking_provenance_no_truncate BEFORE TRUNCATE ON public.booking_provenance_quarantine
    FOR EACH STATEMENT EXECUTE FUNCTION public.booking_provenance_enrollment_guard();
DROP TRIGGER IF EXISTS booking_provenance_no_truncate ON public.booking_provenance_quarantine_releases;
CREATE TRIGGER booking_provenance_no_truncate BEFORE TRUNCATE ON public.booking_provenance_quarantine_releases
    FOR EACH STATEMENT EXECUTE FUNCTION public.booking_provenance_enrollment_guard();

-- The backend is explicitly allowed to check its exact scoped key. The
-- client predicate below is narrower and cannot reveal another tenant's hold.
CREATE OR REPLACE FUNCTION public.booking_provenance_is_quarantined(p_org uuid,p_booking uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM public.booking_provenance_quarantine q
        WHERE q.organization_id=p_org AND q.booking_id=p_booking
          AND NOT EXISTS (SELECT 1 FROM public.booking_provenance_quarantine_releases r
            WHERE r.organization_id=q.organization_id AND r.booking_id=q.booking_id));
$$;
REVOKE ALL ON FUNCTION public.booking_provenance_is_quarantined(uuid,uuid)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
GRANT EXECUTE ON FUNCTION public.booking_provenance_is_quarantined(uuid,uuid) TO service_role;

CREATE OR REPLACE FUNCTION public.booking_provenance_visible(p_org uuid,p_booking uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT public.current_tenant_membership_not_erased(p_org)
        AND NOT public.booking_provenance_is_quarantined(p_org,p_booking);
$$;
REVOKE ALL ON FUNCTION public.booking_provenance_visible(uuid,uuid)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
GRANT EXECUTE ON FUNCTION public.booking_provenance_visible(uuid,uuid) TO anon,authenticated;

-- The exact outbound message relation is private. Expose only a narrow
-- boolean for the backend and membership-scoped boolean for the RLS gate.
-- Never hide the corresponding inbound message merely because it prompted
-- a held booking.
CREATE OR REPLACE FUNCTION public.booking_provenance_confirmation_is_quarantined(p_org uuid,p_message uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM public.booking_confirmation_outbox o
        JOIN public.messages m ON m.organization_id=o.organization_id AND m.id=o.outbound_message_id
        WHERE o.organization_id=p_org AND o.outbound_message_id=p_message
          AND m.direction='outbound'
          AND public.booking_provenance_is_quarantined(o.organization_id,o.booking_id));
$$;
REVOKE ALL ON FUNCTION public.booking_provenance_confirmation_is_quarantined(uuid,uuid)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
GRANT EXECUTE ON FUNCTION public.booking_provenance_confirmation_is_quarantined(uuid,uuid) TO service_role;

CREATE OR REPLACE FUNCTION public.booking_provenance_confirmation_visible(p_org uuid,p_message uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT public.current_tenant_membership_not_erased(p_org)
        AND NOT public.booking_provenance_confirmation_is_quarantined(p_org,p_message);
$$;
REVOKE ALL ON FUNCTION public.booking_provenance_confirmation_visible(uuid,uuid)
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
GRANT EXECUTE ON FUNCTION public.booking_provenance_confirmation_visible(uuid,uuid) TO anon,authenticated;

DROP POLICY IF EXISTS messages_booking_provenance_visibility ON public.messages;
CREATE POLICY messages_booking_provenance_visibility ON public.messages AS RESTRICTIVE
    FOR SELECT TO anon,authenticated USING (direction IS DISTINCT FROM 'outbound'
        OR public.booking_provenance_confirmation_visible(organization_id,id));

DROP POLICY IF EXISTS bookings_provenance_visibility ON public.bookings;
CREATE POLICY bookings_provenance_visibility ON public.bookings AS RESTRICTIVE
    FOR SELECT TO anon,authenticated USING (public.booking_provenance_visible(organization_id,id));
DROP POLICY IF EXISTS external_booking_sync_provenance_visibility ON public.external_booking_sync;
CREATE POLICY external_booking_sync_provenance_visibility ON public.external_booking_sync AS RESTRICTIVE
    FOR SELECT TO anon,authenticated USING (public.booking_provenance_visible(organization_id,internal_booking_id));
DROP POLICY IF EXISTS event_log_booking_provenance_visibility ON public.event_log;
CREATE POLICY event_log_booking_provenance_visibility ON public.event_log AS RESTRICTIVE
    FOR SELECT TO anon,authenticated USING (source_table <> 'bookings'
        OR public.booking_provenance_visible(organization_id,source_id));
DROP POLICY IF EXISTS audit_log_booking_provenance_visibility ON public.audit_log;
CREATE POLICY audit_log_booking_provenance_visibility ON public.audit_log AS RESTRICTIVE
    FOR SELECT TO anon,authenticated USING (target_table IS DISTINCT FROM 'bookings'
        OR public.booking_provenance_visible(organization_id,target_id));

-- Prevent service-role and worker mutation of a held booking as well as a
-- client mutation in future grants. Use a fresh snapshot after the row lock
-- for concurrent owner enrollment; a higher isolation snapshot is unsafe.
CREATE OR REPLACE FUNCTION public.booking_provenance_mutation_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Booking mutations require read committed' USING ERRCODE='25000';
    END IF;
    IF NOT pg_try_advisory_xact_lock_shared(hashtextextended(OLD.organization_id::text,606)) THEN
        RAISE EXCEPTION 'Booking provenance tenant scope busy' USING ERRCODE='55P03';
    END IF;
    IF public.booking_provenance_is_quarantined(OLD.organization_id,OLD.id) THEN
        RAISE EXCEPTION 'Booking provenance review required' USING ERRCODE='55000';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_provenance_mutation_guard()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
DROP TRIGGER IF EXISTS a_booking_provenance_mutation_guard ON public.bookings;
CREATE TRIGGER a_booking_provenance_mutation_guard BEFORE UPDATE OR DELETE ON public.bookings
    FOR EACH ROW EXECUTE FUNCTION public.booking_provenance_mutation_guard();

-- Retain the exact confirmation linkage while its parent is quarantined;
-- removing or redirecting the outbox could otherwise expose the old message.
CREATE OR REPLACE FUNCTION public.booking_provenance_outbox_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Booking confirmation changes require read committed' USING ERRCODE='25000';
    END IF;
    IF TG_OP <> 'INSERT' THEN
        IF NOT pg_try_advisory_xact_lock_shared(hashtextextended(OLD.organization_id::text,606)) THEN
            RAISE EXCEPTION 'Booking provenance tenant scope busy' USING ERRCODE='55P03';
        END IF;
        IF public.booking_provenance_is_quarantined(OLD.organization_id,OLD.booking_id) THEN
            RAISE EXCEPTION 'Booking provenance review required' USING ERRCODE='55000';
        END IF;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        IF NOT pg_try_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,606)) THEN
            RAISE EXCEPTION 'Booking provenance tenant scope busy' USING ERRCODE='55P03';
        END IF;
        IF public.booking_provenance_is_quarantined(NEW.organization_id,NEW.booking_id) THEN
            RAISE EXCEPTION 'Booking provenance review required' USING ERRCODE='55000';
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION public.booking_provenance_outbox_guard()
    FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
DROP TRIGGER IF EXISTS a_booking_provenance_outbox_guard ON public.booking_confirmation_outbox;
CREATE TRIGGER a_booking_provenance_outbox_guard BEFORE INSERT OR UPDATE OR DELETE ON public.booking_confirmation_outbox
    FOR EACH ROW EXECUTE FUNCTION public.booking_provenance_outbox_guard();

-- Preserve the existing scheduler's narrowly scoped transition and exclude
-- held candidates before pagination. Apply rechecks after taking the shared
-- tenant lock, against concurrent owner enrollment.
CREATE OR REPLACE FUNCTION public.list_scheduled_booking_reviews(p_after_org uuid,p_after_booking uuid)
RETURNS TABLE (organization_id uuid,booking_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
    SELECT b.organization_id,b.id FROM public.bookings b
    JOIN public.booking_allocation_sets a ON a.organization_id=b.organization_id AND a.booking_id=b.id
    JOIN public.organizations o ON o.id=b.organization_id
    WHERE public.booking_scheduled_review_role()
      AND o.subscription_status NOT IN ('canceled','incomplete','past_due')
      AND b.stato='confermata' AND b.completata_at IS NULL AND b.no_show_at IS NULL
      AND a.state='active' AND a.ends_at<=statement_timestamp()
      AND NOT public.booking_provenance_is_quarantined(b.organization_id,b.id)
      AND ((p_after_org IS NULL AND p_after_booking IS NULL)
           OR (b.organization_id,b.id)>(p_after_org,p_after_booking))
    ORDER BY b.organization_id,b.id LIMIT 200
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
    IF public.booking_provenance_is_quarantined(p_org,p_booking) THEN RETURN false; END IF;

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

    PERFORM public.booking_sector_mark_scheduled_review(p_org, p_booking);

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
REVOKE ALL ON FUNCTION public.list_scheduled_booking_reviews(uuid,uuid),
    public.apply_scheduled_booking_review(uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.list_scheduled_booking_reviews(uuid,uuid),
    public.apply_scheduled_booking_review(uuid,uuid) TO melpis_booking_scheduler;

-- The dedicated inbound worker never receives the unrestricted quarantine
-- lookup. Its existing private capability views apply the gate only after
-- checking the effective role and exact live transaction/claim lineage.
CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_booking(
    p_organization_id uuid,p_booking_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
           SELECT 1 FROM c24_private.worker_context AS ctx
           JOIN public.booking_worker_claims AS claim
             ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
            AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
            AND claim.lease_until=ctx.lease_until
           JOIN public.bookings AS booking
             ON booking.organization_id=ctx.organization_id AND booking.id=p_booking_id
            AND booking.source_message_id=ctx.message_id::text
            AND booking.id_conversazione=ctx.conversation_id::text
           WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
             AND ctx.organization_id=p_organization_id AND ctx.lease_until>clock_timestamp()
             AND NOT public.booking_provenance_is_quarantined(booking.organization_id,booking.id)
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_booking(uuid,uuid,text)
    FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_booking(uuid,uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_scope_allows_bound_booking(
    p_organization_id uuid,p_booking_id uuid,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
           SELECT 1 FROM c24_private.worker_context AS ctx
           JOIN public.booking_worker_claims AS claim
             ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
            AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
            AND claim.lease_until=ctx.lease_until
           JOIN public.bookings AS booking
             ON booking.organization_id=ctx.organization_id AND booking.id=p_booking_id
            AND booking.source_message_id=ctx.message_id::text
            AND booking.id_conversazione=ctx.conversation_id::text
           WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
             AND ctx.organization_id=p_organization_id AND ctx.booking_id=p_booking_id
             AND ctx.lease_until>clock_timestamp()
             AND NOT public.booking_provenance_is_quarantined(booking.organization_id,booking.id)
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_scope_allows_bound_booking(uuid,uuid,text)
    FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_scope_allows_bound_booking(uuid,uuid,text)
    TO melpis_c24_booking_worker;

CREATE OR REPLACE FUNCTION c24_private.c24_worker_outbound_booking_message_allows(
    p_organization_id uuid,p_conversation_id uuid,p_idempotency_key text,
    p_recipient text,p_effective_role text
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    SELECT session_user='melpis_c24_booking_worker'
       AND p_effective_role='melpis_c24_booking_worker'
       AND EXISTS (
           SELECT 1 FROM c24_private.worker_context AS ctx
           JOIN public.booking_worker_claims AS claim
             ON claim.organization_id=ctx.organization_id AND claim.message_id=ctx.message_id
            AND claim.claim_id=ctx.claim_id AND claim.generation=ctx.generation
            AND claim.lease_until=ctx.lease_until
           JOIN public.bookings AS booking
             ON booking.organization_id=ctx.organization_id
            AND booking.source_message_id=ctx.message_id::text
            AND booking.id_conversazione=ctx.conversation_id::text
           JOIN public.contacts AS contact
             ON contact.organization_id=booking.organization_id
            AND contact.id=booking.contact_id
           WHERE ctx.backend_pid=pg_backend_pid() AND ctx.transaction_id=pg_current_xact_id()
             AND ctx.organization_id=p_organization_id
             AND ctx.booking_id=booking.id
             AND ctx.conversation_id=p_conversation_id
             AND p_recipient IS NOT NULL AND p_recipient=contact.phone_number
             AND p_idempotency_key='booking-confirm:'||ctx.organization_id::text||':'||ctx.message_id::text
             AND ctx.lease_until>clock_timestamp()
             AND NOT public.booking_provenance_is_quarantined(booking.organization_id,booking.id)
       )
$$;
REVOKE ALL ON FUNCTION c24_private.c24_worker_outbound_booking_message_allows(uuid,uuid,text,text,text)
    FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION c24_private.c24_worker_outbound_booking_message_allows(uuid,uuid,text,text,text)
    TO melpis_c24_booking_worker;

-- Preserve SQL113's owner/operation/subject-scoped projection. A private
-- evidence row for a held booking must not leave the database through RPC.
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
          AND NOT public.booking_provenance_is_quarantined(e.organization_id,e.booking_id)
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
          AND NOT public.booking_provenance_is_quarantined(e.organization_id,e.booking_id)
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
