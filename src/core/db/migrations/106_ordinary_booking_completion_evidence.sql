SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Evidence only. No historical backfill, policy seed, deletion or worker grant.
-- Civil calendar months cannot be represented as a fixed number of days.
ALTER TABLE public.gdpr_policies ADD COLUMN IF NOT EXISTS retention_calendar_months integer;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint
   WHERE conrelid='public.gdpr_policies'::regclass AND conname='gdpr_calendar_month_policy') THEN
  ALTER TABLE public.gdpr_policies ADD CONSTRAINT gdpr_calendar_month_policy CHECK (
   retention_calendar_months IS NULL OR
   (category='completed_bookings_and_stays' AND retention_days IS NULL
    AND retention_calendar_months BETWEEN 1 AND 12));
 END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.ordinary_booking_completion_evidence (
 organization_id uuid NOT NULL,
 booking_id uuid NOT NULL,
 subject_id uuid NOT NULL,
 conversation_id uuid,
 completed_at timestamptz NOT NULL CHECK(isfinite(completed_at)),
 timezone text NOT NULL,
 PRIMARY KEY(organization_id,booking_id),
 FOREIGN KEY(organization_id,booking_id) REFERENCES public.bookings(organization_id,id) ON DELETE RESTRICT
);
ALTER TABLE public.ordinary_booking_completion_evidence ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ordinary_booking_completion_evidence
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;

CREATE OR REPLACE FUNCTION public.ordinary_booking_completion_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'Ordinary booking completion evidence is immutable' USING ERRCODE='42501';
END $$;
REVOKE ALL ON FUNCTION public.ordinary_booking_completion_immutable()
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
DROP TRIGGER IF EXISTS ordinary_booking_completion_immutable ON public.ordinary_booking_completion_evidence;
CREATE TRIGGER ordinary_booking_completion_immutable BEFORE UPDATE OR DELETE
 ON public.ordinary_booking_completion_evidence FOR EACH ROW
 EXECUTE FUNCTION public.ordinary_booking_completion_immutable();
DROP TRIGGER IF EXISTS ordinary_booking_completion_no_truncate ON public.ordinary_booking_completion_evidence;
CREATE TRIGGER ordinary_booking_completion_no_truncate BEFORE TRUNCATE
 ON public.ordinary_booking_completion_evidence FOR EACH STATEMENT
 EXECUTE FUNCTION public.ordinary_booking_completion_immutable();

-- Observe an actual new terminal transition, then defer evidence capture to
-- transaction end so the managed replacement receipt and allocation are
-- visible. An arbitrary status update or historical timestamp is not evidence.
CREATE OR REPLACE FUNCTION public.capture_ordinary_booking_completion() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE b public.bookings%ROWTYPE; a public.booking_allocation_sets%ROWTYPE;
 observed timestamptz:=clock_timestamp(); subj uuid; conv uuid;
BEGIN
 IF OLD.stato NOT IN ('in_attesa','confermata','da_verificare') OR OLD.completata_at IS NOT NULL
    OR NEW.stato<>'completata' OR NEW.no_show_at IS NOT NULL
    OR NEW.completata_at IS DISTINCT FROM transaction_timestamp()
    OR NEW.completata_at NOT BETWEEN observed-interval '1 minute' AND observed
    OR (OLD.organization_id,OLD.id) IS DISTINCT FROM (NEW.organization_id,NEW.id) THEN
  RETURN NEW;
 END IF;
 SELECT * INTO b FROM public.bookings WHERE organization_id=NEW.organization_id AND id=NEW.id;
 SELECT * INTO a FROM public.booking_allocation_sets
  WHERE organization_id=NEW.organization_id AND booking_id=NEW.id;
 IF b.id IS NULL OR b.stato<>'completata' OR b.no_show_at IS NOT NULL
    OR (b.completata_at,b.contact_id,b.id_conversazione) IS DISTINCT FROM
       (NEW.completata_at,NEW.contact_id,NEW.id_conversazione)
    OR a.booking_id IS NULL OR a.state<>'active' OR a.temporal_kind<>'minutes'
    OR a.ends_at>NEW.completata_at OR a.booking_calendar_revision<>b.calendar_revision
    OR EXISTS(SELECT 1 FROM public.booking_stays s
       WHERE s.organization_id=b.organization_id AND s.booking_id=b.id)
    OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name=a.timezone)
    OR NOT EXISTS(SELECT 1 FROM public.booking_catalog_publications c JOIN public.organizations o
       ON o.id=c.organization_id WHERE c.organization_id=b.organization_id AND c.revision=a.catalog_revision
       AND c.catalog->>'sector' IN ('ristorante','parrucchiere','centro_estetico','studio_medico_dentista')
       AND c.catalog->>'sector'=o.business_profile->>'verticale'
       AND c.catalog->>'timezone'=a.timezone AND o.timezone=a.timezone)
    OR NOT EXISTS(SELECT 1 FROM public.booking_resource_allocations r
       WHERE r.organization_id=b.organization_id AND r.booking_id=b.id
       AND (r.state,r.allocation_revision,r.catalog_revision,r.starts_at,r.ends_at)
       IS NOT DISTINCT FROM (a.state,a.allocation_revision,a.catalog_revision,a.starts_at,a.ends_at))
    OR EXISTS(SELECT 1 FROM public.booking_resource_allocations r
       WHERE r.organization_id=b.organization_id AND r.booking_id=b.id
       AND (r.state,r.allocation_revision,r.catalog_revision,r.starts_at,r.ends_at)
       IS DISTINCT FROM (a.state,a.allocation_revision,a.catalog_revision,a.starts_at,a.ends_at)) THEN
  RETURN NEW;
 END IF;
 -- Exact v1 digest of BookingMutation('complete','','','').digest(None).
 -- Receipts for contact edits, confirmations or worker-created allocations
 -- cannot substitute for this typed completion command.
 IF (SELECT count(*) FROM public.booking_allocation_requests r
      JOIN public.organization_memberships membership ON membership.organization_id=r.organization_id
        AND membership.user_id=r.actor_user_id
      JOIN public.user_profiles profile ON profile.id=r.actor_user_id
      JOIN public.auth_access_lifecycle access ON access.auth_user_id=profile.auth_user_id
      WHERE r.organization_id=b.organization_id AND r.booking_id=b.id
        AND r.actor_kind='human' AND membership.joined_at IS NOT NULL
        AND membership.ruolo IN ('owner','manager') AND access.disabled_at IS NULL
        AND r.action='replace' AND r.outcome='replaced'
        AND r.mutation_hash='4efaf69010ad344b83ca9cb9d9f206f54629aa5e356c79c015e959ddb5599eb6'
        AND r.created_at=transaction_timestamp() AND r.applied_allocation_revision=a.allocation_revision
        AND r.catalog_revision=a.catalog_revision)<>1 THEN RETURN NEW; END IF;
 subj:=b.contact_id;
 IF b.id_conversazione IS NOT NULL AND b.id_conversazione<>'' THEN
  BEGIN conv:=b.id_conversazione::uuid;
  EXCEPTION WHEN invalid_text_representation THEN RETURN NEW; END;
  IF NOT EXISTS(SELECT 1 FROM public.conversations c WHERE c.organization_id=b.organization_id
       AND c.id=conv AND c.deleted_at IS NULL AND c.contact_id=subj) THEN RETURN NEW; END IF;
 END IF;
 IF subj IS NULL OR NOT EXISTS(SELECT 1 FROM public.contacts c WHERE c.organization_id=b.organization_id
      AND c.id=subj AND c.deleted_at IS NULL) THEN RETURN NEW; END IF;
 INSERT INTO public.ordinary_booking_completion_evidence(organization_id,booking_id,subject_id,conversation_id,completed_at,timezone)
 VALUES(b.organization_id,b.id,subj,conv,NEW.completata_at,a.timezone)
 ON CONFLICT(organization_id,booking_id) DO NOTHING;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.capture_ordinary_booking_completion()
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
DROP TRIGGER IF EXISTS capture_ordinary_booking_completion ON public.bookings;
CREATE CONSTRAINT TRIGGER capture_ordinary_booking_completion
 AFTER UPDATE OF stato,completata_at ON public.bookings DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW WHEN (OLD.stato IS DISTINCT FROM NEW.stato AND NEW.stato='completata')
 EXECUTE FUNCTION public.capture_ordinary_booking_completion();

