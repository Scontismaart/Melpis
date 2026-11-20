# A06 acceptance evidence

Baseline [4631cc1](https://github.com/Scontismaart/Melpis/commit/4631cc193c1b27b3f1ea7ea06649a9e5cd32de7c).
The approved product decision is [Privacy V1.0](../governance/privacy-data-governance-v1.0.md).
The issue remains open. This record tracks implementation and proof separately.

| Requirement | Evidence on the baseline or candidate | Remaining acceptance work |
| --- | --- | --- |
| Inventory and export completeness | [A06 inventory](a06-data-inventory.md), `catalog.py`, `test_gdpr_catalogue.py`, `test_exports_a06.py`. The catalogue includes bounded organization metadata for allocation, catalogue and document-index receipts, and declares private hold, stay and worker evidence exclusions. | Verify active and historical external stores. Classify remaining inbound-worker receipts and scope authorized supplements for private categories. Resolve unstructured subject attribution and the shared Auth workflow without exporting unrelated people or secret material. |
| Scoped export and revocation | [#114](https://github.com/Scontismaart/Melpis/pull/114), [#130](https://github.com/Scontismaart/Melpis/pull/130), [#208](https://github.com/Scontismaart/Melpis/pull/208). Scoped owner and MFA, account/session/member validation, recheck after preflight, tenant/subject constraints. | Verify proxy and actual application principal on Staging after authorized release. Concurrent revocation after bytes leave the server cannot recall those bytes. |
| One-time token, URLs/logs, Redis failure | Encrypted hashed cookie capability with Redis/Valkey atomic consume, no URL bearer and no memory fallback. `test_gdpr_authorization_boundary.py`, `test_token_store.py`. #114 records a real Valkey concurrent consume proof. | Repeat on deployed settings, including log and proxy redaction. |
| Snapshot expiration | [#210](https://github.com/Scontismaart/Melpis/pull/210) adds an independent bounded expiry job with skip-locked batches and overdue-age monitoring. [#213](https://github.com/Scontismaart/Melpis/pull/213) releases the snapshot lock between chunks so revocation and expiry can interrupt stalled downloads. | Observe physical deletion lag under the deployed workload, contention, interrupted runs and outstanding batches. |
| Erasure idempotency, retry and uncertainty | #130 durable operation/barrier/step/event model, fenced local advancement, partial manifests. [#209](https://github.com/Scontismaart/Melpis/pull/209) requires an inventoried provider step and a confirmed local erasure before claim or reconciliation. | Resolve verified held copies, dead-letter and pending provider evidence. Finish the scoped unstructured, Auth and worker cache workflows. |
| Provider propagation and honest completion | `operations.py`, `propagation.py`, `test_propagation_a06.py`. External stages retain uncertainty without correlated evidence. Historical provider review stays held. | Current and historical provider inventory, authority, supported deletion endpoints and authenticated receipts for applicable vendors, plus controller disposition for copies retained by obligation. No real provider deletion is certified. |
| 60-day visibility | Ordinary inbox/dashboard SQL filters verified receipt time, with tests in `test_retention_visibility_dark.py` and `test_c24_visibility.py`. | Confirm all ordinary surfaces and effective version in Staging. Unknown historical receipt dates remain hidden where the verified anchor is required. |
| 90-day primary message content | SQL096/104 and `test_c24_retention_sweep.py` purge eligible source content and linked copies while preserving accounting. SQL110 distinguishes completed account-channel work from an unknown inbound copy using the same-tenant current A04 ledger epoch, exact claim and payload binding, and an unambiguous response identity. | Prove coverage and deadlines for ordinary legacy rows and billed records without correlated receipt lineage. Unknown or stale response provenance, tenant-wide drains and unresolved provider usage can defer eligible content. Account-channel content has its own retention obligations. |
| C2.4 30-day last pertinent activity | SQL089 idle expiry uses verified last applied inbound source, protects newer unresolved work and invalidates typed state/caches. The owner-only orphan planner identifies a known-source candidate using current policy, exact matching committed billing evidence and fresh hold checks. | Add typed recovery evidence and eligible orphan expiry. Reconcile unattributed legacy copies and other relevant activity, and measure effective workload deadlines. |
| Technical receipts 90 days from verified conclusion | SQL100/103/104, [#207](https://github.com/Scontismaart/Melpis/pull/207). Verified source-derived receipt fences and audit expiry require a retained checkpoint. | Classify idle watermarks, revocations, global idempotency and other receipts. Prove safe expiry and replay resistance for each; unknown dates are not backfilled. |
| Bookings and stays within 12 civil months | Hotel has evidence-backed purge in SQL091/102. Candidate SQL106/108/109 records completion, plans and transactionally purges an independently covered, narrowly scoped manual managed booking with typed replay fences. The isolated encrypted v4 restore drill replays the exact signed plan and checks the SQL postconditions. | Extend coverage beyond the manual managed subset; reconcile historical completion, unstructured copies, source/financial/provider-linked bookings and operational authorization using contemporaneous evidence. |
| Legal holds | SQL074/095 append-only scoped approvals, reviews and releases with explicit rationale, authority and review date. SQL107 applies scoped holds to local erasure. `test_scoped_erasure_holds.py`, `test_restore_policy_state.py`. | Controller must resolve unknown scope, overdue reviews and conflicts between current holds and prior deletion; provider decisions require their own authorized handling. |
| Derived copies, finance and legacy jobs | SQL096 and tenant erasure handle linked event, simulation, delivery and outbox content. #183 retires global purge paths. Billing/quota regression tests and the encrypted restore drill preserve usage counters. | Trace remaining orphan, worker, provider and historical copies. Separate financial evidence from linkable content with category-specific rules; the policy defines no universal accounting, simulator or security duration. |
| Scheduler progress and contention | SQL098/102/104 bounded horizons/cursors, target lock budgets. #210 fixes a live scheduler pool self-deadlock and isolates independent sweeps. `test_c24_retention_sweep.py` verifies lock recovery and independent batches. | Measure progress under sustained work and outage, permanent blockers and restart; verify the actual dedicated runtime principal and authorized schedule. |
| Journal signer/store and independent tip | [#214](https://github.com/Scontismaart/Melpis/pull/214) adds signed and encrypted publication and independently pinned replay. [#216](https://github.com/Scontismaart/Melpis/pull/216) reconciles source coverage. [#217](https://github.com/Scontismaart/Melpis/pull/217) adds portable authority with an offline Windows-to-Linux rehearsal. [#218](https://github.com/Scontismaart/Melpis/pull/218) verifies key rotation at independently recorded boundaries. The candidate backup coverage check authenticates every referenced checkpoint and reports exact v3/v4 capacity; v4 adds ordinary-booking evidence without dropping cumulative v3 receipts or stays. | Commission current independent authority and custody against real resources. Prove compaction or a bounded successor format across every recoverable backup. A stale copy of the authority cannot establish currency. |
| R2 and backup rotation | [#207 candidate design](c24-independent-journal-design.md); separate bucket and EU endpoint adapter. The issue records encrypted Staging baseline backup with an independent R2 EU copy and DPAPI-independent recovery. | Confirm ordinary backup max 30 days and obligations over actual inventory, local and provider. Real journal bucket/access/lock/usage and synthetic byte-level R2 drill require explicit GO after prerequisites. |
| Restore with later erasure and retention | `test_c24_signed_restore.py`: real encrypted PostgreSQL synthetic backup, restore before an operation exists, cross-tenant preservation, authenticated published replay, policy reconciliation, fence-expiry replay and no traffic authorization. | Exercise all remaining ordinary data categories, fully independent host/custody and actual provider storage. Keep quarantine for incomplete scope/hold/journal, or when a backup lacks necessary evidence. |
| Staging security review | #208 and SQL105 address two trigger-only client grants. Read-only advisor review found no client table or column grants on 35 RLS-enabled, policy-less backend relations. The authenticated self-only access helper remains necessary for RLS. Dedicated roles lack login/inherit/superuser/bypass privileges on the baseline. | Verify SQL105+ after authorized migration and the deployed application's non-owner/non-escalating role. Public `btree_gist` extension warning and plan-dependent leaked-password setting are tracked separately from the trigger correction. |

The independent journal and restore increments have these integration points:

| Change | Pull request | `main` integration | Verification |
| --- | --- | --- | --- |
| Backup-inventory coverage and retention review | [#219](https://github.com/Scontismaart/Melpis/pull/219) | `d22bc7f` | [PR CI](https://github.com/Scontismaart/Melpis/actions/runs/44727021885) |
| Verified ordinary-booking preflight | [#220](https://github.com/Scontismaart/Melpis/pull/220) | `b7ea79a` | [PR CI](https://github.com/Scontismaart/Melpis/actions/runs/44728208935), [migration check](https://github.com/Scontismaart/Melpis/actions/runs/44728209154) |
| Source checkpoints, signed stay and receipt replay | [#221](https://github.com/Scontismaart/Melpis/pull/221) | `e782395` | [PR CI](https://github.com/Scontismaart/Melpis/actions/runs/44728293048) |
| Narrow ordinary-booking purge and exact v4 replay | [#222](https://github.com/Scontismaart/Melpis/pull/222) | `0b0f407` | [PR CI](https://github.com/Scontismaart/Melpis/actions/runs/44730012030), [migration check](https://github.com/Scontismaart/Melpis/actions/runs/44730012099), isolated encrypted PostgreSQL restore |
| Authenticated v5 candidates and bounded segment read | [#223](https://github.com/Scontismaart/Melpis/pull/223) | `f66fb7b` | [PR CI](https://github.com/Scontismaart/Melpis/actions/runs/44730657164), 18,500-record codec round-trip |
| EU R2 operator SDK adapter | [#224](https://github.com/Scontismaart/Melpis/pull/224) | `685ecf7` | [PR CI](https://github.com/Scontismaart/Melpis/actions/runs/44730753228), 12 offline SDK checks |

PRs #225–#229 passed their PR checks before integration. Each `main` run
completed validation and published both API and web multi-architecture GHCR
manifests. These five merges changed no Migration Check trigger paths. Its
latest applicable run passed migration 109. [Migration Check](https://github.com/Scontismaart/Melpis/actions/runs/44738927995)

| Change | Pull request | `main` integration | Main CI and GHCR |
| --- | --- | --- | --- |
| v5 authority preflight | [#225](https://github.com/Scontismaart/Melpis/pull/225) | `72c32ab8` | [Run](https://github.com/Scontismaart/Melpis/actions/runs/44752691895) |
| Orphan proposal evidence | [#226](https://github.com/Scontismaart/Melpis/pull/226) | `e8072fb9` | [Run](https://github.com/Scontismaart/Melpis/actions/runs/44752869338) |
| Complete source reconciliation | [#227](https://github.com/Scontismaart/Melpis/pull/227) | `98702807` | [Run](https://github.com/Scontismaart/Melpis/actions/runs/44752969317) |
| Paged backup coverage and restore | [#228](https://github.com/Scontismaart/Melpis/pull/228) | `aba5e8f0` | [Run](https://github.com/Scontismaart/Melpis/actions/runs/44753147296) |
| v5 candidate capture and quarantine verification | [#229](https://github.com/Scontismaart/Melpis/pull/229) | `42cdea7c` | [Run](https://github.com/Scontismaart/Melpis/actions/runs/44753184709) |

The optional coherent-snapshot proof in `test_backup_restore_a06.py` passed
locally on 20 November 2026 using disposable PostgreSQL 16 with pgvector.
Opt-in requires `GDPR_BACKUP_RESTORE_TEST=1` and
`GDPR_BACKUP_RESTORE_CONTAINER=melpis-a06-coherence-20261120`. The fixture
checks the dedicated image and loopback binding before creating a fresh
synthetic source. It ignores the inherited test DSN and removes only the
source it created. The run retained the synthetic restored database
`melpis_a06_restore_114cc900c5dc4a27` and verified the erasure barrier,
billing count and ledger, and decryptable snapshot. A run without the opt-in
skipped before database setup, including with an invalid inherited test DSN.
The signed older-backup replay drills provide the separate evidence for
operations committed after the backup.

The expanded synthetic PostgreSQL 16 and Valkey run enabled both sets of
encrypted restore drills and reported 315 passed, zero skipped. The separate
v5 capacity drill modeled five organizations and 30 daily backups, with 150
tenant/backup pins and 180 erasures across 30 segments. It measured 2,283,960
bytes in 630 encrypted store objects and a 213,092-byte sealed independent
authority. Local promotion took about 3.7 seconds and replacement-authority
recovery with coverage verification about 1.3 seconds. These figures measure
the stated synthetic load on a local filesystem. The oldest backup in that
drill is at genesis. Separate archive tests exercise prefix compaction.

The original [issue #91](https://github.com/Scontismaart/Melpis/issues/91) also records prior
integration and Staging migration 104 evidence. Record CI, Migration Check, commit,
post-merge and published image results against each finished PR before closing the issue.
