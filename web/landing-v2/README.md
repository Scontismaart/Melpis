# Melpis Landing V2

Astro compiles the Italian homepage, pricing page and four sector pages into static HTML. React enhances the synthetic previews in the browser. The web image serves this output with Nginx.

- `/` and `/piani/` are canonical. `/landing-v2/` redirects to `/` and `/prezzi/` redirects to `/piani/`. V2 sector pages and assets use the `/landing-v2/` prefix.
- Access and registration use `/accedi/` and `/registrati/`. The web image keeps the existing localized pages, documentation, original sector routes and legal rendering.
- The generated CSP applies to the V2 documents. The demos use local assets and make no network requests. V2 pages carry noindex metadata and response headers and are omitted from the sitemap.

## Retained localized pages

The German, English, Spanish and French home and pricing pages are maintained directly under `web/landing/<locale>/`. The localization build checks their presence and continues to build localized sector, documentation and account pages, keep reviewed legal pages, and sync locale bundles.

## Verification

Use Node.js 24 and run `npm ci`, `npm test` and `npm run astro:check` from this directory. `npm run build` and `npm run check` compile static output and validate the generated policy.

The integrated workflow builds and launches the real web image on loopback with synthetic legal values, then runs `npm run test:routes`, `npm run test:browser` with `V2_QA_URL=http://127.0.0.1:4180/`, and `npm run test:pricing`. Chromium is installed with `npx playwright install chromium`. Screenshots and browser reports are written to the ignored `artifacts/` directory.

## Recovery and font licenses

The recovered local source is recorded in `571e0b28468d7686cceb2822382cf98cbbba17ff`, based on snapshot `9cf18e054c5585ca860648cf81ab2675a2814b50`. The recovery retains the historical review reports and local development material.

Inter, Outfit, Geist and Newsreader are served from `public/assets/fonts/` with their SIL Open Font License texts in `public/assets/fonts/licenses/`. The Newsreader OFL was retrieved from the pinned Google Fonts repository revision `991ce1de6075188e6b8977a5aa9fcd3610a4e946`.
