# A03: AI admission and provider accounting

## Scope

A03 records technical provider dispatches and their measured cost. It does not
reserve or charge a final response; that remains the separate A04 contract.
The document Q&A entry point receives this gate and accounting, but PDF/RAG
ingestion and answer quality remain B02 work.

## Admission

`crea_llm` refuses to construct a provider client without a private
`ProviderAccountingContext`. HTTP dispatch is possible only after that context
has verified a tenant, a live membership or a claimed inbound message, an
active trial window or paid subscription with a nonempty Stripe subscription ID
and current billing-period window, available numeric quota, and no unresolved
provider attempt or usage outbox entry. Authenticated dashboard calls are checked again
against `auth_access_lifecycle` and `organization_memberships`. An inbound
worker is scoped to its own processing, already-billed message. The current
precharge is subtracted only in the checked snapshot; no quota is reserved or
refunded by A03.

`past_due` does not authorize a new provider dispatch, even though the separate
organization suspension helper preserves dashboard and billing-recovery access
during Stripe retries. Deterministic dashboard access remains available when AI
generation is blocked.

The authorization check runs again for each dispatch while locking the
organization row. This allows one in-flight provider request per tenant. A
second request for that tenant cannot pass while its attempt remains
unresolved; another tenant has an independent lock and ledger.

The anonymous simulator and process-global scheduler cannot create an AI
capability. The simulator returns a fixed sign-in prompt. The scheduled report
uses a deterministic summary. A dashboard report can still return a
deterministic summary if billing or provider accounting is unavailable.

## Durable attempts

Each actual dispatch inserts a `system.provider_attempt` row in `usage_events`
before HTTP. Its `organization_id` is the tenant scope; metadata records the
provider, server-authorized requested model, task, trace/message context,
execution ID, attempt ID and `in_flight` outcome. Generic billing writes and
the governance outbox reject this reserved event type.

After a syntactically valid provider response, the same row stores the provider-returned model,
explicit prompt/completion/total token counts, elapsed time, and a cost estimate
derived from those verified tokens and the server's model price table. Chat
Completions does not return the account's billed euro amount, so this is not an
invoice-level charge measurement. The row also records
`response_received`; if the provider usage is valid but the content fails
application validation, it records `invalid_response` and
`application_outcome=invalid_content` while retaining measured usage. The model must match the authorized model or a documented
OpenRouter free alias. Missing, partial, boolean, inconsistent or malformed
usage is never normalized to zero. A verified model is retained when usage is
incomplete; `usage_present` distinguishes absent, malformed-present, and
unverifiable payloads. Explicit zeros remain valid evidence. If
the model's cost is unknown, measured response fields are retained, cost stays
null, and the tenant is blocked pending reconciliation.

Transport, HTTP, incomplete response, cancellation, process-crash or accounting failures
leave the attempt unresolved. They store only a bounded outcome code, never a
provider body or raw exception, and prevent later AI dispatches until explicit
tenant-scoped reconciliation. A completed provider response remains recorded
even if the user is disabled or billing changes while the response is in
flight, because the usage has already occurred.
An outer model fallback is a new attempt with its own ID and authorized model;
it can run after a completed, metered response that failed application output
validation. An error with unknown provider usage stops fallback until that
attempt is reconciled, avoiding an unmeasured second dispatch.
If the first database finalization fails after usage has been verified, the
adapter retries the same attempt ID idempotently. If persistence remains
unavailable, the pre-dispatch row stays unresolved and blocks later AI calls.

## Transport and metrics source

The app uses a private CrewAI `BaseLLM` adapter. It sends one raw
OpenAI-compatible Chat Completions request with redirects, streaming, tools,
ambient proxies and transport retries disabled. The raw response is checked
before any CrewAI or LiteLLM aggregation. No global usage callback or collector
is used. CrewAI's `usage_metrics` are not an accounting source because the
installed CrewAI/LiteLLM path can turn missing values into zero and aggregates
calls without a tenant execution boundary.

The synchronous adapter enforces an absolute request deadline in addition to
HTTPX's connect/read timeouts, including streamed response reads. The repository
pins CrewAI 1.15.4. The private adapter implements the installed
`BaseLLM.call/acall` contract, including tool, callback, task, agent, and
response-model parameters, but rejects tools and reads provider usage directly
from the raw HTTP response. No LiteLLM usage normalization or retry contract is
used for accounting or provider dispatch.

Provider HTTP responses and offline transport fixtures use the same parsing
contract: returned `model`, `usage.prompt_tokens`,
`usage.completion_tokens`, and `usage.total_tokens`. A provider response
without those explicit values remains unverified and blocks subsequent AI.
Offline tests exercise raw response parsing, pre-dispatch ledger creation,
success, failure, tenant separation and concurrent admission. No live provider
request is part of local CI; none was made for this change.

## Entry point inventory

| Path | A03 context | Notes |
| --- | --- | --- |
| WhatsApp/inbound responder | Claimed message ID | Applies to orchestrator and legacy pipeline |
| Authenticated simulator | User ID plus server-issued request claim | Anonymous simulator cannot call a provider; verified precharge is recognized only in the local admission snapshot |
| Onboarding preview | Authenticated user ID | Strict billing/quota gate |
| Manual and Google review drafts | Authenticated user ID | Deterministic generic draft needs no provider |
| Document Q&A | Authenticated user ID | Gate applies when retrieved sources reach LLM; B02 remains open |
| Dashboard AI report | Authenticated user ID | Falls back to deterministic summary if unavailable |
| Scheduled/global report | No tenant context | Deterministic summary only |
| Intent classifier | Disabled by default | Optional LLM call requires explicit provider context |

## Release boundaries

- No A04 response-reservation protocol is activated.
- No remote migration, deploy, production mutation or paid provider call is
  included.
- Local PostgreSQL migration tests are not remote migration authorization.
- Live provider certification remains a separate external gate and requires an
  explicitly configured test account and budget.
