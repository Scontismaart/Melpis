# INF01 benchmark results

**Status: baseline, ONNX parity, shared HTTP, crash recovery, mixed PDF/pgvector and two-process diagnostics complete; no fleet capacity certification.** The 5/25/100 workload points mean synthetic activities in this harness. They are not pages, tenants, a supported capacity, or a product allowance. The Linux inference run used a root-managed, network-disabled container; it used no real customer content, database, WhatsApp traffic, or external provider.

## Run record

| Item | Observed value |
|---|---|
| Date | 2026-10-25 (local host, Europe/Rome) |
| Source | `327822f0e880ad0bf6a6ac1273e24b30d49dda9c` (`codex/inf01-shared-embedding`); working tree was dirty with concurrent INF01 changes during the run |
| PyTorch baseline runtime | Linux container, Python 3.12.15; PyTorch 2.14.0+cpu; sentence-transformers 5.6.0; Transformers 5.20.1 |
| ONNX runtime | Windows, Python 3.12.5; ONNX Runtime 1.31.0; tokenizers 0.23.3; NumPy 2.5.4; Torch and Transformers were not imported |
| Container resources | 2 GiB cgroup memory limit; two worker CPU affinities; observed cgroup high-water 1,676,976,128 bytes; no OOM kill |
| Cached model | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, snapshot `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` |
| Weights | `model.safetensors`, 470,641,600 bytes; SHA-256 `eaa086f0ffee582aeb45b36e34cdd1fe2d6de2bef61f8a559a1bbc9bd955917b` |
| Tokenizer | `tokenizer.json`, 9,081,518 bytes; SHA-256 `2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8` |
| Model settings | 384 dimensions; `query: ` and `passage: ` prefixes; L2-normalized embeddings |
| Network / database | Disabled / unused; HF and Transformers offline mode enabled |

The PyTorch harness is [benchmark_inf01.py](../../scripts/benchmark_inf01.py); its Linux receipt is [baseline-linux.json](../../artifacts/inf01/baseline-linux.json). The ONNX harness is [benchmark_inf01_onnx.py](../../scripts/benchmark_inf01_onnx.py). Its separate FP32 and INT8 process receipts are [onnx-fp32.json](../../artifacts/inf01/onnx-fp32.json) and [onnx-int8.json](../../artifacts/inf01/onnx-int8.json). The matching 5-activity PyTorch vectors are in [torch-reference-5.json](../../artifacts/inf01/torch-reference-5.json), SHA-256 `79fd9a18b085fbe11e876b0f24816bd8da1ebe35221e4b3d3f827f3a3803a414`. The earlier Windows PyTorch failure remains in [baseline.json](../../artifacts/inf01/baseline.json); it was not overwritten.

## Outcome

The Linux PyTorch baseline completed all three workload points in one model process. Model loading took 1.692 seconds. The worker peak RSS was 1,166,512,128 bytes, process CPU time was 14.478 seconds, and cgroup high-water memory was 1,676,976,128 bytes. The container reported no OOM kill.

| Synthetic activities | Passage embedding wall / CPU | Passage throughput | Query retrieval samples | Query retrieval p50 / p95 / p99 | Retrieval throughput | Top-1 / recall@5 / MRR |
|---:|---:|---:|---:|---:|---:|---:|
| 5 | 0.830 s / 0.506 s | 6.03 activities/s | 10 | 14.442 / 29.310 / 29.310 ms | 58.51 queries/s | 0.80 / 1.00 / 0.90 |
| 25 | 0.219 s / 0.306 s | 114.30 activities/s | 25 | 15.234 / 36.695 / 45.845 ms | 54.63 queries/s | 0.68 / 1.00 / 0.82 |
| 100 | 1.203 s / 1.624 s | 83.13 activities/s | 100 | 15.146 / 22.799 / 29.097 ms | 62.45 queries/s | 0.57 / 0.88 / 0.6884 |

The 5 activity latency percentile uses 10 repeated query observations; the other tiers use one observation per activity. These small samples are diagnostic. The synthetic generator rotates a 32-item multilingual cafe fixture, so repeated and semantically similar entries can affect top-1 and recall scores, especially at 100 activities. These quality scores do not certify production retrieval.

The Windows-only attempts remain a separate failed host observation. With a 1.5 GiB Job cap, safetensors loading failed with OS error 1455. A later 2 GiB Job attempt reached 100% weight loading, then failed with `STATUS_NO_MEMORY` (`0xC0000017`); its last observed worker working set was 1,463,283,712 bytes. It produced no workload measurements. An earlier attempt stopped making progress after a measured 841,793,536-byte peak. These attempts do not invalidate or combine with the successful Linux container run.

