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

The private draft preauthorizes an active owner/manager and locks organization and erasure scope, subject conversation, catalog/capacity, existing booking and request receipt before a typed local mutation. New bookings use declared lineage. The complete typed booking/notice command and lineage are hashed in a nullable receipt column; legacy allocation requests cannot replay a typed mutation receipt. The draft supports only creation and contact-only editing with allocation. Schedule changes, status transitions, and other booking writers fail closed; a sealed locked preimage and complete status/resource policy are still needed. The draft is not wired to routes or workers, and real PostgreSQL concurrency, rollback and authorization verification remains necessary before any release.

Local PostgreSQL 16 evidence: 45 tests passed across the new transaction tests and
the existing allocation-engine suite. These include real rollback after durable
outbox insertion, matching/mismatched concurrent replay, tenant/lifecycle/GDPR
denial, schedule mismatch and suppression fingerprints even when no contact is
linked. No provider was contacted. The same cases still require PostgreSQL 18
and complete CI on the final commit. Passing this bounded suite does not prove
adoption by all existing writers or authorize a new worker identity.
