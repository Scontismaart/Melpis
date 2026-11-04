-- Proposed local-only foundation. Sensitive rollout needs explicit approval.
CREATE TABLE IF NOT EXISTS public.booking_privacy_classifications (
    organization_id uuid PRIMARY KEY REFERENCES public.organizations(id) ON DELETE CASCADE,
    sector text NOT NULL CHECK (sector IN ('ristorante','parrucchiere','centro_estetico','hotel_bnb','hotel','medical_disabled')),
    operational_notes_allowed boolean NOT NULL DEFAULT false,
    revision integer NOT NULL CHECK (revision > 0),
    evidence_reference text NOT NULL CHECK (length(evidence_reference) BETWEEN 1 AND 256),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.booking_privacy_classifications ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_privacy_classifications FROM PUBLIC, anon, authenticated, service_role;

-- The writer shares this lock while deciding. An absent row is protected too.
CREATE OR REPLACE FUNCTION public.booking_privacy_classification_lock() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE target uuid;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.organization_id <> OLD.organization_id THEN
        RAISE EXCEPTION 'Classification organization cannot change';
    END IF;
    IF TG_OP = 'DELETE' THEN target := OLD.organization_id; ELSE target := NEW.organization_id; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(target::text, 815));
    IF TG_OP = 'UPDATE' AND NEW.revision <= OLD.revision THEN
        RAISE EXCEPTION 'Classification revision must increase';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_privacy_classification_lock() FROM PUBLIC, anon, authenticated, service_role;
DROP TRIGGER IF EXISTS booking_privacy_classification_lock ON public.booking_privacy_classifications;
CREATE TRIGGER booking_privacy_classification_lock BEFORE INSERT OR UPDATE OR DELETE
    ON public.booking_privacy_classifications FOR EACH ROW EXECUTE FUNCTION public.booking_privacy_classification_lock();

-- Owner-owned view exposes no evidence contents. No mutation privileges are granted.
CREATE OR REPLACE VIEW public.booking_privacy_classification_reader WITH (security_barrier=true) AS
SELECT organization_id, sector, operational_notes_allowed, revision,
       (length(evidence_reference) > 0) AS evidence_valid
FROM public.booking_privacy_classifications;
REVOKE ALL ON public.booking_privacy_classification_reader FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.booking_privacy_classification_reader TO service_role;
DO $$
DECLARE owner_oid oid;
BEGIN
    SELECT relowner INTO owner_oid FROM pg_class WHERE oid='public.booking_privacy_classifications'::regclass;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role' AND
       (oid=owner_oid OR rolsuper OR pg_has_role(oid, owner_oid, 'MEMBER'))) THEN
        RAISE EXCEPTION 'Classification table must be owned by a separate administrative role';
    END IF;
END $$;

-- A positive healthcare classification restricts every booking writer, even
-- one that does not use the application repository. Administration can still
-- use the existing authorized date, time and customer identity fields.
CREATE OR REPLACE FUNCTION public.minimize_medical_booking_text() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE profile_sector text;
BEGIN
    PERFORM pg_advisory_xact_lock_shared(hashtextextended(NEW.organization_id::text,815));
    SELECT lower(btrim(business_profile->>'verticale')) INTO profile_sector
      FROM public.organizations WHERE id=NEW.organization_id FOR SHARE;
    IF profile_sector IN ('studio_medico_dentista','studio_medico','medico','clinica',
                  'sanitario','odontoiatra','dentista','fisioterapia',
                  'dottore','poliambulatorio','sanita','medical_disabled')
       OR EXISTS (SELECT 1 FROM public.booking_privacy_classifications
                  WHERE organization_id=NEW.organization_id AND sector='medical_disabled') THEN
        NEW.note := '';
        NEW.tipo_evento := '';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.minimize_medical_booking_text() FROM PUBLIC,anon,authenticated,service_role;
DROP TRIGGER IF EXISTS minimize_medical_booking_text ON public.bookings;
CREATE TRIGGER minimize_medical_booking_text
    BEFORE INSERT OR UPDATE OF organization_id,note,tipo_evento ON public.bookings
    FOR EACH ROW EXECUTE FUNCTION public.minimize_medical_booking_text();
