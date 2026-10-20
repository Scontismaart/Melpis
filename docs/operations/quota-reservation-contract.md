# A04 reservation lifecycle: integration contract

## Scope

`QuotaReservations` is wired into the authenticated simulator (`POST
/api/messaggio`), every inbound AI final-response channel routed through
`InboundProcessingService` (including WhatsApp and Instagram), and document Q&A
(`POST /api/documenti/chiedi`). These paths reserve before provider work and
commit only after the server-side guardrail accepts the final response. The
current code deliberately excludes onboarding preview, reviews, outbound
manual messages, human escalation and deterministic non-AI actions from A04.

This describes source integration and local disposable-PostgreSQL evidence;
it does not certify A04 on Staging or Production. Migration 060 is required
before the protocol can operate remotely and must be applied only under its
separate migration authorization. Migration 061 remains a separate A03
provider-attempt fencing gate. Neither migration is presumed applied to
Staging. There is no new migration in this change.

The commercial counter remains `organizations.messages_used_this_period`.
`usage_events` holds zero-quantity reservation records, not a second consumed
counter. Existing private `simulation_requests` stores the replayable response.
Generic usage writers and the governance outbox cannot write the reserved event
namespace. No client flag, serialized approval or LLM output grants admission.

## State and identity

- Operation identity binds tenant, channel, server-verified actor and source ID.
  Reusing that identity with a different payload hash is a conflict.
- Trial identity binds tenant and original trial start, independently of linking
  a Stripe subscription during that trial. The deadline never exceeds seven
  days from that start; the effective allowance never exceeds 150 replies.
- Paid identity additionally binds the verified subscription and period start.
  Only the existing verified billing transitions establish those values.
- A private ticket binds operation, claim nonce, trust epoch, period and lease.
  Renewal cannot revive an expired lease or extend the stored original period
  end. A released operation may retry only in its original period, with a new
  nonce and its original deadline.
- A final response binds the original claim, epoch and period. It cannot be
  adopted by a replacement attempt, even when operation and payload match.

## Atomic operations

Every transaction verifies the current trust marker and effective client ACLs,
locks the organization row and reads the clock after acquiring its locks.
Authenticated channels recheck the current access lifecycle and membership.
Inbound requires the actual tenant-scoped inbound message; a historical charge,
cached reply, quota denial or already delivered message is never adopted as a
new reservation.

1. **Reserve:** check current entitlement and `consumed + pending < limit`.
   Persist a reservation and private idempotency row. Do not increment usage.
2. **Validate:** apply the real deterministic guardrail. A private, nonserialized
   marker preserves a previous blocking decision on a sanitized fallback.
   Public `motivo` strings and JSON approval flags cannot exempt a valid answer
   or approve a blocked answer. Bind the accepted result to its original ticket.
3. **Commit:** recheck principal, entitlement, period, claim and live lease.
   Persist the response, inbound cache and billed timestamp when applicable,
   increment the sole counter once, and mark the ledger committed in one
   database transaction. Any failure rolls back all these writes.
4. **Replay:** return the first stored response, checking its hash against the
   ledger. Never regenerate, overwrite or increment again. Current access and
   tenant authorization still apply. Missing retained response fails closed.
5. **Release:** prove under lock that no response was persisted and invalidate
   the original claim. Repeated release is idempotent. It may clean up after
   principal revocation but cannot expose data, grant generation or refund a
   committed response. A delivery failure after commit never releases usage.

Expiry alone does not prove failure: an expired reservation stays pending and
requires reconciliation. A release fences a late worker; it is not evidence
that an outstanding provider call incurred no technical cost.
The inbound worker, simulator route and Q&A route own a `QuotaLease` from
reservation through final commit/release. Renewal covers retrieval, generation
and post-processing, rather than ending at provider-context close. A lost
renewal prevents finalization; context cancellation only stops heartbeat and
never releases quota automatically. Every provider dispatch and commit still
revalidates the durable PostgreSQL claim.
Rotating the trust epoch invalidates outstanding tickets. Historical records
must not be adopted into the replacement epoch or silently released.

## Code paths and remaining cutover gates

- `src/core/billing/quota_reservations.py` owns the private operation, ticket,
  guardrail proof, reservation, replay, release and atomic commit. Its only
  commercial increment writes the existing `organizations.messages_used_this_period`
  counter in the same transaction as the durable response and ledger transition.
