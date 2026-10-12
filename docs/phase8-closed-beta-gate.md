# Phase 8 — closed beta gate

Status: **Revocation/JIT micro-fix resolved / closed beta NO-GO**.
Phase 7 is incomplete and temporarily deferred. The access-revocation finding
is addressed by the additive lifecycle policy described below.
Baseline: `origin/main` `2383640889fd1ef12cd141e076caccf489ed2eaa`.
This separate branch does not contain unmerged Phase 7 changes. No deploy,
provider configuration, production credentials or real users are in scope.

## Deferred register

| Gate | Classification | Release boundary |
| --- | --- | --- |
| Calendar real consent/certification | BLOCKER CLOSED BETA | Disabled until Phase 7 certification |
| Business/Reviews real consent/certification | BLOCKER CLOSED BETA | Disabled until Phase 7 certification |
| WhatsApp and Instagram sandbox unavailable | BLOCKER CLOSED BETA for those channels | No real outbound activation |
| Airtable sandbox unavailable | DEFERRED BEFORE PUBLIC/PRODUCTION | No provider activation |
| Phase 7 final full Python rerun | BLOCKER CLOSED BETA | Phase 8 cannot certify unmerged Phase 7 |
| Email provider, DNS/domain | DEFERRED BEFORE PUBLIC/PRODUCTION | No deliverability claim |
| Calendar scheduler/Airtable reaper fixes | DEFERRED in unmerged Phase 7 | Known fixes are not imported into this branch |
| python-jose residual CVE-2026-85394 | Superseded by dedicated migration pending merge | See `docs/ai/phase5-hardening.md`; Dependabot alert #16 remains open pending default-branch reevaluation |
| Staging deployment and provider smoke | DEFERRED BEFORE PUBLIC/PRODUCTION | No deployment in this task |
| Backup/restore for real customer data | BLOCKER BEFORE REAL CUSTOMER DATA | `docs/DEPLOY.md` explicitly requires a verified encrypted backup/restore drill |
| Complete access revocation / JIT provisioning | RESOLVED | Durable server-only lifecycle prevents reprovisioning and gates old JWTs |

`roadmap-pubblicazione.md` is absent from this baseline. `docs/product/roadmap.md`
is a stub; the historical master launch roadmap is not current certification.
Use the explicit Phase 8 instructions, the current release runbook and recent
merged Phase 5/6 evidence. Phase 6 live MFA/auth evidence is retained; no human
MFA, provider consent or credential change is requested.

## Evidence

Autonomous checks and final results are recorded here before delivery. Passing
local checks does not prove a cloud deployment, provider consent, live channel
delivery, legal approval, production secret rotation or backup recovery.

### Scoped release corrections

- A TLS substring in a database password/fragment previously passed preflight.
  Preflight and production startup now share effective PostgreSQL URL parsing:
  one `sslmode=require`, `verify-ca` or `verify-full`; malformed/duplicate modes,
  missing database and default credentials are rejected without printing values.
- Production startup also rejects missing/unsafe Auth configuration, wildcard,
  HTTP or unapproved CORS/CSRF origins, and non-private/unbounded proxy networks.
  Origins must match `PUBLIC_APP_URL`; the two canonical Melpis HTTPS origins
  remain supported. Temporary hosts require their own explicit origin. Use the
  compose proxy subnet `172.30.0.0/24`, not an entire private address space.
- A Windows checkout reproduced web entrypoint exit 127 before legal validation.
  The web image now normalizes that shell script to LF during build. The normal
  nginx entrypoint is retained; missing legal configuration still blocks startup.

### Initial release-guard verification (before migration 059)

- Disposable PostgreSQL 16/pgvector: base schemas, triggers and all 55 ordered
  `0*.sql` migrations applied from zero and reapplied successfully; 41 RLS
  tables and 43 policies. No staging/production database used.
