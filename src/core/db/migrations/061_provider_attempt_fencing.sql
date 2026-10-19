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
REVOKE ALL ON provider_attempt_runtime FROM PUBLIC, anon, authenticated;
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
REVOKE ALL ON provider_attempt_evidence FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON provider_attempt_evidence TO service_role;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON provider_attempt_evidence
    FROM PUBLIC, anon, authenticated, service_role;
