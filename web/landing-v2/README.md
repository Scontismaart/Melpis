# Melpis Landing V2 — Astro static page

An isolated static marketing-page experiment at `/landing-v2/`. Astro renders the layout and editorial sections to HTML; one existing synthetic control panel is a React island hydrated with `client:visible`. The page contains no account, backend, API request, message/booking submission, or tracking. Its initial copy, structure, and design follow the approved V2 baseline.

## Local commands

Use Node.js 24. From this directory:

- `npm ci --ignore-scripts` installs the pinned, local development toolchain.
- `npm run build` compiles the page into `dist/` and disables telemetry for the build process.
- `npm run check` validates output paths, generated CSP, static markup, font/license assets, and isolation requirements.
- `npm test` runs static and preview-server tests.
- `npm run test:components` checks React SSR, the no-JavaScript fallback, visible hydration, and both local state changes.
- `npx playwright install chromium` installs the isolated browser used by `npm run test:browser`.
- `npm run test:browser` runs browser, accessibility, keyboard, CSP, JavaScript-off, responsive, and page-isolation checks.
- `npm run performance` measures three fresh-context samples at six viewports against the local PR 79 baseline; it saves ignored screenshots and `performance.json` under `artifacts/phase1b-after/`.
- `npm audit --audit-level=low` checks all dependency severities.
- `npm run preview` serves only `dist/` on loopback at `/landing-v2/` (default port 4175; set `MV2_PORT` to choose another port).

The preview is a local QA server, not a production server. It uses the generated CSP hashes in both the document and response header, adds `frame-ancestors 'none'`, sets noindex headers, permits only GET/HEAD, and rejects traversal. It cannot reach a backend; the CSP sets `connect-src 'none'`.

## Structure and assets

- `src/layouts/` and `src/components/*.astro` provide the static layout and page sections.
- `src/components/ControlPanelDemo.jsx` is the only hydrated React island. It server-renders its initial state and is enhanced only when visible.
- `src/styles/landing.css` is local, page-scoped styling. `src/scripts/mobile-menu.js` retains the small vanilla menu behavior.
- `public/` holds stable, self-hosted browser assets, including all local fonts, their OFL texts, brand SVGs, and robots policy. The extended Outfit font is preserved with its license; the page's declared Unicode range does not require it for the Italian page copy, so it is not preloaded.
- `dist/` is generated inside this directory and is never deployed by these commands.
- `.astroignore` excludes future test files under `src` from Astro compilation; `.gitignore` excludes Astro's generated cache.

## Review records

- [Phase 1B validation and measurements](docs/PHASE-1B-VALIDATION.md)
- [Phase 1B architecture and security review](docs/SOL-PHASE-1B-REVIEW.md)
- [Brand and stack evidence](docs/BRAND-STACK-EVIDENCE.md)
- [Asset provenance and licenses](docs/ASSET-PROVENANCE.md)
- [Phase 1 architecture baseline](docs/PHASE-1-ARCHITECTURE.md)

The baseline and Phase 1B screenshots and raw measurements are ignored local artifacts; the validation record includes the source SHAs and exact capture conditions so the measurements can be reproduced.
