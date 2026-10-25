"""Private embedding wire contract; identity is not tenant authorization."""

import json
import math
import os
import re
from uuid import UUID

MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
SPACE_ID = "minilm-e8f8c211-query-passage-mean-l2-384-v1"
DIMENSION = 384
MAX_BODY = 262144
ARTIFACTS = {
    "torch": "eaa086f0ffee582aeb45b36e34cdd1fe2d6de2bef61f8a559a1bbc9bd955917b",
    "onnx": "10f7a088420252b26caf819236ca2c9d2987afd0fc06fec7553b542a5655a05a",
}
VOCABULARY_SHA256 = "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"


def service_token():
    """Read a bounded deployment secret, never a value supplied by a caller."""
    value = os.getenv("MELPIS_EMBEDDING_TOKEN", "")
    path = os.getenv("MELPIS_EMBEDDING_TOKEN_FILE", "")
    try:
        if path:
            if value:
                raise ValueError()
            with open(path, "rb") as stream:
                raw = stream.read(259)
            if len(raw) > 258:
                raise ValueError()
            value = raw.decode("ascii")
            if value.endswith("\r\n"):
                value = value[:-2]
            elif value.endswith("\n"):
                value = value[:-1]
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value):
            raise ValueError()
    except (OSError, UnicodeError, ValueError):
        raise ValueError("embedding_configuration_invalid") from None
    return value


def strict_json(body):
    if type(body) is not bytes or not body or len(body) > MAX_BODY:
        raise ValueError("embedding_body_limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("embedding_invalid_json")
            result[key] = value
        return result

    def reject(_):
        raise ValueError("embedding_invalid_json")

    try:
        result = json.loads(body, object_pairs_hook=unique, parse_constant=reject)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("embedding_invalid_json") from None
    if type(result) is not dict:
        raise ValueError("embedding_invalid_json")
    return result


def identity(value):
    if type(value) is not str:
        raise ValueError("embedding_invalid_identity")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise ValueError("embedding_invalid_identity") from None
    if not parsed.int or str(parsed) != value.lower():
        raise ValueError("embedding_invalid_identity")
    return str(parsed)


def validate_request(payload):
    if type(payload) is not dict or set(payload) != {
        "organization_id",
        "request_id",
        "texts",
        "tipo",
    }:
        raise ValueError("embedding_invalid_request")
    payload["organization_id"] = identity(payload["organization_id"])
    payload["request_id"] = identity(payload["request_id"])
    texts, tipo = payload["texts"], payload["tipo"]
    if (
        type(tipo) is not str
        or tipo not in {"query", "passage"}
        or type(texts) is not list
        or not 1 <= len(texts) <= (1 if tipo == "query" else 16)
        or any(type(t) is not str or not t.strip() or len(t) > 12000 for t in texts)
        or sum(map(len, texts)) > 64000
    ):
        raise ValueError("embedding_input_limit")
    return payload


def validate_vectors(vectors, count):
    if type(vectors) is not list or len(vectors) != count:
        raise ValueError("embedding_invalid_vectors")
    for vector in vectors:
        if (
            type(vector) is not list
            or len(vector) != DIMENSION
            or any(
                type(v) not in {int, float} or abs(v) > 1.001 or not math.isfinite(v)
                for v in vector
            )
            or abs(sum(v * v for v in vector) - 1) > 0.002
        ):
            raise ValueError("embedding_invalid_vectors")
    return vectors


def response(vectors, payload, engine):
    return {
        "vectors": validate_vectors(vectors, len(payload["texts"])),
        "organization_id": payload["organization_id"],
        "request_id": payload["request_id"],
        "model_id": MODEL_ID,
        "revision": REVISION,
        "embedding_space": SPACE_ID,
        "dimension": DIMENSION,
        "artifact_sha256": ARTIFACTS[engine],
    }
