# C01 staging cutover

Status: pre-GO. The sole database target is Melpis Staging, project ref `ucncxwnhcdrqonknkvau`. Obtain explicit GO only after the gates below pass. Keep Production out of scope.

## Preflight

1. Verify the GitHub head, review and required checks. Export the exact Git blobs from the approved commit, and verify the 34-entry SHA-256 manifest. Rerun preflight if that commit changes.
2. Confirm project ref and TLS verification, PostgreSQL 17, effective login and object owner `postgres`, and `btree_gist` availability. Read migration history and compare to the approved baseline inventory, including ownership, grants, default grants, policies, functions and Auth triggers. Recheck pending or processing Meta/Calendar/LLM work, writer compatibility, lock pressure and dedicated worker, scheduler and retention flags.
3. Record the date, scope and exclusions of the readable encrypted manual backup, and verify a second encrypted copy in independent protected storage. Classify the current data and obtain explicit acceptance of loss limited to verified synthetic, reproducible data. Record the recovery operator and the external dependencies. See Free Tier reconstruction below.
4. Stage the exact verified history files and only the next selected batch in an isolated CLI work directory. Pin Supabase CLI 2.75.0 and verify the release archive checksum. With the allowlisted Staging session target, use `db push --dry-run --include-all` only after verifying read-only behavior locally. Reject any extra, missing or reordered file. Supply credentials through the approved operator mechanism without printing connection strings. Do not use history repair.
5. Verify per-file lock and statement timeouts with the chosen runner, including after its reset of session settings, and verify that no seed or role-bootstrap options are enabled.

## Execution after GO

Run a single operator and one transaction per file, with history recorded atomically by the qualified runner. Run 060–093 in ascending order. After each batch, confirm history, catalog and effective privileges before the next dry run.

| Batch | Checkpoint |
|---|---|
| 060–061 | Exactly one quota marker. Confirm zero effective client TRUNCATE grants, no dangling provider claims, historical NULL-scope support and expected quota privileges. |
| 062–067 | Outbox scope FKs, nullable inbound, Calendar queue state, GDPR barrier ACL, document publication and byte evidence, booking credentials ACL. |
| 068–075 | Catalog and request idempotency, allocative exclusion, conversation lineage and deferred constraints, legal holds and immutable hotel evidence. |
| 076–081 | Scoped worker receipts and claims, historical retention/purge gates, provider and conversation provenance. |
| 082–089 | Dedicated roles dormant, monitoring ACL reduced as intended, admission epochs backfilled, worker identity protected by RLS and retention scheduling off. |
| 090–093 | Admission rules, lifecycle transactions and final worker capability ownership, ACLs and expiry. |

Suspend only incompatible writers with proven active leases or unsafe schema transitions. Keep Meta acknowledgement and durable intake available. A SQL error, timeout, uncertain commit, unexpected object, history mismatch, broader ACL or functional regression stops the operation.

## Recovery and final smoke

Read history and catalog before retrying a disconnected step. Record the last confirmed commit and whether DDL and its history record agree. If the failed file was atomic and absent, fix the cause and rerun its dry run. If a harmful change committed, keep affected writers fenced and select a tested forward fix or request separate authorization for controlled Free Tier reconstruction. Reconcile Auth triggers, ACL, GDPR deletions, and external queue or provider events before reopening.

Verify the original five records plus exactly 34 new records, no invalid indexes or unvalidated constraints, and the expected RLS/ACL matrix. Use synthetic fixtures under controlled integration configuration to check booking, cancellation, concurrent resource allocation, Calendar, document publication, GDPR and retention, claim and capability expiry, inbound durability, outbound deduplication, opt-out and human escalation. Resume each affected writer after its checks pass.

## Free Tier reconstruction

1. Keep affected workers and external jobs fenced. Retain the encrypted source and verified independent copy. Confirm which records must be recovered and which have explicit approval for loss. Capture the failed state separately if needed for diagnosis.
2. After separate authorization, use a blank approved Staging target with no external dispatch configured. Confirm its identity and TLS before writes. Prepare platform roles, supported extensions and Auth settings, then reconstruct the versioned baseline and documented Staging deltas. The live baseline had CASCADE on the organization FK for faq_cache, message_feedback and onboarding_profiles. Retain those semantics in reconstruction unless separately reviewed. The deployment batch leaves those FKs unchanged. Restore the 045 client TRUNCATE policy and validate the actual grants.
3. Inspect the encrypted archive inventory before selecting application data to recover. Restore in dependency order and verify counts, tenancy and history. Use the approved managed Supabase path to re-create or import Auth users and verify Auth triggers and lifecycle. Reconfigure custom roles without reusing passwords and reconcile Auth settings, Storage objects, provider configuration, secrets and webhook destinations separately. Verify that each required legacy history record has a corresponding approved migration through the supported deployment path.
4. Rerun the catalog, ACL and two-tenant checks, reconcile GDPR deletions, and ensure pending provider operations cannot be replayed with unintended external effects. Keep the target isolated until the smoke tests pass. The synthetic PG17 rehearsal proves the SQL path only. Full hosted reconstruction, imported Auth and external reconnect remain untested.

[CLI migration transactions](https://github.com/supabase/cli/blob/v2.75.0/pkg/migration/file.go) · [Supabase Free Tier backups](https://supabase.com/kb/faqs/platform/free-plan-backups)
