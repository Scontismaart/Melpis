# C2.4 private booking worker identity

The private confirmation worker is disabled unless
`C24_PRIVATE_BOOKING_WORKER_ENABLED=true`. Enabling it requires
`C24_PRIVATE_BOOKING_DATABASE_URL` and a database login that is distinct from
`DATABASE_URL` and is exactly `melpis_c24_booking_worker`. The API startup
validates the identity, the required schema, the bounded table grants, the
absence of extra role memberships, and the separation from the application
login before it starts the worker pool. Both connections must also report the
same immutable per-database identity marker and PostgreSQL database OID. A
worker DSN pointed at another database fails closed even when it uses the
expected login. A missing or unsuitable identity fails closed.

Before migration `086_private_booking_worker_role.sql`, a database superuser
must provision `melpis_c24_booking_worker` with the exact login attributes and
no password or role memberships. PostgreSQL requires a superuser to create a
`BYPASSRLS` role; a `CREATEROLE` migration user cannot create or repair it
safely. Migration 086 verifies role attributes and memberships, but cannot
read the password verifier as the migration user; the C01 superuser preflight
must verify that no password is set without displaying the verifier. The
migration then grants access only to the booking evidence, policy, allocation,
confirmation, and message-delivery tables used by this worker. It does not
create or alter the role, set credentials, or grant access to catalogs, billing,
membership, credentials, or other product domains. `BYPASSRLS` is confined to
this non-inheriting backend login;
the worker's tenant-scoped queries and authorization checks remain mandatory
and the readiness check rejects other table or security-definer privileges.
PostgreSQL row-lock privileges are granted only at column scope for protected
parent/evidence tables, and an enabled trigger rejects all worker `UPDATE`s on
those tables. Tests verify row locks still work and primary-key mutation is
denied. The immutable database identity marker is readable by the worker but
cannot be updated or deleted.
The capability function that permits fenced sector admission is the only
security-definer function allowed to this identity.

The database credential must be provisioned in a protected secret store after
the migration has been reviewed and applied in an authorized environment. No
credential is stored in the repository. The worker remains off in all
operational environments until that provisioning, the migration, and the
separate C2.4 activation approval are complete. This repository change does
not apply a remote migration or enable the worker.
