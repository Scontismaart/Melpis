SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- These SECURITY DEFINER functions are trigger-only. Supabase's direct
-- role grants survive a REVOKE from PUBLIC. Remove those unnecessary direct
-- execution grants without altering the existing trigger or worker checks.
REVOKE ALL ON FUNCTION public.booking_allocation_validate_worker_source()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.booking_conversation_validate_worker_source()
    FROM PUBLIC, anon, authenticated, service_role;
