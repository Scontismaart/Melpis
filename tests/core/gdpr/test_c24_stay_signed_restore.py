"""Signed replay composes independently authenticated stay purge history."""
import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.restore_checkpoint import RestoreBlocked, verify_restore_checkpoint, verify_restore_journal_replay
from src.core.gdpr.restore_journal import JournalCheckpoint
from tests.core.bookings.test_booking_stays_postgres import hotel_setup
from tests.core.bookings.test_hotel_allocation_archive_postgres import _complete_with_old_evidence

pytestmark = pytest.mark.asyncio


async def test_signed_replay_requires_authenticated_stay_checkpoint(pg_pool):
    env, _ = await hotel_setup(pg_pool)
    signer, installation, other = Ed25519PrivateKey.generate(), uuid4(), uuid4()
    anchor = JournalCheckpoint(0, '0' * 64)
    record = {'sequence': 1, 'organization_id': str(other), 'operation_id': str(uuid4()),
              'scope': 'organization', 'subject_id': None, 'identifier_digest': None, 'key_id': None}
    raw = json.dumps({'version': 1, 'installation_id': str(installation),
                      'previous_sha256': anchor.sha256, 'entries': [record]}).encode()
    signature = signer.sign(b'melpis.restore.erasure-journal.v1\0' + raw)
    trust = dict(installation_id=installation, trusted_public_key=signer.public_key(),
                 backup_checkpoint=anchor, independent_tip=JournalCheckpoint(1, hashlib.sha256(raw).hexdigest()))
    key = b'synthetic-checkpoint-key-32-bytes'
    payload = {'version': 'c24-restore-v2', 'organization_id': str(env.org), 'backup_id': 'backup',
               'sequence': 3, 'complete': True, 'erasures': [], 'stay_purges': []}
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    checkpoint = {'payload': payload, 'signature': hmac.new(key, canonical, hashlib.sha256).hexdigest()}
    async with pg_pool.acquire() as conn, conn.transaction():
        with pytest.raises(RestoreBlocked, match='stay purge journal'):
            await verify_restore_journal_replay(conn, env.org, raw, signature, **trust)
        for options in (dict(backup_id='wrong', minimum_sequence=3), dict(backup_id='backup', minimum_sequence=4)):
            with pytest.raises(RestoreBlocked):
                await verify_restore_journal_replay(conn, env.org, raw, signature, stay_checkpoint=checkpoint,
                    checkpoint_verification_key=key, **options, **trust)
        proof = await verify_restore_journal_replay(conn, env.org, raw, signature, stay_checkpoint=checkpoint,
            checkpoint_verification_key=key, backup_id='backup', minimum_sequence=3, **trust)
        assert proof['traffic_authorized'] is False
    async with pg_pool.acquire() as conn, conn.transaction(isolation='repeatable_read'):
        with pytest.raises(RestoreBlocked, match='read committed'):
            await verify_restore_journal_replay(conn, env.org, raw, signature, stay_checkpoint=checkpoint,
                checkpoint_verification_key=key, backup_id='backup', minimum_sequence=3, **trust)


async def test_restore_rejects_remaining_purged_allocation_or_completion(pg_pool):
    env, lineage, booking = await _complete_with_old_evidence(pg_pool)
    key = b'synthetic-checkpoint-key-32-bytes'
    async with pg_pool.acquire() as conn, conn.transaction():
        # Preserve pre-purge copies to simulate an incomplete restore replay.
        await conn.execute('CREATE TEMP TABLE restored_sets AS SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
        await conn.execute('CREATE TEMP TABLE restored_claims AS SELECT * FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
        await conn.execute('CREATE TEMP TABLE restored_completion AS SELECT * FROM booking_stay_completion_evidence WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
        await conn.fetchval('SELECT archive_completed_hotel_allocation($1,$2)', env.org, booking)
        await conn.execute('CREATE TEMP TABLE restored_archived_sets AS SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
        await conn.fetchval("SELECT booking_stay_admin_purge($1,$2,'retention',NULL,'synthetic-stay-v1')", env.org, booking)
        await conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
        request_ids = await conn.fetch('SELECT request_id FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2', env.org, booking)
        payload = {'version': 'c24-restore-v2', 'organization_id': str(env.org), 'backup_id': 'backup',
                   'sequence': 3, 'complete': True, 'erasures': [], 'stay_purges': [{
                       'booking_id': str(booking), 'subject_id': str(lineage.contact_id),
                       'purpose': 'retention', 'operation_id': None, 'policy_version': 'synthetic-stay-v1',
                       'request_ids': [str(row['request_id']) for row in request_ids]}]}
        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        checkpoint = {'payload': payload, 'signature': hmac.new(key, canonical, hashlib.sha256).hexdigest()}

        async def verify():
            return await verify_restore_checkpoint(conn, env.org, checkpoint, verification_key=key,
                                                   backup_id='backup', minimum_sequence=3)

        assert (await verify())['traffic_authorized'] is False
        for source in ('restored_archived_sets', 'restored_sets', 'restored_completion'):
            savepoint = conn.transaction()
            await savepoint.start()
            if source == 'restored_completion':
                await conn.execute('INSERT INTO booking_stay_completion_evidence SELECT * FROM restored_completion')
            else:
                await conn.execute('ALTER TABLE booking_allocation_sets DISABLE TRIGGER allocation_guard')
                await conn.execute(f'INSERT INTO booking_allocation_sets SELECT * FROM {source}')
                if source == 'restored_sets':
                    await conn.execute('ALTER TABLE booking_resource_allocations DISABLE TRIGGER allocation_guard')
                    await conn.execute('INSERT INTO booking_resource_allocations SELECT * FROM restored_claims')
            with pytest.raises(RestoreBlocked, match='Stay purge copies'):
                await verify()
            await savepoint.rollback()
