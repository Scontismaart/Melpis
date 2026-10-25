"""Focused contract, admission, and recovery tests for the private embedding API."""

import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from src.core.documenti.embedding_contract import ARTIFACTS, DIMENSION, MAX_BODY, MODEL_ID, REVISION, SPACE_ID
from src.core.documenti.embedding_service import create_app


TOKEN = "x" * 32
ORG_A = "8b13d4ca-995d-45f5-8b94-3d823d71168c"


def _vector():
    return [1.0] + [0.0] * (DIMENSION - 1)


def _payload(*, organization_id=ORG_A, tipo="query", texts=None, request_id=None):
    return {
        "organization_id": organization_id,
        "request_id": request_id or str(uuid4()),
        "texts": texts if texts is not None else ["synthetic cafe opening hours"],
        "tipo": tipo,
    }


class FakeWorker:
    """Async worker fake that can block, fail, time out, and restart deterministically."""

    def __init__(self, *, vector_factory=None, block_first=False, delay=0, crash_once=False, startup_error=None):
        self.vector_factory = vector_factory or (lambda payload: [_vector() for _ in payload["texts"]])
        self.block_first = block_first
        self.delay = delay
        self.crash_once = crash_once
        self.startup_error = startup_error
        self.process = None
        self.startups = 0
        self.calls = []
        self.first_call = asyncio.Event()
        self.release_first = asyncio.Event()
        self.closed = False

    async def ensure_started(self):
        if self.startup_error is not None:
            raise RuntimeError(self.startup_error)
        if self.process is None or self.process.returncode is not None:
            self.startups += 1
            self.process = SimpleNamespace(returncode=None)

    async def call(self, payload, timeout):
        await self.ensure_started()
        call_index = len(self.calls)
        self.calls.append({"tipo": payload["tipo"], "texts": list(payload["texts"])})
        if self.crash_once:
            self.crash_once = False
            self.process.returncode = 1
            raise RuntimeError("fake_worker_crashed")
        if self.block_first and call_index == 0:
            self.first_call.set()
            await self.release_first.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.vector_factory(payload)

    async def close(self):
        self.closed = True
        self.release_first.set()
        if self.process is not None and self.process.returncode is None:
            self.process.returncode = 0


@asynccontextmanager
async def _live_app(worker):
    app = create_app(worker_factory=lambda: worker, token=TOKEN, engine="onnx")
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://embedding.test") as client:
            yield app, client


