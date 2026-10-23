# B01 Calendar implementation review

**Review state: implementation review only; not a release or staging certification.**
No Google provider call, OAuth session, migration application, deployment, or
staging cutover is claimed here. The readiness checks and operator flags are
gates, not proof that C03 has completed.

## Authority contracts

The stored booking authority is `booking_settings.config.a05.authority`. Missing
legacy configuration defaults to `internal` in
`src/core/calendar/connection.py::authority_from`.

| Mode | Booking decision | Calendar behavior |
| --- | --- | --- |
| `internal` (UI: **Solo Melpis**) | Existing A05/local policy is authoritative. | Google is not required and existing work is not opted in by selecting or defaulting this mode. A transition to this mode freezes unresolved work; an existing cancellation can still request provider cleanup. |
| `internal_calendar_sync` (UI: **Melpis con Google opzionale**) | A05/local policy is authoritative. | Booking writes create/update the existing Google ledger through migration 064. Provider failure or uncertainty does not change the local booking decision. |
| `google_authoritative` (UI: **Google autorevole**) | A booking remains pending until verified Google availability and event state allow the fenced finalization path. Direct `confermata` insert/update is rejected by the migration 064 trigger. | The worker verifies availability, writes or reconciles the owned event, rechecks the lease/revision, and finalizes only after its proof and local A05/capacity checks pass. An unknown result is not free. |

The booking policy and transport share A05's existing validation and
`external_booking_sync` ledger. Migration 064 adds Calendar desired, claimed,
and applied revisions plus connection epoch, lease token/fence, dispatch state,
payload hash, event ETag, verification time, and authoritative-finalization
metadata. It does not create a second booking store or opt historical bookings
into synchronization. `src/core/db/migrations/064_calendar_lifecycle.sql`
marks unresolved legacy Google work without a verified connection epoch as
blocked rather than inferring consent or connection state.

OAuth requests the `calendar.events` and `calendar.events.freebusy` scopes
(`src/core/calendar/connection.py::SCOPES`). Calendar auth is owner/MFA scoped,
uses bound OAuth state and PKCE, and validates the configured redirect URI in
`src/core/calendar/routes.py`. No credentials or tokens belong in client
responses or logs.

## Availability and delivery guarantees

`get_busy_intervals` returns `CalendarAvailability` with explicit
`verified_free`, `verified_busy`, `unknown`, or `disabled_or_unconfigured`
status. `parse_freebusy` requires the complete exact resource set, valid
`calendar#freeBusy` response kind and time bounds, and valid intervals. Missing,
extra, malformed, errored, stale, or incomplete provider evidence yields
`unknown`; it is not converted to an empty busy list. Local ambiguous or
nonexistent DST wall times are rejected.

`CalendarLifecycle` is the single provider writer for create, update, and
delete. A request that may have reached Google is marked as possibly sent.
Expired recovery keeps the original claimed revision, action, and payload hash;
it performs an exact GET reconciliation and does not repeat the write merely
because GET returned 404. Lease/fence/revision checks prevent stale workers
from finalizing newer intent. Stable event IDs and ownership metadata prevent
cross-tenant adoption and cancellation resurrection.

There is no atomic transaction spanning Google free/busy and local booking
insert/finalization. Availability can change between the free/busy read and the
local write, and provider/network outcomes can be uncertain. The implementation
does not promise a cross-provider lock, atomic “free then book” operation, or
zero race window. `google_authoritative` has a final proof and local capacity
revalidation path, but these do not make Google and PostgreSQL one transaction.
Human review remains necessary for unresolved provider outcomes.

## Schema and compatibility gates

Migration files are ordered `060` through `064`; the files describe distinct
prerequisites and must be verified against the actual target rather than
assumed present from checkout state or CI:

1. `060_quota_provenance.sql` establishes trusted quota provenance.
2. `061_provider_attempt_fencing.sql` establishes provider-attempt leases and
   uncertainty fencing.
3. `062_auto_booking.sql` establishes A05 booking policy and backend-owned
   booking mutations.
4. `063_gdpr_operations.sql` establishes GDPR operation records and controls.
5. `064_calendar_lifecycle.sql` adds the Calendar lifecycle to existing
   credentials, OAuth nonce, booking, confirmation-outbox, and external-sync
   tables.

The local readiness configuration's `GOOGLE_CALENDAR_SCHEMA_VERSION=064` is an
operator assertion only. The runtime database path must verify actual schema
064 before enabling the worker. The preflight requires Calendar disabled by
default and, to enable it, affirmative operational approval, a non-placeholder
certification reference, schema assertion `064`, worker enablement, OAuth
client values, a valid Fernet key, and a strict HTTPS redirect on the
`PUBLIC_APP_URL` origin. Google Business Profile remains forced off. Neither
startup guard nor release preflight contacts Google or proves staging
certification; see `docs/operations/calendar-production-readiness.md`.

