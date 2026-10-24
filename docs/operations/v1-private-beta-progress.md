# Melpis V1 private beta progress register

**Intake snapshot date:** 2026-10-24  
**Repository:** [Scontismaart/Melpis](https://github.com/Scontismaart/Melpis)  
**Purpose:** evidence-based readiness register for the private beta. This records GitHub state and the roadmap snapshot; it does not certify remote infrastructure, providers, or production.

## 1. Executive snapshot

1. **Canonical issue set:** #85–#102 contains 18 issues: #85, #86, #87, and #89 are closed; the other 14 are open. #121 is an additional, open P0 blocker for OpenRouter recovery, outside that 18-issue count.
2. **Integrated work:** PRs #103, #119, #120, #122, #123, #130, and #131 are merged. Current `main` is `a7f09f2d4155dade5f93a6512fb2f5bdd4a9a5ea`, the merge commit for #131.
3. **B02 at intake:** PR [#132](https://github.com/Scontismaart/Melpis/pull/132) was open, non-draft, and mergeable at `4035c8d69c248d185a77a84eb95b862cf97ade52`, with 118 changed files. It has since been returned to draft for the replacement correction below. Its current head, review state and exact-head checks are recorded by GitHub on that PR; none of these authorize merge.
4. **PR-level checks:** #132 CI run [40096981755](https://github.com/Scontismaart/Melpis/actions/runs/40096981755) and Migration run [40096981661](https://github.com/Scontismaart/Melpis/actions/runs/40096981661) succeeded on the exact head SHA. The reports show 3,806 passed and 36 skipped; CI reported 124 warnings. The GitHub jobs exercised disposable PostgreSQL/Valkey services and provide regression evidence for the specific database, concurrency, and ACL cases they ran. Four no-push image build jobs succeeded: API and web for amd64 and arm64. Image publish and multi-architecture manifest jobs were skipped as expected for a no-push run.
5. **Main checks:** main CI [40001253658](https://github.com/Scontismaart/Melpis/actions/runs/40001253658) and Migration [40001253651](https://github.com/Scontismaart/Melpis/actions/runs/40001253651) succeeded on the current main SHA. These validate the integrated #131 state, not the unmerged #132 changes.
6. **Deployment evidence remains separate:** GitHub tests exercise disposable database services, but do not prove a deployed disposable host, staging migration, or production readiness. The roadmap and issue #96 contain older remote-environment observations; those observations are not a current remote-state check.
7. **A03 remains a beta blocker:** #88 is open and #121 separately blocks certification until OpenRouter’s generation recovery is reconciled with provider-native evidence and fail-closed handling. A prior OpenRouter E2E generation succeeded, but recovery GET returned 404, so recovery is not certified. Earlier Stripe Test A02 evidence also exists; C04 still lacks delivery/verification of the public endpoint. Preserve those partial proofs without claiming complete provider coverage.
8. **Commercial limits are not approved:** INF01/INF02 and the new file-size figures are proposals. Proposed aggregate sizes are 100 MB / 1 GB / 5 GB and per-file sizes 10 / 25 / 50 MB; trial quota is TBD. The older 5 / 15 / 40 MB plus 1 MB trial proposal is superseded. Do not treat either set as approved plan policy.
9. **Recommended order:** first reproduce and fix the B02 replacement/re-index defect, then push a new SHA and complete exact-head CI, Migration, four image builds, and review. Only after that request explicit GO to merge. Progress #121, INF01, C01/C02, and C05; then B03/B04 and phase-specific gates; D01 precedes D02.
10. **Immediate executable step:** reproduce the B02 replacement failure with focused regression coverage and correct it without weakening tenant/quota guarantees. Current green checks cover the current SHA, not the subsequently reported defect. A new SHA must pass checks and review before requesting merge GO.

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
| [#93](https://github.com/Scontismaart/Melpis/issues/93) | Open, P0 | V1-B02 complete tenant-scoped PDF/RAG. PR #132 has exact-head CI, Migration, and four no-push image builds green, but a replacement/re-index defect has been reported for focused reproduction and correction. | **SPLIT** implementation from integration/activation certification. Fix replacement first, push a new SHA, rerun checks/review, then obtain explicit merge GO. Staging, quota approval, and capacity gates remain separate. |
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
| [#131](https://github.com/Scontismaart/Melpis/pull/131) | Merged; merge commit/current main `a7f09f2d4155dade5f93a6512fb2f5bdd4a9a5ea` | B01 Calendar readiness. Main CI and Migration both succeeded at this SHA (runs above). |
| [#132](https://github.com/Scontismaart/Melpis/pull/132) | Open, non-draft, mergeable; head `4035c8d69c248d185a77a84eb95b862cf97ade52`; base current main | B02 tenant-scoped PDF/RAG and file quota implementation. Exact-head CI/Migration and four no-push image builds succeeded. 118 files; +7,382 / −812. **Implemented on PR only.** |

The #132 green checks are reproducible GitHub evidence for that PR head, including the specific disposable PostgreSQL/Valkey regressions exercised by its jobs. They do not change the PR’s open status or certify a deployed host, remote staging, provider behavior, production operations, or commercial approval. The replacement/re-index defect reported after these checks needs a focused fix and new exact-head evidence before merge GO.

## 5. Environment and evidence separation

| Environment / activity | State supported by this snapshot | What remains unproven |
|---|---|---|
| Local implementation | B02 implementation exists in the isolated worktree/PR branch; local development checks were reported by the parent. | Not integrated on main; replacement/re-index correction remains to be reproduced and fixed. |
| PR ready for review | #132 currently has green exact-head checks and is open/non-draft/mergeable. | Reported replacement/re-index defect must be fixed on a new SHA, then checks and review repeated before merge GO. |
| GitHub disposable-service regression tests | #132 exact SHA succeeded: CI run 40096981755; Migration 40096981661; 3,806 passed, 36 skipped; 124 warnings. Jobs exercised disposable PostgreSQL/Valkey services for their DB/concurrency/ACL regression cases. | Scoped cases only; no deployed disposable host, provider proof, or remote infrastructure certification. |
| Image builds | API/web amd64 and arm64 succeeded in no-push jobs. | Publish/manifest jobs skipped; no registry artifact or deployment was produced. |
| Main | #131 integrated at `a7f09f2`; main CI 40001253658 and Migration 40001253651 succeeded. | #132 is not in main. |
| Disposable services | GitHub CI/Migration exercised disposable PostgreSQL/Valkey services and provide regression evidence for cases run. | This is not a deployed disposable application/host, full end-to-end environment, or provider certification. |
| Staging | No current staging certification is established here. | Current project/ref/schema inventory and migration result under #96, after explicit authorization. |
| Providers | Prior partial evidence exists: OpenRouter E2E generated successfully, but recovery GET returned 404; earlier Stripe Test A02 evidence exists. | OpenRouter recovery remains uncertified (#121); C04 still needs public endpoint delivery/verification; Meta/Google authorized E2E remains open (#98). Preserve prior evidence without overstating scope. |
| Production | No production approval or deployment is established here. | All D01 gates, operational rollback/backup/cost controls, legal approval, and explicit go/no-go. |
| Tracker closure | Four of #85–#102 are closed. | Closing remaining issues requires their acceptance evidence; green CI alone is insufficient. |

## 6. INF01 / INF02 and commercial decisions

**INF01 (capacity benchmark) is a proposal, not a GitHub issue or certification.** It should measure the current MiniLM/PyTorch path against a shared process and viable alternatives such as ONNX/INT8; include batching/queue behavior, cold start and offline model availability, RSS/CPU, latency (including p95), throughput, concurrency, retry/crash/retrieval behavior, and mixed WhatsApp plus PDF load. Workload points proposed in the roadmap include 3–5 beta businesses and 5/25/100 workload scenarios. It depends on B02’s stable behavior, C01’s known schema/environment, and C02’s hosting target. Keep benchmark output separate from production capacity claims.

**INF02 (original-file storage) is a proposal, not implemented storage.** Current B02 discards uploaded originals after processing; any promise to retain, download, or re-index originals needs a separately reviewed private object-storage design. R2 is a candidate only. INF02 depends on B02, B04 commercial copy, A06 deletion/retention, C05 legal policy, and C02 backup/security/cost controls. The proposal should cover tenant authorization, content hash, idempotent upload, retries/orphans, re-index, erasure/retention, backups/versioning, downgrade/concurrency, streaming, timeouts, and malicious archive/document handling.

**File limits remain unapproved.** The current proposal is 100 MB / 1 GB / 5 GB aggregate and 10 / 25 / 50 MB per file, with trial quota TBD. The former 5 / 15 / 40 MB aggregate and 1 MB trial figures are superseded. Neither set should be described as a commitment or enabled as default plan values. B02’s missing/NULL limit is intentionally fail-closed for new file uploads; manual/retrieval behavior remains available according to the implementation contract. No commercial document/page count limit is approved here.

## 7. Dependencies and recommended sequence

| Order | Work | Depends on / gate |
|---|---|---|
| 1 | Reproduce and fix the confirmed B02 replacement/re-index defect; add focused regression coverage. | Current #132 green SHA does not include a fix for this confirmed issue. Preserve tenant isolation, quota accounting, and prior published content on failed replacement. |
| 2 | Push the corrected SHA; complete exact-head CI, Migration, four image builds, and review. | Rerun evidence must correspond to the corrected SHA. |
| 3 | Only after step 2, obtain explicit GO to merge #132. | A green PR is not itself merge authorization; merge does not authorize deployment. |
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
| Current #132 | **XL** | 118 files, +7,382 / −812. CI is green, but review should be split by migration/schema, quota ledger/publication, API/RAG, and tests/docs. The current PR is already the reviewable package; do not silently rewrite its scope. |
| #121 recovery contract and tests | **M** | Keep deterministic accounting/recovery logic separate from any authorized provider E2E. |
| INF01 benchmark harness/report | **M** | Benchmark code/config and findings; avoid bundling unrelated runtime optimizations. |
| C01 inventory/migration evidence | **S–M** | Primarily environment-specific evidence and controlled migration work; remote actions require explicit scope/authorization. |
| C02 operational setup | **M–L** | Depends on approved budget/host and backup/restore/monitoring requirements. |
| INF02 storage design | **S–M** for design; **L** for implementation | Treat the storage decision and implementation as separate review steps. |
| B04 UI/copy follow-up | **S–M** | Requires approved commercial limits and truthful usage semantics first. |

## 9. Real P0 blockers and founder decisions

Explicit open P0 blockers: (1) confirmed B02 replacement/re-index defect; (2) #121 OpenRouter recovery; (3) INF01 capacity evidence; (4) C01 staging schema and historical-row handling; (5) C02 host, backup, monitoring, and restore; (6) C03 authorized Meta/Google E2E; (7) C04 public Stripe endpoint delivery/verification and pricing approval; (8) C05 legal/provider policy and A06 deletion/retention evidence; (9) B02 file quotas not commercially approved. INF02 becomes a gate if product copy promises original-file retention, download, or re-index; otherwise it remains a conditional design item.

1. Approve or reject the proposed aggregate/per-file quota matrix and define trial quota; until then retain the unconfigured-budget behavior that denies new file growth.
2. Decide whether private beta supports restaurant/table booking only or a broader vertical set; this determines the A05/#90 acceptance boundary.
3. Select the authorized provider/account and recovery owner for #121; do not release A03 based only on synthetic test evidence.
4. Identify the staging project and approve the exact inventory/migration scope for C01.
5. Approve C02 budget and operational target, informed by INF01 capacity measurements.
6. Approve the legal/privacy/processor wording and retention policy under C05 before claiming A06 complete.
7. Decide whether originals must be retained. If yes, authorize a separate INF02 design review; R2 is only a candidate.
8. After those gates, make an explicit D01 beta go/no-go. No production deployment or onboarding authorization is implied by this register.

## 10. Checkout note

The B02 worktree is isolated. The primary local checkout's main reference is
`0e1c60b`, while remote GitHub `main` is
`a7f09f2d4155dade5f93a6512fb2f5bdd4a9a5ea`. It was not reset or modified.
Use the remote SHA recorded above as the integration baseline.

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
3 skipped. Final database totals and complete workflow evidence belong to
the current-head checks on [PR #132](https://github.com/Scontismaart/Melpis/pull/132/checks),
and the sanitized final PR review report. Intake run links below remain
historical evidence for 4035c8d, not evidence for a later head.

Merge remains a founder GO gate. None of the open issue decisions, staging,
provider certification, INF01/INF02 work or commercial approvals is completed
by this correction. Only the task's dedicated PostgreSQL container is stopped
after testing; other local Supabase services are left running as instructed.

## 12. Source links

- Founder-supplied roadmap `MELPIS_V1_ROADMAP_COMPLETA_2026-10-24 (5).md` and the accompanying coordination mandate (planning inputs; live GitHub is authoritative for repository state).
- GitHub issues: [#85](https://github.com/Scontismaart/Melpis/issues/85) through [#102](https://github.com/Scontismaart/Melpis/issues/102), plus [#121](https://github.com/Scontismaart/Melpis/issues/121).
- GitHub PRs: [#103](https://github.com/Scontismaart/Melpis/pull/103), [#119](https://github.com/Scontismaart/Melpis/pull/119), [#120](https://github.com/Scontismaart/Melpis/pull/120), [#122](https://github.com/Scontismaart/Melpis/pull/122), [#123](https://github.com/Scontismaart/Melpis/pull/123), [#130](https://github.com/Scontismaart/Melpis/pull/130), [#131](https://github.com/Scontismaart/Melpis/pull/131), [#132](https://github.com/Scontismaart/Melpis/pull/132).
- Current #132 exact-head runs: [CI 40096981755](https://github.com/Scontismaart/Melpis/actions/runs/40096981755), [Migration 40096981661](https://github.com/Scontismaart/Melpis/actions/runs/40096981661).
- Current main runs: [CI 40001253658](https://github.com/Scontismaart/Melpis/actions/runs/40001253658), [Migration 40001253651](https://github.com/Scontismaart/Melpis/actions/runs/40001253651).

