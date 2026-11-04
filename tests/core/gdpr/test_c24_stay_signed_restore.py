"""Signed replay composes independently authenticated stay purge history."""
import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.restore_checkpoint import RestoreBlocked, verify_restore_journal_replay
from src.core.gdpr.restore_journal import JournalCheckpoint
from tests.core.bookings.test_booking_stays_postgres import hotel_setup

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
