"""Synthetic document-format, extraction-boundary, and local-worker regressions."""

import asyncio
import io
import os
import zipfile

import pytest
from pypdf import PdfWriter

from src.core.documenti import isolated, limits, safe_extraction
from src.core.documenti.safe_extraction import ExtractionRejected, extract_document


def _pdf(pages=1, *, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("synthetic-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _docx(xml: bytes):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return output.getvalue()


@pytest.mark.parametrize(
    ("content", "name", "error"),
    [
        (b"not a pdf", "broken.pdf", "invalid_pdf"),
        (b"%PDF-1.7 broken %%EOF", "broken.pdf", "invalid_document"),
        (_pdf(encrypted=True), "protected.pdf", "protected_pdf"),
        (_pdf(), "blank.pdf", "no_text"),
        (_pdf(limits.PDF_PAGES + 1), "too-many.pdf", "page_limit"),
    ],
)
def test_pdf_magic_protection_blank_and_page_bounds(content, name, error):
    with pytest.raises(ExtractionRejected, match=error):
        extract_document(content, name, "application/pdf")


def test_mixed_text_and_scanned_pdf_fails_closed_when_ocr_is_missing(monkeypatch):
    fitz = pytest.importorskip("fitz")
    from PIL import Image

    image_buffer = io.BytesIO()
    Image.new("RGB", (24, 24), "white").save(image_buffer, format="PNG")
    doc = fitz.open()
    text_page = doc.new_page()
    text_page.insert_text((20, 30), "Synthetic text page")
    scan_page = doc.new_page()
    scan_page.insert_image(fitz.Rect(0, 0, 24, 24), stream=image_buffer.getvalue())
    content = doc.tobytes()
    doc.close()

    def missing_ocr(_image):
        raise ExtractionRejected("ocr_unavailable")

    monkeypatch.setattr(safe_extraction, "_ocr", missing_ocr)
    with pytest.raises(ExtractionRejected, match="ocr_unavailable"):
        extract_document(content, "mixed-scan.pdf", "application/pdf")


def test_selectable_footer_does_not_hide_scanned_content_on_same_page(monkeypatch):
    import fitz
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (24, 24), "white").save(output, format="PNG")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((20, 30), "Selectable footer")
    page.insert_image(fitz.Rect(0, 40, 24, 64), stream=output.getvalue())
    content = doc.tobytes()
    doc.close()
    seen = []
    monkeypatch.setattr(safe_extraction, "_ocr", lambda image: seen.append(image.size) or "Scanned menu text")
    result = extract_document(content, "same-page.pdf")
    assert seen and result["ocr_pages"] == 1
    assert "Scanned menu text" in result["text"] and "Selectable footer" in result["text"]


def test_valid_text_pdf_through_real_limited_worker():
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((20, 30), "Synthetic bounded PDF")
    content = doc.tobytes()
    doc.close()
    result = asyncio.run(isolated.extract_local(content, "valid.pdf", "application/pdf"))
    assert "Synthetic bounded PDF" in result["text"]


def test_text_rejects_binary_control_bytes_and_nul():
    for content in (b"hello\x00world", b"hello\x01world"):
        with pytest.raises(ExtractionRejected, match="invalid_text"):
            extract_document(content, "binary.txt", "text/plain")


def test_docx_decompressed_xml_cap_is_enforced_before_parse():
    oversized_xml = b"<document>" + b"x" * (limits.DECOMPRESSED_BYTES + 1) + b"</document>"
    with pytest.raises(ExtractionRejected, match="decompressed_limit"):
        extract_document(_docx(oversized_xml), "oversized.docx")


def test_docx_extracts_text_from_synthetic_xml():
    xml = (
        b'<document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:p><w:r><w:t>synthetic DOCX text</w:t></w:r></w:p></document>"
    )
    result = extract_document(_docx(xml), "small.docx")
    assert result["text"] == "synthetic DOCX text"


def test_chunking_splits_long_unbroken_token_into_bounded_nonempty_chunks():
    from src.core.documenti.chunking import chunk_testo

    chunks = chunk_testo("x" * 1307, size=500, overlap=50)
    assert chunks
    assert all(chunk and len(chunk) <= 500 for chunk in chunks)


@pytest.mark.asyncio
async def test_real_owned_worker_extracts_synthetic_text_without_external_services():
    result = await isolated.extract_local(b"owned child extraction", "tiny.txt", "text/plain")
    assert result["text"] == "owned child extraction"
    assert result["pages"] == 1


@pytest.mark.asyncio
async def test_worker_startup_timeout_cleans_up_its_owned_child(monkeypatch):
    workers = []
    base_worker = isolated.LocalWorker

    class TrackingWorker(base_worker):
        def __init__(self, mode):
            super().__init__(mode)
            workers.append(self)

    monkeypatch.setattr(isolated, "LocalWorker", TrackingWorker)
    monkeypatch.setattr(limits, "EXTRACTION_SECONDS", 0.0001)

    with pytest.raises(asyncio.TimeoutError):
        await isolated.extract_local(b"timeout probe", "tiny.txt", "text/plain")

    assert len(workers) == 1
    assert workers[0].process is None
    assert workers[0].job is None
