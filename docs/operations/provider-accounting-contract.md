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
organization row. This allows one in-flight provider request per tenant. When
the only unresolved row is a recent `in_flight` request, a second valid request
waits for at most 35 seconds. Each retry uses a short PostgreSQL transaction to
revalidate the actor/message, billing window and quota; it releases all locks
before sleeping and before provider HTTP. The next dispatch is denied if the
first request fails, the wait expires, the context closes, authorization changes,
or the in-flight row is older than five minutes. Failed, stale, duplicate,
outbox-backed or otherwise uncertain attempts remain fail-closed. Other tenants
use independent organization locks and can continue.

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
null, and the tenant is blocked pending evidence-backed reconciliation. The
recovery path remains unavailable unless an authorized immutable evidence store
is configured.

Transport, HTTP, incomplete response, cancellation, process-crash or accounting failures
leave the attempt unresolved. They store only a bounded outcome code, never a
provider body or raw exception, and prevent later AI dispatches until explicit
tenant-scoped reconciliation backed by verified evidence. A completed provider response remains recorded
even if the user is disabled or billing changes while the response is in
flight, because the usage has already occurred.
An outer model fallback is a new attempt with its own ID and authorized model;
it can run after a completed, metered response that failed application output
validation. An error with unknown provider usage stops fallback until that
attempt is reconciled, avoiding an unmeasured second dispatch.
If the first database finalization fails after usage has been verified, the
adapter retries the same attempt ID idempotently. If persistence remains
unavailable, the pre-dispatch row stays unresolved and blocks later AI calls.

## Administrative recovery

Recovery is a two-step, tenant-scoped process. The proposal endpoint
`POST /api/billing/provider-attempts/reconciliation/proposals` and approval
endpoint `/api/billing/provider-attempts/reconciliation/approvals` each require
an active owner/manager and a verified AAL2 session. The approving principal
must differ from the proposer. PostgreSQL locks the organization and attempt,
checks active membership again, and uses a monotonically increasing proposal
version plus state/outcome compare-and-set. Proposal and approval audit records
are written in the same transactions as their state changes.

Both principals attest independently that they verified the provider account
and request, terminal provider outcome, and metrics (or their explicit absence).
They reference the same tenant-scoped immutable artifact using a strict object
ID and SHA-256 digest. The server fetches the artifact from an authorized store,
checks its digest, attempt, provider, terminal state, schema, model, and exact
token arithmetic. This is an evidence-format and human-attestation check; the
application does not contact the provider or automatically authenticate the
provider account/request. It recomputes any cost from the trusted price table. A
no-bill result is accepted only from an explicit terminal provider record.
Only Groq and OpenRouter currently receive the random attempt UUID as the
OpenAI-compatible `user` field. The ledger stores a safe provider-returned
request ID when the response body or response headers provide one. The artifact
must contain that exact ID, the matching provider correlation UUID, and a
timezone-aware provider event time within 15 minutes of the attempt row's
`created_at`. Recovery is rejected if the provider request ID was not recorded,
does not match, or the provider does not have this documented correlation
contract; the UUID in a hand-authored artifact alone is not sufficient.
Only the evidence object ID/version/digest, attestations, actors, and validated
accounting metrics are persisted; artifact contents and provider account/request
references are never written to PostgreSQL or audit logs. Store implementations
must enforce tenant-scoped access and immutable/WORM retention.

Evidence artifacts are limited to 16 KiB and rejected before hashing or JSON
parsing when empty or oversized. `GROQ_PROVIDER_ACCOUNT_REF` and
`OPENROUTER_PROVIDER_ACCOUNT_REF` must be set in the trusted runtime
configuration to the non-secret account identifier for the corresponding
provider before evidence for that provider can be verified. These values are
compared to the artifact and are never persisted or logged. They are not API
keys.

**Operational gate:** no authorized immutable evidence store is configured in
the current application, so these endpoints fail closed with HTTP 503 and do
not change attempt state. Do not manually construct provider references or
upload credentials into the application. An operator must verify the exact
provider account/request and terminal record in the provider console/support
system before placing an evidence artifact in the approved immutable store.
The store must return the exact immutable object version and prove a compliance-
mode retention deadline at least 90 days in the future; the server computes
SHA-256 from the returned bytes. Both approvers must inspect the artifact
independently.

An `outcome=in_flight` attempt is never reconciled based on age or timeout. The
current adapter does not persist a verifiable worker fencing token or terminal
provider correlation that would prove an old worker cannot later finalize it.
Such attempts remain blocking until that fencing/terminality mechanism exists;
two human approvals alone do not clear them. This is an explicit unresolved
recovery limitation, not a completed administrative capability. The process is
A03-only and does not reserve, refund, or alter A04 final-answer quota.

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
success, failure, tenant separation and concurrent admission. PostgreSQL tests
run two valid inbound WhatsApp messages for one organization, verify bounded
same-tenant wait and separate recorded attempts, and verify another tenant can
progress independently. Waiting attempts revalidate the message before
dispatch. No live provider request is part of local CI.

## Entry point inventory

| Path | A03 context | Notes |
| --- | --- | --- |
| Inbound responder (WhatsApp and Instagram) | Claimed message ID and private A04 operation/ticket | Applies to orchestrator and legacy pipeline; A04 counts each persisted final AI response separately |
| Authenticated simulator | User ID plus private A04 operation/ticket | Anonymous simulator cannot call a provider; simulator response quota is atomically committed by A04 |
| Onboarding preview | Authenticated user ID | A03 provider accounting only; excluded from A04 answer count |
| Manual and Google review drafts | Authenticated user ID | Deterministic generic draft needs no provider |
| Document Q&A | Authenticated user ID plus private A04 operation/ticket | A04 persists `{risposta,fonti}`; B02 PDF/RAG completion remains open |
| Dashboard AI report | Authenticated user ID | Falls back to deterministic summary if unavailable |
| Scheduled/global report | No tenant context | Deterministic summary only |
| Intent classifier | Disabled by default | Optional LLM call requires explicit provider context |

## Release boundaries

- A04 reserve/commit paths are wired for every inbound AI channel routed
  through `InboundProcessingService` (including WhatsApp and Instagram), the
  authenticated simulator and document Q&A. The A03 provider attempt remains a separate technical-cost
  record and unresolved attempts prevent unsafe A04 release.
- Migration 060 has not been remotely applied by this source change. Migration
  061 is a separate A03 gate. Staging is not certified.
- No remote migration, deploy, production mutation or paid provider call is
  included.
- Local PostgreSQL migration tests are not remote migration authorization.
- Live provider certification remains a separate external gate and requires an
  explicitly configured test account and budget.
