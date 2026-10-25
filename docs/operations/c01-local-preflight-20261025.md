# C01 local migration preflight — 25 October 2026

This runbook describes a **local disposable PostgreSQL exercise** for migrations
060–066. It is not a production migration approval or evidence that a remote
database has this schema. No remote Supabase connection is part of this runbook.

The offline preflight is [`scripts/c01_migration_preflight.py`](../../scripts/c01_migration_preflight.py).
It supports a fresh local schema (`clean`), a verified 059 baseline (`upgrade`),
and a post-066 repeat (`replay`). It defaults to printing the migration plan.
Execution additionally requires `--execute`, a local DSN restricted to
`127.0.0.1:55496/melpis_c01_*`, an explicit acknowledgement, an administrative
role, a database-side synthetic marker, and a matching PostgreSQL
`application_name`. The script never creates the marker or a database, reads
no `.env` file, prints no DSN, and has no automatic `DROP` or rollback path.
After migrations, replay, and postconditions all succeed, it advances the
marker `baseline_version` to 66. An interrupted attempt leaves the earlier
marker in place and requires manual inspection before any retry.

```powershell
python scripts/c01_migration_preflight.py --scenario clean
python scripts/c01_migration_preflight.py --scenario upgrade
python scripts/c01_migration_preflight.py --scenario replay
```

These commands are read-only planning. Run `--execute` only after the owner has
provisioned an isolated database on port 55496, verified its identity, supplied
its local DSN and explicit GO, and seeded the marker. The marker is a precondition
and must be created by the database owner only after verifying the exact
synthetic database; the tool refuses to create it. Required marker shape:

The client DSN remains fixed to `127.0.0.1:55496`. With the dedicated
containerized PostgreSQL service, host port 55496 is forwarded through NAT to
container port 5432; the server therefore reports port 5432 and a private
container address (or loopback). The guard checks those server-reported values
along with the database name, marker, application name, and administrative
role. It rejects public server addresses and does not loosen the client DSN.

```sql
CREATE TABLE public.c01_synthetic_marker (
  marker_key text PRIMARY KEY,
  marker_value text NOT NULL,
  baseline_version integer NOT NULL
);
INSERT INTO public.c01_synthetic_marker
  (marker_key, marker_value, baseline_version)
VALUES ('melpis-c01-preflight', 'C01_SYNTHETIC_DISPOSABLE_V1', 0);
```

Use baseline version `0` for a newly provisioned empty database, `59` for an
independently prepared 059 schema, or `66` for replay. Do not reuse a shared,
staging, or production database. The script checks the marker before any DDL,
then confirms server address, port, database name, application name, expected
baseline shape, required roles, and administrative privileges.

## Migration inventory and dependencies

| Migration | Main change | Preconditions and operational check |
|---|---|---|
| `060_quota_provenance.sql` | Revokes client writes on `messages` and `usage_events`, preserves prior backend capabilities, and writes/rotates the quota trust epoch marker. | Requires existing RLS and the baseline ACL shape on both tables. The migration takes access-exclusive locks. Verify effective table and column grants before reopening writers. Replay intentionally rotates the trust epoch. |
| `061_provider_attempt_fencing.sql` | Adds runtime lease/fencing state and append-only provider attempt evidence, backfills legacy attempts as uncertain. | Depends on `usage_events` and provider-attempt rows. An unresolved legacy attempt is uncertain; do not infer that it was never sent. Verify application compatibility with these tables before resuming provider dispatch. |
| `062_auto_booking.sql` | Adds booking confirmation outbox and booking authority state/policies. | Depends on organizations, bookings, booking settings, message rows, and existing booking integrations. Check pending confirmation/outbox state and policy defaults. |
| `063_gdpr_operations.sql` | Adds durable operation, policy, export, event, and erasure barrier records; installs write guards and immutable evidence grants. | Depends on the tenant/contact/message/booking/document and integration tables, plus prior provider/booking work. No retention-policy rows are approved or seeded by this migration. Verify barriers and application writer paths. |
| `064_calendar_lifecycle.sql` | Adds Calendar connection generations, sync fencing, and booking lifecycle triggers. | Depends on booking changes from 062 and Google Calendar credential/sync tables. Legacy pending Google work without verified connection context is blocked. Verify queue state before enabling calendar workers. |
| `065_document_publication.sql` | Adds fenced document index claims, atomic publication, knowledge epochs, FAQ invalidation, and client write revokes. | Depends on `documents`, `document_chunks`, `faq_cache`, organizations, and the GDPR write guard from 063. Verify RLS, tenant scope, table and column privileges, and publication/replay behavior. |
| `066_document_file_bytes.sql` | Adds original-file byte/hash evidence, pending replacement evidence, and fail-closed constraints. | Depends on the publication protocol from 065. Historical byte counts are unknown and remain NULL. Do not estimate them from chunks or `metadata.bytes`. |

