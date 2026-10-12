# Melpis V2 Phase 3 validation

Phase 3 builds on the approved Phase 2 integration branch. It adds a static graphite/silver CSS atmosphere to the existing hero, adds responsive separation below its copy, and replaces the previous static Journey with an Astro-rendered fallback plus one `client:visible` React storytelling island. All examples use fixed synthetic restaurant data for Tuesday 13 October 2026. The local booking guard requires integer capacity at least equal to party size, verified availability, and explicit customer confirmation. No production application runtime, API, storage, cookie, provider, or LLM is used. There are no new dependencies or remote assets; the existing GSAP and font versions are unchanged from Phase 2.

## Final validation campaign

Campaign date: 12 October 2026. The campaign is run once after implementation is complete. Repeats are recorded only for checks affected by a correction.

| Gate | Result | Evidence |
| --- | --- | --- |
| Astro build | PASS | `npm run build`; repeated after scoped source/copy corrections |
| Astro check | PASS | `npm run astro:check`; 13 files, 0 errors/warnings/hints. First concurrent run hit a Vite EPERM rename; rerun after the build completed passed |
| Dependency audit | PASS | `npm audit --audit-level=low`; 0 vulnerabilities |
| Static output checks | PASS | `npm run check`; repeated after the final scene fields/layout changes |
| Static and booking guard tests | PASS | `npm test`; 3 tests, including 0/insufficient/unknown capacity and missing confirmation fail-closed |
| Component QA | PASS | `npm run test:components`; six showcase views, no-JS, keyboard, mobile and reduced motion |
| Browser QA | PASS | `npm run test:browser`; final run: 6 widths, all six showcase views, three story outcomes, reset/replay, keyboard, no-JS and reduced motion |
| Axe, keyboard, focus, reduced motion, no-JS, reflow | PASS | Browser QA includes Axe at all six widths and again after story interaction; no overflow at 320, 360, 390, 768, 1024 and 1440px |
| CSP, noindex, isolation and request checks | PASS | Hash-based CSP, robots/noindex, V2 path isolation, no CSP violations, no remote, fetch/XHR, or non-GET/HEAD requests; local/session storage unchanged |
| Six viewport visual review | PASS | Hero 1440/390; tablet 768; full story sections at desktop/mobile for all outcomes; six showcase views |
| Sol security and visual review | PASS | Security review: P0/P1/P2 = 0; hero and all three desktop story scenes approved. Mobile full-section captures delivered for final visual evidence |

Screenshots, raw browser JSON, the performance report, and optional video are captured under ignored `artifacts/phase3-review/`; they are review evidence, not distribution files.

## Performance method

The final campaign compares the frozen Phase 1B and Phase 2 worktrees with this Phase 3 worktree in Chromium using fresh contexts, CPU throttling 4×, three samples per width, 900px viewport height, DPR 1, `networkidle`, ready fonts, and a 750ms quiet interval. The browser records the initial hero before scrolling, then the showcase interaction, then the deferred storytelling island after it becomes visible, and finally all manual scenarios. JavaScript bytes include inline scripts separately and use encoded body sizes for external resources; they are loopback body measurements, not gzip estimates. Interaction timings are local browser timings, not field INP. For every triple below, values are ordered Phase 1B / Phase 2 / Phase 3.

