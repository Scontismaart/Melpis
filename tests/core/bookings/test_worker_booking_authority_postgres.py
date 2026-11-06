"""Disposable PostgreSQL checks for the owner-only worker-proof draft."""
from pathlib import Path
import asyncio
from uuid import uuid4
import pytest
from tests.core.bookings.worker_booking_authority_fixture import DraftWorkerBookingAuthority, WorkerProofDenied, service_receipt_v2
from src.core.bookings.service import BookingService
from src.core.db.repositories.booking_repo import BookingRepository
from tests.core.bookings.test_conversation_state_postgres import state_env
from tests.core.bookings.test_conversation_observation_postgres import ready
from tests.core.bookings.test_a05_auto_booking import _configure
from tests.core.test_quota_reservation_lifecycle import _used

pytestmark = pytest.mark.asyncio

@pytest.fixture
async def owner_claim_schema(pg_pool):
    proposal = Path(__file__).parent / 'fixtures' / 'worker_claim_proposal.sql'
    sql = proposal.read_text(encoding='utf-8').replace('CREATE TABLE public.draft_booking_worker_claims', 'CREATE TABLE IF NOT EXISTS public.draft_booking_worker_claims')
    await pg_pool.execute(sql)
    yield

async def test_service_identity_is_stable_and_separate():
    org, source = uuid4(), uuid4()
    first = service_receipt_v2(org, source, 'create', 'a'*64)
    assert first == service_receipt_v2(org, source, 'create', 'a'*64)
    assert first[:2] == service_receipt_v2(org, source, 'create', 'b'*64)[:2]
    assert first[2] != service_receipt_v2(org, source, 'create', 'b'*64)[2]
    assert first != service_receipt_v2(uuid4(), source, 'create', 'a'*64)

async def test_private_claim_expiry_fences_previous_owner(state_env, owner_claim_schema):
    env = state_env
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn:
        async with conn.transaction():
            first = await factory.claim_for_owner_test(conn, env['org'], env['message'])
        async with conn.transaction():
            with pytest.raises(WorkerProofDenied, match='already held'):
                await factory.claim_for_owner_test(conn, env['org'], env['message'])
        await conn.execute('UPDATE draft_booking_worker_claims SET lease_until=clock_timestamp()-interval \'1 second\' WHERE organization_id=$1 AND message_id=$2', env['org'], env['message'])
        async with conn.transaction():
            second = await factory.claim_for_owner_test(conn, env['org'], env['message'])
        assert second.generation == first.generation+1 and second.claim_id != first.claim_id
        row = await conn.fetchrow('SELECT claim_id,generation FROM draft_booking_worker_claims WHERE organization_id=$1 AND message_id=$2', env['org'], env['message'])
        assert row['claim_id'] == second.claim_id and row['generation'] == second.generation
    assert not await env['pool'].fetchval("SELECT has_table_privilege('service_role','public.draft_booking_worker_claims','SELECT')")

@pytest.mark.parametrize('limit,used', [(0, 1), (150, 150)])
async def test_committed_canonical_claim_mints_dark_one_use_proof(state_env, owner_claim_schema, limit, used):
    env = state_env
    state, _ = await ready(env)
    await _configure(env['pool'], BookingService(BookingRepository(env['pool'])), env['org'])
    await env['pool'].execute("UPDATE contacts SET consent_status='granted' WHERE organization_id=$1 AND id=(SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2)", env['org'], env['conv'])
    # Synthetic accounting snapshot: the canonical A04 response was committed
    # above; this checks proof behavior, not ledger reconciliation.
    await env['pool'].execute('UPDATE organizations SET messages_limit=$2,messages_used_this_period=$3 WHERE id=$1', env['org'], limit, used)
    prior = await _used(env['pool'], env['org'])
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn:
        async with conn.transaction():
            claim = await factory.claim_for_owner_test(conn, env['org'], env['message'])
        async with conn.transaction():
            cap = await factory.mint(conn, claim, env['conv'], expected_state_revision=state.revision, expected_catalog_revision=state.catalog_revision)
            task = asyncio.create_task(factory.revalidate(conn, cap))
            with pytest.raises(WorkerProofDenied, match='scope'):
                await task
            result = await factory.revalidate(conn, cap)
            assert not result['runtime_enabled'] and not result['confirmation_authorized']
            with pytest.raises(WorkerProofDenied):
                await factory.revalidate(conn, cap)
    assert await _used(env['pool'], env['org']) == prior
    assert await env['pool'].fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', env['org']) == 0

