# C23 booking writer inventory (read-only)

Scope: repository at codex/c23-allocation-contracts; local source inspection. This is a writer and boundary inventory, not certification of runtime coverage. Existing Graphify query was run first. No provider or database access was performed.

## Operational write paths

- Automatic inbound: `src/core/inbound/service.py:465` `_apply_committed_booking` is invoked after canonical A04 commit at lines 1134 and 1214; validates customer evidence, freshness, tenant and deterministic policy, with source-message lookup at 510, 589. Automatic confirmed booking and notification are committed together via `src/core/db/repositories/booking_repo.py:183` `insert_auto_confirmed`; Google authoritative pending variant at 286. Receipt/replay identity is `(organization_id, source_message_id)` with partial unique insert conflict at booking_repo.py:145-166; notification identity is `booking-confirm:{org}:{source_message_id}` at 229-257.
- Manual/dashboard: `src/core/bookings/routes.py:99,136,159,175,188,199,210` calls create/update/confirm/reject/cancel/no-show/completed service methods. `src/core/bookings/service.py:167-220` create validates, checks Google before lock, checks local capacity and inserts pending inside slot lock. Source-message replay resolves before and inside lock, but manual creation without source_message_id has no local request receipt. External creation uses `ext-book:{org}:{source_message_id}` or attribute digest at 250-281; the digest can conflate indistinguishable legitimate requests.
- Updates: `service.py:340-421` validates, checks Google before lock, locks destination slot only, rereads optimistic snapshot, checks destination capacity and updates. After reconfirmation failure it separately locks old slot, rolls back if still current and available, otherwise marks intervention. Both destination update and rollback require future allocation participation.
- Confirmation/reactivation and completion: `service.py:492-516` locks occupied slot, rechecks snapshot and capacity; Google authoritative confirmation transitions to pending and awaits proof. Manual confirmation uses `booking-confirm-manual:{org}:{booking}:{calendar_revision}` (`service.py:518-560`); automatic/Google notices use durable outbox.
- Rejection/cancellation/no-show: `service.py:565-611` reject and cancel update status directly; cancellation external key `ext-cancel:{org}:{booking}`; no-show executes direct SQL. Reject sends notification without explicit key at this call. `src/core/bookings/no_show_job.py:15` status update; `src/core/bookings/reminder_job.py:22,53` and `booking_repo.py:522-552` reminder status/rejection changes. Review each status for allocation release or retention.
- Repository SQL: `booking_repo.py:134-181` creates bookings; :388-445 status/details; :406 completed; :447-462 payment; :483-552 reminders; :653 intervention. `src/core/billing/webhook_handler.py:671` updates payment status; `service.py:546,795,820` deposit/payment fields. These ancillary updates matter for trigger interactions but are not automatically capacity mutations.
- Legacy/external adapters: `src/core/bookings/adapters/internal_adapter.py:102,145,171` delegates create/update/cancel to service. Router `src/core/bookings/router.py:292,486` dispatches provider create/cancel; provider adapters include apaleo, beds24, calcom, simplybook, zak and fake. External side effects and their uncertainty do not constitute local allocation authority.
- Calendar: migration `src/core/db/migrations/064_calendar_lifecycle.sql:62-144` tracks booking changes, guards direct confirmation under Google authority and queues sync. `src/core/calendar/repository.py:109-164` finalizes fenced claims; `src/core/calendar/lifecycle.py:200-205` changes status to review/confirmed after proof. Claims carry desired/claimed revision, lease, fence, and verified provider state; uncertainty is not availability. `calendar/repository.py:199` only adopts correlated GET receipt after explicit retry.
- GDPR: `src/core/gdpr/deletion.py:27-31,83-86,150-189` takes organization/exclusive erasure fence, blocks active bookings and unresolved sync, removes associated outboxes, redacts booking fields including conversation link. Allocation records must be scoped and covered by retention/export/erasure rules; do not make redaction an accidental booking authorization or untracked release.

## Existing locks and receipts

