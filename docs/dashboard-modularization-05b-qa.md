# Dashboard CSS modularization 05B QA

## Scope and byte contract

The original `web/style.css` remains the checked-in runtime URL and the canonical byte-for-byte golden from main commit `f466cf82c79ce176795c995d75ca8693dc347160`. Its Git blob is `958b0ee5939cfbcbb1ad3fc796137a9e3d181b0e`; the raw content is 364,962 bytes / 13,707 LF lines, SHA-256 `cb1c181a4e617c43998b62ebde1b2c57ca77f56b4c297fe1ccf44a9fb5ae1fa1`. It has no UTF-8 BOM or CR bytes. The 11 ordered `web/dashboard-css/NN.css` segments are contiguous byte cuts from that original sequence; concatenation is exactly the same 364,962 bytes and SHA-256. No CSS rules are reordered or normalized.

| Part | Lines | Byte offsets (end exclusive) | Bytes | Existing sequence (descriptive only) |
| --- | ---: | ---: | ---: | --- |
| `01.css` | 1–417 | 0–12,122 | 12,122 | Foundations, tokens, toast/skeleton, initial app shell/sidebar |
| `02.css` | 418–1,113 | 12,122–26,619 | 14,497 | Settings and account base |
| `03.css` | 1,114–2,280 | 26,619–50,385 | 23,766 | Main/topbar/views, overview and chat |
| `04.css` | 2,281–3,605 | 50,385–82,811 | 32,426 | Reviews |
| `05.css` | 3,606–4,598 | 82,811–107,070 | 24,259 | Bookings |
| `06.css` | 4,599–6,046 | 107,070–135,903 | 28,833 | Reports, onboarding, docs/knowledge and AI setup |
| `07.css` | 6,047–8,286 | 135,903–186,202 | 50,299 | Inbox core, states and existing overrides |
| `08.css` | 8,287–10,137 | 186,202–229,921 | 43,719 | Dark theme, integrations, audit/subscription/security |
| `09.css` | 10,138–11,378 | 229,921–280,214 | 50,293 | Team/collaborators |
| `10.css` | 11,379–12,280 | 280,214–299,808 | 19,594 | Later Inbox Graphite overrides |
| `11.css` | 12,281–13,707 | 299,808–364,962 | 65,154 | Later dashboard shell overrides |

On Windows, the initial checkout was autocrlf-smudged (378,669 bytes, SHA-256 `fe6a38633a14779a9b6e4a226ce3a0edd44e9743888464e122872d412336ce18`). The 05B checkout was then materialized from the verified `HEAD:web/style.css` Git blob and `/web/style.css -text` plus `/web/dashboard-css/** -text` attributes were added to preserve raw bytes. No global Git configuration was changed. The baseline Git blob and the checked-out file are distinct metadata from a checkout’s platform-dependent line-ending representation; generation itself uses raw buffers only.

## Composition and delivery checks

- `node web/scripts/compose-dashboard-css.mjs --check`: PASS; 11 parts, expected byte count and hash.
- CSS compositor tests: PASS, 8 passed / 1 skipped (Windows cannot create the symlink fixture; the symlink rejection test remains enabled on Linux).
- Final frontend suite (`npm run test:web`): PASS, 394 tests / 393 passed / 0 failed / 1 skipped.
- Targeted final CSS, baseline, and asset-audit tests: PASS, 14 tests / 13 passed / 0 failed / 1 skipped; `the composer rejects file symlinks and symlinked source roots` is skipped on Windows because file symlink creation may require elevated privileges. Actual directory and Windows-junction cases still run.
- `npm run lint:web`: PASS.
- `git diff --check`: PASS.
- Docker image build: PASS. The existing Node 24 dashboard-builder stage composes and `cmp`s both dashboard HTML and CSS against their checked-in goldens.
- Container HTTP check: PASS. With only synthetic legal-render data and a synthetic `wa_at` test cookie, `/app/style.css?v=ui-linear-dashboard-9` returned HTTP 200, `text/css`, 364,962 bytes, SHA-256 `cb1c181a4e617c43998b62ebde1b2c57ca77f56b4c297fe1ccf44a9fb5ae1fa1`. Temporary container was stopped and removed after the check.
- `npm run audit:assets`: PASS after teaching the asset audit that manifest-listed CSS source fragments resolve their relative URLs from the final `web/style.css` location; the new regression test confirms a missing final-path asset still fails closed.
- `npm run test:i18n`: PASS, 3,148 translation keys verified. `npm run audit:csp`, `npm run audit:links`, `npm run check:fullcalendar-css`, and `npm run check:dashboard`: PASS.
- Gitleaks was not installed on this host; no Gitleaks result is claimed.
- Local QA loopback servers use synthetic fixtures only. Main assets are an immutable snapshot of merge commit `f466cf82c79ce176795c995d75ca8693dc347160`; branch assets are from 05B. Both `/app/style.css?v=ui-linear-dashboard-9` responses are 200 and byte-identical to the canonical hash.

## Browser and visual checks

An earlier real-browser smoke covered desktop and mobile routes, sidebar/menu, Settings keyboard traversal, dark theme, back/forward/reload, scrolling, and booking dialog open/close focus restoration; its main and branch consoles had no warnings or errors. The current two-run capture exercise was separate and focused on strict screenshot comparison; it does not certify a new complete nine-route smoke. Browser evidence, screenshots, and metadata are local `.qa/` QA data and are not part of the intended source change.

