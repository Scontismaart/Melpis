SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- A CHECK accepts UNKNOWN. SQL074's deadline comparison therefore also
-- accepts a reviewed event with a NULL next_review_by. Require a deadline
-- explicitly while preserving SQL074's chronology and maximum interval.
DO $$
BEGIN
 IF NOT EXISTS (
     SELECT 1 FROM pg_catalog.pg_constraint
      WHERE conrelid = 'public.gdpr_retention_hold_events'::regclass
        AND conname = 'gdpr_hold_review_deadline_required'
 ) THEN
    ALTER TABLE public.gdpr_retention_hold_events
      ADD CONSTRAINT gdpr_hold_review_deadline_required
        CHECK (kind <> 'reviewed' OR next_review_by IS NOT NULL);
 END IF;
END $$;