- `booking_repo.py:29-43` `slot_lock`: transaction, shared GDPR advisory lock 606, shared `booking-capacity|org` advisory, exclusive slot advisory. Automatic `auto_booking_lock` :46-75 adds conversation row `FOR UPDATE` before capacity and slot. Settings lock :81-100 takes shared GDPR fence then exclusive capacity advisory. These are point-slot locks; they do not protect arbitrary overlapping intervals or multiple resources.
- Calendar finalization `calendar/repository.py:109-150`: organization lock, GDPR shared fence, credentials row, conversation row, capacity shared, slot exclusive, booking row, queue row. Queue claim uses `FOR UPDATE SKIP LOCKED` and lease/fence/revision; preserve order to prevent deadlocks, and do not substitute a new lock for existing lifecycle fences.
- Catalog administration `src/core/db/repositories/booking_catalog_repo.py:101-169`: settings lock (GDPR shared then capacity exclusive), settings row `FOR UPDATE`, compare expected revision; request UUID scoped to organization plus actor/payload hash supports replay, changed payload conflicts. Publication writes immutable snapshot (`booking_catalog_publications`) and receipt (`booking_catalog_requests`) transactionally, updates only catalog_v2 namespace, and records audit. State reads check revision, receipt, and publication consistency at :38-76. A future admission must bind to the server-read published revision and prevent publish/admission races; draft is not authority.
- GDPR `src/core/gdpr/guard.py:32,53` shared advisory 606; deletion uses exclusive 606 after organization lock. Future storage and writers must obey the same barrier and scoped erasure model.
- A04 final canonical response is a prerequisite, not an AI permission; A05 policy, customer evidence, capacity, consent, guardrails and idempotent side-effect handling remain independent. `catalog_contracts.py:270` observation is evidence, not confirmation authority. Provider-free/unknown and unavailable states must not be conflated.

## Dark-engine risks / non-goals

The existing writers are not all in one allocation protocol. Do not claim interval enforcement or wire any operational writer until deterministic lock order across all resources and old/new intervals, catalog revision serialization, atomic booking+allocation+receipt/outbox writes, cancellation/release and rollback, manual and legacy compatibility, Calendar finalization, and GDPR boundaries are designed and verified under actual PostgreSQL concurrency. Existing Google precheck is best-effort/fail-open and has a documented TOCTOU window; it is not authoritative proof. A simulated or observed FREE does not authorize an operation. ADR-006 (`docs/decisions/ADR-006-multisector-booking-v1.md:195-230`) leaves allocation and deterministic post-A04 admission for separate work; current v1 restaurant behavior remains authoritative.

## C23-A prepared schema and activation boundary

Migration 069 and the pure contracts are dark, additive preparation only. They do not implement a pool capacity sweep, admission repository, operational enforcement, provider synchronization, booking confirmation or GDPR export/erasure integration. No live migration or activation is authorized. The future repository must follow the architecture blueprint lock order and recheck subject lineage, active membership, catalog health and revision after waits before returning even historical receipts. Receipt identity covers the original semantic request and trusted actor, independent of subsequently published catalog data. Structural cascade support is not a completed erasure integration.

The later merge and issue 149 receipt must report the exact reviewed commit, local migration and test evidence, limitations and remaining activation gates; it must not imply that a production migration or allocation cutover occurred.

## Executable protocol for the later isolated repository

READ COMMITTED, one connection and transaction. Lock order: organization FOR SHARE;
GDPR606 shared; current auth lifecycle/profile/membership; optional conversation;
existing booking-capacity shared; settings; scoped booking; request key;
sorted union of old and new resource IDs. Catalog publication takes capacity
exclusive, so reread the published revision after waits. No provider calls
inside the transaction. No operational writer uses these new resource locks yet.

Pool admission in C23-B must sweep the entire half-open interval, include active
claims from every catalog revision, and retain all old claims on failed replace.
No start-only check, revision-partitioned resource identity or automatic expiry
refund. Parent drift or external authority without verified evidence yields
UNKNOWN. A historical request receipt never authorizes a new booking or restores
claims after cancellation. C23-A has no repository or availability verdict.

The new tables preserve direct tenant scope and composite booking/catalog links.
Exclusive overlaps are excluded across revisions; pools have no duplicated
commercial counter. Dedicated guards resolve both direct contact and same-tenant
conversation lineage. Medical data is not accepted. These booking-linked metadata
still need scoped export, retention and erasure integration before production
persistence; existing GDPR operational writers remain unchanged.

## Local evidence, 30 October 2026

43 focused tests passed on PostgreSQL16 and43 on PostgreSQL18 with pgvector,
using disposable loopback-only databases. They cover composite constraints,
exclusive adjacency/collision across revisions, client grants and inherited
SET ROLE paths, direct/helper backend ownership, inaccessible membership edges,
first-install late-failure rollback, old ACL preservation and permitted backend
writes. The write fixture reproduces existing Supabase backend BYPASSRLS/read
rights inside a rolled-back transaction; migration069 does not grant them on
pre-existing tables. The earlier failed runs remain historical evidence.

Main baseline after149 is eefc34c2a2e76ddc62aaf86fa788028adf46ea7f;
post-merge CI40924307235 passed4177 tests with36 skipped and audited203 packages.
API/web publications and amd64/arm64 manifests were verified independently.
This resolves the old filelock audit blocker without changing its failed run.
No remote069 migration, pool engine, live confirmation or provider E2E is certified.
