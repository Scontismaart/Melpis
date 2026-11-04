-- Evidence is recorded only by the transactional completion operation. It is
-- not a purge authorization and is not backfilled from legacy timestamps.
CREATE TABLE IF NOT EXISTS public.booking_stay_completion_evidence (
    organization_id uuid NOT NULL,
    booking_id uuid NOT NULL,
    completed_at timestamptz NOT NULL CHECK (isfinite(completed_at)),
    timezone text NOT NULL,
    PRIMARY KEY (organization_id, booking_id)
);
ALTER TABLE public.booking_stay_completion_evidence ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_stay_completion_evidence FROM PUBLIC, anon, authenticated, service_role;

-- A transaction-local private marker prevents direct evidence deletion.
CREATE TABLE IF NOT EXISTS public.booking_completion_erasure_markers (
    transaction_id bigint NOT NULL, organization_id uuid NOT NULL, booking_id uuid NOT NULL,
    PRIMARY KEY(transaction_id,organization_id,booking_id)
);
ALTER TABLE public.booking_completion_erasure_markers ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_completion_erasure_markers FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.booking_stay_completion_evidence_immutable()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
    IF TG_OP='DELETE' AND EXISTS (SELECT 1 FROM public.booking_completion_erasure_markers m
       WHERE m.transaction_id=txid_current() AND m.organization_id=OLD.organization_id
         AND m.booking_id=OLD.booking_id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Completion evidence is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.booking_stay_completion_evidence_immutable() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS completion_evidence_immutable ON public.booking_stay_completion_evidence;
CREATE TRIGGER completion_evidence_immutable BEFORE UPDATE OR DELETE
ON public.booking_stay_completion_evidence FOR EACH ROW
EXECUTE FUNCTION public.booking_stay_completion_evidence_immutable();
DROP TRIGGER IF EXISTS completion_evidence_no_truncate ON public.booking_stay_completion_evidence;
CREATE TRIGGER completion_evidence_no_truncate BEFORE TRUNCATE
ON public.booking_stay_completion_evidence FOR EACH STATEMENT
EXECUTE FUNCTION public.booking_stay_completion_evidence_immutable();

-- An approved matching barrier removes evidence even while an occupied stay
-- remains pending separate administrative disposition. The evidence contains
-- only booking linkage, completion instant and zone, never contact fields.
CREATE OR REPLACE FUNCTION public.erase_hotel_completion_evidence()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE cat text; pol public.gdpr_policies%ROWTYPE; item record;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.organization_id::text,606));
    IF NOT EXISTS (SELECT 1 FROM public.gdpr_operations o
        WHERE o.organization_id=NEW.organization_id AND o.id=NEW.operation_id
          AND o.kind='erase' AND o.status IN ('requested','running','blocked','partial')
          AND o.scope=NEW.scope AND o.subject_id IS NOT DISTINCT FROM NEW.subject_id) THEN
        RETURN NEW;
    END IF;
    FOR cat IN SELECT unnest(ARRAY['booking_stay_content','contact_content','cutover']) LOOP
        SELECT * INTO pol FROM public.gdpr_policies WHERE organization_id=NEW.organization_id
          AND category=cat AND approved_at<=now() ORDER BY approved_at DESC,version DESC LIMIT 1;
        IF NOT FOUND OR pol.legal_hold OR NOT pol.erasure_allowed OR pol.valid_until<=now() THEN
            RETURN NEW;
        END IF;
    END LOOP;
    FOR item IN SELECT e.booking_id FROM public.booking_stay_completion_evidence e
      JOIN public.booking_stays s ON s.organization_id=e.organization_id AND s.booking_id=e.booking_id
      WHERE e.organization_id=NEW.organization_id
        AND (NEW.scope='organization' OR (NEW.scope='contact' AND s.original_subject_id=NEW.subject_id))
      ORDER BY e.booking_id FOR UPDATE OF e,s LOOP
        INSERT INTO public.booking_completion_erasure_markers VALUES(txid_current(),NEW.organization_id,item.booking_id);
        DELETE FROM public.booking_stay_completion_evidence
          WHERE organization_id=NEW.organization_id AND booking_id=item.booking_id;
        DELETE FROM public.booking_completion_erasure_markers
          WHERE transaction_id=txid_current() AND organization_id=NEW.organization_id AND booking_id=item.booking_id;
    END LOOP;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.erase_hotel_completion_evidence() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS erase_hotel_completion_evidence ON public.gdpr_erasure_barriers;
CREATE TRIGGER erase_hotel_completion_evidence AFTER INSERT ON public.gdpr_erasure_barriers
FOR EACH ROW EXECUTE FUNCTION public.erase_hotel_completion_evidence();

-- Trigger records only a new terminal transition with an already-ended and
-- coherent civil-night allocation. It cannot establish application actor
-- authorization; retention remains disabled until that boundary is secured.
CREATE OR REPLACE FUNCTION public.capture_hotel_completion_evidence()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE a public.booking_allocation_sets%ROWTYPE; s public.booking_stays%ROWTYPE;
        observed_at timestamptz := clock_timestamp();
BEGIN
    IF OLD.stato IN ('in_attesa','confermata','da_verificare')
       AND OLD.completata_at IS NULL AND NEW.stato='completata'
       AND NEW.completata_at IS NOT NULL AND NEW.no_show_at IS NULL
       AND NEW.completata_at BETWEEN observed_at - interval '1 minute' AND observed_at THEN
        SELECT * INTO a FROM public.booking_allocation_sets
          WHERE organization_id=NEW.organization_id AND booking_id=NEW.id FOR UPDATE;
        SELECT * INTO s FROM public.booking_stays
          WHERE organization_id=NEW.organization_id AND booking_id=NEW.id FOR UPDATE;
        IF a.booking_id IS NOT NULL AND s.booking_id IS NOT NULL
           AND a.state='active' AND a.temporal_kind='civil_nights'
           AND a.ends_at <= observed_at
           AND (a.arrival_date,a.departure_date) IS NOT DISTINCT FROM (s.arrival_date,s.departure_date)
           AND EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name=a.timezone)
           AND EXISTS (SELECT 1 FROM public.booking_resource_allocations r
             WHERE r.organization_id=NEW.organization_id AND r.booking_id=NEW.id
               AND r.state='active' AND r.allocation_revision=a.allocation_revision
               AND r.catalog_revision=a.catalog_revision AND r.starts_at=a.starts_at AND r.ends_at=a.ends_at)
           AND NOT EXISTS (SELECT 1 FROM public.booking_resource_allocations r
             WHERE r.organization_id=NEW.organization_id AND r.booking_id=NEW.id
               AND (r.state,r.allocation_revision,r.catalog_revision,r.starts_at,r.ends_at)
                   IS DISTINCT FROM ('active',a.allocation_revision,a.catalog_revision,a.starts_at,a.ends_at))
           AND NOT EXISTS (SELECT 1 FROM public.booking_stay_completion_evidence
             WHERE organization_id=NEW.organization_id AND booking_id=NEW.id)
        THEN
            INSERT INTO public.booking_stay_completion_evidence(organization_id,booking_id,completed_at,timezone)
              VALUES(NEW.organization_id,NEW.id,observed_at,a.timezone);
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.capture_hotel_completion_evidence() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS capture_hotel_completion_evidence ON public.bookings;
CREATE TRIGGER capture_hotel_completion_evidence AFTER UPDATE OF stato,completata_at ON public.bookings
FOR EACH ROW EXECUTE FUNCTION public.capture_hotel_completion_evidence();
