"""C2.4 privacy lifecycle integration tests against isolated PostgreSQL."""
import asyncio
from uuid import uuid4

import asyncpg
import pytest
from cryptography.fernet import Fernet

from src.core.gdpr.deletion import Deletion
from src.core.gdpr.catalog import CATEGORIES
from src.core.gdpr.exporter import Exporter
from tests.core.gdpr.test_exports_a06 import export_bytes
from src.core.retention_job import run_retention
from tests.core.bookings.test_conversation_state_postgres import (
    state_env, commit_source, apply, second_source,
)
from tests.core.gdpr.test_deletion_a06 import approve_policies
from tests.core.test_quota_reservation_lifecycle import _used, _organization, _auth_member

pytestmark = [pytest.mark.asyncio, pytest.mark.migration_limit(70)]


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode('ascii'))


async def populated(env):
    request = uuid4()
    await commit_source(env, env['message'], '2026-11-12',
                        {'service_id': 'standard', 'appointment_date': '2026-11-12'}, 1700000000)
    await apply(env, env['message'], request=request)
    return request


async def test_historical_source_without_original_subject_cannot_be_adopted(state_env):
    env = state_env
    await populated(env)
    # Synthetic pre-migration evidence: only the test database administrator can
    # remove a provenance value; the application cannot backfill it itself.
    async with env['pool'].acquire() as conn, conn.transaction():
        await conn.execute('ALTER TABLE messages DISABLE TRIGGER booking_message_original_subject')
        await conn.execute('UPDATE messages SET c24_original_subject_id=NULL WHERE organization_id=$1 AND id=$2', env['org'], env['message'])
        await conn.execute('ALTER TABLE messages ENABLE TRIGGER booking_message_original_subject')
    with pytest.raises(PermissionError):
        await apply(env, env['message'])
    assert await _used(env['pool'], env['org']) == 1


async def test_contact_erasure_covers_canonical_copy_after_source_hard_delete(state_env, monkeypatch):
    env = state_env
    await populated(env)
    contact = await subject(env)
    await env['pool'].execute('DELETE FROM messages WHERE organization_id=$1 AND id=$2', env['org'], env['message'])
    assert await counts(env) == (0, 0)
    assert await env['pool'].fetchval('SELECT count(*) FROM simulation_requests WHERE organization_id=$1 AND c24_subject_id=$2', env['org'], contact) == 1
    await approve_policies(env['pool'], env['org'])
    monkeypatch.setenv('GDPR_ERASURE_EXECUTION_ENABLED', 'true')
    blocked = await Deletion(env['pool']).request(env['org'], 'synthetic-c24', uuid4(), scope='contact', subject_id=contact)
    assert 'policy_required.booking_conversation_content' in blocked['manifest']['blockers']
    await approve_conversation(env['pool'], env['org'])
    result = await Deletion(env['pool']).request(env['org'], 'synthetic-c24', uuid4(), scope='contact', subject_id=contact)
    assert result['status'] == 'partial'
    assert await env['pool'].fetchval('SELECT count(*) FROM simulation_requests WHERE organization_id=$1 AND c24_subject_id=$2', env['org'], contact) == 0
    assert await _used(env['pool'], env['org']) == 1


async def test_expiring_only_simulation_proposal_invalidates_state(state_env, monkeypatch):
    from src.core.retention_job import run_retention
    env = state_env
    await populated(env)
    await approve_conversation(env['pool'], env['org'])
    monkeypatch.setenv('GDPR_RETENTION_EXECUTION_ENABLED', 'true')
    await env['pool'].execute("UPDATE simulation_requests SET completed_at=NOW()-interval '40 days' WHERE organization_id=$1", env['org'])
    await run_retention(env['pool'])
    assert await counts(env) == (0, 0)
    assert await env['pool'].fetchval('SELECT ai_reply_cache IS NOT NULL FROM messages WHERE organization_id=$1 AND id=$2', env['org'], env['message'])
    with pytest.raises(PermissionError):
        await apply(env, env['message'])
    assert await _used(env['pool'], env['org']) == 1