## ONNX comparison

The local FP32 and INT8 ONNX engines ran in separate processes, one model at a time. Each used a 1.5 GiB Windows Job cap, a two-CPU process affinity mask (`0x3`), two ONNX Runtime intra-op threads and one inter-op thread. FP32 preflight had 3,808,518,144 bytes of available physical memory and 16,666,677,248 bytes of available commit; INT8 preflight had 3,760,275,456 bytes and 16,558,063,616 bytes. Both passed the 2.35 GiB physical-memory and 3 GiB commit thresholds. Peak RSS below is per engine process, not an estimate of total application or host savings.

| Engine | Asset size | Load time | Tier wall time | Process peak RSS | Process CPU | Torch parity |
|---|---:|---:|---:|---:|---:|---|
| FP32 ONNX | 470,301,610 bytes | 1.821 s | 4.592 s | 923,860,992 bytes | 7.859 s user CPU | Pass |
| INT8 ONNX | 118,453,870 bytes | 1.376 s | 4.372 s | 478,617,600 bytes | 8.078 s user CPU | Fail |

The per-process CPU totals in these receipts are Windows user CPU from `GetProcessTimes`; passage and query CPU below use `time.process_time`. The harness now records total process CPU with `time.process_time` for future runs.

The FP32 artifact hash is `10f7a088420252b26caf819236ca2c9d2987afd0fc06fec7553b542a5655a05a`; the INT8 artifact hash is `98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f`. Both used tokenizer SHA-256 `2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8`, 128-token truncation, `query: ` / `passage: ` prefixes, attention-mask mean pooling, and L2 normalization.

| Engine | Activities | Passage wall / CPU | Passage throughput | Query p50 / p95 / p99 | Query throughput | Top-1 / recall@5 / MRR |
|---|---:|---:|---:|---:|---:|---:|
| FP32 | 5 | 0.049 s / 0.094 s | 102.52 activities/s | 9.914 / 11.257 / 11.257 ms | 100.99 queries/s | 0.80 / 1.00 / 0.90 |
| FP32 | 25 | 0.233 s / 0.453 s | 107.46 activities/s | 10.079 / 13.972 / 23.148 ms | 92.50 queries/s | 0.68 / 1.00 / 0.82 |
| FP32 | 100 | 0.908 s / 1.719 s | 110.09 activities/s | 11.141 / 20.318 / 26.551 ms | 83.17 queries/s | 0.57 / 0.88 / 0.6884 |
| INT8 | 5 | 0.050 s / 0.109 s | 100.30 activities/s | 10.134 / 11.141 / 11.141 ms | 99.15 queries/s | 0.80 / 1.00 / 0.90 |
| INT8 | 25 | 0.215 s / 0.438 s | 116.44 activities/s | 8.661 / 17.480 / 22.234 ms | 102.53 queries/s | 0.72 / 1.00 / 0.8333 |
| INT8 | 100 | 0.818 s / 1.609 s | 122.21 activities/s | 9.148 / 16.509 / 24.450 ms | 101.34 queries/s | 0.52 / 0.90 / 0.6635 |

FP32 matched the Torch reference for all five query and passage vectors: minimum cosine was 0.99999994 for queries and 0.99999976 for passages, above the 0.99999 threshold. The top-five ordering matched for every reference query.

INT8 did not meet its 0.995 cosine threshold: minimum cosine was 0.9916876 for queries and 0.99382949 for passages. Its top-five ordering also differed from Torch on the five-item fixture. A preliminary paired run measured top-five ordering agreement with FP32 of 80% / 48% / 12% at 5 / 25 / 100 activities; those ranking comparisons are retained in [onnx-runtime-results.json](../../artifacts/inf01/onnx-runtime-results.json), while the per-engine RSS figures above come only from the later separate-process runs. INT8 therefore fails this diagnostic parity gate.

## Comparison coverage

| Candidate | Result |
|---|---|
| Current PyTorch | Completed 5/25/100 activities in the Linux container; metrics above. Separate Windows attempts failed during model startup and are recorded in `baseline.json`. |
| Shared local runtime | Real private HTTP service; three lightweight client processes; bounded and overload runs complete. See below. |
| FP32 ONNX | Meets the defined five-query/five-passage cosine and top-five parity checks; synthetic timings and per-process RSS are above. |
| INT8 ONNX | Fails the defined cosine and ranking parity checks; do not treat it as an equivalent embedding space. |
| Multiple model workers | One versus two verified PyTorch workers measured sequentially in one 2.5 GiB / two-CPU network-disabled container; no OOM. See below. |

## Scope and follow-up

