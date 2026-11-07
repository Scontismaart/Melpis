CREATE TABLE public.booking_sector_admission_requests (
    organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    request_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    published_revision bigint NOT NULL CHECK (published_revision > 0),
    enabled boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, request_id)
);
ALTER TABLE public.booking_sector_admission_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_sector_admission_requests FROM PUBLIC, anon, authenticated, service_role;
CREATE FUNCTION public.booking_sector_admission_request_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
    RAISE EXCEPTION 'Admission receipt is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.booking_sector_admission_request_immutable() FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER booking_sector_admission_request_immutable
BEFORE UPDATE OR DELETE ON public.booking_sector_admission_requests
FOR EACH ROW EXECUTE FUNCTION public.booking_sector_admission_request_immutable();
