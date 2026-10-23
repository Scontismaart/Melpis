"""Synthetic local-provider tests for durable GDPR propagation fencing."""
import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio

from src.core.gdpr import propagation
from src.core.gdpr.operations import OperationUnavailable, Operations


@dataclass
class ProviderState:
    deletes: int = 0
    lookups: int = 0
    fail_first_delete: bool = False
    delay: float = 0
    mismatch: bool = False
    last_org: str | None = None
    last_operation: str | None = None
    deleted: bool = False

    def receipt(self):
        org = str(uuid.uuid4()) if self.mismatch else self.last_org
        return {
            "organization_id": org,
            "operation_id": self.last_operation,
            "provider": "provider.synthetic",
            "status": "deleted",
            "evidence_digest": hashlib.sha256(b"synthetic provider verification").hexdigest(),
        }


@pytest_asyncio.fixture
async def local_provider():
    state = ProviderState()

    async def respond(writer, status, body=b""):
        labels = {200: "OK", 503: "Service Unavailable"}
        writer.write(
            f"HTTP/1.1 {status} {labels[status]}\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def handler(reader, writer):
        line = await reader.readline()
        try:
            method, path, _version = line.decode("ascii").strip().split(" ", 2)
        except ValueError:
            await respond(writer, 503)
            return
        headers = {}
        while line := await reader.readline():
            if line in (b"\r\n", b"\n"):
                break
            name, value = line.decode("latin-1").split(":", 1)
            headers[name.casefold()] = value.strip()
        if method == "DELETE" and path == "/delete":
            state.deletes += 1
            state.last_org = headers.get("x-organization-id")
            state.last_operation = headers.get("x-operation-id")
            if state.delay:
                await asyncio.sleep(state.delay)
            state.deleted = True
            if state.fail_first_delete and state.deletes == 1:
                await respond(writer, 503)
                return
            receipt = state.receipt()
            await respond(writer, 200, json.dumps(receipt).encode())
            return
        if method == "GET" and path == "/receipt":
            state.lookups += 1
            if state.deleted:
                await respond(writer, 200, json.dumps(state.receipt()).encode())
            else:
                await respond(writer, 503)
            return
        await respond(writer, 503)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield state, f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


class LocalSyntheticAdapter:
    provider = "provider.synthetic"
    idempotent_delete = True

    def __init__(self, base_url, *, secret="synthetic-local-secret"):
        self.base_url = base_url
        self.secret = secret

    async def delete(self, org, operation_id, *, idempotency_key):
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                self.base_url + "/delete",
                headers={
                    "X-Organization-Id": str(org),
                    "X-Operation-Id": str(operation_id),
                    "Idempotency-Key": idempotency_key,
                    "X-Synthetic-Secret": self.secret,
                },
            )
        if response.status_code >= 500:
            raise OSError("synthetic provider response is uncertain")
        return response.json()

    async def lookup_deletion(self, org, operation_id):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.base_url + "/receipt",
                headers={
                    "X-Organization-Id": str(org),
                    "X-Operation-Id": str(operation_id),
                },
            )
        if response.status_code >= 500:
            raise OSError("synthetic receipt is not available")
        return response.json()


async def make_partial_operation(pool, org, requester="provider-qa"):
    oid = await Operations(pool).create(
        org, requester, uuid.uuid4(), kind="erase", scope="organization",
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE gdpr_operations SET status='partial' WHERE organization_id=$1 AND id=$2",
            org, oid,
        )
        await conn.execute(
            """INSERT INTO gdpr_operation_steps(organization_id,operation_id,name,status,attempts)
               VALUES($1,$2,'local','confirmed',1)""",
            org, oid,
        )
    return oid


@pytest.fixture(autouse=True)
def _provider_cutover_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GDPR_PROVIDER_ERASURE_EXECUTION_ENABLED", raising=False)


@pytest.mark.asyncio
async def test_provider_cutover_disabled_makes_no_http_request(pg_pool, sample_org, local_provider):
    state, url = local_provider
    oid = await make_partial_operation(pg_pool, sample_org["id"])
    with pytest.raises(OperationUnavailable):
        await propagation.propagate_operation(
            pg_pool, sample_org["id"], oid, "provider.synthetic", LocalSyntheticAdapter(url),
        )
    assert state.deletes == 0


