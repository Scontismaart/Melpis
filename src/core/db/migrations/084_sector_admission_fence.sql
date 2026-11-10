-- Dormant, owner-only admission fence for new hair/spa tenants.
-- This deliberately does not backfill existing bookings or permit legacy
-- admission after registration. Activation and all schedule writes serialize.
-- A stable row per tenant supplies a fresh lockable version even before opt-in.
CREATE TABLE public.booking_sector_admission_epochs (
    organization_id uuid PRIMARY KEY REFERENCES public.organizations(id) ON DELETE CASCADE,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0)
);
ALTER TABLE public.booking_sector_admission_epochs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_sector_admission_epochs FROM PUBLIC, anon, authenticated, service_role;
CREATE FUNCTION public.booking_sector_create_epoch() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    INSERT INTO public.booking_sector_admission_epochs(organization_id) VALUES(NEW.id);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_sector_create_epoch() FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER booking_sector_create_epoch AFTER INSERT ON public.organizations
FOR EACH ROW EXECUTE FUNCTION public.booking_sector_create_epoch();
INSERT INTO public.booking_sector_admission_epochs(organization_id)
SELECT id FROM public.organizations ON CONFLICT DO NOTHING;

CREATE FUNCTION public.booking_sector_lock_epoch(p_org uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM 1 FROM public.booking_sector_admission_epochs WHERE organization_id=p_org FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'booking admission epoch unavailable' USING ERRCODE='55000'; END IF;
END $$;
REVOKE ALL ON FUNCTION public.booking_sector_lock_epoch(uuid) FROM PUBLIC, anon, authenticated, service_role;

CREATE TABLE public.booking_sector_admission_fences (
    organization_id uuid PRIMARY KEY REFERENCES public.organizations(id) ON DELETE CASCADE,
    sector text NOT NULL CHECK (sector IN ('parrucchiere','centro_estetico','hotel_bnb')),
    enabled_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE public.booking_sector_admission_markers (
    transaction_id bigint NOT NULL,
    organization_id uuid NOT NULL,
    booking_id uuid NOT NULL,
    PRIMARY KEY (transaction_id, organization_id, booking_id),
    FOREIGN KEY (organization_id) REFERENCES public.booking_sector_admission_fences(organization_id) ON DELETE CASCADE
);
ALTER TABLE public.booking_sector_admission_fences ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.booking_sector_admission_markers ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_sector_admission_fences, public.booking_sector_admission_markers FROM PUBLIC, anon, authenticated, service_role;

CREATE FUNCTION public.booking_sector_activate_empty(p_org uuid, p_sector text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF COALESCE(NULLIF(current_setting('role'), 'none'), session_user) <>
        pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='public.booking_sector_admission_fences'::regclass)) THEN
        RAISE EXCEPTION 'owner required' USING ERRCODE='42501';
    END IF;
    IF p_sector NOT IN ('parrucchiere','centro_estetico','hotel_bnb') OR p_org IS NULL THEN
        RAISE EXCEPTION 'invalid sector';
    END IF;
    -- Match worker/typed order: organization share, then admission fence.
    -- SHARE also permits the KEY SHARE acquired by a concurrent booking FK.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'activation requires read committed';
    END IF;
    PERFORM 1 FROM public.organizations WHERE id=p_org AND business_profile->>'verticale'=p_sector FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'tenant must have no bookings and matching sector';
    END IF;
    UPDATE public.booking_sector_admission_epochs SET revision=revision+1 WHERE organization_id=p_org;
    IF NOT FOUND THEN RAISE EXCEPTION 'booking admission epoch unavailable'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text, 884));
    -- A new READ COMMITTED snapshot after the wait sees any prior legacy commit.
    IF EXISTS(SELECT 1 FROM public.bookings WHERE organization_id=p_org) THEN
        RAISE EXCEPTION 'tenant must have no bookings and matching sector';
    END IF;
    INSERT INTO public.booking_sector_admission_fences(organization_id,sector) VALUES(p_org,p_sector);
END $$;
REVOKE ALL ON FUNCTION public.booking_sector_activate_empty(uuid,text) FROM PUBLIC, anon, authenticated, service_role;

