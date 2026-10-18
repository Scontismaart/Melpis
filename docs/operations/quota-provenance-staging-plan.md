# Quota provenance: migration 060

Status: local implementation; remote execution is not authorized by the earlier
058/059 approval. This change does not certify A04 or enable a new billing flow.

## Reason and scope

The current inbound claim increments the period counter before AI authorization.
A strict `used >= limit` gate can reject the already admitted 150th message.
An admission exception requires evidence written exclusively by the backend.
Neither a client-editable `messages.billed_at` nor historical usage metadata is
sufficient evidence.

Read-only inspection of **Melpis Staging** (`ucncxwnhcdrqonknkvau`) found direct
client write grants on `public.messages` and `public.usage_events`, with member
policies for all operations. No tenant data was read. Existing organization
policies do not grant client writes to billing entitlements. No Production
project was queried.

Migration 060 touches only those two tables and one reserved protocol record:

- Revoke direct client/PUBLIC table and column writes, including TRUNCATE and
  PostgreSQL 17 MAINTAIN. Keep existing table/column SELECT and grant options.
- Check effective client privileges, including inheritance. Abort if any write
  remains; do not change role membership or use CASCADE.
- Compare effective owner/service_role write capabilities before and after.
  Abort if any backend capability changes. Preserve existing RLS and policies.
- Insert or rotate `usage_events` ID
  `6206d423-cea9-5480-bb9b-59d757503f14`: NULL organization, type
  `system.quota_trust_epoch`, quantity zero, protocol version one and a freshly
  generated UUID epoch. A conflicting unrecognized record aborts the transaction.
  Other historical rows are not changed or deleted.

Reapplication deliberately rotates the epoch, invalidating old admission proofs.
The ACL operation is repeatable; the control record is deliberately not stable.
The marker is not a credential or billable event. Existing billing summaries and
accounting resolution queries are organization-scoped; retention does not delete
usage_events. No usage_events SQL trigger was found in the repository or in the
read-only Staging catalog inspection. The existing messages event-log trigger
is untouched; migration 060 does not insert or update messages.

Read-only catalog inspection found no SECURITY DEFINER function body referencing
either source. The only client-executable, non-extension SECURITY DEFINER function
in public is the read-only `current_auth_access_allowed` helper. This is focused
catalog evidence, not a general certification of every external integration.

The application outbox is also an indirect writer of usage_events. Staging has
RLS enabled on governance_outbox with only the deny-client policy. Generic usage
writers and the drain now reject the two reserved quota protocol event types,
so an arbitrary queued ID/payload cannot become an authorization record. This
namespace check neither drains nor deletes existing queued events on rejection.

The inactive loader verifies effective ACLs, the exact epoch, tenant/message,
the original claim nonce, current message state and the matching billing period
in a database transaction. A cached or sent response cannot authorize fresh AI
generation. A replacement worker's claim nonce cannot be adopted by an old
worker. No entry point is activated by this package.

The global marker SELECT uses a literal UUID, NULL organization, fixed protocol
type and zero quantity. Its reviewed exception uses the tenant checker's existing
function-plus-SQL-fingerprint mechanism. Other statements in the same function
remain checked; changing the selector, scope, type, quantity or decorator must
fail the checker. Runtime database tests also reject a substitute marker row.

## Required before remote execution

1. Review the exact SQL commit, local PG16/PG17 tests and CI. Recheck the target
   project ID, ACLs, policies, triggers and relevant privileged RPCs for drift.
2. Preserve an encrypted, fresh Staging backup plus original table/column ACL and
   role metadata; verify restoration in an isolated private database. Retain all
   existing backups. No Production access, credentials changes or service deploy.
3. Obtain separate founder approval covering the two table ACL changes and the
   single protocol control insert/update, including epoch rotation.
4. Execute the single atomic DO statement on Staging with a bounded lock wait.
   A lock timeout, inherited client write, backend permission change, dependent
   grant or unknown marker collision must abort. Do not repair it with CASCADE.
5. Verify effective grants, unchanged SELECT/RLS/backend writes and marker shape.
   Run synthetic tenant application checks through the backend and confirm
   direct client writes fail. Do not read or mutate existing tenant records.

## Rollback and activation

Any SQL failure rolls back all ACL and marker changes together. No partial change
should be accepted. If post-commit checks fail, leave admission activation off;
retain evidence and the encrypted backup, then request a specific recovery
decision. Restoring broad client write grants weakens the security boundary and
must not happen automatically. Preserve the exact original ACL snapshot for a
reviewed recovery transaction; do not guess new grants.

Future admission issuance must require both the current trusted epoch and
effective server-only provenance. A marker alone does not prove current ACLs.
Old billed messages or unverified historical ledger rows must fail closed.
Before activation, test 149/150 admission, 151st denial, retry/replay, tenant and
period changes, lease expiry, cancellation and accounting blocks. Proof validation
does not replace these entitlement checks.

This prepares a compatibility prerequisite. Commercial reserve/commit/release,
final guardrailed response counting, all-channel fencing and full A04 acceptance
remain open. No new monthly allowance, extra trial day or paid conversion is
introduced.
