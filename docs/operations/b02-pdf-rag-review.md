# B02 PDF and RAG implementation review

**State: implementation review; not a release or cutover certification.**
Migrations 065/066 are present in source but have **not** been applied to
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

The founder revised the commercial contract after c29274f7: PDF/RAG is
included in Essenziale, Crescita and Scala, including an authorized trial.
Prices and response quotas remain 29/69/149 EUR and 500/2,000/10,000 monthly
responses; trial generation remains limited to seven days or 150 responses.
A03/A04 admission, accounting and final-response quotas remain separate.
Only original uploaded file bytes differentiate commercial RAG capacity.
No numeric paid or trial file budget has been approved or configured. Retrieval failure or an empty result falls back without
document context; a stale non-empty context is denied before generation.

| Path | Current integration | Current plan/trial and A03/A04 behavior |
| --- | --- | --- |
| Inbound legacy | `src/core/inbound/legacy_pipeline.py` uses shared retrieval and checks context freshness around generation. | Entitlement is checked at retrieval and again before use; existing A03/A04 path remains authoritative. |
| Inbound orchestrated | `src/core/receptionist/conversation_orchestrator.py` uses shared retrieval and freshness checks. | Same RAG gate; no change to A03/A04 admission or accounting. |
| Simulator | `src/api/routes/simulator.py` retrieves context and revalidates it around generation. | Valid current-plan/trial gate applies; authenticated simulations retain the existing A04 reservation, canonical commit and replay contract; anonymous demo remains separate. |
| Preview | `src/core/onboarding.py` uses shared retrieval and checks current context. | Valid current-plan/trial gate applies; existing preview flow and accounting are unchanged. |
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
060 through 066, verify the actual target schema, drain old application
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
no provider calls and grants no C03 readiness.

The only commercial file limits are original bytes. Plan and trial budgets
remain `None` (not configured, never unlimited). New uploaded files/growth
are blocked until approved budgets exist; lookup, manual knowledge sources
and dashboard access are independent. Existing known-file replacements that
do not increase their charge remain possible. The earlier proposal of a
commercial document/page allowance is withdrawn; those counts are usage
information only. See [the MB proposal](b02-file-quota-policy.md), which is
not an activated entitlement.


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

## Founder-approved requirement change: file bytes, all plans

Migration 066 adds original file byte/hash evidence and pending byte/hash
fields directly to documents. Original uploads are not retained. Authoritative
values come from server-side `len(file_content)` and SHA-256, never filename,
MIME, source type, client metadata, extracted text or vector storage size.
A non-file source has explicitly known zero original-file bytes; a historical
row without provenance remains NULL/unknown, including old manual sources.

`DocumentPublication` reuses the existing organization lock, request ledger,
lease token and fencing generation. Occupancy is the sum of max(committed,
pending) per document, derived from these rows rather than a second mutable
counter. Claim reserves before extraction/embedding; publication rechecks the
current plan/trial policy and atomically settles the exact original bytes.
Replay cannot add a reservation. Expiry alone cannot release it; a new fenced
claim or a verified terminal transition is required. A failed replacement
releases only its pending increment. A successful smaller replacement or an
explicit tenant-scoped deletion lowers occupancy; inactive files still count.

Billing renewal/trial conversion does not reset file usage. Upgrade uses the
current approved plan budget. Downgrade preserves stored content and read
access; existing-file replacements can keep/reduce a known charge, while
new/growing sources may not exceed the current budget. A plan change during
preparation is checked again before publication. A03/A04 still gate provider
calls and response consumption independently of file storage capacity.

C01 must inventory historical NULL values before enabling new file uploads.
Do not backfill `metadata.bytes`, document type, text length or SQL relation
size as an original-byte measurement. An unknown row may not be converted
silently to zero by a text replacement. A separately verified reconciliation,
or explicit owner deletion and fresh upload, is required. No remote migration
or such historical modification is authorized by this PR. The dashboard
reports unknown counts and prevents unjustified remaining-MB claims.

