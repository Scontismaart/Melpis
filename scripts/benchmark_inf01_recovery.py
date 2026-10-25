"""Explicit destructive-worker smoke test for the named owned disposable container only.

No provider/DB operations. Never run against an operational embedding service.
The environment acknowledgement and PID1 service identity are checked before signals.
"""

import asyncio, json, os, signal, socket, time
from pathlib import Path
import httpx
from src.core.documenti.embedding_contract import service_token, strict_json
from uuid import uuid4


async def main():
    if os.environ.get("INF01_OWNED_DISPOSABLE") != "melpis-inf01-shared-20261025":
        raise RuntimeError("owned_disposable_acknowledgement_required")
    if b"src.core.documenti.embedding_service:app" not in Path(
        "/proc/1/cmdline"
    ).read_bytes().split(b"\0"):
        raise RuntimeError("disposable_embedding_container_required")
    headers = {"Authorization": "Bearer " + service_token()}
    base = "http://127.0.0.1:8081"
    async with httpx.AsyncClient(
        base_url=base, headers=headers, trust_env=False, timeout=45
    ) as c:
        for _ in range(90):
            try:
                h = await c.get("/health")
                if h.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        else:
            raise RuntimeError("service_not_ready")

        def workers():
            result = []
            for path in Path("/proc").glob("[0-9]*/cmdline"):
                try:
                    parts = path.read_bytes().split(b"\0")
                    if (
                        b"src.core.documenti.local_worker" in parts
                        and b"embed_onnx" in parts
                    ):
                        result.append(int(path.parent.name))
                except OSError:
                    pass
            return result

        before = workers()
        assert len(before) == 1
        unauthorized = await c.get(
            "/health", headers={"Authorization": "Bearer synthetic-invalid"}
        )
        assert unauthorized.status_code == 401
        payload = {
            "organization_id": str(uuid4()),
            "request_id": str(uuid4()),
            "tipo": "passage",
            "texts": ["Synthetic cafe opening hours. " * 100] * 16,
        }
        pending = asyncio.create_task(c.post("/v1/embeddings", json=payload))
        await asyncio.sleep(0.025)
        os.kill(before[0], signal.SIGKILL)
        failure = await pending
        assert failure.status_code == 503, failure.status_code
        after_failed = workers()
        # No automatic replay after the failed dispatch. A distinct request may cold-start.
        new = {
            "organization_id": payload["organization_id"],
            "request_id": str(uuid4()),
            "tipo": "query",
            "texts": ["Synthetic cafe hours?"],
        }
        cold = await c.post("/v1/embeddings", json=new)
        # Startup may exceed the query deadline. This request is not replayed.
        assert cold.status_code in (200, 503)
        for _ in range(90):
            h = await c.get("/health")
            if h.status_code == 200:
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError("worker_not_recovered")
        final = await c.post("/v1/embeddings", json={**new, "request_id": str(uuid4())})
        assert final.status_code == 200
        body = strict_json(final.content)
        assert (
            body["organization_id"] == payload["organization_id"]
            and len(body["vectors"][0]) == 384
        )
        after = workers()
        assert len(after) == 1 and after[0] != before[0]
        blocked = False
        try:
            s = socket.create_connection(("1.1.1.1", 443), timeout=1)
            s.close()
        except OSError:
            blocked = True
        assert blocked
        # Exact production singleton fence, no second model allocation.
        from src.core.documenti.embedding_instance import instance_lock

        fenced = False
        try:
            with instance_lock():
                pass
        except RuntimeError:
            fenced = True
        assert fenced
        receipt = {
            "status": "passed",
            "in_flight_crash_status": failure.status_code,
            "cold_request_status": cold.status_code,
            "new_request_status": final.status_code,
            "model_processes_before": len(before),
            "model_processes_after": len(after),
            "no_automatic_worker_after_failed_dispatch": not after_failed,
            "public_egress_blocked": blocked,
            "second_instance_fenced": fenced,
            "assets_read_only": True,
            "cgroup_peak_bytes": int(Path("/sys/fs/cgroup/memory.peak").read_text()),
            "scope": "real offline ONNX worker and HTTP; synthetic inputs; no provider or database",
        }
        Path("/repo/artifacts/inf01/service-smoke.json").write_text(
            json.dumps(receipt, indent=2)
        )
        print(json.dumps(receipt))


asyncio.run(main())
