# #121 — Observed generation identity, not verified consumption

Baseline: `6a26813e9a0f52d25e78da55465744e3ca798c3a`.
Scope: minimal adapter acquisition fix and offline/disposable regressions. No provider E2E, merge, operational activation, migration, credential change or issue closure.

## Defect and contract

The sync and async adapters previously extracted body IDs only after validating model/usage. A valid OpenRouter `gen-...` ID present only in the body was lost if usage was missing, malformed or incoherent. The tenant remained fail-closed, but recovery lost its lookup reference. This is distinct from the historical GET 404: retaining an ID does not prove that endpoint can recover it.

- One bounded, strict JSON decode rejects ambiguous duplicate fields, malformed/non-object JSON and nonfinite constants before body identity is observed.
- Eligible provider HTTP status/encoding and existing size/deadline gates precede observation. Only the configured provider response, not client input or prompts, supplies these IDs.
- Header observations can be persisted before reading a body; async now streams like sync, retaining an eligible header ID on a subsequent body timeout. Incomplete or invalid JSON cannot supply body IDs.
- Body identity is observed before independent usage/model validation. `gen-...` is accepted only for OpenRouter's generation namespace; request IDs come from their explicit request fields/headers, never from a completion `id` fallback.
- Two valid but conflicting header/body values reject the response before overwriting the first observation. Existing header evidence remains unverified; no completion/usage finalization occurs on the conflict.
- ID persistence remains scoped to the existing organization, attempt, worker and lease epoch. ID observation never calls finish, creates token/cost values, reconciles a request or releases quota. Existing accounting/entitlement/fencing rules are unchanged.
- ID-persistence failure remains fail-closed; the exception path may retain already observed IDs through the existing failure-evidence mechanism. There is no second generative POST or automatic retry introduced by this fix.
- Errors remain sanitized; no response body, prompt, credential or personal content is logged or returned by the identity helper.

## Local verification

Before fix: the six body-only missing/malformed/incoherent usage cases fail on the exact baseline adapter loaded into an isolated pytest process, with valid streaming fixtures and parser outcomes asserted. No on-disk source swap/reset was used.

After fix, reviewed Python 3.12.5:

- New focused matrix: 40 unit cases and 2 PostgreSQL cases passed.
- Full unit regression: 1,596 passed, 1 skipped (86 warnings).
- Extended PostgreSQL A03 accounting, identity and A04 reservation-lifecycle suite: 75 passed. Includes the 2 new PostgreSQL tests; do not sum overlapping suites.
- Ruff critical checks across src/tests/scripts and git diff whitespace checks passed.
- Independent final security review found no confirmed P0/P1 in this adapter diff; real provider lookup remains outside certification.

Tests used synthetic credentials and local MockTransport with external sockets blocked. The dedicated synthetic PostgreSQL container used 1 CPU / 512 MiB and port 127.0.0.1:55494, then was stopped without removal. Existing Supabase containers and Docker Desktop were not changed. The first development fixture used an already-buffered mock response; that fixture was corrected and the baseline RED repeated with real streaming semantics before accepting these results.

Complete CI, Migration check, installed dependency audit and all four image builds must also pass on the exact PR head. Tests cover unusable usage, namespace separation, header/body conflicts, wrong model, strict JSON, non-2xx/encoding/size rejection, persistence failures, timeouts, repeated recovery, concurrent attempts and tenant/accounting isolation. MockTransport verifies a single synthetic generative POST per attempt; it is not a real provider E2E.

Windows test harnesses must permit asyncio's internal loopback socketpair while blocking external sockets. Mock provider responses must use an unconsumed byte stream rather than an already-buffered Response: otherwise StreamConsumed would stop before the intended parser branch. No production check is relaxed for test-fixture mistakes.

## Official contract and 404 hypothesis matrix

Documentation checked 24 October 2026:

- [Generation metadata GET](https://openrouter.ai/docs/api/api-reference/generations/get-generation): authenticated GET `/api/v1/generation`, required query `id` containing the generation ID; the response exposes identity and native usage/cost metadata and documents HTTP 404.
- [OpenRouter support](https://openrouter.ai/support): basic request metadata is distinct from opt-in prompt/completion logging.
- [SDK generation API](https://openrouter.ai/docs/client-sdks/typescript/api-reference/generations): generation lookup takes an ID, not a replacement generation request.

These references specify lookup shape; they do not establish this account's successful recovery, a publication delay SLA, metadata retention period, or a special guarantee for Free-model lookup. Unproven entries below remain hypotheses.

| Hypothesis | Current evidence / limitation | Smallest safe verification, only after separate real-provider authorization |
|---|---|---|
| Wrong endpoint/method/query | Local adapter and offline tests match the documented GET URL and `id` query. This excludes that construction error locally, not an old script/config error. | Compare sanitized host/path/query-field names from the original receipt; one authenticated GET for the exact observed ID at the fixed official origin. No redirects. |
| Metadata not yet available | No verified timing SLA or reason for the historical 404. Never interpret 404 as no consumption. | Reuse the same ID with a small, explicitly approved GET-only retry budget and bounded delays. Record status/timestamps; no replacement POST. |
| Different key/account/workspace | Local tests cannot identify the historical account or prove credential scope. Do not print keys or rotate/change settings to investigate. | Founder confirms Activity/account ownership for that generation and the approved credential source; perform lookup with the originating authorized credential in memory. Do not enumerate other accounts or transfer keys. |
| Response ID versus generation/request/completion ID | Code distinguishes the namespaces; the body-only loss bug is reproduced. This does not prove the historical saved ID was wrong. | Compare the exact observed response ID with the durable attempt ID field and selected non-sensitive metadata; never synthesize a `gen-` prefix from another ID. |
| Free-model behavior | A prior Free generation succeeded; its metadata lookup returned 404. No inspected metadata-GET contract establishes a Free-specific exception. Generation-routing 404s and metadata-GET 404s are different operations. | First retry metadata for the existing authorized Free ID. If a fresh controlled generation is necessary, obtain separate approval for one explicitly allowlisted Free request with cost ceiling zero; stop if unavailable. No paid fallback. |
| Visibility/retention/policy limitation | Prompt/completion retention and billing metadata are different. Applicable generation-visibility/retention rules are not yet verified for this account. | Review account/workspace metadata visibility with the founder and current official support documentation; if unresolved, prepare a sanitized support case for founder approval. No settings change, content endpoint or support message is sent automatically. |

No local mock can prove the cause or resolution of the historical 404. #88 and #121 remain open; Groq remains uncertified.

## Proposed real E2E protocol — not executed or authorized here

1. Founder approves account/credential source, exact Free model, synthetic fixture, request budget and zero spending ceiling. Confirm no paid fallback, unchanged privacy policy and local disposable PostgreSQL target. No remote migration/deploy.
2. Prefer GET-only verification of the already observed generation if still available. If separately approved, run one CrewAI generation on the reviewed SHA; bind its synthetic attempt UUID through existing provider correlation and persist returned IDs without logging body or secrets.
3. Query the exact observed generation ID with the originating authorized credential. Keep responses bounded and retain only required non-sensitive evidence: status/time, ID correlation, external_user, effective model, native tokens, provider cost and request ID when available. No `/generation/content` request.
4. Verify owner/tenant/attempt correlation and persisted evidence. An ID, a 200 status or a zero price alone is insufficient. Missing/mismatched metadata, 404, timeout, unavailable Free model or uncertain persistence leaves the accounting hold intact.
5. Exercise recovery idempotently without a second generative POST. Record fencing and ledger state, keep A03 costs separate from A04 final response units, and request independent review before any issue closure or merge.
