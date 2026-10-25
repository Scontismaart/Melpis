"""Offline FP32/INT8 ONNX embedding parity and capacity diagnostic.

This script reads only locally provisioned assets and synthetic fixtures. It
does not contact a provider, database, or network service and never imports
PyTorch or Transformers.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
import os
import sys
import time
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_ASSET_ROOT = Path.home() / ".cache/melpis-embedding" / REVISION
DEFAULT_REFERENCE = ROOT / "artifacts/inf01/torch-reference-5.json"
DEFAULT_RECEIPT = ROOT / "artifacts/inf01/onnx-runtime-results.json"
MEMORY_CAP_BYTES = int(1.5 * 1024**3)
MEMORY_HEADROOM_BYTES = int(0.85 * 1024**3)
MIN_AVAILABLE_BYTES = MEMORY_CAP_BYTES + MEMORY_HEADROOM_BYTES
MIN_AVAILABLE_COMMIT_BYTES = int(3 * 1024**3)
STOP_AVAILABLE_BYTES = int(0.75 * 1024**3)
STARTUP_TIMEOUT_SECONDS = 60
RUN_TIMEOUT_SECONDS = 60
TOTAL_TIMEOUT_SECONDS = 120
SIZES = (5, 25, 100)
EXPECTED_VOCABULARY_SHA256 = "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"
FP32_COSINE_THRESHOLD = 0.99999
INT8_COSINE_THRESHOLD = 0.995


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _distribution_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _global_memory_status() -> dict[str, int] | None:
    if os.name != "nt":
        return None
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return {"available_physical_bytes": int(status.ullAvailPhys), "available_commit_bytes": int(status.ullAvailPageFile)}


def _cgroup_memory_status() -> dict[str, int | None]:
    result: dict[str, int | None] = {"limit_bytes": None, "current_bytes": None, "peak_bytes": None}
    for key, filename in (("limit_bytes", "memory.max"), ("current_bytes", "memory.current"), ("peak_bytes", "memory.peak")):
        try:
            value = (Path("/sys/fs/cgroup") / filename).read_text(encoding="ascii").strip()
            result[key] = None if value == "max" else int(value)
        except (OSError, ValueError):
            pass
    return result


def _linux_process_memory(pid: int) -> tuple[int | None, int | None]:
    try:
        values: dict[str, int] = {}
        for line in (Path("/proc") / str(pid) / "status").read_text(encoding="ascii").splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                name, raw, unit = line.split()[:3]
                values[name.rstrip(":")] = int(raw) * (1024 if unit == "kB" else 1)
        return values.get("VmRSS"), values.get("VmHWM")
    except (OSError, ValueError):
        return None, None


def _attach_windows_job(memory_bytes: int) -> tuple[Any, Any, int]:
    """Cap this process before importing native inference libraries."""
    if os.name != "nt":
        return None, None, 0
    import ctypes
    from ctypes import wintypes

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessAffinityMask.restype = wintypes.BOOL
    kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.restype = wintypes.BOOL
    kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = ExtendedLimit()
    # The limited process is this harness itself, so closing its job at normal
    # exit must not terminate the harness after it writes the receipt.
    info.BasicLimitInformation.LimitFlags = 0x00000100  # PROCESS_MEMORY
    info.ProcessMemoryLimit = memory_bytes
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel.CloseHandle(job)
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    # TERMINATE | SET_QUOTA | SET_INFORMATION | QUERY_INFORMATION
    process = kernel.OpenProcess(0x0001 | 0x0100 | 0x0200 | 0x0400, False, os.getpid())
    if not process:
        kernel.CloseHandle(job)
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    if not kernel.AssignProcessToJobObject(job, process):
        kernel.CloseHandle(process)
        kernel.CloseHandle(job)
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    process_mask = ctypes.c_size_t()
    system_mask = ctypes.c_size_t()
    affinity = None
    if kernel.GetProcessAffinityMask(process, ctypes.byref(process_mask), ctypes.byref(system_mask)):
        allowed = int(process_mask.value)
        selected = 0
        for bit in range(allowed.bit_length()):
            if allowed & (1 << bit):
                selected |= 1 << bit
                if selected.bit_count() >= 2:
                    break
        if selected and kernel.SetProcessAffinityMask(process, ctypes.c_size_t(selected)):
            affinity = selected
    return kernel, (job, process), affinity


def _close_windows_job(resources: tuple[Any, Any, int] | tuple[Any, Any] | None) -> None:
    if not resources:
        return
    kernel, handles = resources[0], resources[1]
    job, process = handles
    kernel.CloseHandle(process)
    kernel.CloseHandle(job)


@lru_cache(maxsize=1)
def _windows_metrics_module():
    spec = importlib.util.spec_from_file_location("_pdf_rag_metrics", ROOT / "scripts/benchmark_pdf_rag.py")
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _process_metrics() -> dict[str, float | int | None]:
    if os.name == "nt":
        module = _windows_metrics_module()
        return module._process_metrics(os.getpid()) if module else {"peak_rss_bytes": None, "cpu_user_seconds": None}
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        peak_rss = int(usage.ru_maxrss) * (1024 if sys.platform.startswith("linux") else 1)
        return {"peak_rss_bytes": peak_rss, "cpu_user_seconds": usage.ru_utime + usage.ru_stime}
    except (ImportError, OSError, ValueError):
        return {"peak_rss_bytes": None, "cpu_user_seconds": None}


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, math.ceil((len(ordered) - 1) * percentile)))
    return round(ordered[position], 6)


def _load_activities(size: int) -> tuple[list[str], list[str], list[int], list[str]]:
    spec = importlib.util.spec_from_file_location("_inf01_baseline_fixtures", ROOT / "scripts/benchmark_inf01.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("baseline fixture generator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._activities(size)


def _read_reference(path: Path) -> dict[str, Any]:
    reference = json.loads(path.read_text(encoding="utf-8"))
    supplied_hash = reference.get("vectors_sha256")
    canonical_data = {key: value for key, value in reference.items() if key != "vectors_sha256"}
    canonical = json.dumps(canonical_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if actual_hash != supplied_hash:
        raise RuntimeError("torch_reference_checksum_mismatch")
    if (reference.get("model_id") != MODEL_ID or reference.get("model_snapshot") != REVISION
            or reference.get("dimension") != 384 or reference.get("dtype") != "float32"
            or reference.get("normalized") is not True
            or reference.get("query_prefix") != "query: " or reference.get("passage_prefix") != "passage: "):
        raise RuntimeError("torch_reference_contract_mismatch")
    if not (len(reference.get("query_vectors", [])) == len(reference.get("passage_vectors", [])) == 5):
        raise RuntimeError("torch_reference_fixture_mismatch")
    return reference


def _cosine_rows(left: list[list[float]], right: list[list[float]]) -> list[float]:
    import numpy as np
    lhs, rhs = np.asarray(left, dtype=np.float32), np.asarray(right, dtype=np.float32)
    return np.sum(lhs * rhs, axis=1).astype(float).tolist()


def _rankings(query_vectors: list[list[float]], passage_vectors: list[list[float]]) -> list[list[int]]:
    import numpy as np
    scores = np.asarray(query_vectors, dtype=np.float32) @ np.asarray(passage_vectors, dtype=np.float32).T
    return np.argsort(-scores, axis=1).tolist()


def _quality(query_vectors: list[list[float]], passage_vectors: list[list[float]], expected: list[int], reference_rankings: list[list[int]] | None = None) -> dict[str, Any]:
    ranking = _rankings(query_vectors, passage_vectors)
    reciprocal_ranks = []
    recall5 = []
    correct = 0
    same_top5 = []
    for index, order in enumerate(ranking):
        position = order.index(expected[index]) + 1
        reciprocal_ranks.append(1 / position)
        recall5.append(float(position <= min(5, len(order))))
        correct += int(position == 1)
        if reference_rankings is not None:
            same_top5.append(order[:5] == reference_rankings[index][:5])
    result = {
        "queries": len(ranking),
        "top1_accuracy": round(correct / len(ranking), 4),
        "recall_at_5": round(sum(recall5) / len(ranking), 4),
        "mean_reciprocal_rank": round(sum(reciprocal_ranks) / len(ranking), 4),
    }
    if reference_rankings is not None:
        result["same_top5_rate"] = round(sum(same_top5) / len(same_top5), 4)
        result["same_top5_all"] = all(same_top5)
    return result


def _asset_manifest(asset_root: Path, quantized_sha256: str | None, require_int8: bool) -> dict[str, Any]:
    # The shared application verifier pins the FP32 ONNX file, tokenizer, and
    # mean-pooling contract. This is metadata/hash verification only.
    os.environ["MELPIS_EMBEDDING_MODEL_DIR"] = str(asset_root)
    sys.path.insert(0, str(ROOT))
    from src.core.documenti.embedding_contract import ARTIFACTS, VOCABULARY_SHA256
    from src.core.documenti.embedding_assets import verified_assets

    verified_root, fp32_path = verified_assets("onnx")
    quantized_path = verified_root / "onnx/model_quint8_avx2.onnx"
    quantized_actual = None
    if require_int8:
        if not quantized_path.is_file():
            raise RuntimeError("verified_int8_asset_missing")
        if not quantized_sha256:
            raise RuntimeError("int8_expected_sha256_required")
        quantized_actual = _sha256(quantized_path)
        if quantized_actual.lower() != quantized_sha256.lower():
            raise RuntimeError("int8_asset_checksum_mismatch")
    tokenizer_path = verified_root / "tokenizer.json"
    return {
        "root": str(verified_root),
        "fp32_path": str(fp32_path),
        "fp32_sha256": ARTIFACTS["onnx"],
        "fp32_bytes": fp32_path.stat().st_size,
        "int8_path": str(quantized_path) if require_int8 else None,
        "int8_sha256": quantized_actual,
        "int8_bytes": quantized_path.stat().st_size if require_int8 else None,
        "tokenizer_path": str(tokenizer_path),
        "vocabulary_sha256": VOCABULARY_SHA256,
        "dimension": 384,
        "max_sequence_length": 128,
        "pooling": "attention-mask mean, L2 normalization",
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
    }


class _Int8OnnxEncoder:
    """Use the pinned tokenizer and the application's exact pooling math."""
    def __init__(self, model_path: Path, tokenizer_path: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=128)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.inputs = {item.name for item in self.session.get_inputs()}
        if not self.inputs <= {"input_ids", "attention_mask", "token_type_ids"}:
            raise RuntimeError("unexpected_onnx_input_names")

    def encode(self, texts: list[str], tipo: str) -> list[list[float]]:
        import numpy as np
        prefix = "query: " if tipo == "query" else "passage: "
        tokens = self.tokenizer.encode_batch([prefix + text for text in texts])
        values = {
            "input_ids": np.asarray([token.ids for token in tokens], dtype=np.int64),
            "attention_mask": np.asarray([token.attention_mask for token in tokens], dtype=np.int64),
            "token_type_ids": np.asarray([token.type_ids for token in tokens], dtype=np.int64),
        }
        hidden = self.session.run(None, {name: values[name] for name in self.inputs})[0]
        mask = values["attention_mask"][..., None].astype(np.float32)
        pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
        normalized = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
        return normalized.astype(np.float32).tolist()


