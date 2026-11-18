SET lock_timeout = '5s';
SET statement_timeout = '5min';

-- Record verified receipt conclusion only. This migration does not enable
-- suppression retention or derive a conclusion for idle-expiry evidence.
DO $migration$
BEGIN
 -- Wait for in-flight receipt triggers before switching the fence writer.
 LOCK TABLE public.booking_conversation_requests IN SHARE ROW EXCLUSIVE MODE;
 ALTER TABLE public.c24_source_fences ADD COLUMN IF NOT EXISTS c24_concluded_at timestamptz;

 -- Lock and modify the fence table in one transaction. Keep the existing
 -- immutable trigger effective for other sessions throughout the backfill.
 ALTER TABLE public.c24_source_fences DISABLE TRIGGER c24_immutable;
 UPDATE public.c24_source_fences f
 SET c24_concluded_at=r.c24_concluded_at
 FROM public.booking_conversation_requests r
 WHERE f.organization_id=r.organization_id
   AND f.conversation_id=r.conversation_id
   AND f.subject_id=r.original_subject_id
   AND f.request_id=r.request_id
   AND f.source_message_id=r.source_message_id
   AND f.c24_concluded_at IS NULL
   AND r.c24_concluded_at IS NOT NULL;
 ALTER TABLE public.c24_source_fences ENABLE TRIGGER c24_immutable;

 -- The receipt BEFORE INSERT trigger stamps the conclusion server-side.
 -- Other fence producers omit this column and leave the provenance unknown.
 CREATE OR REPLACE FUNCTION public.c24_fence_request() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $fence$
 DECLARE subj uuid;
 BEGIN
  SELECT subject_id INTO STRICT subj FROM public.booking_conversation_states
   WHERE organization_id=NEW.organization_id AND conversation_id=NEW.conversation_id;
  INSERT INTO public.c24_source_fences
   (organization_id,conversation_id,subject_id,request_id,source_message_id,c24_concluded_at)
  VALUES(NEW.organization_id,NEW.conversation_id,subj,NEW.request_id,NEW.source_message_id,NEW.c24_concluded_at);
  RETURN NEW;
 END $fence$;
 REVOKE ALL ON FUNCTION public.c24_fence_request() FROM PUBLIC,anon,authenticated,service_role;
END
$migration$;
