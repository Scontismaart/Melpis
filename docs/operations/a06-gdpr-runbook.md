# A06 GDPR rights operations runbook

## Status and authority

A06 is a technical partial, not issue closure or a legal certification. The
DPA remains an unpublished contractual draft. The founder has approved the
five product decisions in [Privacy V1.0](../governance/privacy-data-governance-v1.0.md),
registered as `melpis-privacy-v1.0`; those decisions are closed, not pending
preference choices. This is not proof of effective enforcement. Migration 063 seeds no policies;
policy and operation-event records are immutable, and the application
`service_role` cannot insert, update, or delete policy rows. A controller or
authorized administrator must establish compatible effective tenant instructions,
versioned records and an authorized operational cutover
before primary-data erasure or retention can run. Do not infer periods from
legacy constants or sample values. The product registry now checks supported
worker day schedules against the approved maxima, including the locked latest
row and cross-category C2.4 permission. Unsupported schedules fail closed;
they are never silently clamped. Visibility, complete expiry coverage and
last-pertinent-activity timing still require implementation and verification;
see the [category matrix](../governance/privacy-v1-implementation-matrix.md).

All three execution flags default to off and must be set explicitly to the
literal string `true` for their separate work:

- `GDPR_ERASURE_EXECUTION_ENABLED`
- `GDPR_RETENTION_EXECUTION_ENABLED`
- `GDPR_PROVIDER_ERASURE_EXECUTION_ENABLED`

The cursor-backed C2.4 retention and hotel lifecycle sweep is a separate
least-privilege worker. It is scheduled only when all three of these are
`true`: `GDPR_RETENTION_EXECUTION_ENABLED`,
`GDPR_C24_OWNER_RETENTION_ENABLED`, and `GDPR_C24_AUTO_RETENTION_ENABLED`.
It also requires `GDPR_C24_OWNER_RETENTION_DSN`, a dedicated connection whose
login is validated for the narrow `melpis_c24_retention` role. The optional
`GDPR_C24_RETENTION_INTERVAL_SECONDS` must be between 60 and 86,400 seconds
(default 3,600). The scheduler fails closed if the dedicated DSN is missing;
it never falls back to the application/service-role connection. These code
gates do not establish that a deployed schedule, role, DSN, or policy exists.

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

## Review of partial subject exports

The frozen subject export intentionally excludes unstructured content when a
reliable subject boundary cannot be proven. The following is a proposed
operator procedure, not an enabled application feature or evidence that a
manual review has occurred. Until the controller adopts the workflow and names
an authorized reviewer and secure delivery route, keep the export marked
partial or held.

1. Authenticate the requester through the owner-and-MFA route and verify the
   requested subject belongs to the requested organization. Use the export
   operation ID and manifest as the scope record; never search by a contact
   string across tenants.
2. List the excluded/partial categories from the manifest. Identify only the
   specific records whose relationship can be reviewed without expanding the
   organization scope. Do not query arbitrary tables or change the frozen
   export snapshot in place.
3. Assign a reviewer authorized by the controller and restricted to the
   necessary tenant and categories. Review only the excluded material needed
   to answer the request. Redact other people's identifiers, credentials,
   unrelated special-category data, and content outside the request scope.
4. If the subject cannot be separated reliably, the record has unknown
   lineage, or a hold/third-party right may apply, stop and refer it to the
   controller or qualified reviewer. Leave that category partial/held; do not
   infer ownership or erase the source to make the export appear complete.
5. If the controller authorizes a supplementary extract, store it through the
   approved encrypted case-delivery channel. Record only the operation ID,
   category, reviewer, review date, redaction decision, disposition, and a
   non-sensitive artifact reference/hash. Keep source text and tokens out of
   issue comments, CI output, and ordinary logs.
6. Deliver the original manifest and the separately identified supplement
   together. Do not mark the application operation complete by ad hoc SQL.
   Resolve the status only through a verified workflow that records every
   requested category and outstanding provider/backup step; otherwise the
   result remains partial.

The barrier prevents later guarded data production for an erased contact or
organization. It is durable and is not cleared by a timeout. Before allowing
traffic to resume after an erasure event, verify all relevant writers honor
the database barrier and that blocked inbound data is handled according to
the controller's instructions.

## Retention, backups, and evidence

