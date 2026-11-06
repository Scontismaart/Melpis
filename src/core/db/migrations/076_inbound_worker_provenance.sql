-- Private evidence only. No client or application role receives privileges here.
CREATE TABLE IF NOT EXISTS inbound_worker_receipts (
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    inbox_id uuid NOT NULL REFERENCES meta_webhook_inbox(id) ON DELETE CASCADE,
    message_id uuid NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    PRIMARY KEY (organization_id, inbox_id, message_id)
);
CREATE TABLE IF NOT EXISTS inbound_worker_operations (
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    inbox_id uuid NOT NULL,
    message_id uuid NOT NULL,
    operation_id uuid NOT NULL REFERENCES usage_events(id) ON DELETE CASCADE,
    PRIMARY KEY (organization_id, message_id, operation_id),
    FOREIGN KEY (organization_id, inbox_id, message_id)
        REFERENCES inbound_worker_receipts (organization_id, inbox_id, message_id) ON DELETE CASCADE
);
ALTER TABLE inbound_worker_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE inbound_worker_operations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON inbound_worker_receipts, inbound_worker_operations FROM PUBLIC;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    REVOKE ALL ON inbound_worker_receipts, inbound_worker_operations FROM service_role;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON inbound_worker_receipts, inbound_worker_operations FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON inbound_worker_receipts, inbound_worker_operations FROM authenticated;
  END IF;
END $$;
