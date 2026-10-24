# B02 PDF and RAG implementation review

**State: implementation review; not a release or cutover certification.**
Migration 065 is present in the working tree but has **not** been applied to
the remote database. Local PostgreSQL publication tests (25) and a real
Valkey concurrency test passed; wider regressions and CI/build results are
still pending. C03 real-provider certification is separate. No real-provider calls are part
of this review.

## Architecture and reused components

### Measured disposable PostgreSQL receipt, 24 October 2026

See `pdf-rag-postgres-benchmark.json`: PostgreSQL 16/pgvector 0.7.4,
loopback-only Docker, 2 CPU/1 GiB container ceiling, Windows Python 3.12.5
client. This uses deterministic synthetic vectors, not model inference.
90/900/3,600 chunks publish in 0.272/2.987/13.784 seconds and occupy
171,900/1,720,800/6,886,800 bytes in their content/vector/metadata fields.
Shared relation plus indexes after all three tiers is 17,768,448 bytes;
that is neither a per-tenant bill nor an isolated tenant index size.
Twenty full entitled retrievals: p50 12.4 ms, p95 15.6 ms. Two-tenant
concurrent reads and small publications complete in 238 ms with zero
cross-tenant results. Database CPU/RSS are not measured by this client.
The new isolated MiniLM worker also produced a real 384-dimensional vector
locally under its Windows job limit; it made no provider API request.

B02 extends the existing `documents` / `document_chunks` pgvector store. It
does not add a second index. `src/core/documenti/indexing.py` claims an
idempotent request, prepares extracted text and embeddings, then delegates
atomic publication to `DocumentPublication` in
`src/core/db/repositories/document_publication.py`. Publication validates all
chunks and 384-dimensional finite vectors before SQL, fences stale workers,
and commits chunks, the verified document state, revision, and organization
knowledge epoch together. The existing tenant-scoped repository, chunk
organization-consistency trigger, pgvector search, FAQ cache, content
serialization, and structured-price guardrails are reused.

Extraction uses the existing PDF/text/DOCX parsers with bounded local worker
processes; image-only PDF pages use the existing OCR path when available.
Embeddings use the multilingual MiniLM model already used by the application.
The new worker boundary applies OS resource limits and fails closed when it
cannot enforce them. The retrieval path in
`src/core/documenti/rag_context.py` checks current entitlement, bounds the
whole embedding-and-search operation to three seconds, returns empty context
on unavailable sources, and rechecks the knowledge epoch before use. Before
generation or exposure, callers revalidate entitlement and epoch again.

## Ingress and entitlement matrix

RAG remains available only to eligible Scala organizations. Essenziale and
Crescita remain unchanged with RAG disabled. A03/A04 accounting and admission
remain separate from this feature; B02 makes no entitlement, reservation, or
quota-policy changes. Retrieval failure or an empty result falls back without
document context; a stale non-empty context is denied before generation.

| Path | Current integration | Scala and A03/A04 behavior |
| --- | --- | --- |
| Inbound legacy | `src/core/inbound/legacy_pipeline.py` uses shared retrieval and checks context freshness around generation. | Entitlement is checked at retrieval and again before use; existing A03/A04 path remains authoritative. |
| Inbound orchestrated | `src/core/receptionist/conversation_orchestrator.py` uses shared retrieval and freshness checks. | Same RAG gate; no change to A03/A04 admission or accounting. |
| Simulator | `src/api/routes/simulator.py` retrieves context and revalidates it around generation. | Scala gate applies; simulation keeps its existing non-production accounting behavior. |
| Preview | `src/core/onboarding.py` uses shared retrieval and checks current context. | Scala gate applies; existing preview flow and accounting are unchanged. |
| Reviews | `src/api/routes/simulator.py` and `src/core/reviews/google_service.py` use shared retrieval for review drafts. | RAG access is checked; A03/A04 review-generation controls remain separate. |
| Knowledge QA | `src/core/documenti/qa_agent.py` uses the shared retrieval and safe prompt serialization. | RAG access is checked; existing QA generation admission remains in force. |

## Schema, historical data, and cutover

Migration `src/core/db/migrations/065_document_publication.sql` adds the
knowledge epoch, publication request ledger and document fencing/revision
fields, verified-index marker, and cache epoch. Its presence in source does
not prove remote application or target-schema compatibility.

Historical rows are not certified from the `indicizzata` label alone:
`index_verified` defaults to false. Retrieval requires a verified,
ready, active source. An owner must explicitly reindex historical documents
through the canonical publisher; no migration or startup path should infer
verification from legacy state.

The C01 cutover runbook must cover backups and a tested restore for migrations
060 through 065, verify the actual target schema, drain old application
writers before enabling the new publisher, and record the deployment/worker
state. Rollback is a deliberate operator procedure with compatible code and
database state; there is no automatic schema rollback. Do not cut over until
the remote migration, restore evidence, and old-writer drain are verified.

## Local model and capacity evidence

The local multilingual MiniLM snapshot must be provisioned on the C01 host
before enabling indexing or retrieval. Provisioning must be offline and
version-pinned; runtime workers must not download model assets. Start with one
API process: its shared embedding child has a 2 GiB ceiling, and an extraction
child has a separate 512 MiB ceiling. These do not include API, PostgreSQL or
OS memory. Index admission permits one preparation per API event loop;
multiple API processes therefore require a separately measured host budget.
Retain the three-second query deadline and confirm host RAM/headroom in C01.

The [synthetic CPU benchmark](pdf-rag-synthetic-benchmark.md) records the
actual selectable-text 5/50/200-page measurements: each fresh worker peaked
around 1.53 GB RSS; document embedding took about 7.42/39.68/186.63 seconds,
and warm query embedding p95 was 46/31/50 ms. The benchmark did not measure
PostgreSQL. Its two-worker concurrency case was skipped because available
memory was insufficient; do not treat it as passed. OCR was measured
separately. PostgreSQL tests are currently running under the root task; their
results beyond the focused receipt remain pending. The focused PostgreSQL
and Valkey results above are real disposable-service measurements. The actual
isolated embedding child also succeeded with 384 dimensions on Linux inside
a network-disabled disposable container (2 CPU/3 GiB container ceiling,
2 GiB child data limit), using the cached snapshot and current source. This
is a resource-boundary probe against an existing Python 3.12 runtime image,
not certification of the final API image or its deployment model assets.

## Security and release gates

Before release, verify on the deployed schema and code that:

- retrieval and publication remain organization-scoped, and direct client
  writes to documents, chunks, and the semantic cache remain denied;
- stale publication leases are fenced, request keys are immutable, deleted
  targets remain tombstoned in the request ledger, and retries cannot
  resurrect them;
- epoch changes invalidate cached and in-flight context, with compare-and-set
  cache writes refusing stale epochs;
- profile-bound structured data revalidates its expected profile before
  publication, and source identifiers are rechecked before using selected
  sources;
- historical unverified rows stay out of retrieval until an owner publishes
  a complete verified revision;
- retrieved PDF, FAQ, and web text stays serialized as untrusted data, and
  structured listino/price-priority guardrails continue to run.

CI/build and PostgreSQL integration results remain **PENDING**. C03 must use
the approved real-provider certification procedure; this document performs
no provider calls and grants no C03 readiness. No automatic commercial limits
are configured (`commercial_limits` remain `NULL`). The proposed Scala
allowance of 20 documents and 200 aggregate pages is a conservative option for
founder approval and evaluation only. The 100-document/500-page values are an
unvalidated hypothesis. Neither proposal is implemented or advertised.
