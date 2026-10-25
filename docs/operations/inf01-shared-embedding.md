# INF01: private shared embeddings

## Decision and scope

Use one private, preloaded **ONNX FP32** encoder per service instance. API,
inbound, retry and supervisor processes use the same authenticated service when
the explicitly reviewed candidate overlay is selected. The default deployment
is unchanged: no flag or operational environment was activated. There is no
local-model fallback when shared mode is configured and the service fails.

The service uses the **same immutable reviewed API image**, with a different
single-worker command. No second dependency resolver, separate heavyweight
image family, GPU, external inference service, or new package is introduced.
The ordinary API amd64/arm64 builds therefore package this code as well.

INT8 is rejected for this vector space: it failed the predeclared cosine and
ranking parity gates. The lighter asset alone is insufficient. FP32 preserves
the pinned tokenizer, query/passage prefixes, 128-token truncation, masked
mean pooling, L2 normalization and 384 dimensions. The existing PostgreSQL
index is not rewritten. The small multilingual parity fixture is diagnostic,
not general retrieval quality certification.

## Reused components and call paths

- `LocalWorker` and its terminating process boundary, startup handshake,
  offline flags and process memory controls remain the execution primitive.
- `index_content` retains the B02 fenced claim/publication and file accounting
  protocol; batches of at most 16 yield between embedding requests.
- `rag_context`, FAQ lookup/store and legacy/orchestrated inbound callers pass
  their already-authorized organization. Simulator, preview and Q&A reuse the
  same retrieval path. Their A03/A04 gates, guardrails, source permissions and
  commercial response counter are unchanged.
- `embedding_contract` validates UUID identity, exact JSON keys, finite unit
  vectors, immutable model/artifact fingerprints and echoed request/tenant.
- `shared_client` has bounded response/body/deadline and no retries, redirects,
  environment proxies, client-selected URL or local fallback.

The service is stateless and has no database, source corpus, answer cache or
cross-request text persistence. The internal credential authenticates a trusted
backend principal. An organization UUID is **not** independently authenticated
by this service: public-route authorization and tenant-scoped repositories stay
mandatory. Never distribute its credential to browsers or customer tenants.

## Admission, deadlines and process ownership

One inference runs at a time, at most 16 requests are in flight/queued globally
and two per organization. Queries have a three-second end-to-end service
budget; passages thirty seconds. An eight-query burst then one passage prevents
permanent indexing starvation. Full queues return 429 with Retry-After. There
is no unbounded retry or generation POST in the encoder.

The candidate uvicorn command caps HTTP concurrency at 32, including request
body readers; request bodies are bounded at 256 KiB and two seconds before model admission. A cancelled queued request
is removed. A dispatched request is never automatically replayed. On timeout
or worker failure, the current caller fails; only a distinct explicit request
can start a replacement worker. No quota/accounting state is changed here.

A process-level instance lock rejects a second model-owning service process
before model allocation. An additional regression fixes an existing startup
race: a completed, shielded cold start after the original caller timed out must
be consumed, rather than overwritten by another startup which could orphan a
resident model. Readiness stays false during startup and becomes true after its
verified handshake even if the original waiter has timed out.

## Offline assets and deployment candidate

`compose.inf01.candidate.yml` is a **review-only overlay**, not an activation.
It adds an internal-only embedding network, no published encoder port, a
read-only verified model mount, a file-backed shared secret, one uvicorn worker,
1536 MiB memory / two CPU / 128 PID ceilings, read-only filesystem and bounded
tmpfs. All possible application callers select shared mode explicitly. Existing
application, database and provider networking is not replaced.

Prepare the model outside the runtime using
`scripts/provision_embedding_assets.py --destination <protected-directory>`.
It requires the pinned existing tokenizer/config cache. An explicit
`--allow-download` permits build/provisioning-time public immutable asset
retrieval with exact size/SHA checks; runtime never calls this script. Mount the
completed directory read-only. Missing or incorrect assets fail startup, without
network fallback. No model binary, key, source PDF or .env is committed.

Provision `MELPIS_EMBEDDING_TOKEN_FILE` separately (32–256 URL-safe characters,
optional single newline). Ambiguous file plus literal token configuration is
rejected. The local synthetic harness used an ephemeral in-memory token; no
provider credential was passed into Docker. `.dockerignore` excludes diagnostic
artifacts as well as existing secret/runtime paths.

Use the authenticated `/health` route only on the private network. It reports
readiness/fingerprint, queue lengths, outstanding count and completed/rejected/
timeout/failure counters, without text or tenant-labelled metrics. Container
CPU/memory and process count provide capacity telemetry. Public API health and
manual dashboard access remain separate.

## Local evidence and limitations

See [benchmark results](inf01-benchmark-results.md) and the explicit receipts.
Real bounded Linux HTTP smoke proved crash -> failed request -> replacement ->
new request success, exactly one resident model, instance fencing, blocked
public egress, and no automatic replay. Missing-model startup also failed closed.
These observations used a cached diagnostic runtime; final PR CI validates the
current reviewed dependency process and both ordinary API/web architectures.
They do not certify a deployed Staging or Production image.

Shared load with three client processes at concurrency one each completed
5/25/100 synthetic activities. A separate concurrency-eight overload run
exercised rejection rather than hiding failures or enlarging resource caps.
Measurements for different operating systems/runtimes are not direct speedup
comparisons. The test unit is a synthetic activity, not an active customer or
licensed capacity.

## Scaling and go/no-go

Increasing API process count must not increase model count when the candidate
shared configuration is in use. Start with one encoder instance; cap indexing
admission and monitor queue wait/rejections/query tails. Do not create one
encoder per tenant. If measured demand exceeds this service, first tune bounded
batching/admission from new evidence. A second explicitly sized service instance
is a later topology decision; it is never spawned on a request or a timeout.

The local Docker VM is approximately 3.825 GiB and hosts unrelated Supabase
services. It is not the proposed four-vCPU/eight-GiB release host. C02 still
requires an identified/approved host, full application+database+encoder mixed
load, capacity/SLO thresholds, actual image/secret provisioning, monitoring and
restore/rollback evidence. No purchase or sizing guarantee follows from this PR.

No remote migration, deployment, provider operation, commercial limit activation,
issue closure, backup removal or Docker Desktop shutdown is part of INF01.
