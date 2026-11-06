"""Read-only C2.4 allocation observations over committed conversation evidence."""
import asyncio
import json
from uuid import uuid4

import pytest

from src.core.bookings.conversation_state import StateConflict, state_digest
from src.core.db.repositories.booking_conversation_repo import ConversationStateDenied, ConversationStateUnavailable
from tests.core.bookings.test_conversation_state_postgres import state_env, commit_source, apply
from tests.core.test_quota_reservation_lifecycle import _auth_member, _inbound_message, _organization, _used

pytestmark = pytest.mark.asyncio
DENIED = (PermissionError, StateConflict, ConversationStateDenied, ConversationStateUnavailable)


async def ready(env):
    op = await commit_source(env, env['message'], '2026-11-12 10:00', {
        'service_id': 'standard', 'customer_name': True,
        'appointment_date': '2026-11-12', 'appointment_time': '10:00', 'party_size': 2,
    }, 1700000000)
    state = (await apply(env, env['message']))['state']
    assert not state.missing_fields and not state.ambiguous_fields and not state.handoff_reason
    return state, op


async def observe(env, state, **overrides):
    args = {'expected_state_revision': state.revision,
            'expected_catalog_revision': state.catalog_revision,
            'expected_state_digest': state_digest(state)}
    args.update(overrides)
    return await env['repo'].observe_candidate(env['org'], env['auth'], env['conv'], **args)


async def snapshot(env):
    pool = env['pool']
    tables = ('bookings', 'booking_allocation_sets', 'booking_resource_allocations',
              'booking_allocation_requests', 'booking_conversation_states',
              'booking_conversation_requests', 'booking_confirmation_outbox')
    rows = {}
    for table in tables:
        rows[table] = await pool.fetchval(
            f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb)::text FROM {table} t WHERE organization_id=$1",
            env['org'])
    rows['usage'] = await _used(pool, env['org'])
    return rows


async def test_no_opt_in_observation_is_unknown_read_only_and_repeatable(state_env):
    env = state_env
    state, _ = await ready(env)
    before = await snapshot(env)
    first = await observe(env, state)
    second = await observe(env, state)
    assert first == second
    assert first['allocation_observation'] in {'available', 'unavailable', 'unknown'}
    assert first['opt_in_authorized'] is False
    assert first['reason'] == 'opt_in_missing'
    assert first['operational_availability'] == 'unknown'
    assert first['runtime_enabled'] is False and first['confirmation_authorized'] is False
    assert first['state_revision'] == state.revision
    assert first['state_digest'] == state_digest(state)
    assert first['catalog_revision'] == state.catalog_revision
    assert await snapshot(env) == before


@pytest.mark.parametrize('field,value', [
    ('expected_state_revision', 999), ('expected_catalog_revision', 999),
    ('expected_state_digest', '0' * 64),
])
async def test_stale_expectations_cannot_observe(state_env, field, value):
    env = state_env
    state, _ = await ready(env)
    before = await snapshot(env)
    with pytest.raises(DENIED):
        await observe(env, state, **{field: value})
    assert await snapshot(env) == before


async def test_foreign_tenant_and_worker_cannot_observe(state_env):
    env = state_env
    state, op = await ready(env)
    foreign = await _organization(env['pool'])
    foreign_auth = await _auth_member(env['pool'], foreign['id'], role='owner')
    kwargs = dict(expected_state_revision=state.revision,
                  expected_catalog_revision=state.catalog_revision,
                  expected_state_digest=state_digest(state))
    before = await snapshot(env)
    with pytest.raises(PermissionError):
        await env['repo'].observe_candidate(foreign['id'], foreign_auth, env['conv'], **kwargs)
    with pytest.raises(PermissionError):
        await env['repo'].observe_candidate(env['org'], op.actor_id, env['conv'], **kwargs)
    assert await snapshot(env) == before


@pytest.mark.parametrize('tamper', ['deleted', 'source', 'canonical', 'consent'])
async def test_revoked_or_tampered_evidence_denies_observation(state_env, tamper):
    env = state_env
    state, _ = await ready(env)
    pool = env['pool']; org = env['org']; message = env['message']
    if tamper == 'deleted':
        await pool.execute('UPDATE messages SET deleted_at=now() WHERE organization_id=$1 AND id=$2', org, message)
    elif tamper == 'source':
        await pool.execute("UPDATE messages SET content_text='changed' WHERE organization_id=$1 AND id=$2", org, message)
    elif tamper == 'canonical':
        await pool.execute("UPDATE messages SET ai_reply_cache=jsonb_set(ai_reply_cache,'{booking_state_proposal,intent}', '\"cancel\"'::jsonb) WHERE organization_id=$1 AND id=$2", org, message)
    else:
        await pool.execute("UPDATE contacts SET consent_status='withdrawn' WHERE organization_id=$1 AND id=(SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2)", org, env['conv'])
    before = await snapshot(env)
    with pytest.raises(DENIED):
        await observe(env, state)
    assert await snapshot(env) == before


async def test_concurrent_observations_are_read_only(state_env):
    env = state_env
    state, _ = await ready(env)
    before = await snapshot(env)
    results = await asyncio.gather(*(observe(env, state) for _ in range(8)))
    assert all(result == results[0] for result in results)
    assert results[0]['allocation_observation'] in {'available', 'unavailable', 'unknown'}
    assert results[0]['opt_in_authorized'] is False
    assert await snapshot(env) == before


