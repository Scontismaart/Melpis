# A06 acceptance evidence

Baseline [4631cc1](https://github.com/Scontismaart/Melpis/commit/4631cc193c1b27b3f1ea7ea06649a9e5cd32de7c).
The approved product decision is [Privacy V1.0](../governance/privacy-data-governance-v1.0.md).
The issue remains open. This record tracks implementation and proof separately.
The [Staging SQL115 postflight and updated issue matrix](https://github.com/Scontismaart/Melpis/issues/91#a06-staging-115-postflight)
supersede the pending commissioning statements in the historical evidence below.

### Final technical acceptance candidate

- The Staging cutover committed SQL105–115 and exact enrollment of 42
  historical bookings in one transaction. Independent postflight confirmed
  61 migration records, the preserved 18 messages including three billed
  messages, unchanged usage/accounting fingerprints, and tenant-scoped
  visibility. The next booking review is due 21 December 2026.
- The protected main revision was `142ce6d0108e19612f38fb8592b8c922eb63f43b`.
  [Main CI](https://github.com/Scontismaart/Melpis/actions/runs/44923853294)
  and [Migration Check](https://github.com/Scontismaart/Melpis/actions/runs/44923853290)
  passed. The published API and web multi-architecture image configurations
  matched that revision. The new encrypted C01 backup was authenticated
  from R2 EU using the Drive recovery envelope and a separate 1Password
  passphrase. The R2 synthetic v5 publication/replay and independent custody
  receipts are recorded in the [operational evidence](https://github.com/Scontismaart/Melpis/issues/91#issuecomment-7065451594).
- The final export candidate excludes uncertain message derivatives from
  organization exports, requires the immutable subject anchor for contact
  request metadata, makes omissions and oversized records visible in the
  manifest, and invalidates snapshots produced by earlier export formats.
  The controller review path continues to require exact evidence; unknown
  timestamps and subject links remain withheld. Individual exports exclude
  unstructured text absent a safe subject attribution.
- The provider candidate refreshes coverage on claim and transitions expired
  claims to uncertainty before authenticated receipt lookup. No timer or
  expired lease authorizes another delete. Local simulated provider tests
  cover known unsent retries, uncertain sends, dead letters and correlated
  receipts. The lifecycle owner can record a separately verified exact
  inventory review, including previously undiscovered provider stages,
  without claiming those copies were deleted. Active-provider specific
  adapters and authorization remain deployment-dependent.
- The backup candidate detects overdue ordinary backups at the exact 30-day
  boundary. The seven-day ordinary backup rotation validates the full
  expired-object listing and confirms exact remote ciphertext and manifest
  readback before deleting old objects. A failed inventory or a missing
  replacement is an error. Independent journal inventory retirement still
  requires its signed exact-unrecoverable evidence before advancing the
  authoritative coverage floor. The deployed backup job and monitoring
  remain subject to the operational gate.
- The bounded ordinary-booking owner selection advances across examined
  evidence and returns only eligible exact plans. The isolated encrypted
  restore test uses that selection as input to the existing authenticated
  checkpoint and inventory-covered source purge, then replays it under v5.
  The old global webhook dedup age-only deletion and the legacy completed
  inbox hard-delete method are explicitly retired. The scoped C2.4 sweep
  retains the completed inbox receipt and scrubs its payload; a synthetic
  retry exercises the actual repository and verifies that the receipt is
  not re-enqueued. Creation time is not verified conclusion. Other global receipts
  still require an independently supported conclusion and safe replay
  disposition; no arbitrary time or tenant attribution is inferred.

### Subsequent local lifecycle extensions (not commissioned)

- SQL116 extends the ordinary booking plan only to a Dashboard booking with
  no source message, human allocation receipts and the exact conversation
  identity captured at completion. The new typed candidate binds that
  conversation in the existing v5 envelope; previous source-less plans are
  unchanged. Quarantine, holds, provider/financial/worker dependencies and
  exact replay fences remain fail-closed. PostgreSQL 17 passed 34 focused
  plan, purge and codec tests and the signed encrypted older-backup v5 replay
  with the linked conversation preserved. This does not infer historical
  completion or authorize any source-linked AI or external-provider purge.
- SQL117 replaces the global webhook receipt tuple with its deterministic
  length-framed SHA-256 digest, preserving exact duplicate suppression and
  historical creation timestamps without storing raw provider identifiers or
  status text. The digest is pseudonymized, not anonymous; it is the minimum
  necessary replay proof until a verified conclusion and safe replay horizon
  are established. A 90-day age-only deletion remains prohibited. Stripe
  event receipts stay in the separate accounting category subject to the
  specific documented obligation, not the ordinary technical-receipt clock.
  Thirteen focused PostgreSQL 16 and worker tests passed, including legacy
  upgrade, repeated migration, duplicate suppression, transactional rollback
  and failed writes on incompatible application/schema pairings. A release
  requires coordinated worker quiescence and migration before restarting
  ingress; neither new migration has been applied to Staging.
- A genuinely source-linked ordinary booking needs the exact source-message
  and worker/copy disposition, independently verified on every recoverable
  backup or reproducible by authenticated replay. The inventory manifest and
  a checkpoint captured from the current source alone do not prove the
  actual preimages in older backup images. The existing narrow purge must
  not be widened for this category until that recovery condition is met.
  Financial and provider-linked bookings also need separate evidence of the
  accounting and provider disposition. These technical limitations remain
  in A06; actual provider commissioning is tracked separately.

| Requirement | Evidence on the baseline or candidate | Remaining acceptance work |
| --- | --- | --- |
| Inventory and export completeness | [A06 inventory](a06-data-inventory.md), `catalog.py`, `test_gdpr_catalogue.py`, `test_exports_a06.py`. #240 covers exact tenant-staff author feedback metadata in account exports. SQL113 adds a bounded, operation/requester/owner-authorized projection of verified inbound receipt dates and hotel/ordinary booking completion metadata. | Verify active and historical external stores. Controller review is required for remaining private, ambiguous or unstructured subject attribution, including worker authorization and financial records. |
| Scoped export and revocation | [#114](https://github.com/Scontismaart/Melpis/pull/114), [#130](https://github.com/Scontismaart/Melpis/pull/130), [#208](https://github.com/Scontismaart/Melpis/pull/208). Scoped owner and MFA, account/session/member validation, recheck after preflight, tenant/subject constraints. | Verify proxy and actual application principal on Staging after authorized release. Concurrent revocation after bytes leave the server cannot recall those bytes. |
| One-time token, URLs/logs, Redis failure | Encrypted hashed cookie capability with Redis/Valkey atomic consume, no URL bearer and no memory fallback. `test_gdpr_authorization_boundary.py`, `test_token_store.py`. #114 records a real Valkey concurrent consume proof. | Repeat on deployed settings, including log and proxy redaction. |
| Snapshot expiration | [#210](https://github.com/Scontismaart/Melpis/pull/210) adds an independent bounded expiry job with skip-locked batches and overdue-age monitoring. [#213](https://github.com/Scontismaart/Melpis/pull/213) releases the snapshot lock between chunks so revocation and expiry can interrupt stalled downloads. | Observe physical deletion lag under the deployed workload, contention, interrupted runs and outstanding batches. |
| Erasure idempotency, retry and uncertainty | #130 durable operation/barrier/step/event model, fenced local advancement, partial manifests. [#209](https://github.com/Scontismaart/Melpis/pull/209) requires an inventoried provider step and a confirmed local erasure before claim or reconciliation. The exact blocked-account review reports owner, shared-membership, assignment, invitation, audit, access and provider dependencies without changing identity or exposing other tenant identifiers. | Actual global account erasure requires the independent account controller's disposition, ownership transfers, session revocation, verified Auth provider receipts and backup replay contract. Unstructured subject attribution and held copies require case-specific evidence. |
| Provider propagation and honest completion | `operations.py`, `propagation.py`, `test_propagation_a06.py`. #237 caps confirmed-unsent retries and permits a separately evidenced operator recovery of a dead letter. External stages retain uncertainty without correlated evidence. Historical provider review stays held. | Current and historical provider inventory, authority, supported deletion endpoints and authenticated receipts for applicable vendors, plus controller disposition for copies retained by obligation. No real provider deletion is certified. |
| 60-day visibility | Ordinary inbox/dashboard SQL filters verified receipt time. SQL114 applies the rule to direct client reads of messages and their event and feedback copies. `test_c24_visibility.py` exercises the API projections and database authenticated role. | Confirm the effective version and actual client role in Staging. |
| 90-day primary message content | SQL096/104 and `test_c24_retention_sweep.py` purge eligible source content and linked copies while preserving accounting. SQL110 distinguishes completed account-channel work from an unknown inbound copy using the same-tenant current A04 ledger epoch, exact claim and payload binding, and an unambiguous response identity. | Prove coverage and deadlines for ordinary legacy rows and billed records without correlated receipt lineage. Unknown or stale response provenance, tenant-wide drains and unresolved provider usage can defer eligible content. Account-channel content has its own retention obligations. |
| C2.4 30-day last pertinent activity | SQL089 handles verified last applied inbound activity. SQL111 and the v5 orphan plan path expire a verified never-applied proposal from both copies, preserve message content and accounting, and leave a replay fence. The isolated restore covers a backup with the preimage, a verified backup that predates source receipt, a subsequent signed erasure, and policy renewal under current holds. | Reconcile unattributed legacy copies and other relevant activity, measure effective workload deadlines and commission the owner publication schedule. |
| Technical receipts 90 days from verified conclusion | SQL100/103/104, [#207](https://github.com/Scontismaart/Melpis/pull/207). Verified source-derived receipt fences and audit expiry require a retained checkpoint. SQL113 projects verified inbound receipt metadata to the relevant subject. | A controller must independently establish each remaining request-conclusion anchor, category and replay disposition. Idle-watermark expiry, revocation creation and global idempotency creation are different events; global dedup has no tenant scope. |
| Bookings and stays within 12 civil months | Hotel has evidence-backed purge in SQL091/102. Candidate SQL106/108/109 records completion, plans and transactionally purges an independently covered, narrowly scoped manual managed booking with typed replay fences. The isolated encrypted v4 restore drill replays the exact signed plan and checks the SQL postconditions. | Extend coverage beyond the manual managed subset; reconcile historical completion, unstructured copies, source/financial/provider-linked bookings and operational authorization using contemporaneous evidence. |
| Legal holds | SQL074/095 append-only scoped approvals, reviews and releases with explicit rationale, authority and review date. SQL107 applies scoped holds to local erasure. `test_scoped_erasure_holds.py`, `test_restore_policy_state.py`. | Controller must resolve unknown scope, overdue reviews and conflicts between current holds and prior deletion; provider decisions require their own authorized handling. |
| Derived copies, finance and legacy jobs | SQL096 and tenant erasure handle linked event, simulation, delivery and outbox content. #183 retires global purge paths. #239 and SQL112 add bounded expired unused/orphan capability payload scrubbing with retained lineage. Billing/quota regression tests and the encrypted restore drill preserve usage counters. Simulator cache synchronization checks the exact persisted tenant barrier at lookup, write and process-local reconciliation. | Verify replicas and provider calls already dispatched. The durable account response remains a separately attributed category. Financial obligations, unstructured historical copies and provider dispositions require verified evidence; the policy defines no universal accounting, simulator or security duration. |
| Scheduler progress and contention | SQL098/102/104 bounded horizons/cursors, target lock budgets. #210 fixes a live scheduler pool self-deadlock and isolates independent sweeps. `test_c24_retention_sweep.py` verifies lock recovery and independent batches. The synthetic five-tenant test checks six scheduler restarts with 180 initial eligible envelopes, 120 additional due envelopes, 120 fresh envelopes and 25 held envelopes. The initial backlog clears by the fourth invocation. Three further invocations with fresh arrivals must clear every outstanding due envelope while protecting all fresh and held payloads. | Measure deployed latency and recovery after outages across all categories; verify the actual dedicated runtime principal and authorized schedule. |
| Journal signer/store and independent tip | [#214](https://github.com/Scontismaart/Melpis/pull/214) adds signed and encrypted publication and independently pinned replay. [#216](https://github.com/Scontismaart/Melpis/pull/216) reconciles source coverage. [#217](https://github.com/Scontismaart/Melpis/pull/217) adds portable authority with an offline Windows-to-Linux rehearsal. [#218](https://github.com/Scontismaart/Melpis/pull/218) verifies key rotation at independently recorded boundaries. #231 adds atomic v5 checkpoint promotion and actual restore/replay. #232 verifies encrypted authoritative prefix compaction; #233 covers inventory rollover and checkpoint-key rotation. #238 adds bounded source-to-authority publication and retry under the source owner transaction. | Commission current independent authority and custody against real resources. Prove operational expiry/retirement and recoverability with the complete real backup inventory and verify published checkpoints after advancing the tip. A stale copy of the authority cannot establish currency. |
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
This coherence guard explains the single skip in the earlier 312-test suite.
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

An additional synthetic PostgreSQL 16 and Valkey regression, including the
GDPR suite, API access revocation, private booking worker, inbound-worker
authority and provenance tests, reported 363 passed and zero skipped in 809.87
seconds. A single HTTPX cookie deprecation warning was emitted. Both guarded
encrypted restore fixtures were enabled. The later PG17 source-publication
and orphan replay test passed separately, along with 22 focused journal tests.

The expanded integrated run on the subsequent candidate with feedback export,
bounded worker cleanup, source journal publication and synchronized simulator
cache completed with **375 passed, zero skipped** in 691.94 seconds.
Both encrypted synthetic restore fixtures remained enabled. The first two runs
of this expanded suite each identified a test issue: a synthetic private
capability persisted beyond its fixture and aged into a later test, and an
arrival behind the sweep cursor could require a further scan. Both fixture
corrections are retained; the final test asserts complete bounded catch-up
while fresh arrivals continue. Twelve focused repeated load runs passed
across PostgreSQL 16 and PostgreSQL 17.

The consolidated candidate implementing SQL112–113, exact blocked-account
review, synchronized simulator histories across API replicas and fenced
durable replay passed **409 tests, zero skipped** in 696.57 seconds on
21 November 2026. The run includes PostgreSQL 16, Valkey, both encrypted
synthetic restore fixtures, GDPR APIs, access revocation, booking and inbound
workers, document routes and simulator tests. It ran the booking-worker
suites before the GDPR suite to exercise the corrected private-schema
fixture isolation. The same HTTPX cookie deprecation warning remained.
Independent focused tests passed on PostgreSQL 17 (15) and unit/API paths
(44); the CI-order fixture regression passed 44 tests on PostgreSQL 16.

Subsequent integration evidence as of 21 November 2026:

| Pull request | Merge commit | Main CI / Migration Check |
| --- | --- | --- |
| [#230](https://github.com/Scontismaart/Melpis/pull/230) | `7cf910ae` | [CI + GHCR passed](https://github.com/Scontismaart/Melpis/actions/runs/44790755853) |
| [#231](https://github.com/Scontismaart/Melpis/pull/231) | `3148571e` | [CI + GHCR passed](https://github.com/Scontismaart/Melpis/actions/runs/44790778006) |
| [#232](https://github.com/Scontismaart/Melpis/pull/232) | `b884f91e` | [CI + GHCR passed](https://github.com/Scontismaart/Melpis/actions/runs/44796598706) |
| [#233](https://github.com/Scontismaart/Melpis/pull/233) | `1f944cb0` | [CI + GHCR passed](https://github.com/Scontismaart/Melpis/actions/runs/44801972612) |
| [#234](https://github.com/Scontismaart/Melpis/pull/234) | `93caeabb` | [CI + GHCR passed](https://github.com/Scontismaart/Melpis/actions/runs/44802334009), [Migration passed](https://github.com/Scontismaart/Melpis/actions/runs/44802333977) |
| [#235](https://github.com/Scontismaart/Melpis/pull/235) | `f9798a3a` | [CI + GHCR passed](https://github.com/Scontismaart/Melpis/actions/runs/44802353492), [Migration passed](https://github.com/Scontismaart/Melpis/actions/runs/44802352972) |
| [#236](https://github.com/Scontismaart/Melpis/pull/236) | `ab38ff81` | [CI + GHCR passed](https://github.com/Scontismaart/Melpis/actions/runs/44803696507) |
| [#237](https://github.com/Scontismaart/Melpis/pull/237) | `53a96c31` | [CI + GHCR passed on rerun after OSV read timeout](https://github.com/Scontismaart/Melpis/actions/runs/44803851044) |
| [#238](https://github.com/Scontismaart/Melpis/pull/238) | `d8f64436` | [CI + GHCR passed](https://github.com/Scontismaart/Melpis/actions/runs/44805955474) |
| [#239](https://github.com/Scontismaart/Melpis/pull/239) | `c7c59932` | [PR checks passed](https://github.com/Scontismaart/Melpis/actions/runs/44817157891), [main CI](https://github.com/Scontismaart/Melpis/actions/runs/44857793192) |
| [#240](https://github.com/Scontismaart/Melpis/pull/240) | `38614615` | [PR checks passed](https://github.com/Scontismaart/Melpis/actions/runs/44817169456), [main CI](https://github.com/Scontismaart/Melpis/actions/runs/44857806537) |
| [#241](https://github.com/Scontismaart/Melpis/pull/241) | `153a6979` | [PR checks passed](https://github.com/Scontismaart/Melpis/actions/runs/44817180784), [main CI](https://github.com/Scontismaart/Melpis/actions/runs/44857821336) |
| [#242](https://github.com/Scontismaart/Melpis/pull/242) | `9039e49c` | [PR checks passed](https://github.com/Scontismaart/Melpis/actions/runs/44859008640), [main CI and GHCR](https://github.com/Scontismaart/Melpis/actions/runs/44864127011), [main Migration Check for SQL112–113](https://github.com/Scontismaart/Melpis/actions/runs/44864127019) |

All PR checks required for the above merges passed on the exact reviewed
heads. The #239–242 main runs passed and published API and web GHCR
multi-architecture manifests. The first full CI
runs for #239–241 found a private capability fixture retained across tests
that recreate the public schema. The subsequent fixture reset clears the
private test schema at the same disposable database boundary. The production
scrub retains its exact eligibility and batch limits.

### Inputs required for remaining dispositions

- Historical messages: authenticated contemporaneous evidence of the same
  organization, source, conversation, original subject and actual server
  receipt date, plus exact billed-operation and response lineage where
  relevant. Review by the controller is required where those facts are absent.
- Technical watermarks, revocations and global idempotency: verified request
  conclusion and category, and demonstrated independent replay protection
  for every still recoverable backup. Creation and suppression timestamps
  alone cannot establish the 90-day anchor.
- AI/provider-linked and financial bookings: verified original completion and
  attribution, treatment of retained claims, invoices, usage, provider
  synchronization and redaction duties. The controller must identify the
  exact applicable obligation and minimum retained fields.
- Shared Auth and unstructured data: independent controller disposition,
  authorized account ownership and session arrangements, verified provider
  receipts, and scoped attribution of text and other derived copies.
- Operational commissioning: documented DPA applicability and account-wide
  R2 usage, dedicated EU journal bucket, distinct credentials, independent
  key and tip custody, synthetic drill, new backup and rehearsal, and
  authorized Staging migration. Recheck actual provider and backup inventory
  before certifying external copies or restoring traffic.

### Founder dispositions and Staging inventory (2026-11-21)

The founder confirmed that Staging is intended solely for reproducible synthetic
data. This defines the environment's permitted purpose. It does not establish
the provenance of pre-existing rows. The following inventory used a read-only
transaction against the explicitly identified Melpis Staging project in
`eu-central-1`. No row content, personal identifier, token or secret was
retrieved. Production was excluded.

| Aggregate result | Count |
| --- | ---: |
| Relations examined (application, private worker, selected Auth and Storage) | 101 |
| Nonempty relations | 26 |
| Organizations / joined memberships / Auth users | 25 / 21 / 28 |
| Contacts / conversations / messages | 3 / 3 / 18 |
| Messages missing both trusted receipt and original subject | 18 |
| Billed messages lacking verified lineage | 3 |
| Bookings / without a contact reference / broken contact references | 43 / 42 / 0 |
| Bookings with source message or completion timestamp | 0 / 0 |
| Auth users with more than one joined tenant membership | 0 |
| Auth sessions / refresh tokens | 130 / 157 |
| Usage events / global usage events | 28 / 7 |
| Usage records mapped to quota reservation / provider attempts / trust marker / other | 0 / 0 / 1 / 27 |
| Storage objects / provider WhatsApp account rows | 0 / 1 |

A separate read-only ACL check confirmed direct `SELECT` privileges on
messages, event log, feedback and delivery attempts for the hosted client
roles. SQL114 applies restrictive read policies to the raw messages, event
log and feedback. The existing delivery-attempt policy resolves its source
through messages and inherits the visibility restriction. Private inbound
receipt exports also require an exact current message and tenant contact
match and a nonfuture receipt timestamp.

Migration history records SQL104 as the latest applied A06 migration; SQL105+
requires the specific GO. No synthetic label, current contact link, or raw
creation date establishes provenance. The 18 messages require quarantine and
exact evidence-backed reconciliation. Contactless manual guest bookings can be
valid operational records. The 42 bookings with no contact reference and the
absence of completion evidence require a review of source, subject and disposition,
without assigning fabricated identities or deadlines. The remaining Auth
records, document, messaging integration and usage records also require an
inventory of their generating fixtures and approved disposition before any
real-data export, backup transfer or erasure. Maintain access restrictions
and a dated controller review until the cases are resolved; do not use
quarantine as an indefinite retention basis.

The financial disposition distinguishes invoices and statutory accounting
records from counters, technical receipts, conversation content and bookings.
The ordinary ten-year civil-accounting rule applies only to its verified legal
scope and anchor, with professional validation before actual use. It does not
set a generic AI, booking or conversation deadline. The product-policy registry
and existing booking/retention gates preserve financial obligations without
resetting quota or inferring an obligation from unrelated linked material.

The shared Auth disposition requires tenant-local membership revocation and
preservation of independent global identity, other tenant memberships, and
accounting. SQL114 is an additional, narrowly scoped prerequisite identified
after the founder disposition: it fences subsequent membership grants at the
existing organization barrier and denies stale erased-tenant access. The
original GO range 105–113 therefore needs an explicitly named extension
to include SQL114. The migration itself performs no historical deletion.
Existing committed cases with residual access require exact current policy,
hold recheck and execution authority. Global account deletion, ownership
changes and provider/session disposition require independently verified
authorization. No such real changes are authorized by this record.

| Founder disposition | Local candidate evidence | Precondition for operational acceptance |
| --- | --- | --- |
| Historical data | `test_c24_visibility.py` verifies normal inbox/dashboard and export quarantine for missing, mismatched or future receipt evidence, continued access to proven rows, rejection of older snapshots, and dated owner review. | The responsible controller must classify the inventoried Staging rows and establish contemporaneous evidence or a dated disposition. Use a newly seeded, reproducible dataset for the operational drill and its backup. |
| Financial data | `test_webhook_ordering.py` and `test_deletion_a06.py` verify that terminal or proven-unsent checkout URLs expire without changing accounting or idempotency and that unresolved or other-tenant intents are preserved. | Record the verified legal class and applicable retention anchor for statutory records, with professional validation of the actual accounting workflow. Reconcile unknown provider and booking records using correlated evidence. |
| Shared Auth | SQL114 and `test_tenant_access_erasure.py` verify local membership and invitation removal, refusal of regrants, retention of foreign memberships and Auth identities, and policy-gated reconciliation of stale membership links. `test_c24_signed_restore.py` replays an organization erasure through the authenticated v5 tip into an older encrypted PostgreSQL backup. | Verify deployed role behavior after migration. Global Auth decisions, session revocation and ownership transfer need verified cross-tenant dependencies and the proper authority. |
| Journal custody | Signed v5 publication, checkpoint advancement, exact replay, policy and hold reconciliation, key rotation, and archive safeguards have local synthetic coverage. | Record the applicable account agreement and DPA, scope credentials to a private R2 EU journal, prove independent custody and run the approved synthetic drill before any remote real-data processing. |

The candidate passed the local PostgreSQL 16 upgrade from 104 through 114
on a synthetic database with pre-existing organization erasure history.
Applying SQL114 again preserved the historical membership and Auth identity.
Separate exact-scope reconciliation tests verify that an erased tenant's
stale membership is inaccessible and can be removed only under current
authorization. The synthetic Windows-to-Linux authority portability drill
also recovered the independent authority and journal and rejected the old
sealed authority in a replacement container without networking.

The broad synthetic PostgreSQL 17 and Valkey run reported 586 passed, zero
skipped in 967.93 seconds. It covered booking and inbound workers, the GDPR
suite, API access, simulator and document routes, billing and journal tests,
including both encrypted restore fixtures. The new v5 organization replay
case was run separately on PostgreSQL 16. The broad run started before the
final direct client visibility and private receipt export correction. The
focused check below verifies that correction on the final candidate.

The final PostgreSQL 16 check ran the database-role and ordinary API
visibility paths, export and exact access reconciliation, plus both
encrypted restore fixtures. All 32 tests passed with zero skips, including
the signed v5 organization replay and the future-dated private receipt
regression. The only warnings were the existing HTTPX cookie deprecation.

### Conditional Staging commissioning record (2026-11-21)

| Gate | Verified evidence | Disposition |
| --- | --- | --- |
| Candidate release | [PR #243](https://github.com/Scontismaart/Melpis/pull/243). Independent review covered `0913f00a` and the exact corrected head `14de1929b474e47e5ac04ec9dc24563ac98f7b31`. The first CI secret scan flagged the deterministic SHA-256 fingerprint of a tenant-scoping query. Splitting the tuple over multiple lines preserved its exact value and kept the scanner and review controls intact. PR [CI](https://github.com/Scontismaart/Melpis/actions/runs/44892800562) and [Migration Check](https://github.com/Scontismaart/Melpis/actions/runs/44892800611) passed. | Protected merge `1ca339cd3c262121a5ac34e95ac834126dd6dcc0` has the same tree as the reviewed head. [Main CI and GHCR](https://github.com/Scontismaart/Melpis/actions/runs/44896262045) and [main Migration Check](https://github.com/Scontismaart/Melpis/actions/runs/44896262038) both passed. GHCR registry readback confirmed both `linux/amd64` and `linux/arm64` for API `sha256:3fa5077bade39d57baab46558097e6650697df68950a0bac8de1c3d32de75588` and web `sha256:a6e4c88983ba5e5999f6105a8b2eecec02c723e210fd93d44bfc9361d53ddc40`. |
| Staging drift and historical evidence | Read-only project checks reconfirmed migration 104, 18 messages missing receipt and original subject including three billed, 43 bookings including 42 contactless, and zero invalid public/private application indexes. The 18 lacked exact worker receipts and message audit references. Eight contactless bookings had an exact audit record. Seven had a manual-creation marker, six with an exact staff profile and an event within five minutes of the stored creation record. | Message source and subject remain unresolved. The booking audit records corroborate manual origination for a subset. Customer attribution, completion and synthetic provenance remain unverified. Keep the historical corpus outside any transferred backup or replay until authorized review. |
| Non-destructive upgrade and booking access | A disposable PostgreSQL 17 104-to-114 rehearsal used the same counts with wholly synthetic fixtures. An injected rollback restored the SQL104 catalogue and data fingerprints. On successful reapplication the row fingerprints, counts and billed markers still matched byte-for-byte. The authenticated role could read none of the 18 unverified messages. | The same role could still read its 42 contactless bookings in the rehearsal. Booking quarantine remains unresolved. A nullable contact is valid on new manual guest bookings. Keep Staging traffic and booking workers isolated unless exact controller-classified records can be protected without destructive changes. |
| Dedicated journal container and spend | R2 account analytics showed 32 Standard Class A and 9 Class B operations this month and a 469,043-byte Standard daily peak, within current included usage. The authorized empty `melpis-staging-gdpr-journal` bucket was created Standard with `eu` jurisdiction on 21 November. Readback verified `eu`, the disabled managed domain and an empty custom-domain list. It is separate from the backup bucket. | The journal remains empty. Operational credentials await commissioning. Recheck account-wide usage before the drill. |
| Contract and independent custody | The individual account holder confirmed personal use for Melpis and acceptance of the [Self-Serve terms](https://www.cloudflare.com/terms/) dated 12 September 2025 and the incorporated [DPA v6.4](https://www.cloudflare.com/cloudflare-customer-dpa/) effective 3 April 2026. The API confirmed a Standard account, a single administrator consistent with the declaration and active R2. See [contract evidence and transfer safeguards](c24-independent-journal-design.md#contract-and-cost-verification-2026-11-21). The custodian independently recovered synthetic keys and a sealed checkpoint from a private 1Password vault using hidden input and an offline Linux container. The local receipt `verify OK` records journal verification and rollback rejection. | Account acceptance is evidenced by the holder's declaration and observed service use. Verify actual processing scope, controller authorization where applicable and transfer safeguards before any real-data upload. Commission fresh operational keys and the independent authoritative tip separately. |
| Hosted backup, drill and migrations | Staging remains at 104. Hosted backup, data changes, scheduler and traffic release remain gated. | A fresh encrypted backup and its R2 copy require a verified source and transfer basis. Complete the independent synthetic journal drill and verify the backup and rehearsal before the authorized 105–115 cutover, exact cohort enrollment and independent postflight. |

The 21 November follow-up read-only exact scoped inventory again identified 42
contactless bookings, all within a single organization. It computed a server-side
sorted scoped-pair digest without returning individual identifiers. This binds the
observed cohort at that inspection time without asserting historical attribution.
SQL115 is a separate candidate requirement for exact non-destructive booking
quarantine after the earlier release demonstrated the remaining read path.
The candidate provides an immutable exact-pair enrollment and separately
reviewed release, a finite review deadline, direct-client and application
visibility gates, preserved occupancy and accounting, revoked stale export
snapshots and linked confirmation containment through delivery. Independent
review found and verified corrections for the raw confirmation message,
private completion RPC and generic retry path with missing handling type.
Focused synthetic PostgreSQL 16 and 17 regressions and the expanded booking,
calendar, CSV and authorization regression passed; the candidate has not been
applied to Staging. The new 115 migration and exact cohort enrollment require
an explicitly scoped extension to the existing migration GO after the other
operational gates. No remote record has been changed or deleted.

Public legal references for professional confirmation: [Italian accounting
records, Civil Code Article 2220 as reproduced in Law 772/1986](https://www.normattiva.it/atto/caricaDettaglioAtto?atto.codiceRedazionale=086U0772&atto.dataPubblicazioneGazzetta=1986-11-25&bloccoAggiornamentoBreadCrumb=true&dataVigenza=&generaTabId=true&qId=&tabID=&tipoDettaglio=originario&title=lbl.dettaglioAtto),
[electronic invoice preservation](https://agenziaentrate.gov.it/portale/web/guest/aree-tematiche/fatturazione-elettronica/guida-fatturazione-elettronica/come-predisporre-inviare-ricevere-fe/come-si-conservano-fe).

The original [issue #91](https://github.com/Scontismaart/Melpis/issues/91) also records prior
integration and Staging migration 104 evidence. Record CI, Migration Check, commit,
post-merge and published image results against each finished PR before closing the issue.