async def test_service_trigger_cannot_be_used_as_arbitrary_delete_capability(state_env):
    env = state_env
    await populated(env)
    async with env['pool'].acquire() as conn, conn.transaction():
        # Model only the backend's pre-existing parent writer permission.
        # No new table DELETE/INSERT or function EXECUTE permission is granted.
        await conn.execute('GRANT SELECT,UPDATE ON messages,conversations,contacts TO service_role')
        await conn.execute('CREATE POLICY c24_fixture_parent_messages ON messages TO service_role USING(true) WITH CHECK(true)')
        await conn.execute('CREATE POLICY c24_fixture_parent_conversations ON conversations TO service_role USING(true) WITH CHECK(true)')
        await conn.execute('SET LOCAL ROLE service_role')
        assert not await conn.fetchval('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')
        assert not await conn.fetchval("SELECT has_table_privilege(current_user,'booking_conversation_states','DELETE')")
        assert not await conn.fetchval("SELECT has_table_privilege(current_user,'booking_conversation_requests','DELETE')")
        assert not await conn.fetchval("SELECT has_table_privilege(current_user,'booking_conversation_revocations','INSERT')")
        assert not await conn.fetchval("SELECT has_function_privilege(current_user,'booking_state_parent_lifecycle()','EXECUTE')")
        changed = await conn.execute('UPDATE messages SET deleted_at=NOW() WHERE organization_id=$1 AND id=$2', env['org'], env['message'])
        assert changed == 'UPDATE 1'
        assert await conn.fetchval('SELECT count(*) FROM booking_conversation_states WHERE organization_id=$1', env['org']) == 0
        assert await conn.fetchval('SELECT count(*) FROM booking_conversation_requests WHERE organization_id=$1', env['org']) == 0
        assert await conn.fetchval('SELECT count(*) FROM booking_conversation_revocations WHERE organization_id=$1', env['org']) == 1


async def counts(env):
    return tuple([await env['pool'].fetchval(
        f'SELECT count(*) FROM {table} WHERE organization_id=$1 AND conversation_id=$2',
        env['org'], env['conv']) for table in
        ('booking_conversation_states', 'booking_conversation_requests')])


async def subject(env):
    return await env['pool'].fetchval('SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2', env['org'], env['conv'])


async def approve_conversation(pool, org):
    await pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
        retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'booking_conversation_content','synthetic-c24',TRUE,30,FALSE,
        'test:synthetic','qa-c24',NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 day')""", org)


async def approve_technical_receipts(pool, org):
    await pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
        retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'technical_receipts','synthetic-c24',TRUE,90,FALSE,
        'test:receipts','qa-c24',NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 day')""", org)


async def test_erasure_requires_conversation_policy_then_removes_all_copies(state_env, monkeypatch):
    env = state_env
    request = await populated(env)
    contact = await subject(env)
    await env['pool'].execute("UPDATE messages SET status='handled', handling_type='ai' WHERE organization_id=$1 AND id=$2", env['org'], env['message'])
    await approve_policies(env['pool'], env['org'])
    monkeypatch.setenv('GDPR_ERASURE_EXECUTION_ENABLED', 'true')
    blocked = await Deletion(env['pool']).request(env['org'], 'synthetic-c24', uuid4(), scope='contact', subject_id=contact)
    assert blocked['status'] == 'blocked'
    assert any('booking_conversation_content' in item for item in blocked['manifest']['blockers'])
    assert await counts(env) == (1, 1)
    await approve_conversation(env['pool'], env['org'])
    result = await Deletion(env['pool']).request(env['org'], 'synthetic-c24', uuid4(), scope='contact', subject_id=contact)
    assert result['status'] == 'partial'
    assert await counts(env) == (0, 0)
    assert await _used(env['pool'], env['org']) == 1
    with pytest.raises(PermissionError):
        await env['repo'].read(env['org'], env['auth'], env['conv'])
    with pytest.raises(PermissionError):
        await apply(env, env['message'], request=request)


async def test_withdrawal_invalidates_persisted_state_and_receipts(state_env):
    env = state_env
    request = await populated(env)
    await env['pool'].execute("UPDATE contacts SET consent_status='withdrawn' WHERE organization_id=$1 AND id=$2", env['org'], await subject(env))
    assert await counts(env) == (0, 0)
    with pytest.raises(PermissionError):
        await env['repo'].read(env['org'], env['auth'], env['conv'])
    with pytest.raises(PermissionError):
        await apply(env, env['message'], request=request)
    assert await _used(env['pool'], env['org']) == 1


async def test_old_source_redaction_invalidates_whole_derived_state(state_env):
    env = state_env
    await populated(env)
    later = await second_source(env, 1700000001)
    await apply(env, later, revision=1)
    assert await counts(env) == (1, 2)
    await env['pool'].execute('UPDATE messages SET deleted_at=now() WHERE organization_id=$1 AND id=$2', env['org'], env['message'])
    assert await counts(env) == (0, 0)
    assert await _used(env['pool'], env['org']) == 2