async def test_claim_denies_foreign_tenant(state_env, owner_claim_schema):
    env = state_env
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn, conn.transaction():
        with pytest.raises(WorkerProofDenied, match='Source'):
            await factory.claim_for_owner_test(conn, uuid4(), env['message'])

async def test_proof_denies_withdrawn_consent_before_any_booking(state_env, owner_claim_schema):
    env = state_env
    state, _ = await ready(env)
    await _configure(env['pool'], BookingService(BookingRepository(env['pool'])), env['org'])
    await env['pool'].execute("UPDATE contacts SET consent_status='withdrawn' WHERE organization_id=$1 AND id=(SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2)", env['org'], env['conv'])
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn:
        async with conn.transaction():
            claim = await factory.claim_for_owner_test(conn, env['org'], env['message'])
        async with conn.transaction():
            with pytest.raises(WorkerProofDenied):
                await factory.mint(conn, claim, env['conv'], expected_state_revision=state.revision, expected_catalog_revision=state.catalog_revision)
    assert await env['pool'].fetchval('SELECT count(*) FROM bookings WHERE organization_id=$1', env['org']) == 0

@pytest.mark.parametrize('billing_update', ["ai_accounting_blocked=true", "trial_end=now()-interval '1 second'"])
async def test_current_billing_fail_closed(state_env, owner_claim_schema, billing_update):
    env = state_env
    state, _ = await ready(env)
    await _configure(env['pool'], BookingService(BookingRepository(env['pool'])), env['org'])
    await env['pool'].execute(f'UPDATE organizations SET {billing_update} WHERE id=$1', env['org'])
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn:
        async with conn.transaction():
            claim = await factory.claim_for_owner_test(conn, env['org'], env['message'])
        async with conn.transaction():
            with pytest.raises(WorkerProofDenied, match='billing'):
                await factory.mint(conn, claim, env['conv'], expected_state_revision=state.revision, expected_catalog_revision=state.catalog_revision)

async def test_marketing_opt_out_does_not_disable_service_proof(state_env, owner_claim_schema):
    env = state_env
    state, _ = await ready(env)
    await _configure(env['pool'], BookingService(BookingRepository(env['pool'])), env['org'])
    await env['pool'].execute('UPDATE contacts SET marketing_opt_out=true WHERE organization_id=$1 AND id=(SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2)', env['org'], env['conv'])
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn:
        async with conn.transaction():
            claim = await factory.claim_for_owner_test(conn, env['org'], env['message'])
        async with conn.transaction():
            cap = await factory.mint(conn, claim, env['conv'], expected_state_revision=state.revision, expected_catalog_revision=state.catalog_revision)
            assert not (await factory.revalidate(conn, cap))['confirmation_authorized']

async def test_bad_claim_token_and_generation_denied(state_env, owner_claim_schema):
    from dataclasses import replace
    env = state_env
    state, _ = await ready(env)
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn:
        async with conn.transaction():
            claim = await factory.claim_for_owner_test(conn, env['org'], env['message'])
        for bad in (replace(claim, claim_id=uuid4()), replace(claim, generation=claim.generation+1)):
            async with conn.transaction():
                with pytest.raises(WorkerProofDenied, match='claim'):
                    await factory.mint(conn, bad, env['conv'], expected_state_revision=state.revision, expected_catalog_revision=state.catalog_revision)

