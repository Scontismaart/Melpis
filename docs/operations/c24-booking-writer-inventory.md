# C24 booking writer inventory and dormant adapter design

## Existing writer and coordination inventory

- Inbound auto and historical staff-review create: `src/core/inbound/service.py` uses `BookingRepository.auto_booking_lock`: tenant shared advisory lock, conversation row lock, capacity shared lock and hourly slot lock, revalidates fresh inbound intent, and uses source-message replay. Auto-confirm commits booking and A05 confirmation outbox together; internal Calendar sync also queues its event. Google authority creates pending booking and waits for Calendar proof. This is after the A04 canonical reply; preserve that proof boundary.
- Manual create: `src/core/bookings/service.py:create_booking`, hourly slot lock around capacity check and insert. Google free/busy is checked before the lock and is best effort; network sync is after commit. Source-message replay is supported. External adapters are dispatched separately with deterministic idempotency keys.
- Update: `service.update_booking` locks the destination hour, rechecks latest fields, verifies capacity, and updates with expected snapshot; failed WhatsApp reconfirmation can roll back to the old slot after rechecking old capacity. Any allocation must participate in both initial mutation and compensating rollback.
- Confirm/completion/reactivation: `_transition_to_occupied` locks slot and rechecks latest snapshot and capacity. Google-authoritative confirmation waits for async proof. Completion semantics need explicit allocation treatment because legacy capacity counts completed bookings whereas the dark engine treats completed as ineligible.
- Reject/cancel: direct status changes; cancel dispatches external provider cancellation and both sync Calendar afterward. `mark_no_show` uses raw SQL. `no_show_job.py` uses status repository. Reminder repository/job can change reminder status and reject bookings. These release or potentially alter occupancy without allocation integration.
- Calendar `CalendarQueue.verified` obtains credential row, conversation, capacity, slot, booking then queue locks; checks lease, fence, revision, epoch and freshness; finalizer rechecks local capacity, latest inbound and policy before confirmation and A05 outbox. A concurrent cancellation creates a newer delete. Preserve this proof and lock ordering.
- External provider create/update/cancel are in booking adapters and router; their authority and remote side effects cannot be made atomic by local claims. `scripts/migrate_airtable_to_bookings.py` inserts directly; repository facade and raw CRUD are legacy bypasses. Explicitly classify and gate these paths before activation.
- Non-occupancy writers: payment/deposit in booking service and billing webhook, reminder fields, Calendar event IDs, intervention flag, and GDPR redaction. Identify which fields affect allocation fingerprint and revision, without reintroducing erased data. Historical migration SQL also modifies bookings.

## Existing dark allocation boundary

`BookingAllocationRepository` uses its own read-committed transaction. It locks org scope, validates owner/manager membership and lineage/GDPR, serializes catalog, locks booking and request identity, checks expected revisions, then orders booking/resource advisory locks and rechecks. It stores semantic idempotency receipt and allocation set/claims. It requires an existing booking and an owner/manager actor. Calling `apply` from a separate booking transaction is not atomic and its actor model is not an inbound system authority. Unsupported external authority, stale/ineligible scope and unknown evidence fail closed.

## Dormant PR3/PR4 direction

PR3: expose a connection-owned deterministic allocation primitive that uses the caller's transaction and prescribed locks, with a narrow trusted command carrying tenant, actor/provenance, request identity and expected revisions. Preserve the existing authorized wrapper and forbid provider/network calls. Add an explicit dormant orchestration contract that can atomically compose booking, claims, receipt and existing outbox on one connection; do not wire any runtime path or add worker grants.

PR4: explicitly adopt each authorized writer, preserving A04 proof, A05 notice, Calendar fencing, tenant/lineage authorization, ordering, replay semantics and postcommit side effects. Replace old/new allocations atomically on reschedule and compensation; release claims on terminal states where policy requires, and specify completion/reactivation behavior. Unsupported authority/import/legacy routes must fail closed during any future activation. No activation is proposed here.

## Calendar refresh race

`connection.credentials` holds an exclusive per-org session advisory lock while reading, refreshing and persisting with epoch/version CAS, so normal concurrent refresh and disconnect serialize. However, cancellation while awaiting `asyncio.to_thread(creds.refresh, ...)` can release the lock while the underlying thread/provider request continues. A refresh-token rotation can occur without durable persistence and another operation may overlap; CAS cannot reverse a provider-side rotation. A robust design must serialize until the underlying request settles and handle unknown results or rotation conservatively, including process death. Never log credential material.

## Regression matrix