- Selected auth/MFA/AAL2, CSRF, tenant/IDOR, OAuth state, GDPR export/delete,
  retention, AI safety, webhook security, logging/audit and release tests:
  696 passed. Final release-only additions are covered by the final full suite.
- Recent merged live Auth/session/desktop/mobile/MFA certification is reused
  from `docs/phase6-authenticated-qa.md` (2026-10-06); no redundant human MFA run.
- Frontend: 132/132 DOM tests, lint, i18n (3,148 keys), CSP, assets, links, SEO,
  images and advisory hardcoded checks passed; npm audit high: zero findings.
- Python dependency audit: 201 packages; only exact-version/advisory reviewed
  residuals accepted at the time of this report. The later dedicated JWT
  migration removes python-jose and its audit exception; algorithm-confusion
  regressions remain required.
- Compose production syntax and both Caddy configurations validated with only
  synthetic/public configuration; no deployment or image publication.
- Existing offline bootstrap and backup/restore shell guard tests PASS; no VM,
  provider request or actual production restore was invoked.
- One final full Python: **2449 passed / 33 skipped / 0 failed / 0 errors**, 1117.70s.
  Skip audit: 20 explicitly opt-in live LLM tests; 13 provider sandbox tests
  (Airtable 3, Apaleo 3, SimplyBook 4, WuBook/ZaK 3). No Phase 8 test skipped.
- Ruff CI selectors, tracked Python `py_compile`, tenant guard and diff check PASS.
- Gitleaks main history PASS with the existing main-version exact fingerprint
  policy. An initial scan used the old primary-checkout policy and reported the
  three already-reviewed fingerprints; no exception was added or broadened.
- API amd64 and web amd64/arm64 built locally without push. API arm64 requires
  the native-runner PR CI build; local emulation initially hit a download timeout
  and was retried. Do not treat a pending architecture build as certification.
  API live/ready 200;
  unauthenticated API denied; all nine private deep links and private JS denied;
  public auth/legal pages 200; direct legal templates 404. Web tested non-root,
  read-only and with capabilities dropped using its normal entrypoint. Missing
  legal config blocks web; incomplete production config blocks API startup.
- Graphify: one successful update; optional SQL parser unavailable, non-blocking.

### Final revocation/JIT micro-fix verification

- Real PostgreSQL and cryptographically verified RSA JWT regressions: 17 passed.
  Same JWT after membership removal is denied; no organization/membership is
  recreated. Disabled accounts, OAuth, multi-org, concurrent JIT/disable,
  profile recreation, migration replay and direct authenticated-role RLS covered.
- Auth/security plus relevant tenant, AI and webhook tests: 601 passed.
- Full Python coverage: 2,506 unique cases. The logical run was resumed without
  repeating completed cases after a public embedding-model download stalled;
  the remaining run used the verified public cache offline. One legacy test
  expected RuntimeError instead of the new earlier PermissionError denial.
  Its exact expectation and zero-side-effect assertions were corrected, then
  registration/revocation tests rerun. Final coverage: 2,473 passed, 33 existing
  live-provider skips, zero unresolved failures/errors. PR CI runs the complete
  suite afresh on the committed tree; its result is the final release gate.
- Ruff CI selectors, Python compilation, tenant guard and diff check PASS.
  No frontend or dependency edits; standard CI reconfirms their existing gates.
- Micro-fix Graphify update attempted but extraction stalled; stopped,
  non-blocking. No staging/production or Phase 7 resources touched.

## Operational safety and objective stop criteria

