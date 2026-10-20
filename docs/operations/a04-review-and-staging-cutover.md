# A04 independent review and Staging cutover

Scope: issue #89, PR #122. This is an execution plan, not evidence that a
remote migration, deployment or cutover has happened. Exact source SHA and CI
results are recorded in the PR so this document does not certify its own commit.

## Independent review

The review of `417e5bdadf950f81c3b43a6408c7e8264e09e91b` confirmed:

| Finding | Required correction |
| --- | --- |
| P0: both inbound implementations create bookings before final A04 persistence | Validate the proposal first, persist a truthful pending-request response, then run the deterministic booking service from the committed payload; replay resumes this same action without another provider call |
| P1: lease renewal ends at provider-context close | Caller owns renewal from reservation through final commit/release, including retrieval and post-processing; lost ownership fails closed |
| P1: inbound ignores the canonical commit result | Deliver the stored, guardrail-normalized payload on both first delivery and replay |
| P1: a blocked guardrail fallback prevents human handoff | Use a private server disposition for a deterministic non-billable handoff, with safe release and staff escalation |
| P1: a later accounting block replaces a committed delivery retry with a generic notice | Strictly verify and deliver the stored reply without permitting another provider call; keep new booking actions blocked |
| Security: booking-shaped model output alone does not prove customer authorization | Before commit and before execution, prove the request and mandatory booking values against persisted customer messages; retain capacity checks and the original authorizing source as the stable booking key |

`richiede_umano=True` is not by itself an erroneous charge: `_escalate` also
delivers customer text. A useful, validated and persisted AI answer remains
billable even when it requests human follow-up. Only server-selected handoffs
and deterministic fallbacks are excluded; no committed answer is refunded.

Booking authorization uses a contiguous request/continuation sequence rather
than searching through unrelated topics. Two concurrent confirmations of the
same request share its original booking source key. Missing, conflicting or
unsupported date/time/party-size evidence goes to staff without a new booking
or commercial charge; the model's category or reason cannot grant permission.
Relative dates, when supported, use the original message timestamp rather than
the clock at retry time. This conservative fallback is an operational limit,
not certification of unrestricted natural-language booking interpretation.
Supported evidence includes ISO or explicit day/month/year dates, today/tomorrow
relative to each source message in Europe/Rome, explicit times and party sizes.
Only a server-defined vertical default may supply an omitted party size. New
booking execution additionally requires a request no older than 30 minutes and
a future local slot; a stale committed response remains replayable but routes
any unexecuted booking to staff. An existing booking is never re-dispatched.

No runtime callers of the disabled legacy precharge, increment or direct reply
writers were found. Their compatibility facades fail explicitly. The review
also confirmed the existing plan limits (500/2,000/10,000), the trial cap of
150 replies/seven days, and the separate provider-attempt ledger.

## Path matrix

| Path | Admission and persistence | Retry / non-commercial disposition |
| --- | --- | --- |
| WhatsApp and Instagram, orchestrated worker | Tenant-scoped inbound row; reserve before AI; real final guardrail; canonical commit before booking/delivery | Same committed response and booking source ID; no new AI or counter increment |
| WhatsApp and Instagram, legacy worker | Same A04 contract | Same replay, booking and handoff rules |
| Authenticated simulator | Current membership; idempotency key; reserve/commit; simulation cannot create a real booking | Exact stored response; deterministic fast paths release safely |
| Document Q&A | Current membership; tenant/active-source retrieval; guardrail; complete answer and sources persisted together | Hash-verified original answer/sources; missing retained response fails closed |
| Onboarding preview | Existing A03 authorization and rate limit | Explicitly outside A04 |
| STOP, operator request, feedback, reminder, manual/dashboard | Deterministic/manual paths remain available, including quota zero | No commercial AI increment |

The regression evidence must cover booking/commit failure, expired/lost lease,
guardrail rejection, crash before and after commit, concurrent commit/release,
same-message replay, non-billable handoff and billable AI-with-follow-up,
provider uncertainty, tenant separation and delivery failure after persistence.
Local PostgreSQL uses synthetic rows in a disposable database. Provider HTTP
failures are simulated; no paid provider operation is part of this review.

