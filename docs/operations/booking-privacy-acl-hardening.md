# Booking privacy boundary — candidate 067

Baseline reviewed: `7bb6b04262edd4a20d54827e020a95f21b23f23e`.
This change is implementation only. No remote ACL inspection, migration,
activation, provider call, legal DPA decision or merge is certified here.

The founder's extension authorizes correcting the pre-existing lost-update race
in `ExternalBookingRepository.update_mode`, with real concurrent regressions.
No new MFA permission, provider capability or legal policy is introduced.

## Current and intended access

| Principal / path | Before 067, when client grants exist | After 067 |
| --- | --- | --- |
| anon | Table grants may exist; membership RLS normally excludes rows | No table/column reads or writes |
| authenticated staff / manager | Membership policy permits own-tenant rows, including encrypted credentials | No direct reads or writes |
| authenticated owner, aal1 or aal2 | Same SQL membership policy; SQL does not enforce API MFA | No direct reads or writes, regardless of MFA |
| Backend table owner | Existing CRUD through tenant-scoped repository queries | Existing capabilities preserved |
| Existing service_role | Depends on actual SQL grants and role attributes | Existing capabilities preserved; unexpected row-access contract aborts migration |
| Owner + MFA through POST/DELETE API | Authorized tenant-derived configuration / disconnect | Preserved; credentials never returned |
| Owner / manager through mode PATCH | Whole-config overwrite can restore a stale DPA flag | Atomic mode-only JSON patch; current-provider restrictions enforced in SQL |
| Staff / manager / owner through status GET | Sanitized integration status | Preserved; no credential blob |

The pre-existing 063 GDPR write trigger also matters: on the disposable database,
a staff UPDATE under the old membership policy was rejected because the client
could not read `gdpr_erasure_barriers`. A SELECT of its own credential row still
succeeded. Tests preserve this trigger and its privileges. They do not grant
clients access to private GDPR tables or claim a successful baseline DPA UPDATE.
Neither remote exposure nor an actual disclosure is established.

067 provides an explicit table boundary independent of that incidental trigger.
It revokes all table and column privileges from PUBLIC, anon and authenticated,
removes the membership policy, and adds a restrictive client-deny policy. Even
an accidental later CRUD regrant plus a permissive policy cannot override it.
RLS does not protect TRUNCATE/REFERENCES: those privileges are also revoked.

Unexpected inherited privileges, membership/SET ROLE paths to privileged roles,
or a backend capability change cause the entire migration to fail atomically.
It does not revoke unrelated role memberships or change schema-wide grants,
default privileges, other tables, or the `external_booking_sync` protocol.
No new backend privilege or privileged RPC is introduced.

## Inventory and compatibility

- `src/api/main.py` and `src/core/scheduler.py` use asyncpg with `DATABASE_URL`;
  the DSN's deployed identity has not been inspected. Migration 045 documents
  `postgres` as the application role; CI uses postgres. Do not assume that a
  deployed DSN is `service_role` or postgres without a separately authorized
  operational preflight.
- `ExternalBookingRepository`: credential upsert/read/delete, status lookup,
  encrypted token rotation and mode-only updates. All per-tenant SQL filters on
  `organization_id`; backend privilege requires application-level tenant checks.
- `BookingAdapterRouter.resolve_adapter` reads protected configuration;
  `_persist_credential_update` and the existing sync workers retain server access.
- GDPR export/catalog and deletion existence checks read this table server-side.
- No frontend direct-table reader/writer, view, or callable SECURITY DEFINER
  function referencing this table was found in repository sources. Unknown
  deployed views/RPCs/role relationships remain an operational preflight gate.
- POST and DELETE remain owner + verified MFA. The existing mode-only PATCH
  contract remains owner/manager. Body injection cannot mutate the DPA field.
  No new MFA requirement or commercial/consent contract is introduced here.

## Atomic mode update and remaining writer boundaries

`update_mode` validates the existing enum and executes one tenant-filtered
UPDATE with `jsonb_set(config, '{mode}', to_jsonb($2::text), true)`. It does not
read the JSON into Python or rewrite other config fields, credentials, provider,
organization or activation state. Scalar, array and JSON-null configuration
raise PostgreSQL errors; no fallback silently replaces them with an empty object.
Missing rows are not created.

