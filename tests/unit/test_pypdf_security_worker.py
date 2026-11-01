"""Bounded regression probes for untrusted PDFs in the real extraction worker."""

import asyncio
import io

import pytest
from pypdf import PdfWriter

from src.core.documenti import isolated, limits


def _incomplete_xref_pdf():
    # Like the upstream regression, put the incomplete xref at actual EOF.
    # Fixed-width offset makes its own length independent of the offset value.
    prefix = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nstartxref\n"
    offset = len(prefix) + len(b"000\n%%EOF\n")
    return prefix + f"{offset:03d}".encode("ascii") + b"\n%%EOF\nxref\n1 0\naaa"


def _encrypted_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("synthetic-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _text_pdf():
    import fitz

    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((20, 30), "Synthetic bounded PDF")
        return doc.tobytes()
    finally:
        doc.close()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (_incomplete_xref_pdf(), "invalid_document"),
        (_encrypted_pdf(), "protected_pdf"),
    ],
    ids=["incomplete-xref-without-trailer", "encrypted"],
)
@pytest.mark.asyncio
async def test_rejected_pdf_then_healthy_pdf_uses_bounded_owned_workers(monkeypatch, content, expected):
    healthy = _text_pdf()
    assert len(content) < 16 * 1024 and len(healthy) < 16 * 1024
    budget = 256 * 1024 * 1024
    monkeypatch.setattr(limits, "EXTRACTION_MEMORY_BYTES", budget)

    # The subprocess has its own Python state. Set the same lower test budget
    # before running the unchanged worker module, including on POSIX where the
    # worker itself applies its memory limit. Windows uses the parent value too.
    original_spawn = asyncio.create_subprocess_exec
    launcher = (
        "import runpy; from src.core.documenti import limits; "
        f"limits.EXTRACTION_MEMORY_BYTES = {budget}; "
        "runpy.run_module('src.core.documenti.local_worker', run_name='__main__', alter_sys=True)"
    )

    async def spawn_with_budget(executable, *args, **kwargs):
        assert args[:2] == ("-m", "src.core.documenti.local_worker")
        return await original_spawn(executable, "-c", launcher, *args[2:], **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_with_budget)
    workers = []
    base_worker = isolated.LocalWorker

    class TrackingWorker(base_worker):
        def __init__(self, mode):
            super().__init__(mode)
            self.owned_process = None
            workers.append(self)

        async def close(self):
            if self.process is not None:
                self.owned_process = self.process
            await super().close()

    monkeypatch.setattr(isolated, "LocalWorker", TrackingWorker)
    try:
        # A timeout is a test failure, not an acceptable malformed-file result.
        async with asyncio.timeout(8):
            with pytest.raises(isolated.LocalProcessingUnavailable, match=f"^{expected}$"):
                await isolated.extract_local(content, "probe.pdf", "application/pdf")
            result = await isolated.extract_local(healthy, "healthy.pdf", "application/pdf")
            assert result == {
                "text": "Synthetic bounded PDF",
                "page_texts": ["Synthetic bounded PDF"],
                "pages": 1,
                "ocr_pages": 0,
            }
    finally:
        assert workers
        for worker in workers:
            assert worker.process is None
            assert worker.job is None
            if worker.owned_process is not None:
                assert worker.owned_process.returncode is not None
