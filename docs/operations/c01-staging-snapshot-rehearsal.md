# C01 Staging snapshot and restore rehearsal

This procedure is restricted to the Supabase project `ucncxwnhcdrqonknkvau`.
It uses the Session Pooler session endpoint on port 5432 and requires
`sslmode=verify-full` with the pinned Supabase root certificate. The source
connection is read-only. The snapshot code keeps the repeatable-read transaction
open while `pg_dump` imports its exported snapshot. Both source and clone
catalog collectors pin and record `search_path=pg_catalog` for stable object
and type rendering.

The encrypted artifact contains the PostgreSQL custom archive with database and
object owners/ACLs, a `pg_dumpall --roles-only --no-role-passwords` snapshot, and
an encrypted catalog/data manifest. AES-GCM authenticates the archive; the
sidecar manifest binds its length, SHA-256, Staging project ref, and catalog
fingerprint with HMAC. No plaintext dump is written to disk. The database
manifest includes per-table row counts and deterministic content fingerprints,
sequence values and parameters, sequence-to-column ownership, column defaults
and identity/generated metadata, large-object counts/byte totals/content
fingerprint/owners/ACLs, index validity and readiness flags, and metadata for extensions/dependencies,
roles/memberships, schemas, default and object ACLs, RLS, policies, Auth
triggers, functions and aggregates, `search_path` and transition definitions,
constraints, indexes, types, and
`supabase_migrations` history. It does not include row contents outside the
encrypted archive.

The Windows operator key is generated once and persisted as a current-user
DPAPI-protected blob with application-specific entropy. Its containing folder,
the key file, the source environment file, and the local-clone connection file
must pass the protected-ACL check. PowerShell 7 (`pwsh`) is required to inspect
the exact ACL; absence of the executable or ACL module fails closed. Plaintext key files are rejected. The source
environment must pin the Staging Session Pooler identity and CA; the separate
clone-only environment may contain only `127.0.0.1`, the random published port,
database `postgres`, local user `postgres`, its password, and `PGSSLMODE=disable`.
That local connection is accepted only after the Docker internal-network and
loopback-only target checks pass. Neither environment file or key material is
printed by the CLI.

The bundle key is generated randomly and persisted only as a Windows DPAPI
current-user protected blob with project-specific entropy inside the same
restricted local directory. Plaintext key files and key arguments are rejected;
the private ACL is checked before both key use and backup reads. The restore
driver uses a collector that verifies the isolated container's internal network,
loopback-only mapping, local connection file ACL, and pinned PG17 image before
and after restore. It returns a receipt only after the complete catalog/data
snapshot, including sequence ownership and state, matches.

## Disposable PostgreSQL 17 clone

Restore is permitted only into a running Supabase PostgreSQL 17 container on a
dedicated Docker `--internal` network with exactly that one container attached,
no host bind mounts, `tmpfs` data, and PostgreSQL published only to
`127.0.0.1` on a randomly assigned port. The expected image digest, internal
network flag, exact attachment set, and loopback binding must be rechecked before
each write. No other container or internet route is permitted. The restore role
is the local `supabase_admin`, used only inside that
isolated container so ownership and ACLs can be restored. This does not grant
that role to an application or any remote database.

The source Staging inventory observed during preflight is:

| Extension | Version | Schema |
| --- | --- | --- |
| `pg_stat_statements` | 1.11 | `extensions` |
| `pgcrypto` | 1.3 | `extensions` |
| `plpgsql` | 1.0 | `pg_catalog` |
| `supabase_vault` | 0.3.1 | `vault` |
| `uuid-ossp` | 1.1 | `extensions` |
| `vector` | 0.8.2 | `extensions` |

The candidate local Supabase image is PostgreSQL 17.6.1.171, while the remote
server reports PostgreSQL 17.6.1.166. Its major/minor version and the six
extension versions above are compatible, but this patch-level difference must
remain visible in the rehearsal receipt. The candidate image also has
`pg_graphql` in its initial `postgres` database; the source does not. A
`pg_dump --create` restore recreates the source `postgres` database from
`template0`, so this initial-image-only extension is not copied into the clone.
The post-restore extension comparison still must match exactly.

Two role-flag differences were observed in the candidate local image:

| Role | Image default `BYPASSRLS` | Staging `BYPASSRLS` | Clone-only normalization |
| --- | --- | --- | --- |
| `pgbouncer` | true | false | `ALTER ROLE "pgbouncer" NOBYPASSRLS` |
| `supabase_auth_admin` | true | false | `ALTER ROLE "supabase_auth_admin" NOBYPASSRLS` |

The image also lacks `supabase_realtime_admin`. Staging has it as `NOLOGIN`,
`NOBYPASSRLS`, `NOINHERIT`, `NOCREATEROLE`, `NOCREATEDB`, and
`NOREPLICATION`. The only allowed clone-only creation and memberships are:

```sql
CREATE ROLE "supabase_realtime_admin" NOLOGIN NOBYPASSRLS NOINHERIT
  NOCREATEROLE NOCREATEDB NOREPLICATION;
GRANT "anon" TO "supabase_realtime_admin"
  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
GRANT "authenticated" TO "supabase_realtime_admin"
  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
GRANT "service_role" TO "supabase_realtime_admin"
  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
```

The helper proposes and applies these statements only through the local restore
adapter after the clone has passed its network-none/tmpfs/image checks. It never
applies them to Staging or Production. The role SQL is retained encrypted as
evidence, but is not replayed blindly: source roles and memberships are checked
against the target, and only the exact statements above can be run. The initial
image's extra `pg_graphql` is discarded when the default database is dropped;
the source archive recreates `postgres` from `template0` and must install the
exact source `vector` version. Any other extension, role, membership, schema,
owner, ACL, or version difference fails closed. After the destructive local
step begins, any failure leaves the target isolated and quarantined for review;
there is no automatic cleanup or retry.

## Acceptance gates

1. Verify the encrypted artifact and HMAC sidecar before decryption.
2. Verify exact extension versions/dependencies and role inventory before
   restore; apply only the documented clone-only role and image-extension
   normalizations.
3. Restore ownership and ACLs without `--no-owner` or `--no-privileges`.
4. Compare source and clone catalog manifests and counts for every non-system
   table, including Auth, before applying migrations.
5. Require exact ACL, RLS/policy, Auth trigger, `SECURITY DEFINER`, function
   `search_path`, extension, and migration-history parity. Any unexplained
   difference blocks the rehearsal.
6. Exercise signed GDPR replay with synthetic evidence only; keep the clone
   quarantined. This does not certify deletion at external providers or
   reconcile real post-backup erasures.
7. Apply migrations 060–093 to the clone in order, then run the authorized
   compatibility and booking tests. This document does not authorize any
   Staging or Production write.
