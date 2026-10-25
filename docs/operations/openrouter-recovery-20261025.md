# OpenRouter recovery verification — 2026-10-25

## Later implementation update — PR #141

The one-POST probe and its blocked accounting state below are historical and
remain unchanged; the attempt was not reissued, retried, or reconciled by this
update. PR [#141](https://github.com/Scontismaart/Melpis/pull/141) is now
integrated at `cfe09e2214431f4015e3fa01972cd1f553ad736e`. It adds a recovery-only
binding for exactly `openrouter/xingchen-agi/xing4.0-29b-a4b:free` to
`xingchen-agi/xing4.0-29b-a4b-20261015:free`, based on separately captured
official public catalog/endpoints evidence. The provider-reported cost for the
specific authenticated generation receipt must also be zero; the finalizer
records the binding and pricing provenance. This does not change the shared
generic model matcher, accept arbitrary dated aliases, or make current catalog
pricing evidence of historical charges.

The integrated local verifier/finalizer path is implementation evidence, not
provider or staging certification. The original blocked attempt remains
unresolved, and A03/#88 and #121 remain open. No additional provider request
was made for this update.

## Recovery receipt review

The local receipt `artifacts/openrouter-20261025/recovery-result.json` records a provider generation lookup returning HTTP 200, followed by `ProviderReconciliationUnavailable`. The provider-reported model is the mismatching field:

| Field | Recorded value |
|---|---|
| Requested model | `openrouter/xingchen-agi/xing4.0-29b-a4b:free` |
| Generation metadata model | `xingchen-agi/xing4.0-29b-a4b-20261015:free` |
| Generation ID and `external_user` | Match the recorded attempt |
| Returned request ID | Present and syntactically valid |
| Expected request ID | `null`; no exact request-ID comparison is required |

`provider_model_matches()` strips the `openrouter/` prefix and accepts an exact identifier or the same identifier without the terminal `:free` suffix. It rejects the versioned model because `-20261015` is inserted before `:free`. The local matcher reproduces that result. Thus, the dated model mismatch is the first failing validation condition; the request ID did not cause this rejection. The receipt does not retain the exact `attempt_created_at`, so timestamp validation cannot be independently confirmed from the receipt alone.

The receipt’s catalog observation establishes that the requested base alias was listed with zero pricing. It does not record an authoritative mapping from that alias to the dated concrete model. A general dated-suffix rule would broaden model acceptance without that mapping. Even changing the verifier alone would not resolve recovery: `finalize_openrouter_recovery()` independently applies `provider_model_matches()` and prices from the requested alias.

The real, non-synthetic probe receipt records exactly **one POST** and generation GET statuses **404, 404, 200** (at offsets 0, 2, and 10 seconds). The final GET returned metadata for the same recorded generation and attempt, but the dated model identifier failed the verifier. Recovery was therefore **not certified**. The final state remains fail-closed: `ai_accounting_blocked=true`, `messages_used_this_period=0`, and no reconciled provider usage was committed. The receipt reports `provider_responses_synthetic=false`; this is one attempt-specific observation, not a provider-wide availability guarantee.

The read endpoint used by this existing receipt is documented by [OpenRouter's Get Generation API](https://openrouter.ai/docs/api/api-reference/generations/get-generation). No new provider requests are part of this review.

### Historical regression test proposal (superseded by PR #141)

Add a local verifier test with a fully valid generation record and the exact requested/returned model pair above; assert that current behavior rejects it. If an authoritative alias-to-snapshot and pricing binding is established, add tests for that exact binding at both verifier and finalizer boundaries, while confirming unrelated dated models, a wrong generation ID or external user, and an expected request-ID mismatch remain rejected. Keep the rejection as the safe outcome until the binding and accounting treatment are established.

## What this observation establishes

- Endpoint: the documented generation route returned 200 for the newly observed
  ID. The endpoint is not disproved by the initial two 404s.
- Availability: 404 at offsets 0/2 seconds and 200 at 10 seconds demonstrates
  delayed visibility for this attempt. It does not establish a provider SLA or
  the cause of every historical 404.
- Credential/correlation: the same authorized credential performed POST and
  GET; the generation and external_user matched the local attempt. No separate
  account-identity API was called. The historical ID could not be recovered
  from trusted sanitized evidence, so no historical GET was attempted.
- Namespace: `gen-` was observed from the actual response and returned by the
  read endpoint; its separate `req-` identifier was not substituted for it.
- Free models: this model exposed native prompt/completion token counts 17/32
  and total_cost 0. A blanket claim that all Free models lack metadata would
  be unsupported. Effective dated-model binding is still unverified.
- Recovery: one provider-attempt row remains uncertain and blocked; A04 stays
  reserved, with no commercial increment or automatic release. Successful
  reconciliation/idempotent finalization is **not certified** by rejection.

The one-POST budget and three new-generation GETs are exhausted. No extra call
was made to work around the mismatch. Next minimal authorized investigation
would be read-only authoritative alias/canonical-model and pricing evidence,
then offline verifier/finalizer tests against this already recorded generation.
Do not accept any arbitrary dated suffix or unlock an uncertain attempt merely
because total_cost is zero. Groq and A03 #88 remain separate open gates.

## Anonymous support draft — not sent

For an authorized Free/ZDR synthetic request, the generation metadata endpoint
returned 404 twice, then 200 after ten seconds. The requested model alias was
`xingchen-agi/xing4.0-29b-a4b:free`; metadata returned
`xingchen-agi/xing4.0-29b-a4b-20261015:free`. Can you identify the authoritative
alias-to-snapshot/pricing mapping and the documented visibility contract for
these metadata? A generation ID can be provided privately by the account owner.
No credential, customer content or account identity is included in this draft.
