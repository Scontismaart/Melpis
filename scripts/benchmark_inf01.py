"""Bounded, offline INF01 embedding and synthetic retrieval benchmark.

This harness measures model inference only; it never opens a database, PDF, or
provider connection. Workload sizes are counts of synthetic activities, not
pages, tenants, customer records, or a capacity entitlement.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_CACHE = Path.home() / ".cache/huggingface/hub/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/snapshots"
MODEL_SNAPSHOT = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
MODEL_PATH = MODEL_CACHE / MODEL_SNAPSHOT
MODEL_SHA256 = "eaa086f0ffee582aeb45b36e34cdd1fe2d6de2bef61f8a559a1bbc9bd955917b"
MEMORY_CAP_BYTES = int(2 * 1024**3)
MIN_AVAILABLE_BYTES = int(3 * 1024**3)
MIN_AVAILABLE_COMMIT_BYTES = int(4 * 1024**3)
STOP_AVAILABLE_BYTES = int(0.75 * 1024**3)
STARTUP_TIMEOUT_SECONDS = 60 if os.name == "nt" else 300
RUN_TIMEOUT_SECONDS = 120 if os.name == "nt" else 300
TOTAL_TIMEOUT_SECONDS = 900
SIZES = (5, 25, 100)


# Frozen, fictional questions and matching passages. The rotation preserves
# meaning while varying harmless identifying details for each activity.
CASES = [
    ("it", "A che ora apre la caffetteria?", "La caffetteria apre alle otto del mattino."),
    ("es", "¿A qué hora abre la cafetería?", "La cafetería abre a las ocho de la mañana."),
    ("en", "What time does the cafe open?", "The cafe opens at eight in the morning."),
    ("it", "La cucina è aperta la domenica?", "La cucina è chiusa la domenica."),
    ("es", "¿La cocina abre los domingos?", "La cocina permanece cerrada los domingos."),
    ("en", "Is the kitchen open on Sunday?", "The kitchen is closed on Sundays."),
    ("it", "Come posso prenotare un tavolo?", "Per prenotare un tavolo, chiama la reception."),
    ("es", "¿Cómo puedo reservar una mesa?", "Para reservar una mesa, llama a recepción."),
    ("en", "How can I reserve a table?", "To reserve a table, call the reception desk."),
    ("it", "Sono disponibili opzioni vegane?", "Sono disponibili piatti vegani su richiesta."),
    ("es", "¿Hay opciones veganas disponibles?", "Hay platos veganos disponibles si los solicitas."),
    ("en", "Are vegan options available?", "Vegan dishes are available on request."),
    ("it", "La connessione Wi-Fi è gratuita?", "Il Wi-Fi è gratuito per tutti gli ospiti."),
    ("es", "¿El acceso Wi-Fi es gratuito?", "El Wi-Fi es gratuito para todos los clientes."),
    ("en", "Is Wi-Fi access free?", "Wi-Fi is free for all guests."),
    ("it", "Accettate pagamenti con carta?", "Accettiamo pagamenti con carta e contanti."),
    ("es", "¿Aceptan pagos con tarjeta?", "Aceptamos pagos con tarjeta y en efectivo."),
    ("en", "Can I pay by card?", "We accept card and cash payments."),
    ("it", "Dove si trova l'ingresso accessibile?", "L'ingresso accessibile si trova sul lato nord."),
    ("es", "¿Dónde está la entrada accesible?", "La entrada accesible está en el lado norte."),
    ("en", "Where is the step-free entrance?", "The step-free entrance is on the north side."),
    ("it", "Posso portare il cane?", "I cani sono ammessi nell'area esterna."),
    ("es", "¿Puedo traer a mi perro?", "Se permiten perros en la zona exterior."),
    ("en", "May I bring my dog?", "Dogs are welcome in the outdoor seating area."),
    ("it", "Qual è il numero per contattare il locale?", "Puoi contattare il locale al numero 020 555 0142."),
    ("es", "¿Cuál es el teléfono del local?", "Puedes llamar al local al 020 555 0142."),
    ("en", "What is the venue phone number?", "You can call the venue at 020 555 0142."),
    ("it", "Fino a che ora servite la colazione?", "La colazione viene servita fino alle undici."),
    ("es", "¿Hasta qué hora sirven el desayuno?", "El desayuno se sirve hasta las once."),
    ("en", "Until what time is breakfast served?", "Breakfast is served until eleven."),
    ("it", "Avete seggioloni per bambini?", "I seggioloni per bambini sono disponibili su richiesta."),
    ("es", "¿Tienen tronas para niños?", "Hay tronas disponibles si las solicitas."),
    ("en", "Do you have high chairs for children?", "High chairs for children are available on request."),
]


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
    return round(ordered[index], 6)


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


def _global_available_memory() -> int | None:
    status = _global_memory_status()
    return status["available_physical_bytes"] if status else None


def _cgroup_memory_status() -> dict[str, int | None]:
    """Read cgroup-v2 memory counters without assuming a particular container runtime."""
    result: dict[str, int | None] = {"limit_bytes": None, "current_bytes": None, "peak_bytes": None, "oom_kill_count": None}
    for key, filename in (("limit_bytes", "memory.max"), ("current_bytes", "memory.current"), ("peak_bytes", "memory.peak")):
        path = Path("/sys/fs/cgroup") / filename
        try:
            raw = path.read_text(encoding="ascii").strip()
            result[key] = None if raw == "max" else int(raw)
        except (OSError, ValueError):
            continue
    try:
        events = dict(line.split() for line in (Path("/sys/fs/cgroup/memory.events")).read_text(encoding="ascii").splitlines())
        result["oom_kill_count"] = int(events.get("oom_kill", "0"))
    except (OSError, ValueError):
        pass
    return result


def _linux_process_memory(pid: int) -> tuple[int | None, int | None]:
    """Return current and high-water resident bytes from procfs, when available."""
    try:
        fields = {}
        for line in (Path("/proc") / str(pid) / "status").read_text(encoding="ascii").splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                name, value, unit = line.split()[:3]
                fields[name.rstrip(":")] = int(value) * (1024 if unit == "kB" else 1)
        return fields.get("VmRSS"), fields.get("VmHWM")
    except (OSError, ValueError):
        return None, None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _distribution_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _attach_bounded_job(pid: int) -> tuple[Any, Any]:
    """Attach a process-memory-limited Windows Job before the worker loads torch."""
    if os.name != "nt":
        return None, None
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
    kernel.SetProcessAffinityMask.restype = wintypes.BOOL
    kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00002000  # PROCESS_MEMORY | KILL_ON_JOB_CLOSE
    info.ProcessMemoryLimit = MEMORY_CAP_BYTES
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel.CloseHandle(job)
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    process = kernel.OpenProcess(0x0001 | 0x0100 | 0x0400, False, pid)  # TERMINATE | SET_QUOTA | QUERY_LIMITED_INFORMATION
    if not process:
        kernel.CloseHandle(job)
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    if not kernel.AssignProcessToJobObject(job, process):
        kernel.CloseHandle(process)
        kernel.CloseHandle(job)
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
    # The selected mask is restricted to the first two logical CPUs.
    kernel.SetProcessAffinityMask(process, ctypes.c_size_t(0x3))
    return kernel, (job, process)


def _close_job(resources: tuple[Any, Any] | None) -> None:
    if not resources:
        return
    kernel, handles = resources
    job, process = handles
    kernel.CloseHandle(process)
    kernel.CloseHandle(job)


def _activities(size: int) -> tuple[list[str], list[str], list[int], list[str]]:
    queries, passages, expected, languages = [], [], [], []
    for index in range(size):
        language, query, passage = CASES[index % len(CASES)]
        tag = f"Synthetic activity {index + 1}"
        queries.append(f"{query} {tag}")
        passages.append(f"{passage} {tag}")
        expected.append(index)
        languages.append(language)
    # Add fixed, cross-topic distractors to make nearest-neighbor ranking meaningful.
    return queries, passages, expected, languages


def _worker(model_path: Path, sizes: tuple[int, ...], checkpoint_path: Path | None = None) -> dict[str, Any]:
    print(json.dumps({"pid": os.getpid()}), flush=True)
    if sys.stdin.readline().strip() != '{"start":true}':
        raise RuntimeError("benchmark parent did not attach the memory-limited job")
    if os.name != "nt":
        try:
            allowed_cpus = sorted(os.sched_getaffinity(0))
            os.sched_setaffinity(0, set(allowed_cpus[:2]))
        except (AttributeError, OSError):
            pass
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
    sys.path.insert(0, str(ROOT))
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(2)
    model_load_start = time.perf_counter()
    model = SentenceTransformer(str(model_path), local_files_only=True, trust_remote_code=False)
    model_load_seconds = time.perf_counter() - model_load_start
    print(json.dumps({"ready": True, "model_load_seconds": round(model_load_seconds, 6)}), flush=True)
    outcomes: dict[str, Any] = {"model_load_seconds": round(model_load_seconds, 6), "tiers": {}}
    if sys.platform.startswith("linux"):
        try:
            outcomes["worker_affinity_cpus"] = sorted(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            outcomes["worker_affinity_cpus"] = None
    for size in sizes:
        queries, passages, expected, languages = _activities(size)
        cpu_before = time.process_time()
        started = time.perf_counter()
        passage_vectors = model.encode(
            ["passage: " + text for text in passages], normalize_embeddings=True,
            show_progress_bar=False, batch_size=16,
        )
        passage_seconds = time.perf_counter() - started
        passage_cpu = time.process_time() - cpu_before

        # Warm each query before timed observations; each observation performs
        # query embedding and a local cosine/dot-product ranking over this corpus.
        model.encode(["query: " + queries[0]], normalize_embeddings=True, show_progress_bar=False)
        latencies: list[float] = []
        reciprocal_ranks: list[float] = []
        recall5: list[float] = []
        top1_correct = 0
        query_vectors: list[list[float]] = []
        query_cpu_start = time.process_time()
        for sample_index in range(max(size, 10)):
            query_index = sample_index % size
            query, expected_index = queries[query_index], expected[query_index]
            started = time.perf_counter()
            vector = model.encode(["query: " + query], normalize_embeddings=True, show_progress_bar=False)[0]
            query_vectors.append(np.asarray(vector, dtype=np.float32).tolist())
            scores = np.asarray(passage_vectors) @ np.asarray(vector)
            ranking = np.argsort(-scores)
            latencies.append(time.perf_counter() - started)
            rank = int(np.where(ranking == expected_index)[0][0]) + 1
            reciprocal_ranks.append(1 / rank)
            recall5.append(float(rank <= min(5, size)))
            top1_correct += int(rank == 1)
        outcomes["tiers"][str(size)] = {
            "activities": size,
            "queries": size,
            "passages": size,
            "embedding_dimensions": int(passage_vectors.shape[1]),
            "passage_embedding_seconds": round(passage_seconds, 6),
            "passage_embedding_cpu_seconds": round(passage_cpu, 6),
            "passage_throughput_activities_per_second": round(size / passage_seconds, 3) if passage_seconds else None,
            "query_retrieval_samples": len(latencies),
            "query_retrieval_cpu_seconds": round(time.process_time() - query_cpu_start, 6),
            "query_retrieval_p50_seconds": _percentile(latencies, 0.50),
            "query_retrieval_p95_seconds": _percentile(latencies, 0.95),
            "query_retrieval_p99_seconds": _percentile(latencies, 0.99),
            "retrieval_throughput_queries_per_second": round(len(latencies) / sum(latencies), 3) if sum(latencies) else None,
            "quality_queries": len(reciprocal_ranks),
            "top1_accuracy": round(top1_correct / len(latencies), 4),
            "recall_at_5": round(sum(recall5) / len(latencies), 4),
            "mean_reciprocal_rank": round(sum(reciprocal_ranks) / len(latencies), 4),
            "languages": {key: languages.count(key) for key in sorted(set(languages))},
        }
        if size == 5:
            reference = {
                "model_id": MODEL_ID,
                "model_snapshot": MODEL_SNAPSHOT,
                "weights_sha256": MODEL_SHA256,
                "vocabulary_sha256": "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8",
                "runtime": {"torch": torch.__version__, "sentence_transformers": __import__("sentence_transformers").__version__},
                "dimension": int(passage_vectors.shape[1]),
                "dtype": "float32",
                "normalized": True,
                "query_prefix": "query: ",
                "passage_prefix": "passage: ",
                "activities": [
                    {"activity_id": f"activity-{index + 1:03d}", "language": languages[index],
                     "query": queries[index], "passage": passages[index], "relevant_passage_index": expected[index]}
                    for index in range(size)
                ],
                "query_vectors": query_vectors[:size],
                "passage_vectors": np.asarray(passage_vectors, dtype=np.float32).tolist(),
            }
            canonical = json.dumps(reference, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            reference["vectors_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            reference_path = ROOT / "artifacts/inf01/torch-reference-5.json"
            reference_path.parent.mkdir(parents=True, exist_ok=True)
            reference_path.write_text(json.dumps(reference, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
            outcomes["reference_vectors_file"] = reference_path.name
            outcomes["reference_vectors_sha256"] = reference["vectors_sha256"]
        if checkpoint_path is not None:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(
                json.dumps({"model_load_seconds": outcomes["model_load_seconds"], "tiers": outcomes["tiers"],
                            "reference_vectors_file": outcomes.get("reference_vectors_file"),
                            "reference_vectors_sha256": outcomes.get("reference_vectors_sha256")}, allow_nan=False),
                encoding="utf-8",
            )
    del model
    if os.name != "nt":
        import resource
        peak_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        outcomes["process_peak_rss_bytes"] = peak_rss * (1024 if sys.platform.startswith("linux") else 1)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        outcomes["process_cpu_seconds"] = round(usage.ru_utime + usage.ru_stime, 6)
    return outcomes


def _run_baseline(model_path: Path, sizes: tuple[int, ...], checkpoint_path: Path) -> dict[str, Any]:
    status = _global_memory_status()
    available = status["available_physical_bytes"] if status else None
    available_commit = status["available_commit_bytes"] if status else None
    if available is not None and available < MIN_AVAILABLE_BYTES:
        raise RuntimeError(f"preflight stopped: available physical memory {available} bytes is below {MIN_AVAILABLE_BYTES}")
    if available_commit is not None and available_commit < MIN_AVAILABLE_COMMIT_BYTES:
        raise RuntimeError(f"preflight stopped: available commit {available_commit} bytes is below {MIN_AVAILABLE_COMMIT_BYTES}")
    if not (model_path / "model.safetensors").is_file():
        raise RuntimeError("cached model weights are missing; refusing downloads")
    actual_hash = _sha256(model_path / "model.safetensors")
    if actual_hash.lower() != MODEL_SHA256:
        raise RuntimeError(f"cached model checksum mismatch: {actual_hash}")

    inherited_runtime_vars = {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "VIRTUAL_ENV",
        "LD_LIBRARY_PATH", "LD_PRELOAD", "LANG", "LC_ALL",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in inherited_runtime_vars}
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
    env.pop("PYTHONPATH", None)
    worker_python = getattr(sys, "_base_executable", sys.executable)
    site_packages = Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"
    if site_packages.is_dir():
        env["PYTHONPATH"] = os.pathsep.join([str(site_packages), str(ROOT)])
    command = [worker_python, str(Path(__file__).resolve()), "--worker", str(model_path), ",".join(map(str, sizes)), "--worker-checkpoint", str(checkpoint_path)]
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    job_resources = None
    stdout_lines: list[str] = []
    stderr_tail: list[str] = []
    stdout_reader = None
    stderr_reader = None
    def drain_stdout() -> None:
        stdout_lines.extend(proc.stdout)
    def drain_stderr() -> None:
        for line in proc.stderr:
            stderr_tail.append(line)
            while sum(map(len, stderr_tail)) > 4096:
                stderr_tail.pop(0)
    try:
        header = proc.stdout.readline()
        if not header:
            raise RuntimeError("worker exited before memory-cap handshake")
        pid = int(json.loads(header)["pid"])
        if os.name == "nt":
            kernel, handles = _attach_bounded_job(pid)
            job_resources = (kernel, handles)
        proc.stdin.write('{"start":true}\n')
        proc.stdin.flush()
        proc.stdin.close()
        # Keep both pipes draining while sampling so a verbose runtime cannot
        # block the inference child on a full stdout/stderr pipe.
        stdout_reader = threading.Thread(target=drain_stdout, daemon=True)
        stderr_reader = threading.Thread(target=drain_stderr, daemon=True)
        stdout_reader.start()
        stderr_reader.start()

        peak_rss = 0
        peak_rss_procfs = 0
        metrics_module = None
        if os.name == "nt":
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "_pdf_rag_benchmark_metrics", ROOT / "scripts/benchmark_pdf_rag.py"
            )
            metrics_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(metrics_module)
        started = time.perf_counter()
        ready_at = None
        while proc.poll() is None:
            if ready_at is None and any('"ready": true' in line or '"ready":true' in line for line in stdout_lines):
                ready_at = time.perf_counter()
            elapsed = time.perf_counter() - started
            if elapsed > TOTAL_TIMEOUT_SECONDS:
                proc.terminate()
                raise TimeoutError("baseline exceeded the 900-second total hard budget")
            if ready_at is None and elapsed > STARTUP_TIMEOUT_SECONDS:
                proc.terminate()
                raise TimeoutError(f"baseline model startup exceeded the {STARTUP_TIMEOUT_SECONDS}-second hard budget")
            if ready_at is not None and time.perf_counter() - ready_at > RUN_TIMEOUT_SECONDS:
                proc.terminate()
                raise TimeoutError(f"baseline exceeded the {RUN_TIMEOUT_SECONDS}-second post-startup hard budget")
            available_now = _global_available_memory()
            if available_now is not None and available_now < STOP_AVAILABLE_BYTES:
                proc.terminate()
                raise RuntimeError(f"stopped: available physical memory fell below 0.75 GiB ({available_now} bytes)")
            if os.name == "nt":
                peak_rss = max(peak_rss, metrics_module._process_metrics(pid).get("peak_rss_bytes") or 0)
            else:
                _, proc_peak = _linux_process_memory(pid)
                peak_rss_procfs = max(peak_rss_procfs, proc_peak or 0)
            time.sleep(0.2)
        stdout_reader.join(timeout=5)
        stderr_reader.join(timeout=5)
        if proc.returncode:
            raise RuntimeError(f"worker failed ({proc.returncode}): {''.join(stderr_tail)[-1200:]}")
        if not stdout_lines:
            raise RuntimeError("worker exited without a result payload")
        cgroup = _cgroup_memory_status() if sys.platform.startswith("linux") else {}
        worker = json.loads(stdout_lines[-1])
        process_peak = worker.get("process_peak_rss_bytes") or 0
        return {
            **worker,
            "peak_rss_bytes": peak_rss or peak_rss_procfs or process_peak or None,
            "memory_cap_bytes": MEMORY_CAP_BYTES if os.name == "nt" else cgroup.get("limit_bytes"),
            "cgroup_memory_peak_bytes": cgroup.get("peak_bytes"),
        }
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        for reader in (stdout_reader, stderr_reader):
            if reader is not None:
                reader.join(timeout=2)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        _close_job(job_resources)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=2, metavar=("MODEL_PATH", "SIZES"), help=argparse.SUPPRESS)
    parser.add_argument("--worker-checkpoint", type=Path, help=argparse.SUPPRESS)
    default_receipt = ROOT / "artifacts/inf01" / ("baseline-linux.json" if sys.platform.startswith("linux") else "baseline.json")
    parser.add_argument("--receipt", type=Path, default=default_receipt)
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH, help="local cached model snapshot; downloads are never attempted")
    parser.add_argument("--sizes", default="5,25,100", help="synthetic activity counts (default: 5,25,100)")
    args = parser.parse_args()
    if args.worker:
        result = _worker(Path(args.worker[0]), tuple(int(x) for x in args.worker[1].split(",")), args.worker_checkpoint)
        print(json.dumps(result, allow_nan=False))
        return

    sizes = tuple(int(value) for value in args.sizes.split(","))
    if not sizes or any(value <= 0 or value > 100 for value in sizes):
        raise SystemExit("sizes must be positive synthetic activity counts up to 100")
    available = _global_available_memory()
    receipt: dict[str, Any] = {
        "benchmark": "INF01 synthetic embedding and retrieval diagnostic",
        "recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host_platform": sys.platform,
        "python": sys.version.split()[0],
        "network_access": False,
        "database_access": False,
        "customer_data": False,
        "workload_unit": "synthetic activities; not pages or tenants",
        "sizes": list(sizes),
        "attempted_sizes": list(sizes),
        "preflight_available_physical_memory_bytes": available,
        "preflight_available_commit_bytes": (_global_memory_status() or {}).get("available_commit_bytes"),
        "cgroup_memory_preflight": _cgroup_memory_status() if sys.platform.startswith("linux") else None,
        "min_preflight_memory_bytes": MIN_AVAILABLE_BYTES if os.name == "nt" else None,
        "min_preflight_commit_bytes": MIN_AVAILABLE_COMMIT_BYTES if os.name == "nt" else None,
        "stop_memory_bytes": STOP_AVAILABLE_BYTES if os.name == "nt" else None,
        "worker_memory_cap_bytes": MEMORY_CAP_BYTES if os.name == "nt" else (_cgroup_memory_status().get("limit_bytes") if sys.platform.startswith("linux") else None),
        "worker_cpu_affinity": "first two CPUs from the process allowed affinity set" if sys.platform.startswith("linux") else "first two logical CPUs where Windows permits",
        "model": {
            "id": MODEL_ID,
            "snapshot": MODEL_SNAPSHOT,
            "path": str(args.model_path),
            "weights_file_bytes": (args.model_path / "model.safetensors").stat().st_size if (args.model_path / "model.safetensors").is_file() else None,
            "weights_sha256": _sha256(args.model_path / "model.safetensors") if (args.model_path / "model.safetensors").is_file() else None,
            "tokenizer_file_bytes": (args.model_path / "tokenizer.json").stat().st_size if (args.model_path / "tokenizer.json").is_file() else None,
            "vocabulary_sha256": _sha256(args.model_path / "tokenizer.json") if (args.model_path / "tokenizer.json").is_file() else None,
            "passage_prefix": "passage: ",
            "query_prefix": "query: ",
            "normalization": "L2",
        },
        "runtime_versions": {
            package: _distribution_version(distribution)
            for package, distribution in {
                "torch": "torch", "sentence_transformers": "sentence-transformers",
                "transformers": "transformers", "onnx": "onnx",
                "onnxruntime": "onnxruntime", "optimum": "optimum",
            }.items()
        },
        "limitations": [
            "Synthetic local embedding and in-memory retrieval only; no PDF, database, API, or WhatsApp work.",
            "One process only; multiprocess and ONNX/INT8 cases are not executed by this harness.",
            "Quality metrics are diagnostic for a small frozen synthetic multilingual fixture, not a production retrieval evaluation.",
        ],
    }
    existing = None
    if args.receipt.is_file():
        try:
            existing = json.loads(args.receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
    if existing and existing.get("model") == receipt.get("model"):
        receipt = existing
    receipt["sizes"] = list(sizes)
    receipt["attempted_sizes"] = list(sizes)
    receipt["recorded_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    receipt["preflight_available_physical_memory_bytes"] = available
    receipt["preflight_available_commit_bytes"] = (_global_memory_status() or {}).get("available_commit_bytes")
    receipt["cgroup_memory_preflight"] = _cgroup_memory_status() if sys.platform.startswith("linux") else None
    receipt["min_preflight_memory_bytes"] = MIN_AVAILABLE_BYTES if os.name == "nt" else None
    receipt["min_preflight_commit_bytes"] = MIN_AVAILABLE_COMMIT_BYTES if os.name == "nt" else None
    receipt["stop_memory_bytes"] = STOP_AVAILABLE_BYTES if os.name == "nt" else None
    receipt["worker_memory_cap_bytes"] = MEMORY_CAP_BYTES if os.name == "nt" else (_cgroup_memory_status().get("limit_bytes") if sys.platform.startswith("linux") else None)
    available_commit = receipt["preflight_available_commit_bytes"]
    if (available is not None and available < MIN_AVAILABLE_BYTES) or (available_commit is not None and available_commit < MIN_AVAILABLE_COMMIT_BYTES):
        receipt["baseline"] = {"status": "skipped", "attempted_sizes": [], "reason": "preflight physical memory below 3.0 GiB or available commit below 4.0 GiB"}
    else:
        prior_baseline = receipt.get("baseline", {})
        prior_tiers = prior_baseline.get("tiers", {}) if prior_baseline.get("status") == "complete" else {}
        receipt["baseline"] = {"status": "running", "attempted_sizes": list(sizes), "completed_tiers": sorted(prior_tiers)}
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        checkpoint_path = args.receipt.with_name(args.receipt.stem + "-progress.json")
        try:
            checkpoint_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            run = _run_baseline(args.model_path, sizes, checkpoint_path)
            checkpoint_path.unlink(missing_ok=True)
            merged_tiers = {**prior_tiers, **run.pop("tiers", {})}
            receipt["baseline"] = {"status": "complete", **run, "tiers": merged_tiers}
        except Exception as exc:
            partial = {}
            if checkpoint_path.is_file():
                try:
                    partial = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    partial = {}
            partial_tiers = partial.get("tiers", {})
            detail = str(exc).lower()
            if "1455" in detail or "paging file" in detail or "file di paging" in detail:
                failure = "Windows paging file was too small during model load (OS error 1455); no workload tiers completed."
            elif "3221226505" in detail or "memory allocation" in detail:
                failure = "Windows worker hit a memory allocation failure while loading local model assets (STATUS_NO_MEMORY 0xC0000017); no workload tiers completed."
            elif sys.platform.startswith("linux") and "worker failed" in detail:
                failure = "Linux worker failed inside the container; completed tier checkpoints are retained in this receipt."
            elif isinstance(exc, TimeoutError):
                failure = str(exc)
            else:
                failure = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
            receipt["baseline"] = {
                "status": "failed",
                "attempted_sizes": list(sizes),
                "completed_tiers": sorted(set(prior_tiers) | set(partial_tiers)),
                "tiers": {**prior_tiers, **partial_tiers},
                "model_load_seconds": partial.get("model_load_seconds"),
                "reference_vectors_file": partial.get("reference_vectors_file"),
                "reference_vectors_sha256": partial.get("reference_vectors_sha256"),
                "failure": failure,
            }
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
            raise
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote INF01 receipt: {args.receipt}")


if __name__ == "__main__":
    main()
