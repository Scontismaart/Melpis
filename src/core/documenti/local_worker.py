"""Private bounded local extraction/embedding process. No tenant authority or AI tools."""
import base64
import json
import os
import sys


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
    sys.stdout.flush()


def read_request():
    line = sys.stdin.buffer.readline(40 * 1024 * 1024 + 1)
    if not line or len(line) > 40 * 1024 * 1024:
        raise ValueError("invalid_worker_request")
    return json.loads(line)


def main():
    mode = sys.argv[1]
    # The parent attaches the ACTUAL interpreter PID (not a venv launcher)
    # to its Windows memory-limited job before sending the startup command.
    emit({"pid": os.getpid()})
    if read_request() != {"start": True}:
        return
    from src.core.documenti import limits
    from src.core.documenti.process_limits import set_posix_limits
    budget = limits.EXTRACTION_MEMORY_BYTES if mode == "extract" else limits.EMBEDDING_MEMORY_BYTES
    if os.name != "nt":
        set_posix_limits(budget)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    try:
        if mode in {"embed", "embed_verified"}:
            import torch
            torch.set_num_threads(2)
            from sentence_transformers import SentenceTransformer
            model_path = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            if mode == "embed_verified":
                from src.core.documenti.embedding_assets import verified_assets
                model_path = str(verified_assets("torch")[0])
            model = SentenceTransformer(
                model_path,
                local_files_only=True, trust_remote_code=False,
            )
        elif mode == "embed_onnx":
            from src.core.documenti.embedding_assets import OfflineOnnxEncoder
            model = OfflineOnnxEncoder()
        elif mode != "extract":
            return
        emit({"ready": True})
        while True:
            request = read_request()
            if mode == "extract":
                from src.core.documenti.safe_extraction import extract_document
                payload = base64.b64decode(request["content"], validate=True)
                emit({"result": extract_document(payload, request["name"], request.get("content_type", ""))})
                return
            texts = request["texts"]
            if (not isinstance(texts, list) or not 1 <= len(texts) <= limits.CHUNKS
                    or any(not isinstance(t, str) or len(t) > 12_000 for t in texts)
                    or sum(map(len, texts)) > limits.TEXT_CHARACTERS):
                raise ValueError("embedding_input_limit")
            prefix = "query: " if request.get("tipo") == "query" else "passage: "
            if mode == "embed_onnx":
                vectors = model.encode(texts, request.get("tipo", "passage"))
            else:
                vectors = model.encode([prefix + t for t in texts], batch_size=16,
                                       normalize_embeddings=True, show_progress_bar=False).tolist()
            emit({"result": vectors})
            del request, texts, vectors
    except Exception as exc:
        # Never serialize decoder/model exception strings or document contents.
        from src.core.documenti.safe_extraction import ExtractionRejected
        code = str(exc) if type(exc) is ExtractionRejected else "local_processing_failed"
        emit({"error": code})


if __name__ == "__main__":
    main()
