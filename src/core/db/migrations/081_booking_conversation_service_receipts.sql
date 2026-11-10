-- Extend immutable conversation receipts for the private inbound lifecycle.
-- Existing writers remain human by default; source evidence may be purged later.
ALTER TABLE public.booking_conversation_requests
    ADD COLUMN IF NOT EXISTS actor_kind text NOT NULL DEFAULT 'human',
    ADD COLUMN IF NOT EXISTS service_inbox_id uuid,
    ADD COLUMN IF NOT EXISTS service_operation_id uuid,
    ALTER COLUMN actor_user_id DROP NOT NULL;

DO $migration$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.booking_conversation_requests'::regclass
          AND conname = 'booking_conversation_requests_actor_shape') THEN
        ALTER TABLE public.booking_conversation_requests
            ADD CONSTRAINT booking_conversation_requests_actor_shape CHECK (
                (actor_kind = 'human' AND actor_user_id IS NOT NULL
                    AND service_inbox_id IS NULL AND service_operation_id IS NULL)
                OR (actor_kind = 'inbound_worker' AND actor_user_id IS NULL
                    AND service_inbox_id IS NOT NULL
                    AND ((action = 'proposal' AND service_operation_id IS NOT NULL)
                      OR (action = 'confirmation' AND service_operation_id IS NULL)))
            );
    END IF;
END
$migration$;

-- Lock the precise ingress and operation evidence while accepting a receipt.
-- No foreign key is added: independent receipt identifiers survive retention.
CREATE OR REPLACE FUNCTION public.booking_conversation_validate_worker_source()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF NEW.actor_kind = 'inbound_worker' THEN
        -- SECURITY DEFINER changes current_user; inspect the actual active role.
        IF COALESCE(NULLIF(current_setting('role'), 'none'), session_user)
           IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
               WHERE oid = 'public.inbound_worker_operations'::regclass)) THEN
            RAISE EXCEPTION 'Worker receipt requires lifecycle owner' USING ERRCODE = '42501';
        END IF;
        PERFORM 1 FROM public.inbound_worker_receipts r
        JOIN public.meta_webhook_inbox i ON i.id = r.inbox_id
            AND i.organization_id = r.organization_id
        WHERE r.organization_id = NEW.organization_id
          AND r.inbox_id = NEW.service_inbox_id
          AND r.message_id = NEW.source_message_id
          AND i.channel = 'whatsapp' AND i.status = 'completed'
        FOR SHARE OF r, i;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Invalid worker ingress source' USING ERRCODE = '23503';
        END IF;
        IF NEW.action = 'proposal' THEN
            PERFORM 1 FROM public.inbound_worker_operations o
            WHERE o.organization_id = NEW.organization_id
              AND o.inbox_id = NEW.service_inbox_id
              AND o.message_id = NEW.source_message_id
              AND o.operation_id = NEW.service_operation_id
            FOR SHARE OF o;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Invalid worker operation source' USING ERRCODE = '23503';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.booking_conversation_validate_worker_source() FROM PUBLIC;
DROP TRIGGER IF EXISTS booking_conversation_validate_worker_source ON public.booking_conversation_requests;
CREATE TRIGGER booking_conversation_validate_worker_source
    BEFORE INSERT ON public.booking_conversation_requests
    FOR EACH ROW EXECUTE FUNCTION public.booking_conversation_validate_worker_source();