async def test_incomplete_state_cannot_observe(state_env):
    env = state_env
    await commit_source(env, env['message'], '2026-11-12',
                        {'service_id': 'standard', 'appointment_date': '2026-11-12'}, 1700000000)
    state = (await apply(env, env['message']))['state']
    assert state.missing_fields
    before = await snapshot(env)
    result = await observe(env, state)
    assert result['allocation_observation'] == 'unknown'
    assert result['operational_availability'] == 'unknown'
    assert result['confirmation_authorized'] is False
    assert await snapshot(env) == before


async def test_all_current_slot_sources_require_live_canonical_evidence(state_env):
    env = state_env
    original, _ = await ready(env)
    from tests.core.bookings.test_conversation_state_postgres import second_source
    later = await second_source(env, 1700000001, '11:00')
    state = (await apply(env, later, revision=original.revision))['state']
    assert len({slot.source_message_id for slot in state.slots}) == 2
    await env['pool'].execute(
        "UPDATE messages SET ai_reply_cache=jsonb_set(ai_reply_cache,'{booking_state_proposal,intent}', '\"cancel\"'::jsonb) WHERE organization_id=$1 AND id=$2",
        env['org'], env['message'])
    before = await snapshot(env)
    with pytest.raises(DENIED):
        await observe(env, state)
    assert await snapshot(env) == before

async def test_existing_allocation_counts_for_new_candidate_without_exclusion(state_env):
    from tests.core.bookings.test_allocation_engine_postgres import Environment, CATALOGS
    env = state_env
    state, _ = await ready(env)
    allocation = Environment(env['pool'], env['org'], env['auth'], env['actor'], CATALOGS['ristorante'])
    allocation.revision = state.catalog_revision
    booking = await allocation.booking()
    await env['pool'].execute("UPDATE bookings SET data='2026-11-12',ora='10:00',coperti=12 WHERE organization_id=$1 AND id=$2", env['org'], booking)
    request = allocation.request(booking, appointment_date='2026-11-12', appointment_time='10:00', party_size=12)
    assert (await allocation.apply(request))['outcome'] == 'allocated'
    assert await env['pool'].fetchval('SELECT count(*) FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env['org'], booking) == 1
    before = await snapshot(env)
    observed = await observe(env, state)
    assert observed['allocation_observation'] == 'unavailable'
    assert observed['operational_availability'] == 'unknown'
    assert observed['confirmation_authorized'] is False and observed['runtime_enabled'] is False
    assert await snapshot(env) == before


async def test_valid_a05_opt_in_does_not_authorize_observation(state_env):
    from src.core.bookings.service import BookingService
    from src.core.db.repositories.booking_repo import BookingRepository
    from tests.core.bookings.test_a05_auto_booking import _configure
    env = state_env
    state, _ = await ready(env)
    await _configure(env['pool'], BookingService(BookingRepository(env['pool'])), env['org'])
    before = await snapshot(env)
    result = await observe(env, state)
    assert result['opt_in_authorized'] is True
    assert result['authority'] == 'internal'
    assert result['operational_availability'] == 'unknown'
    assert result['runtime_enabled'] is False and result['confirmation_authorized'] is False
    assert await snapshot(env) == before


@pytest.mark.parametrize('timestamp', ['1700000001', '1700000000', 'malformed', None])
async def test_unprocessed_message_with_uncertain_order_makes_observation_unknown(state_env, timestamp):
    env = state_env
    state, _ = await ready(env)
    source = uuid4(); wam = f'synthetic-{uuid4()}'
    body = {'id': wam, 'timestamp': timestamp, 'type': 'text', 'text': {'body': '11:00'}}
    await env['pool'].execute("""INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,content_text,wam_id,status)
        VALUES($1,$2,$3,'inbound','text',$4::jsonb,'11:00',$5,'processing')""",
        source, env['org'], env['conv'], json.dumps(body), wam)
    before = await snapshot(env)
    result = await observe(env, state)
    assert result['allocation_observation'] == 'unknown'
    assert result['reason'] == 'source_order_uncertain'
    assert result['operational_availability'] == 'unknown'
    assert await snapshot(env) == before

@pytest.mark.parametrize('mutation', ['invalid_duration', 'invalid_capacity'])
async def test_enabled_malformed_a05_policy_never_authorizes(state_env, mutation):
    from src.core.bookings.service import BookingService
    from src.core.db.repositories.booking_repo import BookingRepository
    from tests.core.bookings.test_a05_auto_booking import _configure
    env = state_env
    state, _ = await ready(env)
    await _configure(env['pool'], BookingService(BookingRepository(env['pool'])), env['org'])
    if mutation == 'invalid_duration':
        await env['pool'].execute("UPDATE booking_settings SET config=jsonb_set(config,'{a05,duration_minutes}','30'::jsonb) WHERE organization_id=$1", env['org'])
    else:
        await env['pool'].execute("UPDATE booking_settings SET capienze_orarie='{}'::jsonb WHERE organization_id=$1", env['org'])
    settings = await env['pool'].fetchrow('SELECT config,capienze_orarie FROM booking_settings WHERE organization_id=$1', env['org'])
    assert isinstance(settings['config'], str) and isinstance(settings['capienze_orarie'], str)
    before = await snapshot(env)
    result = await observe(env, state)
    assert result['opt_in_authorized'] is False
    assert result['reason'] == 'opt_in_missing'
    assert result['operational_availability'] == 'unknown'
    assert result['runtime_enabled'] is False and result['confirmation_authorized'] is False
    assert await snapshot(env) == before