async def test_foreign_tenant_cannot_inherit_state(state_env):
    env = state_env
    await populated(env)
    foreign = await _organization(env['pool'])
    auth = await _auth_member(env['pool'], foreign['id'], role='owner')
    with pytest.raises(PermissionError):
        await env['repo'].read(foreign['id'], auth, env['conv'])
    assert await counts(env) == (1, 1)


async def test_reassignment_does_not_transfer_original_subject_state(state_env):
    env = state_env
    await populated(env)
    other = uuid4()
    await env['pool'].execute("INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)", other, env['org'], '+15550009999')
    with pytest.raises(asyncpg.PostgresError):
        await env['pool'].execute('UPDATE conversations SET contact_id=$3 WHERE organization_id=$1 AND id=$2', env['org'], env['conv'], other)
    assert await subject(env) != other
    assert await counts(env) == (1, 1)


async def test_reassignment_rejected_before_first_apply(state_env):
    env = state_env
    other = uuid4()
    await env['pool'].execute("INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)", other, env['org'], '+15550009999')
    with pytest.raises(asyncpg.PostgresError):
        await env['pool'].execute('UPDATE conversations SET contact_id=$3 WHERE organization_id=$1 AND id=$2', env['org'], env['conv'], other)
    assert await subject(env) != other


async def test_export_projections_bind_original_subject_and_exclude_account(state_env):
    env = state_env
    request = await populated(env)
    contact = await subject(env)
    other = uuid4()
    categories = {c.name: c for c in CATEGORIES}
    for name in ('booking_conversation_states', 'booking_conversation_requests'):
        cat = categories[name]
        org_rows = await env['pool'].fetch(cat.query, env['org'])
        subject_rows = await env['pool'].fetch(cat.contact_query, env['org'], contact)
        assert len(org_rows) == len(subject_rows) == 1
        assert str(subject_rows[0]['conversation_id']) == str(env['conv'])
        assert not await env['pool'].fetch(cat.contact_query, env['org'], other)
        assert cat.account_query is None
    receipt = (await env['pool'].fetch(categories['booking_conversation_requests'].contact_query, env['org'], contact))[0]
    assert receipt['request_id'] == request
    assert 'actor_user_id' not in receipt and 'payload_hash' not in receipt
    exporter = Exporter(env['pool'])
    for scope, subject_id in (('organization', None), ('contact', contact), ('account', None)):
        kwargs = {'scope': scope, 'request_id': uuid4()}
        if subject_id is not None:
            kwargs['subject_id'] = subject_id
        requester = str(env['auth']) if scope == 'account' else 'synthetic-export'
        result = await exporter.prepare(env['org'], requester, **kwargs)
        raw = await export_bytes(exporter, env['org'], result['export_id'], requester)
        if scope == 'account':
            assert str(request).encode() not in raw
        else:
            assert str(request).encode() in raw


async def test_approved_message_retention_invalidates_entire_state(state_env, monkeypatch):
    env = state_env
    await populated(env)
    later = await second_source(env, 1700000001)
    await apply(env, later, revision=1)
    await env['pool'].execute("UPDATE messages SET created_at=now()-interval '40 days', status='handled', handling_type='ai', billed_at=NULL WHERE organization_id=$1 AND id=$2", env['org'], env['message'])
    await env['pool'].execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
        retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'messages_content','synthetic-c24',TRUE,30,FALSE,
        'test:synthetic','qa-c24',NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 day')""", env['org'])
    monkeypatch.setenv('GDPR_RETENTION_EXECUTION_ENABLED', 'true')
    await run_retention(env['pool'])
    assert await counts(env) == (1, 2)
    assert not await env['pool'].fetchval('SELECT deleted_at IS NOT NULL FROM messages WHERE id=$1', env['message'])
    await approve_conversation(env['pool'], env['org'])
    result = await run_retention(env['pool'])
    assert result['messages_redacted'] >= 1
    assert await env['pool'].fetchval('SELECT deleted_at IS NOT NULL FROM messages WHERE id=$1', env['message'])
    assert await counts(env) == (0, 0)
    assert await _used(env['pool'], env['org']) == 2