class _Fp32OnnxEncoder:
    """Thin adapter around the application's verified FP32 ONNX encoder."""
    def __init__(self):
        from src.core.documenti.embedding_assets import OfflineOnnxEncoder
        self.encoder = OfflineOnnxEncoder()

    def encode(self, texts: list[str], tipo: str) -> list[list[float]]:
        return self.encoder.encode(texts, tipo)


def _preflight(asset_root: Path, reference_path: Path, sizes: tuple[int, ...], engine: str, int8_sha256: str | None) -> dict[str, Any]:
    if "torch" in sys.modules or "transformers" in sys.modules:
        raise RuntimeError("forbidden_torch_or_transformers_import")
    if os.name == "nt":
        status = _global_memory_status()
        available = status["available_physical_bytes"] if status else None
        commit = status["available_commit_bytes"] if status else None
        if available is None or available < MIN_AVAILABLE_BYTES or commit is None or commit < MIN_AVAILABLE_COMMIT_BYTES:
            raise RuntimeError("insufficient_windows_memory_headroom")
    else:
        cgroup = _cgroup_memory_status()
        limit, current = cgroup.get("limit_bytes"), cgroup.get("current_bytes")
        if limit is None or current is None or limit < MEMORY_CAP_BYTES or limit - current < STOP_AVAILABLE_BYTES:
            raise RuntimeError("insufficient_cgroup_memory_headroom")
    reference = _read_reference(reference_path)
    manifest = _asset_manifest(asset_root, int8_sha256, engine in {"int8", "both"})
    if engine in {"fp32", "both"} and not Path(manifest["fp32_path"]).is_file():
        raise RuntimeError("verified_fp32_asset_missing")
    if engine in {"int8", "both"} and not int8_sha256:
        raise RuntimeError("int8_expected_sha256_required")
    return {"reference": reference, "manifest": manifest, "sizes": sizes}


