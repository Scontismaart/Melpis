"""Bounded format-aware extraction, executed only in the isolated child."""
import io
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

from src.core.documenti import limits


class ExtractionRejected(ValueError):
    pass


def _reject(code):
    raise ExtractionRejected(code)


def _bounded_text(text):
    if len(text) > limits.TEXT_CHARACTERS:
        _reject("text_limit")
    return text.strip()


def _ocr(image):
    import importlib.util
    spec = importlib.util.find_spec("rapidocr_onnxruntime")
    if spec is None or spec.origin is None:
        _reject("ocr_unavailable")
    models = Path(spec.origin).parent / "models"
    if not models.is_dir() or len(list(models.glob("*.onnx"))) < 3:
        _reject("ocr_unavailable")
    if image.width * image.height > limits.OCR_PIXELS:
        _reject("ocr_pixel_limit")
    from src.core.documenti.extractor import _ocr_immagine
    return _bounded_text(_ocr_immagine(image))


def extract_document(content, name, content_type=""):
    if not content or len(content) > limits.UPLOAD_BYTES:
        _reject("upload_limit")
    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".pdf":
            if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
                _reject("invalid_pdf")
            return _pdf(content)
        if content.startswith(b"%PDF-"):
            _reject("format_mismatch")
        if suffix in {".txt", ".md", ".csv", ".json"}:
            text = content.decode("utf-8-sig", errors="strict")
            if any(ord(c) < 32 and c not in "\t\n\r" for c in text):
                _reject("invalid_text")
        elif suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entry = archive.getinfo("word/document.xml")
                if entry.file_size > limits.DECOMPRESSED_BYTES:
                    _reject("decompressed_limit")
                with archive.open(entry) as stream:
                    xml = stream.read(limits.DECOMPRESSED_BYTES + 1)
                if len(xml) > limits.DECOMPRESSED_BYTES or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                    _reject("invalid_docx")
                root = ET.fromstring(xml)
                text = "\n".join(element.text or "" for element in root.iter() if element.tag.endswith("}t"))
        elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            from PIL import Image
            image = Image.open(io.BytesIO(content))
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                _reject("format_mismatch")
            if image.width * image.height > limits.OCR_PIXELS:
                _reject("ocr_pixel_limit")
            text = _ocr(image.convert("RGB"))
            return _result([text], 1)
        else:
            _reject("unsupported_format")
        return _result([text], 0)
    except ExtractionRejected:
        raise
    except Exception:
        _reject("invalid_document")


def _result(pages, ocr_pages):
    texts = [_bounded_text(page) for page in pages]
    if sum(map(len, texts)) > limits.TEXT_CHARACTERS:
        _reject("text_limit")
    if not any(texts):
        _reject("no_text")
    return {"text": "\n\n".join(texts), "page_texts": texts,
            "pages": len(texts), "ocr_pages": ocr_pages}


def _pdf(content):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        _reject("protected_pdf")
    count = len(reader.pages)
    if not 1 <= count <= limits.PDF_PAGES:
        _reject("page_limit")
    pages, ocr_pages, rendered = [], 0, None
    try:
        for number, page in enumerate(reader.pages):
            stream = page.get_contents()
            if stream and len(stream.get_data()) > limits.DECOMPRESSED_BYTES:
                _reject("decompressed_limit")
            text = (page.extract_text() or "").strip()
            resources = page.get('/Resources')
            has_xobjects = resources is not None and bool(resources.get_object().get('/XObject'))
            # A PDF page may contain a selectable footer plus a scanned menu.
            # Any raster evidence must be read as well; a small text layer must
            # never cause the rest of the page to be silently discarded.
            if not text or has_xobjects:
                try:
                    import fitz
                except ImportError:
                    _reject("ocr_unavailable")
                if rendered is None:
                    rendered = fitz.open(stream=content, filetype="pdf")
                source = rendered[number]
                # A truly blank page is not an OCR error. Image-only pages
                # must be read or rejected, including in mixed text/scan PDFs.
                if source.get_images():
                    if source.rect.width * source.rect.height * 4 > limits.OCR_PIXELS:
                        _reject("ocr_pixel_limit")
                    from PIL import Image
                    pixmap = source.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    ocr_text = _ocr(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples))
                    if not ocr_text and not text:
                        _reject("no_text")
                    text = "\n".join(dict.fromkeys(t for t in (text, ocr_text) if t))
                    ocr_pages += 1
                elif source.get_drawings():
                    _reject("ocr_required")
            pages.append(_bounded_text(text))
            if sum(map(len, pages)) > limits.TEXT_CHARACTERS:
                _reject("text_limit")
    finally:
        if rendered is not None:
            rendered.close()
    return _result(pages, ocr_pages)
