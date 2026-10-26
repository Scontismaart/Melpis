# Melpis V1 private beta progress register

**Intake snapshot date:** 2026-10-24; current cycle update: 2026-10-26
**Repository:** [Scontismaart/Melpis](https://github.com/Scontismaart/Melpis)
**Purpose:** evidence-based readiness register for the private beta. This records GitHub state and the roadmap snapshot; it does not certify remote infrastructure, providers, or production.

## 1. Executive snapshot

**Current integration snapshot: 2026-10-26.** Main is
`06388f4f0c88a937d125ce227d2d54d13a2d8979`, normally merged from PR #142 at
reviewed head `64628bbea25da248c9b8ef1a7a977e17f54776f0`. This follows the
previous 2026-10-25 main checkpoint `800c56c0b631b81b7bbefe0f51c843dd13d498d1`
and its six-PR train. Exact PR heads, merge commits, CI receipts and split
registry-manifest evidence are recorded in the
[integration and checkpoint register](merge-train-20261025.md). Integration
does not certify staging or authorize beta activation.

1. **Post-merge CI:** [40374919190](https://github.com/Scontismaart/Melpis/actions/runs/40374919190)
   succeeded on current main: **3,972 passed, 36 skipped, 126 warnings**. The
   pre-merge run [40370667199](https://github.com/Scontismaart/Melpis/actions/runs/40370667199)
   had the same counts. Scanner fixtures and the actual main-history scan
   passed; the Linux disposable regression suite passed all 13 cases. Audit,
   frontend, four architecture publications, and both multiarch manifests
   succeeded. No separate Migration check run appeared; the main CI exercised
   its disposable database migration/regression chain.
2. **GHCR:** current-main API index digest
   `sha256:3ecc28eaa4c6c769e9ca9ca38760e88d2a6c88308ec31e55f641ab001ec40ee8`
   and web index digest
   `sha256:1d499faa7232a9b28dc784d09647aa62b4101bd39e4126b68116c6bc3c3bf82d`.
   Both contain linux/amd64 and linux/arm64. The previous `800c56c` checkpoint
   and its digests remain recorded as dated history. No service was deployed.
3. **Audit scope:** the existing reviewed Python dependency audit policy passed;
   nine known residual advisories remain in `chromadb==1.1.1` and
   `json-repair==0.25.3`. The frontend audit found zero vulnerabilities. No new
   exception, suppression or policy weakening was added.
4. **OpenRouter #121: implementation integrated, recovery not certified.** PR
   #141 adds a recovery-only exact binding for the reviewed Free alias and
   versioned `:free` model, requiring zero provider-reported cost in the
   authenticated generation receipt. The generic matcher remains unchanged;
   no arbitrary dated alias is accepted. The original one-POST probe remains
   blocked and was not revisited or reconciled. #121 and #88 remain open; see
   [probe history and correction](openrouter-recovery-20261025.md). Groq is
   still uncertified.
5. **INF01 #139: integrated, not operationally certified.** The shared private
   authenticated pinned ONNX FP32 runtime has bounded queues and process
   fencing. Local offline, crash, missing-model, load and PDF/pgvector mixed
   diagnostics passed. Whole-host C02/SLO, ARM inference and durable-inbox/real
   WhatsApp mixed load remain unverified; this is not deployment or capacity
   certification. Current CI is linked above.
6. **Other integrated PRs:** #136 provides guarded local migration/replay
   evidence only; #137 does not certify public Stripe delivery; #135 does not
   replace real responsive/provider/staging acceptance; #138 corrects plan and
   Calendar-optional copy but does not certify Calendar. Exact evidence is in
   the merge-train record.
7. **Beta scope decisions:** D02 is five businesses, one each from restaurant,
   hair/barber, beauty/spa, hotel/B&B, and medical/dental. A05 currently
   authorizes only restaurant/table booking with 60-minute slots; the other four
   sectors are not certified for automatic booking. Unsupported or ambiguous
   requests must remain on the safe human-handoff path. Calendar is optional
   for each tenant, but its complete C03 functionality must be certified in
   staging before beta go/no-go.
8. **B02 dashboard replacement:** backend `index_intent` replacement semantics
   are implemented. The remaining beta gate is operational/dashboard evidence
   for successful and failed replacement, retry, concurrent and revoked cases;
   verify the correlated published request before showing completion.
   Original-PDF storage is not part of V1. INF02 is postponed and is not a beta
   gate. Replacement or re-indexing requires a new upload; retained originals
   and original-file downloads are not offered.
9. **C05:** the [technical readiness checklist](c05-readiness-20261025.md)
   still records the DPA §9 fixed 90-day physical-deletion promise versus the
   runtime's policy-gated redaction. This mismatch remains blocked pending
   founder/controller and legal review. No legal approval, retention activation
   or provider configuration change is claimed.
10. **Open issues and boundaries:** #85–87 and #89 are closed; #88, #90–102 and
    #121 remain open. No deployment, remote migration, provider operation,
    commercial activation, retention/erasure activation or issue closure is
    claimed. Temporary runtime/credential cleanup and rotation remain gates.

The B02 correction-cycle details in section 11 remain historical. The current
main and integration evidence are in the merge-train record above.

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
| [#88](https://github.com/Scontismaart/Melpis/issues/88) | Open, P0 | V1-A03 provider gate, attempts, attribution, and accounting. PR #120 integrated deterministic dispatch/accounting; PR #141 adds one exact recovery-only model binding. The original blocked probe/ledger state was not reconciled. | **KEEP OPEN / BLOCKED** on attempt-specific recovery and complete accounting evidence. Preserve the original evidence; do not call recovery certified. Test billing absent/error, expired trial, exhausted quota, accounting outage, and provider-native model/token/cost correlation. |
| [#89](https://github.com/Scontismaart/Melpis/issues/89) | Closed | V1-A04 | Issue closed; PR #122 integrated atomic quota reservation. |
| [#90](https://github.com/Scontismaart/Melpis/issues/90) | Open, P0 | V1-A05 deterministic booking authorization. PR #123 integrates restaurant/table booking with 60-minute slots. Selected sectors: restaurant, hair/barber, beauty/spa, hotel/B&B, and medical/dental. | **KEEP OPEN / BLOCKED** for per-sector acceptance. Only restaurant/table booking is currently authorized; the other four sectors are not certified for automatic booking. Unsupported or ambiguous requests stay on safe human handoff. |
| [#91](https://github.com/Scontismaart/Melpis/issues/91) | Open, P0 | V1-A06 export/delete/retention/token protection. PR #130 integrated durable GDPR gates. | **KEEP OPEN / BLOCKED** on data inventory, legal retention/DPA, provider deletion propagation, and backup/restore evidence. CI does not prove external erasure or retention. |
| [#92](https://github.com/Scontismaart/Melpis/issues/92) | Open, P0 | V1-B01 Calendar readiness. The feature is optional per tenant, but complete C03 Calendar functionality must be certified in identified staging before beta go/no-go. | **KEEP OPEN / BLOCKED** on authorized Google E2E, full Calendar-function certification and #96 staging evidence. Optional customer use does not waive the release gate. |
| [#93](https://github.com/Scontismaart/Melpis/issues/93) | Open, P0 | V1-B02 tenant-scoped PDF/RAG; #132 integrated safe replacement using `index_intent`, retaining the published revision while a replacement is pending or fails. V1 does not store original PDFs. | **KEEP OPEN.** Verify dashboard success, failure, retry, concurrent and revoked replacement behavior; correlate the published request before reporting completion. Staging/capacity, historical byte provenance and commercial quota remain separate gates. INF02 original-file storage is postponed and is not a beta gate. |
| [#94](https://github.com/Scontismaart/Melpis/issues/94) | Open, P1 | V1-B03 wizard, multilingual behavior, and AI transparency reuse the dashboard. | **KEEP OPEN.** #135 supplies local wizard/session/persistence/error-state regressions; exact-head CI and four builds pass. Real Staging/Meta/Google/responsive acceptance remains separate. |
| [#95](https://github.com/Scontismaart/Melpis/issues/95) | Open, P1 | V1-B04 plans, dashboard consumption, and product copy; #138 is integrated with all-plan RAG/trial and optional Calendar copy. Founder-approved quota direction: 100 MB aggregate / 10 MB per file; 1 GB / 25 MB; 5 GB / 50 MB. | **KEEP OPEN / BLOCKED** pending benchmark and certification before activation. The approved direction is inactive and must not be represented as enabled entitlements. Trial 20 MB aggregate / 10 MB per file is proposal only. INF02 original-file storage is postponed, not a beta gate. |
| [#96](https://github.com/Scontismaart/Melpis/issues/96) | Open, P0 | V1-C01 staging/production identification and migration certification. #136 is integrated and supplies guarded local clean/upgrade/replay evidence for 060–066. | **KEEP OPEN / BLOCKED** pending authorized current-environment identification, historical-row review and remote migration evidence. Local CI does not certify remote schema or cutover. |
| [#97](https://github.com/Scontismaart/Melpis/issues/97) | Open, P0 | V1-C02 host, HTTPS, email, backups, monitoring, and approved budget. | **KEEP OPEN / BLOCKED** on infrastructure ownership/budget and operational evidence, including restore and alert checks. INF01 capacity evidence informs sizing. |
| [#98](https://github.com/Scontismaart/Melpis/issues/98) | Open, P0 | V1-C03 Meta and Google Calendar with authorized recipients/accounts. | **KEEP OPEN / BLOCKED** until authorized provider E2E is run against the identified environment with traceable evidence. |
| [#99](https://github.com/Scontismaart/Melpis/issues/99) | Open, P0 | V1-C04 Stripe Test and preparation of monthly Live prices. Earlier Stripe Test A02 evidence exists. | **KEEP OPEN / BLOCKED** on public endpoint delivery/verification under C04 and explicit Live-price approval. Preserve prior A02 evidence without treating C04 as complete. |
| [#100](https://github.com/Scontismaart/Melpis/issues/100) | Open, P0 | V1-C05 data treatment, legal text, and AI-provider policy. | **KEEP OPEN / BLOCKED** on founder/legal approval of DPA, privacy/retention wording, and processor policy. This gates A06 and launch. |
| [#101](https://github.com/Scontismaart/Melpis/issues/101) | Open, P0 | V1-D01 integrated release and beta go/no-go. | **KEEP OPEN / BLOCKED** until required P0s and staging, security, backup, cost, and rollback gates have evidence and an explicit go/no-go. |
| [#102](https://github.com/Scontismaart/Melpis/issues/102) | Open, P1 | V1-D02 onboard five businesses, one each from restaurant, hair/barber, beauty/spa, hotel/B&B, and medical/dental, and monitor quality/cost. | **KEEP OPEN / BLOCKED** on D01 approval, per-sector A05 acceptance, selected participants, operational monitoring, and support readiness. Sector selection is complete; automatic booking remains authorized only for restaurant/table with 60-minute slots. |

### Additional P0 blocker

| Issue | GitHub state | Evidence / recommendation |
|---|---|---|
| [#121](https://github.com/Scontismaart/Melpis/issues/121) | Open, P0 beta blocker | PR #141 integrates one recovery-only exact requested/actual model binding and requires zero provider-reported cost; generic model matching remains unchanged. The original blocked probe was not reconciled. A 404, timeout, missing field or mismatch stays fail-closed. **KEEP OPEN / BLOCKED** until attempt-specific recovery and authorized operational evidence complete. It blocks A03/#88 certification. |

## 4. Integrated PR history

| PR | State / main evidence | Scope |
|---|---|---|
| [#103](https://github.com/Scontismaart/Melpis/pull/103) | Merged; merge commit `a25930ecd8ace14e4bf5ffa1492f56824e807793` | Per-tenant onboarding preview limit. |
| [#119](https://github.com/Scontismaart/Melpis/pull/119) | Merged; merge commit `bf259adff71c820aa4c3e9d1f20924dd71b1dc0a` | Monthly-only pricing. |
| [#120](https://github.com/Scontismaart/Melpis/pull/120) | Merged; merge commit `de05813d0230a262b4daf7d24aded8cb380f627c` | A03 gate and accounting at AI provider dispatch. |
| [#122](https://github.com/Scontismaart/Melpis/pull/122) | Merged; merge commit `b9fff31629d03af8c3b102e0c88646ec7011fd5a` | Atomic A04 quota reservation. |
| [#123](https://github.com/Scontismaart/Melpis/pull/123) | Merged; merge commit `a94d8d938fdaa06db91f977ace2814d8467f9f75` | A05 deterministic auto-booking authorization. |
| [#130](https://github.com/Scontismaart/Melpis/pull/130) | Merged; merge commit `bf7ddcb6bcf3467b07beae05779e82de77791486` | A06 durable GDPR gates. |
| [#131](https://github.com/Scontismaart/Melpis/pull/131) | Merged at `a7f09f2d4155dade5f93a6512fb2f5bdd4a9a5ea` | B01 Calendar readiness; prior baseline, not current main. |
| [#132](https://github.com/Scontismaart/Melpis/pull/132) | Merged at the former B02 baseline `6a26813e9a0f52d25e78da55465744e3ca798c3a`; approved head `d141d1625175b97d3ba746c66a8ce9654cbc5f0f` | B02 PDF/RAG, byte accounting and safe replacement. Technical merge only; #93 remains open. |
| [#133](https://github.com/Scontismaart/Melpis/pull/133) | Merged at `688737ac0fb563c9b7144cb5106b892e8df41e0d` | Separate B02 progress documentation. |
| [#134](https://github.com/Scontismaart/Melpis/pull/134) | Merged; merge commit `327822f0e880ad0bf6a6ac1273e24b30d49dda9c` | Preserve authentic observed OpenRouter generation ID independently of invalid usage; recovery certification remains separate. |
| [#136](https://github.com/Scontismaart/Melpis/pull/136) | Merged; `9cb0482b42d68b31f1272ed4858d078dbfb3c50c` | C01 guarded local migration and replay evidence; remote certification remains separate. |
| [#137](https://github.com/Scontismaart/Melpis/pull/137) | Merged; `42dc1d0fddad69b6c8ec571642c6b188bc4c97f9` | C04 Stripe boundary and reliability controls; public delivery/Live approval remain separate. |
| [#135](https://github.com/Scontismaart/Melpis/pull/135) | Merged; `239b5f4a0af3dff04c9bbfe35997ce9ac29ac475` | B03 onboarding wizard and states; staging/provider acceptance remains separate. |
| [#138](https://github.com/Scontismaart/Melpis/pull/138) | Merged; `7bef5f507e3ef9adfd2790d99a8191e9eb422c30` | B04 plans and truthful product copy; commercial approvals remain separate. |
| [#141](https://github.com/Scontismaart/Melpis/pull/141) | Merged; `cfe09e2214431f4015e3fa01972cd1f553ad736e` | Exact-pair OpenRouter recovery correction; provider recovery certification remains separate. |
| [#139](https://github.com/Scontismaart/Melpis/pull/139) | Merged at prior main checkpoint `800c56c0b631b81b7bbefe0f51c843dd13d498d1` (25 October) | INF01 shared runtime and local diagnostics; C02 operational capacity and deployment remain separate. |
| [#142](https://github.com/Scontismaart/Melpis/pull/142) | Merged; current main `06388f4f0c88a937d125ce227d2d54d13a2d8979`; reviewed head `64628bbea25da248c9b8ef1a7a977e17f54776f0` | Fail-closed Gitleaks gate and candidate-history CI evidence; no activation or operational certification. |

The full reviewed heads, exact post-merge CI runs and published manifests for #135–#142 are in the [integration and checkpoint register](merge-train-20261025.md). The old `4035c8d` snapshot and runs 40096981755/40096981661 are superseded intake history; they do not certify the replacement correction or current main. Historical B02 details remain in section 11.

## 5. Environment and evidence separation

| Environment / activity | Verified state | Remaining boundary |
|---|---|---|
| Historical B02 local regressions | Unit: 1,556 passed, 1 skipped; frontend: 418 passed, 3 skipped. Replacement plus repository PostgreSQL suite: 28 passed. | Overlapping local/CI suites must not be added together. Synthetic providers are not real-provider E2E. |
| Approved PR head | `d141d162`; CI/Migration green, each 3,824 passed and 36 skipped. Four no-push builds green. | PR jobs did not publish GHCR images. |
| Integrated main | Current main is `06388f4f0c88a937d125ce227d2d54d13a2d8979`; PR #142 is normally merged after the dated 25 October `800c56c` checkpoint. Receipts are in the [integration and checkpoint register](merge-train-20261025.md). | Technical integration does not authorize activation. |
| GHCR | Current-main API/web index digests verified with linux/amd64 and linux/arm64; see the 26 October checkpoint. | Published image is not a deployed service. |
| Disposable services | CI exercises PostgreSQL/Valkey database, concurrency and ACL cases. | Not a deployed disposable application/host or operational capacity certificate. |
| Staging | No current operational certificate established by this merge. | C01: named environment, historical rows, backup/restore, migrations 060–066, ACL/RLS, drain and cutover under separate GO. |
| Providers | PR #141 integrates the exact-pair local OpenRouter recovery path. PR #142 and its CI made no provider operation. | Original #121 attempt remains unresolved; C04 public webhook delivery and C03 Meta/Google proof remain open. |
| Production | No deployment or approval. | D01 requires remaining P0s, legal/operational evidence and explicit go/no-go. |
| Tracker | #85–87 and #89 closed; #88 and #90–102 remain open; #121 remains open. | Integration does not close issues or certify operational readiness. |

## 6. INF01 / INF02 and commercial decisions

**INF01 is integrated in #139 at the 25 October `800c56c` checkpoint; it is not fleet or C02 certification.** The shared pinned ONNX FP32 runtime and bounded local workload diagnostics are evidence for this implementation, not whole-host capacity, production SLO, ARM inference or durable-inbox/real WhatsApp mixed-load certification. Workload points include 5/25/100 load scenarios; the selected private-beta cohort is five businesses, one each from restaurant, hair/barber, beauty/spa, hotel/B&B, and medical/dental. Keep benchmark output separate from production capacity claims.

**INF02 (original-file storage) is postponed and is not a V1 beta gate.** Current B02 discards uploaded originals after processing; the founder decision is not to retain original PDFs or offer original-file downloads in V1; replacement and re-indexing require a new upload. Any future storage proposal needs separate review against B02, B04 commercial copy, A06 deletion/retention, C05 legal policy, and C02 backup/security/cost controls. R2 remains only a candidate if that decision changes.

**File quota direction is approved but inactive pending benchmark and certification.** The direction is 100 MB aggregate / 10 MB per file; 1 GB / 25 MB; and 5 GB / 50 MB. Do not enable or present these as active entitlements until the required benchmark and certification complete. Trial quota of 20 MB aggregate / 10 MB per file remains proposal only. The former 5 / 15 / 40 MB aggregate and 1 MB trial figures are superseded. B02’s missing/NULL limit remains fail-closed for new file growth. No commercial document/page count limit is approved here.

## 7. Dependencies and recommended sequence

| Order | Work | Depends on / gate |
|---|---|---|
| 1 | **Done:** reproduce/fix B02 replacement and add PostgreSQL/DOM regressions. | Integrated in #132; preserve source authorization, quota and fencing. |
| 2 | **Done:** founder-authorized normal merges through #142; exact heads, post-merge CI and image index digests are in the checkpoint register. | Integration and CI are not operational certification, deployment or issue closure. |
| 3 | **Done:** current main is `06388f4f0c88a937d125ce227d2d54d13a2d8979`; post-merge CI and actual multiarch registry manifests are verified. The `800c56c` state remains the dated previous checkpoint. | #93 and other operational gates remain distinct from merge/publication. |
| 4 | Verify #121 recovery with attempt-specific evidence; preserve the blocked original attempt until it can be safely reconciled. | PR #141 implements only one exact recovery pair; no generic aliasing and no provider-wide or original-attempt certification. Keep #88/#121 open. |
| 5 | Complete C02 whole-host capacity, backup/restore/monitoring and C05 legal/privacy/provider review. | INF01 local evidence informs sizing but does not certify a host; C05 gates A06 and any original-file retention promise. |
| 6 | Authorize C01 remote inventory/migration certification and complete the C02 operational plan. | Named environment, explicit scope and approved budget. Do not infer remote state from local CI. |
| 7 | Verify existing B02 dashboard replacement behavior. INF02 remains postponed; V1 retains no original files and offers no original downloads; replacement/re-indexing requires a new upload. | Success/failure/retry/concurrent/revoked replacement evidence is a beta gate. INF02 is not a beta gate. |
| 8 | Certify full B01 Calendar functionality and authorized Meta/Google flows through C03 in identified staging. | Calendar is optional per tenant; complete staging certification is still required before beta. C01 plus authorized accounts/recipients and traceable E2E evidence. |
| 9 | Complete A06 deletion/retention and backup propagation evidence. | C01, C03 where applicable, and C05. |
| 10 | Verify Stripe Test endpoint delivery and approve Live pricing under C04. | Preserve earlier A02 evidence; complete the missing public endpoint delivery/verification under explicit authorization. |
| 11 | Complete phase gates, then D01 integrated release review and explicit beta go/no-go. | Required P0 gates, per-sector A05 acceptance, Calendar certification, security, restore, cost/kill-switch, monitoring, legal, and rollback. |
| 12 | D02 onboard five businesses, one each from restaurant, hair/barber, beauty/spa, hotel/B&B, and medical/dental, and monitor. | D01 approval, selected participants, per-sector A05 acceptance, support/incident readiness. |

Avoid starting multiple broad implementation PRs at once. INF01 can provide decision evidence while #121 and C05 progress; INF02 remains postponed and should not implement original-file retention promises in V1.

## 8. Qualitative PR sizing

| Candidate | Relative size | Notes |
|---|---|---|
| Integrated #132 | **XL** | Broad existing B02 package, including the reviewed replacement correction. Completed technical review and merge; future work stays in separate, smaller PRs. |
| #121 recovery operations and attempt-specific reconciliation | **S–M** | PR #141's exact-pair local implementation is integrated; keep any authorized provider operations and original-attempt reconciliation separately scoped. |
| INF01 operational capacity evidence | **M** | #139 implementation and local diagnostics are integrated; remaining work is whole-host/C02 and real workload certification, not another unbounded code package. |
| C01 inventory/migration evidence | **S–M** | Primarily environment-specific evidence and controlled migration work; remote actions require explicit scope/authorization. |
| C02 operational setup | **M–L** | Depends on approved budget/host and backup/restore/monitoring requirements. |
| INF02 storage design | **S–M** for design; **L** for implementation | Postponed; not a V1 beta gate. No original-PDF storage promise is in scope. |
| B04 UI/copy follow-up | **S–M** | Requires approved commercial limits and truthful usage semantics first. |

## 9. Real P0 blockers and founder decisions

Explicit open P0 blockers: (1) B02 operational certification, including dashboard success/failure/retry/concurrent/revoked replacement evidence (replacement semantics are implemented in #132); (2) attempt-specific #121 recovery and A03/#88 certification; (3) C02 whole-host capacity, backup, monitoring and restore; (4) C01 remote staging schema and historical-row handling; (5) full C03 Meta/Google E2E including Calendar certification; (6) C04 public Stripe endpoint delivery/verification and pricing approval; (7) C05 legal/provider policy and A06 deletion/retention evidence; (8) per-sector A05 acceptance for the five selected sectors; (9) approved quota direction remains inactive pending benchmark and certification. INF02 original-file storage is postponed and is not a V1 beta gate; no original-PDF retention or original download is offered; re-indexing requires a new upload. Replacement by uploading a new file does not require original-file storage.

1. Keep the approved aggregate/per-file quota direction inactive pending benchmark and certification: 100 MB aggregate / 10 MB per file; 1 GB / 25 MB; and 5 GB / 50 MB. Trial 20 MB aggregate / 10 MB per file is proposal only. Until gates complete, new file growth remains fail-closed.
2. The founder selected exactly five D02 sectors: restaurant, hair/barber, beauty/spa, hotel/B&B, and medical/dental. Each beta business is from a different sector. Define and evidence A05 rules per selected sector; only restaurant/table booking with 60-minute slots is currently authorized for automation. Other sectors are not certified; unsupported/ambiguous requests use human handoff.
3. Select the authorized provider/account and recovery owner for #121; do not release A03 based only on local tests. Preserve the original blocked probe unless attempt-specific reconciliation is independently authorized and evidenced.
4. Identify the staging project and approve the exact inventory/migration scope for C01.
5. Approve C02 budget and operational target, informed by INF01 local capacity measurements.
6. Approve the legal/privacy/processor wording and retention policy under C05 before claiming A06 complete. In particular, resolve the draft DPA's 90-day physical-deletion promise against the runtime's policy-gated redaction; do not activate a policy by implication.
7. V1 will not retain original PDFs; INF02 is postponed and is not a beta gate. Do not promise original-file retention or downloads; require a new upload for replacement or re-indexing. Any later change requires separate authorization and design review.
8. Keep Calendar optional per tenant but require complete C03 staging certification before D01 beta go/no-go. Verify the dashboard replacement success/failure/retry/concurrent/revoked paths against existing `index_intent` behavior; this is an operational acceptance gate, not a missing core feature.
9. After those gates, make an explicit D01 beta go/no-go. No production deployment or onboarding authorization is implied by this register.

## 10. Checkout note

This register records integrated main
`06388f4f0c88a937d125ce227d2d54d13a2d8979`; the former
`800c56c0b631b81b7bbefe0f51c843dd13d498d1` main is retained as the dated
25 October checkpoint. PR #142 is integrated; its CI and publication evidence
are recorded in the checkpoint register. This documentation update does not
change the recorded main commit or imply activation.

## 11. Historical B02 correction cycle (24 October)

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
see section 12 for the historical B02 exact-head and post-merge links. No overlapping counts are summed.

At the time of this historical entry, founder GO authorized only the technical
merge. The current issue states and remaining staging, provider, INF01/INF02,
commercial and legal gates are recorded in the sections above. Only the task's
dedicated PostgreSQL container was stopped after testing; other local Supabase
services were left running as instructed.

## 12. Source links

- Founder-supplied roadmap `MELPIS_V1_ROADMAP_COMPLETA_2026-10-24 (5).md` and the accompanying coordination mandate (planning inputs; live GitHub is authoritative for repository state).
- GitHub issues: [#85](https://github.com/Scontismaart/Melpis/issues/85) through [#102](https://github.com/Scontismaart/Melpis/issues/102), plus [#121](https://github.com/Scontismaart/Melpis/issues/121).
- GitHub PRs: [#103](https://github.com/Scontismaart/Melpis/pull/103), [#119](https://github.com/Scontismaart/Melpis/pull/119), [#120](https://github.com/Scontismaart/Melpis/pull/120), [#122](https://github.com/Scontismaart/Melpis/pull/122), [#123](https://github.com/Scontismaart/Melpis/pull/123), [#130](https://github.com/Scontismaart/Melpis/pull/130), [#131](https://github.com/Scontismaart/Melpis/pull/131), [#132](https://github.com/Scontismaart/Melpis/pull/132).
- Approved #132 head: [CI 40111907116](https://github.com/Scontismaart/Melpis/actions/runs/40111907116), [Migration 40111907091](https://github.com/Scontismaart/Melpis/actions/runs/40111907091).
- Integrated #132 main: [CI 40114921956](https://github.com/Scontismaart/Melpis/actions/runs/40114921956), [Migration 40114921848](https://github.com/Scontismaart/Melpis/actions/runs/40114921848).
