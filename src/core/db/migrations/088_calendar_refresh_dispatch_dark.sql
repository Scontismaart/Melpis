-- DRAFT: local/synthetic validation only; no worker or authority activation.
-- A non-null marker means refresh may have reached the provider. Never expire it
-- into a retry. A newly authorized connection replaces it under a new epoch.
ALTER TABLE public.google_calendar_credentials
 ADD COLUMN IF NOT EXISTS refresh_dispatch_id uuid;
