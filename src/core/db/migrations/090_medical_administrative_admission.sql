-- C2.4: permit the same owner-gated, empty-history admission fence for the
-- medical administrative appointment path. Clinical data is still rejected
-- by the catalog, inbound boundary, conversation state and owner mutation.
ALTER TABLE public.booking_sector_admission_fences
    DROP CONSTRAINT IF EXISTS booking_sector_admission_fences_sector_check;
ALTER TABLE public.booking_sector_admission_fences
    ADD CONSTRAINT booking_sector_admission_fences_sector_check
    CHECK (sector IN ('parrucchiere','centro_estetico','hotel_bnb','studio_medico_dentista'));

CREATE OR REPLACE FUNCTION public.booking_sector_activate_empty(p_org uuid, p_sector text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF COALESCE(NULLIF(current_setting('role'), 'none'), session_user) <>
        pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='public.booking_sector_admission_fences'::regclass)) THEN
        RAISE EXCEPTION 'owner required' USING ERRCODE='42501';
    END IF;
    IF p_sector NOT IN ('parrucchiere','centro_estetico','hotel_bnb','studio_medico_dentista') OR p_org IS NULL THEN
        RAISE EXCEPTION 'invalid sector';
    END IF;
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
    IF EXISTS(SELECT 1 FROM public.bookings WHERE organization_id=p_org) THEN
        RAISE EXCEPTION 'tenant must have no bookings and matching sector';
    END IF;
    INSERT INTO public.booking_sector_admission_fences(organization_id,sector) VALUES(p_org,p_sector);
END $$;

REVOKE ALL ON FUNCTION public.booking_sector_activate_empty(uuid,text)
    FROM PUBLIC, anon, authenticated, service_role;