def _current_headroom() -> int | None:
    if os.name == "nt":
        status = _global_memory_status()
        return status["available_physical_bytes"] if status else None
    cgroup = _cgroup_memory_status()
    if cgroup.get("limit_bytes") is not None and cgroup.get("current_bytes") is not None:
        return int(cgroup["limit_bytes"] - cgroup["current_bytes"])
    return None


def _run_engine(engine: str, asset_root: Path, manifest: dict[str, Any], reference: dict[str, Any], sizes: tuple[int, ...], total_deadline: float) -> dict[str, Any]:
    if _current_headroom() is not None and _current_headroom() < STOP_AVAILABLE_BYTES:
        raise RuntimeError("available headroom fell below 0.75 GiB before engine load")
    if engine == "fp32":
        started = time.perf_counter()
        model_load_cpu_start = time.process_time()
        encoder = _Fp32OnnxEncoder()
    else:
        started = time.perf_counter()
        model_load_cpu_start = time.process_time()
        encoder = _Int8OnnxEncoder(Path(manifest["int8_path"]), Path(manifest["tokenizer_path"]))
    model_load_seconds = time.perf_counter() - started
    model_load_cpu_seconds = time.process_time() - model_load_cpu_start
    if model_load_seconds > STARTUP_TIMEOUT_SECONDS:
        raise TimeoutError(f"{engine} model startup exceeded {STARTUP_TIMEOUT_SECONDS} seconds")
    if "torch" in sys.modules or "transformers" in sys.modules:
        raise RuntimeError("forbidden_torch_or_transformers_import")
    print(json.dumps({"engine_ready": engine, "load_seconds": round(model_load_seconds, 6)}), flush=True)

    torch_queries = reference["query_vectors"]
    torch_passages = reference["passage_vectors"]
    torch_rankings = _rankings(torch_queries, torch_passages)
    metrics_at_start = _process_metrics()
    observed_peak_rss = metrics_at_start.get("peak_rss_bytes") or 0
    process_cpu_at_start = time.process_time()
    outcomes: dict[str, Any] = {
        "status": "complete", "model_load_seconds": round(model_load_seconds, 6),
        "model_load_cpu_seconds": round(model_load_cpu_seconds, 6), "tiers": {}, "vectors": {},
    }
    all_started = time.perf_counter()
    for size in sizes:
        if time.perf_counter() - all_started > RUN_TIMEOUT_SECONDS or time.perf_counter() > total_deadline:
            raise TimeoutError(f"{engine} execution exceeded {RUN_TIMEOUT_SECONDS} seconds")
        queries, passages, expected, languages = _load_activities(size)
        if size == 5 and queries != [row["query"] for row in reference["activities"]]:
            raise RuntimeError("torch_reference_fixture_mismatch")
        if size == 5 and passages != [row["passage"] for row in reference["activities"]]:
            raise RuntimeError("torch_reference_fixture_mismatch")

        passage_vectors: list[list[float]] = []
        passage_cpu_start = time.process_time()
        started = time.perf_counter()
        for offset in range(0, size, 16):
            if time.perf_counter() > total_deadline:
                raise TimeoutError("120-second total benchmark deadline reached")
            if _current_headroom() is not None and _current_headroom() < STOP_AVAILABLE_BYTES:
                raise RuntimeError("available headroom fell below 0.75 GiB during passage embeddings")
            passage_vectors.extend(encoder.encode(passages[offset:offset + 16], "passage"))
            observed_peak_rss = max(observed_peak_rss, _process_metrics().get("peak_rss_bytes") or 0)
        passage_seconds = time.perf_counter() - started
        passage_cpu_seconds = time.process_time() - passage_cpu_start

        # Record a cold first query separately, then time warm query embedding
        # plus in-memory cosine ranking for one query at a time.
        started = time.perf_counter()
        encoder.encode([queries[0]], "query")
        cold_first_query_seconds = time.perf_counter() - started
        latencies: list[float] = []
        query_vectors: list[list[float]] = []
        query_cpu_start = time.process_time()
        query_samples = max(size, 10)
        for sample_index in range(query_samples):
            if time.perf_counter() > total_deadline:
                raise TimeoutError("120-second total benchmark deadline reached")
            if _current_headroom() is not None and _current_headroom() < STOP_AVAILABLE_BYTES:
                raise RuntimeError("available headroom fell below 0.75 GiB during query embeddings")
            query_index = sample_index % size
            query_text = queries[query_index]
            started = time.perf_counter()
            encoded = encoder.encode([query_text], "query")[0]
            _ = _rankings([encoded], passage_vectors)
            latencies.append(time.perf_counter() - started)
            observed_peak_rss = max(observed_peak_rss, _process_metrics().get("peak_rss_bytes") or 0)
            if sample_index < size:
                query_vectors.append(encoded)
        query_cpu_seconds = time.process_time() - query_cpu_start

        # Retrieval quality is computed once per distinct activity, not from
        # the repeated latency samples used by the 5-item tier.
        quality_query_vectors = query_vectors
        quality_passage_vectors = passage_vectors
        candidate_rankings = _rankings(quality_query_vectors, quality_passage_vectors)
        quality = _quality(quality_query_vectors, quality_passage_vectors, expected)
        tier: dict[str, Any] = {
            "activities": size,
            "queries": size,
            "passages": size,
            "embedding_dimensions": len(passage_vectors[0]) if passage_vectors else 0,
            "passage_embedding_batch_size": 16,
            "passage_embedding_seconds": round(passage_seconds, 6),
            "passage_embedding_cpu_seconds": round(passage_cpu_seconds, 6),
            "passage_throughput_activities_per_second": round(size / passage_seconds, 3) if passage_seconds else None,
            "cold_first_query_seconds": round(cold_first_query_seconds, 6),
            "query_retrieval_samples": len(latencies),
            "query_retrieval_cpu_seconds": round(query_cpu_seconds, 6),
            "query_retrieval_p50_seconds": _percentile(latencies, 0.50),
            "query_retrieval_p95_seconds": _percentile(latencies, 0.95),
            "query_retrieval_p99_seconds": _percentile(latencies, 0.99),
            "retrieval_throughput_queries_per_second": round(len(latencies) / sum(latencies), 3) if sum(latencies) else None,
            "languages": {key: languages.count(key) for key in sorted(set(languages))},
            "retrieval_quality": quality,
        }
        if size == 5:
            torch_query_cosines = _cosine_rows(quality_query_vectors, torch_queries)
            torch_passage_cosines = _cosine_rows(passage_vectors, torch_passages)
            torch_cosines = torch_query_cosines + torch_passage_cosines
            torch_parity = {
                "query_cosine_min": round(min(torch_query_cosines), 8),
                "query_cosine_mean": round(sum(torch_query_cosines) / len(torch_query_cosines), 8),
                "passage_cosine_min": round(min(torch_passage_cosines), 8),
                "passage_cosine_mean": round(sum(torch_passage_cosines) / len(torch_passage_cosines), 8),
                "all_cosines_above_threshold": min(torch_cosines) >= (FP32_COSINE_THRESHOLD if engine == "fp32" else INT8_COSINE_THRESHOLD),
                "threshold": FP32_COSINE_THRESHOLD if engine == "fp32" else INT8_COSINE_THRESHOLD,
                "same_top5_all_queries_as_torch": all(candidate_rankings[i][:5] == torch_rankings[i][:5] for i in range(5)),
            }
            tier["torch_parity"] = torch_parity
        outcomes["tiers"][str(size)] = tier
        outcomes["vectors"][str(size)] = {"queries": quality_query_vectors, "passages": quality_passage_vectors}
    outcomes["wall_seconds"] = round(time.perf_counter() - all_started, 6)
    final_metrics = _process_metrics()
    outcomes["process_peak_rss_bytes"] = max(observed_peak_rss, final_metrics.get("peak_rss_bytes") or 0) or None
    outcomes["process_cpu_seconds"] = round(time.process_time() - process_cpu_at_start, 6)
    del encoder
    gc.collect()
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--engine", choices=("both", "fp32", "int8"), default="both")
    parser.add_argument("--int8-sha256", help="root-verified SHA-256 for model_quint8_avx2.onnx")
    parser.add_argument("--sizes", default="5,25,100")
    args = parser.parse_args()
    sizes = tuple(int(part) for part in args.sizes.split(","))
    if not sizes or any(size not in SIZES for size in sizes):
        raise SystemExit("sizes must be a comma-separated subset of 5,25,100")

    started = time.perf_counter()
    receipt: dict[str, Any] = {
        "benchmark": "INF01 offline ONNX embedding parity and retrieval diagnostic",
        "recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "network_access": False,
        "database_access": False,
        "customer_data": False,
        "workload_unit": "synthetic activities; not pages or tenants",
        "sizes": list(sizes),
        "engine_selection": args.engine,
        "model_id": MODEL_ID,
        "revision": REVISION,
        "reference_file": str(args.reference),
        "reference_vectors_sha256": None,
        "asset_root": str(args.asset_root),
        "memory_cap_bytes": MEMORY_CAP_BYTES if os.name == "nt" else None,
        "minimum_preflight_headroom_bytes": MIN_AVAILABLE_BYTES if os.name == "nt" else STOP_AVAILABLE_BYTES,
        "minimum_preflight_commit_bytes": MIN_AVAILABLE_COMMIT_BYTES if os.name == "nt" else None,
        "stop_headroom_bytes": STOP_AVAILABLE_BYTES,
        "thread_limits": {"onnxruntime_intra_op": 2, "onnxruntime_inter_op": 1, "worker_affinity": "two CPUs"},
        "runtime_versions": {"onnxruntime": _distribution_version("onnxruntime"), "tokenizers": _distribution_version("tokenizers"), "numpy": _distribution_version("numpy"), "torch_imported": False, "transformers_imported": False},
        "engines": {},
        "limitations": [
            "Small frozen synthetic multilingual fixture; diagnostic parity only, not production retrieval certification.",
            "No PDF, database, API, WhatsApp traffic, or provider calls are measured.",
            "Run engines sequentially and unload each session before creating the next one.",
        ],
    }
    windows_status = _global_memory_status()
    receipt["preflight_available_physical_memory_bytes"] = windows_status.get("available_physical_bytes") if windows_status else None
    receipt["preflight_available_commit_bytes"] = windows_status.get("available_commit_bytes") if windows_status else None
    receipt["cgroup_memory_preflight"] = _cgroup_memory_status() if sys.platform.startswith("linux") else None

    job_resources = None
    try:
        prepared = _preflight(args.asset_root, args.reference, sizes, args.engine, args.int8_sha256)
        reference, manifest = prepared["reference"], prepared["manifest"]
        receipt["reference_vectors_sha256"] = reference["vectors_sha256"]
        receipt["assets"] = manifest
        receipt["quality_thresholds"] = {"fp32_cosine_min": FP32_COSINE_THRESHOLD, "int8_cosine_min": INT8_COSINE_THRESHOLD, "rank_condition": "identical top-5 ordering for every Torch reference query"}
        if os.name == "nt":
            kernel, handles, affinity = _attach_windows_job(MEMORY_CAP_BYTES)
            job_resources = (kernel, handles, affinity)
            receipt["worker_affinity_mask"] = affinity

        engine_names = ("fp32", "int8") if args.engine == "both" else (args.engine,)
        vectors_by_engine: dict[str, dict[str, Any]] = {}
        for engine in engine_names:
            if time.perf_counter() - started > TOTAL_TIMEOUT_SECONDS:
                receipt["engines"][engine] = {"status": "skipped", "reason": "120-second total hard deadline reached"}
                break
            headroom = _current_headroom()
            required_headroom = MIN_AVAILABLE_BYTES if os.name == "nt" else STOP_AVAILABLE_BYTES
            if headroom is not None and headroom < required_headroom:
                receipt["engines"][engine] = {"status": "skipped", "reason": f"preflight headroom below {required_headroom} bytes"}
                break
            try:
                receipt["engines"][engine] = {"status": "running"}
                args.receipt.parent.mkdir(parents=True, exist_ok=True)
                receipt["elapsed_seconds"] = round(time.perf_counter() - started, 6)
                args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
                result = _run_engine(engine, args.asset_root, manifest, reference, sizes, started + TOTAL_TIMEOUT_SECONDS)
                vectors_by_engine[engine] = result.pop("vectors")
                receipt["engines"][engine] = result
                tier5 = receipt["engines"][engine]["tiers"].get("5")
                receipt["engines"][engine]["parity_pass"] = bool(
                    tier5 and tier5.get("torch_parity", {}).get("all_cosines_above_threshold")
                    and tier5.get("torch_parity", {}).get("same_top5_all_queries_as_torch")
                ) if engine in {"fp32", "int8"} else None
                if "fp32" in vectors_by_engine and "int8" in vectors_by_engine:
                    import numpy as np
                    comparisons = {}
                    for size in sizes:
                        fp, quant = vectors_by_engine["fp32"][str(size)], vectors_by_engine["int8"][str(size)]
                        q_cos = _cosine_rows(quant["queries"], fp["queries"])
                        p_cos = _cosine_rows(quant["passages"], fp["passages"])
                        fp_rank = _rankings(fp["queries"], fp["passages"])
                        q_rank = _rankings(quant["queries"], quant["passages"])
                        comparisons[str(size)] = {
                            "query_cosine_min": round(min(q_cos), 8), "passage_cosine_min": round(min(p_cos), 8),
                            "same_top5_rate": round(sum(q_rank[i][:5] == fp_rank[i][:5] for i in range(len(q_rank))) / len(q_rank), 4),
                            "same_top5_all": all(q_rank[i][:5] == fp_rank[i][:5] for i in range(len(q_rank))),
                        }
                    receipt["fp32_vs_int8"] = comparisons
                    receipt["int8_vs_fp32_quality_pass"] = all(
                        entry["query_cosine_min"] >= INT8_COSINE_THRESHOLD
                        and entry["passage_cosine_min"] >= INT8_COSINE_THRESHOLD
                        and entry["same_top5_all"] for entry in comparisons.values()
                    )
                args.receipt.parent.mkdir(parents=True, exist_ok=True)
                receipt["elapsed_seconds"] = round(time.perf_counter() - started, 6)
                args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
                gc.collect()
            except Exception as exc:
                receipt["engines"][engine] = {"status": "failed", "failure": f"{type(exc).__name__}: {str(exc).splitlines()[0][:240]}"}
                args.receipt.parent.mkdir(parents=True, exist_ok=True)
                receipt["elapsed_seconds"] = round(time.perf_counter() - started, 6)
                args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
                break
        receipt["cgroup_memory_after"] = _cgroup_memory_status() if sys.platform.startswith("linux") else None
        receipt["elapsed_seconds"] = round(time.perf_counter() - started, 6)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    except Exception as exc:
        receipt["preflight_failure"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:240]}"
        receipt["elapsed_seconds"] = round(time.perf_counter() - started, 6)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    finally:
        _close_windows_job(job_resources)
    print(f"Wrote ONNX benchmark receipt: {args.receipt}")


if __name__ == "__main__":
    main()