CREATE FUNCTION public.booking_sector_mark_worker_insert(p_org uuid, p_booking uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF COALESCE(NULLIF(current_setting('role'), 'none'), session_user) <>
        pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='public.booking_sector_admission_fences'::regclass)) THEN
        RAISE EXCEPTION 'owner required' USING ERRCODE='42501';
    END IF;
    PERFORM public.booking_sector_lock_epoch(p_org);
    PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text, 884));
    IF NOT EXISTS(SELECT 1 FROM public.booking_sector_admission_fences WHERE organization_id=p_org) THEN
        RAISE EXCEPTION 'admission fence unavailable';
    END IF;
    INSERT INTO public.booking_sector_admission_markers VALUES(txid_current(),p_org,p_booking);
END $$;
REVOKE ALL ON FUNCTION public.booking_sector_mark_worker_insert(uuid,uuid) FROM PUBLIC, anon, authenticated, service_role;

CREATE FUNCTION public.booking_sector_fence_booking() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE scope uuid;
BEGIN
    scope := NEW.organization_id;
    IF TG_OP='UPDATE' AND OLD.organization_id IS DISTINCT FROM NEW.organization_id THEN
        RAISE EXCEPTION 'booking organization immutable';
    END IF;
    -- Non-capacity updates (including GDPR redaction) need no admission fence.
    IF TG_OP='UPDATE' AND (NEW.data,NEW.ora,NEW.coperti,NEW.stato)
            IS NOT DISTINCT FROM (OLD.data,OLD.ora,OLD.coperti,OLD.stato) THEN
        RETURN NEW;
    END IF;
    -- Locking the epoch row aborts a stale RR/serializable snapshot if an
    -- activation changed it, and makes activation wait for earlier writers.
    PERFORM public.booking_sector_lock_epoch(scope);
    PERFORM pg_advisory_xact_lock(hashtextextended(scope::text, 884));
    IF EXISTS(SELECT 1 FROM public.booking_sector_admission_fences WHERE organization_id=scope) THEN
        IF TG_OP='INSERT' THEN
            IF NOT EXISTS(SELECT 1 FROM public.booking_sector_admission_markers
                WHERE transaction_id=txid_current() AND organization_id=scope AND booking_id=NEW.id) THEN
                RAISE EXCEPTION 'sector booking requires guarded admission' USING ERRCODE='42501';
            END IF;
            DELETE FROM public.booking_sector_admission_markers
                WHERE transaction_id=txid_current() AND organization_id=scope AND booking_id=NEW.id;
        ELSIF (NEW.data,NEW.ora,NEW.coperti,NEW.stato) IS DISTINCT FROM (OLD.data,OLD.ora,OLD.coperti,OLD.stato) THEN
            IF NOT EXISTS(SELECT 1 FROM public.booking_sector_admission_markers
                WHERE transaction_id=txid_current() AND organization_id=scope AND booking_id=NEW.id) THEN
                RAISE EXCEPTION 'sector scheduling requires guarded lifecycle' USING ERRCODE='42501';
            END IF;
            DELETE FROM public.booking_sector_admission_markers
                WHERE transaction_id=txid_current() AND organization_id=scope AND booking_id=NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_sector_fence_booking() FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER booking_sector_admission_fence BEFORE INSERT OR UPDATE ON public.bookings
FOR EACH ROW EXECUTE FUNCTION public.booking_sector_fence_booking();


-- Executable by the owning SECURITY DEFINER function only, not by the
-- scheduler role directly. It does not alter scheduler actor attribution.
CREATE FUNCTION public.booking_sector_mark_scheduled_review(p_org uuid, p_booking uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF NOT public.booking_scheduled_review_role() THEN
        RAISE EXCEPTION 'scheduled review role required' USING ERRCODE='42501';
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'booking admission requires read committed' USING ERRCODE='40001';
    END IF;
    PERFORM public.booking_sector_lock_epoch(p_org);
    PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text, 884));
    IF EXISTS(SELECT 1 FROM public.booking_sector_admission_fences WHERE organization_id=p_org) THEN
        INSERT INTO public.booking_sector_admission_markers VALUES(txid_current(),p_org,p_booking);
    END IF;
END $$;
REVOKE ALL ON FUNCTION public.booking_sector_mark_scheduled_review(uuid,uuid)
    FROM PUBLIC, anon, authenticated, service_role, melpis_booking_scheduler;

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

    -- Take the same admission fence before resource locks and mint only the
    -- marker consumed by this verified scheduler transition.
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