## Cutover sequence

1. Freeze the release revision and stop all old API processes, inbound
   processors, provider dispatchers, booking/calendar workers, and scheduled
   writers. Wait for in-flight database transactions and reconcile uncertain
   provider attempts. Keep external side-effect workers stopped throughout the
   local exercise.
2. Capture a protected database snapshot and confirm a separate restore target
   is available. Restore and inspect it before treating it as a recovery point;
   see [`supabase_r2_backup_restore.md`](supabase_r2_backup_restore.md) for the
   existing backup/restore design and its separate approval requirements.
3. Prepare two dedicated local databases if both clean and upgrade paths are
   needed. Set the synthetic marker values as described above. The tool refuses
   hostnames other than the literal loopback IPv4 address, all ports other than
   55496, database names outside `melpis_c01_*`, query parameters, and an absent
   explicit GO.
4. Run the `clean` path against an empty database and the `upgrade` path against
   a separately verified 059 baseline. Both paths apply 060–066 in numeric
   order, then replay 060–066. The `replay` path replays only an already marked
   066 database.
5. Review postconditions: RLS enabled on documents, chunks, FAQ cache and usage
   ledger; no client table or column write grants on protected document/ledger
   relations; required tenant-scoped lifecycle tables exist; evidence tables
   remain append-only; historical `file_bytes` values are still unknown; and
   all migration output is clean. The script does not certify provider E2E or
   remote Supabase state.
6. For a real cutover, obtain a separate approved change window and use the
   reviewed release migration procedure. Deploy compatible application code,
   keep workers drained until schema and grants are verified, then resume
   workers in a controlled order. Monitor failed provider attempts, booking
   queues, GDPR barriers, document publication, quota admission, and database
   locks.

## Historical file accounting

Migration 066 intentionally does not backfill `file_bytes` or `file_sha256`.
`src/core/documenti/file_quota.py` counts unknown documents and blocks additional
file growth while historical usage is unknown. This is the expected safe state
until the owner either reuploads the source or attests its byte count and digest
from a retained original. Never estimate original upload size from extracted
text, vector count, chunk metadata, or legacy `metadata.bytes`; that field also
covered text, FAQ, and web imports. Numeric plan limits remain a separate
founder decision.

## Abort and recovery

Stop on an identity, marker, baseline, ACL/RLS, migration, lock-timeout, or
postcondition failure. Do not restart old workers against a partially upgraded
schema. Preserve logs without connection strings or customer payloads, inspect
the disposable database, and repair forward or recreate that disposable target
only after owner approval. The script never runs automated down migrations or
table drops. For a real database, recovery means restoring the verified backup
to an isolated target and following a separately approved recovery plan; do not
drop new evidence tables or erase append-only records as a rollback shortcut.

## Local verification performed

The offline unit suite covers DSN refusal, NAT identity validation, migration
ordering, dry-run inventory, and the explicit GO gate. Against the separately
authorized local synthetic cluster `melpis-cycle-pg-20261025` only, both clean
installation and upgrade from the seeded 059 baseline completed; 060–066 replay
then passed on each database. The final replay performed live checks using the
`anon` and `authenticated` roles, with temporary SELECT grants inside a rolled
back transaction to exercise RLS against the historical upload. It also
confirmed denied DELETEs, preserved backend grants, the commercial counter at
7, and the historical upload's `file_bytes` NULL state. See the machine-readable
[`c01-local-preflight-results-20261025.json`](c01-local-preflight-results-20261025.json)
for the per-database results. No Supabase remote state or provider was tested.
