"""Async process boundary with terminable work and bounded queue admission."""
import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import weakref

from src.core.documenti import limits


class LocalProcessingUnavailable(RuntimeError):
    pass


class LocalWorker:
    def __init__(self, mode):
        self.mode = mode
        self.process = None
        self.job = None
        self.lock = asyncio.Lock()
        self.starting = None

    async def _read(self):
        line = await self.process.stdout.readline()
        if not line or len(line) > 64 * 1024 * 1024:
            raise LocalProcessingUnavailable("local_worker_unavailable")
        value = json.loads(line)
        if "error" in value:
            allowed = {"ocr_unavailable", "ocr_required", "ocr_pixel_limit", "protected_pdf", "invalid_pdf",
                       "invalid_document", "format_mismatch", "no_text", "page_limit", "text_limit",
                       "decompressed_limit", "unsupported_format", "invalid_text", "upload_limit", "invalid_docx"}
            code = value["error"] if value["error"] in allowed else "local_processing_failed"
            raise LocalProcessingUnavailable(code)
        return value

    async def _write(self, value):
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.process.stdin.drain()

    async def _start(self):
        try:
            env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                   "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "src.core.documenti.local_worker", self.mode,
                cwd=str(Path(__file__).resolve().parents[3]), env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=64 * 1024 * 1024,
            )
            async with asyncio.timeout(limits.MODEL_START_SECONDS):
                hello = await self._read()
                if os.name == "nt":
                    from src.core.documenti.process_limits import attach_windows_job
                    budget = limits.EXTRACTION_MEMORY_BYTES if self.mode == "extract" else limits.EMBEDDING_MEMORY_BYTES
                    self.job = attach_windows_job(hello["pid"], budget)
                    self.job.__enter__()
                await self._write({"start": True})
                if await self._read() != {"ready": True}:
                    raise LocalProcessingUnavailable("local_worker_unavailable")
        except BaseException:
            await self.close()
            raise

    async def ensure_started(self):
        if self.starting is None:
            if self.process is not None and self.process.returncode is None:
                return
            self.starting = asyncio.create_task(self._start())
            # First retrieval can time out while the bounded model startup
            # continues. It must not restart a cold model on every question.
            self.starting.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        startup = self.starting
        try:
            # A previous caller can time out while startup succeeds. Consume
            # that same handshake, including a completed task; never spawn a
            # replacement over its live process and orphan a loaded model.
            await asyncio.shield(startup)
        finally:
            if startup.done() and self.starting is startup:
                self.starting = None

    async def call(self, payload, timeout):
        try:
            async with asyncio.timeout(timeout):
                await self.ensure_started()
                async with self.lock:
                    try:
                        await self._write(payload)
                        return (await self._read())["result"]
                    except BaseException:
                        await self.close()
                        raise
        except (Exception, asyncio.CancelledError):
            raise

    async def close(self):
        starting = self.starting
        if starting is not None and starting is not asyncio.current_task() and not starting.done():
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)
        if self.job is not None:
            self.job.__exit__(None, None, None)
            self.job = None
        process, self.process = self.process, None
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()


_workers = weakref.WeakKeyDictionary()


async def embed_local(texts, tipo="passage", *, timeout=limits.QUERY_SECONDS, organization_id=None):
    from src.core.documenti.shared_client import _configuration, embed_shared
    if _configuration() == "shared":
        if organization_id is None:
            raise LocalProcessingUnavailable("embedding_organization_required")
        return await embed_shared(texts, tipo, organization_id, timeout)
    loop = asyncio.get_running_loop()
    worker = _workers.get(loop)
    if worker is None:
        worker = _workers[loop] = LocalWorker("embed")
    return await worker.call({"texts": texts, "tipo": tipo}, timeout)


async def extract_local(content, name, content_type=""):
    worker = LocalWorker("extract")
    try:
        return await worker.call({"content": base64.b64encode(content).decode(), "name": name,
                                  "content_type": content_type}, limits.EXTRACTION_SECONDS)
    finally:
        await worker.close()


async def shutdown_local_workers():
    worker = _workers.pop(asyncio.get_running_loop(), None)
    if worker is not None:
        if worker.starting and not worker.starting.done():
            worker.starting.cancel()
            await asyncio.gather(worker.starting, return_exceptions=True)
        await worker.close()
