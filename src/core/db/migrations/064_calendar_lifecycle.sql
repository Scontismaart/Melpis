-- B01 technical capability only. No historical opt-in or remote activation.
ALTER TABLE google_calendar_credentials ALTER COLUMN access_token DROP NOT NULL;
ALTER TABLE google_calendar_credentials ALTER COLUMN refresh_token DROP NOT NULL;
ALTER TABLE google_calendar_credentials ALTER COLUMN token_expiry DROP NOT NULL;
ALTER TABLE google_calendar_credentials ADD COLUMN IF NOT EXISTS connection_epoch UUID NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE google_calendar_credentials ADD COLUMN IF NOT EXISTS oauth_generation UUID NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE google_calendar_credentials ADD COLUMN IF NOT EXISTS credential_version BIGINT NOT NULL DEFAULT 0;
ALTER TABLE google_calendar_credentials ADD COLUMN IF NOT EXISTS connection_status TEXT NOT NULL DEFAULT 'reconnect_required';
ALTER TABLE google_calendar_credentials ADD COLUMN IF NOT EXISTS granted_scopes JSONB NOT NULL DEFAULT '[]';
ALTER TABLE google_calendar_credentials ADD COLUMN IF NOT EXISTS resource_calendar_ids JSONB NOT NULL DEFAULT '[]';
ALTER TABLE google_calendar_credentials ADD COLUMN IF NOT EXISTS last_error_code TEXT;
ALTER TABLE google_calendar_credentials ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ;
ALTER TABLE oauth_nonces ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE oauth_nonces ADD COLUMN IF NOT EXISTS calendar_generation UUID;

-- One existing queue, with separate desired/claimed/applied generations.
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_action TEXT;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_desired_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_claimed_revision BIGINT;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_applied_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_connection_epoch UUID;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_id TEXT;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_fence BIGINT NOT NULL DEFAULT 0;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_lease_token UUID;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_lease_until TIMESTAMPTZ;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_dispatch_state TEXT NOT NULL DEFAULT 'not_sent';
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_claimed_action TEXT;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_payload_hash TEXT;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_event_etag TEXT;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_verified_at TIMESTAMPTZ;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_inbound_message_id UUID;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_notification_channel TEXT;
ALTER TABLE external_booking_sync ADD COLUMN IF NOT EXISTS calendar_authoritative BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS calendar_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS calendar_confirm_pending BOOLEAN NOT NULL DEFAULT FALSE;
-- Explicit staff confirmations do not have an inbound AI message to wait for.
ALTER TABLE booking_confirmation_outbox ALTER COLUMN inbound_message_id DROP NOT NULL;
ALTER TABLE booking_confirmation_outbox ADD COLUMN IF NOT EXISTS booking_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE booking_confirmation_outbox DROP CONSTRAINT IF EXISTS booking_confirmation_outbox_organization_id_booking_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS booking_confirmation_revision_unique
    ON booking_confirmation_outbox(organization_id,booking_id,booking_revision);

-- Do not reinterpret pre-B01 work lacking a verified connection context.
UPDATE external_booking_sync SET sync_status='blocked',sync_error='legacy_context_unverified'
WHERE provider='google' AND calendar_connection_epoch IS NULL
  AND sync_status IN ('pending','pending_retry','processing');

CREATE INDEX IF NOT EXISTS calendar_sync_pending ON external_booking_sync(updated_at)
WHERE provider='google' AND calendar_connection_epoch IS NOT NULL;

-- No client may read encrypted credentials, inject nonces or change authority.
DROP POLICY IF EXISTS google_calendar_credentials_org_member ON google_calendar_credentials;
REVOKE ALL ON google_calendar_credentials,oauth_nonces FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON google_calendar_credentials,oauth_nonces TO service_role;