- `src/core/inbound/service.py` claims a tenant-scoped inbound row, skips
  reservation for deterministic replies and system/manual handoffs selected
  before AI generation, and commits every accepted AI response/cache before
  attempting channel delivery. A valid AI-generated customer answer remains
  billable when its guardrail output also requests human follow-up. This includes inbound
  WhatsApp and Instagram responses routed through the shared service. A
  delivery failure does not refund the committed answer. An existing message
  cache is never replay authority: the A04 committed request and ledger must
  match the message cache hash and billed timestamp.
  Both first delivery and replay use the canonical payload returned by the
  database commit. A blocked guardrail response becomes a private server-owned
  non-billable handoff; a useful AI answer with human follow-up still counts.
- Inbound booking proposals are guardrail-validated before persistence. The
  persisted response states only that the request was received pending
  verification. BookingService runs after commit from that same stored payload,
  with independently verified customer intent from tenant-scoped source messages,
  capacity validation and stable `source_message_id`. Replay resumes the action
  without another generation. A failure routes to staff and never rewrites or
  refunds the canonical response; no booking runs after a failed A04 commit.
- `src/api/routes/simulator.py` uses the authenticated actor and mandatory
  `Idempotency-Key`; successful response and counter are durable before it is
  returned. Replays return the first response.
- `src/api/routes/knowledge.py` uses an authenticated actor and mandatory
  idempotency key; the private proof is removed before serialization. The
  successful persisted/replayed public shape remains exactly `{risposta,fonti}`.
- `src/core/provider_accounting.py` carries the unforgeable A04 operation and
  ticket separately from A03 provider attempts. An uncertain A03 attempt keeps
  its capacity reserved; lease expiry alone cannot release or zero its cost.
- Legacy direct charge/precharge and reply-write entry points fail closed.
  Periodic paid-cycle reset remains the billing webhook's responsibility.

Before any remote cutover, all of these conditions are required:

1. Apply migration 060 only after its own explicit environment-specific
   authorization and required backup/restore checks. Do not infer application
   from CI or the presence of migration files. Keep migration 061 separate.
2. Drain old application workers and deploy a version that fences legacy
   `increment_message_usage`, inbound precharge, simulator precharge and direct
   reply writers before allowing A04 reservations. Never overlap a legacy
   counter writer with pending reservations.
3. Review existing `simulation_requests` rows. Reuse only a verified completed
   response under current tenant authorization; do not adopt or automatically
   release old reserved/failed/ambiguous rows as A04 evidence. Review
   historical `outbound_dedup` rows too: they are never delivery authority
   without a matching committed A04 record, and unmatched rows remain
   fail-closed for explicit reconciliation.
4. Verify current membership, role, tenant, entitlement, consent, kill switch,
   trust epoch and database ACLs in the target environment. Staging remains
   uncertified until its migrations and real-path acceptance tests are
   separately authorized and completed.
5. Confirm local CI plus disposable PostgreSQL regressions for last-unit
   contention across channels, inbound persistence, simulator replay, Q&A
   no-leak/replay, uncertain A03 release fencing, and rollback/failure cases.
6. Inventory billing-period anchors before enabling A04. Every entitled trial
   organization must have a valid `trial_start`/`trial_end`; every paid
   organization must have a valid `current_period_start`/`current_period_end`.
   Migration 003 added these columns as nullable and did not backfill legacy
   rows. New signup and subscription-webhook paths write the anchors, but
   historical rows without them remain ineligible and fail closed. Do not
   infer an anchor from `created_at` or another timestamp. Before cutover,
   perform an authorized Staging inventory and obtain a founder-approved,
   evidence-based remediation/backfill for any missing anchors. No remote
   inventory or migration is part of this code change; Staging is not
   certified.

## Rollback behavior

If any cutover check fails, stop new AI admission for the affected paths using
the existing tenant kill switch or by disabling those AI entry points. Keep
manual inbox, dashboard, billing usage reads and human escalation available.
Do not roll back to a binary that can write the old precharge/increment paths
while A04 reservations exist. Do not decrement the counter, delete ledger
records, release uncertain provider attempts, or erase completed responses as
part of rollback. Preserve pending/uncertain reservations for tenant-scoped
reconciliation. A code rollback is safe only to a reviewed version that also
fences legacy writers; migration 060 is not automatically reversed.

No deployment, remote migration, Production change or backup deletion is
included in the source integration of this component.

See `a04-review-and-staging-cutover.md` for the independent findings, path matrix
and ordered inventory, drain, migration-compatibility and rollback gates.
