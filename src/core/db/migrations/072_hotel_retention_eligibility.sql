-- Eligibility only: the caller must establish that p_completed_at is the
-- trusted completion instant for a completed hotel stay and p_timezone is the
-- property's trusted IANA zone. This function does not authorize any purge.
CREATE OR REPLACE FUNCTION public.hotel_completed_stay_retention_eligible(
    p_completed_at timestamptz, p_timezone text, p_as_of timestamptz
) RETURNS boolean
LANGUAGE sql STABLE
SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN p_completed_at IS NULL OR p_as_of IS NULL OR p_timezone IS NULL
          OR NOT isfinite(p_completed_at) OR NOT isfinite(p_as_of)
          OR NOT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = p_timezone)
        THEN false
        ELSE p_as_of >= (
            (p_completed_at AT TIME ZONE p_timezone + interval '12 months')
            AT TIME ZONE p_timezone
        )
    END
$$;
