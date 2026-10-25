"""Measure synthetic HTTP load against the private shared INF01 embedding API.

This script is intentionally limited to the internal embedding service. It
does not access a database, provider, PDF, or customer content. The bearer token
is read from the environment in each process and is never written or printed.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import importlib.util
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core.documenti.embedding_contract import (  # noqa: E402
    ARTIFACTS, DIMENSION, MODEL_ID, REVISION, SPACE_ID, strict_json, validate_request,
    validate_vectors,
)


def _activity_fixture(size: int) -> tuple[list[str], list[str], list[int], list[str]]:
    module_path = Path(__file__).with_name("benchmark_inf01.py")
    spec = importlib.util.spec_from_file_location("inf01_torch_benchmark_fixture", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic_fixture_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._activities(size)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
    return round(ordered[index], 6)


def _usage() -> tuple[float | None, int | None]:
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = int(usage.ru_maxrss) * (1024 if sys.platform.startswith("linux") else 1)
        return round(float(usage.ru_utime + usage.ru_stime), 6), rss
    except (ImportError, OSError, ValueError):
        return None, None


def _env_config() -> tuple[str, str]:
    base_url = os.environ.get("MELPIS_EMBEDDING_URL", "").rstrip("/")
    token = os.environ.get("MELPIS_EMBEDDING_TOKEN", "")
    parsed = urlparse(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"embedding", "localhost", "127.0.0.1"}
            or not parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise RuntimeError("MELPIS_EMBEDDING_URL_must_be_an_internal_http_service_url")
    if not 32 <= len(token) <= 256 or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for ch in token):
        raise RuntimeError("MELPIS_EMBEDDING_TOKEN_missing_or_invalid")
    if os.environ.get("MELPIS_EMBEDDING_ENGINE", "onnx") != "onnx":
        raise RuntimeError("shared_benchmark_requires_onnx_engine")
    return base_url, token


async def _health(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("/health")
    if response.status_code != 200:
        raise RuntimeError("embedding_health_not_ready")
    try:
        data = strict_json(response.content)
    except ValueError:
        raise RuntimeError("embedding_health_invalid_json") from None
    expected = {
        "ready": True, "model_id": MODEL_ID, "revision": REVISION,
        "embedding_space": SPACE_ID, "dimension": DIMENSION,
        "artifact_sha256": ARTIFACTS["onnx"],
    }
    if any(data.get(key) != value for key, value in expected.items()):
        raise RuntimeError("embedding_health_fingerprint_mismatch")
    return {key: data.get(key) for key in (*expected, "queued_queries", "queued_passages", "in_flight_and_queued", "counters")}


async def _initial_health(base_url: str, token: str, timeout_seconds: float) -> dict[str, Any]:
    async with httpx.AsyncClient(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"},
        timeout=timeout_seconds, trust_env=False,
    ) as client:
        return await _health(client)


async def _client_process(client_index: int, activities: list[dict[str, Any]], base_url: str,
                          concurrency: int, timeout_seconds: float) -> dict[str, Any]:
    token = os.environ.get("MELPIS_EMBEDDING_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"}
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0))
    latencies: list[float] = []
    statuses: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    successful_activities = 0
    max_queued = 0
    start = time.perf_counter()
    cpu_before, _ = _usage()

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout,
                                 limits=limits, trust_env=False) as client:
        async def sample_queue() -> None:
            nonlocal max_queued
            while True:
                try:
                    health = await _health(client)
                    max_queued = max(max_queued, int(health.get("in_flight_and_queued", 0)))
                except Exception:
                    errors["health_sample"] += 1
                await asyncio.sleep(0.025)

        sampler = asyncio.create_task(sample_queue())
        semaphore = asyncio.Semaphore(concurrency)

        async def send(activity: dict[str, Any], tipo: str, texts: list[str]) -> bool:
            nonlocal successful_activities
            organization_id = activity["organization_id"]
            payload = {
                "organization_id": organization_id,
                "request_id": str(uuid4()), "texts": texts, "tipo": tipo,
            }
            try:
                validate_request(payload)
            except ValueError:
                errors["fixture_contract"] += 1
                return False
            async with semaphore:
                before = time.perf_counter()
                try:
                    response = await client.post("/v1/embeddings", json=payload)
                    latencies.append(time.perf_counter() - before)
                    statuses[str(response.status_code)] += 1
                    if response.status_code != 200:
                        if response.status_code == 429:
                            errors["backpressure_429"] += 1
                        elif response.status_code == 503:
                            errors["service_503"] += 1
                        else:
                            errors["http_error"] += 1
                        return False
                    result = strict_json(response.content)
                    validate_vectors(result.get("vectors"), len(texts))
                    if (result.get("organization_id") != organization_id
                            or result.get("request_id") != payload["request_id"]
                            or result.get("model_id") != MODEL_ID
                            or result.get("revision") != REVISION
                            or result.get("embedding_space") != SPACE_ID
                            or result.get("dimension") != DIMENSION
                            or result.get("artifact_sha256") != ARTIFACTS["onnx"]):
                        errors["response_contract"] += 1
                        return False
                    return True
                except httpx.TimeoutException:
                    latencies.append(time.perf_counter() - before)
                    errors["timeout"] += 1
                    return False
                except (httpx.HTTPError, ValueError, TypeError):
                    latencies.append(time.perf_counter() - before)
                    errors["request_or_response_error"] += 1
                    return False

        async def run_activity(activity: dict[str, Any]) -> None:
            nonlocal successful_activities
            results = await asyncio.gather(
                send(activity, "query", [activity["query"]]),
                send(activity, "passage", [activity["passage"]]),
            )
            successful_activities += int(all(results))

        try:
            await asyncio.gather(*(run_activity(activity) for activity in activities))
        finally:
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)

    elapsed = time.perf_counter() - start
    cpu_after, peak_rss = _usage()
    return {
        "client_process": client_index,
        "activities": len(activities),
        "successful_activities": successful_activities,
        "requests": len(latencies),
        "latencies_seconds": latencies,
        "statuses": dict(statuses),
        "errors": dict(errors),
        "max_observed_in_flight_and_queued": max_queued,
        "elapsed_seconds": round(elapsed, 6),
        "cpu_seconds": round(max(0.0, (cpu_after or 0) - (cpu_before or 0)), 6) if cpu_after is not None else None,
        "peak_rss_bytes": peak_rss,
    }


def _child_entry(client_index: int, activities: list[dict[str, Any]], base_url: str,
                 concurrency: int, timeout_seconds: float, output_queue: Any) -> None:
    try:
        output_queue.put({"ok": True, "result": asyncio.run(
            _client_process(client_index, activities, base_url, concurrency, timeout_seconds))})
    except Exception as exc:
        # Keep exception text out of receipts: HTTP client diagnostics can contain
        # untrusted response material, and credentials never belong in output.
        output_queue.put({"ok": False, "error": type(exc).__name__})


def _run_tier(size: int, base_url: str, clients: int, concurrency: int,
              timeout_seconds: float, startup_context: mp.context.BaseContext) -> dict[str, Any]:
    queries, passages, expected, languages = _activity_fixture(size)
    activity_items = []
    for index, (query, passage, relevant, language) in enumerate(zip(queries, passages, expected, languages)):
        activity_items.append({
            "activity_id": f"synthetic-{index + 1:04d}", "query": query, "passage": passage,
            "relevant_passage_index": relevant, "language": language,
            "organization_id": str(uuid5(NAMESPACE_URL, f"inf01-client-{index % clients}-tenant-{index % 4}")),
        })
    buckets = [activity_items[index::clients] for index in range(clients)]
    result_queue = startup_context.Queue()
    processes = [startup_context.Process(
        target=_child_entry, args=(index, buckets[index], base_url, concurrency, timeout_seconds, result_queue),
    ) for index in range(clients)]
    wall_started = time.perf_counter()
    for process in processes:
        process.start()
    collected = []
    for _ in processes:
        try:
            collected.append(result_queue.get(timeout=timeout_seconds + 15))
        except Exception:
            collected.append({"ok": False, "error": "client_process_timeout"})
    for process in processes:
        process.join(timeout=2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
    result_queue.close()
    wall_seconds = time.perf_counter() - wall_started
    client_results = [entry["result"] for entry in collected if entry.get("ok")]
    latencies = [sample for result in client_results for sample in result["latencies_seconds"]]
    requests = sum(result["requests"] for result in client_results)
    statuses = Counter()
    errors = Counter()
    for entry in collected:
        if entry.get("ok"):
            statuses.update(entry["result"]["statuses"])
            errors.update(entry["result"]["errors"])
        else:
            errors[entry.get("error", "client_process_failed")] += 1
    successful_activities = sum(result["successful_activities"] for result in client_results)
    backpressure = int(errors.get("backpressure_429", 0))
    return {
        "activities": size,
        "synthetic_languages": {language: languages.count(language) for language in sorted(set(languages))},
        "client_processes": clients,
        "per_process_concurrency": concurrency,
        "successful_activities": successful_activities,
        "requests": requests,
        "error_events": sum(errors.values()),
        "successful_requests": int(statuses.get("200", 0)),
        "status_counts": dict(statuses),
        "errors": dict(errors),
        "backpressure_responses": backpressure,
        "backpressure_rate": round(backpressure / requests, 6) if requests else None,
        "latency_p50_seconds": _percentile(latencies, 0.50),
        "latency_p95_seconds": _percentile(latencies, 0.95),
        "latency_p99_seconds": _percentile(latencies, 0.99),
        "successful_activities_per_second": round(successful_activities / wall_seconds, 3) if wall_seconds else None,
        "wall_seconds": round(wall_seconds, 6),
        "client_cpu_seconds_total": round(sum(result["cpu_seconds"] or 0 for result in client_results), 6),
        "client_process_peak_rss_bytes": [result["peak_rss_bytes"] for result in client_results],
        "client_process_max_observed_in_flight_and_queued": [
            result["max_observed_in_flight_and_queued"] for result in client_results
        ],
        "client_process_errors": [entry["error"] for entry in collected if not entry.get("ok")],
        "service_rss_note": "Service RSS is not exposed by the HTTP contract; collect it from the container runtime.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="5,25,100", help="synthetic activity counts (default: 5,25,100)")
    parser.add_argument("--clients", type=int, default=3, help="lightweight client processes (default: 3)")
    parser.add_argument("--concurrency", type=int, default=8, help="in-flight requests per process (default: 8)")
    parser.add_argument("--timeout", type=float, default=10.0, help="per-request timeout seconds")
    parser.add_argument("--receipt", type=Path, help="optional JSON output path")
    args = parser.parse_args()
    sizes = tuple(int(part) for part in args.sizes.split(",") if part)
    if not sizes or any(size not in {5, 25, 100} for size in sizes) or len(set(sizes)) != len(sizes):
        parser.error("--sizes must contain unique values from 5,25,100")
    if not 1 <= args.clients <= 8 or not 1 <= args.concurrency <= 32 or not 1 <= args.timeout <= 120:
        parser.error("clients, concurrency, or timeout is outside the supported bound")
    try:
        base_url, _token = _env_config()
    except RuntimeError as exc:
        parser.error(str(exc))
    # Validate readiness and model identity before starting the load clients.
    health = asyncio.run(_initial_health(base_url, _token, min(args.timeout, 5)))
    context = mp.get_context("spawn")
    tiers = {}
    for size in sizes:
        tiers[str(size)] = _run_tier(size, base_url, args.clients, args.concurrency, args.timeout, context)
    receipt = {
        "benchmark": "inf01_shared_http_synthetic",
        "status": "complete" if all(not tier["client_process_errors"] for tier in tiers.values()) else "partial",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "service_fingerprint": health,
        "scope": "Synthetic activity DTOs over the internal embedding HTTP API; no database, PDF, provider, or customer-data workload.",
        "tiers": tiers,
    }
    serialized = json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if receipt["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
