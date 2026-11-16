# C01 staging cutover

Status: **060–093 Staging cutover completed 2026-11-15.** This execution record supersedes the pre-GO status for the migration batch below. The broader acceptance criteria in GitHub issue #96 remain open; this record does not close them. Production remains out of scope.

## Completed execution record — 2026-11-15

The private operator evidence package records the following result. It contains migration metadata and synthetic smoke results; no customer row contents or credentials are copied into this runbook.

| Item | Recorded result |
|---|---|
| Target | Melpis Staging, project ref `ucncxwnhcdrqonknkvau` |
| Approved source commit | `7634562630d705a9e8dc644260bb0174f03a25fd` |
| Manifest digest | `8dfb5867e10510460deba6b42ab940a340a180e0620c8f11f2eb83e589340efb` (canonical JSON, sorted keys, compact UTF-8, excluding its own digest field) |
| CLI and TLS | Supabase CLI `2.75.0`; PostgreSQL TLS `verify-full` |
| Selection | Exactly 34 ordered migrations, `060`–`093`; role and seed options disabled |
| Final migration history | 39 records |
| CI | GitHub Actions run `43695636137` passed for the approved commit |
| Production | Not used |

The manifest and evidence refer to the approved source snapshot `7634562630d705a9e8dc644260bb0174f03a25fd`, which was `main` at the time of the cutover. Later commits have advanced `main`; this historical evidence remains bound to the approved snapshot. All 34 Git blob IDs resolve to files in that commit, and each manifest SHA-256 matches the corresponding Git blob contents. The Windows checkout has `core.autocrlf=true`; raw working-tree hashes differ for CRLF-converted files, while all 34 LF-normalized hashes match the manifest.

| Checkpoint | Cumulative history |
|---|---:|
| 060–061 | 7 |
| 062–067 | 13 |
| 068–075 | 21 |
| 076–081 | 27 |
| 082–089 | 35 |
| 090–093 | 39 |

The runner recorded two transient pre-dry-run stops for activity/lock pressure at 062–067 and 068–075. Both batches resumed after authorization; the final evidence records all six checkpoints as passed.

### Final synthetic smoke results

All 12 checks in the evidence package passed, and the synthetic rollback/concurrency fixtures were removed and verified:

1. History count and schema integrity.
2. RLS and ACL checkpoints.
3. Auth profile and authenticated tenant isolation for booking.
4. Client booking writes denied.
5. Synthetic booking insert/update/cancel and Calendar revision.
6. Dark Calendar sends nothing; authoritative confirmation is denied without verification.
7. Document knowledge epoch and byte-evidence validation.
8. GDPR barrier denies booking and document writes for a protected tenant.
9. Worker readiness denies a non-worker session.
10. Exclusive overlap rejected across catalog revisions.
11. Two-session overlap waits for an in-flight claim and succeeds after rollback.
12. Synthetic rollback and committed concurrency fixtures removed and verified.

### Read-only Staging metadata check — 2026-11-15

The Supabase project metadata identifies Staging in `eu-central-1` on PostgreSQL 17.6.1.166. A read-only PostgreSQL role-catalog query, run through the connected management query context, reported `session_user=current_user=postgres`; `postgres` is not a superuser and has `BYPASSRLS`; `service_role` has `BYPASSRLS` but no login; and `anon`/`authenticated` have neither login nor `BYPASSRLS`.

This records project metadata and role attributes only. The query ran as the management `postgres` principal; it does not establish the deployed VM/API `DATABASE_URL` identity, effective role memberships, or object ownership observed by the application pool. Do not treat it as proof that application authorization was exercised under the deployed service principal. No application rows were read and no remote settings or data were changed.

### Issue #96 boundary

Keep issue #96 open. The execution package proves the identified Staging target, exact migration release, history/checkpoints, scoped smoke behavior, and that Production was not used. The read-only metadata check confirms the Staging region and PostgreSQL version, but does not establish the deployed application database role. Full-release clean-install/replay, membership-revocation and limited-signup checks, and explicit service-role authorization under the deployed principal remain open. Do not repeat the completed cutover to fill these evidence gaps; use applicable existing local/CI evidence or schedule only the missing scoped verification.

## Preflight

1. Verify the GitHub head, review and required checks. Export the exact Git blobs from the approved commit, and verify the 34-entry SHA-256 manifest. Rerun preflight if that commit changes.
2. Confirm project ref and TLS verification, PostgreSQL 17, effective login and object owner `postgres`, and `btree_gist` availability. Read migration history and compare to the approved baseline inventory, including ownership, grants, default grants, policies, functions and Auth triggers. Recheck pending or processing Meta/Calendar/LLM work, writer compatibility, lock pressure and dedicated worker, scheduler and retention flags.
3. Record the date, scope and exclusions of the readable encrypted manual backup, and verify a second encrypted copy in independent protected storage. Classify the current data and obtain explicit acceptance of loss limited to verified synthetic, reproducible data. Record the recovery operator and the external dependencies. See Free Tier reconstruction below.
4. Stage the exact verified history files and only the next selected batch in an isolated CLI work directory. Pin Supabase CLI 2.75.0 and verify the release archive checksum. With the allowlisted Staging session target, use `db push --dry-run --include-all` only after verifying read-only behavior locally. Reject any extra, missing or reordered file. Supply credentials through the approved operator mechanism without printing connection strings. Do not use history repair.
5. The 34 migration files set `lock_timeout = '5s'` and `statement_timeout = '5min'` before their DDL. The pinned CLI runs `RESET ALL` before each file, so verify both settings again after that reset in the isolated PG17 runner. Also verify that no seed or role-bootstrap options are enabled.

The read-only Staging ACL inventory found effective client `TRUNCATE` drift on existing tables and separate default grants for tables created by `postgres` and `supabase_admin`. Migration 060 revokes current table grants and defaults for its `postgres` migration owner. Staging's `postgres` login is not a superuser or member of `supabase_admin`, so it cannot change that platform role's defaults. All current application tables are owned by `postgres`; keep application DDL on that principal and verify no application table is created under `supabase_admin`.

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
