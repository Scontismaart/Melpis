-- Private dormant worker fencing. No runtime role receives authority here.
CREATE TABLE IF NOT EXISTS booking_worker_claims (
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    message_id uuid NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    claim_id uuid NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    lease_until timestamptz NOT NULL,
    PRIMARY KEY (organization_id, message_id)
);
ALTER TABLE booking_worker_claims ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON booking_worker_claims FROM PUBLIC;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    REVOKE ALL ON booking_worker_claims FROM service_role;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON booking_worker_claims FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON booking_worker_claims FROM authenticated;
  END IF;
END $$;