async def test_canonical_receipt_mismatch_denied(state_env, owner_claim_schema):
    from src.core.db.repositories.booking_conversation_repo import ConversationStateUnavailable
    env = state_env
    state, op = await ready(env)
    await _configure(env['pool'], BookingService(BookingRepository(env['pool'])), env['org'])
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn:
        async with conn.transaction():
            claim = await factory.claim_for_owner_test(conn, env['org'], env['message'])
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute('UPDATE simulation_requests SET claim_token=$4 WHERE organization_id=$1 AND auth_user_id=$2 AND request_id=$3', env['org'], op.actor_id, op.id, uuid4())
            with pytest.raises(ConversationStateUnavailable):
                await factory.mint(conn, claim, env['conv'], expected_state_revision=state.revision, expected_catalog_revision=state.catalog_revision)
        finally:
            await tx.rollback()

async def test_canonical_response_hash_mismatch_denied(state_env, owner_claim_schema):
    from src.core.db.repositories.booking_conversation_repo import ConversationStateUnavailable
    env = state_env
    state, _ = await ready(env)
    await _configure(env['pool'], BookingService(BookingRepository(env['pool'])), env['org'])
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn:
        async with conn.transaction():
            claim = await factory.claim_for_owner_test(conn, env['org'], env['message'])
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("UPDATE messages SET ai_reply_cache=jsonb_set(ai_reply_cache,'{risposta}',to_jsonb('tampered'::text)) WHERE organization_id=$1 AND id=$2", env['org'], env['message'])
            with pytest.raises(ConversationStateUnavailable):
                await factory.mint(conn, claim, env['conv'], expected_state_revision=state.revision, expected_catalog_revision=state.catalog_revision)
        finally:
            await tx.rollback()

async def test_capability_does_not_cross_transactions(state_env, owner_claim_schema):
    env = state_env
    state, _ = await ready(env)
    await _configure(env['pool'], BookingService(BookingRepository(env['pool'])), env['org'])
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn:
        async with conn.transaction():
            claim = await factory.claim_for_owner_test(conn, env['org'], env['message'])
        async with conn.transaction():
            cap = await factory.mint(conn, claim, env['conv'], expected_state_revision=state.revision, expected_catalog_revision=state.catalog_revision)
        async with conn.transaction():
            with pytest.raises(WorkerProofDenied, match='scope'):
                await factory.revalidate(conn, cap)

async def test_equal_timestamp_latest_inbound_uses_uuid_tiebreaker(state_env, owner_claim_schema):
    from uuid import UUID
    env = state_env
    state, _ = await ready(env)
    await _configure(env['pool'], BookingService(BookingRepository(env['pool'])), env['org'])
    source = UUID(str(env['message']))
    assert 0 < source.int < (1 << 128) - 1  # Fixture creates UUIDv4 sources.
    factory = DraftWorkerBookingAuthority()
    async with env['pool'].acquire() as conn:
        async with conn.transaction():
            claim = await factory.claim_for_owner_test(conn, env['org'], source)
        for delta in (-1, 1):
            tx = conn.transaction()
            await tx.start()
            try:
                other = UUID(int=source.int + delta)
                await conn.execute('''INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,content_text,status,handling_type,created_at)
                    SELECT $3,organization_id,conversation_id,'inbound','text','{}'::jsonb,'Synthetic timestamp peer','handled','manual',created_at
                    FROM messages WHERE organization_id=$1 AND id=$2''', env['org'], source, other)
                if delta < 0:
                    capability = await factory.mint(conn, claim, env['conv'], expected_state_revision=state.revision, expected_catalog_revision=state.catalog_revision)
                    assert not (await factory.revalidate(conn, capability))['confirmation_authorized']
                else:
                    with pytest.raises(WorkerProofDenied, match='no longer current'):
                        await factory.mint(conn, claim, env['conv'], expected_state_revision=state.revision, expected_catalog_revision=state.catalog_revision)
            finally:
                await tx.rollback()
