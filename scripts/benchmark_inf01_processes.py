"""Compare one versus two verified PyTorch embedding workers in owned Docker only.

This probe has no database, provider, shared-service, or network clients. It
loads one frozen query per worker and writes a bounded receipt, including a
checkpoint after worker one so a failed second load still leaves evidence.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXPECTED_MARKER = "melpis-inf01-multiprocess-20261025"
MAX_CGROUP_BYTES = 2_684_354_560  # 2.5 GiB
EXPECTED_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
EXPECTED_ARTIFACT_SHA256 = "eaa086f0ffee582aeb45b36e34cdd1fe2d6de2bef61f8a559a1bbc9bd955917b"
EXPECTED_VOCABULARY_SHA256 = "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"
TOTAL_DEADLINE_SECONDS = 120
SAMPLE_INTERVAL_SECONDS = 0.20
REFERENCE_PATH = ROOT / "artifacts" / "inf01" / "torch-reference-5.json"
DEFAULT_RECEIPT = ROOT / "artifacts" / "inf01" / "processes-linux.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cgroup_root() -> Path:
    if not Path("/proc/self/cgroup").is_file():
        raise RuntimeError("proc_cgroup_missing")
    # The owned container is started with cgroup v2. Refuse ambiguous or
    # legacy layouts rather than guessing which ancestor owns the limit.
    entries = Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines()
    if not any(line.startswith("0::/") for line in entries):
        raise RuntimeError("cgroup_v2_required")
    return Path("/sys/fs/cgroup")


def _preflight() -> dict[str, Any]:
    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise RuntimeError("linux_container_required")
    if os.environ.get("INF01_OWNED_DISPOSABLE") != EXPECTED_MARKER:
        raise RuntimeError("owned_container_marker_required")
    if not Path("/.dockerenv").is_file():
        raise RuntimeError("docker_container_required")
    cgroup = _cgroup_root()
    try:
        limit = int((cgroup / "memory.max").read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        raise RuntimeError("finite_cgroup_memory_limit_required") from None
    if limit <= 0 or limit > MAX_CGROUP_BYTES:
        raise RuntimeError("cgroup_memory_limit_out_of_range")
    model_root = Path(os.environ.get("MELPIS_EMBEDDING_MODEL_DIR", ""))
    if model_root.name != EXPECTED_REVISION:
        raise RuntimeError("pinned_model_revision_required")
    weight = model_root / "model.safetensors"
    tokenizer = model_root / "tokenizer.json"
    if not weight.is_file() or not tokenizer.is_file():
        raise RuntimeError("verified_torch_assets_missing")
    weight_hash = _sha256(weight)
    tokenizer_hash = _sha256(tokenizer)
    if weight_hash != EXPECTED_ARTIFACT_SHA256 or tokenizer_hash != EXPECTED_VOCABULARY_SHA256:
        raise RuntimeError("verified_torch_assets_hash_mismatch")
    reference = _load_reference()
    if reference.get("model_snapshot") != EXPECTED_REVISION:
        raise RuntimeError("reference_revision_mismatch")
    if reference.get("weights_sha256") != EXPECTED_ARTIFACT_SHA256:
        raise RuntimeError("reference_artifact_mismatch")
    if reference.get("vocabulary_sha256") != EXPECTED_VOCABULARY_SHA256:
        raise RuntimeError("reference_tokenizer_mismatch")
    if reference.get("dimension") != 384 or not reference.get("normalized"):
        raise RuntimeError("reference_contract_mismatch")
    return {
        "container_marker_verified": True,
        "docker_marker_file_present": True,
        "cgroup_version": 2,
        "cgroup_memory_limit_bytes": limit,
        "model_revision": EXPECTED_REVISION,
        "weights_sha256": weight_hash,
        "vocabulary_sha256": tokenizer_hash,
        "reference_vectors_sha256": reference.get("vectors_sha256"),
        "network_or_external_clients": False,
    }


def _load_reference() -> dict[str, Any]:
    try:
        return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError("frozen_reference_unavailable") from None


def _proc_cpu_seconds(pid: int) -> float | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = raw[raw.rfind(")") + 2 :].split()
        ticks = int(fields[11]) + int(fields[12])
        return ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


def _proc_rss_bytes(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _cgroup_value(name: str) -> int | None:
    try:
        raw = (_cgroup_root() / name).read_text(encoding="ascii").strip()
        return int(raw) if raw and raw != "max" else None
    except (OSError, ValueError):
        return None


def _oom_kill_count() -> int | None:
    try:
        for line in (_cgroup_root() / "memory.events").read_text(encoding="ascii").splitlines():
            key, value = line.split()
            if key == "oom_kill":
                return int(value)
    except (OSError, ValueError):
        return None
    return None


def _validate_vector(vector: Any, expected: list[float]) -> dict[str, Any]:
    if (not isinstance(vector, list) or len(vector) != 384
            or any(type(value) not in {int, float} or not math.isfinite(value) for value in vector)):
        raise RuntimeError("worker_vector_shape_or_finiteness_invalid")
    norm = math.sqrt(sum(float(value) ** 2 for value in vector))
    if abs(norm - 1.0) > 0.002:
        raise RuntimeError("worker_vector_norm_invalid")
    if len(expected) != 384:
        raise RuntimeError("reference_vector_shape_invalid")
    dot = sum(float(left) * float(right) for left, right in zip(vector, expected))
    ref_norm = math.sqrt(sum(float(value) ** 2 for value in expected))
    cosine = dot / (norm * ref_norm) if ref_norm else float("nan")
    canonical = json.dumps(vector, separators=(",", ":"), allow_nan=False).encode("ascii")
    return {
        "dimension": 384,
        "l2_norm": round(norm, 9),
        "finite": True,
        "reference_cosine": round(cosine, 9),
        "vector_sha256": hashlib.sha256(canonical).hexdigest(),
    }


class _Sampler:
    def __init__(self, cgroup_start: dict[str, int | None]):
        self.cgroup_start = cgroup_start
        self.workers: list[Any] = []
        self.phase = "preload"
        self.stages: dict[str, dict[str, Any]] = {}
        self.task: asyncio.Task | None = None

    def set_workers(self, workers: list[Any], phase: str) -> None:
        self.workers = workers
        self.phase = phase

    async def run(self) -> None:
        while True:
            await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)
            pids = [worker.process.pid for worker in self.workers
                    if worker.process is not None and worker.process.returncode is None]
            rss_values = [value for pid in pids if (value := _proc_rss_bytes(pid)) is not None]
            cpu_values = [value for pid in pids if (value := _proc_cpu_seconds(pid)) is not None]
            current = _cgroup_value("memory.current")
            stage = self.stages.setdefault(self.phase, {
                "samples": 0,
                "peak_worker_rss_bytes": 0,
                "peak_cgroup_current_bytes": 0,
                "max_live_worker_processes": 0,
                "max_cgroup_processes": 0,
                "last_worker_cpu_seconds": 0.0,
            })
            stage["samples"] += 1
            stage["peak_worker_rss_bytes"] = max(stage["peak_worker_rss_bytes"], sum(rss_values))
            if current is not None:
                stage["peak_cgroup_current_bytes"] = max(stage["peak_cgroup_current_bytes"], current)
            stage["max_live_worker_processes"] = max(stage["max_live_worker_processes"], len(pids))
            cgroup_processes = _cgroup_value("pids.current")
            if cgroup_processes is not None:
                stage["max_cgroup_processes"] = max(stage["max_cgroup_processes"], cgroup_processes)
            stage["last_worker_cpu_seconds"] = round(sum(cpu_values), 6)


def _atomic_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


async def _benchmark(receipt_path: Path) -> dict[str, Any]:
    started_at = time.monotonic()
    preflight = _preflight()
    reference = _load_reference()
    try:
        activity = reference["activities"][0]
        query = activity["query"]
        expected = reference["query_vectors"][0]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("frozen_reference_query_missing") from None

    # Import only after all hard container, cgroup, model hash, and reference
    # fences pass. LocalWorker starts isolated child processes lazily.
    from src.core.documenti.isolated import LocalWorker

    workers: list[Any] = []
    cgroup_start = {
        "memory_current_bytes": _cgroup_value("memory.current"),
        "memory_peak_bytes": _cgroup_value("memory.peak"),
        "oom_kill_count": _oom_kill_count(),
    }
    sampler = _Sampler(cgroup_start)
    receipt: dict[str, Any] = {
        "benchmark": "INF01_TORCH_PROCESS_MEMORY_COMPARISON",
        "status": "running",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "preflight": preflight,
        "query": {"activity_id": activity.get("activity_id"),
                  "language": activity.get("language"),
                  "sha256": hashlib.sha256(query.encode("utf-8")).hexdigest()},
        "workers": [],
        "stages": {},
        "cgroup_start": cgroup_start,
        "limitations": [
            "Only one frozen synthetic query is encoded per worker.",
            "This measures process memory and one-versus-two model residency, not request capacity or production throughput.",
            "The second-worker result is valid only if both workers reach ready and pass vector validation.",
        ],
    }
    sampler.task = asyncio.create_task(sampler.run())
    try:
        remaining_seconds = max(0.0, TOTAL_DEADLINE_SECONDS - (time.monotonic() - started_at))
        async with asyncio.timeout(remaining_seconds):
            for index in range(2):
                worker = LocalWorker("embed_verified")
                workers.append(worker)
                sampler.set_workers(workers, f"loading_worker_{index + 1}")
                load_started = time.monotonic()
                await worker.ensure_started()
                load_seconds = time.monotonic() - load_started
                sampler.set_workers(workers, f"encoding_worker_{index + 1}")
                encode_started = time.monotonic()
                vectors = await worker.call({"texts": [query], "tipo": "query"}, timeout=15)
                encode_seconds = time.monotonic() - encode_started
                validation = _validate_vector(vectors[0] if isinstance(vectors, list) and vectors else None, expected)
                receipt["workers"].append({
                    "ordinal": index + 1,
                    "pid": worker.process.pid if worker.process else None,
                    "startup_seconds": round(load_seconds, 6),
                    "one_query_seconds": round(encode_seconds, 6),
                    "quality": validation,
                })
                receipt["stages"] = sampler.stages
                receipt["cgroup_checkpoint"] = {
                    "memory_current_bytes": _cgroup_value("memory.current"),
                    "memory_peak_bytes": _cgroup_value("memory.peak"),
                    "oom_kill_count": _oom_kill_count(),
                    "pids_current": _cgroup_value("pids.current"),
                }
                receipt["elapsed_seconds"] = round(time.monotonic() - started_at, 6)
                receipt["status"] = "one_worker_complete" if index == 0 else "complete"
                # The first-model checkpoint is durable before we attempt the
                # second model load, so OOM or startup failure preserves it.
                _atomic_receipt(receipt_path, receipt)
    except asyncio.TimeoutError:
        receipt["status"] = "deadline_exceeded"
        receipt["failure_type"] = "TimeoutError"
    except BaseException as exc:
        receipt["status"] = "second_worker_failed" if len(workers) >= 2 and len(receipt["workers"]) == 1 else "failed"
        receipt["failure_type"] = type(exc).__name__
    finally:
        sampler.task.cancel()
        await asyncio.gather(sampler.task, return_exceptions=True)
        receipt["stages"] = sampler.stages
        receipt["cgroup_end"] = {
            "memory_current_bytes": _cgroup_value("memory.current"),
            "memory_peak_bytes": _cgroup_value("memory.peak"),
            "oom_kill_count": _oom_kill_count(),
            "pids_current_before_worker_close": _cgroup_value("pids.current"),
        }
        receipt["elapsed_seconds"] = round(time.monotonic() - started_at, 6)
        receipt["worker_returncodes_before_close"] = [
            worker.process.returncode if worker.process else None for worker in workers
        ]
        # Close only the LocalWorker instances created by this harness.
        owned_processes = [worker.process for worker in workers]
        for worker in reversed(workers):
            await worker.close()
        receipt["worker_returncodes_after_close"] = [
            process.returncode if process else None for process in owned_processes
        ]
        if receipt.get("status") == "running":
            receipt["status"] = "failed"
        _atomic_receipt(receipt_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()
    try:
        result = asyncio.run(_benchmark(args.receipt))
    except Exception as exc:
        result = {
            "benchmark": "INF01_TORCH_PROCESS_MEMORY_COMPARISON",
            "status": "preflight_failed",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "failure_type": type(exc).__name__,
        }
        _atomic_receipt(args.receipt, result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result.get("status") == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