The UPDATE predicate enforces the existing ZaK/WuBook shadow/local_only policy
against the current row, including after waiting for a concurrent writer. A
blocked/deleted row produces HTTP 409 after the route's initial read; the route
does not report false success. Successful responses use the returned provider.
PostgreSQL applies the UPDATE to the current row after a competing update and
re-evaluates its predicate: see [Read Committed](https://www.postgresql.org/docs/16/transaction-iso.html)
and [jsonb_set](https://www.postgresql.org/docs/16/functions-json.html).

The authorized owner+MFA POST remains a complete configuration UPSERT. An owner
who subsequently submits a complete configuration legitimately replaces it;
this change does not add optimistic versioning to owner saves or promise that
every configuration race is solved.

The unchanged token-refresh writer updates only `credentials_encrypted` and
`updated_at`, never DPA/config/provider/is_active. Its callback organization is
bound by the server. It still has a pre-existing envelope read/merge/write race
with owner credential replacement or provider change. Independent review found
no demonstrated additional significant authorization/confidentiality bypass in
that inspected chain, but credential integrity is not certified under that race.
Track and review it separately before operational use of affected adapters;
do not label all credential concurrency solved or restrict possible impact to
availability alone. No change to that writer is included here.

## Python minimization

The canonical medical code `studio_medico_dentista` and existing medical aliases
are minimized. Only known nonmedical sectors preserve operational notes by
default: ristorante, parrucchiere, centro_estetico, hotel_bnb and legacy hotel.
Whitespace/case normalize; unknown, missing and non-string values fail closed.
Only original boolean `True` from resolved backend configuration satisfies the
existing DPA flag; strings, numbers, malformed values and missing fields do not.
A missing resolved flag cannot inherit a caller's `True` default. Tests exercise
both SHADOW and AUTHORITATIVE with synthetic adapters and unchanged input objects.
Raw flag/vertical values are no longer included in minimization logs.

This technical flag does not prove GDPR compliance or authorize indiscriminate
medical data transfer. A06/C05 remain open gates.

## Verification

Focused local tests cover actual SQL denial for anon and authenticated
staff/manager/owner, both MFA levels; table/column grants; PUBLIC and inherited
privileges; atomic abort/reapplication; unchanged other-table ACLs; restrictive
RLS after a CRUD regrant; backend encrypted CRUD/token refresh; tenant isolation;
real route dependencies plus local PostgreSQL writes; synthetic adapter payloads,
replay, retry and malformed DPA values. Authentication identities are synthetic
test fixtures, not real Supabase OAuth sessions.

Earlier runs passed 132 focused tests on PostgreSQL 18 and 120 on PostgreSQL 16.
A former broader run ended with 386 passed, 10 skipped and 78 database setup
errors during local database unavailability. It is not a green receipt. These
historical results do not certify subsequent changes; final-head evidence must
come from the new local runs and exact PR CI.

The first new full local run reached 4,068 passes and 53 skips, with one failure
in the pre-existing profile/RAG prompt test: its unmocked structure-indexing
boundary failed on the first request and succeeded on the second, changing the
retrieved context. The precise initial indexing error is not diagnosed beyond
that trace. A private empty offline model directory makes the no-provisioned-model
test environment reproducible, matching the CI runner that does not provision
model assets. All five tests in that file then pass with unchanged assertions.
No RAG implementation, timeout, authorization check or test is weakened. This
does not certify real-model indexing; the full suite is repeated in that explicit
environment, with its final receipt recorded in the PR.

Final local receipts on the unchanged source candidate:

| Check | Result |
| --- | --- |
| Complete backend, PostgreSQL 16, explicit offline/no-assets environment | 4,069 passed, 53 skipped, zero failures; 123 warnings |
| PostgreSQL 18 privacy/ACL/API/concurrency/A05/A04 matrix | 141 passed |
| Frontend | 444 passed, 3 skipped; lint/dashboard/i18n/asset checks passed |
| Python audit | 204 installed distributions verified; only 9 existing reviewed advisories in 2 packages |
| npm audit | Zero vulnerabilities |
| Python lint | Existing CI selection passed |

The 53 local skips comprise 33 live-provider opt-in cases, 2 PostgreSQL 17+
MAINTAIN cases, the separately opted-in backup/restore proof, and 17 unavailable
local fixture/platform cases (signed C04 fixture, Valkey, WeasyPrint and POSIX).
They are not counted as passed. CI must exercise its own complete configured
fixture and all four API/web architecture builds on the exact commit.

The deterministic negative control loads the baseline repository into a separate
process: the real owner+MFA-route/concurrent-manager test fails because the old
implementation restores `medical_dpa_signed: true` and loses another owner field.
The same test uses identical assertions on the candidate. Other tests coordinate
real PostgreSQL blockers via `pg_blocking_pids`, events and bounded timeouts;
there are no arbitrary time sleeps. They cover both orders, tenant separation,
provider changes, malformed configs, retries and unchanged credential ciphertext.

Negative control reloads the baseline router into a separate test process;
the focused regressions fail on the old implementation. No production file or
test expectation is weakened to obtain this result.

## Operational cutover and rollback — not executed

1. Obtain separate GO for remote metadata preflight: actual DSN role, effective
   table/column grants (including inherited/SET ROLE paths), RLS, views and RPCs.
   Check backend owner/BYPASSRLS contract and all callers before applying 067.
2. Record metadata snapshots privately without credential/config contents.
   Require approved encrypted backup and verified restore under C01.
3. Drain configuration/token-refresh writers, bound lock wait, and apply 067 in
   an explicit transaction. Review role/capability exceptions; do not bypass them.
4. Verify actual client denials and authorized backend operations with synthetic
   records, tenant isolation, mode-only edits and sanitized status. Resume only
   after successful checks. Do not rewrite tenant DPA values.
5. Before commit, any error rolls back the entire migration. After commit,
   prefer a reviewed forward repair or authorized backend DSN correction;
   reverting Python alone must not reopen client grants or restore the broad
   membership policy. Restoring old ACLs requires a separate security decision
   and containment plan. Never use a broad GRANT ALL rollback.

Merge and remote application require separate approvals. C2 ADR remains isolated;
no new multisector auto-confirmation is part of this change. Issues #90, #91 and
#100 remain open; no operational or legal gate is closed by this implementation.