Primary-data retention runs only when `GDPR_RETENTION_EXECUTION_ENABLED=true`
and valid, approved per-tenant category policies exist. The general daily job
returns before its ordinary content worker when the newer C2.4 schema is
present, and removes only typed C2.4 booking proposal fields from eligible
source/cache records. SQL096 adds a bounded, hold-aware purge for eligible
terminal C2.4 messages only when receipt and subject lineage are present,
along with the policy and lifecycle checks; `deleted_at` need not be set. It
is merged into source, but remote application is unverified and no live
schedule is proven. Ordinary or legacy messages
without those C2.4 fields have no verified 60/90-day cleanup path in this
snapshot; overall message-retention coverage remains open.
The product policy does not assign a simulator-response period or authorize a
general simulator-response purge. The general job also does not perform the
dedicated C2.4 lifecycle sweep; SQL096 replaces and extends the SQL089
`c24_retention_sweep()` path.
`GET /api/gdpr/retention-policy` reports
`policy_version=tenant-approval-required`, the `C05 #100` policy/cutover gate,
and `null` for `retention_days`, `purge_after_days`, and
`simulator_response_days`. `simulator_history_idle_hours=24` is not a legal
retention period. `automatic_erasure_enabled` reflects the execution flag and
does not by itself establish policy approval.

| Category or copy | Code path and policy source | Failure, holds, and what is not proven |
|---|---|---|
| `messages_content` | With the newer C2.4 schema present, the generic job exits before its ordinary content worker. SQL096 adds a bounded hold-aware purge for eligible terminal C2.4 messages that have verified `c24_received_at` and original-subject lineage; `deleted_at` need not be set. SQL096 replaces and extends the SQL089 `c24_retention_sweep()` path, which handles supported lifecycle-aware inbox/source content under the dedicated role. | No mutation without the global flag and current tenant policy. C2.4 honors tenant `legal_hold`, `erasure_allowed`, valid policy windows, and lifecycle fences. Ordinary/legacy messages without C2.4 lineage have no verified 60/90-day cleanup path in this snapshot. A C2.4 database error stops that sweep; logs omit driver detail. Remote application is unverified; no live schedule or deployed tenant record was verified. |
| `simulator_responses` | No general retention path is authorized by the product policy; `simulator_response_days` is null. C2.4 booking proposal fields have a separate typed removal path. | A 24-hour simulator-history setting is not a legal retention period. Local simulator cache is a separate copy; no approved period or deployed purge schedule is proven. |
| `booking_conversation_content` | Migration 089 and the C2.4 owner sweep expire eligible typed conversation state/proposal material under the approved category policy. | The allowed effective period comes from a valid tenant record within the approved product policy. Legal hold, erasure permission, source lineage, and fences are checked. This is not blanket deletion of all booking text or customer records. |
| `technical_receipts` request records | The SQL089 sweep, replaced and extended by SQL096, handles concluded `booking_conversation_requests` under the 90-day technical-receipt policy using `c24_concluded_at` and the private keyset cursor. | Requires the C2.4 dedicated role, valid tenant policy, no hold, and safe lifecycle state. A held/uncertain target is deferred and the bounded cursor advances; a false `batch_clear` is not a completed sweep. Other webhook/idempotency rows are not covered by this claim. No successful live run is evidenced. |
| C2.4 replay and suppression metadata | The SQL089 sweep, replaced and extended by SQL096, applies the tenant technical-receipt window to `c24_source_fences.fenced_at`, `c24_idle_watermarks.expired_at`, and suppression audit `occurred_at` through its lifecycle sweep. | The approved policy anchor is request conclusion. A reviewed mapping from these event timestamps to that anchor is not established; keep the retention-policy mapping OPEN. Other audit-retention decisions and successful live runs are also unverified. |
| `booking_stay_content` | Migration 089's hotel sweep and migration 091's hotel lifecycle functions archive completed allocation state or purge eligible stay content after completion/lineage checks. | The 12-month period applies to eligible content only. It does not set a period for outbox, audit, or replay fences/tombstones; the reviewed source has no cleanup path for the stay purge fences and tombstones. Current approved policy, no matching hold, valid completion/allocation evidence, and safe terminal state are required. Schedule and tenant policy are unverified. |
| Worker capability payload (`c24_private.worker_capabilities`) | Migration 093 stores a short-lived JSON payload containing booking/customer and confirmation details. Merged PR #179 (`f5f5040`) adds migration 094 and transactional scrubbing: successful completion clears the payload; a PostgreSQL-only bounded cleanup nulls expired unconsumed payloads while retaining hashes and replay tombstones. Migration check [run #43741180457](https://github.com/Scontismaart/Melpis/actions/runs/43741180457) applied all migrations in order through 094 in a disposable database. | Expiry limits authority but does not itself delete content. CI/source evidence does not prove Staging or Production application or a scheduled run. No live schedule, backup/cache cleanup, or subject-scope behavior is certified. Do not treat capability tombstones as zero-cost billing evidence. |
| Encrypted export snapshots | The general retention worker expires export operations and removes encrypted chunks after the 15-minute snapshot lifetime, independently of the primary-data-retention flag. | The job must be deployed and monitored. A failed purge leaves data for retry; no live schedule or provider/backup deletion is established. |
| Other account, organization, consent, billing, audit, provider, log, cache, and backup records | No universal automatic expiry is established by this runbook. Their category and legal-hold rules require the approved controller schedule and specific owner workflow. | Do not infer a retention period from code defaults, technical receipt maxima, or the DPA draft. Keep records with a hold or unresolved legal/accounting basis until the authorized owner resolves it. |

