# Offline PDF and RAG CPU benchmark

`scripts/benchmark_pdf_rag.py` creates deterministic, fictional searchable PDFs (5, 50, and 200 pages) in a temporary directory and runs the repository's actual PDF extractor, chunker, and cached multilingual MiniLM encoder. It measures extraction, chunking, model-load, embedding, ten warm single-query encodes (p50/p95), process CPU time, peak working set, PDF bytes, text volume, and chunk count. It also runs one and two concurrent 5-page jobs. The benchmark does not invoke OCR, read credentials, contact external services, download models, start Docker, or connect to a database.

## Run

Use the reviewed Python 3.12 environment and run from the repository root:

```powershell
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
& "C:\Users\muzzo\AppData\Local\Temp\melpis-a06-otel-20261022\Scripts\python.exe" scripts\benchmark_pdf_rag.py
```

The runner requires a local snapshot of `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. If it cannot find one, it stops instead of attempting a download. It sets offline flags for each worker and limits PyTorch intra-op CPU threads to two. A JSON receipt is written to `docs/operations/pdf-rag-synthetic-benchmark.json`; it contains only synthetic data. To measure the image-only OCR path with the locally bundled RapidOCR models, run:

```powershell
& "C:\Users\muzzo\AppData\Local\Temp\melpis-a06-otel-20261022\Scripts\python.exe" scripts\benchmark_pdf_rag.py --ocr-benchmark
```

The OCR worker denies socket connections and has a 60-second process bound.

## Scope and interpretation

The 5/50/200-page generated PDFs contain selectable text, so their extraction measurements use the pypdf path. The optional one-page image-only sample exercises the production fallback through fitz rasterization and RapidOCR. RapidOCR's ONNX model files were verified as locally bundled before that run; its worker blocks network sockets.

The measured model-load time includes loading weights into a fresh worker. The embedding time is the first encode in that worker, so it is a cold encode. Warm query p50/p95 times are measured after document encoding has warmed the model. The two-worker concurrency scenario runs independent processes and includes process/model startup in its wall time; it is not an estimate for already-warm persistent service workers. Each worker reports its own peak working set and CPU time. The runner checkpoints the receipt after each completed tier so an interruption preserves completed measurements.

No local PostgreSQL listener was available on ports 5432, 5433, or 55432, and the Docker daemon was unavailable at inventory time. Therefore this receipt makes no claim about database indexing, vector storage, retrieval latency, or database concurrency. Run those measurements only against a separately provisioned disposable local pgvector database; do not substitute synthetic numbers for them.

## Baseline results

Measured on Windows with Python 3.12.5, cached MiniLM snapshot `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, and two PyTorch CPU threads. These are local CPU timings; cold model initialization and host load affect them.

| Synthetic PDF | Size | Extracted chars | Chunks | Extract | Chunk | Model load | First document encode | Peak RSS | Warm query p50 / p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Small, 5 pages | 17,679 B | 14,888 | 90 | 13 ms | 0.63 ms | 3.49 s | 7.42 s | 1.53 GB | 28 / 46 ms |
| Medium, 50 pages | 173,797 B | 148,989 | 900 | 137 ms | 5.84 ms | 4.21 s | 39.68 s | 1.53 GB | 17 / 31 ms |
| Large, 200 pages | 695,049 B | 596,290 | 3,600 | 659 ms | 31.7 ms | 2.01 s | 186.63 s | 1.53 GB | 40 / 50 ms |
| Image-only OCR, 1 page | 9,603,568 B | 108 | n/a | 4.37 s | n/a | n/a | n/a | 472 MB | n/a |

The one-worker, 5-page concurrent-runner wall time was 39.62 s including process startup and model initialization. That concurrency pass used the working-tree chunker/embedding files after the baseline tier runs, so it is recorded separately in the receipt with its blob IDs. Two independent model workers were skipped: the observed ~1.53 GB peak per worker plus 1 GiB headroom needs 4.14 GB of free physical memory, while the preflight sample showed 2.76 GB. The receipt records the exact values.

The warm query numbers time embedding a single query vector. They are not retrieval timings and do not include database access. They support keeping a model resident for query requests; reloading the model for each request would add seconds before query encoding.

## Baseline provenance

The baseline tiers were run against source commit `a7f09f2d4155dade5f93a6512fb2f5bdd4a9a5ea` (`#93 Melpis`). The receipt also records the extractor, chunker, and embedding Git blob IDs and cached model snapshot directory. No production source file was changed by the benchmark work.
