-- Durable A03 transport state, worker fencing and append-only recovery evidence.
-- Legacy unresolved attempts are deliberately backfilled as uncertain: age or
-- the absence of a worker lease cannot prove that an HTTP request was unsent.
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_events_org_id_pair
    ON usage_events (organization_id, id);
CREATE TABLE IF NOT EXISTS provider_attempt_runtime (
    organization_id UUID,
    attempt_id UUID NOT NULL,
    worker_id UUID,
    lease_epoch BIGINT NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_expires_at TIMESTAMPTZ,
    dispatch_state TEXT NOT NULL CHECK (
        dispatch_state IN ('prepared','dispatching','uncertain','verified','not_sent','resolved')
    ),
    generation_id TEXT CHECK (
        generation_id IS NULL OR generation_id ~ '^gen-[0-9A-Za-z-]{1,120}$'
    ),
    provider_request_id TEXT CHECK (
        provider_request_id IS NULL OR provider_request_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$'
    ),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (attempt_id),
    FOREIGN KEY (attempt_id)
        REFERENCES usage_events(id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, attempt_id)
        REFERENCES usage_events(organization_id, id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_provider_attempt_runtime_unresolved
    ON provider_attempt_runtime (organization_id, lease_expires_at)
    WHERE dispatch_state IN ('prepared','dispatching','uncertain');

INSERT INTO provider_attempt_runtime (
    organization_id, attempt_id, worker_id, lease_epoch, lease_expires_at,
    dispatch_state, generation_id, provider_request_id
)
SELECT organization_id, id, NULL, 0, NULL,
       CASE metadata->>'accounting_status'
            WHEN 'recorded' THEN 'verified'
            WHEN 'resolved' THEN 'resolved'
            ELSE 'uncertain' END,
       CASE WHEN metadata->>'provider_generation_id' ~ '^gen-[0-9A-Za-z-]{1,120}$'
            THEN metadata->>'provider_generation_id' ELSE NULL END,
       CASE WHEN metadata->>'provider_request_id' ~ '^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$'
            THEN metadata->>'provider_request_id' ELSE NULL END
  FROM usage_events
 WHERE event_type='system.provider_attempt'
ON CONFLICT (attempt_id) DO NOTHING;

ALTER TABLE provider_attempt_runtime ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS provider_attempt_runtime_deny_clients ON provider_attempt_runtime;
CREATE POLICY provider_attempt_runtime_deny_clients ON provider_attempt_runtime
    FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
REVOKE ALL ON provider_attempt_runtime FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE ON provider_attempt_runtime TO service_role;
REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON provider_attempt_runtime
    FROM PUBLIC, anon, authenticated, service_role;

-- This table stores only state transitions and provider-issued accounting
-- evidence; never request/response text, credentials, or customer content.
CREATE TABLE IF NOT EXISTS provider_attempt_evidence (
    id UUID PRIMARY KEY,
    organization_id UUID,
    attempt_id UUID NOT NULL,
    worker_id UUID,
    lease_epoch BIGINT NOT NULL CHECK (lease_epoch >= 0),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'prepared','dispatch_authorized','provider_ids_observed',
        'provider_response_verified','provider_failure_uncertain',
        'worker_fenced','attempt_not_sent','recovery_claimed',
        'openrouter_generation_verified','attempt_reconciled'
    )),
    details JSONB NOT NULL CHECK (
        jsonb_typeof(details)='object' AND octet_length(details::text) <= 16384
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (attempt_id)
        REFERENCES usage_events(id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, attempt_id)
        REFERENCES usage_events(organization_id, id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_provider_attempt_evidence_scope
    ON provider_attempt_evidence (organization_id, attempt_id, created_at, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_attempt_evidence_response_once
    ON provider_attempt_evidence (organization_id, attempt_id, lease_epoch) NULLS NOT DISTINCT
    WHERE event_type='provider_response_verified';

ALTER TABLE provider_attempt_evidence ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS provider_attempt_evidence_deny_clients ON provider_attempt_evidence;
CREATE POLICY provider_attempt_evidence_deny_clients ON provider_attempt_evidence
    FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);
REVOKE ALL ON provider_attempt_evidence FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT ON provider_attempt_evidence TO service_role;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON provider_attempt_evidence
    FROM PUBLIC, anon, authenticated, service_role;

-- A nullable composite FK skips the scope comparison for detached history.
-- Confirm NULL against the parent while holding a key lock. The existing FK
-- still handles scoped changes and propagates SET NULL on organization deletion.
CREATE OR REPLACE FUNCTION public.provider_attempt_check_null_scope() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE parent_org uuid;
BEGIN
    SELECT organization_id INTO parent_org
      FROM public.usage_events WHERE id = NEW.attempt_id FOR KEY SHARE;
    IF NOT FOUND OR parent_org IS NOT NULL THEN
        RAISE EXCEPTION 'Detached provider attempt requires a detached billing event'
            USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.provider_attempt_check_null_scope()
    FROM PUBLIC, anon, authenticated;
DROP TRIGGER IF EXISTS provider_attempt_check_null_scope ON provider_attempt_runtime;
CREATE TRIGGER provider_attempt_check_null_scope
    BEFORE INSERT OR UPDATE OF organization_id, attempt_id ON provider_attempt_runtime
    FOR EACH ROW WHEN (NEW.organization_id IS NULL)
    EXECUTE FUNCTION public.provider_attempt_check_null_scope();
DROP TRIGGER IF EXISTS provider_attempt_check_null_scope ON provider_attempt_evidence;
CREATE TRIGGER provider_attempt_check_null_scope
    BEFORE INSERT OR UPDATE OF organization_id, attempt_id ON provider_attempt_evidence
    FOR EACH ROW WHEN (NEW.organization_id IS NULL)
    EXECUTE FUNCTION public.provider_attempt_check_null_scope();

-- The reverse transition would skip the composite FK's update cascade.
-- Detached billing history has no supported path back into a tenant.
CREATE OR REPLACE FUNCTION public.provider_attempt_keep_detached() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF OLD.event_type = 'system.provider_attempt'
       OR EXISTS (SELECT 1 FROM public.provider_attempt_runtime WHERE attempt_id = OLD.id)
       OR EXISTS (SELECT 1 FROM public.provider_attempt_evidence WHERE attempt_id = OLD.id) THEN
        RAISE EXCEPTION 'Detached provider attempt cannot be assigned to a tenant'
            USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.provider_attempt_keep_detached()
    FROM PUBLIC, anon, authenticated;
DROP TRIGGER IF EXISTS provider_attempt_keep_detached ON public.usage_events;
CREATE TRIGGER provider_attempt_keep_detached
    BEFORE UPDATE OF organization_id ON public.usage_events
    FOR EACH ROW WHEN (OLD.organization_id IS NULL AND NEW.organization_id IS NOT NULL)
    EXECUTE FUNCTION public.provider_attempt_keep_detached();
