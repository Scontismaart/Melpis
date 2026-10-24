"""Operational ceilings, not commercial plan entitlements or allowances."""
UPLOAD_BYTES = 20 * 1024 * 1024
PDF_PAGES = 200
CHUNKS = 4000
TEXT_CHARACTERS = 2_000_000
DECOMPRESSED_BYTES = 8 * 1024 * 1024
OCR_PIXELS = 4_000_000
EXTRACTION_SECONDS = 45
INDEX_SECONDS = 600
MODEL_START_SECONDS = 60
QUERY_SECONDS = 3
EXTRACTION_MEMORY_BYTES = 512 * 1024 * 1024
EMBEDDING_MEMORY_BYTES = 2 * 1024 * 1024 * 1024


def technical_limits():
    return {"upload_bytes": UPLOAD_BYTES, "pages_per_document": PDF_PAGES,
            "chunks_per_document": CHUNKS, "text_characters": TEXT_CHARACTERS,
            "extraction_seconds": EXTRACTION_SECONDS, "index_seconds": INDEX_SECONDS,
            "embedding_memory_bytes": EMBEDDING_MEMORY_BYTES}
