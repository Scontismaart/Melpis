"""Prepare with bounded local resources, then publish one complete tenant index."""
import asyncio
import hashlib
import json
from uuid import UUID, uuid5
import weakref

from fastapi import HTTPException

from src.core.documenti import limits
from src.core.documenti.access import rag_access
from src.core.documenti.chunking import chunk_testo
from src.core.documenti.embeddings import vettorizza
from src.core.documenti.isolated import embed_local, extract_local
from src.core.documenti.file_quota import FileQuotaError, http_error

_index_locks = weakref.WeakKeyDictionary()


def document_repository(repo):
    return getattr(repo, "doc_repo", repo)


async def index_content(repo, org_id, name, **kwargs):
    loop = asyncio.get_running_loop()
    lock = _index_locks.get(loop)
    if lock is None:
        lock = _index_locks[loop] = asyncio.Lock()
    try:
        await asyncio.wait_for(lock.acquire(), timeout=0.01)
    except TimeoutError:
        raise HTTPException(429, "Indicizzazione locale occupata. Riprova tra poco.", headers={"Retry-After": "5"}) from None
    try:
        return await _index_content(repo, org_id, name, **kwargs)
    finally:
        lock.release()


async def _index_content(repo, org_id, name, *, request_key=None, text=None, file_content=None,
                        content_type="", tipo="upload", fonte="", document_id=None,
                        metadata=None, embedding_fn=None, expected_profile=None):
    if not await rag_access(repo, org_id):
        raise HTTPException(403, "Knowledge Base AI non disponibile per il piano o lo stato corrente.")
    if not isinstance(name, str) or not name.strip() or len(name) > 300:
        raise HTTPException(422, "Nome documento non valido.")
    if (text is None) == (file_content is None):
        raise HTTPException(422, "Contenuto documento non valido.")
    source = text.encode("utf-8") if text is not None else file_content
    if not source or len(source) > limits.UPLOAD_BYTES:
        raise HTTPException(413, "Dimensione documento non consentita.")
    metadata = {key: value for key, value in (metadata or {}).items()
                if key not in {"pages", "ocr_pages", "bytes", "chunk_pages", "chunks",
                               "original_file_bytes", "original_file_sha256"}}
    original_bytes = len(file_content) if file_content is not None else 0
    original_hash = hashlib.sha256(file_content).hexdigest() if file_content is not None else None
    spec = {"content": hashlib.sha256(source).hexdigest(), "name": name,
            "tipo": tipo, "fonte": fonte, "target": str(document_id) if document_id else None,
            "content_type": content_type, "metadata": metadata or {}, "pipeline": 2,
            "original_file_bytes": original_bytes, "original_file_sha256": original_hash,
            "expected_profile": expected_profile}
    payload_hash = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    try:
        key = UUID(str(request_key)) if request_key else uuid5(UUID(str(org_id)), payload_hash)
        documents = document_repository(repo)
        claim = await documents.begin_index(org_id, name, key, payload_hash, tipo=tipo,
                                             fonte=fonte, document_id=document_id, expected_profile=expected_profile,
                                             file_bytes=original_bytes, file_sha256=original_hash)
    except FileQuotaError as exc:
        raise http_error(exc) from None
    except ValueError as exc:
        if str(exc) == "rag_not_authorized":
            raise HTTPException(403, "Knowledge Base AI non disponibile.") from None
        raise HTTPException(409, "Richiesta documento non valida o non piu disponibile.") from None
    status, document = claim["status"], claim.get("document")
    if status == "conflict":
        raise HTTPException(409, "Richiesta gia utilizzata o fonte modificata. Avvia una nuova operazione.")
    if status in {"replay", "in_progress"}:
        return {"status": status, "document": document, "indexed": (document.get("metadata") or {}).get("chunks", 0)}
    try:
        async with asyncio.timeout(limits.INDEX_SECONDS):
            extracted = await extract_local(file_content, name, content_type) if file_content is not None else {
                "page_texts": [text], "pages": 0, "ocr_pages": 0,
            }
            pages = extracted["page_texts"]
            if sum(map(len, pages)) > limits.TEXT_CHARACTERS:
                raise ValueError("text_limit")
            chunks, chunk_pages = [], []
            for page_number, page in enumerate(pages, 1):
                parts = chunk_testo(page)
                chunks.extend(parts)
                chunk_pages.extend([page_number] * len(parts))
                if len(chunks) > limits.CHUNKS:
                    raise ValueError("chunk_limit")
            if not chunks:
                raise ValueError("no_text")
            if embedding_fn is not None and embedding_fn is not vettorizza:
                # Backward-compatible dependency injection is a server-only
                # test/custom adapter boundary, never accepted from HTTP data.
                vectors = await asyncio.to_thread(embedding_fn, chunks, tipo="passage")
            else:
                vectors = []
                # Yield between small model batches so a knowledge query is
                # not queued behind an entire large document.
                for offset in range(0, len(chunks), 16):
                    vectors.extend(await embed_local(chunks[offset:offset + 16], timeout=limits.INDEX_SECONDS,
                                                     organization_id=str(org_id)))
            record = await documents.publish_index(org_id, document["id"], claim["token"], claim["fence"],
                chunks, vectors, {**(metadata or {}), "pages": extracted["pages"],
                                  "ocr_pages": extracted["ocr_pages"], "bytes": len(source), "chunk_pages": chunk_pages})
            return {"status": "indexed", "document": record, "indexed": len(chunks)}
    except BaseException as exc:
        messages = {
            "ocr_unavailable": "OCR locale non disponibile. Carica un PDF con testo selezionabile.",
            "ocr_required": "Questa pagina richiede OCR. Carica una versione con testo selezionabile.",
            "protected_pdf": "Il PDF e protetto. Carica una copia non protetta.",
            "no_text": "Il documento non contiene testo leggibile.",
            "page_limit": "Il PDF supera il limite tecnico di pagine per documento.",
            "chunk_limit": "Il documento supera il limite tecnico di frammenti.",
        }
        code = str(exc) if str(exc) in messages else "indexing_failed"
        try:
            await asyncio.shield(documents.fail_index(org_id, document["id"], claim["token"], claim["fence"], code))
        except Exception:
            pass  # Still not certified; expiry permits a fenced, explicit retry.
        if isinstance(exc, asyncio.CancelledError):
            raise
        if isinstance(exc, FileQuotaError):
            raise http_error(exc) from None
        raise HTTPException(422, messages.get(code, "Indicizzazione non completata. Verifica il file o riprova; nessuna fonte parziale e disponibile.")) from None


async def knowledge_limits(repo, org_id):
    documents = await document_repository(repo).list_sources(org_id)
    usage = {"documents": len(documents), "pages": 0, "chunks": 0}
    for document in documents:
        metadata = document.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        usage["pages"] += int(metadata.get("pages") or 0)
        usage["chunks"] += int(document.get("chunk") or 0)
    current = await document_repository(repo).file_quota_status(org_id)
    quota = current["file_quota"]
    usage.update(file_bytes=quota["committed_bytes"], reserved_file_bytes=quota["reserved_bytes"],
                 unknown_file_sources=quota["unknown_documents"])
    return {**current,
            "technical_limits": limits.technical_limits(), "usage": usage,
            "commercial_limits": {"file_bytes": quota["limit_bytes"], "status": quota["status"]},
            "remaining": {"file_bytes": quota["remaining_bytes"]}}
