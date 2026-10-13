# Phase 3 follow-up polish

## Scope and visual references

This follow-up continues the approved Melpis V2 page at `/landing-v2/` on the same branch and base as the Phase 3 review. It lowers the dashboard/showcase modestly and gives the hero atmosphere the full viewport width while preserving the existing anchor, title, copy, six showcase views, logo, favicon, fonts and product data.

The art direction uses restrained visual principles observed in the Resend landing page ([resend.com](https://resend.com/)): dark negative space and soft directional light around a product, adapted into original static CSS gradients and one faint elliptical filament. The Linear screenshot supplied in the conversation informed the restrained silver product rim and calm, editorial composition. These references are directional only: no reference bitmap, source code, typeface, image or other asset is copied. No asset or dependency is added; existing GSAP and fonts keep their prior versions. All decorative effects are static CSS; no shader, animation loop, canvas or remote request is introduced.

The conversation demo now uses a narrow non-interactive chapter rail with one `aria-current="step"`, one compact conversation stage with the existing Melpis symbol as the assistant avatar, and a concise operations column. Controls stay in the stage footer, keep their existing labels and accessible target size, and remain manual. The positive sequence asks for confirmation before showing the customer reply, runs the unchanged deterministic guard, then replaces the temporary availability card with the final compact booking row. The unavailable and missing-information outcomes remain fail-closed. Astro fallback, `client:visible`, local-only fixtures, CSP, reduced motion and keyboard focus behavior remain in scope.

## Final verification campaign

Campaign date: 13 October 2026. Validation is run once after all follow-up implementation and test updates. Prior Phase 3 results remain in `PHASE-3-VALIDATION.md`; this document records only this follow-up.

| Gate | Result | Evidence |
| --- | --- | --- |
| Build and Astro check | PASS | Final results and evidence below |
| Audit, static checks and booking guard tests | PASS | Final results and evidence below |
| Component and browser/Axe QA | PASS | Final results and evidence below |
| Keyboard, focus, reflow, reduced motion and no-JS | PASS | Final results and evidence below |
| CSP, network/storage and V2 isolation | PASS | Final results and evidence below |
| Visual screenshots and Sol review | PASS | Final results and evidence below |
| Security recheck | PASS | Final results and evidence below |

## Performance comparison

The current Phase 3 build is measured at six widths (320, 360, 390, 768, 1024 and 1440 px), 3 samples each, Chromium, fresh contexts, CPU 4×, DPR 1, 900 px height, ready fonts and 750 ms quiet interval. This follow-up records initial hero before scroll, deferred island added after story visibility, total JavaScript after all manual interactions, initial LCP and layout-shift traces. The retained prior Phase 3 baseline is 318,931 initial JS bytes, 325,348 after interaction and 6,417 deferred story-island bytes, including inline scripts and using encoded loopback resource bodies. These are not gzip estimates or field INP. The historical medians remain in the immutable committed Phase 3 report. The later artifact provenance correction and separate final current-only raw report are documented below.

Final follow-up measurements are recorded below.

Initial quiet-window layout-shift sums and the diagnostic raw `PerformanceObserver` entries are reported separately. Raw sums after scripted state changes are diagnostic only, not canonical session-window CLS. Only the exact current Phase 3 build is repeated; frozen Phase 1B/Phase 2 baselines are not re-run.

## Review artifacts

Raw browser, performance and rendered screenshots are stored locally under ignored `artifacts/phase3-review/`. Hero-stage screenshots cover six widths; the rendered story gallery includes the initial/intermediate positive states and the final positive, unavailable and team outcomes at desktop and mobile; a short local walkthrough records the manual progression. CI artifacts are produced by the existing isolated V2 workflow.

## Final results — 13 October 2026

| Gate | Result | Evidence |
| --- | --- | --- |
| Build / Astro check / static checks | PASS | 13 Astro files, zero errors/warnings/hints; built font URLs resolve from self-hosted public assets |
| Unit / component QA | PASS | Three tests; fail-closed guard, canonical logo, six showcase views, keyboard, SSR and reduced motion |
| Browser / accessibility | PASS | Six widths, three outcomes, Axe, keyboard/focus, no-JS, dynamic reduced-motion cleanup, no console/CSP or remote/API request errors |
| Mobile correction | PASS | Heading and copy stacked, full-width scenario select, three scenarios at 390/360/320, no overflow and controls >=44px |
| Audit / isolation | PASS | Zero vulnerabilities; 650 protected files byte-identical; V1, dashboard/backend and production config unchanged |
| Sol design / source / security | PASS | Actual rendered hero at six widths and three outcomes desktop/mobile; scoped reviewer P0/P1/P2 = 0 |

The preview at `http://127.0.0.1:4175/landing-v2/` was restarted after the overnight interruption. The first browser invocation could not connect; no browser assertions ran on that attempt. The successful campaign then used this explicit loopback URL, avoiding the already occupied port that interrupted the previous agent. The final visual review found a narrow two-column heading on mobile; only build/static and mobile scenario/Axe/reflow/captures were repeated after fixing those affected CSS rules. Desktop source and fixtures did not change. No Python tests or unrelated dashboard work were performed.

### Final current-build measurements

Definitions and conditions are the same as above. Historical comparison values are from the immutable validation report at commit `36ec6020ea0e3af15b7e32b542c02f2454440ded`; the final current-only measurement is `artifacts/phase3-review/performance-phase3-followup.json`.

| Width | LCP ms | Initial layout-shift sum | Story hydration ms | Raw post-interaction shift sum |
| ---: | ---: | ---: | ---: | ---: |
| 1440 | 480 | 0 | 33.9 | 0 |
| 1024 | 624 | 0 | 37.9 | 0 |
| 768 | 624 | 0 | 36.2 | 0 |
| 390 | 580 | 0 | 34.9 | 0.5083 |
| 360 | 508 | 0 | 32.4 | 0.4701 |
| 320 | 508 | 0 | 30.2 | 0.4686 |

Initial JavaScript including inline remains **318,931 bytes**. Deferred story island: **8,568 bytes**, versus 6,417 previously. Total after interaction: **327,499 bytes**, versus 325,348 (+2,151). HTML is 29,913 bytes (+1,380) and CSS 77,821 (+9,590). Request counts remain 11 initial / 12 after story; no new image, font or dependency. The initial window contains no layout-shift entries, so observed initial CLS is zero. Post-interaction observer sums are diagnostics of scripted geometry changes, not canonical session-window CLS; they are explicitly retained rather than claimed zero. Local headless timings, not field INP or proof of statistically significant speed improvement.

### Measurement provenance

One invocation mistakenly passed `--phase3-only` before that CLI option was implemented; the tool only recognized an environment flag and ran all three variants. That extra comparison reused `performance-phase3.json` and overwrote its prior local raw samples. Historical medians remain in the immutable committed Phase 3 report, but its original local raw sample file is no longer available. The extra pre-mobile comparison is retained as `performance-phase3-followup-before-mobile.json` and is not used as the historical Phase 3 baseline. The tool now accepts the documented CLI switch and rejects unknown options. The final run measured only the current build and wrote a separate follow-up JSON. No historical samples were reconstructed or invented.

### Visual artifacts

Hero: `phase3-followup-{1440|1024|768|390|360|320}-hero-stage.png` and corresponding viewport captures. Story: `phase3-followup-story-{available|unavailable|team}-{1440|390|360|320}-full.png` (360/320 are mobile follow-up captures). Initial/intermediate positive desktop captures and the local video `phase3-followup-story-walkthrough.webm` are retained. Decorative background is original static CSS; generated assets NONE. No competitor code or assets copied.

Delivery remains on `codex/landing-v2-phase-3`, without merge, deploy or Phase 4. GitHub connector PR permission is still unavailable; retain the compare/title/body fallback rather than retry the refused credential method.
