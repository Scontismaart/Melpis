"""Fail-closed HTTP client for the private shared embedding worker."""

import asyncio
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

from src.core.ai_raw_transport import create_bounded_http_client
from src.core.documenti import embedding_contract as contract


class SharedEmbeddingUnavailable(RuntimeError):
    """A sanitized shared embedding failure."""


_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", "embedding", "melpis-embedding"}
_RESPONSE_KEYS = {
    "vectors",
    "organization_id",
    "request_id",
    "model_id",
    "revision",
    "embedding_space",
    "dimension",
    "artifact_sha256",
}


def _configuration():
    backend = os.getenv("MELPIS_EMBEDDING_BACKEND", "local").strip().lower()
    if backend not in {"local", "shared"}:
        raise SharedEmbeddingUnavailable("embedding_configuration_invalid")
    return backend


def _url():
    raw = os.getenv("MELPIS_EMBEDDING_URL", "").strip()
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        # Accessing port also validates malformed port syntax.
        port = parsed.port
    except ValueError:
        raise SharedEmbeddingUnavailable("embedding_configuration_invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or host not in _ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise SharedEmbeddingUnavailable("embedding_configuration_invalid")
    return raw.rstrip("/") + "/v1/embeddings"


def _token():
    return contract.service_token()


def _engine():
    engine = os.getenv("MELPIS_EMBEDDING_ENGINE", "onnx").strip().lower()
    if engine not in contract.ARTIFACTS:
        raise SharedEmbeddingUnavailable("embedding_configuration_invalid")
    return engine


async def embed_shared(texts, tipo, organization_id, timeout):
    """Embed text through the authenticated private service, with no retries."""
    try:
        request = contract.validate_request(
            {
                "organization_id": organization_id,
                "request_id": str(uuid4()),
                "texts": texts,
                "tipo": tipo,
            }
        )
        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(body) > contract.MAX_BODY:
            raise ValueError("embedding_body_limit")
        url = _url()
        token = _token()
        engine = _engine()
        expected = {
            "model_id": contract.MODEL_ID,
            "revision": contract.REVISION,
            "embedding_space": contract.SPACE_ID,
            "dimension": contract.DIMENSION,
            "artifact_sha256": contract.ARTIFACTS[engine],
        }
        async with asyncio.timeout(timeout):
            async with create_bounded_http_client(
                max_response_bytes=contract.MAX_BODY,
                max_concurrency=1,
                deadline_seconds=timeout,
            ) as client:
                response = await client.post(
                    url,
                    content=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
            if response.status_code != 200:
                raise ValueError("embedding_http_error")
            decoded = contract.strict_json(response.content)
            if set(decoded) != _RESPONSE_KEYS:
                raise ValueError("embedding_invalid_response")
            if type(decoded.get("dimension")) is not int:
                raise ValueError("embedding_invalid_response")
            if (
                decoded.get("organization_id") != request["organization_id"]
                or decoded.get("request_id") != request["request_id"]
            ):
                raise ValueError("embedding_invalid_response")
            if any(decoded.get(key) != value for key, value in expected.items()):
                raise ValueError("embedding_invalid_response")
            return contract.validate_vectors(decoded.get("vectors"), len(texts))
    except asyncio.CancelledError:
        raise
    except (Exception, asyncio.TimeoutError):
        raise SharedEmbeddingUnavailable("embedding_unavailable") from None
