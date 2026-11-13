# C2.4 private booking worker identity

The booking worker remains disabled unless
`C24_PRIVATE_BOOKING_WORKER_ENABLED=true`. Enabling it requires
`C24_PRIVATE_BOOKING_DATABASE_URL` and a database login that is distinct from
`DATABASE_URL` and exactly `melpis_c24_booking_worker`. Startup checks the
worker identity, database marker, PostgreSQL database OID, role attributes,
role memberships, effective ACLs and the absence of unexpected executable
`SECURITY DEFINER` functions. A missing or unsuitable identity fails closed.

Migrations 086 and 093 create/harden this identity as `NOLOGIN`, `NOINHERIT`,
`NOBYPASSRLS`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE` and
`NOREPLICATION`, with a NULL password. The worker has no ownership and no
direct table, sequence or public-schema data grants. It receives `USAGE` on
`c24_private` and a reviewed allowlist of capability functions and
tenant-filtered views. `worker_context` is inaccessible to the worker; the
security-definer helpers read only a context bound to the current backend,
transaction, tenant, conversation, source message, lease and claim. Writes are
limited to claim-bound views and are guarded again by database triggers.

On Supabase PostgreSQL 17, role creation may leave the platform administration
membership where `postgres` is the member and the grantor is `postgres` or
`supabase_admin`, with `ADMIN=true`, `SET=false` and `INHERIT=false`. Migration
086/093 accept only that exact platform edge, and reject any outgoing worker
membership, other incoming edge, owned object, unsafe role flag or effective
privilege outside the private capability surface. This edge is not treated as
worker data authority. PostgreSQL permits an administrator holding the
membership `ADMIN` option to grant the role to another principal, so readiness
rechecks membership state and every helper/view independently checks that
`session_user` is the dedicated worker. A login that is later granted the
worker role, including with `SET ROLE`, does not satisfy this identity check
and receives no tenant data or capability. Only a separately managed trusted
database administrator can change role membership; such a change is audited
and causes startup readiness to fail unless it is the exact platform edge.

The worker connection uses `search_path=pg_catalog,c24_private,pg_temp`;
security-definer functions pin their path to `pg_catalog,pg_temp` and qualify
all application objects. No `current_setting('tenant_id')` value is used as
authorization evidence. Tenant and job scope comes from persisted, committed
booking/A04 provenance and a transaction-bound capability.

PostgreSQL requires an UPDATE privilege on at least one column for `SELECT FOR
UPDATE/SHARE`. The worker therefore receives only the required column-level
lock privileges on filtered read views. A trigger rejects every actual data
UPDATE by the worker; narrow write views and their predicates are separate.
This preserves locking without making the lock grant a mutation path.

The worker is still `NOLOGIN`, has no credential, and is not enabled in
operational environments. Tests may temporarily enable a synthetic login only
inside disposable PostgreSQL databases, then restore `NOLOGIN` and NULL
password. No remote migration, credential provisioning, deploy, provider call
or worker activation is performed by this repository change.