| Width | Initial JS incl. inline (bytes) | Story JS added after scroll (bytes) | JS after all interactions (bytes) | LCP (ms) | Initial quiet-window layout-shift sum | HTML (bytes) | CSS (bytes) | Requests, initial→after story/interaction |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1440 | 5,965 / 318,436 / 318,931 | 0 / 0 / 6,417 | 229,199 / 318,436 / 325,348 | 444 / 620 / 624 | 0 / 0 / 0 | 22,567 / 27,344 / 28,533 | 32,601 / 60,283 / 68,231 | 5→8 / 10→10 / 11→12 |
| 1024 | 5,965 / 318,436 / 318,931 | 0 / 0 / 6,417 | 229,199 / 318,436 / 325,348 | 456 / 752 / 632 | 0 / 0 / 0 | 22,567 / 27,344 / 28,533 | 32,601 / 60,283 / 68,231 | 5→8 / 10→10 / 11→12 |
| 768 | 5,965 / 318,436 / 318,931 | 0 / 0 / 6,417 | 229,199 / 318,436 / 325,348 | 432 / 684 / 628 | 0 / 0 / 0 | 22,567 / 27,344 / 28,533 | 32,601 / 60,283 / 68,231 | 5→8 / 10→10 / 11→12 |
| 390 | 5,965 / 318,436 / 318,931 | 0 / 0 / 6,417 | 229,199 / 318,436 / 325,348 | 480 / 540 / 576 | 0 / 0 / 0 | 22,567 / 27,344 / 28,533 | 32,601 / 60,283 / 68,231 | 5→8 / 10→10 / 11→12 |
| 360 | 5,965 / 318,436 / 318,931 | 0 / 0 / 6,417 | 229,199 / 318,436 / 325,348 | 464 / 632 / 524 | 0 / 0 / 0 | 22,567 / 27,344 / 28,533 | 32,601 / 60,283 / 68,231 | 5→8 / 10→10 / 11→12 |
| 320 | 5,965 / 318,436 / 318,931 | 0 / 0 / 6,417 | 229,199 / 318,436 / 325,348 | 520 / 568 / 516 | 0 / 0 / 0 | 22,567 / 27,344 / 28,533 | 32,601 / 60,283 / 68,231 | 5→8 / 10→10 / 11→12 |

The median story-island hydration after scrolling was 32–43 ms (Chromium measurement). The island added one request after it became visible; its shared React/GSAP runtime was already loaded for the hero. Initial Phase 3 JavaScript is 495 bytes above Phase 2; total after every showcase and story interaction is 6,912 bytes above Phase 2. Phase 3 LCP changes versus Phase 2 are −52, −108, +36, −56, −120 and +4 ms at widths 320, 360, 390, 768, 1024 and 1440 respectively. The initial 750ms quiet-window layout-shift sum was zero at all widths. The separate diagnostic below reports raw observer sums after scripted interactions; those sums are not canonical session-window CLS. The measured CSS grows by 7,948 bytes and HTML by 1,189 bytes versus Phase 2. These values describe the measured local builds only; the 390px and 1440px LCP differences are small enough to reflect run-to-run noise. Request sequences in the last column are initial→after-story/interaction, ordered Phase 1B / Phase 2 / Phase 3.

### Mobile layout-shift diagnostic

A one-sample follow-up at 390×900, DPR 1 and CPU 4× used the same explicit showcase scroll for both frozen Phase 2 and Phase 3. This diagnostic sums `PerformanceObserver` layout-shift entries where `hadRecentInput=false`; it is not the CLS session-window metric. Both builds recorded zero at initial quiet and after the showcase entered view. The Phase 2 showcase interactions accumulated 0.3824; Phase 3 accumulated 0.4713 after showcase interactions, with no additional entries during story-island scroll/hydration or the positive scenario. Phase 3's unavailable and team scenarios then added 0.1334 and 0.0349 respectively as the synthetic result card and controls changed height. The trace attributes those entries to visible card, progress, disclosure, showcase-frame and trust-strip geometry. This is diagnostic evidence from scripted state changes; it does not indicate a load-time or island-hydration shift. The measured post-interaction totals are not presented as CLS. The original three-sample comparison remains the source for initial LCP and transfer measurements; the narrow diagnostic was not used to replace or extend that campaign.

Raw per-run data and resource URLs: ignored `artifacts/phase3-review/performance-phase3.json`. Hero “before” captures are `phase2-phase2-hero-1440.png` and `phase2-phase2-hero-390.png`; “after” captures are `phase2-phase3-hero-1440.png` and `phase2-phase3-hero-390.png`. Full story captures are `phase3-story-{available|unavailable|team}-{1440|390}-full.png`; tablet capture is `phase3-tablet-768.png`. Six showcase views and `phase3-story-walkthrough.webm` are under the same artifact directory.

`graphify update .` completed after the code changes. The graph tool reported that its optional `tree_sitter_sql` dependency is absent, so 59 SQL files were omitted; generated graph files remain local and are not part of the change.

The browser QA had two narrowly scoped assertion failures during implementation: a duplicated confirmation phrase needed an exact element locator; after the scene copy changed, the zero-capacity assertion was updated to match the explicit “no posti disponibili” state and the structured availability fields. Only browser QA was repeated for those changes; the final rerun passed with no remaining failures. Build/output/browser checks were also repeated after the final structured availability and inbox-row markup/style changes.
