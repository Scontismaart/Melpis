ALTER TABLE public.booking_allocation_requests
    ADD COLUMN IF NOT EXISTS mutation_hash text CHECK (mutation_hash IS NULL OR mutation_hash ~ '^[a-f0-9]{64}$');
