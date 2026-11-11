# C2.4 encrypted backup drill

The Python drill is disabled until `BACKUP_DRILL_ENABLED=true`. It accepts an
explicit source URL, a separate target URL and a 32-byte encryption key from the
operator secret store. The environment names are `BACKUP_DRILL_SOURCE_DSN`,
`BACKUP_DRILL_TARGET_DSN`, and `BACKUP_DRILL_ENCRYPTION_KEY` (URL-safe base64).
Do not print, commit or place these values in command arguments. The default
backup directory is `backups`; `BACKUP_DRILL_DIR` selects a private directory.
On POSIX that directory must be owned by the process and inaccessible to group
and other users. On other platforms, configure equivalent operating-system ACLs.

Before enabling the drill, provision a unique database named
`melpis_restore_drill_<16–32 lowercase hex characters>`. It must have no tenant
objects or data, deny PUBLIC and other roles connection, creation and temporary
privileges, and be owned by a dedicated unprivileged login with no role
memberships. There must be no other client connections. The source and target
must resolve to distinct database names and identities. A database administrator
may preinstall only pgcrypto, vector and btree_gist in the same versions and
schemas as the source. The drill checks both extension inventory and other
user-created objects before invoking PostgreSQL tools. It never runs
`pg_restore --clean` or removes a pre-existing target.

`pg_dump` reads an exported repeatable-read snapshot. Its custom-format stream
is encrypted directly to an exclusive new file in authenticated AES-GCM chunks;
plaintext dump files are not created. The full archive is authenticated before
restore, then authenticated again while streaming to `pg_restore`. A failed run
may leave its newly created encrypted file or a partially populated isolated
target for investigation; do not reuse that target. Credentials are passed in a
restricted process environment and PostgreSQL tool diagnostics are suppressed.

The result compares the table inventory and row counts, extension versions and
large-object counts, and always reports `traffic_authorized=false`,
`erasure_reconciliation=required`, and `external_copies_certified=false`.
This is an isolated logical-restore drill, not authorization to serve restored
data. Ownership, privileges, comments and security labels are intentionally not
restored by the drill; verify and apply the approved deployment configuration
separately. A complete restore requires an independently held authenticated
post-backup erasure journal, replay of later deletions and stay purge fences,
verification of all affected tenants, external copies and security settings, and
a separate operational go/no-go. Missing or uncertain evidence keeps the target
quarantined.

The approved backup maximum is 30 days unless a documented obligation applies.
This drill does not inventory or delete existing backup objects, establish their
rotation, or authorize deletion of a backup. Verify storage encryption, access,
retention and rotation during the separately authorized cutover.
