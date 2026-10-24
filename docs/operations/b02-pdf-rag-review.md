# B02 PDF and RAG implementation review

**State: implementation review; not a release or cutover certification.**
Migration 065 is present in the working tree but has **not** been applied to
the remote database. Local PostgreSQL publication tests (25) and a real
Valkey concurrency test passed. Another 82 wider PostgreSQL regressions
also passed. Final SHA-specific CI/audit/build results are recorded in PR #132;
only successful runs on its current head certify that head. C03 real-provider certification is separate. No real-provider calls are part
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
| Simulator | `src/api/routes/simulator.py` retrieves context and revalidates it around generation. | Scala gate applies; authenticated simulations retain the existing A04 reservation, canonical commit and replay contract; anonymous demo remains separate. |
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
child has a separate 1 GiB ceiling. These do not include API, PostgreSQL or
OS memory. Index admission permits one preparation per API event loop;
multiple API processes therefore require a separately measured host budget.
Retain the three-second query deadline and confirm host RAM/headroom in C01.

The [synthetic CPU benchmark](pdf-rag-synthetic-benchmark.md) records the
actual selectable-text 5/50/200-page measurements: each fresh worker peaked
around 1.53 GB RSS; document embedding took about 7.42/39.68/186.63 seconds,
and warm query embedding p95 was 46/31/50 ms. The benchmark did not measure
PostgreSQL. Its two-worker concurrency case was skipped because available
memory was insufficient; do not treat it as passed. OCR was measured
separately; this baseline OCR measurement does not certify OCR under the new
extraction memory ceiling. The focused and wider PostgreSQL and Valkey
results above are real disposable-service measurements. The actual
isolated embedding child also succeeded with 384 dimensions on Linux inside
a network-disabled disposable container (2 CPU/3 GiB container ceiling,
2 GiB child data limit), using the cached snapshot and current source. This
is a resource-boundary probe against an existing Python 3.12 runtime image,
not certification of the final API image or its deployment model assets.

## Security and release gates

Before release, verify on the deployed schema and code that:

- schema CREATE privileges on public/extensions are denied to untrusted roles
  before using the explicit application search path;
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

The final PR check suite must pass application validation, installed-runtime
audit, Migration check and all four API/web architecture builds. Initial
failed runs are not certification of the corrected head. C03 must use
the approved real-provider certification procedure; this document performs
no provider calls and grants no C03 readiness. No automatic commercial limits
are configured (`commercial_limits` remain `NULL`). The proposed Scala
allowance of 20 documents and 200 aggregate pages is a conservative option for
founder approval and evaluation only. The 100-document/500-page values are an
unvalidated hypothesis. Neither proposal is implemented or advertised.

## Dependency audit correction

The first CI run stopped on three new filelock advisories. LiteLLM 1.106.2
requires filelock <4, so 4.2.1 is not a compatible normal-resolver update.
Pin the official unaffected 3.20.3 release: it contains the older
CVE-2025-68146/CVE-2026-22701 fixes and predates the new RW-lock/oplock code
affected by CVE-2026-112907/112913/112940. Its PyPI wheel SHA256 is
`4b0dda527ee31078689fc205ec4f1c1bf7d56cf88b6dc9426c4f230e46c2dce1`.
The local install verified that hash and passed normal `pip check`; the OSV
version query reports no advisory for 3.20.3. The complete installed-runtime
CI audit remains mandatory. No audit exception or package metadata override
is introduced. CrewAI/MCP/OTel artifact verification is unchanged.

Primary references: [older SoftFileLock fix](https://github.com/advisories/GHSA-qmgc-5h2g-mvrw),
[new Windows oplock range](https://github.com/advisories/GHSA-567q-5qvw-pf92),
[new Windows RW-lock range](https://github.com/advisories/GHSA-9vhv-jxww-xfm5),
[new POSIX RW-lock range](https://github.com/tox-dev/filelock/security/advisories/GHSA-v4x8-3wr5-3wq8).
LiteLLM is explicitly pinned at the existing 1.106.2 version, following its
[official incident guidance](https://docs.litellm.ai/blog/security-incident-oct-21-2026);
1.107.0 is not installed or executed by this task.

## Final contract corrections

Document-Q&A final responses now carry the captured knowledge epoch into the
existing A04 capability: a profile change that leaves PDF revisions unchanged
still rejects a stale final commit. A PostgreSQL regression covers this race.
Legacy document metadata/type/status writers invalidate verification; they
cannot turn an already indexed source into a new trusted priority silently.
Empty retrieval contexts do not manufacture an epoch capability.

Cross-origin upload preflight explicitly permits Idempotency-Key only for the
configured origins; unauthorized origins remain rejected. Nginx indexing
routes allow 660 seconds around the bounded 600-second preparation, while
ordinary API requests retain the existing 60-second timeout. The application
pool explicitly uses public/extensions so pgvector resolves in databases
whose name differs from the historical postgres migration target.

The local real pipeline receipt `pdf-rag-pipeline-probe.json` records complete
synthetic PDF extraction, actual cached MiniLM embedding, atomic PostgreSQL
publication, idempotent replay, two-tenant parallel retrieval, toggle exclusion
and unchanged commercial counters. It identifies the source commit, dirty
state and file hashes; no remote provider is used. OCR status is reported
separately and must not be inferred from the overall text-PDF pipeline result.

The bounded Windows OCR probe failed safely at 512 MiB and succeeded at
1 GiB (about five seconds for synthetic printed text). The extraction ceiling
is therefore 1 GiB; it remains independent of the 2 GiB embedding ceiling.
This certifies that synthetic OCR case, not arbitrary scan accuracy or a
second concurrently resident embedding model. C01 must budget both children,
API, PostgreSQL and OS headroom before activation.
