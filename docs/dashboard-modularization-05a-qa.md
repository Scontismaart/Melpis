# Dashboard modularization 05A QA

## Scope and source baseline

This change composes the dashboard document from an entry shell and 17 declared HTML partials. The generated `web/index.html` is byte-identical to the dashboard HTML on the source baseline at commit `3060374...` (201,387 bytes; SHA-256 `e405629231283503b63f41391a8102dbc54aa85e4487bbb7d6c37124571a12b9`). The base PR #75 was already merged as `bae7abe3d169ae9110cb47a7663f88029188e497`; its CI run `38157424183` completed successfully, including GHCR amd64 and arm64 images and the multi-platform manifest. The baseline V1/V2 browser captures were left untouched.

The reviews partial retains several whitespace-only lines from the original document to preserve byte-for-byte output; its Git whitespace check is disabled for that file only.

In Docker, Node 24 composes the page and compares it with the committed HTML. Nginx serves only the generated document; dashboard source partials are not copied into the serving image.

## Automated checks

- Full frontend suite: 380 passed, 0 failed, 0 skipped.
- Dashboard composition suite: 7 passed, 0 failed, 0 skipped.
- Dashboard composition check and ESLint: passed.
- i18n checks (3,148), CSP, asset and link checks, Docker build, and HTTP/hash/404 checks: passed.

The build commands are `npm run build:dashboard` to regenerate `web/index.html` after source edits and `npm run check:dashboard` to verify that the committed output matches its sources. Future dashboard edits should be made in `web/dashboard-src/`, then regenerated and checked with those commands.

## Browser comparison

The browser certification compared six views on the source baseline and this branch at desktop and mobile sizes: overview, inbox, bookings, settings, AI simulator, and sidebar. All 24 image comparisons reported zero changed pixels. The actual capture setup used desktop 1440×900 at DPR 1.25 and mobile 375×812 at approximately DPR 1, in Italian with Europe/Rome time zone and the dark theme. Inter and Outfit were loaded. The fixed fixture clock was `2026-10-08T10:00:00Z`.

During capture, viewport setup and readiness produced transient captures that were repeated; only the corrected captures were used in the comparison. No specific environmental cause was established. Source V2 captures at commit `3060374...` and the existing V1/V2 evidence were not modified.

The interaction pass covered the nine routes on desktop and mobile, sidebar navigation, settings, browser back/forward and reload, inbox error state, booking dialog focus and Escape behavior, invalid-login denial, fresh valid login, booking deep links, and logout followed by reload. The checks passed with an empty console and no private content after logout.

## Local evidence

The detailed capture metadata, smoke records, and baseline/branch screenshots remain under `.qa/` as local evidence and are excluded from this change.