-- A read-only owner planning check, NOT purge authorization. A future purge
-- must lock/revalidate policy and holds, reconcile dependent provider, billing,
-- receipt and restore evidence, and establish a durable anti-replay barrier.
CREATE OR REPLACE FUNCTION public.ordinary_completed_booking_retention_eligible(p_org uuid,p_booking uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog,public AS $$
 SELECT EXISTS(
  SELECT 1 FROM public.ordinary_booking_completion_evidence e
  JOIN public.bookings b ON b.organization_id=e.organization_id AND b.id=e.booking_id
  JOIN LATERAL(SELECT p.* FROM public.gdpr_policies p WHERE p.organization_id=e.organization_id
    AND p.category='completed_bookings_and_stays' AND p.approved_at<=clock_timestamp()
    ORDER BY p.approved_at DESC,p.version DESC LIMIT 1) p ON true
  WHERE e.organization_id=p_org AND e.booking_id=p_booking
   AND b.stato='completata' AND b.no_show_at IS NULL AND b.completata_at=e.completed_at
   AND b.contact_id=e.subject_id
   AND COALESCE(b.id_conversazione,'')=COALESCE(e.conversation_id::text,'')
   AND EXISTS(SELECT 1 FROM public.contacts c WHERE c.organization_id=e.organization_id AND c.id=e.subject_id)
   AND (e.conversation_id IS NULL OR EXISTS(SELECT 1 FROM public.conversations c
     WHERE c.organization_id=e.organization_id AND c.id=e.conversation_id AND c.contact_id=e.subject_id))
   AND NOT EXISTS(SELECT 1 FROM public.booking_stays s WHERE s.organization_id=e.organization_id AND s.booking_id=e.booking_id)
   AND p.valid_until>clock_timestamp() AND p.erasure_allowed AND NOT p.legal_hold
   AND p.retention_days IS NULL AND p.retention_calendar_months BETWEEN 1 AND 12
   AND EXISTS(SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name=e.timezone)
   AND clock_timestamp()>=(e.completed_at AT TIME ZONE e.timezone
           + make_interval(months=>p.retention_calendar_months)) AT TIME ZONE e.timezone
   AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_holds h WHERE h.organization_id=e.organization_id
      AND h.category IN ('completed_bookings_and_stays','contact_content')
      AND (h.scope='organization' OR (h.scope='subject' AND h.scope_id=e.subject_id)
        OR (h.scope='conversation' AND h.scope_id=e.conversation_id)
        OR (h.scope='record' AND h.scope_id=e.booking_id))
      AND NOT EXISTS(SELECT 1 FROM public.gdpr_retention_hold_events v
       WHERE v.organization_id=h.organization_id AND v.hold_id=h.hold_id AND v.kind='released'))
 );
$$;
REVOKE ALL ON FUNCTION public.ordinary_completed_booking_retention_eligible(uuid,uuid)
 FROM PUBLIC,anon,authenticated,service_role,melpis_c24_booking_worker,melpis_c24_retention;
