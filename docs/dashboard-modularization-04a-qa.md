# Dashboard modularization 04A QA record

Status: implementation, visual A/B, and local frontend checks are complete. Historical V1 remains mismatched; the user explicitly authorized a distinct V2 generated from immutable main after repeatability and A/B equivalence passed. No commit, PR, or deployment has yet been made.

## Scope and metrics

- Base: `3060374cbf4385f305626bde27dde696445e1143`.
- `web/app.js`: 4,852 lines at base, 2,192 after extraction (2,660 net fewer lines; diff is 2,711 removed and 51 added).
- The three new factories contain 72 named function declarations in total. Comparing original declarations in the base `app.js` against current declarations, 33 original named implementations were moved or reworked in the modules: 19 retain their original names and 14 are represented by renamed/nested declarations. The unused `caricaIntegrazioni` alias was intentionally removed. The 46 listener-registration sites currently in the modules are not reported as a count of anonymous handlers transferred, because some are new lifecycle/safety wiring and the old global `app.js` count is not a valid denominator.
- New focused test files: AI Settings, Settings, Integrations, and FullCalendar CSS checker. Synthetic Settings/AI fixtures are opt-in and accept only fixed fake identities/actions.

## Automated checks

- Full frontend suite: 357/357 passed after adding the active V2 integrity/provenance gate and QA-only environment metadata test. This is +42 tests net over the 315-test baseline (not a count of newly authored cases). Targeted Settings tests pass 15/15; targeted Settings/AI/fixture set passed 36/36.
- `npm run lint:web`, `git diff --check`, `npm run test:i18n` (3,148 keys), and CSP/assets/links/SEO audits passed. SEO: 60 pages, zero errors; links: zero issues.
- `npm audit --omit=dev`: zero vulnerabilities; package manifests unchanged.
- Offline Gitleaks scan of the final diff and new source/manifest/metadata text: no leaks found. `.qa/` screenshots remain local evidence and are excluded from the commit.
- FullCalendar generated CSS check passes with CRLF/LF normalization only; vendor CSS was not rewritten.

## Browser and visual evidence

- Root browser checks: desktop route coverage and mobile menu/routes passed without console errors; timezone save and synthetic AI save succeeded. Calendar disconnect dialog Escape/focus return passed on desktop, cancellation passed on mobile, and signed-out private Settings access was denied.
- The V2 capture used main A, main B, and 04A with independent fresh tab navigations and the same CUA/fixture/render setup. Exact decoded-RGB results: main A vs main B 12/12; main A vs 04A 12/12; main B vs 04A 12/12; dynamic main vs 04A 10/10. Every passing pair had zero changed pixels. Earlier three-pair static mismatches from a separate capture pass were not reproduced; one mobile Inbox file looked blurred relative to its main counterpart. Preserve both runs; no application code change was made to hide the earlier discrepancy.
- All ten dynamic A/B captures cover Settings and AI Settings populated/error states at both viewports plus full-page AI at both viewports. The four expected console HTTP 500 responses are injected AI error fixtures; no unexpected console errors or warnings were reported in the fresh capture runs.
- Historical `dashboard-01` is unchanged and records source commit `4f915578edfdb4b0224dd0566aaa5a17d7825c86`. Main at `3060374cbf4385f305626bde27dde696445e1143` still matches 0/12 historical images; the historical root cause is unknown. Previous `fullPage:false` and historical pipeline probes also failed, so no browser/font/GPU cause is asserted.
- Browser/runtime metadata is in the V2 `capture-metadata.json`: UA reports Chrome 155.0.0.0 on Windows/Win32; the host has WebView2 155.0.4283.45, but its exact mapping to the capture tab is unverified. Runtime DPR is 1.0000000149011612 (approximately 1), zoom 1, locale Italian, timezone Europe/Rome, dark theme, fixed clock `2026-10-08T10:00:00.000Z`. Loaded font faces are Inter and Outfit; exact font and CSS hashes are pinned in the V2 manifest. The QA-only bootstrap writes these values to `body[data-qa-environment]` after readiness; it reads no session or credential data and is not shipped in web assets.
- V2 lives in `tests/frontend/baselines/dashboard-02/`, is sourced only from main A at the immutable main SHA, and records second-series/A-B measurements. The `dashboard-01` manifest and JPEGs remain untouched. CI's active V2 test verifies manifest, source pin, image/metadata SHA-256, dimensions, runtime metadata and current font/CSS asset hashes. It is an artifact-integrity gate; CI does not run a real-browser screenshot capture or RGB comparison.
- The timezone read-flight fix prevents stale pending reads from suppressing a fresh load after same-scope route re-entry. A fresh Settings-error capture visibly shows the localized failure and disabled save control.
- The AI remove-rule literal template/ARIA label and untranslated Settings timezone fallback were fixed and covered by targeted tests. Root browser checks confirmed translated rule remove label, desktop dialog Escape/focus return, mobile cancel, and denial of private Settings when signed out.
- V2 is the authorized visual reference; V1 is archival evidence and remains failed. Do not claim the historical V1 gate passes.

## Security and dependencies

Changes are confined to dashboard client modules, shell wiring, tests, synthetic QA fixtures, and the stylesheet checker. Authentication/session implementation, backend authorization, MFA, CSP policy, and provider credentials were not changed. No dependency was added.

## Visual certification — 2026-10-11

- Historical V1: **0/12 exact** against immutable main; cause unknown. The V1 assets and manifest remain untouched.
- Authorized V2: **main repeats 12/12; main vs 04A 12/12; dynamic 10/10** strict decoded RGB identical. V2 captures are main-A only; the V2 manifest pins their image and metadata hashes, and CI checks artifact integrity.
- The earlier three-pair A/B mismatch was not reproduced in the independent fresh-tab series. Its capture variation remains recorded in `.qa/block4a-unblock/`; no production code change was made in response.
- Root security review approved the QA metadata path: no auth/session changes, secrets, CSP/API changes, or production rendering changes.
- Browser coverage remains as noted above. No merge, deployment, Block 4B, or backend/provider work was performed.
