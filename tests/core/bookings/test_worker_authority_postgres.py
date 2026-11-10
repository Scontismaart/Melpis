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
@pytest.mark.parametrize(('ticket_status', 'closed_date', 'entrypoint'), [
    ('AI_ACTIVE', False, False), ('PENDING_STAFF', False, False),
    ('CLAIMED', False, False), ('AI_ACTIVE', True, False), ('AI_ACTIVE', False, True),
])
async def test_private_worker_validates_confirmed_a04_proposal(state_env, private_worker_pool, ticket_status, closed_date, entrypoint, monkeypatch):
    from src.core.bookings.conversation_state import render_confirmation_prompt
    from tests.core.bookings.test_a05_auto_booking import _policy

    env = state_env
    org, message, pool, conversation = env["org"], env["message"], env["pool"], env["conv"]
    appointment = (date.today() + timedelta(days=7)).isoformat()
    proposal_wam = 'synthetic-proposal-' + uuid4().hex
    await pool.execute('UPDATE messages SET wam_id=$3 WHERE organization_id=$1 AND id=$2', org, message, proposal_wam)
    await _persist_ingress(pool, org, proposal_wam)
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    await commit_source(env, message, f"Mi chiamo Mario Rossi. {appointment} 10:00", {
        "service_id": "standard", "customer_name": True, "appointment_date": appointment,
        "appointment_time": "10:00", "party_size": 2,
    }, 1700000000, provider_id=proposal_wam)
    policy = _policy("internal")
    if closed_date:
        policy["a05"]["closed_dates"] = [appointment]
    await pool.execute("""UPDATE booking_settings SET config=config || $2::jsonb,capienze_orarie=$3::jsonb
        WHERE organization_id=$1""", org, json.dumps(policy), json.dumps({"10:00": 10}))
    if entrypoint:
        result = await env["repo"].apply_worker_source(org, conversation, message)
        assert not result["replayed"]
        assert (await env["repo"].apply_worker_source(org, conversation, message))["replayed"]
        state = result["state"]
        prompts = await pool.fetch("""SELECT id FROM messages WHERE organization_id=$1 AND conversation_id=$2
            AND handling_type='c24_confirmation_prompt' AND status='queued'""", org, conversation)
        assert len(prompts) == 1
        prompt = prompts[0]["id"]
        async with env["repo"].confirmation_prompt_delivery_guard(org, conversation, prompt) as eligible:
            assert eligible
        await pool.execute("""UPDATE messages SET status='sent',wam_id=$3,sent_at=clock_timestamp()-interval '2 minutes'
            WHERE organization_id=$1 AND id=$2""", org, prompt, "synthetic-prompt-" + uuid4().hex)
    else:
        state = (await apply(env, message))["state"]
        prompt = uuid4()
        await pool.execute("""INSERT INTO messages
            (id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id,sent_at)
            VALUES($1,$2,$3,'outbound','text','{}','sent',$4,$5,clock_timestamp()-interval '2 minutes')""",
            prompt, org, conversation, render_confirmation_prompt(state, prompt), "synthetic-prompt-" + uuid4().hex)
    reply = uuid4()
    reply_wam = "synthetic-confirmation-" + uuid4().hex
    stamp = await pool.fetchval("SELECT floor(extract(epoch from clock_timestamp()-interval '1 minute'))::bigint")
    text = f"CONFIRM {prompt}"
    reply_body = {"id": reply_wam, "type": "text", "timestamp": str(stamp), "text": {"body": text}}
    await pool.execute("""INSERT INTO messages
        (id,organization_id,conversation_id,direction,message_type,content,status,content_text,wam_id)
        VALUES($1,$2,$3,'inbound','text',$4::jsonb,'processing',$5,$6)""",
        reply, org, conversation, json.dumps(reply_body), text, reply_wam)
    await _persist_ingress(pool, org, reply_wam)
    if entrypoint:
        confirmation = await env["repo"].apply_worker_source(org, conversation, reply)
        assert confirmation["details_confirmed_current"]
        assert await pool.fetchval('''SELECT count(*) FROM booking_conversation_requests
            WHERE organization_id=$1 AND conversation_id=$2 AND actor_kind='inbound_worker' ''',
            org, conversation) == 2
    else:
        await env["repo"].confirm(org, env["auth"], conversation, reply, prompt, request_id=uuid4(),
                                  expected_revision=1, expected_catalog_revision=2)
    await pool.execute('UPDATE conversations SET ticket_status=$3 WHERE organization_id=$1 AND id=$2',
                       org, conversation, ticket_status)
    if entrypoint:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from src.core.workers.inbound_worker import InboundWorker

        authority = WorkerBookingAuthority()
        with pytest.raises(WorkerProofDenied, match='Confirmed proposal unavailable'):
            await authority.process_confirmation(private_worker_pool, uuid4(), conversation, reply)
        repo = SimpleNamespace(pool=pool, reap_stale_claims=AsyncMock(),
                               claim_inbound_messages=AsyncMock(return_value=[]))
        worker = InboundWorker(repo, None, private_booking_pool=private_worker_pool)
        await worker.process_next_batch()
        await worker.process_next_batch()
        booking = await pool.fetchval('SELECT id FROM bookings WHERE organization_id=$1 AND source_message_id=$2',
                                      org, str(message))
        assert booking is not None
        assert await pool.fetchval('''SELECT count(*) FROM booking_confirmation_outbox
            WHERE organization_id=$1 AND booking_id=$2''', org, booking) == 1
        return
    async with pool.acquire() as conn, conn.transaction():
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
        capability = await authority.mint(conn, claim, conversation,
                                          expected_state_revision=2, expected_catalog_revision=2)
        monkeypatch.delenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', raising=False)
        with pytest.raises(WorkerProofDenied, match='disabled'):
            await authority.create_booking(conn, capability)
        monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
        from src.core.db.repositories.booking_repo import BookingRepository

        async def unavailable(*_args):
            raise RuntimeError('synthetic outbox failure')

        with monkeypatch.context() as patch:
            patch.setattr(BookingRepository, 'enqueue_calendar_confirmation', unavailable)
            with pytest.raises(RuntimeError, match='synthetic outbox failure'):
                await authority.create_booking(conn, capability)
        assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM bookings WHERE organization_id=$1 AND id=$2)',
                                       org, proof['booking_id'])
        assert not await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM booking_allocation_requests
            WHERE organization_id=$1 AND booking_id=$2)''', org, proof['booking_id'])
        capability = await authority.mint(conn, claim, conversation,
                                          expected_state_revision=2, expected_catalog_revision=2)
        booking = await authority.create_booking(conn, capability)
        assert booking == proof['booking_id']
        assert await conn.fetchval('SELECT stato FROM bookings WHERE organization_id=$1 AND id=$2',
                                   org, booking) == 'confermata'
        assert await conn.fetchval('''SELECT actor_kind FROM booking_allocation_requests
            WHERE organization_id=$1 AND booking_id=$2''', org, booking) == 'inbound_worker'
        assert await conn.fetchval('''SELECT count(*) FROM booking_confirmation_outbox
            WHERE organization_id=$1 AND booking_id=$2''', org, booking) == 1
        replay = await authority.mint(conn, claim, conversation,
                                      expected_state_revision=2, expected_catalog_revision=2)
        assert await authority.create_booking(conn, replay) == booking
        assert await conn.fetchval('''SELECT count(*) FROM booking_confirmation_outbox
            WHERE organization_id=$1 AND booking_id=$2''', org, booking) == 1


async def _persist_ingress(pool, org, provider_id):
    inbox, lease = uuid4(), uuid4()
    payload = {"entry": [{"changes": [{"value": {"messages": [{"id": provider_id}]}}]}]}
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("""INSERT INTO meta_webhook_inbox
            (id,organization_id,channel,event_key,payload,trace_id,status,lease_token,lease_until)
            VALUES($1,$2,'whatsapp',$3,$4::jsonb,'synthetic','processing',$5,now()+interval '1 minute')""",
            inbox, org, provider_id, json.dumps(payload), lease)
        await record_receipt(conn, {"id": inbox, "organization_id": org, "lease_token": lease}, provider_id)
        await conn.execute("UPDATE meta_webhook_inbox SET status='completed' WHERE organization_id=$1 AND id=$2", org, inbox)
