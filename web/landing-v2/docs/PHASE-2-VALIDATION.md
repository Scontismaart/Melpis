# Melpis V2 Phase 2 validation

Phase 2 starts from integration merge `4f9f7af02b06e4be5b669d49820e262a198e6324` (Phase 1B PR #80 merge). The Phase 1B comparison tree is frozen at `dc3d51b97ffb16c0dd9120c36170446b2ab9792e`.

## Performance comparison

The reproducible paired harness is `tools/performance-phase2.cjs`. It runs both trees in fresh Chromium 151.0.7922.34 contexts at 320, 360, 390, 768, 1024, and 1440 CSS pixels wide, 900px high, DPR 1, CPU throttle 4×, three samples per width. Each capture waits for `networkidle`, `document.fonts.ready`, and 750ms of quiet. Values below are per-width medians. Body sizes use HTML `Content-Length` and CSS/JavaScript Resource Timing encoded body sizes from the loopback preview; they are not estimates of compressed wire transfer. The script records individual samples, LCP/CLS observer entries and sources, hydration timing, requests, post-interaction JavaScript, and view-switch response measurements in the ignored raw JSON artifact.

| Viewport width | Phase 1B LCP | Phase 2 LCP | Phase 1B CLS | Phase 2 CLS |
| ---: | ---: | ---: | ---: | ---: |
| 320 | 784 ms | 764 ms | 0.000 | 0.000 |
| 360 | 508 ms | 572 ms | 0.000 | 0.000 |
| 390 | 516 ms | 584 ms | 0.000 | 0.000 |
| 768 | 480 ms | 596 ms | 0.000 | 0.000 |
| 1024 | 564 ms | 652 ms | 0.000 | 0.000 |
| 1440 | 432 ms | 696 ms | 0.000 | 0.000 |

The first paired run found a mobile CLS of about 0.28. Source investigation traced it to the mobile header: server-rendered fallback links occupied a second row and the deferred menu script later collapsed them. A guarded, same-origin synchronous head bootstrap now applies the `mv2-js` class before layout only on this page; the expanded menu remains the no-JavaScript fallback. The final paired run recorded no initial layout-shift entries at any tested width.

The interactive preview is intentionally hydrated on load. Phase 2's median initial JavaScript payload, including inline script bytes, is 318,436 bytes versus 5,965 bytes in Phase 1B; after interacting with the demo Phase 1B reaches 229,199 bytes. Phase 2's six-view React showcase and GSAP animation account for the eager client code. The raw comparison records 60,283 bytes CSS versus 32,601 bytes in Phase 1B and 27,344 versus 22,567 HTML bytes. Initial request counts are 10 versus 5, and post-interaction counts are 10 versus 8. The added CSS includes the responsive showcase and new interaction states; obsolete hero illustration rules were removed.

Phase 2 hydration reaches enabled showcase controls in about 687–908ms from navigation across the six viewports; the controls are enabled before the showcase scrolls into view. The measured mean click-to-heading response while switching through all six views is about 67ms at mobile widths, 134ms at 768px, 151ms at 1024px, and 158ms at 1440px. This is a local browser measurement, not a field INP result. LCP medians are slower at five of six widths; the per-width values above are retained without presenting the redesign as a performance improvement.

Raw measurements and image/video review artifacts live under ignored `artifacts/phase2-review/`, including `performance-phase2.json`, `browser-qa.json`, `gallery.html`, three viewport screenshots, six desktop view captures, three mobile view captures, and `showcase-demo.webm`.

## Checks

- `npm run build`
- `npm run astro:check` — 13 Astro files, 0 errors, warnings, or hints
- `npm run check`
- `npm test` — 2 checks pass
- `npm run test:components` — SSR/no-JS fallback, all six views, selections, keyboard, mobile selector, and reduced motion
- `npm run test:browser` — 6 viewport widths, all six views, CSP/no external requests, Axe WCAG A/AA, responsive overflow, no-JS, reduced-motion, and screenshot capture
- `npm audit --audit-level=low` — 0 vulnerabilities
- `node tools/isolation.mjs` — validates the Phase 2 allowlist and protected-source boundaries

The Astro/React/check stack is pinned to Astro 7.3.8, `@astrojs/react` 6.0.2, React/React DOM 19.3.0, and `@astrojs/check` 0.9.10. Package metadata reports MIT licenses for Astro, the Astro React integration, React, React DOM, and `@astrojs/check`; TypeScript 6.0.3 is Apache-2.0; Playwright 1.62.1 is Apache-2.0; axe-core 4.14.0 is MPL-2.0. GSAP 3.15.0 package metadata identifies its standard no-charge license: <https://gsap.com/standard-license/>.

The CSP remains same-origin and hash-based for inline scripts, with no `unsafe-inline`, `unsafe-eval`, or `connect-src` allowance. The head bootstrap is a tiny same-origin external script; it makes no network calls or product data changes. Browser QA verifies no remote requests, CSP violations, console errors, or layout overflow. All dashboard content shown in the preview is synthetic and read-only.