The active V2 screenshot files and manifest were not changed. RGB comparison of the 12 original V2 JPEGs against main and branch found 11/12 exact matches; all 12 main/branch pairs match each other. The remaining `desktop-bookings.jpg` captures are not byte-decoded RGB-exact against V2: 7,235/1,296,000 pixels differ, bounding box `(248,64)-(1431,895)`, maximum channel delta 4, overall mean absolute channel delta 0.005752. Main and branch bookings captures match exactly, so no difference was introduced by the CSS split. The cause of the small V2-only difference is not proven. The user authorized a one-PR visual exception specifically for this CSS-only Block 5B change; this recorded 11/12 result applies only to that PR. V2 assets remain immutable, and no test or CI threshold was weakened.

Two new independent 12-case main/branch runs were then captured from fresh browser tabs. All 48 metadata records passed viewport, DPR, locale/time-zone, fixed-clock, dark-theme, language, font, and baseline-fixture checks. Run 1 passed RGB exactly: main↔branch 12/12 and both sets↔V2 12/12. Run 2 did not: main↔branch 10/12; the mismatches are desktop bookings (7,235 pixels) and desktop simulator (14,815 pixels), each with maximum channel delta 4. Run 2 main↔V2 and branch↔V2 are each 11/12. Cross-run same-host comparisons are also not stable: main run 1↔run 2 differs on desktop bookings (7,235 pixels), while branch run 1↔run 2 differs on desktop simulator (14,815 pixels). The mismatched pairs report the same JPEG dimensions, sampling mode, and quantization-table hash; this does not establish the cause of pixel variation. The CSS source and served-asset hashes are equal across main, branch, and Docker. Under the earlier acceptance criteria, this gate failure stopped delivery. The final, separate user authorization is recorded below and does not change the measured results.

The 05B change does not alter the stylesheet URL, HTML composition, runtime CSS, landing pages, auth/backend, or pricing. Browser screenshots, QA server logs, and generated artifacts are excluded from the intended change list.

## Direct investigation — 2026-10-12 (not certified)

A failing executable JSDOM regression demonstrated that the old bootstrap left `data-qa-ready=true` after a late DOM render. It also did not revoke readiness on pointer-induced transitions. The QA-only bootstrap now observes DOM changes, resize, scroll, focus and transition starts; it requires zero pending requests, loaded fonts/images, no running finite animations and three unchanged layout samples. The existing fixed clock and infinite-animation phase remain unchanged. No production styles or markup are edited.

Capture-mode experiment on the same loaded bookings document produced 185,196 different RGB pixels between full-page-plus-clip and native viewport capture. The viewport capture retains visible scrollbars; the old full-page capture has a different scrollbar/compositing result. This is evidence of a capture-path difference, not proof of its internal GPU implementation. New QA helper `scripts/dashboard-capture-protocol.mjs` validates readiness and dimensions and captures only the native viewport. Each certification case uses a fresh tab, closed after capture. No JPEGs are filtered, repaired or selected to manufacture a pass.

The installed browser runtime is WebView2 155.0.4283.45, headed, with GPU processes; no software-rendering flag was observed. Capture metadata reports Chromium 155, deterministic Italian locale/Europe-Rome timezone, the fixed clock, dark theme, Inter/Outfit, matching viewport/DPR and zero pending requests. Fixture server responses use `Cache-Control: no-store`. Local diff/overlay evidence and DOM/font/rectangle records are under `.qa/diagnosis/`, not committed.

Targeted isolated captures of bookings and simulator passed all three same-source repeats and main/branch comparisons with zero different pixels. However the subsequent complete certification still failed: run 1 main/branch **10/12** (mobile Inbox 204 pixels, mobile sidebar 1,132 pixels); run 2 **11/12** (desktop Inbox 29 pixels). The differences concentrate around SVG/icon and rounded-border edges. Same-source cross-run comparisons also differ for Inbox/sidebar. All 48 final records have no console warnings/errors. The remaining raster/capture non-repeatability has not been causally identified; a GPU or codec explanation is not claimed as proven.

Final frontend regression: **394 tests, 393 pass, 1 Windows symlink skip, 0 fail**. The two new capture/readiness regression tests pass. CSS/HTML composition, frontend lint, CSP/i18n and working/staged diff checks pass. CSS and V1/V2 remain unchanged. At the time of the initial stop, no commit/push/PR had been created because the then-mandatory exact-RGB certification failed. The final authorization below supersedes that delivery stop while retaining the failed visual result.

## Final authorized exception — 2026-10-12

The user explicitly authorized a visual-gate exception limited to this Block 5B PR. The automatic exact-RGB visual gate **did not pass**: the recorded independent runs were 10/12 and 11/12, and same-code captures also differed for Inbox/sidebar edges. The cause of the remaining raster variation has not been demonstrated. No claim of 12/12 visual equivalence is made.

The exception accepts the change for review based on the primary structural proof: the generated `web/style.css` is byte-identical to main at 364,962 bytes with SHA-256 `cb1c181a4e617c43998b62ebde1b2c57ca77f56b4c297fe1ccf44a9fb5ae1fa1`; the stylesheet served by the QA container had the same hash. HTML/CSS composition and Docker checks passed. The existing browser desktop/mobile smoke and the frontend functional suite passed. No CSS output, visual baseline, screenshot, test threshold, or shared CI visual gate was changed to conceal the results.

The historical V2 files and manifest remain untouched. Their previously recorded comparison remains 11/12, with desktop bookings as the V2-only discrepancy; this PR does not relabel that result as passing. The Windows symlink-fixture test is the single frontend skip because creating file symlinks may require elevation; Windows directory and junction rejection cases remain active. Gitleaks was unavailable locally and is not claimed as a local pass; the PR CI security scans remain required.

This exception applies only to Block 5B. It records a failed visual gate transparently and does not assert that raster instability was fixed or explained.
