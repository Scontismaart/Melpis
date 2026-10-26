# B01 Google Calendar staging E2E plan

**Status: planned, not certified.** This is the execution plan for C03 #98. No Google request, OAuth flow, database migration, or remote event operation has been run as part of preparing this document. Passing unit tests is not evidence of staging-provider readiness.

## Private-beta boundary

Google Calendar is optional for each tenant: a participating business may leave
it disconnected and disabled. The Calendar integration must nevertheless be
fully functional and pass this complete C03 staging certification before the
private-beta go/no-go. Optional per-tenant use does not waive release-level
certification. This requirement does not authorize connecting a beta customer's
account or performing an unapproved provider operation; the staging approvals
and evidence requirements below still apply.

## Preconditions and approvals

- Use an explicitly authorized Google staging account and a calendar created for this test. Confirm the account owner, calendar ID, organization, staging environment, and an approved test window before connecting it.
- The user must approve the OAuth consent request for both `https://www.googleapis.com/auth/calendar.events` and `https://www.googleapis.com/auth/calendar.events.freebusy`. The current `calendar.events` grant alone does not authorize the free/busy read needed for verified availability.
- Obtain explicit user approval before each remote test-event create, update, or delete. Record the event's returned ID and calendar ID. Cleanup is a separate approved delete; never infer permission to delete from permission to create, and never delete automatically.
- Confirm local database backup/restore readiness and that the plan targets the local staging database only. Migration `064` is local-only for this plan; do not apply it to a remote or production database.
- Record the selected IANA timezone and the exact UTC interval for each test. Use dates covering both a 23-hour spring-forward day and a 25-hour fall-back day.

## Test matrix

Exercise booking decisions under each configured authority value and require an explicit unknown-provider case:

| Booking authority | Expected behavior to verify |
| --- | --- |
| `internal` | Local booking policy is authoritative; Google availability must not silently become booking authority. |
| `internal_calendar_sync` | Local booking is authoritative; optional Google delivery remains queued/idempotent and provider uncertainty does not rewrite the local result. |
| `google_authoritative` | A booking may be recorded locally as pending, but it must not become confirmed or notify the customer until Google returns verified availability and the exact event is read back with matching ownership, revision, epoch, and payload proof. |
| Provider unknown/error/missing resource | Fail closed for externally authoritative booking. Preserve `unknown` as distinct from verified free and verified busy; do not treat missing data, provider errors, stale/invalid responses, or an unconfigured provider as an empty calendar. |

`external_authoritative` is not a supported Calendar mode and must be rejected; do not use it as a synonym for `google_authoritative`. A local `in_attesa` booking can exist while Google proof is pending. This pending insert does not confirm the reservation or enqueue a customer confirmation; the Google proof must precede the final `CONFIRM` transition and its notification.

For the Google-backed checks, include all configured resources in one requested interval. Verify empty `busy` arrays for every resource produce verified free, any intersecting busy interval produces busy, overlaps are unioned, interval ends are exclusive, malformed/error responses produce unknown, and all timestamps normalize to UTC. Include the local DST fold and gap cases and reject ambiguous/nonexistent wall times rather than choosing a fold implicitly.

## Staging execution sequence

1. Record the release/build identifier, staging organization ID, operator/session identity, calendar owner identity, selected calendar ID, timezone, OAuth scope consent, feature-flag values, and current connection state. Do not record access or refresh tokens.
2. Run local-only migration `064` against the staging database after a database backup. Capture the migration receipt and verify the resulting schema. Stop if the database is not positively identified as local staging.
3. In the approved staging session, connect the authorized account and select the test calendar. Capture the authenticated owner/session and organization association, granted scopes, and saved calendar identity. Verify reconnect and disconnect controls are restricted to the expected organization and session.
4. With separate explicit approval, create one uniquely named test event and retain its exact returned event ID. Verify reads show that same event on the selected calendar. With separate approval, update only that event ID and verify the update. With separate approval, delete only that event ID and verify its absence. Capture a receipt for each approved action. If any result is uncertain, stop and reconcile by the recorded ID before considering a retry or cleanup.
5. Run the free/busy matrix without creating bookings. Record requested UTC start/end, resource IDs, provider outcome category, normalized intervals, connection epoch, and trace/correlation IDs. Do not log tokens, event descriptions, customer content, or unrelated calendar data.
6. Run booking-authority checks with deterministic fixtures or the approved staging provider path. For `google_authoritative`, verify a local pending booking is allowed, but an explicit `CONFIRM` remains pending and sends no customer notification until verified Google availability and matching event read-back proof complete. Confirm `internal`, `internal_calendar_sync`, `google_authoritative`, and provider unknown/error all produce their expected local decision and audit/outbox receipts. Replaying the same operation must not create duplicate bookings, notifications, sync rows, or remote events.
7. Verify an all-day interval spanning each DST transition is evaluated using the correct timezone boundaries (23 hours in spring, 25 in autumn), while transport and persisted instants are compared in UTC. Verify a repeated local wall time and a nonexistent local wall time are rejected.
8. Disconnect the staging account only after capturing the state and receiving approval for any required remote cleanup. Confirm the application stops using the connection, retains stable local/provider identity fields needed for audit/reconciliation, and does not report unknown availability as free. Reconnect only with fresh approval and confirm the new connection epoch prevents stale availability from being reused.
9. Restore the local database backup if the staging test requires rollback. Record which local rows/schema were restored and independently reconcile approved remote event IDs; a local rollback does not undo Google operations.

## Go/no-go evidence

The run is **not certified** until a reviewer can inspect receipts for the approved staging identity/session, OAuth scopes, local-only migration, event IDs and individually approved create/update/delete/cleanup actions, per-resource free/busy results, authority-mode decisions, provider-unknown behavior, UTC and DST cases, idempotent replay, disconnect/reconnect state, and rollback. Include timestamps and correlation IDs while redacting secrets and unrelated personal data.

There is no global atomic transaction spanning Google free/busy and local booking insertion. A free/busy result can become stale before a local insert; concurrency races and provider/network uncertainty remain possible. Therefore this plan does not claim a cross-provider availability lock or an atomic “free then book” guarantee. A no-go is required if the implementation or observed receipts imply such a guarantee, if any provider uncertainty becomes free, if the tenant/session/calendar ownership check fails, or if an unapproved remote side effect occurs. Keep human review/escalation available for unresolved cases.
