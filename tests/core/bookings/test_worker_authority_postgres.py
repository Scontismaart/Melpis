import json
from datetime import date, timedelta
from uuid import uuid4

import pytest

from src.core.bookings.worker_authority import WorkerBookingAuthority, WorkerProofDenied
from src.core.db.inbound_worker_provenance import record_committed_operation, record_receipt
from tests.core.bookings.test_conversation_state_postgres import apply, commit_source, state_env
from tests.core.test_quota_reservation_lifecycle import _inbound_operation


@pytest.mark.asyncio
async def test_private_worker_claim_requires_persisted_provenance_and_fences_retry(state_env):
    env = state_env
    org, message, pool = env["org"], env["message"], env["pool"]
    text = "2026-11-12"
    await commit_source(env, message, text, {"service_id": "standard"}, 1700000000)
    await apply(env, message)
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
        with pytest.raises(WorkerProofDenied, match="State preimage"):
            await authority.mint(conn, first, env["conv"],
                                 expected_state_revision=1, expected_catalog_revision=1)
        with pytest.raises(WorkerProofDenied, match="already held"):
            await authority.claim(conn, org, message)
        await conn.execute("""UPDATE booking_worker_claims SET lease_until=clock_timestamp()-interval '1 second'
            WHERE organization_id=$1 AND message_id=$2""", org, message)
        retry = await authority.claim(conn, org, message)
        assert retry.claim_id != first.claim_id
        assert retry.generation == first.generation + 1

@pytest.mark.asyncio
@pytest.mark.parametrize(('ticket_status', 'closed_date'), [
    ('AI_ACTIVE', False), ('PENDING_STAFF', False), ('CLAIMED', False), ('AI_ACTIVE', True),
])
async def test_private_worker_validates_confirmed_a04_proposal(state_env, ticket_status, closed_date):
    from src.core.bookings.conversation_state import render_confirmation_prompt
    from tests.core.bookings.test_a05_auto_booking import _policy

    env = state_env
    org, message, pool, conversation = env["org"], env["message"], env["pool"], env["conv"]
    appointment = (date.today() + timedelta(days=7)).isoformat()
    proposal = await commit_source(env, message, f"{appointment} 10:00", {
        "service_id": "standard", "customer_name": True, "appointment_date": appointment,
        "appointment_time": "10:00", "party_size": 2,
    }, 1700000000)
    state = (await apply(env, message))["state"]
    prompt, reply = uuid4(), uuid4()
    reply_wam = "synthetic-confirmation-" + uuid4().hex
    await pool.execute("""INSERT INTO messages
        (id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id,sent_at)
        VALUES($1,$2,$3,'outbound','text','{}','sent',$4,$5,clock_timestamp()-interval '2 minutes')""",
        prompt, org, conversation, render_confirmation_prompt(state, prompt), "synthetic-prompt-" + uuid4().hex)
    stamp = await pool.fetchval("SELECT floor(extract(epoch from clock_timestamp()-interval '1 minute'))::bigint")
    text = f"CONFIRM {prompt}"
    reply_body = {"id": reply_wam, "type": "text", "timestamp": str(stamp), "text": {"body": text}}
    await pool.execute("""INSERT INTO messages
        (id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id)
        VALUES($1,$2,$3,'inbound','text',$4::jsonb,'processing',$5,$6)""",
        reply, org, conversation, json.dumps(reply_body), text, reply_wam)
    await env["repo"].confirm(org, env["auth"], conversation, reply, prompt, request_id=uuid4(),
                              expected_revision=1, expected_catalog_revision=2)
    await pool.execute('UPDATE conversations SET ticket_status=$3 WHERE organization_id=$1 AND id=$2',
                       org, conversation, ticket_status)
    policy = _policy("internal")
    if closed_date:
        policy["a05"]["closed_dates"] = [appointment]
    await pool.execute("""UPDATE booking_settings SET config=config || $2::jsonb,capienze_orarie=$3::jsonb
        WHERE organization_id=$1""", org, json.dumps(policy), json.dumps({"10:00": 10}))
    proposal_wam = await pool.fetchval("SELECT wam_id FROM messages WHERE organization_id=$1 AND id=$2", org, message)
    async with pool.acquire() as conn, conn.transaction():
        for provider_id in (proposal_wam, reply_wam):
            inbox, lease = uuid4(), uuid4()
            payload = {"entry": [{"changes": [{"value": {"messages": [{"id": provider_id}]}}]}]}
            await conn.execute("""INSERT INTO meta_webhook_inbox
                (id,organization_id,channel,event_key,payload,trace_id,status,lease_token,lease_until)
                VALUES($1,$2,'whatsapp',$3,$4::jsonb,'synthetic','processing',$5,now()+interval '1 minute')""",
                inbox, org, provider_id, json.dumps(payload), lease)
            await record_receipt(conn, {"id": inbox, "organization_id": org, "lease_token": lease}, provider_id)
            await conn.execute("UPDATE meta_webhook_inbox SET status='completed' WHERE organization_id=$1 AND id=$2", org, inbox)
        await record_committed_operation(conn, proposal)
        authority = WorkerBookingAuthority()
        claim = await authority.claim(conn, org, message)
        if ticket_status != 'AI_ACTIVE':
            with pytest.raises(WorkerProofDenied, match='Conversation unavailable'):
                await authority.mint(conn, claim, conversation,
                                     expected_state_revision=2, expected_catalog_revision=2)
            return
        if closed_date:
            with pytest.raises(WorkerProofDenied, match='Current slot policy denied'):
                await authority.mint(conn, claim, conversation,
                                     expected_state_revision=2, expected_catalog_revision=2)
            return
        capability = await authority.mint(conn, claim, conversation,
                                          expected_state_revision=2, expected_catalog_revision=2)
        proof = await authority.revalidate(conn, capability)
        assert proof["request_id"] and proof["booking_id"]
        assert not proof["runtime_enabled"]
