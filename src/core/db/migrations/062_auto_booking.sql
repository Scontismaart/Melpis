-- A05: only the trusted backend may mutate booking authority and state.
-- Historic tenants remain opted out because config.a05 is absent by default.
DROP POLICY IF EXISTS bookings_org_member ON public.bookings;
DROP POLICY IF EXISTS booking_settings_org_member ON public.booking_settings;
DROP POLICY IF EXISTS external_booking_sync_org_member ON public.external_booking_sync;
DROP POLICY IF EXISTS bookings_member_read ON public.bookings;
DROP POLICY IF EXISTS booking_settings_member_read ON public.booking_settings;
DROP POLICY IF EXISTS external_booking_sync_member_read ON public.external_booking_sync;

CREATE POLICY bookings_member_read ON public.bookings FOR SELECT TO authenticated
USING (organization_id IN (
    SELECT om.organization_id FROM public.organization_memberships om
    JOIN public.user_profiles up ON up.id = om.user_id
    WHERE up.auth_user_id = auth.uid()
));
CREATE POLICY booking_settings_member_read ON public.booking_settings FOR SELECT TO authenticated
USING (organization_id IN (
    SELECT om.organization_id FROM public.organization_memberships om
    JOIN public.user_profiles up ON up.id = om.user_id
    WHERE up.auth_user_id = auth.uid()
));
CREATE POLICY external_booking_sync_member_read ON public.external_booking_sync FOR SELECT TO authenticated
USING (organization_id IN (
    SELECT om.organization_id FROM public.organization_memberships om
    JOIN public.user_profiles up ON up.id = om.user_id
    WHERE up.auth_user_id = auth.uid()
));

REVOKE ALL ON public.bookings, public.booking_settings, public.external_booking_sync FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON public.bookings, public.booking_settings, public.external_booking_sync FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON public.bookings, public.booking_settings, public.external_booking_sync FROM authenticated;
        GRANT SELECT ON public.bookings, public.booking_settings, public.external_booking_sync TO authenticated;
    END IF;
END $$;

-- Install the scoped parent keys before creating the confirmation outbox.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.bookings'::regclass AND conname = 'bookings_org_id_unique'
    ) THEN
        ALTER TABLE public.bookings
            ADD CONSTRAINT bookings_org_id_unique UNIQUE (organization_id, id);
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS messages_org_id_unique
    ON public.messages (organization_id, id);

CREATE TABLE IF NOT EXISTS public.booking_confirmation_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id),
    booking_id UUID NOT NULL,
    inbound_message_id UUID NOT NULL,
    outbound_message_id UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting_for_reply'
        CHECK (status IN ('waiting_for_reply', 'scheduled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    scheduled_at TIMESTAMPTZ,
    CONSTRAINT booking_confirmation_outbox_booking_scope
        FOREIGN KEY (organization_id, booking_id) REFERENCES public.bookings (organization_id, id),
    CONSTRAINT booking_confirmation_outbox_inbound_scope
        FOREIGN KEY (organization_id, inbound_message_id) REFERENCES public.messages (organization_id, id),
    CONSTRAINT booking_confirmation_outbox_outbound_scope
        FOREIGN KEY (organization_id, outbound_message_id) REFERENCES public.messages (organization_id, id),
    UNIQUE (organization_id, booking_id),
    UNIQUE (organization_id, outbound_message_id)
);
CREATE INDEX IF NOT EXISTS idx_booking_confirmation_outbox_waiting
    ON public.booking_confirmation_outbox (created_at)
    WHERE status = 'waiting_for_reply';
ALTER TABLE public.booking_confirmation_outbox ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.booking_confirmation_outbox FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON public.booking_confirmation_outbox FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON public.booking_confirmation_outbox FROM authenticated;
    END IF;
END $$;
