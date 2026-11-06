import json
from uuid import uuid4

import pytest

from src.core.billing.quota_reservations import QuotaReservations
from src.core.db.inbound_worker_provenance import record_committed_operation, record_receipt
from tests.core.test_quota_reservation_lifecycle import _final, _inbound_message, _operation, _organization


@pytest.mark.asyncio
async def test_provenance_requires_valid_lease_and_real_a04_commit(pg_pool):
    org = (await _organization(pg_pool))['id']
    message = await _inbound_message(pg_pool, org)
    inbox, lease = uuid4(), uuid4()
    provider_id = 'synthetic-inbound-' + uuid4().hex
    payload = {'entry': [{'changes': [{'value': {'messages': [{'id': provider_id}]}}]}]}
    await pg_pool.execute('UPDATE messages SET wam_id=$1 WHERE organization_id=$2 AND id=$3', provider_id, org, message)
    await pg_pool.execute("""INSERT INTO meta_webhook_inbox
        (id,organization_id,channel,event_key,payload,trace_id,status,lease_token,lease_until)
        VALUES($1,$2,'whatsapp',$3,$4::jsonb,'synthetic','processing',$5,now()+interval '1 minute')""",
        inbox, org, provider_id, json.dumps(payload), lease)
    row = {'id': inbox, 'organization_id': org, 'lease_token': lease}
    operation = _operation(org, source_id=message, channel='inbound', actor_id=None)
    async with pg_pool.acquire() as conn, conn.transaction():
        await record_receipt(conn, {**row, 'lease_token': uuid4()}, provider_id)
        assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM inbound_worker_receipts WHERE organization_id=$1)', org)
        await record_receipt(conn, row, provider_id)
        await record_receipt(conn, row, provider_id)
        assert await conn.fetchval('SELECT count(*) FROM inbound_worker_receipts WHERE organization_id=$1', org) == 1
        await conn.execute("UPDATE meta_webhook_inbox SET status='completed' WHERE organization_id=$1 AND id=$2", org, inbox)
        await record_committed_operation(conn, operation)
        assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM inbound_worker_operations WHERE organization_id=$1)', org)

    quota = QuotaReservations(pg_pool)
    reserved = await quota.reserve(operation)
    assert reserved['status'] == 'reserved'
    await quota.commit(operation, reserved['ticket'], _final(operation, reserved['ticket']))
    async with pg_pool.acquire() as conn, conn.transaction():
        await record_committed_operation(conn, operation)
        await record_committed_operation(conn, operation)
        assert await conn.fetchval('SELECT count(*) FROM inbound_worker_operations WHERE organization_id=$1 AND operation_id=$2', org, operation.id) == 1
