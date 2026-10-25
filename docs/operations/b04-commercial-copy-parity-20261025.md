# B04 commercial-copy parity — 2026-10-25

## Contract

- Essenziale, Crescita, and Scala remain €29/€69/€149 monthly with 500/2,000/10,000 AI replies respectively.
- PDF/RAG is stated consistently on all three paid-plan cards and in the trial copy. The trial is described as up to seven days or 150 AI replies, whichever limit arrives first.
- Google Calendar wording presents it as optional and requiring authorized configuration; it does not claim that an account is already connected or verified. Runtime plan capabilities and integration authorization were not changed.
- File quota remains to be defined. No monthly storage allowance, per-plan 10/25/50 MB promise, R2 retention promise, or commercial 50 MB per-file promise is introduced. The separate technical upload cap remains 20 MiB.

## Updated copy surfaces

The same plan features and trial headline key are synchronized across `locales/{it,en,es,fr,de}/pricing.json`, `web/locales/{it,en,es,fr,de}/pricing.json`, and `web/landing/locales/{it,en,es,fr,de}/pricing.json`. The dashboard account-plan cards in `web/dashboard-settings.js` now show PDF/RAG for every plan and no longer present document knowledge as Scala-only. Calendar copy is qualified as optional/configuration-required where it is listed. No onboarding files or B03-owned onboarding strings were changed.

The plan names, prices, monthly message limits, annual prices, backend entitlement mapping, and file-quota values were not changed.

## Regression checks

`tests/unit/billing/test_b04_commercial_copy_parity.py` checks all three locale trees and five languages for plan price parity, PDF/RAG on all plans, optional Calendar phrasing, the trial's seven-day/150-reply first-limit rule, and absence of proposed file-size claims. It also verifies the runtime plan catalog and that an active trial without an assigned plan maps to the RAG-enabled tier. `tests/core/billing/test_plans_pricing.py` remains the existing price/catalog contract test.

Focused local verification: 26 Python tests passed (copy parity and plan pricing); 40 frontend tests passed (dashboard settings, composition, and i18n); dashboard build/check passed; i18n verifier passed with 3,216 keys; ESLint on `web/dashboard-settings.js` passed. No provider calls or database connections were made. The full frontend suite was not run because this worktree has no local `jsdom`; the focused tests reused the already installed checkout dependency without installing packages.