Use real Postgres transactions and deterministic barriers: last resource across auto/manual; crossing reschedules; cancel/reactivation versus admission; catalog publish versus admission; same request replay and changed payload/actor; cross-tenant, revoked actor and GDPR lineage; stale catalog/allocation/fingerprint. Inject exceptions at booking, claims, receipt, A05 outbox and Calendar queue boundaries and prove all-or-none, including failed reconfirmation with old slot taken. Test confirm/reject/no-show/completion/reminder cancellation and repeats; stale or uncommitted A04 source and newer conversation cancellation; exactly-once and stale notice suppression; Calendar finalization versus cancellation, changed revision and expired lease; unsupported authority and legacy/import fail-closed. Mock refresh thread cancellation and rotation, second caller, reconnect/disconnect, epoch and token persistence without secret logs. No provider calls, external credentials or Docker changes are needed.

## PR3 draft boundary and outstanding limitations

The private draft preauthorizes an active owner/manager and locks organization and erasure scope, subject conversation, catalog/capacity, existing booking and request receipt before a typed local mutation. New bookings use declared lineage. The complete typed booking/notice command and lineage are hashed in a nullable receipt column; legacy allocation requests cannot replay a typed mutation receipt. The draft supports creation, initial allocation/adoption, and contact-only editing of a currently active minute-based allocation through the existing replacement protocol. The current dark commands are described below, including validated rescheduling, coherent occupied-state transitions, and source-proven civil stays. Legacy/import/provider/worker adoption is not implemented; unsupported historical states remain fail-closed. The draft is not wired to routes or workers, and real PostgreSQL concurrency, rollback and authorization verification remains necessary before any release.

Historical initial prototype evidence (superseded by later receipts): 45 PostgreSQL 16 tests passed across the new transaction tests and
the existing allocation-engine suite. These include real rollback after durable
outbox insertion, matching/mismatched concurrent replay, tenant/lifecycle/GDPR
denial, schedule mismatch and suppression fingerprints even when no contact is
linked. No provider was contacted. That early proof did not include PostgreSQL 18. Current exact-head receipts below replace that limitation; full CI remains mandatory on every new SHA. Passing this bounded suite does not prove
adoption by all existing writers or authorize a new worker identity.

## Active contact-only edit: transaction proof

An active edit requires `replace` with the current allocation/catalog revisions.
Before any write, the transaction validates the real locked booking and allocation
fingerprint, explicit Calendar revision, eligibility, service, timezone, minute
interval and every resource claim against the unchanged requested allocation.
A private one-use capability is minted from database rows, never caller-supplied
fingerprints; it is bound to the engine instance, connection, PostgreSQL
transaction, tenant and exact request.

SQL064 also increments Calendar revision for name/phone/note changes. Therefore
ignoring that revision or simply recalculating the old fingerprint would be
unsafe. The capability permits only that transaction's contact-only postimage
and zero/one trigger revision increment; every schedule/status/subject field
must remain equal. The normal allocator still replaces the complete allocation,
advances its revision and every claim revision atomically, and stores the actual
post-trigger fingerprint. Matching replay precedes mutation and never increments
Calendar/allocation revisions or emits another notice. Ordinary allocation calls
retain their stale-state check. This is not a generic stale-state override.

Default edits emit no confirmation notice. Existing SQL064 may enqueue a
versioned Google synchronization when a tenant has a configured integration;
that queue remains local and transactional, with no direct provider call or new
worker wiring. No operational caller is connected by this draft.

The first active-edit prototype failed its actual PostgreSQL regression because
it omitted the Calendar trigger interaction. That local failure is retained in
the evidence and is not treated as a passing result. The corrected implementation
requires fresh PostgreSQL 16/18 and exact-head CI receipts.

Corrected active-edit proof (2 November 2026): PostgreSQL 16.3 and 18.6 each
passed 145 tests across transaction mutations, the existing allocator, and
tenant-scope guard regressions. Assertions cover the real SQL064 revision bump,
post-trigger allocation fingerprint, preserved claim contents with revision
advancement, no default confirmation notice, replay, stale explicit revision,
forged token/raw-fingerprint denial, concurrent CAS and outbox-failure rollback.
These suites overlap previous integration evidence and are not additive unique
test counts. Independent final static review found no P0/P1 in this bounded dark
scope. General writer/worker integration and whole-system five-sector runtime
remain unfinished.

## Additional dark minute and civil-night commands