The harness creates fictional multilingual cafe questions and matching passages, embeds them offline, and ranks in memory. Its measures exclude PDF extraction, PostgreSQL/pgvector, API handling, WhatsApp processing, shared-service queueing, and production traffic. Even a successful run would be a small diagnostic sample, not an SLA or capacity certificate.

These measurements cover local synthetic embedding and in-memory retrieval only. The Linux PyTorch baseline and Windows ONNX runs used different runtimes and execution environments, so their timing values are not a direct speedup comparison. Shared-runtime measurements with API clients are recorded below and remain distinct from direct inference timings. Root selected shared FP32 ONNX as the next candidate; INT8 did not meet the diagnostic parity gate and is rejected for this embedding space. This does not establish production capacity or rollout readiness. The Windows PyTorch failure remains documented independently of the completed Linux PyTorch baseline.

## Shared HTTP: bounded demand and overload

The service ran with an ephemeral internal credential, no published port, an
internal-only network, read-only verified assets, 1536 MiB/two CPU/128 PID caps.
Three separate lightweight client processes shared one encoder. The diagnostic
image was `melpis-reviewed-mcp-runtime:qa-f322359`, not the eventual release
image. No provider credentials were passed to it.

| Activities | Requests | Concurrency per client | HTTP p95 / p99 | Successful activities/s | Rejected |
|---:|---:|---:|---:|---:|---:|
| 5 | 10 | 1 | 80.250 / 80.250 ms | 4.621 | 0 |
| 25 | 50 | 1 | 117.008 / 125.742 ms | 11.506 | 0 |
| 100 | 200 | 1 | 159.201 / 247.193 ms | 19.992 | 0 |
| 5 | 10 | 8 | 114.589 / 114.589 ms | 5.203 | 0 |
| 25 | 50 | 8 | 255.109 / 266.299 ms | 6.627 | 24 |
| 100 | 200 | 8 | 482.998 / 707.056 ms | 4.441 | 173 |

The overloaded run completed its observations, **not all requested operations**.
Its percentiles include quick rejected requests and must not be treated as a
successful-operation SLA. Backpressure was retained instead of increasing caps.
Each successful activity uses a passage and a query request. These are synthetic
activity DTOs, not concurrent production tenants. Client peaks were about 42 MB
each and no client loaded Torch or an encoder.

The separately sampled service cgroup peak was 974,974,976 bytes; its one worker
peak RSS was 899,636 KiB. Sampled cumulative worker CPU was 12.54 seconds and
whole-container CPU 24.414 seconds (includes the HTTP benchmark clients run
inside that container). These are labelled snapshots, not model-only CPU for
all later mixed tests. See `service-runtime.json`, `shared-http-bounded.json`
and `shared-http.json` in `artifacts/inf01/`.

## Real worker failure and missing assets

`service-smoke.json` records a killed in-flight ONNX worker: dispatched request
503, distinct cold-start request 503, and a later distinct request 200. Exactly
one model process was present before and after; no model was restarted until a
new request. A second service owner was fenced and public TCP egress was blocked.
`missing-model.json` records real subprocess startup rejection without assets,
with worker teardown and no runtime download.

This exercise exposed and fixed an existing LocalWorker startup race: after a
waiter timeout, a completed shielded startup could be overwritten, leaving live
model processes. The new unit regression proves eight concurrent waiters consume
one handshake; the repeated real smoke then passed. Initial failed diagnostics
remain local and are not reported as passing measurements.

The reproducible crash script is `scripts/benchmark_inf01_recovery.py`; it
requires the exact owned-container acknowledgement and verifies that container
PID 1 is the embedding server before sending a signal. Never run it against an
operational service. The inherited diagnostic image's API healthcheck targeted
the wrong route; actual embedding health was checked with its authenticated
endpoint. The candidate overlay overrides that healthcheck correctly.

## Mixed PDF, shared encoder and PostgreSQL

The successful receipt is `artifacts/inf01/mixed-pdf-pg-d.json`. It ran a separate
1024 MiB/one-CPU client container against the 1536 MiB encoder and 256 MiB/
half-CPU owned PostgreSQL container. All were private or loopback-only. PostgreSQL
16/pgvector 0.7.4 had the complete local schema through 066; no remote schema was
accessed. The database started empty. Nine synthetic organizations and 133
verified documents/chunks were retained for inspection (130 fixture activities
plus three cross-tenant canaries). No database, volume or backup was removed.

