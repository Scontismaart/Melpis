# Dashboard visual baseline V2

`dashboard-02` is the active CI dashboard baseline, captured from immutable `main` at `3060374cbf4385f305626bde27dde696445e1143`. The historical `dashboard-01` set is preserved unchanged. Its mismatch cause is unknown: a fresh run on immutable main matched V2 across two independent 12-image series, but matched none of the 12 historical images. V2 was separately authorized after main repeatability and main-versus-04A equivalence were demonstrated.

## Evidence and environment

- Main series A and B: 12/12 exact decoded-RGB matches. Main A versus Block 4A: 12/12. Dynamic Settings/AI Settings: 10/10.
- Capture metadata for each V2 image is in `capture-metadata.json`; `manifest.json` pins image and metadata SHA-256, dimensions, source revision, and environment. V1's manifest and images remain untouched.
- Codex in-app browser on Windows; browser UA reports Chrome 155.0.0.0 / Win32. A running Edge WebView2 155.0.4283.45 host runtime was observed, but its exact mapping to the capture tab is unverified.
- Desktop 1440x900 and mobile 375x812 CSS pixels. Runtime DPR reported `1.0000000149011612` (approximately 1); visual viewport scale 1. Locale `it`, timezone `Europe/Rome`, Italian, dark theme, fixed clock `2026-10-08T10:00:00.000Z`.
- Loaded font faces: Inter and Outfit. Body computed stack is Outfit; heading stack requests Geist Sans then Inter. Font and stylesheet SHA-256 values are in `manifest.json`.
- Dataset is the empty synthetic dashboard fixture with synthetic owner identity. No production API, credentials, database, or provider is used.

## Reproduction

Use the existing `scripts/dashboard-baseline-server.cjs` with `DASHBOARD_QA_INTERACTIONS=1`, `DASHBOARD_QA_VIEW_STATES=1`, `DASHBOARD_QA_ASSET_ROOT` set to a checkout of `main` at the pinned SHA, and a distinct `DASHBOARD_QA_PORT`. Capture the six routes and two viewports listed in `manifest.json`; for the sidebar case open the desktop account menu or mobile navigation drawer. Wait for `body[data-qa-ready="true"]`, then record `body[data-qa-environment]`. The QA bootstrap waits for font readiness, completes finite animations, and pauses infinite animations at time zero. Capture using Codex CUA `fullPage: true` clipped to the target viewport CSS dimensions.

Compare decoded RGB pixels with the existing offline helper, for example:

```powershell
py -3 scripts/compare_dashboard_baseline.py <baseline.jpg> <capture.jpg>
```

## CI scope

The active `dashboard-baseline-manifest.test.cjs` test verifies V2's manifest, source pin, per-image and metadata hashes, dimensions, runtime metadata, and pinned font/CSS assets. It is an artifact-integrity gate, not a real-browser screenshot capture or RGB-comparison job. The RGB equivalence values above are explicit local CUA evidence and are not presented as automatic visual E2E in CI.
