# Phase 1B validation record

## Scope and integration

Phase 1B migrates only the approved Melpis landing experiment under `web/landing-v2/` to static Astro with one below-fold React island. Editorial copy, section order, visual direction, and above-fold structure stay aligned with the PR 79 baseline. Astro owns the layout and static sections; the existing synthetic control panel is server-rendered and hydrates with `client:visible`. No GSAP, animation framework, tracker, API, backend, booking action, or account flow was added.

The user authorized PR 79 only into the experimental branch, then the Phase 1B branch and a new Phase 1B pull request left open. PR 79 was open with base `codex/landing-v2`, exact head `e61ac6509173e5c2fbfd8d92cddae86d10c899b4`, mergeable, with its required exact-head check green and no review block. It was merged as a merge commit into `codex/landing-v2`, producing integration commit `13a1f20e29b6f33e04712cc5a1855033420efe43`. That merge SHA has no check runs: the dedicated workflow is scoped to phase branch pushes and pull requests targeting `codex/landing-v2`, so the merge commit itself did not trigger it. Do not describe its checks as green.

The isolated Phase 1B branch is `codex/landing-v2-phase-1b`, based on that integration commit. The reviewed source remains scoped to V2 plus `.github/workflows/landing-v2.yml`. Phase 1B is not merged; `main` and the live site were not changed; no deployment or Phase 2 work was performed. The new Phase 1B pull request is to remain open.

## Implementation and dependencies

The exact package pins are Astro `7.3.8`, `@astrojs/react` `6.0.2`, React/ReactDOM `19.3.0`, Playwright `1.62.1`, and Axe Core `4.14.0`. `@astrojs/react` 6.0.2 was selected to satisfy Astro 7's current optional Vite/React peer ranges without `legacy-peer-deps` or peer overrides. `package-lock.json` pins the full install. The package declares Node `>=22.12.0`; local verification used Node 24.15.0. All packages are build/test development dependencies.

Astro writes only to `web/landing-v2/dist/`; `base` is `/landing-v2/`. The Astro layout/components hold static page structure. The control panel uses local React state; it has meaningful SSR and a disabled, explanatory JavaScript-off fallback, then becomes interactive after visibility hydration. It allocates no timer, subscription, network request, or other effect resource. The mobile menu remains a small page-scoped vanilla module. Source and preview assets stay self-hosted in the V2 path. The three local fonts and their license texts are in `public/assets/fonts/`; Inter and Outfit Latin are preloaded, while Outfit Latin Extended remains licensed and served but is not preloaded for this page's current Unicode range. Vite's “absolute public asset did not resolve at build time; left unchanged for runtime” notices for these known font paths are expected; browser checks confirmed the font URLs return 200.

Astro's generated CSP hashes authorize the local inline bootstrap; the response header repeats the generated policy and adds `frame-ancestors 'none'`. The policy keeps `connect-src 'none'`, `base-uri`, `form-action`, `object-src`, and `frame-src` restricted, allows no `unsafe-inline`, `unsafe-eval`, or `strict-dynamic`, and permits same-origin local scripts, styles, images, and fonts. The preview runs on loopback, adds noindex headers, serves only the V2 build output, and rejects methods other than GET/HEAD and path traversal.

## Verification gates

The isolated workflow `.github/workflows/landing-v2.yml` remains read-only (`contents: read`), runs on Node 24, installs with `npm ci --ignore-scripts`, audits, builds/checks/tests V2, runs the React component QA and browser/Axe suite, and uploads ignored QA artifacts. It runs for `codex/landing-v2-phase-*` pushes and pull requests targeting `codex/landing-v2`; it has no deploy or publish step.

Local gates on the Phase 1B tree:

- `npm run build`, `npm run check`, and `npm test` passed.
- `npm run test:components` passed: server-rendered original state, no-JavaScript fallback, deferred hydration while offscreen, and both state transitions after visibility.
- `npm run test:browser` passed 133/133 checks across 1440, 1024, 768, 390, 360, and 320 CSS-pixel widths. Coverage includes Axe WCAG A/AA, keyboard/focus, mobile menu and JavaScript-off navigation, anchors, page overflow, local asset scope, CSP/noindex headers, no external browser requests, and page-style isolation. The six-width hero/full-page captures are in ignored `artifacts/browser-qa/` and `artifacts/phase1b-after/` directories.
- `npm audit --audit-level=low` reported 0 vulnerabilities.
- `node tools/isolation.mjs` reported 650 protected V1/dashboard/backend/production files byte-identical and 59 Phase files within the V2/workflow allowlist.
- The 1,036-file primary-checkout snapshot at `C:/Users/muzzo/.codex/worktrees/landing-v2-phase1/evidence/primary-checkout-sha256.json` matched the user's primary checkout with 0 mismatches. The primary checkout was read only.
- The root security review found no P0/P1/P2 issue. Browser checks verified the generated CSP, no CSP violations, no external browser requests, preview method/path restrictions, noindex, and output isolation. Dependency audit was clean. This was a limited source/configuration and automated-browser review, not a penetration test or production-host audit.
- `node --check` passed for the build, check, preview, performance, component-QA, and browser-QA scripts. `git diff --check` passed.

