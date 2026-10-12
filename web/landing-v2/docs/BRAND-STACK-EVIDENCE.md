# Brand and stack evidence for Landing V2

Research date: 2026-10-12  
Reviewed source: PR 79 worktree at `8ce2c2d4d6a2ecd59689f49ae0a1170410ff91a4`.

This is a fact sheet for the V2 brand revision and a qualitative stack comparison. It records the files and official documentation reviewed; it does not authorize a stack migration or library installation. No transfer-size, gzip, Lighthouse, field-performance, or bundle comparison was measured for these alternatives.

## Brand sources and active cascade

`web/design-tokens.css` is the shared token file. Its root palette is monochrome: canvas `#090909`, surface `#171717`, raised surface `#222222`, ink `#fafafa`, muted `#c5c5c5`, subtle `#a3a3a3`, accent and focus `#ffffff`. It defines Inter/Outfit font stacks and shared radii and shadow tokens.

The official landing at `web/landing/index.html` links to `/style.css`, which the Docker image supplies from `web/landing/style.css`; it also links to `/design-tokens.css` through that landing stylesheet's `@import`. The final landing overrides in `web/landing/style.css` set the hero to near-black, use the monochrome token text, and explicitly set `.btn-cta-primary` to white with dark text and a white hover state. Earlier CSS comments and token names refer to lime/emerald, but those comments do not describe the final CTA rule. `--green-deep: #10b981` exists as a token, but the CTA does not consume it.

`web/style.css` belongs to the dashboard build (`web/index.html` links it under `/app/style.css`); the landing does not load it. That file has old and later `:root` declarations, multiple dashboard-specific green values including `--dashboard-accent: #10B981`, and a later dark-shell mapping that sets the dashboard accent from `--melpis-accent`. The CSS cascade matters inside the dashboard; these values are not evidence that the landing CTA currently renders green.

The canonical dark mark is `web/landing/brand/melpis-symbol-dark.svg`, a three-path SVG with `viewBox="0 0 200 144"` and silver/gray gradients. At the reviewed PR 79 baseline its SHA-256 is `714F9EB037919E51681BF6AC094140C0591B851E9F84C5E21E4DB377758B5BDE`. At that baseline V2's visible wordmark mark was drawn with CSS and its favicon was separate. The three V2 font files are locally named `mv2-outfit-latin.woff2`, `mv2-outfit-latin-ext.woff2`, and `mv2-inter-latin.woff2`; the reviewed V2 stylesheet references those local files and font family names scoped to the page. At this baseline each V2 font file is byte-identical to its corresponding source in `web/fonts/`: Outfit Latin `6C18D579FD87C3776BE068B762CBC83FDE3ACB543D49EABD3ADE842EB987E887`, Outfit Latin Extended `0F53D1C03B3918D744A843B5039001EE31695CA1E255E3914188DF81BEB461E9`, and Inter Latin `3100E775E8616CD2611BEECFA23A4263D7037586789B43F035236A2E6FBD4C62`.

The user has explicitly set `#10B981` as the desired emerald CTA intent for the revision. This is a product direction, rather than a claim about the current live landing CSS. Keep the dark foundation and the approved asset/font licensing decisions aligned to the V2 implementation.

## Stack comparison

The existing V2 baseline is static HTML/CSS with page-scoped vanilla JavaScript, built and previewed by Node built-ins. The three options below are assessed for this one static marketing surface and its small set of interactions, rather than for a future dashboard or application.

| Option | Official behavior documented | Qualitative fit and added operational work |
| --- | --- | --- |
| **A. Vanilla + GSAP** | GSAP is framework-agnostic and can be used from package managers or script tags. ScrollTrigger attaches to elements and supports scroll-linked triggers. The current Standard “No Charge” license permits commercial use and all GSAP plugins, with a restriction on visual no-code animation-building products that compete with Webflow. | Closest to V2's current architecture and static output. Keep native HTML interactions and CSS motion where they are sufficient; GSAP is available for a specific motion need without introducing a UI framework. A pinned package and its additional client code would still need to be justified and validated; no measured size or performance claim is made here. |
| **B. Astro + React islands + GSAP** | Astro components produce HTML/CSS without client runtime by default; only components marked with `client:*` hydrate. `client:idle` and `client:visible` choose when an island hydrates. The official React integration provides server rendering and client hydration for React components. GSAP can be used in React or other web frameworks. | Suitable if the site grows into many reusable static pages with a few interactive widgets. The landing's mostly static page could remain Astro HTML, with React restricted to widgets. It adds Astro, the React integration and React peers, framework configuration, build/preview changes, and a hydration boundary to maintain. That cost has no demonstrated benefit for the current single-page, static V2. |
| **C. React + Vite prerender** | React's static `prerender`/`prerenderToNodeStream` APIs generate static HTML; interactivity uses `hydrateRoot` to attach React behavior to the server-generated markup. Vite documents pre-rendering known routes using the SSR flow and describes its SSR API as low-level, with server/client entries and separate client and SSR builds. | Viable for a React-centered site, but the prerender and hydration pipeline must be deliberately wired and maintained. It introduces React runtime and a server-render/build path when the requested V2 is already authored as HTML with small vanilla interactions. React/Vite alone does not make those steps disappear; no comparative bundle or runtime numbers were measured. |