All source kinds retain common technical ceilings, rate limits, admission
serialization and process budgets. There are no new commercial document/page
limits. FAQ/manual/web/structured content consumes no original-file quota,
but still passes the bounded publication/retrieval and authorization paths.
The earlier c29274f7 CI is historical evidence only: new checks must pass on
the revised head before independent review.

The existing full MiniLM/PostgreSQL pipeline receipt predates the founder
requirement change; its source hashes identify that earlier contract. New
file-quota lifecycle evidence comes from the disposable PostgreSQL regression
suite. A fresh local pipeline rerun was unavailable because Docker/WSL failed
to start; no new end-to-end model/quota result is claimed. The updated local
probe supports an explicit synthetic allowance and records it separately.

## Replacement failure correction and recovery

The former claim overwrote canonical source identity and made the certified
revision unavailable before extraction succeeded. A disposable PostgreSQL
regression reproduced this on the intake head 4035c8d.

Migration 066 now adds a backend-owned `index_intent` on the existing document,
separate from its published name/type/source, request key/hash, revision,
metadata, chunks and committed original-byte evidence. A claim changes only
pending intent, lease/fence and reservation fields. The previous certified
revision remains available through the normal current-entitlement and active
source checks; historical/unverified sources are never promoted by failure.

Publication rechecks entitlement, quota and live token/fence, then swaps all
canonical fields, chunks and committed bytes in one transaction. Its temporary
unverified state is invisible outside that transaction. An insertion error or
lease loss rolls the entire swap back. Terminal failure clears only the owned
reservation and lease, retains the last failed intent for explicit same-key
retry, and never enables a source or restores a certificate. Process crash
leaves an interrupted, fenced claim; expiry alone does not refund capacity.

Delete, toggle, semantic metadata edits, certified-chunk mutation and relevant
business-profile edits revoke pending work. They invalidate its token/fence
and retry identity, so a delayed worker or old request cannot undo the owner's
action. A new explicit request is needed after such a revocation. Successful
replay requires the request ledger's published revision to match the canonical
revision and latest request identity. Old A04 evidence is fenced by the normal
knowledge epoch and source-revision checks.

The dashboard receives only operation status and non-authorizing request UUIDs,
not lease tokens, fences or raw intent. Pending/failed replacement is shown
separately from the still-available previous revision. Polling must correlate
the actual published request UUID before reporting a replacement as complete.
Original files still are not stored; retry/reindex requires the owner to supply
the original again. INF01, INF02/R2 and larger file limits are separate work.

## C1 dashboard replacement contract (27 October 2026)

An already indexed document offers **Replace document** and requires a new local
file. The dashboard reuses `POST /api/documenti/{id}/reindicizza` with the same
document identity and an idempotency key. A retry of an uncertain request keeps
that key. New uploads with an existing canonical filename are directed to the
replacement action; this UI check supplements the authoritative backend rules.
No original-file storage, download or restore capability is introduced.

Each card shows the canonical name, backend indexing state, revision when known,
and availability under the current verified RAG entitlement. An accepted HTTP
request is not reported as a completed publication: success requires the matching
published request identity. Pending, failed, interrupted and unknown outcomes
remain distinct. A previously published revision is described as available only
when the backend marks it active and verified and current entitlement permits it.

On organization changes, permissions, cached document names, pending retry and
picker state are invalidated. Uploads wait for permissions belonging to the
effective authenticated organization; a selected organization alone grants no
authority. Late responses cannot update the new context. In-flight UI mutation
locks are scoped to the effective user and organization and survive same-tenant
exit/reentry until their own request settles. Server-side authorization,
publication, quota and idempotency contracts are unchanged.

Regression evidence lives in `tests/frontend/dashboard-knowledge-module.test.cjs`
and the real-shell frontend contracts. Operational certification of B02 #93,
remote migrations, capacity benchmarks and commercial quota activation remain
separate gates; this UI package does not close them.