## Booking UI status handling

`web/dashboard-integrations.js` presents the three modes, connection states,
queue counts, and safe error labels. Saving is explicit; onboarding does not
require Calendar or opt it in. The booking list in
`web/dashboard-bookings.js` renders the actual `p.stato`, so an authoritative
booking whose backend state is `in_attesa` is not displayed as confirmed.

The booking list now consumes `calendar_confirm_pending` and shows
“Prenotazione non confermata: in attesa di nuova verifica Google”; blocked and
uncertain authoritative attempts have distinct labels. That label takes
priority over external sync state, so an authoritative booking cannot be shown
as confirmed or synced while confirmation is pending. Optional-mode confirmed
bookings retain distinct local-confirmed wording for queued, failed, blocked,
or uncertain Google sync. Only the backend's `sync_failed` and `retry_pending`
error-code enum values are displayed; unrecognized values are suppressed.

## Staging cutover, backup, drain, and rollback

This plan is **not executed**. No migration, backup, worker drain, staging
OAuth, Google event operation, or rollback was performed for this review.

1. Identify the staging project, application revision, organization, and
   operator. Confirm the target database is staging and that encrypted backup
   and restore procedures work. Preserve existing backups.
2. Keep Calendar and its worker disabled. Inventory existing Google ledger
   rows, credentials, connection epochs, active leases, blocked rows,
   possibly-sent/uncertain requests, and pending/retry work. Reconcile or
   explicitly hold uncertain attempts; do not clear them to make the drain
   appear complete.
3. Drain only after stopping new Calendar work. Verify no active worker leases
   or in-flight requests remain. Preserve a scoped inventory of pending,
   failed, uncertain, and blocked rows for post-migration reconciliation.
4. Independently verify that `060`–`063` are installed and compatible with the
   reviewed application revision. Take a fresh protected backup and prove
   restore readiness. Apply `064` to the identified staging database only
   under separate migration authorization; verify actual columns, indexes,
   trigger definitions, ownership, and grants before proceeding.
5. Keep Calendar off until C03 certification and the runtime schema check pass.
   Obtain explicit staging-account consent for both required scopes and
   separately approve each event create, update, delete, or cleanup operation.
   Record exact event/calendar IDs and redact tokens and unrelated personal
   data.
6. Re-enable only after reviewing the staging receipts, per-resource
   free/busy outcomes, authority-mode cases, uncertainty recovery, OAuth
   disconnect/reconnect epochs, DST cases, idempotent replay, and UI pending
   status. Any failed or uncertain criterion is a no-go.
7. On rollback, stop new Calendar work and keep the feature disabled. Do not
   blindly reverse `064`, reset fences, erase queue history, or replay an
   uncertain provider write. Preserve the protected backup and scoped row
   inventory; reconcile remote event IDs individually under approval. Prefer a
   reviewed forward repair if the migration has been applied. Restore only
   under the approved staging recovery procedure, then reconcile database and
   provider state before reopening traffic.

The broader local backup/restore and migration gates in
`docs/operations/a04-review-and-staging-cutover.md`,
`docs/operations/a06-gdpr-runbook.md`, and
`docs/operations/b01-google-staging-e2e.md` remain applicable.

## Review evidence status

PR [#131](https://github.com/Scontismaart/Melpis/pull/131) records the final head
SHA and its exact CI receipts; earlier successful checks do not certify a newer
head. Local snapshots include the full unit suite (**1,477 passed**, before the
last two health regressions), the full frontend suite (**405 passed, 3 skipped**),
and **74 passed** in the final targeted OAuth/MFA/availability/lifecycle run.
Ruff, frontend lint and tenant-scoping checks passed locally.

The unchanged CI runs the complete application suite against disposable
PostgreSQL/Valkey, the migration check through 064, installed-dependency audit,
frontend checks, and four API/web Docker builds. The audit retains only the
existing explicitly reviewed baseline findings; no security exception was
added for B01. PR builds do not publish GHCR images or deploy services.

PostgreSQL regressions cover authoritative deferred confirmation, optional
provider failure, create/update/delete, concurrent claims, durable per-tenant
dispatch exclusion, timeout/GET-only recovery, reconnect receipts, cancellation
during creation, revoked authorization, timezone changes, GDPR blocking and
lease expiry during final notification insertion. The final transaction CAS
checks wall-clock lease and fresh availability after all notification/FK waits;
failure rolls back confirmation/outbox/receipt together and preserves the
possibly-sent request for reconciliation. Reconnect never clears uncertainty
on 404 and never re-enables an explicitly paused integration.

These are synthetic provider tests, not real Google certification. Actual
target-schema verification, C03 staging certification and cutover gates remain
unexecuted. No Google network call, remote migration or deployment was made
as part of this review.
