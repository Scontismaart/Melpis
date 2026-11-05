# C2.4 PR1 — draft conversation state, not operational approval

Baseline: main `2a23abab12797ddb6b4c7e135a9f96adc9839923`.
This package is disconnected from every route, worker and operational booking
writer. It does not enable multisector booking, change A05 authority, send a
message, call a provider or introduce worker privileges. Results always carry
`runtime_enabled=false` and `confirmation_authorized=false`.

## Why new storage remains Draft

Existing A04 `DatiPrenotazione` only contains legacy name/phone/date/time/seats
and notes. It cannot faithfully replay service/resource identifiers, hotel stay
dates and room counts, correction operations or field provenance. Re-running AI
or filling gaps from a new provider call is not a recovery strategy.

The inactive `StructuredConversationResponse` subtype can carry a strict
`booking_state_proposal` and the backend-bound catalog revision through the
existing guardrail and A04 commit. It is not imported by the production model,
prompt, route or worker. No existing writer or A04 counter is changed. A real
PostgreSQL test uses the existing reserve/guardrail/commit once; the state
repository only reads those records and never reserves, commits or charges.

**These are new retained personal data**, both inside the existing canonical
A04 cache/simulation response and in SQL070. No C05/A06 retention/export/erasure
policy for them is approved by this code. SQL070 remains local-only and the PR
must remain Draft until that approval and the lifecycle contract are explicit.
No retention duration, legal basis or DPA policy is invented or activated.

## Administrative schema and provenance

The proposal accepts only intent, fixed slot/value pairs, explicit clear fields
and ambiguity fields. It rejects tenant/actor/source identities, provenance,
availability, authorization, confirmation and duration flags from model JSON.
Duplicate keys, oversized/deep JSON, bool-as-count, malformed civil dates/time,
clinical/custom fields and unknown catalog references are rejected.

Names, phone numbers and email are **presence booleans only**, never copies of
their raw text. Other values are catalog identifiers, civil date/time and bounded
positive administrative quantities. Resource requirements and duration are
derived from the current published catalog. The source message UUID, source
revision and catalog revision accompany values. AI extraction stays AI origin;
only narrow independently matched literal evidence is labeled customer-provided.
Backend-derived values retain their dependency fields. No raw source, prompt,
name, phone, email, diagnosis, symptom or free notes are copied into snapshots.

Correction preserves independent date/time values. Service/resource compatibility
is rechecked, and arrival/nights only invalidate a departure originally derived
from them. Superseded metadata stores field/source/revision and value digest,
not the old raw value. The snapshot has a technical bound of64 superseded
summaries; durable operation receipts preserve event idempotency independently.
The count bound is not an approved retention policy. Catalog revision mismatch
fails closed rather than silently changing an already discussed duration/resource;
an explicit reviewed re-proposal/rebase path is a remaining integration gate.

Medical input uses a conservative administrative vocabulary boundary. Unknown or
clinical/urgent wording goes to staff, independently of model intent. It is not
a medical classifier or a claim of clinical understanding. Medical/out-of-scope
handoff cannot be cleared by a subsequent model proposal. Human escalation in
the existing runtime remains unchanged.

## Database and concurrency

SQL070 adds backend-only `booking_conversation_states` and immutable
`booking_conversation_requests`. Composite organization/conversation/source/prompt
foreign keys prevent cross-tenant and cross-conversation references. The new
composite unique constraints on existing parent tables are logically redundant
with their global UUID primary keys; no existing row values or grants change.

Each state operation owns one READ COMMITTED transaction. Lock order is
organization SHARE, GDPR606 shared, current auth lifecycle/profile/membership,
conversation UPDATE, live contact, catalog-capacity shared/settings, request key,
parent source references, then state child row. Parent locks precede the child
lock to match FK deletion order. A real owner/manager is derived from current DB
membership; the A04 `_WORKER` namespace is never used as an owner identity.

AI proposal writes require a committed A04 usage event, current trust epoch,
matching completed simulation receipt and matching billed message-cache hashes.
The structured proposal is loaded from that canonical response, not supplied to
the repository as a second caller payload. Legacy/malformed/missing evidence
fails closed. Quota exhaustion does not block administrative state reads or
create another AI call.

CAS protects the state revision. Source order uses the validated raw Meta
message ID/timestamp after trusted ingestion, not `created_at DEFAULT NOW()`;
timestamps that are missing, tied, delayed or uncertain are rejected. This is
not operational Meta E2E certification. Same key or source with changed semantic
payload conflicts. Historical replay returns its applied revision and the
current snapshot without restoring old state. Current access, consent, source
lineage, erasure barriers and catalog health are checked before replay.

RLS denies anon/authenticated. Backend gets only state SELECT/INSERT/UPDATE and
receipt SELECT/INSERT. Effective table/column/PUBLIC permissions and SET/ADMIN
role paths use the reviewed SQL069 protocol. No SECURITY DEFINER, client write
path, role promotion or old-table grant change is introduced. The migration is
one atomic statement, including its ACL audit. Parent cascade support is not
claimed to complete A06.

## Confirmation is customer evidence, not booking authorization

An explicit details confirmation requires an actual sent, live outbound message
in the same organization/conversation, exact backend template bound to the
current state digest/revision and server-owned version4 prompt message UUID,
and a strictly later inbound `CONFIRM <prompt UUID>`. Queued, ambiguous, failed,
deleted, mismatched or cross-conversation prompts fail closed. A short yes or a
model flag is insufficient. Store prompt/source references, template version and
body hash, not a second copy of either message body. A state digest is correlation
data, not an authentication credential or booking permission. Hashes and UUIDs
remain linkable personal metadata, not anonymization.

Confirmed details still cannot create/confirm a booking. New corrections remove
the confirmation binding. Reads revalidate the source and live prompt proof;
deleted proof must not become valid because a snapshot still says confirmed.

## Required C05/A06 decision and PR3 lifecycle implementation

Review, by category and subject scope:

- Current snapshot: administrative values, presence booleans, source references,
  catalog/revision and derived intervals. A medical appointment can itself reveal
  health-related information; the absence of diagnoses is not a legal approval.
- Receipts: actor, source/prompt references, operation/content hashes and revisions.
- Canonical A04 metadata: the same strict proposal in message cache and simulation
  response. Extending that JSON is also retention; do not overlook it because it
  reuses an existing column.
- Confirmation and superseded summaries: pseudonymous linkable evidence.

Approve lawful processing, actual retention boundary, legal-hold behavior,
subject/organization/account export projections and erasure across **all** copies.
An export must parse the closed schema and preserve tenant/contact scope rather
than expose raw canonical JSON or other participants' text. Define in-flight
drain/fencing, partial message-retention behavior, proof deletion, cleanup/rebuild
rules and rollback. Parent cascades already prevent dangling FK references; they
do not prove the actual A06 job selects every subject or prevents resurrection.
No automatic retention/erasure flag or provider operation is enabled here.

## Verification boundary

Tests use reviewed Python3.12, synthetic data and isolated local PostgreSQL16/18.
The corpus-wide150 safety checks are distinct from independently authored
multi-turn temporal traces; neither is a model extraction/quality E2E. Exact test
counts, SHA, CI/audit/build receipts and remaining failures belong in the PR.
Provider/Meta/Calendar certification, remote migration, new worker identity,
booking/allocation/outbox transaction composition and operational cutover require
separate GO. A05 remains the existing restaurant/table60-minute path.