async def test_concurrent_erasure_and_apply_cannot_resurrect_state(state_env, monkeypatch):
    env = state_env
    await commit_source(env, env['message'], '2026-11-12', {'service_id': 'standard'}, 1700000000)
    contact = await subject(env)
    await env['pool'].execute("UPDATE messages SET status='handled', handling_type='ai' WHERE organization_id=$1 AND id=$2", env['org'], env['message'])
    await approve_policies(env['pool'], env['org'])
    await approve_conversation(env['pool'], env['org'])
    monkeypatch.setenv('GDPR_ERASURE_EXECUTION_ENABLED', 'true')
    # Queue both operations behind the same advisory fence, then release it.
    async with env['pool'].acquire() as holder:
        async with holder.transaction():
            await holder.execute('SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))', str(env['org']))
            writing = asyncio.create_task(apply(env, env['message']))
            erasing = asyncio.create_task(Deletion(env['pool']).request(
                env['org'], 'synthetic-race', uuid4(), scope='contact', subject_id=contact))
            async def queued():
                while True:
                    count = await env['pool'].fetchval(
                        'WITH RECURSIVE blocked(pid) AS (SELECT pid FROM pg_stat_activity WHERE $1=ANY(pg_blocking_pids(pid)) UNION SELECT a.pid FROM pg_stat_activity a JOIN blocked b ON b.pid=ANY(pg_blocking_pids(a.pid))) SELECT count(*) FROM blocked',
                        holder.get_server_pid())
                    if count >= 2:
                        return
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(queued(), 10)
    write_result, erase_result = await asyncio.wait_for(asyncio.gather(writing, erasing, return_exceptions=True), 20)
    assert isinstance(erase_result, dict), repr(erase_result)
    # If erasure sees a preexisting or in-flight proposal, explicit policy covers it.
    assert erase_result['status'] == 'partial'
    assert isinstance(write_result, (dict, PermissionError))
    assert await counts(env) == (0, 0)
    with pytest.raises(PermissionError):
        await env['repo'].read(env['org'], env['auth'], env['conv'])


