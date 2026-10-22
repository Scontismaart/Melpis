# A06 GDPR rights operations runbook

## Status and authority

A06 is a technical partial, not issue closure or a legal certification. The
DPA content is a technical template pending C05 review and approval. A06 does
not approve a retention schedule. Migration 063 seeds no policies;
policy and operation-event records are immutable, and the application
`service_role` cannot insert, update, or delete policy rows. A controller or
founder must provide the C05 approval and an authorized operational cutover
before primary-data erasure or retention can run. Do not infer periods from
legacy constants or sample values.

All three execution flags default to off and must be set explicitly to the
literal string `true` for their separate work:

- `GDPR_ERASURE_EXECUTION_ENABLED`
- `GDPR_RETENTION_EXECUTION_ENABLED`
- `GDPR_PROVIDER_ERASURE_EXECUTION_ENABLED`

No production policy rows or operational approval are documented here. Until
those approvals and deployment checks exist, erase requests should remain
blocked and retention should not mutate primary data. Export snapshot expiry
is separate from primary-data retention.

## Endpoints and authorization

Export and erasure request routes, plus operation status/retry, require an
authenticated organization owner with current MFA (`aal2`). Informational
routes such as the DPA and retention-policy response are not owner request
routes. Export initiation requires a current authenticated session. Download
also requires the same owner, account, user, organization membership, and
authenticated session that created the ticket. Stale or missing session
identity is rejected. When an owner belongs to multiple organizations, the
selected organization must match the export's organization.

| Method and path | Purpose |
| --- | --- |
| `GET /api/gdpr/export` | Legacy organization export request. Creates a bounded snapshot and a one-time download ticket. |
| `POST /api/gdpr/export` | Scoped export. JSON body: `request_id` (UUID, required), `scope` (`organization`, `contact`, or `account`, default `organization`), and `subject_id` (UUID; required for `contact`). |
| `GET /api/gdpr/download` | Streams the ticket's snapshot once after rechecking owner, MFA, organization, session and current membership. |
| `POST /api/gdpr/download/revoke` | Revokes the current ticket and its snapshot where present. |
| `POST /api/gdpr/delete` | Durable erasure request; JSON body required with `request_id` (UUID), `scope`, and optional `subject_id`. Returns HTTP 202 with operation status. Missing body is rejected; do not create non-idempotent requests implicitly. |
| `GET /api/gdpr/operations/{operation_id}` | Reads the requester's operation status and step summary. |
| `POST /api/gdpr/operations/{operation_id}/retry` | Re-evaluates a durable erasure operation; it does not make an uncertain provider deletion safe to repeat. |

`request_id` is idempotent per organization and requester. Reusing it with a
different kind, scope, or subject returns conflict. A contact identifier is
looked up within the selected organization. Account scope refers to the
requesting authenticated account for export; shared authentication-account
erasure remains blocked for owner-transfer and identity-provider review.
Erasure is currently supported for organization and contact scopes subject
to policy and cutover gates.

The download ticket is a short-lived, HttpOnly, Secure-when-configured,
SameSite=Strict cookie with a 15-minute lifetime. Redis and the export
encryption key are required. There is no bearer download URL; the response
returns only the same-origin `/api/gdpr/download` path. Cache and referrer
headers prohibit storage and referrer leakage. Legacy inline tokens that do
not point to a durable encrypted snapshot are rejected with 404 and no inline
data is returned. Do not copy tickets into logs, support systems, or URLs.

Snapshots are encrypted in PostgreSQL and streamed in bounded chunks. A row
over 512 KiB is omitted and recorded as partial. A snapshot over 512 MiB fails
closed and requires a controlled offline export process. Snapshot expiry is
15 minutes; the expiry worker removes encrypted chunks and marks the operation
expired. Verify the worker is scheduled and monitor failures. An export
manifest identifies excluded and partial categories; an export is not proof
that all external copies or backups were searched.

## Handling an erasure request

1. Confirm the owner and organization through the application, and record the
   supplied request ID and operation ID in the approved support workflow. Do
   not place contact text or credentials in operator notes.
2. Drain inbound webhook processing, AI generation, outbound delivery, and
   booking work for the organization. Resolve every uncertain provider attempt
   and quota reservation; an unresolved attempt blocks local erasure. Active
   bookings require controller action. Do not infer safety from an elapsed
   worker lease.
3. Review the operation status and blockers. The current implementation
   requires valid, unexpired, non-held, erasure-enabled policies for the
   categories involved and the cutover. Organization scope also requires
   knowledge-content and organization-profile policies. Missing policy rows
   are expected until C05 approval is recorded through the authorized process.
4. If the checks pass and erasure execution has been approved and enabled,
   the local transaction creates a durable erasure barrier and erases or
   redacts the supported relational records. It does not physically cascade
   delete the organization. Billing, audit, provider accounting, backups, and
   shared authentication accounts remain separately governed.
