# Dashboard modularization — Block 4B Auth/Session QA

Source: main merge `19182e23e5264dd003e8d25c57d809caff7deb89`; V2 is the pinned `dashboard-02` baseline from main `3060374cbf4385f305626bde27dde696445e1143`. V1 and V2 images/manifests were not changed. Local `.qa/block4b/` captures and browser summaries are evidence artifacts and are intentionally not committed.

## Extraction

`web/dashboard-auth-transport.js` owns only the existing cookie/CSRF helpers and request transport (`leggiCookie`, `csrfToken`, `tentaRefresh`, `apiFetch`). The factory exposes only frozen `apiFetch` and `tentaRefresh` functions. `app.js` retains the session state/bootstrap, invalidation/logout, route/auth gate, organization selection, network banner, BFCache handlers, MFA/security UI, and private-shell reveal. `web/auth.js` and `web/mfa.js`, backend, CSS, and authorization policy are unchanged. The index loads the classic script once before `app.js`.

`app.js` is 2,124 lines versus 2,192 at the main baseline (68 net fewer lines; 97 removed and 29 added). Four existing top-level functions moved; no refresh single-flight or new session policy was introduced. Sixteen net frontend tests were added: nine transport/logout regressions, four actual-source `caricaSessione` cases, and three synthetic QA-fixture tests.

## Critical scenario map

| ID | Coverage and evidence |
|---|---|
| A | Authenticated first load — `auth-session-bootstrap.test.cjs`; browser authenticated routes. |
| B | Protected deep link — `auth-next.test.cjs`; browser login deep-link flow. |
| C | Unauthenticated protected route — browser login redirect and post-logout Back denial. |
| D | Expired session — `auth-expired` fixture returns 401 for `/auth/me` and refresh; browser redirects to login. |
| E | Refresh succeeds — rotated CSRF and single protected-request retry in `api-fetch-csrf-refresh.test.cjs`. |
| F | Refresh fails — no mutation retry; network failure invalidates and redirects. |
| G | Concurrent protected 401s — test characterizes the existing independent refresh attempts; no new coordination policy. |
| H | Logout — frontend test and desktop/mobile browser flows. |
| I | Logout while work is pending — deferred logout and late private response tests; session stays null and the shell stays hidden. |
| J | Revoked/anonymous session — bootstrap rejects 401/anonymous responses and does not reveal the private shell. |
| K | MFA requested — MFA-marked 403 callback opens Security only after the user action. |
| L | MFA completed — `mfa.test.cjs` enrollment/verification promotes AAL and clears setup data. |
| M | MFA unsatisfied — incorrect/expired code shows a safe error; no secret is logged or persisted. |
| N | Staff versus owner — existing Settings, AI Settings, Team, and modularization-contract role-denial tests. |
| O | Organization switch — browser Team selector changed QA A to QA B; subsequent members showed QA B. |
| P | Old-tenant response — existing Inbox/Reviews/Bookings/Knowledge/Settings/Integrations stale-scope tests. |
| Q | Back/Forward — router history tests and desktop/mobile browser history smoke. |
| R | Hard refresh — desktop/mobile browser reload smoke on dashboard routes. |
| S | BFCache — synthetic `pagehide`/persisted `pageshow` lifecycle tests pass; native BFCache restoration was not observed, so it is not claimed. Back after logout remained fail-closed. |
| T | Duplicate route listeners — router `start()` idempotence test; production bootstrap remains single-init and unchanged. |
| U | CSRF missing/invalid — mutation token rotation, missing-cookie stale-header removal, malformed-cookie fail-closed tests. |
| V | 401/403 — auth endpoint 401 does not recurse; permission 403 does not redirect; MFA 403 remains distinguishable. |

## Browser and visual results

Real browser QA used the existing loopback-only synthetic server. Desktop and mobile covered all nine routes (Overview, Inbox, Bookings, Reviews, Team, AI Simulator, Knowledge, AI Settings, Settings); login success/failure, expired session, protected deep links, organization switch, logout, reload, Back/Forward, and mobile menu/logout passed. Browser console errors/warnings: **0**. Native BFCache behavior is the limitation recorded in scenario S.

V2 comparisons used the same Codex in-app browser QA setup and pinned fixture data. Main vs V2: **12/12 exact decoded RGB**. Block 4B vs V2: **12/12 exact decoded RGB**, zero changed pixels in each pair. Desktop images are 1440×900 and mobile images 375×812. The runtime reported DPR 1.25 for this capture set versus approximately 1 for the original V2 metadata; despite the reported difference, direct same-size RGB comparisons were exact. No environment cause is inferred. V2's CI test validates artifact integrity only; it does not capture screenshots or perform RGB comparisons.

## Verification

Final frontend suite: **373/373 PASS**, zero failures/skips. ESLint, i18n (3,148 keys), CSP, local assets, links, and diff-check passed. Full Python is delegated to PR CI. No merge or deployment is authorized for Block 4B.

The shared test harness now loads the real transport module in the same order as the dashboard, and contract tests invoke the existing public `MelpisAPI.fetch` bridge. Existing assertions were retained. The V2 metadata integrity check normalizes only checkout CRLF to Git's LF before comparing the unchanged pinned hash; an additional assertion proves content alterations remain detectable. Baseline artifacts were not modified.
