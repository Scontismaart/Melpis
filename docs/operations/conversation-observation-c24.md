# C2.4 PR2 — read-only diagnostic observation

Depends on Draft PR1 #153 (`46a7cf74840c3ef09781d6d70dad27255e23b330`). No runtime route or worker imports this package. No new SQL, grants, service identity, A04 writer, booking writer or outbox integration is introduced. Its merge is blocked by the PR1 privacy/lifecycle decision and all security/CI gates.

## Contract and transaction

`BookingConversationRepository.observe_candidate(org, auth, conversation, expected_state_revision, expected_catalog_revision, expected_state_digest)` reuses the authenticated PR1 scope and current state on one PostgreSQL connection/transaction. The caller provides no proposal, booking UUID, resource claims, tenant override, actor profile or authority flags.

Every surviving slot source and latest proposal must retain live tenant/conversation lineage, committed A04 usage/receipt/cache evidence and the same original proposal receipt hash. The current catalog and provenance are checked again; derived hotel dates are recomputed. Consent/GDPR/current membership checks precede diagnostics. Missing or changed proof fails closed. Newer, tied, malformed or excessive inbound ordering produces UNKNOWN, never an invented causal order.

`AllocationCandidate` contains only typed temporal/service/quantity fields. Existing `AllocationRequest` behavior is retained and shares pure bounds/claims derivation. `_observe_candidate_locked` uses the same connection and sorted resource-lock namespace as C2.3. A None booking exclusion includes **all** existing bookings; no fake booking UUID is created. It writes no allocation or receipt and starts no independent transaction.

The result distinguishes `allocation_observation` (diagnostic dark-ledger capacity) from `operational_availability`, which is **always unknown**. Legacy occupancy and writer reconciliation are incomplete. Even diagnostic available + valid opt-in cannot authorize a booking. Incomplete/ambiguous/handoff state stays unknown. Strict existing A05 validation determines opt-in metadata from decoded settings, not a model flag; it still certifies only restaurant/table 60 minutes. No other sector is activated.

## Restricted worker identity — design only

A future service principal must be created by trusted ingestion/queue code, not by an owner UUID or client payload. It must bind organization, conversation/contact, the verified provider account and ingress receipt, source message and opaque claim generation, canonical A04 operation/response hash/trust epoch, catalog/state/policy revisions, current consent/revocation and erasure barriers. A queue row status or heartbeat alone is not a fencing token.

At use time the backend must atomically validate current claim ownership, signature/ingress lineage, membership-independent tenant policy, expiry/revocation, canonical response and expected revisions. Audit must distinguish service identity from user identity. It may only inspect this one message context; owner/MFA administration remains separate. No new privileges or worker entry point exist in this PR. Independent security review and explicit founder GO are required before implementing grants or operational wiring.

## A04 and future booking boundary

Observation cannot reserve, renew, release or commit quota, call AI, create a booking, send a notification or contact a provider. Repeated and concurrent observations leave all business rows and commercial consumption unchanged. A canonical A04 reply already persisted is replay evidence, not a second generation trigger.

Future PR3 must place canonical booking, allocations, idempotent receipt and outbox in one existing scoped PostgreSQL transaction, with deterministic authorization and rollback. Two independent repository commits are not atomic. A04 is already committed before this transaction and cannot be refunded because delivery fails. UNKNOWN or uncertain provider/accounting state cannot authorize a retry POST or booking grant.

## Verification boundary

Tests use synthetic local PostgreSQL, not real Meta/Google/AI. Positive and malformed A05 opt-in, capacity collision without exclusion, stale/newer sources, multi-source A04 tampering, tenant denial, worker denial, consent, replay and concurrent read-only invariants are exercised. CI/audit/build receipts are recorded in the PR; no operational certification is implied.
