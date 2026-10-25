# OpenRouter: exact canonical-model recovery binding

## Defect and bounded correction

The captured Free request used `openrouter/xingchen-agi/xing4.0-29b-a4b:free`.
Its authenticated generation lookup returned
`xingchen-agi/xing4.0-29b-a4b-20261015:free`. The generic identity matcher safely
rejected this previously unverified relationship, leaving accounting blocked.

Public, unauthenticated catalog GETs on 2026-10-25 now provide two independent
fields linking the identifiers:

- [Model record](https://openrouter.ai/api/v1/model/xingchen-agi/xing4.0-29b-a4b:free):
  `id=xingchen-agi/xing4.0-29b-a4b:free`,
  `canonical_slug=xingchen-agi/xing4.0-29b-a4b-20261015`.
- [Endpoint record](https://openrouter.ai/api/v1/models/xingchen-agi/xing4.0-29b-a4b:free/endpoints):
  `name=Phala | xingchen-agi/xing4.0-29b-a4b-20261015:free`,
  `model_id=xingchen-agi/xing4.0-29b-a4b:free`, prompt/completion prices zero.

The timestamped responses and raw-response digests are retained in
[the catalog receipt](evidence/openrouter-xing-canonical-20261025.json).
Its complete file SHA-256 is
`94d4db5f5a64d03c63dd64258047f94549284e6b30ce917e667a315cf1a2372e`.
The dated `:free` identifier itself returned 404 from the model-record endpoint;
that lookup alone is therefore insufficient. The alias endpoint record supplies
the exact returned `:free` identity. No inference POST or authenticated
generation GET was made in this correction cycle.

## Accounting and security boundaries

Only this exact requested/actual pair is added, and only to recovery. The
generic model matcher and live generation policy remain unchanged. Both the
verifier and finalizer independently check the pair and require a verified
provider receipt with exactly zero USD cost. No arbitrary dated suffix, paid
variant, alternate provider, or client-supplied mapping is accepted.

Today's catalog is identity evidence, **not historical list-price evidence**.
The charge for the individual generation comes from its authenticated receipt;
zero USD is also zero EUR without an exchange-rate assumption. The actual
dated model and native tokens remain unchanged. Persisted accounting and audit
evidence include the mapping receipt digest and
`pricing_source=openrouter_generation_receipt_zero_cost`. Nonzero or unknown
cost for the newly mapped identity is rejected.

Tenant, membership, recent MFA, generation/attempt/external-user binding,
request ID, timestamp, lease/fencing and append-only audit checks remain in
place. Recovery does not replay a provider POST or a generated answer. A04
reservations and commercial counters are not modified by this correction.

## Certification scope

Regression tests replay the already captured generation metadata locally and
exercise the finalizer against disposable PostgreSQL. The original synthetic
attempt's `created_at` was recovered by a read-only query of its retained local
ledger: `2026-10-25T05:01:57.617982+00:00`; the generation metadata timestamp is
`2026-10-25T05:01:58.566Z`. No attempt timestamp needs to be invented.

These tests are offline replay of real captured fields, not another live
provider E2E or proof of a universal metadata visibility SLA. The prior
404/404/200 sequence remains an attempt-specific observation. The original
uncertain ledger/reservation is retained unchanged. Keep #88 and #121 open
pending review and explicit operational certification. Groq remains uncertified.
No migration, deployment, paid service or provider configuration is required.