async def _wait_until(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true before test deadline")
        await asyncio.sleep(0.002)


@pytest.mark.asyncio
async def test_health_and_embedding_routes_require_auth_and_health_is_content_free():
    worker = FakeWorker()
    async with _live_app(worker) as (app, client):
        assert (await client.get("/health")).status_code == 401
        assert (await client.post("/v1/embeddings", json=_payload())).status_code == 401
        headers = {"Authorization": f"Bearer {TOKEN}"}

        health = await client.get("/health", headers=headers)
        assert health.status_code == 200
        data = health.json()
        assert data == {
            "ready": True,
            "model_id": MODEL_ID,
            "revision": REVISION,
            "embedding_space": SPACE_ID,
            "dimension": DIMENSION,
            "artifact_sha256": ARTIFACTS["onnx"],
            "queued_queries": 0,
            "queued_passages": 0,
            "in_flight_and_queued": 0,
            "counters": {},
        }
        assert "synthetic cafe opening hours" not in health.text
        assert TOKEN not in health.text

        response = await client.post("/v1/embeddings", json=_payload(), headers=headers)
        assert response.status_code == 200
        assert response.json()["vectors"] == [_vector()]
        assert app.state.broker.counters["completed"] == 1
    assert worker.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    b"{bad json",
    b'{"organization_id":"' + ORG_A.encode() + b'","request_id":"' + str(uuid4()).encode() + b'","request_id":"' + str(uuid4()).encode() + b'","texts":["x"],"tipo":"query"}',
    b'{"organization_id":"' + ORG_A.encode() + b'","request_id":"' + str(uuid4()).encode() + b'","texts":[NaN],"tipo":"query"}',
])
async def test_malformed_duplicate_and_nonfinite_json_are_rejected(body):
    async with _live_app(FakeWorker()) as (_, client):
        response = await client.post("/v1/embeddings", content=body, headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_oversized_body_is_rejected_before_json_parsing():
    async with _live_app(FakeWorker()) as (_, client):
        response = await client.post(
            "/v1/embeddings", content=b"x" * (MAX_BODY + 1),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert response.status_code == 413
        assert response.json()["detail"] == "embedding_body_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", [
    lambda value: value.pop("request_id"),
    lambda value: value.update(extra="unexpected"),
    lambda value: value.update(organization_id="not-a-uuid"),
    lambda value: value.update(tipo="unknown"),
    lambda value: value.update(texts=[]),
    lambda value: value.update(texts=["", "second"]),
    lambda value: value.update(texts=["query one", "query two"]),
    lambda value: value.update(tipo="passage", texts=[f"chunk {index}" for index in range(17)]),
])
async def test_bad_request_schema_and_batch_sizes_are_rejected(mutate):
    payload = _payload()
    mutate(payload)
    async with _live_app(FakeWorker()) as (_, client):
        response = await client.post("/v1/embeddings", json=payload, headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_valid_vectors_have_384_dimensions_and_unit_norm():
    async with _live_app(FakeWorker()) as (_, client):
        response = await client.post("/v1/embeddings", json=_payload(), headers={"Authorization": f"Bearer {TOKEN}"})
    vector = response.json()["vectors"][0]
    assert len(vector) == DIMENSION
    assert sum(value * value for value in vector) == pytest.approx(1.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_vector", [
    [1.0, 0.0],
    [0.0] * DIMENSION,
    [float("nan")] + [0.0] * (DIMENSION - 1),
    [float("inf")] + [0.0] * (DIMENSION - 1),
])
async def test_wrong_dimension_nonunit_and_nonfinite_worker_vectors_fail_closed(invalid_vector):
    worker = FakeWorker(vector_factory=lambda _payload: [invalid_vector])
    async with _live_app(worker) as (_, client):
        response = await client.post("/v1/embeddings", json=_payload(), headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 503
        assert response.json()["detail"] == "embedding_unavailable"


@pytest.mark.asyncio
async def test_per_tenant_in_flight_limit_is_two_and_returns_retry_after():
    worker = FakeWorker(block_first=True)
    async with _live_app(worker) as (app, client):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        first = asyncio.create_task(client.post("/v1/embeddings", json=_payload(), headers=headers))
        await asyncio.wait_for(worker.first_call.wait(), 1)
        second = asyncio.create_task(client.post("/v1/embeddings", json=_payload(tipo="passage"), headers=headers))
        await _wait_until(lambda: len(app.state.broker.passages) == 1)
        rejected = await client.post("/v1/embeddings", json=_payload(tipo="passage"), headers=headers)
        assert rejected.status_code == 429
        assert rejected.headers["retry-after"] == "1"
        assert app.state.broker.per_tenant[ORG_A] == 2
        worker.release_first.set()
        first_response, second_response = await asyncio.gather(first, second)
        assert first_response.status_code == second_response.status_code == 200


@pytest.mark.asyncio
async def test_global_in_flight_limit_is_sixteen():
    worker = FakeWorker(block_first=True)
    async with _live_app(worker) as (app, client):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        tenants = [ORG_A] + [str(uuid4()) for _ in range(7)]
        first = asyncio.create_task(client.post("/v1/embeddings", json=_payload(), headers=headers))
        await asyncio.wait_for(worker.first_call.wait(), 1)
        queued = []
        for tenant in tenants:
            count = 1 if tenant == ORG_A else 2
            for _ in range(count):
                queued.append(asyncio.create_task(client.post(
                    "/v1/embeddings", json=_payload(organization_id=tenant), headers=headers,
                )))
        await _wait_until(lambda: sum(app.state.broker.per_tenant.values()) == 16)
        assert sum(app.state.broker.per_tenant.values()) == 16
        rejected = await client.post("/v1/embeddings", json=_payload(organization_id=str(uuid4())), headers=headers)
        assert rejected.status_code == 429
        worker.release_first.set()
        responses = await asyncio.gather(first, *queued)
        assert all(response.status_code == 200 for response in responses)


@pytest.mark.asyncio
async def test_queries_are_prioritized_but_passages_are_not_starved_after_eight_queries():
    worker = FakeWorker(block_first=True)
    async with _live_app(worker) as (app, client):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        first = asyncio.create_task(client.post("/v1/embeddings", json=_payload(), headers=headers))
        await asyncio.wait_for(worker.first_call.wait(), 1)
        query_tasks = [asyncio.create_task(client.post(
            "/v1/embeddings", json=_payload(organization_id=str(uuid4())), headers=headers,
        )) for _ in range(9)]
        passage_task = asyncio.create_task(client.post(
            "/v1/embeddings", json=_payload(organization_id=str(uuid4()), tipo="passage"), headers=headers,
        ))
        await _wait_until(lambda: len(worker.calls) == 1 and len(app.state.broker.queries) == 9
                          and len(app.state.broker.passages) == 1)
        worker.release_first.set()
        responses = await asyncio.gather(first, *query_tasks, passage_task)
        assert all(response.status_code == 200 for response in responses)
        call_types = [call["tipo"] for call in worker.calls]
        assert call_types[0] == "query"
        assert call_types.index("passage") == 8
        assert call_types[-1] == "query"


@pytest.mark.asyncio
async def test_cancelled_queued_request_is_removed_before_worker_dispatch():
    worker = FakeWorker(block_first=True)
    async with _live_app(worker) as (app, client):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        first = asyncio.create_task(client.post("/v1/embeddings", json=_payload(), headers=headers))
        await asyncio.wait_for(worker.first_call.wait(), 1)
        queued = asyncio.create_task(client.post("/v1/embeddings", json=_payload(tipo="passage"), headers=headers))
        await _wait_until(lambda: len(app.state.broker.passages) == 1)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        await _wait_until(lambda: len(app.state.broker.passages) == 0)
        assert sum(app.state.broker.per_tenant.values()) == 1
        worker.release_first.set()
        assert (await first).status_code == 200


@pytest.mark.asyncio
async def test_timeout_is_sanitized_and_worker_crash_reloads_fake_worker():
    worker = FakeWorker(delay=4.0)
    async with _live_app(worker) as (app, client):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        timed_out = await client.post("/v1/embeddings", json=_payload(), headers=headers, timeout=5)
        assert timed_out.status_code == 503
        assert timed_out.json()["detail"] == "embedding_timeout"
        assert app.state.broker.counters["timeouts"] == 1

    recovering = FakeWorker(crash_once=True)
    async with _live_app(recovering) as (_, client):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        first = await client.post("/v1/embeddings", json=_payload(), headers=headers)
        assert first.status_code == 503
        second = await client.post("/v1/embeddings", json=_payload(), headers=headers)
        assert second.status_code == 200
        assert recovering.startups == 2


@pytest.mark.asyncio
async def test_missing_model_fails_application_startup():
    app = create_app(
        worker_factory=lambda: FakeWorker(startup_error="embedding_assets_unverified"),
        token=TOKEN, engine="onnx",
    )
    with pytest.raises(RuntimeError, match="embedding_assets_unverified"):
        async with app.router.lifespan_context(app):
            pytest.fail("application must fail startup when model assets are unavailable")
