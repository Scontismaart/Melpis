import json
from uuid import uuid4

import pytest

from src.core.bookings.worker_authority import WorkerBookingAuthority, WorkerProofDenied
from src.core.db.inbound_worker_provenance import record_committed_operation, record_receipt
from tests.core.bookings.test_conversation_state_postgres import commit_source, state_env
from tests.core.test_quota_reservation_lifecycle import _inbound_operation


@pytest.mark.asyncio
async def test_private_worker_claim_requires_persisted_provenance_and_fences_retry(state_env):
    env = state_env
    org, message, pool = env["org"], env["message"], env["pool"]
    text = "2026-11-12"
    await commit_source(env, message, text, {"service_id": "standard"}, 1700000000)
    authority = WorkerBookingAuthority()
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(WorkerProofDenied, match="provenance"):
            await authority.claim(conn, org, message)
        with pytest.raises(WorkerProofDenied, match="Organization"):
            await authority.claim(conn, uuid4(), message)

    provider = await pool.fetchval("SELECT wam_id FROM messages WHERE organization_id=$1 AND id=$2", org, message)
    inbox, lease = uuid4(), uuid4()
    payload = {"entry": [{"changes": [{"value": {"messages": [{"id": provider}]}}]}]}
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("""INSERT INTO meta_webhook_inbox
            (id,organization_id,channel,event_key,payload,trace_id,status,lease_token,lease_until)
            VALUES($1,$2,'whatsapp',$3,$4::jsonb,'synthetic','processing',$5,now()+interval '1 minute')""",
            inbox, org, provider, json.dumps(payload), lease)
        await record_receipt(conn, {"id": inbox, "organization_id": org, "lease_token": lease}, provider)
        await conn.execute("UPDATE meta_webhook_inbox SET status='completed' WHERE organization_id=$1 AND id=$2", org, inbox)
        await record_committed_operation(conn, _inbound_operation(org, message, text=text))
        first = await authority.claim(conn, org, message)
        with pytest.raises(WorkerProofDenied, match="already held"):
            await authority.claim(conn, org, message)
        await conn.execute("""UPDATE booking_worker_claims SET lease_until=clock_timestamp()-interval '1 second'
            WHERE organization_id=$1 AND message_id=$2""", org, message)
        retry = await authority.claim(conn, org, message)
        assert retry.claim_id != first.claim_id
        assert retry.generation == first.generation + 1
