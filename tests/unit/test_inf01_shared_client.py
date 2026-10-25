from uuid import uuid4

import httpx
import pytest

from src.core.documenti import embedding_contract as contract
from src.core.documenti import shared_client


ORG_ID = "8b13d4ca-995d-45f5-8b94-3d823d71168c"


def _vector():
    return [1.0] + [0.0] * (contract.DIMENSION - 1)


def _response(request_payload, *, updates=None, vectors=None):
    value = {
        "vectors": vectors if vectors is not None else [_vector()] * len(request_payload["texts"]),
        "organization_id": request_payload["organization_id"],
        "request_id": request_payload["request_id"],
        "model_id": contract.MODEL_ID,
        "revision": contract.REVISION,
        "embedding_space": contract.SPACE_ID,
        "dimension": contract.DIMENSION,
        "artifact_sha256": contract.ARTIFACTS["torch"],
    }
    value.update(updates or {})
    return httpx.Response(200, json=value)


@pytest.fixture(autouse=True)
def shared_environment(monkeypatch):
    monkeypatch.setenv("MELPIS_EMBEDDING_BACKEND", "shared")
    monkeypatch.setenv("MELPIS_EMBEDDING_URL", "http://127.0.0.1/")
    monkeypatch.setenv("MELPIS_EMBEDDING_TOKEN", "x" * 40)
    monkeypatch.setenv("MELPIS_EMBEDDING_ENGINE", "torch")


async def _mock_http(monkeypatch, handler):
    monkeypatch.setattr(
        shared_client,
        "create_bounded_http_client",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False,
                                           follow_redirects=False, verify=True),
    )


@pytest.mark.asyncio
async def test_shared_client_posts_exact_request_and_validates_identity(monkeypatch):
    seen = {}

    async def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        payload = contract.strict_json(await request.aread())
        seen["payload"] = payload
        return _response(payload)

    await _mock_http(monkeypatch, handler)
    result = await shared_client.embed_shared(["hello"], "query", ORG_ID, 2.0)

    assert result == [_vector()]
    assert seen["url"] == "http://127.0.0.1/v1/embeddings"
    assert seen["authorization"] == "Bearer " + "x" * 40
    assert set(seen["payload"]) == {"organization_id", "request_id", "texts", "tipo"}
    assert seen["payload"]["organization_id"] == ORG_ID
    assert seen["payload"]["tipo"] == "query"
    assert len(seen["payload"]["request_id"]) == 36


@pytest.mark.asyncio
async def test_shared_client_rejects_response_from_another_request(monkeypatch):
    async def handler(request):
        payload = contract.strict_json(await request.aread())
        return _response(payload, updates={"request_id": str(uuid4())})

    await _mock_http(monkeypatch, handler)
    with pytest.raises(shared_client.SharedEmbeddingUnavailable):
        await shared_client.embed_shared(["hello"], "query", ORG_ID, 2.0)


@pytest.mark.asyncio
async def test_shared_client_rejects_response_for_another_organization(monkeypatch):
    async def handler(request):
        payload = contract.strict_json(await request.aread())
        return _response(payload, updates={"organization_id": "8b13d4ca-995d-45f5-8b94-3d823d71169c"})

    await _mock_http(monkeypatch, handler)
    with pytest.raises(shared_client.SharedEmbeddingUnavailable):
        await shared_client.embed_shared(["hello"], "query", ORG_ID, 2.0)


@pytest.mark.asyncio
async def test_shared_client_rejects_non_integer_dimension(monkeypatch):
    async def handler(request):
        payload = contract.strict_json(await request.aread())
        return _response(payload, updates={"dimension": float(contract.DIMENSION)})

    await _mock_http(monkeypatch, handler)
    with pytest.raises(shared_client.SharedEmbeddingUnavailable):
        await shared_client.embed_shared(["hello"], "query", ORG_ID, 2.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,headers,body_size",
    [
        (302, {"Location": "http://evil.example/"}, 8),
        (200, {}, contract.MAX_BODY + 1),
    ],
)
async def test_shared_client_rejects_redirect_and_oversized_response(
    monkeypatch, status, headers, body_size
):
    from src.core.ai_raw_transport import create_bounded_http_client

    async def handler(request):
        return httpx.Response(status, headers=headers, content=b"x" * body_size)

    original_factory = create_bounded_http_client
    monkeypatch.setattr(
        shared_client,
        "create_bounded_http_client",
        lambda **kwargs: original_factory(
            **kwargs, transport=httpx.MockTransport(handler)
        ),
    )

    with pytest.raises(shared_client.SharedEmbeddingUnavailable):
        await shared_client.embed_shared(["hello"], "query", ORG_ID, 2.0)


@pytest.mark.asyncio
async def test_shared_client_rejects_bad_vectors_and_fingerprint(monkeypatch):
    async def bad_vector(request):
        payload = contract.strict_json(await request.aread())
        return _response(payload, vectors=[[0.0] * contract.DIMENSION])

    await _mock_http(monkeypatch, bad_vector)
    with pytest.raises(shared_client.SharedEmbeddingUnavailable):
        await shared_client.embed_shared(["hello"], "query", ORG_ID, 2.0)

    async def bad_fingerprint(request):
        payload = contract.strict_json(await request.aread())
        return _response(payload, updates={"revision": "unpinned"})

    await _mock_http(monkeypatch, bad_fingerprint)
    with pytest.raises(shared_client.SharedEmbeddingUnavailable):
        await shared_client.embed_shared(["hello"], "query", ORG_ID, 2.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://evil.example/", "http://localhost/?x=1", "http://user@localhost/",
    "http://localhost/#fragment", "http://localhost/other",
])
async def test_shared_client_rejects_unapproved_url_before_connect(monkeypatch, url):
    monkeypatch.setenv("MELPIS_EMBEDDING_URL", url)
    with pytest.raises(shared_client.SharedEmbeddingUnavailable):
        await shared_client.embed_shared(["hello"], "query", ORG_ID, 2.0)


@pytest.mark.asyncio
async def test_shared_client_rejects_missing_org_and_short_token(monkeypatch):
    with pytest.raises(shared_client.SharedEmbeddingUnavailable):
        await shared_client.embed_shared(["hello"], "query", None, 2.0)
    monkeypatch.setenv("MELPIS_EMBEDDING_TOKEN", "short")
    with pytest.raises(shared_client.SharedEmbeddingUnavailable):
        await shared_client.embed_shared(["hello"], "query", ORG_ID, 2.0)


@pytest.mark.asyncio
async def test_shared_backend_failure_never_falls_back_to_local(monkeypatch):
    from src.core.documenti import isolated

    async def unavailable(*args, **kwargs):
        raise shared_client.SharedEmbeddingUnavailable("embedding_unavailable")

    def local_worker(*args, **kwargs):
        raise AssertionError("shared backend must not start a local model")

    monkeypatch.setattr(shared_client, "embed_shared", unavailable)
    monkeypatch.setattr(isolated, "LocalWorker", local_worker)
    with pytest.raises(shared_client.SharedEmbeddingUnavailable):
        await isolated.embed_local(["hello"], tipo="query", organization_id=ORG_ID)