### Proposed legacy-message provenance review (read-only)

This is a review protocol only. It neither authorizes retention or erasure nor
closes the legacy coverage gap. No verified application route currently
provides a complete legacy-message provenance inventory. Until the controller
adopts this workflow, names an authorized reviewer, and approves a restricted
evidence channel, no manual review is considered complete. Do not improvise a
service-role query or use the retention worker as an inventory endpoint.

1. Open a review for one organization from an authenticated owner-and-MFA
   request. Bind every inventory request and evidence reference to that
   organization. Never search by phone, provider identifier, or content across
   tenants.
2. Before assessing retention eligibility, confirm the effective
   `messages_content` policy for that organization in the authorized policy
   registry: current version, validity window, retention period, erasure
   permission, and hold state. If policy is absent, expired, held, or
   inconsistent, record the review as blocked and stop. The legacy
   `organizations.message_retention_days` value, code defaults, sample
   policies, and `simulator_history_idle_hours` are not substitutes.
3. Use only a separately approved, read-only, tenant-scoped inventory that
   exposes the minimum evidence needed to link a source message to its
   contemporaneous trusted ingress receipt. Do not include message bodies,
   arbitrary JSON, credentials, or other tenants' data in the review package.
4. Mark provenance as verified only when existing trusted evidence binds the
   same organization, message, conversation, original subject, and receipt
   timestamp. Migration 070 deliberately leaves historical
   `c24_original_subject_id` unset; migration 074 stamps `c24_received_at` on
   new writes and does not reconstruct the historical timestamp. A current
   conversation/contact link or `messages.created_at` does not establish the
   missing original-subject attribution or C2.4 receipt anchor. A phone-number
   match, message ordering, cache, or text similarity does not establish those
   historical facts. Do not fill or backfill the missing C2.4 fields.
5. If any link is missing, contradictory, or depends on inference, document
   provenance as unknown and leave retention coverage open. Do not assign a
   guessed date, mark the record expired, or claim eligibility.
6. Store only the case ID, organization ID, opaque record reference, evidence
   artifact reference/hash, effective policy version, reviewer, review date,
   and the provenance outcome in the approved restricted channel. Keep content,
   phone numbers, raw receipts, and tokens out of logs, issue comments, and
   ordinary notes. A hash is a reference, not proof of completeness.
7. This review performs no update, deletion, hold change, policy write, purge
   invocation, or completion marking. Any later retention remediation requires
   a separate approved implementation and security review using the dedicated,
   tenant-bound retention authority. Until that path and its deployment are
   verified, overall message-retention coverage remains open.

For C2.4, a false/ineligible target result is a deferral, not a success receipt.
Database/authorization errors stop the run and do not silently invoke the
application DSN. The scheduler records only the error class, then raises a
failed-job signal. Preserve the cursor and retry after an authorized repair;
do not weaken holds or lifecycle checks to force the cursor clear.

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
simulation separately. On the reviewed Python 3.12 runtime, 221 focused
GDPR, PostgreSQL, Valkey, authorization, MFA, and accounting tests passed
(one opt-in backup test skipped in that run), followed by 89 A04/A05 and
multichannel worker regressions. The runtime/provenance suite passed 62
tests separately. These results do not certify production PostgreSQL, Valkey,
backup, provider, or legal behavior. The synthetic snapshot and backup/restore
proof passed separately: one opt-in PostgreSQL test encrypted a synthetic
`pg_dump` in memory before writing it, restored decrypted bytes through stdin
into a newly created isolated database, and verified the barrier, billing
counter/ledger, and decryptable export. No existing backup was deleted.
Only the temporary files created by that synthetic test were removed; three
new synthetic restore databases remain. The latest passing database on the
reviewed runtime is `melpis_a06_restore_989767dd93994917`; the previous passing
database is `melpis_a06_restore_ede0ae2ecd8b4b2e` and the earlier harness run is
`melpis_a06_restore_4f37335d522b4b61`. This proves a coherent snapshot,
not restoration of an older backup with a newer erasure journal. That
quarantine/reconciliation procedure remains a C01 operational gate.
CI status and final revision belong in the PR evidence. This runbook
does not certify a legal basis, retention schedule, or external deletion.