This is a qualitative fit assessment based on the documented mechanisms and current V2 source layout. It is not a measured ranking of page speed or output size.

## Brand revision and QA verification

After the PR 79 baseline, the approved V2 revision uses graphite `#07090A`, paper `#F7F7F5`, emerald action `#10B981`, silver `#D7D9DC`, and muted text `#AAB2C0`. These are page-scoped V2 variables and explicit component rules; the primary buttons use the emerald action, while the source mark remains silver. The header brand now renders the SVG as an image. Both `web/landing-v2/assets/mv2-symbol.svg` and `web/landing-v2/assets/favicon.svg` are byte-identical to the canonical dark mark: each has SHA-256 `714F9EB037919E51681BF6AC094140C0591B851E9F84C5E21E4DB377758B5BDE`, matching the original file. All three V2 font hashes listed above remain unchanged.

The dashboard CSS was also evaluated in a local, static browser fixture with the real token file loaded before the real `web/style.css`, `html[data-theme="dark"]`, and a dashboard `.app-shell`. The root's dark-theme tokens compute to `--bg: #07090A`, `--brand: #10B981`, `--accent: #F7F7F5`, and `--green-deep: #10B981`. Shared tokens resolve to `--melpis-canvas: #090909`, `--melpis-accent: #ffffff`, and `--dashboard-accent: #ffffff`. Inside `.app-shell`, the later scoped surface values compute to `--app-bg: #0b0b0b`, `--panel-bg: #111111`, `--view-bg: #111111`, `--surface-1: #111111`, and `--text-primary: #eeeeee`; a `.view` background computes to `rgb(17, 17, 17)`. Thus the dashboard keeps emerald semantic `--brand`/`--green-deep`, while its generic `--accent` and mapped `--dashboard-accent` are light neutral. This fixture evaluates dashboard CSS only and makes no claim about the V1 landing CTA.

The updated V2 passed `npm run build`, `npm run check`, and `npm test`. The browser suite passed all 97 checks in local Chrome and bundled Chromium. Chrome screenshots at 1440px and 390px, both hero and full-page, and the two browser result reports are in `artifacts/brand-review/`. Browser coverage includes the synthetic-demo disclosures, internal anchors, menu keyboard behavior, contrast, overflow, CSP, and preview noindex headers. This remains browser emulation and automated accessibility checking, not physical-device or screen-reader testing.

## Component families and license checks

| Family | Officially documented shape | Licensing evidence and review boundary |
| --- | --- | --- |
| **Magic UI** | A React collection of reusable components, blocks and templates for landing pages and marketing sites. Its catalogue includes heroes, grids, marquees, buttons, backgrounds, text effects and motion components. | The official `magicuidesign/magicui` repository declares MIT. Check the chosen component source and its dependencies before copying it; the library is React/Tailwind-oriented and a design library is not a reason by itself to add React to V2. |
| **React Bits** | A catalog of animated React components across text animation, UI, micro-interaction and background families; it publishes JS/CSS and TS/Tailwind source variants and copy/CLI installation. | The official repository license is MIT plus Commons Clause: commercial use is allowed, while selling, sublicensing, or redistributing the components themselves is restricted. Check dependencies and the exact component; avoid importing a component merely for its animation style. |
| **21st.dev** | A multi-author registry/catalog covering React components, marketing blocks, templates, themes, backgrounds and other interface families. The service describes components as source copied into a project and edited there. | Its current Terms (last updated 2026-07-20) state marketplace content belongs to its respective authors and 21st; they prohibit redistributing or republishing marketplace components elsewhere without a clear visible link to the original page. Individual author/code rights may also apply. Review the exact item license and Terms before copying; browsing a preview does not grant rights to its preview media. |

No component from these catalogs was installed or copied into V2 during this review.

## Official references

All links below are primary documentation or repository/terms pages, checked 2026-10-12.

- [Astro islands architecture](https://docs.astro.build/en/concepts/islands/) — static-by-default UI components and `client:*` hydration directives.
- [Astro React integration](https://docs.astro.build/en/guides/integrations-guide/react/) — React server rendering and client hydration integration.
- [React static DOM APIs](https://react.dev/reference/react-dom/static) and [prerender](https://react.dev/reference/react-dom/static/prerender) — static HTML prerendering.
- [React `hydrateRoot`](https://react.dev/reference/react-dom/client/hydrateRoot) — hydrate server-generated HTML.
- [Vite SSR guide](https://vite.dev/guide/ssr) — pre-rendering known routes and low-level SSR setup.
- [GSAP installation](https://gsap.com/docs/v3/Installation/), [ScrollTrigger](https://gsap.com/docs/v3/Plugins/ScrollTrigger/), and [Standard “No Charge” license](https://gsap.com/standard-license/) — loading and using GSAP; license effective 2025-04-30, modified 2025-05-30.
- [Magic UI introduction](https://magicui.design/docs) and [official MIT license](https://github.com/magicuidesign/magicui/blob/main/LICENSE.md).
- [React Bits official site](https://reactbits.dev/) and [official license](https://github.com/DavidHDev/react-bits/blob/main/LICENSE.md).
- [21st.dev component catalog](https://docs.21st.dev/) and [Terms of Service](https://docs.21st.dev/terms) (last updated 2026-07-20).

