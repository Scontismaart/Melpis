-- LOCAL PROPOSAL ONLY. No migration number, runtime grants, or deployed identity.
CREATE TABLE public.draft_booking_worker_claims (
 organization_id uuid NOT NULL, message_id uuid NOT NULL, conversation_id uuid NOT NULL,
 claim_id uuid NOT NULL, generation bigint NOT NULL CHECK(generation>0),
 lease_until timestamptz NOT NULL,
 PRIMARY KEY(organization_id,message_id),
 FOREIGN KEY(organization_id,conversation_id,message_id) REFERENCES public.messages(organization_id,conversation_id,id) ON DELETE RESTRICT
);
ALTER TABLE public.draft_booking_worker_claims ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.draft_booking_worker_claims FROM PUBLIC,anon,authenticated,service_role;
-- No policy, backend grant, or role. Only disposable DB owner tests may use this.
-- Lease expiry fences BOOKING CLAIMS only, never grants AI invocation or refund.