def checkpoint(env, operation, contact, key, sequence=2):
    import hashlib
    import hmac
    import json
    payload = {'version': 'c24-restore-v1', 'organization_id': str(env['org']),
               'backup_id': 'synthetic-backup', 'sequence': sequence, 'complete': True,
               'erasures': [{'operation_id': str(operation), 'scope': 'contact', 'subject_id': str(contact)}]}
    signature = hmac.new(key, json.dumps(payload, sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()
    return {'payload': payload, 'signature': signature}


async def test_restore_checkpoint_requires_replay_and_rejects_stale_content(state_env):
    from src.core.gdpr.restore_checkpoint import RestoreBlocked, verify_restore_checkpoint
    env = state_env
    await populated(env)
    contact, operation, key = await subject(env), uuid4(), b'synthetic-key-32-bytes-0123456789'
    journal = checkpoint(env, operation, contact, key)
    async with env['pool'].acquire() as conn, conn.transaction():
        async def verify(value, minimum=2):
            return await verify_restore_checkpoint(conn, env['org'], value, verification_key=key,
                                                   backup_id='synthetic-backup', minimum_sequence=minimum)
        for invalid in (None, {'payload': journal['payload'], 'signature': '0' * 64}):
            with pytest.raises(RestoreBlocked):
                await verify(invalid)
        with pytest.raises(RestoreBlocked):
            await verify(journal, minimum=3)
        with pytest.raises(RestoreBlocked):
            await verify(journal)  # external journal newer than restored database
        await conn.execute("""INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,kind,scope,status,subject_id)
            VALUES($1,$2,'synthetic',$3,$4,'erase','contact','running',$5)""", operation, env['org'], uuid4(), 'a'*64, contact)
        await conn.execute("""INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope,subject_id,identifier_digest,key_id)
            VALUES($1,$2,'contact',$3,$4,'synthetic')""", env['org'], operation, contact, 'b'*64)
        with pytest.raises(RestoreBlocked):
            await verify(journal)  # a replayed barrier alone cannot erase old content


@pytest.mark.migration_limit(74)
async def test_restore_checkpoint_accepts_completed_local_erasure_only(state_env, monkeypatch):
    from hashlib import sha256
    import json
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from src.core.gdpr.restore_checkpoint import RestoreBlocked, verify_restore_checkpoint, verify_restore_journal_replay
    from src.core.gdpr.restore_journal import JournalCheckpoint
    env = state_env
    await populated(env)
    contact = await subject(env)
    await env['pool'].execute("UPDATE messages SET status='handled', handling_type='ai' WHERE organization_id=$1 AND id=$2", env['org'], env['message'])
    await approve_policies(env['pool'], env['org'])
    await approve_conversation(env['pool'], env['org'])
    await approve_technical_receipts(env['pool'], env['org'])
    monkeypatch.setenv('GDPR_ERASURE_EXECUTION_ENABLED', 'true')
    erased = await Deletion(env['pool']).request(env['org'], 'synthetic-restore', uuid4(), scope='contact', subject_id=contact)
    assert erased['status'] == 'partial'
    assert await counts(env) == (0, 0)
    key = b'synthetic-key-32-bytes-0123456789'
    journal = checkpoint(env, erased['id'], contact, key)
    async with env['pool'].acquire() as conn, conn.transaction():
        proof = await verify_restore_checkpoint(conn, env['org'], journal, verification_key=key,
                                                backup_id='synthetic-backup', minimum_sequence=2)
        barrier = await conn.fetchrow('''SELECT identifier_digest,key_id FROM gdpr_erasure_barriers
            WHERE organization_id=$1 AND operation_id=$2''', env['org'], erased['id'])
        signer, installation = Ed25519PrivateKey.generate(), uuid4()
        anchor = JournalCheckpoint(0, '0' * 64)
        record = {'sequence': 1, 'organization_id': str(env['org']),
                  'operation_id': str(erased['id']), 'scope': 'contact', 'subject_id': str(contact),
                  'identifier_digest': barrier['identifier_digest'], 'key_id': barrier['key_id']}
        async def verify_signed():
            raw = json.dumps({'version': 1, 'installation_id': str(installation),
                              'previous_sha256': anchor.sha256, 'entries': [record]}).encode()
            signature = signer.sign(b'melpis.restore.erasure-journal.v1\0' + raw)
            return await verify_restore_journal_replay(
                conn, env['org'], raw, signature, installation_id=installation,
                trusted_public_key=signer.public_key(), backup_checkpoint=anchor,
                independent_tip=JournalCheckpoint(1, sha256(raw).hexdigest()),
            )
        signed_proof = await verify_signed()
        assert signed_proof['verified_erasures'] == 1
        assert signed_proof['traffic_authorized'] is False
        record['identifier_digest'] = '0' * 64
        with pytest.raises(RestoreBlocked, match='does not match the journal'):
            await verify_signed()
    assert proof['scope'] == 'primary_c24_only'
    assert proof['traffic_authorized'] is False
    assert proof['external_copies_certified'] is False


async def test_conversation_retention_requires_policy_and_expires_all_derived_copies(state_env, monkeypatch):
    from tests.core.test_quota_reservation_lifecycle import _inbound_operation
    env = state_env
    request = await populated(env)
    operation_id = _inbound_operation(env['org'], env['message'], text='2026-11-12').id
    await env['pool'].execute("UPDATE messages SET created_at=now()-interval '40 days', status='handled', handling_type='ai' WHERE organization_id=$1 AND id=$2", env['org'], env['message'])
    await env['pool'].execute("UPDATE simulation_requests SET completed_at=now()-interval '40 days' WHERE organization_id=$1 AND request_id=$2", env['org'], operation_id)
    monkeypatch.setenv('GDPR_RETENTION_EXECUTION_ENABLED', 'true')
    await run_retention(env['pool'])
    assert await counts(env) == (1, 1)
    assert await env['pool'].fetchval("SELECT ai_reply_cache ? 'booking_state_proposal' FROM messages WHERE id=$1", env['message'])
    await approve_conversation(env['pool'], env['org'])
    await run_retention(env['pool'])
    assert await counts(env) == (0, 0)
    assert not await env['pool'].fetchval("SELECT ai_reply_cache ? 'booking_state_proposal' FROM messages WHERE id=$1", env['message'])
    assert not await env['pool'].fetchval("SELECT response ? 'booking_state_proposal' FROM simulation_requests WHERE organization_id=$1 AND request_id=$2", env['org'], operation_id)
    assert await _used(env['pool'], env['org']) == 1
    with pytest.raises(PermissionError):
        await apply(env, env['message'], request=request)


@pytest.mark.parametrize('policy_state', ['held', 'expired'])
async def test_conversation_retention_rejects_held_or_expired_policy(state_env, monkeypatch, policy_state):
    env = state_env
    await populated(env)
    await env['pool'].execute("UPDATE messages SET created_at=now()-interval '40 days', status='handled', handling_type='ai' WHERE organization_id=$1 AND id=$2", env['org'], env['message'])
    await env['pool'].execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
        retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'booking_conversation_content','synthetic-held',TRUE,30,$2,
        'test:synthetic','qa-c24',NOW()-INTERVAL '2 days',
        CASE WHEN $3 THEN NOW()-INTERVAL '1 day' ELSE NOW()+INTERVAL '1 day' END)""",
        env['org'], policy_state == 'held', policy_state == 'expired')
    monkeypatch.setenv('GDPR_RETENTION_EXECUTION_ENABLED', 'true')
    await run_retention(env['pool'])
    assert await counts(env) == (1, 1)
    assert await env['pool'].fetchval("SELECT ai_reply_cache ? 'booking_state_proposal' FROM messages WHERE id=$1", env['message'])
    assert await _used(env['pool'], env['org']) == 1
