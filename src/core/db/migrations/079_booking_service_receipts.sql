-- Extend existing immutable receipts without changing legacy human inserts.
-- A service receipt must reference the exact tenant-bound inbound operation.
DO $migration$
BEGIN
    ALTER TABLE public.booking_allocation_requests
        ADD COLUMN IF NOT EXISTS actor_kind text NOT NULL DEFAULT 'human',
        ADD COLUMN IF NOT EXISTS service_inbox_id uuid,
        ADD COLUMN IF NOT EXISTS service_message_id uuid,
        ADD COLUMN IF NOT EXISTS service_operation_id uuid,
        ALTER COLUMN actor_user_id DROP NOT NULL;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.booking_allocation_requests'::regclass AND conname='booking_allocation_requests_actor_shape') THEN
        ALTER TABLE public.booking_allocation_requests ADD CONSTRAINT booking_allocation_requests_actor_shape CHECK (
            (actor_kind = 'human' AND actor_user_id IS NOT NULL AND service_inbox_id IS NULL AND service_message_id IS NULL AND service_operation_id IS NULL)
            OR (actor_kind = 'inbound_worker' AND actor_user_id IS NULL AND service_inbox_id IS NOT NULL AND service_message_id IS NOT NULL AND service_operation_id IS NOT NULL)
        );
    END IF;

END
$migration$;

-- The evidence may later be removed by retention. Lock it while accepting the
-- immutable receipt, but retain the source identifiers independently afterward.
CREATE OR REPLACE FUNCTION public.booking_allocation_validate_worker_source()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF NEW.actor_kind = 'inbound_worker' THEN
        -- SECURITY DEFINER changes current_user; the active SET ROLE (or the
        -- login identity when no role is set) preserves the actual caller.
        IF COALESCE(NULLIF(current_setting('role'), 'none'), session_user)
           IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
               WHERE oid = 'public.inbound_worker_operations'::regclass)) THEN
            RAISE EXCEPTION 'Worker receipt requires lifecycle owner' USING ERRCODE = '42501';
        END IF;
        PERFORM 1 FROM public.inbound_worker_operations
        WHERE organization_id = NEW.organization_id AND inbox_id = NEW.service_inbox_id
          AND message_id = NEW.service_message_id AND operation_id = NEW.service_operation_id
        FOR KEY SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Invalid worker source' USING ERRCODE = '23503';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_allocation_validate_worker_source() FROM PUBLIC;
DROP TRIGGER IF EXISTS booking_allocation_validate_worker_source ON public.booking_allocation_requests;
CREATE TRIGGER booking_allocation_validate_worker_source BEFORE INSERT ON public.booking_allocation_requests
    FOR EACH ROW EXECUTE FUNCTION public.booking_allocation_validate_worker_source();