The V2 base integration commit had no CI run by design; the source PR 79 check was green at its exact head. The Phase 1B workflow will run against the exact branch/PR SHA after push/open. Do not claim those future checks passed before they run.

## Reproducible local performance comparison

The tracked compact PR 79 baseline is [PHASE-1B-PERFORMANCE-BASELINE.json](PHASE-1B-PERFORMANCE-BASELINE.json), captured from source head `e61ac6509173e5c2fbfd8d92cddae86d10c899b4`. The baseline and Phase 1B screenshots and raw run data are ignored local artifacts; no large screenshots are committed. Run `npm run performance` after `npm run build` to capture fresh after-migration samples, screenshots, and raw `performance.json` under `artifacts/phase1b-after/`. The script reads the tracked baseline JSON, reports the current Git head and dirty status dynamically, and measures the page before and after scrolling to the visible island.

Both captures used bundled Chromium `151.0.7922.34`, headless mode, fresh browser context per sample, 900-pixel viewport height, DPR 1, 4× CPU throttling, three samples per width, and the local uncompressed loopback preview. Each browser waited for network idle and `document.fonts.ready`; the baseline then used a 500 ms quiet interval and Phase 1B used 750 ms before its initial observation. This difference is a limitation of the lab comparison. LCP is the final buffered observer candidate and CLS is measured before any scripted scroll. Body sizes are `Content-Length`/Resource Timing body sizes, not transfer-wire estimates. The visible-to-enabled duration starts immediately before scrolling and ends when a `MutationObserver` sees the island's initially disabled control become enabled; it includes lazy bundle fetch and hydration, so it is not a React-only CPU duration.

| Width | Baseline LCP | Phase 1B LCP | Baseline CLS | Phase 1B CLS |
|---:|---:|---:|---:|---:|
| 1440 | 472 ms | 448 ms | 0 | 0 |
| 1024 | 444 ms | 412 ms | 0 | 0 |
| 768 | 448 ms | 552 ms | 0 | 0 |
| 390 | 492 ms | 452 ms | 0 | 0 |
| 360 | 428 ms | 448 ms | 0 | 0 |
| 320 | 448 ms | 676 ms | 0 | 0 |

These local medians show slower LCP at 320 px (+228 ms), 768 px (+104 ms), and 360 px (+20 ms); measured LCP was lower at 1440 px (−24 ms), 1024 px (−32 ms), and 390 px (−40 ms). This is lab data, not field Core Web Vitals.

| Initial resource measure | PR 79 baseline | Phase 1B |
|---|---:|---:|
| HTML body bytes | 15,276 | 22,567 |
| External CSS body bytes | 33,400 | 32,601 |
| External JavaScript bytes | 1,135 | 0 |
| Inline JavaScript UTF-8 bytes | 0 | 5,965 |
| Total request count, including document | 6 | 5 |

The HTML body includes Phase 1B's 5,965 bytes of inline JavaScript; that code is executed and is not “no JavaScript.” It is reported separately to distinguish it from external JavaScript requests. The compact baseline's `resourceBodyBytes` value (116,567) is all initial non-document resources, including its CSS and external script. Phase 1B's corresponding non-document resource total is 114,633 bytes (CSS plus self-hosted fonts/assets). The 223,234 bytes of JavaScript are requested only after the island becomes visible, over three same-origin script requests; across samples, visible-to-enabled medians were 252.4 ms (1440), 245.6 ms (1024), 259.0 ms (768), 253.2 ms (390), 230.3 ms (360), and 238.2 ms (320).

The isolated graph refresh was attempted after code changes. `graphify update .` refused to overwrite its existing generated graph because its current scan contained 268 nodes while the existing graph had 294. No force/purge option was used; generated graph output was left fail-closed for root maintenance.
