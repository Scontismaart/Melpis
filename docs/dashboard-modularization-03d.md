# Dashboard modularization — Block 3D Inbox

Base: `main` at `81811bcc6da87e4788fd4fe5c8c9414c33fc4cfe` (PR72 merge commit).
This block extracts Inbox from the dashboard shell; it does not change backend routes or authorization.

## Extraction

`web/dashboard-inbox.js` owns 22 Inbox functions plus the language refresh body, view state, listeners and polling. `web/app.js` keeps a single factory instance and connects it to route entry/exit, session invalidation, organization changes, `pagehide`, and language changes. The classic script is loaded once after shared utilities and before `app.js`, and is included in the web image and development mount.

`web/app.js` measured 5,957 lines at the base and 4,852 after extraction (1,105 fewer). The new Inbox module is 1,248 lines. There are 27 production-factory tests and 8 synthetic fixture tests, in addition to updated shell/lifecycle contracts.

The factory preserves `apiFetch` as the request path and the backend as the authorization authority. Scope/session epochs gate asynchronous results; list/detail refreshes are single-flight, poll cleanup runs on route exit, invalidation and `pagehide`, and drafts and mutation locks are scoped to the ticket and organization. Replies are idempotent on retries. A response is reported as sent only when it contains a message ID and a confirmed delivery status; uncertain and failed sends retain the draft. Message text is rendered as text, links are limited to HTTP(S), and message IDs are never interpolated into selectors.

Invalidation now clears the visible search and filter chips, ambiguous-send feedback uses the existing locale key, and a mutation's replacement controls are re-enabled after a failed request. The moved rendering code also fixes a pre-existing variable-shadowing error that made the old Inbox fail when rendering an assigned name or failed-escalation warning.

## Verification

- Frontend suite: **315 passed, 0 failed, 0 skipped** (`npm run test:web`).
- Targeted Inbox, auth, router, lifecycle and security tests passed; the full suite is the final post-fix run.
- Ruff-equivalent frontend lint, syntax checks, CSP, i18n (3,148 keys), local assets, links, FullCalendar externalization, dependency audit and diff check passed. `npm audit` reported no vulnerabilities.
- Gitleaks scanned the reviewed diff: no leaks found.
- Twelve committed visual baselines compare pixel-for-pixel: **12/12 identical**. Six Inbox dynamic screenshots (populated list, channel filter, empty, error, mobile list and mobile detail) compare pixel-for-pixel against the base: **6/6 identical**.
- Chromium desktop and mobile smoke covered all nine dashboard routes, Inbox filters/search, claim, reply, assign, resolve, error/timeout/denied states, organization A→B isolation, Back/Forward, reload, and logout. The synthetic app emitted no console errors or warnings in the checked desktop session.
- No Python source/backend changes; full Python verification remains the pull-request CI gate.

## Provider and release boundary

QA fixtures use only synthetic in-memory organizations, tickets and messages; they do not contact WhatsApp, Instagram, Google, an LLM, production or staging. PR72 remains the only merge in this task. Block3D is to be committed, pushed and opened as a PR to `main`; leave it open. No deploy or merge is part of this block.
