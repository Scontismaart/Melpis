from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.db.inbound_worker_provenance import record_receipt, record_committed_operation
from src.core.billing.quota_reservations import QuotaOperation


def connection(*, in_transaction=True):
    return SimpleNamespace(is_in_transaction=lambda: in_transaction, execute=AsyncMock(),
                           fetchrow=AsyncMock(return_value=None), fetch=AsyncMock(return_value=[]))


@pytest.mark.asyncio
async def test_receipt_requires_live_lease_tenant_inbound_and_payload_membership():
    conn = connection()
    row = {"id": uuid4(), "organization_id": uuid4(), "lease_token": uuid4()}
    await record_receipt(conn, row, "wamid.example")
    sql, *args = conn.fetchrow.await_args.args
    assert "i.organization_id=$2" in sql and "m.organization_id=i.organization_id" in sql
    assert "i.lease_token=$3" in sql and "i.status='processing'" in sql
    assert "i.lease_until > clock_timestamp()" in sql and "FOR SHARE OF i, m" in sql
    assert "m.direction='inbound'" in sql and "jsonb_path_exists" in sql
    assert args == [row['id'], row['organization_id'], row['lease_token'], 'wamid.example']
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_operation_evidence_requires_committed_inbound_ledger_and_receipt():
    conn = connection()
    op = QuotaOperation(uuid4(), uuid4(), "inbound", "a" * 64)
    await record_committed_operation(conn, op)
    sql, *args = conn.fetch.await_args.args
    assert "i.status='completed'" in sql and "e.metadata->>'state'='committed'" in sql
    assert "e.metadata->>'channel'='inbound'" in sql and "m.organization_id=r.organization_id" in sql
    assert "FOR SHARE OF r, i, m, e" in sql
    assert args == [op.organization_id, op.source_id, op.id]
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_forged_operation_rejected_before_database_access():
    conn = connection()
    with pytest.raises(ValueError):
        await record_committed_operation(conn, SimpleNamespace(organization_id=uuid4(), source_id=uuid4(), id=uuid4()))
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_both_writes_require_transaction():
    conn = connection(in_transaction=False)
    row = {"id": uuid4(), "organization_id": uuid4(), "lease_token": uuid4()}
    op = QuotaOperation(uuid4(), uuid4(), "inbound", "a" * 64)
    with pytest.raises(RuntimeError, match="transaction"):
        await record_receipt(conn, row, "wamid.example")
    with pytest.raises(RuntimeError, match="transaction"):
        await record_committed_operation(conn, op)
    conn.fetchrow.assert_not_awaited()
    conn.fetch.assert_not_awaited()
    conn.execute.assert_not_awaited()
