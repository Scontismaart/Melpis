# Independent privacy foundation: compatibility contract

Base main9d7c5de4db088fa7c46edf7e96cdfe14c732d3d7 has migrations through069.
This foundation contains no SQL migration, C2.4 query, new grant or policy seeding.
Product standards for future categories are metadata; they do not imply those
stores exist. The existing main message-retention worker checks its supported
90-day cap on enumeration and the latest record under the existing tenant lock.
Unknown simulator schedules fail closed. Export expiry remains pre-existing;
primary destructive execution remains off and needs effective policy plus GO.

## Verification required before the authorized normal merge

- New foundation code against main schema through069 on PostgreSQL16 and18:
  metadata/route, message90/91, latest-policy race, tenant isolation, hold/expiry,
  flag-off behavior and unsupported simulator schedule, no missing-relation error.
- Old main code against the additive future SQL070-072 schema on both versions,
  with primary destructive flags disabled: representative read/write flows,
  ACL/migration checks and no new privileges. This is compatibility evidence,
  not certification of enabled retention or safe cutover.
- Full CI, full-history Gitleaks, unchanged audit, four no-push API/web builds,
  independent review; after normal merge, exact-main CI and actual GHCR digests
  and amd64/arm64 manifests. No deploy or remote migration.

After integration, forward-merge main into #153 and subsequent branches without
force-push. Resolve shared files exactly once and preserve schema-dependent
C2.4 additions in their own diffs. #158 must be reduced to residual adaptations,
not merged a second time as another copy of the foundation.

The health free-text capability, actual retention/lifecycle completeness,
worker authority, old/new writer coordination and real provider/backup evidence
are separate unresolved technical or operational gates. No health activation,
provider call or customer-data change is authorized by foundation integration.