| Activities | Actual index samples | Index p95 / p99 | Pipeline activities/s | pgvector p95 / p99 | Parent peak RSS |
|---:|---:|---:|---:|---:|---:|
| 5 | 5 | 643.540 / 643.540 ms | 1.7345 | 5.016 / 5.016 ms | 218,763,264 B |
| 25 | 25 | 544.568 / 655.136 ms | 2.1412 | 19.053 / 21.076 ms | 222,433,280 B |
| 100 | 100 | 607.205 / 626.086 ms | 2.1813 | 6.732 / 9.836 ms | 226,103,296 B |

The index path used actual isolated selectable-PDF extraction, file-byte claims,
shared HTTP embeddings and fenced PostgreSQL publication. A query on the first
published PDF overlapped extraction of the second at each tier (about 50.8, 23.9
and 3.4 ms respectively). First-request replay retained its document and caused
zero extra passage embedding requests. Foreign-source checks and a non-entitled
third organization passed. Each tier also passed the actual responder schema
and guardrail using an explicitly deterministic Crew/provider-context stub.
That is **not a real-provider or A03 accounting E2E**.

The existing inbound route ACK test passed in a separate subprocess while the
pipeline ran. Its repository is mocked: the 3.22-second process duration includes
Python/pytest startup and is **not webhook ACK latency**, a durable-inbox worker
benchmark or live WhatsApp certification. Those full-host cases remain in C02/C03.

Mixed PDF retrieval top-1/recall@5 was 0.20/1.00, 0.36/0.96 and 0.29/0.62.
These generated pages include common boilerplate and repeated multilingual
content. They differ from the frozen direct-embedding fixture; comparing these
scores with its Torch results would be invalid. This run certifies the stated
publication/isolation mechanics, not acceptable production answer quality.

The canonical mixed-run receipt is [mixed-pdf-pg-d.json](../../artifacts/inf01/mixed-pdf-pg-d.json). It records the completed synthetic PDF extraction, shared embedding, fenced publication, pgvector retrieval, replay, tenant checks and deterministic responder/guardrail exercise. The harness is [benchmark_inf01_mixed.py](../../scripts/benchmark_inf01_mixed.py). The workload labels 5/25/100 mean synthetic activities in this run, not pages or tenants.

## C02 real-host evidence still required

These diagnostics do not establish readiness or capacity for a real host. Before a C02 decision, record the named target and accountable owner; host and container CPU, memory, PID and disk limits; API/worker replica and database/pool topology; baseline competing workloads; monitoring access; and owner-approved resource, latency, throughput and storage thresholds. Then run an explicitly authorized, bounded workload on that target, measuring API, embedding and PostgreSQL resource use together with PDF indexing and retrieval under the intended process topology.

The current mixed run does not exercise the durable WhatsApp inbox/worker path concurrently with PDF work. A mocked route ACK check is not webhook latency or WhatsApp certification. C02/C03 evidence still needs the approved host run to measure ACK separately from background processing, durable worker behavior and restart/replay under mixed load. No live Meta or provider calls are included in the existing receipts. Do not infer a fleet capacity, production rollout, or commercial allowance from these synthetic results.

Failed preliminary harness runs were retained separately: CLI tier parsing and
read-only CrewAI storage were corrected, without relaxing database guards. The
successful client used a disposable HOME/XDG/CrewAI directory under tmpfs and
`PYTHONUSERBASE=/home/appuser/.local` to retain the diagnostic image's reviewed
packages. Models were never loaded in these client processes. Reproduction
requires a new empty allowlisted local database with schema 066 and the same
explicit container limits; none of these commands authorize remote migration.

## One versus two PyTorch workers

`scripts/benchmark_inf01_processes.py` requires the exact owned Docker marker,
network-disabled execution and a finite cgroup ceiling no larger than 2.5 GiB.
The existing shared encoder was stopped first. A fixed query was encoded once
by each verified worker. Both returned cosine 1.0 to the frozen Torch reference,
384 finite normalized components, and identical vector digests.

| Stage | Live models | Sampled sum worker RSS peak | Sampled cgroup current peak |
|---|---:|---:|---:|
| First encoder ready/encode | 1 | 1,123,143,680 B | 1,500,983,296 B |
| Second encoder ready/encode | 2 | 2,194,128,896 B | 2,339,905,536 B |

Cgroup high-water was 2,344,005,632 B, no OOM kill; elapsed time 32.182 seconds.
Verified startup included checksum I/O: 13.56/13.73 seconds per worker, distinct
from the earlier pure model-load timing. The process CPU samples ended near
7.95 seconds for one and 17.00 summed seconds for two. RSS sums can double-count
shared pages; use the cgroup observation for the bounded physical envelope.
Both owned workers were terminated after the measurement. This is a memory
comparison, not a two-worker throughput or fleet-capacity certificate.