CREATE OR REPLACE FUNCTION calendar_track_booking() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE mode TEXT; link RECORD; action TEXT; verified BOOLEAN := FALSE;
BEGIN
    SELECT config#>>'{a05,authority}' INTO mode FROM booking_settings WHERE organization_id=NEW.organization_id;
    IF TG_OP='UPDATE' THEN
        -- A pending confirmation authorizes this exact proposal only. Editing
        -- details requires a fresh explicit confirmation, never inherited intent.
        IF ROW(NEW.data,NEW.ora,NEW.coperti,NEW.nome_cliente,NEW.telefono,NEW.note)
           IS DISTINCT FROM ROW(OLD.data,OLD.ora,OLD.coperti,OLD.nome_cliente,OLD.telefono,OLD.note)
           OR NEW.stato IN ('cancellata','cancellato','rifiutata','no_show') THEN
            NEW.calendar_confirm_pending := FALSE;
        END IF;
        IF ROW(NEW.data,NEW.ora,NEW.coperti,NEW.nome_cliente,NEW.telefono,NEW.note,NEW.stato,NEW.calendar_confirm_pending)
           IS NOT DISTINCT FROM ROW(OLD.data,OLD.ora,OLD.coperti,OLD.nome_cliente,OLD.telefono,OLD.note,OLD.stato,OLD.calendar_confirm_pending)
        THEN RETURN NEW; END IF;
        -- A verified authoritative finalization does not create a new version.
        IF mode='google_authoritative' AND NEW.stato='confermata' AND OLD.calendar_confirm_pending
           AND NOT NEW.calendar_confirm_pending
           AND ROW(NEW.data,NEW.ora,NEW.coperti,NEW.nome_cliente,NEW.telefono,NEW.note)
               IS NOT DISTINCT FROM ROW(OLD.data,OLD.ora,OLD.coperti,OLD.nome_cliente,OLD.telefono,OLD.note) THEN
            SELECT EXISTS(SELECT 1 FROM external_booking_sync s
                JOIN google_calendar_credentials c ON c.organization_id=s.organization_id
                WHERE s.organization_id=NEW.organization_id AND s.internal_booking_id=NEW.id
                  AND s.provider='google' AND s.sync_status='succeeded'
                  AND s.calendar_applied_revision=OLD.calendar_revision
                  AND s.calendar_connection_epoch=c.connection_epoch
                  AND c.connection_status='connected' AND c.sync_enabled
                  AND s.calendar_dispatch_state='verified' AND s.calendar_authoritative
                  AND s.calendar_verified_at>NOW()-INTERVAL '30 seconds') INTO verified;
            IF verified THEN RETURN NEW; END IF;
        END IF;
        NEW.calendar_revision := OLD.calendar_revision+1;
    ELSE NEW.calendar_revision := 1;
    END IF;
    IF mode='google_authoritative' AND NEW.stato='confermata' THEN
        RAISE EXCEPTION 'Calendar authority requires verified finalization' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS calendar_track_booking ON bookings;
CREATE TRIGGER calendar_track_booking BEFORE INSERT OR UPDATE ON bookings
FOR EACH ROW EXECUTE FUNCTION calendar_track_booking();

CREATE OR REPLACE FUNCTION calendar_queue_booking() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE mode TEXT; link RECORD; action TEXT;
BEGIN
    IF TG_OP='UPDATE' AND NEW.calendar_revision=OLD.calendar_revision THEN RETURN NEW; END IF;
    SELECT config#>>'{a05,authority}' INTO mode FROM booking_settings WHERE organization_id=NEW.organization_id;
    IF mode NOT IN ('internal_calendar_sync','google_authoritative') OR mode IS NULL THEN
        -- A mode change never resurrects a booking or hides a required cleanup.
        IF NEW.stato IN ('cancellata','cancellato','rifiutata','no_show') THEN
            UPDATE external_booking_sync SET calendar_action='delete',calendar_desired_revision=NEW.calendar_revision,
                sync_status=CASE WHEN sync_status IN ('processing','uncertain') THEN sync_status ELSE 'pending' END,
                updated_at=NOW()
            WHERE organization_id=NEW.organization_id AND internal_booking_id=NEW.id AND provider='google';
        ELSE
            UPDATE external_booking_sync SET sync_status=CASE WHEN calendar_dispatch_state='possibly_sent'
                THEN 'uncertain' ELSE 'blocked' END,sync_error='authority_changed',updated_at=NOW()
            WHERE organization_id=NEW.organization_id AND internal_booking_id=NEW.id AND provider='google';
        END IF;
        RETURN NEW;
    END IF;
    SELECT connection_epoch,calendar_id,connection_status,sync_enabled INTO link
        FROM google_calendar_credentials WHERE organization_id=NEW.organization_id;
    action := CASE WHEN NEW.stato IN ('cancellata','cancellato','rifiutata','no_show') THEN 'delete'
                   WHEN NEW.calendar_confirm_pending THEN 'authoritative' ELSE 'upsert' END;
    INSERT INTO external_booking_sync(id,organization_id,idempotency_key,internal_booking_id,
        provider,sync_status,calendar_action,calendar_desired_revision,
        calendar_connection_epoch,calendar_id,calendar_authoritative)
    VALUES(gen_random_uuid(),NEW.organization_id,'google-booking:'||NEW.organization_id||':'||NEW.id,NEW.id,
        'google',CASE WHEN link.connection_status='connected' AND link.sync_enabled THEN 'pending' ELSE 'blocked' END,
        action,NEW.calendar_revision,link.connection_epoch,link.calendar_id,mode='google_authoritative')
    ON CONFLICT(organization_id,idempotency_key) DO UPDATE SET
        calendar_action=EXCLUDED.calendar_action,calendar_desired_revision=EXCLUDED.calendar_desired_revision,
        calendar_authoritative=EXCLUDED.calendar_authoritative,
        sync_status=CASE WHEN external_booking_sync.sync_status IN ('processing','uncertain')
            THEN external_booking_sync.sync_status ELSE EXCLUDED.sync_status END,
        updated_at=NOW();
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS calendar_queue_booking ON bookings;
CREATE TRIGGER calendar_queue_booking AFTER INSERT OR UPDATE ON bookings
FOR EACH ROW EXECUTE FUNCTION calendar_queue_booking();