## Staging cutover, ordered gates

1. **Authorize the exact operation.** Founder approval must identify the
   reviewed application SHA, Staging project, SQL hashes and backup/restore
   evidence. Approval of a source PR does not authorize executing this plan.
   Production remains outside this operation.
2. **Establish recoverability.** Take an updated encrypted Staging backup and
   ACL/role/catalog snapshot; verify restore into an isolated private database.
   Preserve existing backups and obtain founder approval before deleting them.
3. **Inventory historical state, initially read-only.** Count entitled trials
   missing/invalid `trial_start` or `trial_end`, paid organizations missing
   `subscription_id`/period anchors, expired periods, limits inconsistent with
   the verified monthly plan, disabled identities and incomplete memberships.
   Reconcile entitlements against their authoritative signup/Stripe evidence.
   Never infer a new trial/period from `created_at`, reset usage, grant extra
   days or invent memberships. Approve any required remediation separately.
4. **Inventory in-flight/legacy work.** Classify inbound precharges with no
   response, cached replies without matching A04 records, outbound-dedup rows,
   simulator request collisions, reserved A04 records and unresolved A03
   attempts. Preserve original usage and identities. Historical caches are
   not authorization for a fresh provider call or a new charge. Missing
   provenance and uncertain attempts remain held for scoped reconciliation.
5. **Drain before changing writers.** Pause new AI admission/consumption using
   an approved existing operational control while Meta ingress continues
   acknowledging and durably queueing events. Keep dashboard/manual staff
   operations available. Let active work complete, then stop every old inbound,
   retry and API worker that can write legacy counters. Record worker/image
   identities and verify none remain. A timeout is not proof of an unsent
   provider request and must not trigger release or retry by itself.
6. **Verify migration 060 on the actual target.** Inspect migration evidence,
   effective table/column grants (including inherited/PUBLIC grants), unchanged
   RLS/SELECT/backend writes, and exact trust-marker shape. If 060 is absent,
   apply its reviewed atomic batch only under the explicit migration approval
   and bounded lock wait. Do not rerun it merely as a health check: reapplication
   rotates the trust epoch and invalidates outstanding claims. Do not adopt
   old records into a replacement epoch.
7. **Verify compatibility with 061.** A03 requires
   `provider_attempt_runtime` and append-only `provider_attempt_evidence` with
   the reviewed constraints/grants. Confirm schema and recorded migration
   hashes; passing CI does not prove remote application. Apply absent 061 only
   under its own approval. Its backfill deliberately treats old unresolved
   attempts as uncertain. Do not weaken that state to enable A04. Gate #121
   remains a beta blocker for real OpenRouter recovery; Groq is uncertified.
8. **Activate only the approved binary.** Deployment is a separate approval.
   Recheck membership, consent, billing period/plan, kill switch, ACLs and trust
   epoch. Run an authorized synthetic-tenant acceptance matrix: last-unit
   contention across channels, trial 149/150/151 and seven-day boundary,
   paid-plan caps, renewal/upgrade without reset, booking failure/replay,
   handoff, lease loss, uncertain A03 hold, Q&A replay and delivery failure.
   No historical customer message is replayed to a provider as a smoke test.
9. **Observe before expanding.** Reconcile committed records, persisted
   responses, `billed_at` and the single commercial counter within each period;
   inspect held claims and human escalation. Resume queued ingress gradually
   only after evidence passes. No second counter or compensating decrement is
   introduced by this procedure.

## Rollback

On a failed gate, keep new AI admission paused and leave manual handling
available. Preserve responses, bookings, ledger records, counter values and
uncertain provider attempts. A booking already produced from a committed
response is not undone or re-issued as a refund mechanism.

Do not restart a legacy binary that can precharge or increment alongside A04.
A code rollback must use a reviewed version that fences those writers. Do not
restore client write grants, rotate the epoch, reverse 060/061, decrement usage
or delete reservations automatically. Any data/ACL restore requires a specific
founder-approved recovery transaction and the preserved encrypted backup.

A03 recovery certification (#121), A04 Staging certification and B02 PDF/RAG
completion are distinct release gates. This plan performs none of them remotely.
