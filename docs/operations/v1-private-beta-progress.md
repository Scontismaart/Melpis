# Melpis V1 private beta progress register

**Intake snapshot date:** 2026-10-24
**Repository:** [Scontismaart/Melpis](https://github.com/Scontismaart/Melpis)
**Purpose:** evidence-based readiness register for the private beta. This records GitHub state and the roadmap snapshot; it does not certify remote infrastructure, providers, or production.

## 1. Executive snapshot

**Current integration snapshot:** 2026-10-24, after the founder-authorized normal merge of PR #132.

1. **Canonical issue set:** #85–#102 contains 18 issues: #85, #86, #87 and #89 are closed; the other 14 remain open. #121 is an additional open P0 blocker. No issue was closed by the B02 technical merge.
2. **Current main:** `6a26813e9a0f52d25e78da55465744e3ca798c3a`, the normal merge commit for [#132](https://github.com/Scontismaart/Melpis/pull/132). Parent main was `a7f09f2d4155dade5f93a6512fb2f5bdd4a9a5ea`. The independently reviewed and explicitly authorized head was `d141d1625175b97d3ba746c66a8ce9654cbc5f0f`.
3. **Replacement defect corrected and integrated:** pending `index_intent` preserves the previous authorized, active, verified revision until atomic publication. Failed replacement does not revoke that revision; owner revocation/deletion and stale-worker fencing cannot be undone by retry.
4. **Approved-head evidence:** [CI 40111907116](https://github.com/Scontismaart/Melpis/actions/runs/40111907116) and [Migration 40111907091](https://github.com/Scontismaart/Melpis/actions/runs/40111907091) passed on `d141d162`. Each ran 3,824 passing Python tests, 36 skipped, 124 warnings. Audit, frontend and all four API/web amd64/arm64 no-push builds passed. The 18 new replacement PostgreSQL regressions are part of these totals, not additional tests to sum.
5. **Post-merge evidence:** [CI 40114921956](https://github.com/Scontismaart/Melpis/actions/runs/40114921956) and [Migration 40114921848](https://github.com/Scontismaart/Melpis/actions/runs/40114921848) target the actual merge SHA. Both completed SUCCESS on `6a26813e`: each 3,824 passed, 36 skipped, 124 warnings. All four actual architecture publications and both multiarch manifests succeeded. The no-push build matrix was skipped as expected on main. Publication must be verified independently of build success; no deployment is authorized.
6. **B02 #93 stays open:** code integration does not certify Staging, original-byte provenance for historical rows, commercial file allowances, capacity, or original-file storage. Migrations 065/066 are source-only here; no remote migration was run.
7. **A03 remains a beta blocker:** #88/#121 remain open. Earlier OpenRouter generation E2E succeeded, but generation recovery GET returned 404. Preserve that partial evidence without certifying recovery or Groq. Earlier Stripe Test A02 evidence remains valid within its scope; C04 public webhook delivery remains separate.
8. **Commercial limits remain unapproved:** 100 MB / 1 GB / 5 GB aggregate and 10 / 25 / 50 MB per-file are proposals; trial is TBD. The former 5/15/40 MB plus 1 MB trial proposal is superseded. Missing budgets deny new/growing file claims. The technical per-file ceiling remains 20 MiB.
9. **Next authorized scope:** after the post-merge report, analyze and run local-only tests for #121; plan INF01. No real provider requests, infrastructure changes, purchases or remote operations are implied. INF02/R2 remains a separate proposal.
10. **Operational boundaries:** no deploy, flag activation, remote migration, provider operation, erasure or backup/volume removal. Only the B02 synthetic PostgreSQL container was stopped after tests; Docker Desktop and other projects' Supabase services remain unchanged.

### Post-merge registry and audit evidence

Verified directly with registry manifest inspection for tag
`sha-6a26813e9a0f52d25e78da55465744e3ca798c3a`:

- API `ghcr.io/scontismaart/melpis-api@sha256:d9c27bb773d63a94f615bd8e68119affd4bf8b5090686096bfb4e4c5ff543fc9`.
- Web `ghcr.io/scontismaart/melpis-web@sha256:7c625d576e723ed5c41d2ca249c53c30249a1e8621f16b31f84da8156b6a0cd2`.
- Both manifests contain `linux/amd64` and `linux/arm64`. No image was launched.
- Audit passed the existing strict policy, not a zero-advisory claim: scanner reported 9 known vulnerabilities in 2 packages. Logged reviewed residuals: chromadb 1.1.1 (PYSEC-2026-311, PYSEC-2026-3813/3814/3815) and json-repair 0.25.3 (GHSA-xf7x-x43h-rpqh). No exceptions or audit policy were changed by this merge operation.
- All publish jobs succeeded; only the main-inapplicable no-push build matrix was skipped. No failed/cancelled post-merge job. The 36 skipped Python tests remain excluded evidence.
- GitHub reported `main` unprotected immediately before merge; the ordinary merge API was used without administrator/bypass options. Branch policy was not changed.

## 2. Evidence stages and vocabulary

Use these labels precisely:

| Stage | Meaning in this register |
|---|---|
| Analyzed | Requirements or risks reviewed; no implementation claim. |
| Implemented locally | Code exists in a worktree; this alone is not integrated or remotely certified. |
| PR ready for review | Code is on an open PR branch with checks; it is not part of `main`. |
| Integrated on main | Change is merged and present at the recorded main SHA. |
| Disposable-service regression evidence | GitHub jobs exercised PostgreSQL/Valkey disposable services for the cases in those jobs, including database/concurrency/ACL regressions. This is scoped test evidence, not a deployed host or full beta certification. |
| Staging-certified | Current staging schema and behavior verified against an identified environment. Not established here. |
| Provider-certified | Authorized real-provider behavior verified with correlated evidence. Not established here. |
| Production-approved | Explicit operational/product/legal go-ahead and required gates complete. Not established here. |
| Issue closed | GitHub issue is closed. This records tracker state only; it is not a substitute for all deployment gates. |

## 3. Issue inventory: #85–#102

Statuses are the GitHub tracker state observed for this snapshot. “Evidence” refers to merged PRs or the specific open PR/checks listed; it does not imply external certification.

| Issue | GitHub state | Topic / implementation evidence | Stage and recommendation |
|---|---|---|---|
| [#85](https://github.com/Scontismaart/Melpis/issues/85) | Closed | V1-A00 | Issue closed. |
| [#86](https://github.com/Scontismaart/Melpis/issues/86) | Closed | V1-A01 | Issue closed. |
| [#87](https://github.com/Scontismaart/Melpis/issues/87) | Closed | V1-A02 | Issue closed. |
| [#88](https://github.com/Scontismaart/Melpis/issues/88) | Open, P0 | V1-A03 provider gate, attempts, attribution, and accounting. PR #120 integrated the deterministic provider-dispatch gate/accounting. Prior OpenRouter E2E generation succeeded, but its recovery GET returned 404. | **KEEP OPEN / BLOCKED** on #121 recovery and complete accounting evidence. Preserve successful-generation evidence; do not call recovery certified. Test billing absent/error, expired trial, exhausted quota, accounting outage, and provider-native model/token/cost correlation. |
| [#89](https://github.com/Scontismaart/Melpis/issues/89) | Closed | V1-A04 | Issue closed; PR #122 integrated atomic quota reservation. |
| [#90](https://github.com/Scontismaart/Melpis/issues/90) | Open, P0 | V1-A05 deterministic booking authorization. PR #123 integrated the implementation. Current documented implementation scope includes restaurant/table booking with 60-minute slots. | **SPLIT** implementation from beta certification/perimeter. Keep the issue open until supported verticals, ambiguity/handoff rules, availability and concurrency cases, and acceptance evidence are agreed. |
| [#91](https://github.com/Scontismaart/Melpis/issues/91) | Open, P0 | V1-A06 export/delete/retention/token protection. PR #130 integrated durable GDPR gates. | **KEEP OPEN / BLOCKED** on data inventory, legal retention/DPA, provider deletion propagation, and backup/restore evidence. CI does not prove external erasure or retention. |
| [#92](https://github.com/Scontismaart/Melpis/issues/92) | Open, P0 | V1-B01 Calendar readiness. PR #131 is integrated at main. Readiness must fail closed on unknown availability and avoid duplicate reconciled events. | **KEEP OPEN / BLOCKED** on identified staging and authorized Google E2E under #96/#98. #131’s main checks are code evidence only. |
| [#93](https://github.com/Scontismaart/Melpis/issues/93) | Open, P0 | V1-B02 tenant-scoped PDF/RAG; #132 integrated at `6a26813e9a0f52d25e78da55465744e3ca798c3a`, including the failed-replacement fix. | **KEEP OPEN.** Technical integration completed; current-head and post-merge evidence are recorded above. Staging/capacity, historical byte provenance, commercial quota approval and any original-storage promise remain separate gates. |
| [#94](https://github.com/Scontismaart/Melpis/issues/94) | Open, P1 | V1-B03 wizard, multilingual behavior, and AI transparency reuse the dashboard. | **KEEP OPEN.** Wizard implementation exists, but post-B01/B02 persistence, error disclosure, and end-to-end acceptance are not evidenced here. |
| [#95](https://github.com/Scontismaart/Melpis/issues/95) | Open, P1 | V1-B04 plans, dashboard consumption, and product copy. | **KEEP OPEN / BLOCKED** on commercial approval. File quotas in INF01/INF02 remain proposals; do not encode proposal values as approved entitlements. |
| [#96](https://github.com/Scontismaart/Melpis/issues/96) | Open, P0 | V1-C01 identify staging/production and certify migrations. Issue body contains historical environment observations and schema checks. | **KEEP OPEN / BLOCKED** pending authorized, current environment identification and migration evidence. Historical issue observations are stale for present-state claims. |
| [#97](https://github.com/Scontismaart/Melpis/issues/97) | Open, P0 | V1-C02 host, HTTPS, email, backups, monitoring, and approved budget. | **KEEP OPEN / BLOCKED** on infrastructure ownership/budget and operational evidence, including restore and alert checks. INF01 capacity evidence informs sizing. |
| [#98](https://github.com/Scontismaart/Melpis/issues/98) | Open, P0 | V1-C03 Meta and Google Calendar with authorized recipients/accounts. | **KEEP OPEN / BLOCKED** until authorized provider E2E is run against the identified environment with traceable evidence. |
| [#99](https://github.com/Scontismaart/Melpis/issues/99) | Open, P0 | V1-C04 Stripe Test and preparation of monthly Live prices. Earlier Stripe Test A02 evidence exists. | **KEEP OPEN / BLOCKED** on public endpoint delivery/verification under C04 and explicit Live-price approval. Preserve prior A02 evidence without treating C04 as complete. |
| [#100](https://github.com/Scontismaart/Melpis/issues/100) | Open, P0 | V1-C05 data treatment, legal text, and AI-provider policy. | **KEEP OPEN / BLOCKED** on founder/legal approval of DPA, privacy/retention wording, and processor policy. This gates A06 and launch. |
| [#101](https://github.com/Scontismaart/Melpis/issues/101) | Open, P0 | V1-D01 integrated release and beta go/no-go. | **KEEP OPEN / BLOCKED** until required P0s and staging, security, backup, cost, and rollback gates have evidence and an explicit go/no-go. |
| [#102](https://github.com/Scontismaart/Melpis/issues/102) | Open, P1 | V1-D02 onboard 3–5 businesses and monitor quality/cost. | **KEEP OPEN / BLOCKED** on D01 approval, selected businesses, operational monitoring, and support readiness. |

### Additional P0 blocker

| Issue | GitHub state | Evidence / recommendation |
|---|---|---|
| [#121](https://github.com/Scontismaart/Melpis/issues/121) | Open, P0 beta blocker | OpenRouter generation recovery must reconcile a generation ID with provider-native evidence. A provider 404, timeout, missing field, or mismatch must remain fail-closed: do not invent cost/tokens or unlock another generation. **KEEP OPEN / BLOCKED** until the correlation and authorized recovery flow are evidenced. It blocks A03/#88 certification. |

## 4. Integrated PRs and current branch

| PR | State / main evidence | Scope |
|---|---|---|
| [#103](https://github.com/Scontismaart/Melpis/pull/103) | Merged; merge commit `a25930ecd8ace14e4bf5ffa1492f56824e807793` | Per-tenant onboarding preview limit. |
| [#119](https://github.com/Scontismaart/Melpis/pull/119) | Merged; merge commit `bf259adff71c820aa4c3e9d1f20924dd71b1dc0a` | Monthly-only pricing. |
| [#120](https://github.com/Scontismaart/Melpis/pull/120) | Merged; merge commit `de05813d0230a262b4daf7d24aded8cb380f627c` | A03 gate and accounting at AI provider dispatch. |
| [#122](https://github.com/Scontismaart/Melpis/pull/122) | Merged; merge commit `b9fff31629d03af8c3b102e0c88646ec7011fd5a` | Atomic A04 quota reservation. |
| [#123](https://github.com/Scontismaart/Melpis/pull/123) | Merged; merge commit `a94d8d938fdaa06db91f977ace2814d8467f9f75` | A05 deterministic auto-booking authorization. |
| [#130](https://github.com/Scontismaart/Melpis/pull/130) | Merged; merge commit `bf7ddcb6bcf3467b07beae05779e82de77791486` | A06 durable GDPR gates. |
| [#131](https://github.com/Scontismaart/Melpis/pull/131) | Merged at `a7f09f2d4155dade5f93a6512fb2f5bdd4a9a5ea` | B01 Calendar readiness; prior baseline, not current main. |
| [#132](https://github.com/Scontismaart/Melpis/pull/132) | Merged at current main `6a26813e9a0f52d25e78da55465744e3ca798c3a`; approved head `d141d1625175b97d3ba746c66a8ce9654cbc5f0f` | B02 PDF/RAG, byte accounting and safe replacement. Technical merge only; #93 remains open. |

The old `4035c8d` snapshot and runs 40096981755/40096981661 are superseded intake history. They do not certify the replacement correction or the integrated main. Use the exact approved-head and post-merge links in section 1.

## 5. Environment and evidence separation

| Environment / activity | Verified state | Remaining boundary |
|---|---|---|
| Local regressions | Unit: 1,556 passed, 1 skipped; frontend: 418 passed, 3 skipped. Replacement plus repository PostgreSQL suite: 28 passed. | Overlapping local/CI suites must not be added together. Synthetic providers are not real-provider E2E. |
| Approved PR head | `d141d162`; CI/Migration green, each 3,824 passed and 36 skipped. Four no-push builds green. | PR jobs did not publish GHCR images. |
| Integrated main | #132 merged normally at `6a26813e9a0f52d25e78da55465744e3ca798c3a`. Post-merge runs linked above. | Technical integration does not authorize activation. |
| GHCR | API/web SHA-specific tags published; each manifest directly inspected and contains linux/amd64 and linux/arm64. Digests below. | Published image is not a deployed service. |
| Disposable services | CI exercises PostgreSQL/Valkey database, concurrency and ACL cases. | Not a deployed disposable application/host or operational capacity certificate. |
| Staging | No current operational certificate established by this merge. | C01: named environment, historical rows, backup/restore, migrations 060–066, ACL/RLS, drain and cutover under separate GO. |
| Providers | Prior partial OpenRouter and Stripe Test evidence retained. | #121 recovery, C04 public webhook delivery and C03 Meta/Google proof remain open. |
| Production | No deployment or approval. | D01 requires remaining P0s, legal/operational evidence and explicit go/no-go. |
| Tracker | 4 original issues closed, 14 open; #121 open additionally. | #93 remains open after technical integration. |

## 6. INF01 / INF02 and commercial decisions

**INF01 (capacity benchmark) is a proposal, not a GitHub issue or certification.** It should measure the current MiniLM/PyTorch path against a shared process and viable alternatives such as ONNX/INT8; include batching/queue behavior, cold start and offline model availability, RSS/CPU, latency (including p95), throughput, concurrency, retry/crash/retrieval behavior, and mixed WhatsApp plus PDF load. Workload points proposed in the roadmap include 3–5 beta businesses and 5/25/100 workload scenarios. It depends on B02’s stable behavior, C01’s known schema/environment, and C02’s hosting target. Keep benchmark output separate from production capacity claims.

**INF02 (original-file storage) is a proposal, not implemented storage.** Current B02 discards uploaded originals after processing; any promise to retain, download, or re-index originals needs a separately reviewed private object-storage design. R2 is a candidate only. INF02 depends on B02, B04 commercial copy, A06 deletion/retention, C05 legal policy, and C02 backup/security/cost controls. The proposal should cover tenant authorization, content hash, idempotent upload, retries/orphans, re-index, erasure/retention, backups/versioning, downgrade/concurrency, streaming, timeouts, and malicious archive/document handling.

**File limits remain unapproved.** The current proposal is 100 MB / 1 GB / 5 GB aggregate and 10 / 25 / 50 MB per file, with trial quota TBD. The former 5 / 15 / 40 MB aggregate and 1 MB trial figures are superseded. Neither set should be described as a commitment or enabled as default plan values. B02’s missing/NULL limit is intentionally fail-closed for new file uploads; manual/retrieval behavior remains available according to the implementation contract. No commercial document/page count limit is approved here.

## 7. Dependencies and recommended sequence

| Order | Work | Depends on / gate |
|---|---|---|
| 1 | **Done:** reproduce/fix B02 replacement and add PostgreSQL/DOM regressions. | Integrated in #132; preserve source authorization, quota and fencing. |
| 2 | **Done:** exact-head checks and independent review on `d141d162`. | CI/Migration green, 3,824 passed and 36 skipped each; four builds green. |
| 3 | **Merge done:** founder GO and normal merge #132 at `6a26813e`. | Post-merge CI, registry proof and #93 gates remain distinct from deployment. |
| 4 | Resolve #121 recovery contract and close A03/#88 provider-accounting gaps. | Authorized provider evidence; fail-closed reconciliation; no fabricated accounting. |
| 5 | Progress INF01 capacity benchmark and C05 legal/privacy/provider policy review. | Known benchmark target and founder/legal decisions; C05 gates A06 and any original-file retention promise. |
| 6 | Authorize C01 remote inventory/migration certification and finish C02 host/backup/monitoring plan. | Named environment, explicit scope, approved budget. Do not infer current state from old issue evidence. |
| 7 | Design INF02 if retention/download/re-index of originals is promised; progress B03/B04 acceptance and copy. | INF02 is conditional on a product promise and depends on A06/C05/C02. B04 copy requires approved commercial limits. |
| 8 | Certify B01 and Meta/Google through C03 in identified staging. | C01 plus authorized accounts/recipients and traceable E2E evidence. |
| 9 | Complete A06 deletion/retention and backup propagation evidence. | C01, C03 where applicable, and C05. |
| 10 | Verify Stripe Test endpoint delivery and approve Live pricing under C04. | Preserve earlier A02 evidence; complete the missing public endpoint delivery/verification under explicit authorization. |
| 11 | Complete phase gates, then D01 integrated release review and explicit beta go/no-go. | Required P0 gates, security, restore, cost/kill-switch, monitoring, legal, and rollback. |
| 12 | D02 onboard 3–5 businesses and monitor. | D01 approval, selected participants, support/incident readiness. |

Avoid starting multiple broad implementation PRs at once. INF01 can provide decision evidence while #121 and C05 progress; INF02 should not implement retention promises before legal/security and backup requirements are fixed.

## 8. Qualitative PR sizing

| Candidate | Relative size | Notes |
|---|---|---|
| Integrated #132 | **XL** | Broad existing B02 package, including the reviewed replacement correction. Completed technical review and merge; future work stays in separate, smaller PRs. |
| #121 recovery contract and tests | **M** | Keep deterministic accounting/recovery logic separate from any authorized provider E2E. |
| INF01 benchmark harness/report | **M** | Benchmark code/config and findings; avoid bundling unrelated runtime optimizations. |
| C01 inventory/migration evidence | **S–M** | Primarily environment-specific evidence and controlled migration work; remote actions require explicit scope/authorization. |
| C02 operational setup | **M–L** | Depends on approved budget/host and backup/restore/monitoring requirements. |
| INF02 storage design | **S–M** for design; **L** for implementation | Treat the storage decision and implementation as separate review steps. |
| B04 UI/copy follow-up | **S–M** | Requires approved commercial limits and truthful usage semantics first. |

## 9. Real P0 blockers and founder decisions

Explicit open P0 blockers: (1) B02 operational certification (replacement code defect resolved in #132); (2) #121 OpenRouter recovery; (3) INF01 capacity evidence; (4) C01 staging schema and historical-row handling; (5) C02 host, backup, monitoring, and restore; (6) C03 authorized Meta/Google E2E; (7) C04 public Stripe endpoint delivery/verification and pricing approval; (8) C05 legal/provider policy and A06 deletion/retention evidence; (9) B02 file quotas not commercially approved. INF02 becomes a gate if product copy promises original-file retention, download, or re-index; otherwise it remains a conditional design item.

1. Approve or reject the proposed aggregate/per-file quota matrix and define trial quota; until then retain the unconfigured-budget behavior that denies new file growth.
2. Decide whether private beta supports restaurant/table booking only or a broader vertical set; this determines the A05/#90 acceptance boundary.
3. Select the authorized provider/account and recovery owner for #121; do not release A03 based only on synthetic test evidence.
4. Identify the staging project and approve the exact inventory/migration scope for C01.
5. Approve C02 budget and operational target, informed by INF01 capacity measurements.
6. Approve the legal/privacy/processor wording and retention policy under C05 before claiming A06 complete.
7. Decide whether originals must be retained. If yes, authorize a separate INF02 design review; R2 is only a candidate.
8. After those gates, make an explicit D01 beta go/no-go. No production deployment or onboarding authorization is implied by this register.

## 10. Checkout note

The primary local checkout was not reset or cleaned. The isolated B02 worktree
was used for the reviewed implementation and the separate document-only
follow-up. Remote GitHub `main` at `6a26813e9a0f52d25e78da55465744e3ca798c3a`
is the integration baseline for this snapshot. Unrelated local files and
runtime directories remain untouched.

## 11. B02 correction cycle

The founder's replacement-failure report was reproduced against a dedicated
local PostgreSQL 16/pgvector disposable database. The initial regression
failed because claim had overwritten the published name before OCR succeeded.
It passed after separating `index_intent` from canonical published state.
The regression suite also covers failure, timeout, cancellation, retry,
concurrent claims, expired-lease fencing, revocation/delete, quota downgrade,
transaction rollback and A04 stale-source checks. UI operation state and
published-request correlation are covered separately by DOM tests.

Implementation is confined to PR #132. Its source-only migration 066 adds
pending intent and mutation fences; 065 remains part of the tested prerequisite
chain. No original-file storage, R2, larger file caps or commercial values are
implemented. The former capacity proposal is explicitly superseded.

Local unit proof: 1,556 passed, 1 skipped. Full frontend proof: 418 passed,
3 skipped. Final approved-head totals are 3,824 passed and 36 skipped in each workflow;
see section 1 for exact-head and post-merge links. No overlapping counts are summed.

Founder GO authorized only the technical merge, now completed. Open issue decisions, staging,
provider certification, INF01/INF02 work and commercial approvals remain incomplete. Only the task's dedicated PostgreSQL container is stopped
after testing; other local Supabase services are left running as instructed.

## 12. Source links

- Founder-supplied roadmap `MELPIS_V1_ROADMAP_COMPLETA_2026-10-24 (5).md` and the accompanying coordination mandate (planning inputs; live GitHub is authoritative for repository state).
- GitHub issues: [#85](https://github.com/Scontismaart/Melpis/issues/85) through [#102](https://github.com/Scontismaart/Melpis/issues/102), plus [#121](https://github.com/Scontismaart/Melpis/issues/121).
- GitHub PRs: [#103](https://github.com/Scontismaart/Melpis/pull/103), [#119](https://github.com/Scontismaart/Melpis/pull/119), [#120](https://github.com/Scontismaart/Melpis/pull/120), [#122](https://github.com/Scontismaart/Melpis/pull/122), [#123](https://github.com/Scontismaart/Melpis/pull/123), [#130](https://github.com/Scontismaart/Melpis/pull/130), [#131](https://github.com/Scontismaart/Melpis/pull/131), [#132](https://github.com/Scontismaart/Melpis/pull/132).
- Approved #132 head: [CI 40111907116](https://github.com/Scontismaart/Melpis/actions/runs/40111907116), [Migration 40111907091](https://github.com/Scontismaart/Melpis/actions/runs/40111907091).
- Integrated #132 main: [CI 40114921956](https://github.com/Scontismaart/Melpis/actions/runs/40114921956), [Migration 40114921848](https://github.com/Scontismaart/Melpis/actions/runs/40114921848).
