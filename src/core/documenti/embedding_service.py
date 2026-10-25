"""Private, bounded, single-model embedding service. No database or tenant data store."""

import asyncio
from collections import deque, Counter
from contextlib import asynccontextmanager, nullcontext
import hmac
import os
import re
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from src.core.documenti.embedding_contract import (
    ARTIFACTS,
    DIMENSION,
    MAX_BODY,
    MODEL_ID,
    REVISION,
    SPACE_ID,
    response,
    strict_json,
    validate_request,
    service_token,
)
from src.core.documenti.isolated import LocalWorker
from src.core.documenti.embedding_instance import instance_lock

BODY_SECONDS = 2.0


def worker_ready(worker):
    starting = getattr(worker, "starting", None)
    return (
        worker.process is not None
        and worker.process.returncode is None
        and (
            starting is None
            or (
                starting.done()
                and not starting.cancelled()
                and starting.exception() is None
            )
        )
    )


class EmbeddingBroker:
    """At most one inference in flight; bounded queues and query-weighted fairness."""

    def __init__(self, worker, engine):
        self.worker, self.engine = worker, engine
        self.queries, self.passages = deque(), deque()
        self.per_tenant = Counter()
        self.seen = set()
        self.changed = asyncio.Event()
        self.runner = None
        self.closed = False
        self.counters = Counter()
        self.query_burst = 0

    async def start(self):
        await self.worker.ensure_started()
        self.runner = asyncio.create_task(self.run())

    async def submit(self, payload):
        org, request_id = payload["organization_id"], payload["request_id"]
        key = (org, request_id)
        if self.closed:
            raise HTTPException(503, "embedding_unavailable")
        if key in self.seen:
            raise HTTPException(409, "embedding_request_in_progress")
        if sum(self.per_tenant.values()) >= 16 or self.per_tenant[org] >= 2:
            self.counters["rejected"] += 1
            raise HTTPException(429, "embedding_busy", headers={"Retry-After": "1"})
        future = asyncio.get_running_loop().create_future()
        budget = 3.0 if payload["tipo"] == "query" else 30.0
        item = (payload, future, time.monotonic() + budget)
        self.seen.add(key)
        self.per_tenant[org] += 1
        (self.queries if payload["tipo"] == "query" else self.passages).append(item)
        self.changed.set()
        try:
            return await asyncio.wait_for(future, timeout=budget)
        except TimeoutError:
            self.counters["timeouts"] += 1
            raise HTTPException(503, "embedding_timeout") from None
        finally:
            # If cancelled before dispatch, discard immediately, not after the
            # currently running model batch. Never retry a dispatched request.
            if future.cancelled():
                for queue in (self.queries, self.passages):
                    try:
                        queue.remove(item)
                    except ValueError:
                        continue
                    self.release(payload)
                    break

    def release(self, payload):
        org = payload["organization_id"]
        self.seen.discard((org, payload["request_id"]))
        self.per_tenant[org] -= 1
        if not self.per_tenant[org]:
            del self.per_tenant[org]

    def next(self):
        if self.queries and (self.query_burst < 8 or not self.passages):
            self.query_burst += 1
            return self.queries.popleft()
        if self.passages:
            self.query_burst = 0
            return self.passages.popleft()
        return None

    async def run(self):
        while not self.closed:
            item = self.next()
            if item is None:
                self.changed.clear()
                await self.changed.wait()
                continue
            payload, future, deadline = item
            try:
                if future.cancelled() or deadline <= time.monotonic():
                    continue
                vectors = await self.worker.call(
                    {"texts": payload["texts"], "tipo": payload["tipo"]},
                    max(0.001, deadline - time.monotonic()),
                )
                result = response(vectors, payload, self.engine)
                self.counters["completed"] += 1
                if not future.done():
                    future.set_result(result)
            except asyncio.CancelledError:
                if not future.done():
                    future.set_exception(HTTPException(503, "embedding_unavailable"))
                raise
            except Exception:
                self.counters["failures"] += 1
                if not future.done():
                    future.set_exception(HTTPException(503, "embedding_unavailable"))
            finally:
                self.release(payload)

    async def close(self):
        self.closed = True
        if self.runner is not None:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)
        for queue in (self.queries, self.passages):
            while queue:
                payload, future, _ = queue.popleft()
                if not future.done():
                    future.set_exception(HTTPException(503, "embedding_unavailable"))
                self.release(payload)
        await self.worker.close()


def create_app(*, worker_factory=None, token=None, engine=None):
    """Explicit factory seams are server/test code, never request configuration."""

    @asynccontextmanager
    async def lifespan(app):
        secret = token if token is not None else service_token()
        selected = engine or os.getenv("MELPIS_EMBEDDING_ENGINE", "onnx")
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret)
            or selected not in ARTIFACTS
        ):
            raise RuntimeError("embedding_configuration_invalid")
        app.state.secret = secret.encode("ascii")
        worker = (
            worker_factory()
            if worker_factory
            else LocalWorker(
                "embed_onnx" if selected == "onnx" else "embed_verified",
            )
        )
        broker = EmbeddingBroker(worker, selected)
        app.state.broker = broker
        # A deployment with accidental uvicorn workers > 1 must fail before
        # allocating another model. Test workers do not own actual model assets.
        with instance_lock() if worker_factory is None else nullcontext():
            try:
                await broker.start()
                yield
            finally:
                await broker.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authenticate(request):
        try:
            supplied = request.headers.get("Authorization", "").encode("ascii")
        except UnicodeEncodeError:
            raise HTTPException(401, "embedding_unauthorized") from None
        if len(supplied) > 300 or not hmac.compare_digest(
            supplied,
            b"Bearer " + request.app.state.secret,
        ):
            raise HTTPException(401, "embedding_unauthorized")

    @app.exception_handler(ValueError)
    async def invalid(_request, _exc):
        return JSONResponse({"detail": "embedding_invalid_request"}, status_code=422)

    @app.get("/health")
    async def health(request: Request):
        authenticate(request)
        broker = request.app.state.broker
        ready = worker_ready(broker.worker)
        return JSONResponse(
            {
                "ready": ready,
                "model_id": MODEL_ID,
                "revision": REVISION,
                "embedding_space": SPACE_ID,
                "dimension": DIMENSION,
                "artifact_sha256": ARTIFACTS[broker.engine],
                "queued_queries": len(broker.queries),
                "queued_passages": len(broker.passages),
                "in_flight_and_queued": sum(broker.per_tenant.values()),
                "counters": dict(broker.counters),
            },
            status_code=200 if ready else 503,
        )

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        authenticate(request)
        body = bytearray()
        try:
            async with asyncio.timeout(BODY_SECONDS):
                async for part in request.stream():
                    if len(body) + len(part) > MAX_BODY:
                        raise HTTPException(413, "embedding_body_limit")
                    body.extend(part)
        except TimeoutError:
            raise HTTPException(408, "embedding_request_timeout") from None
        payload = validate_request(strict_json(bytes(body)))
        return await request.app.state.broker.submit(payload)

    return app


app = create_app()
