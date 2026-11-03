"""Opt-in restore of an encrypted synthetic snapshot before a later erasure.

Never runs against a deployed database. Keeps the restored synthetic DB.
An erasure request already in the backup is replayed while quarantined.
"""

import asyncio
from hashlib import sha256
import json
import os
import re
import subprocess
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from src.core.gdpr.deletion import Deletion
from src.core.gdpr.operations import Operations
from src.core.gdpr.restore_checkpoint import RestoreBlocked, verify_restore_journal_replay
from src.core.gdpr.restore_journal import JournalCheckpoint
from tests.core.gdpr.test_conversation_lifecycle_c24 import (
    approve_conversation, approve_policies, populated, state_env, subject,
)


_CONTAINERS = {
    "melpis-filelock-vector-20261029",
    "melpis-c23-pg18-20261030",
}
_SOURCE = re.compile(r"postgresql://(test|postgres):test@127\.0\.0\.1:16434/(melpis_c24_restore_src_[0-9a-f]{12})")


async def _docker(*args, input_bytes=None):
    result = await asyncio.to_thread(
        subprocess.run, ["docker", *args], input=input_bytes,
        capture_output=True, timeout=45, check=False,
    )
    if result.returncode:
        pytest.fail("isolated synthetic backup or restore command failed")
    return result.stdout


@pytest.fixture(autouse=True)
def restore_target():
    if os.getenv("MELPIS_C24_SYNTHETIC_RESTORE") != "1":
        pytest.skip("isolated named synthetic restore proof is opt-in")
    container = os.getenv("MELPIS_C24_RESTORE_CONTAINER")
    dsn = os.getenv("TEST_DB_DSN", "")
    match = _SOURCE.fullmatch(dsn)
    if container not in _CONTAINERS or not match:
        pytest.fail("restore is restricted to the dedicated synthetic database")
    return container, dsn, match.group(1), match.group(2)


@pytest.mark.asyncio
async def test_old_encrypted_backup_requires_signed_erasure_replay(restore_target, state_env, monkeypatch):
    # Initialize the destructive test fixture only after verifying the opt-in
    # and the dedicated disposable DB target.
    container, dsn, principal, source_db = restore_target
    assert (await _docker("inspect", "--format", "{{.Name}}", container)).strip().decode() == "/" + container
    env = state_env
    await populated(env)
    org, person = env["org"], await subject(env)
    await env["pool"].execute(
        "UPDATE messages SET status='handled', handling_type='ai' WHERE organization_id=$1 AND id=$2",
        org, env["message"],
    )
    await approve_policies(env["pool"], org)
    await approve_conversation(env["pool"], org)
    requester = "synthetic-restore"
    operation = await Operations(env["pool"]).create(
        org, requester, uuid4(), kind="erase", scope="contact", subject_id=person,
    )

    plaintext = await _docker("exec", container, "pg_dump", "-U", principal, "-d", source_db,
                              "--no-owner", "--no-privileges", "--format=plain")
    cipher = Fernet(Fernet.generate_key())
    encrypted_snapshot = cipher.encrypt(plaintext)
    snapshot_sha = sha256(plaintext).digest()
    del plaintext
    assert encrypted_snapshot and b"CREATE TABLE" not in encrypted_snapshot

    monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")
    await Deletion(env["pool"]).advance(org, operation, requester)
    barrier = await env["pool"].fetchrow(
        """SELECT identifier_digest,key_id FROM gdpr_erasure_barriers
           WHERE organization_id=$1 AND operation_id=$2 AND subject_id=$3""", org, operation, person,
    )
    assert barrier
    installation, signer = uuid4(), Ed25519PrivateKey.generate()
    anchor = JournalCheckpoint(0, "0" * 64)
    record = {"sequence": 1, "organization_id": str(org), "operation_id": str(operation),
              "scope": "contact", "subject_id": str(person), **dict(barrier)}
    journal = json.dumps({"version": 1, "installation_id": str(installation),
                          "previous_sha256": anchor.sha256, "entries": [record]}).encode()
    signature = signer.sign(b"melpis.restore.erasure-journal.v1\0" + journal)
    tip = JournalCheckpoint(1, sha256(journal).hexdigest())

    restored_db = "melpis_c24_restore_dst_" + uuid4().hex[:12]
    await _docker("exec", container, "createdb", "-U", principal, restored_db)
    restored_sql = cipher.decrypt(encrypted_snapshot)
    assert sha256(restored_sql).digest() == snapshot_sha
    await _docker("exec", "-i", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
                  "-U", principal, "-d", restored_db, input_bytes=restored_sql)
    del restored_sql
    pool = await asyncpg.create_pool(dsn.rsplit("/", 1)[0] + "/" + restored_db, min_size=1, max_size=3)
    try:
        async def verify():
            async with pool.acquire() as conn, conn.transaction():
                return await verify_restore_journal_replay(
                    conn, org, journal, signature, installation_id=installation,
                    trusted_public_key=signer.public_key(), backup_checkpoint=anchor, independent_tip=tip,
                )
        with pytest.raises(RestoreBlocked, match="has not been replayed"):
            await verify()
        before = await pool.fetchval("SELECT messages_used_this_period FROM organizations WHERE id=$1", org)
        await Deletion(pool).advance(org, operation, requester)
        proof = await verify()
        assert proof["verified_erasures"] == 1 and proof["traffic_authorized"] is False
        assert await pool.fetchval("SELECT messages_used_this_period FROM organizations WHERE id=$1", org) == before
        await Deletion(pool).advance(org, operation, requester)
        assert (await verify())["verified_erasures"] == 1
    finally:
        await pool.close()