@pytest.mark.asyncio
async def test_uncertain_delete_is_not_retried_and_get_receipt_reconciles(
    pg_pool, sample_org, local_provider, monkeypatch, caplog,
):
    state, url = local_provider
    state.fail_first_delete = True
    adapter = LocalSyntheticAdapter(url)
    org = sample_org["id"]
    oid = await make_partial_operation(pg_pool, org)
    monkeypatch.setenv("GDPR_PROVIDER_ERASURE_EXECUTION_ENABLED", "true")

    assert await propagation.propagate_operation(pg_pool, org, oid, adapter.provider, adapter) == "uncertain"
    assert await propagation.propagate_operation(pg_pool, org, oid, adapter.provider, adapter) == "not_dispatched"
    assert state.deletes == 1
    await propagation.reconcile_operation(pg_pool, org, oid, adapter.provider, adapter)
    assert state.lookups == 1

    result = await Operations(pg_pool).get(org, oid, "provider-qa")
    step = next(step for step in result["steps"] if step["name"] == adapter.provider)
    assert step["status"] == "confirmed"
    async with pg_pool.acquire() as conn:
        event = await conn.fetchval(
            "SELECT details FROM gdpr_operation_events WHERE operation_id=$1 "
            "AND event_type='provider.reconciled' ORDER BY id DESC LIMIT 1", oid,
        )
    assert adapter.secret not in caplog.text
    assert "synthetic provider verification" not in str(event)
    assert adapter.secret not in str(event)


@pytest.mark.asyncio
async def test_concurrent_execution_dispatches_one_destructive_request(
    pg_pool, sample_org, local_provider, monkeypatch,
):
    state, url = local_provider
    state.delay = 0.1
    org = sample_org["id"]
    oid = await make_partial_operation(pg_pool, org)
    adapter = LocalSyntheticAdapter(url)
    monkeypatch.setenv("GDPR_PROVIDER_ERASURE_EXECUTION_ENABLED", "true")

    await asyncio.gather(*(
        propagation.propagate_operation(pg_pool, org, oid, adapter.provider, adapter)
        for _ in range(12)
    ))
    assert state.deletes == 1
    result = await Operations(pg_pool).get(org, oid, "provider-qa")
    step = next(step for step in result["steps"] if step["name"] == adapter.provider)
    assert step["status"] == "confirmed"


@pytest.mark.asyncio
async def test_mismatched_receipt_cannot_confirm_provider_stage(
    pg_pool, sample_org, local_provider, monkeypatch,
):
    state, url = local_provider
    state.mismatch = True
    org = sample_org["id"]
    oid = await make_partial_operation(pg_pool, org)
    adapter = LocalSyntheticAdapter(url)
    monkeypatch.setenv("GDPR_PROVIDER_ERASURE_EXECUTION_ENABLED", "true")

    await propagation.propagate_operation(pg_pool, org, oid, adapter.provider, adapter)
    result = await Operations(pg_pool).get(org, oid, "provider-qa")
    step = next(step for step in result["steps"] if step["name"] == adapter.provider)
    assert step["status"] != "confirmed"


@pytest.mark.asyncio
async def test_definitely_not_sent_can_retry_but_only_then_dispatches_once(
    pg_pool, sample_org, local_provider, monkeypatch,
):
    state, url = local_provider
    org = sample_org["id"]
    oid = await make_partial_operation(pg_pool, org)
    adapter = LocalSyntheticAdapter(url)
    monkeypatch.setenv("GDPR_PROVIDER_ERASURE_EXECUTION_ENABLED", "true")

    class RejectedBeforeSend:
        provider = adapter.provider
        idempotent_delete = True

        async def delete(self, *_args, **_kwargs):
            raise propagation.DefinitelyNotSent("synthetic local rejection")

    assert await propagation.propagate_operation(
        pg_pool, org, oid, adapter.provider, RejectedBeforeSend(),
    ) == "not_sent"
    assert state.deletes == 0
    await propagation.propagate_operation(pg_pool, org, oid, adapter.provider, adapter)
    assert state.deletes == 1
    result = await Operations(pg_pool).get(org, oid, "provider-qa")
    step = next(step for step in result["steps"] if step["name"] == adapter.provider)
    assert step["status"] == "confirmed"
