import io
from functools import lru_cache
from pathlib import Path

from PIL import Image
from pypdf import PdfReader


ESTENSIONI_TESTO = {".txt", ".md", ".csv", ".json"}
ESTENSIONI_PDF = {".pdf"}
ESTENSIONI_IMMAGINE = {".png", ".jpg", ".jpeg", ".webp"}
ESTENSIONI_DOCX = {".docx"}


@lru_cache(maxsize=1)
def _ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise ValueError("OCR locale non installato. Esegui pip install -r requirements.txt.") from exc

    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)


def _ocr_immagine(immagine) -> str:
    risultato, _ = _ocr_engine()(immagine)
    testo = "\n".join(riga[1] for riga in (risultato or []) if len(riga) > 1 and riga[1])
    return testo.strip()


def _estrai_immagine(contenuto: bytes, nome: str) -> str:
    immagine = Image.open(io.BytesIO(contenuto)).convert("RGB")
    testo = _ocr_immagine(immagine)
    if not testo.strip():
        raise ValueError(f"Nessun testo leggibile trovato in {nome}.")
    return testo.strip()


def _estrai_docx(contenuto: bytes, nome: str) -> str:
    try:
        import zipfile
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(contenuto)) as z:
            if "word/document.xml" not in z.namelist():
                raise ValueError(f"Il file {nome} non sembra essere un documento DOCX valido.")
            xml_bytes = z.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        paragrafi = []
        for elem in root.iter():
            if elem.tag.endswith("}p") or elem.tag == "p":
                testo_paragrafo = "".join(
                    child.text for child in elem.iter()
                    if (child.tag.endswith("}t") or child.tag == "t") and child.text
                )
                if testo_paragrafo.strip():
                    paragrafi.append(testo_paragrafo.strip())
        testo = "\n\n".join(paragrafi).strip()
        if not testo:
            raise ValueError(f"Nessun testo leggibile trovato in {nome}.")
        return testo
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Errore lettura file DOCX {nome}: {exc}") from exc


def _estrai_pdf_scansionato(contenuto: bytes, nome: str) -> str:
    try:
        import fitz
    except ImportError as exc:
        raise ValueError(f"Il PDF {nome} è scansionato. Installa il supporto PDF locale e riprova.") from exc

    testo_pagine = []
    documento = fitz.open(stream=contenuto, filetype="pdf")
    for pagina in documento:
        pixmap = pagina.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        testo = _ocr_immagine(Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB"))
        if testo:
            testo_pagine.append(testo)
    return "\n\n".join(testo_pagine).strip()


def estrai_testo(contenuto: bytes, nome: str, content_type: str = "") -> str:
    estensione = Path(nome).suffix.lower()
    if estensione in ESTENSIONI_TESTO or content_type.startswith("text/"):
        return contenuto.decode("utf-8-sig", errors="replace").strip()
    if estensione in ESTENSIONI_DOCX or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _estrai_docx(contenuto, nome)
    if estensione in ESTENSIONI_PDF or content_type == "application/pdf":
        reader = PdfReader(io.BytesIO(contenuto))
        testo = "\n\n".join((pagina.extract_text() or "").strip() for pagina in reader.pages)
        if not testo.strip():
            testo = _estrai_pdf_scansionato(contenuto, nome)
        if not testo.strip():
            raise ValueError(f"Nessun testo leggibile trovato in {nome}.")
        return testo.strip()
    if estensione in ESTENSIONI_IMMAGINE or content_type.startswith("image/"):
        return _estrai_immagine(contenuto, nome)
    raise ValueError("Formato non supportato. Usa TXT, MD, CSV, JSON, PDF, DOCX, PNG, JPG o WEBP.")
