# A04 reservation lifecycle: integration contract

## Scope

`QuotaReservations` is an inactive internal component. No HTTP route, worker,
provider or booking path imports it. This change does not certify the 150-reply
limit in the running application and does not complete A03, A04 or Staging.
Migration 060 is a prerequisite; its remote execution requires the separately
authorized backup, restore and migration procedure. There is no new migration
in this component.

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
Rotating the trust epoch invalidates outstanding tickets. Historical records
must not be adopted into the replacement epoch or silently released.

## Required before activation

The adapter work must be reviewed and tested together before enabling any path:

- Replace all legacy pre-generation charging paths in one coherent cutover.
  Never run legacy writers alongside new pending reservations: they do not
  account for pending slots. Drain or fence old workers during an explicitly
  approved service deployment.
- Authenticate the request and derive its tenant and actor server-side. A UUID
  supplied in a request is not an authenticated identity. Continue checking
  consent, application permissions, kill switches and provider accounting.
- Revalidate the lease and authorization before semantic/provider work and any
  deterministic privileged side effect. A response commit cannot retroactively
  authorize a booking. Never let LLM output directly invoke privileged actions.
- Preserve the private guardrail provenance until validation/commit. In
  particular, reconstructing `RispostaOutput` from an orchestrator's public JSON
  loses private state; the adapter must retain the actual server result.
- Keep technical attempts, token/model attribution, latency and estimated cost
  independent of commercial response consumption. An uncertain or late provider
  outcome must not disappear from accounting or trigger an automatic retry.
- Coordinate preview and Q&A idempotency with existing clients and API contracts.
  Do not invent a random server key on retries or silently change client behavior.
- Extend the typed final-response envelope for `RispostaDocumento` before Q&A
  activation, preserving the authorized sources on replay. The current final
  validator accepts `RispostaOutput`; a channel identifier alone does not prove
  that the existing Q&A API contract is supported.
- Reconcile existing simulator rows keyed by the old raw request ID. A completed
  historical response may be replayed after authorization; an old pending/failed
  charge must not be adopted or charged again. No historical row becomes proof
  of a new reservation merely because its timestamps look plausible.
- Align GDPR export labels and retention documentation with the shared response
  store. The existing `simulator_responses` export includes this table and
  organization deletion cascades to it; inbound's internal worker UUID must not
  be presented as a real authenticated user. Expiry of a private response row
  cannot erase the durable ledger or authorize generation again.
- Certify the complete inbound, simulator, preview and PDF/Q&A flows, including
  concurrent boundary requests, cancellations, process failures, webhook period
  changes and real authorized Staging use. Local component tests alone do not
  certify these application flows.

No deployment, remote migration, Production change or backup deletion is
included in the source integration of this component.
