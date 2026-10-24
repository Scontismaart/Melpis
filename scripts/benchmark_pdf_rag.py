"""Offline synthetic PDF/RAG CPU benchmark. Never accesses a database or network."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
BASELINE_COMMIT = "a7f09f2d4155dade5f93a6512fb2f5bdd4a9a5ea"
EXTRACTOR_BLOB = "285ef29ed3b6ec755a0ec7c72929bcd8bb9a9818"
CHUNKER_BLOB = "f2ac0ad2ef4d835be53bcc01b831dd0e59dd714e"
SIZES = (5, 50, 200)


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_synthetic_pdf(path: Path, pages: int) -> None:
    """Write a self-contained PDF with deterministic, searchable text pages."""
    objects: list[bytes] = []
    page_ids = [4 + 2 * i for i in range(pages)]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.extend([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ])
    for page in range(pages):
        page_id = 4 + 2 * page
        stream_id = page_id + 1
        lines = [
            f"Synthetic benchmark page {page + 1} of {pages}.",
            "This fictional cafe serves espresso, tea, pastries, and lunch.",
            "Opening hours are Monday through Saturday from 08:00 to 18:00.",
            "A table reservation can be requested by calling the reception desk.",
            "Prices and descriptions below are generated test data, not real offers.",
        ]
        # A stable page-dependent paragraph gives extraction and chunking realistic volume.
        lines.extend(
            f"Section {i + 1}: sample menu item {i % 17 + 1} costs {i % 23 + 2}.00 euros; "
            "ingredients and availability are fictional benchmark content."
            for i in range(24)
        )
        commands = ["BT", "/F1 9 Tf", "45 790 Td", "12 TL"]
        commands += [f"({_pdf_escape(line)}) Tj T*" for line in lines]
        commands.append("ET")
        data = ("\n".join(commands) + "\n").encode("ascii")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"endstream")

    pdf = bytearray(b"%PDF-1.4\n% synthetic benchmark fixture\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(pdf)


def write_synthetic_scanned_pdf(path: Path) -> None:
    """Create a one-page image-only PDF for a bounded local OCR-path sample."""
    import fitz
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1600, 2000), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
    font = ImageFont.truetype(str(font_path), 58) if font_path.is_file() else ImageFont.load_default()
    for index, line in enumerate((
        "SYNTHETIC CAFE MENU", "Espresso and tea", "Opening hours: 08:00 to 18:00",
        "Call the reception desk to reserve a table.",
    )):
        draw.text((150, 350 + index * 180), line, fill="black", font=font)
    buffer = __import__("io").BytesIO()
    image.save(buffer, format="PNG")
    pdf = fitz.open()
    page = pdf.new_page(width=612, height=765)
    page.insert_image(page.rect, stream=buffer.getvalue())
    pdf.save(path)
    pdf.close()


def worker(pdf_path: Path, model_path: Path) -> dict:
    sys.path.insert(0, str(ROOT))
    from src.core.documenti.chunking import chunk_testo
    from src.core.documenti.extractor import estrai_testo

    pdf_bytes = pdf_path.read_bytes()
    cpu_start = time.process_time()
    started = time.perf_counter()
    extracted = estrai_testo(pdf_bytes, pdf_path.name, "application/pdf")
    extract_wall = time.perf_counter() - started
    extract_cpu = time.process_time() - cpu_start

    cpu_start = time.process_time()
    started = time.perf_counter()
    chunks = chunk_testo(extracted)
    chunk_wall = time.perf_counter() - started
    chunk_cpu = time.process_time() - cpu_start

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from sentence_transformers import SentenceTransformer
    import torch

    # Keep the workload predictable on Windows desktops with high logical-core counts.
    torch.set_num_threads(2)

    cpu_start = time.process_time()
    started = time.perf_counter()
    model = SentenceTransformer(str(model_path), local_files_only=True)
    model_load_wall = time.perf_counter() - started
    model_load_cpu = time.process_time() - cpu_start

    cpu_start = time.process_time()
    started = time.perf_counter()
    vectors = model.encode(
        [f"passage: {text}" for text in chunks],
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=32,
    )
    embedding_wall = time.perf_counter() - started
    embedding_cpu = time.process_time() - cpu_start
    query = "What time does the cafe open, and can I reserve a table?"
    warm_query_samples = []
    for _ in range(10):
        started = time.perf_counter()
        model.encode(
            [f"query: {query}"], normalize_embeddings=True,
            show_progress_bar=False, batch_size=1,
        )
        warm_query_samples.append(time.perf_counter() - started)
    ordered_queries = sorted(warm_query_samples)
    return {
        "pdf_bytes": len(pdf_bytes),
        "pages": len(__import__("pypdf").PdfReader(pdf_path).pages),
        "extracted_characters": len(extracted),
        "chunks": len(chunks),
        "embedding_dimensions": int(vectors.shape[1]) if len(vectors) else 0,
        "extraction_seconds": round(extract_wall, 6),
        "extraction_cpu_seconds": round(extract_cpu, 6),
        "chunking_seconds": round(chunk_wall, 6),
        "chunking_cpu_seconds": round(chunk_cpu, 6),
        "model_load_seconds": round(model_load_wall, 6),
        "model_load_cpu_seconds": round(model_load_cpu, 6),
        "embedding_seconds": round(embedding_wall, 6),
        "embedding_cpu_seconds": round(embedding_cpu, 6),
        "embedding_chunks_per_second": round(len(chunks) / embedding_wall, 3) if embedding_wall else None,
        "warm_query_runs": len(warm_query_samples),
        "warm_query_p50_seconds": round(ordered_queries[4], 6),
        "warm_query_p95_seconds": round(ordered_queries[9], 6),
    }


def ocr_worker(pdf_path: Path) -> dict:
    import socket
    from src.core.documenti.extractor import estrai_testo

    # This sample is strictly local: even accidental socket use fails closed.
    def deny_connect(*_args, **_kwargs):
        raise RuntimeError("Network disabled for local OCR benchmark")
    socket.create_connection = deny_connect
    socket.socket.connect = deny_connect
    pdf_bytes = pdf_path.read_bytes()
    cpu_start = time.process_time()
    started = time.perf_counter()
    text = estrai_testo(pdf_bytes, pdf_path.name, "application/pdf")
    return {
        "pdf_bytes": len(pdf_bytes),
        "pages": 1,
        "extracted_characters": len(text),
        "recognized_text": text,
        "extraction_seconds": round(time.perf_counter() - started, 6),
        "extraction_cpu_seconds": round(time.process_time() - cpu_start, 6),
    }


def _process_metrics(pid: int) -> dict:
    if os.name != "nt":
        return {"peak_rss_bytes": None, "cpu_user_seconds": None, "cpu_kernel_seconds": None}
    import ctypes
    from ctypes import wintypes

    class Times(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return {"peak_rss_bytes": None, "cpu_user_seconds": None, "cpu_kernel_seconds": None}
    try:
        times = [Times() for _ in range(4)]
        memory = MemoryCounters()
        memory.cb = ctypes.sizeof(memory)
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return {"peak_rss_bytes": None, "cpu_user_seconds": None, "cpu_kernel_seconds": None}
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
            rss = None
        else:
            rss = int(memory.PeakWorkingSetSize)
        def seconds(t):
            return (((int(t.high) << 32) | int(t.low)) / 10_000_000)
        return {"peak_rss_bytes": rss, "cpu_user_seconds": seconds(times[3]), "cpu_kernel_seconds": seconds(times[2])}
    finally:
        kernel.CloseHandle(handle)


def _descendant_pids(parent_pid: int) -> list[int]:
    """Return descendants of the launcher (the Windows venv shim starts Python)."""
    if os.name != "nt":
        return [parent_pid]
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return [parent_pid]
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        parents: dict[int, int] = {}
        if kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        result = []
        for pid in parents:
            cursor = pid
            seen: set[int] = set()
            while cursor in parents and cursor != parent_pid and cursor not in seen:
                seen.add(cursor)
                cursor = parents[cursor]
            if cursor == parent_pid:
                result.append(pid)
        return result or [parent_pid]
    finally:
        kernel.CloseHandle(snapshot)


def _available_memory_bytes() -> int | None:
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
    if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys)


def run_one(pdf_path: Path, model_path: Path) -> dict:
    env = os.environ.copy()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
    # Windows venv python.exe is a launcher that keeps a tiny parent process alive
    # while the base interpreter does the work. Invoke that base interpreter
    # directly and add the reviewed venv's packages explicitly for reliable RSS.
    venv_site = Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"
    env["PYTHONPATH"] = os.pathsep.join([str(venv_site), str(ROOT), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    worker_python = getattr(sys, "_base_executable", sys.executable)
    command = [worker_python, str(Path(__file__).resolve()), "--worker", str(pdf_path), str(model_path)]
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    peak = 0
    # Sample the Windows working set while the child runs; no extra dependencies needed.
    metrics = {"cpu_user_seconds": None, "cpu_kernel_seconds": None}
    while proc.poll() is None:
        rows = [_process_metrics(pid) for pid in _descendant_pids(proc.pid)]
        peak = max(peak, max((m.get("peak_rss_bytes") or 0 for m in rows), default=0))
        metrics = {
            "cpu_user_seconds": sum((m.get("cpu_user_seconds") or 0 for m in rows), 0) or None,
            "cpu_kernel_seconds": sum((m.get("cpu_kernel_seconds") or 0 for m in rows), 0) or None,
        }
        time.sleep(0.1)
    stdout, stderr = proc.communicate()
    if proc.returncode:
        raise RuntimeError(f"worker failed ({proc.returncode}): {stderr[-2000:]}")
    result = json.loads(stdout)
    result["peak_rss_bytes"] = peak or None
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=2, metavar=("PDF", "MODEL"))
    parser.add_argument("--ocr-worker", metavar="PDF")
    parser.add_argument("--ocr-benchmark", action="store_true")
    parser.add_argument("--receipt", type=Path, default=ROOT / "docs/operations/pdf-rag-synthetic-benchmark.json")
    args = parser.parse_args()
    if args.worker:
        result = worker(Path(args.worker[0]), Path(args.worker[1]))
        print(json.dumps(result))
        return
    if args.ocr_worker:
        sys.path.insert(0, str(ROOT))
        print(json.dumps(ocr_worker(Path(args.ocr_worker))))
        return
    if args.ocr_benchmark:
        receipt = json.loads(args.receipt.read_text(encoding="utf-8")) if args.receipt.is_file() else {}
        with tempfile.TemporaryDirectory(prefix="melpis-pdf-ocr-") as temp:
            fixture = Path(temp) / "synthetic-scanned-1-page.pdf"
            write_synthetic_scanned_pdf(fixture)
            env = os.environ.copy()
            env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
            venv_site = Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"
            env["PYTHONPATH"] = os.pathsep.join([str(venv_site), str(ROOT), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
            worker_python = getattr(sys, "_base_executable", sys.executable)
            proc = subprocess.Popen(
                [worker_python, str(Path(__file__).resolve()), "--ocr-worker", str(fixture)],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            peak = 0
            started = time.perf_counter()
            try:
                while proc.poll() is None:
                    if time.perf_counter() - started > 60:
                        proc.kill()
                        raise TimeoutError("Local OCR benchmark exceeded 60 seconds.")
                    rows = [_process_metrics(pid) for pid in _descendant_pids(proc.pid)]
                    peak = max(peak, max((row.get("peak_rss_bytes") or 0 for row in rows), default=0))
                    time.sleep(0.1)
                stdout, stderr = proc.communicate(timeout=1)
                if proc.returncode:
                    raise RuntimeError(stderr[-2000:])
                receipt["ocr_scanned_1_page"] = json.loads(stdout)
                receipt["ocr_scanned_1_page"]["peak_rss_bytes"] = peak or None
            except (TimeoutError, subprocess.TimeoutExpired) as exc:
                if proc.poll() is None:
                    proc.kill()
                receipt["ocr_scanned_1_page"] = {"error": str(exc)}
            except Exception as exc:
                receipt["ocr_scanned_1_page"] = {"error": str(exc)}
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return

    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub" / "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2" / "snapshots"
    snapshots = sorted(p for p in cache.iterdir() if p.is_dir()) if cache.is_dir() else []
    if not snapshots:
        raise SystemExit("Cached MiniLM snapshot not found; refusing downloads. See benchmark documentation.")
    model_path = snapshots[-1]
    receipt = {
        "benchmark": "synthetic-pdf-rag-local-cpu",
        "network_access": False,
        "database_access": False,
        "model_id": MODEL_ID,
        "baseline_source_commit": BASELINE_COMMIT,
        "baseline_extractor_git_blob": EXTRACTOR_BLOB,
        "baseline_chunker_git_blob": CHUNKER_BLOB,
        "model_snapshot_directory": model_path.name,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "tiers": {},
        "concurrency": {},
        "limitations": ["No database/index/storage/search benchmark is performed."],
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    def checkpoint() -> None:
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    with tempfile.TemporaryDirectory(prefix="melpis-pdf-benchmark-") as temp:
        directory = Path(temp)
        for pages in SIZES:
            fixture = directory / f"synthetic-{pages}-pages.pdf"
            write_synthetic_pdf(fixture, pages)
            receipt["tiers"][str(pages)] = run_one(fixture, model_path)
            checkpoint()
        fixture = directory / "synthetic-5-pages-concurrency.pdf"
        write_synthetic_pdf(fixture, 5)
        for concurrency in (1, 2):
            observed_peak = receipt["tiers"].get("5", {}).get("peak_rss_bytes") or 0
            available = _available_memory_bytes()
            needed = 2 * observed_peak + 1024**3
            if concurrency == 2 and available is not None and available < needed:
                receipt["concurrency"]["2"] = {
                    "workers": 2,
                    "skipped": True,
                    "reason": "Insufficient free physical memory for two observed peak-RSS workers plus 1 GiB headroom.",
                    "observed_worker_peak_rss_bytes": observed_peak,
                    "available_physical_memory_bytes": available,
                    "required_physical_memory_bytes": needed,
                }
                checkpoint()
                continue
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                rows = list(pool.map(lambda _: run_one(fixture, model_path), range(concurrency)))
            receipt["concurrency"][str(concurrency)] = {
                "workers": concurrency,
                "wall_seconds": round(time.perf_counter() - started, 6),
                "runs": rows,
            }
            checkpoint()
    print(f"Wrote offline synthetic benchmark receipt: {args.receipt.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