The first permitted local cohort uses synthetic data only, at most **10 named
users**, and an operator-maintained roster. This is an operational cap, not a
new application invite feature. Before any externally accessible beta, verify
public signup is disabled at Supabase Auth and add only reviewed invitees.
The existing `/api/auth/register` is not itself an invite-only boundary.
No production/staging Auth settings were changed or certified here.
[Auth configuration](https://supabase.com/docs/guides/auth/general-configuration)
documents the provider-side control.

### Durable access removal / first provisioning (migration 059)

`auth_access_lifecycle` is server-only, keyed to the immutable Auth identity,
not the editable profile or a JWT/client flag. New Auth identities receive a
never-provisioned row. Every membership grant, including invitation acceptance,
marks provisioning atomically; membership/profile removal never resets history.
Existing identities are conservatively backfilled as previously provisioned:
the old schema cannot prove that a legacy identity with no memberships is new.
Migration replay preserves new-user, provisioned and disabled states.

All provisioning wrappers share one row-locked policy: enabled existing members
reuse their organization; enabled never-provisioned users can create one org;
previously provisioned users without memberships and disabled/missing-state
identities are denied. Removing one membership preserves other legitimate orgs.
OAuth and email-confirmation provisioning use this same repository policy.

For an operator, use the trusted backend capability
`await repo.disable_auth_access(auth_user_id)` with an explicitly verified QA/beta
principal UUID. This idempotently sets `disabled_at`, shares the JIT row lock,
and never deletes profiles, memberships, organizations or tenant data. No public
route or write RPC exposes this capability. On an authorized administrative DB
connection the equivalent parameterized operation is:

```sql
UPDATE public.auth_access_lifecycle
SET disabled_at = COALESCE(disabled_at, NOW()) WHERE auth_user_id = $1::uuid;
```

After commit, already-issued JWTs cannot access protected app routes, obtain app
login/refresh cookies, modify credentials/MFA through the app, or trigger JIT.
Missing state denies access. A restrictive profile RLS gate additionally denies
the direct Supabase tenant-data path; existing tenant policies resolve membership
through profiles. The own-subject read-only SQL helper cannot change state.
Client roles cannot edit the lifecycle or execute its mutation trigger functions.

This is an application/data cutoff, not deletion of the provider identity.
Supabase Auth ban/global logout remain optional complementary operator actions;
they are not substitutes for this durable policy. Requests already admitted before
disable commits can finish; JIT and invitation grants serialize with disable.
Do not clear lifecycle history to restore access or delete/recreate a profile.
Any reactivation requires a separately authorized operator review and explicit
membership grant; no automatic reactivation endpoint is added here.

CRM AI tools remain source-owned OFF and cannot be enabled by clients/prompts.
Tenant suspension/quota, budget/rate-limit kill switches and deterministic
mutation authorization remain the controls for allowed AI. No live model call
or provider side effect was needed for this gate. Keep Calendar/Business off,
and do not configure Meta/Airtable channels before their Phase 7 certification.

Incident triage uses trace/org/conversation/message identifiers, sanitized
structured logging, audit events, ready/live checks and existing error capture.
Before external users, assign an incident owner and verify deployed alert delivery,
secret-rotation evidence, controlled onboarding and legal approvals. Local tests
prove mechanisms, not their activation in an untested deployment.

**GO (technical):** all required local regressions/build/static gates green;
P0/P1/P2 release findings zero; tenant and AI controls green; no unreviewed
secret/dependency finding. **GO (closed beta):** additionally close required
Phase 7 gates and operational rollout evidence, including verified separate
encrypted backup/restore before real customer data. These remain blocking.

**STOP immediately:** cross-tenant access, secret exposure, privileged model
mutation, false booking success, duplicate side effects, failed revocation,
quota/accounting bypass, failed DB readiness/migration or unrecoverable data loss.
Stop beta traffic and new onboarding, suspend affected tenant AI and preserve redacted
trace/audit evidence. Do not re-enable until a targeted regression passes.

Rollback follows `docs/DEPLOY.md`: retain previous API/web digest references,
restore those references together, and do not remove additive migrations. A
failed migration requires stopped traffic and a verified restore. Artifact and
runbook checks do not certify a real host rollback or encrypted off-site recovery.
Do not serve authenticated traffic with pre-lifecycle application code after
adopting migration 059: that code does not enforce the durable revocation gate.