5. Contact-scope processing uses verified relational contact links. Unknown
   subject relationships in documents, chunks, FAQ/cache material, and
   unstructured text require controller review; the result remains partial or
   held. Free-text content may contain other people. Do not mark it erased by
   inference.
6. External provider work is a separately fenced stage. Recorded dependencies
can include Meta, AI providers, Google Calendar and Business, Airtable, external
booking, configured Softr/Sentry/SMTP, Stripe retention review, backups, and
worker caches. A permanent `provider.inventory_review` step is held for C05
inventory review; adapters cannot confirm it. Only a verified,
   authenticated, operation-correlated deletion receipt confirms a stage.
   Bare HTTP 2xx/202 and transport failures are uncertain. Never resend a
   destructive request after uncertainty without provider-native idempotency
   and trusted reconciliation. Provider execution is disabled unless separately
   authorized. Existing Airtable/Softr propagation helpers make no HTTP
   requests and return unconfirmed results. Discovering configuration is only
   evidence of possible copies, not proof that historical copies are absent.
7. Report the durable status and each blocker or partial category to the
   controller. Do not report complete while any required provider receipt,
   worker-cache handling, backup treatment, legal decision, or review remains
   unresolved.

The barrier prevents later guarded data production for an erased contact or
organization. It is durable and is not cleared by a timeout. Before allowing
traffic to resume after an erasure event, verify all relevant writers honor
the database barrier and that blocked inbound data is handled according to
the controller's instructions.

## Retention, backups, and evidence

Primary-data retention runs only when `GDPR_RETENTION_EXECUTION_ENABLED=true`
and valid, approved per-tenant category policies exist. Current code handles
message content and simulator responses; it does not establish a legal period.
`GET /api/gdpr/retention-policy` reports
`policy_version=tenant-approval-required`, the `C05 #100` policy/cutover gate,
and `null` for `retention_days`, `purge_after_days`, and
`simulator_response_days`. `simulator_history_idle_hours=24` is not a legal
retention period. `automatic_erasure_enabled` reflects the execution flag and
does not by itself establish policy approval.

Backups and worker-local simulator caches are separate copies. Erasure records
identify them as retained/pending; A06 does not purge existing backup objects
or prove worker-cache completion. Existing backups must not be deleted or
silently rewritten as an erasure shortcut. Any backup lifecycle, restore-time
re-erasure, or key rotation plan must preserve the ability to enforce
suppression barriers and must fail closed if their verification keys or
fingerprints are unavailable. Keep billing counters, A03 provider accounting,
and required audit evidence under their separately approved policies.

## Cutover and rollback gates

Do not enable execution flags until the controller's C05 approval, C01
cutover, migration and grants review, owner workflow, worker drain, and backup
and restore checks are complete. Apply migration 063 only after migrations
060, 061, and 062, and verify the installed schema and service grants. Drain
all application workers, inbox processing, and outbound senders; ensure no
uncertain provider attempts remain. Take a fresh protected backup. Restore it
to an isolated target, verify the restore, and confirm that deletion journals
and newer tombstones are replayed before reopening traffic. A backup existing
on disk is not evidence that restore and re-erasure work.

Check that client roles cannot write contacts directly and that every
production writer goes through the backend barrier-aware path. Confirm the
application identity cannot approve policies or rewrite immutable evidence.
The database service role is privileged within its service boundary; do not
present these grants as protection from a database administrator.

If a gate fails, stop the affected worker or route and leave the execution
flags off. Prefer a forward repair that preserves migration 063's operation,
barrier, policy, and event evidence. Do not drop lifecycle tables or reverse
barriers as a rollback shortcut. No remote migration, provider deletion, or
backup deletion is authorized by this runbook.

## Verification record

Record the deployed revision, migration verification, CI result, backup/restore
drill, isolated local test results, Valkey/Redis behavior, and any provider
simulation separately. The implementation owner reports 138 focused
PostgreSQL, Valkey, and authorization tests passed, including snapshot-race
coverage. This test result does not certify production PostgreSQL, Valkey,
backup, provider, or legal behavior. The synthetic snapshot and backup/restore
proof passed separately: one opt-in PostgreSQL test encrypted a synthetic
`pg_dump` in memory before writing it, restored decrypted bytes through stdin
into a newly created isolated database, and verified the barrier, billing
counter/ledger, and decryptable export. No existing backup was deleted.
Only the temporary files created by that synthetic test were removed; two
new synthetic restore databases remain. The passing database is
`melpis_a06_restore_ede0ae2ecd8b4b2e` (the earlier harness run is
`melpis_a06_restore_4f37335d522b4b61`). This proves a coherent snapshot,
not restoration of an older backup with a newer erasure journal. That
quarantine/reconciliation procedure remains a C01 operational gate.
CI status and final revision belong in the PR evidence. This runbook
does not certify a legal basis, retention schedule, or external deletion.
