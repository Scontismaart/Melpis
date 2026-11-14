# C01 staging cutover

Status: pre-GO. The sole database target is Melpis Staging, project ref `ucncxwnhcdrqonknkvau`. Obtain explicit GO only after the gates below pass. Keep Production out of scope.

## Preflight

1. Verify the GitHub head, review and required checks. Export the exact Git blobs from the approved commit, and verify the 34-entry SHA-256 manifest. Rerun preflight if that commit changes.
2. Confirm project ref and TLS verification, PostgreSQL 17, effective login and object owner `postgres`, and `btree_gist` availability. Read migration history and compare to the approved baseline inventory, including ownership, grants, default grants, policies, functions and Auth triggers. Recheck pending or processing Meta/Calendar/LLM work, writer compatibility, lock pressure and dedicated worker, scheduler and retention flags.
3. Record the latest usable managed backup or PITR time and allowable loss since that point. Record the recovery operator, in-place downtime or alternate restore target, and external dependencies. Keep the existing encrypted backup. A checksum alone gives no restore evidence.
4. Stage the exact verified history files and only the next selected batch in an isolated CLI work directory. Pin Supabase CLI 2.75.0 and verify the release archive checksum. With the allowlisted Staging session target, use `db push --dry-run --include-all` only after verifying read-only behavior locally. Reject any extra, missing or reordered file. Supply credentials through the approved operator mechanism without printing connection strings. Do not use history repair.
5. Verify per-file lock and statement timeouts with the chosen runner, including after its reset of session settings, and verify that no seed or role-bootstrap options are enabled.

## Execution after GO

Run a single operator and one transaction per file, with history recorded atomically by the qualified runner. Run 060–093 in ascending order. After each batch, confirm history, catalog and effective privileges before the next dry run.

| Batch | Checkpoint |
|---|---|
| 060–061 | Exactly one quota marker. Confirm no dangling provider claims, historical NULL-scope support and expected quota privileges. |
| 062–067 | Outbox scope FKs, nullable inbound, Calendar queue state, GDPR barrier ACL, document publication and byte evidence, booking credentials ACL. |
| 068–075 | Catalog and request idempotency, allocative exclusion, conversation lineage and deferred constraints, legal holds and immutable hotel evidence. |
| 076–081 | Scoped worker receipts and claims, historical retention/purge gates, provider and conversation provenance. |
| 082–089 | Dedicated roles dormant, monitoring ACL reduced as intended, admission epochs backfilled, worker identity protected by RLS and retention scheduling off. |
| 090–093 | Admission rules, lifecycle transactions and final worker capability ownership, ACLs and expiry. |

Suspend only incompatible writers with proven active leases or unsafe schema transitions. Keep Meta acknowledgement and durable intake available. A SQL error, timeout, uncertain commit, unexpected object, history mismatch, broader ACL or functional regression stops the operation.

## Recovery and final smoke

Read history and catalog before retrying a disconnected step. Record the last confirmed commit and whether DDL and its history record agree. If the failed file was atomic and absent, fix the cause and rerun its dry run. If a harmful change committed, keep affected writers fenced and select a tested forward fix or request separate authorization to recover to the verified point. Supabase in-place restores make the target unavailable during restore and can discard later writes. Reconcile Auth triggers, ACL, GDPR deletions, and external queue or provider events before reopening.

Verify the original five records plus exactly 34 new records, no invalid indexes or unvalidated constraints, and the expected RLS/ACL matrix. Use synthetic fixtures under controlled integration configuration to check booking, cancellation, concurrent resource allocation, Calendar, document publication, GDPR and retention, claim and capability expiry, inbound durability, outbound deduplication, opt-out and human escalation. Resume each affected writer after its checks pass.

References: [CLI file transaction](https://github.com/supabase/cli/blob/v2.75.0/pkg/migration/file.go), [backup and PITR](https://supabase.com/docs/guides/platform/backups), [restore to a new project](https://supabase.com/docs/guides/platform/clone-project).