The next draft implements minute rescheduling and cancel/reject/no-show with
actual locked preimage validation, exact service/resource requirements,
transaction-bound postimages and the existing allocation/receipt transaction.
An unavailable target rolls the entire change back; the old claims remain.
Rescheduling preserves service/resource selection and returns to `in_attesa`;
confirmation/reconfirmation and compensation remain separate unfinished writers.
No automatic provider send, A04 charge or runtime integration is introduced.

`booking_stays` is a canonical civil arrival/departure record for verified
original subject/conversation/source lineage. It does not count capacity; the
existing allocator remains authoritative for complete room intervals and DST.
Create/reschedule use the published check-in time, preserve source identity and
advance stay/allocation revisions atomically. Missing historical/subjectless
lineage is rejected; no checkout backfill or guessed identity is performed.

Stay erasure/retention is an explicitly administrative workflow. An approved
application erasure request first passes existing execution/policy/drain/booking
checks and places its barrier, then reports `administrative_stay_purge_required`.
The narrowly scoped database-owner purge function checks current policy, subject,
operation/barrier (or approved retention age), terminal booking and released
claims. Private transaction markers authorize only that exact receipt/stay
cleanup, preserve the financial booking/A04 counters, append audit evidence and
write replay fences/tombstones. Ordinary backend/client roles cannot execute
this function, forge markers or delete the immutable receipts/stays. Both Python
and database write guards reject purged booking/request recreation.

This does not authorize an administrator operation, destructive job or new
worker grant. The application retention worker deliberately cannot invoke the
owner-only purge; an independently authorized operator is required. C01 must
verify the ACTUAL application pool principal and absence of owner/SET paths:
named-role ACL tests alone do not prove deployment separation. No credentials
were inspected or changed. Tombstones, private audit references and fences are
still personal metadata with unapproved retention, explicitly exported in
curated form or excluded/retained; no anonymous or fully erased claim is made.

Restore requires a bounded signed v2 journal including each stay purge and its
request tombstones, held independently outside the restored database. v1 cannot
prove absence of later retention purges and remains quarantined. Verified v2
results still never authorize traffic or certify external copies. No external
journal service, policy duration, remote migration or deployment was created.

Still unfinished: reconciliation of unsupported historical states,
reconfirmation compensation, general adoption of every legacy/import/provider
writer, private worker authority integration and full five-sector runtime traces.
Existing A05 activation remains restaurant/table/60 minutes only.

## Coherent occupied-state transitions

Private owner/manager confirm and complete validate the exact active allocation
and retain its resource claims. Completion records the actual server transaction
timestamp and writes the matching postimage fingerprint; it does not release
capacity. Coherent `da_verificare` records may pass the same checks. The fixed
postimage capability never accepts a model-provided bypass flag.

Typed cancel/reject/no-show also persist the real canceled postimage. Reactivation
is limited to coherent canceled/rejected records with no completion/no-show
timestamps, no claims and a matching current postimage/catalog/revision; admission
rechecks capacity before returning to pending. Failure leaves the cancellation
unchanged, while exact replay neither writes nor emits another notice. Unproven
legacy cancellations and no-show/completed resurrection remain denied.

`internal_calendar_sync` retains internal scheduling authority independently of
external synchronization. Google/external authoritative modes are unsupported by
this private local writer and remain fail-closed; no provider is contacted.
These methods are not wired to routes, workers or legacy writers.

Administrative erasure still requires a terminal released stay. Completed
records with retained allocations require an explicitly reviewed archival/retention
procedure; elapsed time alone does not bypass that prerequisite. No financial
record, quota unit or unknown provider consumption is automatically released.

## Current bounded verification

Runtime commit `ff218b2781e3569702c9172e7f3b6d31d21d2720`: PostgreSQL 16.3
and 18.6 each passed 417 cases with one explicit full encrypted backup-drill
skip. The suite includes schema-install rollback/ACL tests, actual stay/receipt
purge effects, signed v2 restore journals, civil DST/adjacency, last-room races,
occupied-state transitions, request replay, tenant isolation and the test-only
private worker-proof design. Earlier red receipts are preserved.

The pre-069 installation test now first removes 071's dependent stay objects
inside its outer rollback; all original failure/ACL assertions remain and the
test additionally verifies restoration of the actual enabled stay-delete guard.
No application FK, role check, test, security workflow or audit policy was removed.

The worker proof remains portable fixture code/schema under tests, not an app
module, migration or grant. It verifies evidence and returns inert IDs/flags;
its same-message identity is not a completed multi-turn booking executor or a
substitute for deterministic intent, confirmation, medical handoff, all-writer
compatibility and separately approved service-principal authority.
