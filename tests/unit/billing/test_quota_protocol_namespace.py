"""Reserved quota protocol events cannot enter through generic billing APIs."""

import uuid
from contextlib import asynccontextmanager

import pytest

from src.core.db.repositories.billing_repo import BillingRepository


class _Pool:
    def __init__(self, conn=None):
        self.conn = conn
        self.acquire_count = 0

    @asynccontextmanager
    async def acquire(self):
        self.acquire_count += 1
        yield self.conn


class _DrainConn:
    def __init__(self, event_type):
        self.event_type = event_type
        self.executed = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetch(self, query, *args):
        if "SELECT DISTINCT organization_id" in query:
            return [{"organization_id": uuid.uuid4()}]
        return [{
            "id": uuid.uuid4(),
            "organization_id": args[0],
            "event_kind": "usage",
            "payload": {"event_type": self.event_type, "quantity": 1, "metadata": {}},
        }]

    async def execute(self, query, *args):
        self.executed.append(query)


class _UsageConn(_DrainConn):
    def __init__(self):
        super().__init__(None)
        self.inserted = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, query, *args):
        self.inserted.append((query, args))
        return {"event_type": args[2], "quantity": args[3]}

    async def execute(self, query, *args):
        self.inserted.append((query, args))


@pytest.mark.parametrize("event_type", [
    "system.quota_trust_epoch", "legacy_quota_admission", "system.quota_reservation",
])
@pytest.mark.parametrize("ingress", ["record_usage", "record_usage_batch", "enqueue_governance", "drain_governance"])
@pytest.mark.asyncio
async def test_reserved_quota_protocol_events_are_rejected_before_usage_insert(event_type, ingress):
    conn = _DrainConn(event_type)
    pool = _Pool(conn)
    repo = BillingRepository(pool)
    org_id = uuid.uuid4()

    with pytest.raises(ValueError, match="Reserved quota protocol event type"):
        if ingress == "record_usage":
            await repo.record_usage(org_id, event_type, quantity=0)
        elif ingress == "record_usage_batch":
            await repo.record_usage_batch(org_id, [{"event_type": event_type, "quantity": 1}])
        elif ingress == "enqueue_governance":
            await repo.enqueue_governance(
                org_id, "usage", {"event_type": event_type, "quantity": 1, "metadata": {}}
            )
        else:
            await repo.drain_governance_outbox(limit=10)

    assert not any("INSERT INTO usage_events" in query for query in conn.executed)
    assert not any("DELETE FROM governance_outbox" in query for query in conn.executed)


@pytest.mark.asyncio
async def test_regular_usage_events_still_pass_through_generic_apis():
    conn = _UsageConn()
    repo = BillingRepository(_Pool(conn))
    org_id = uuid.uuid4()

    recorded = await repo.record_usage(org_id, "ai_response", quantity=1, metadata={"model": "test"})
    await repo.record_usage_batch(org_id, [{"event_type": "ai_tokens", "quantity": 42}])

    assert recorded["event_type"] == "ai_response"
    assert len(conn.inserted) == 2
    assert all("INSERT INTO usage_events" in query for query, _ in conn.inserted)
