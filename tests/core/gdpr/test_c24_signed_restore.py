"""Opt-in restore of an encrypted synthetic snapshot before a later erasure.

Never runs against a deployed database. Keeps the restored synthetic DB.
An erasure request already in the backup is replayed while quarantined.
"""

import asyncio
import hmac
import json
import os
import re
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.gdpr.deletion import Deletion
from src.core.gdpr.checkpoint_pages import publish_checkpoint_pages
from src.core.gdpr.operations import Operations
from src.core.gdpr.restore_checkpoint import (
    RestoreBlocked,
    bind_retained_receipt_prune_checkpoint,
    retain_receipt_prune_checkpoint,
    verify_restore_checkpoint,
    verify_restore_journal_replay,
)
from src.core.gdpr.restore_journal import JournalCheckpoint
from src.core.gdpr.independent_tip import IndependentTipStore
from src.core.gdpr.journal_publication import LocalJournalStore, publish_segment, publish_checkpoint
from src.core.gdpr.restore_policy_state import capture_policy_state
from src.core.gdpr.source_journal_evidence import capture_erasure_journal_delta
from src.core.gdpr.source_journal_pages import capture_erasure_journal_delta_paged, publish_source_erasure_batch
from tests.core.auth.test_access_revocation import add_membership
from tests.core.test_quota_reservation_lifecycle import _organization, _inbound_message
from tests.core.gdpr.test_c24_purged_source_replay import (
    historical_fence,
    signed_prune_checkpoint,
    upgrade_fence_retention,
)
from tests.core.gdpr.test_c24_retention_sweep import approve_messages
from tests.core.gdpr.test_restore_policy_state import add_hold, event as hold_event
from tests.core.gdpr.test_conversation_lifecycle_c24 import (
    approve_conversation,
    approve_policies,
    approve_technical_receipts,
    populated,
    subject,
)

pytest_plugins = ("tests.core.bookings.test_conversation_state_postgres",)

_CONTAINERS = {
    "melpis-filelock-vector-20261029": 16434,
    "melpis-c23-pg18-20261030": 16434,
    "melpis-c24-vector-pg16": 55434,
    "melpis-c24-vector-pg18": 55435,
    "melpis-a06-restore-20261120": 55436,
    "melpis-a06-pg17-20261120": 55437,
}
_VECTOR_IMAGES = {
    "melpis-c24-vector-pg16": "pgvector/pgvector:0.7.4-pg16",
    "melpis-c24-vector-pg18": "pgvector/pgvector:0.8.1-pg18",
    "melpis-a06-restore-20261120": "pgvector/pgvector:0.7.4-pg16",
    "melpis-a06-pg17-20261120": "pgvector/pgvector:pg17",
}
_SOURCE_DB = re.compile(r"melpis_c24_restore_src_([0-9a-f]{12})\Z")


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
    try:
        parsed = urlsplit(dsn)
        match = _SOURCE_DB.fullmatch(parsed.path.removeprefix("/"))
        legacy = (container in {"melpis-filelock-vector-20261029", "melpis-c23-pg18-20261030",
                                "melpis-a06-restore-20261120", "melpis-a06-pg17-20261120"}
                  and parsed.username in {"test", "postgres"} and parsed.password == "test")
        synthetic = (match is not None and parsed.username == "melpis_c24_src_" + match.group(1)
                     and parsed.password is not None and re.fullmatch(r"[0-9a-f]{64}", parsed.password))
        allowed = (container in _CONTAINERS and parsed.scheme == "postgresql" and parsed.hostname == "127.0.0.1"
                   and parsed.port == _CONTAINERS[container] and not parsed.query and not parsed.fragment
                   and match is not None and (legacy or synthetic))
    except (TypeError, ValueError):
        allowed = False
    if not allowed:
        pytest.fail("restore is restricted to the dedicated synthetic database")
    return container, dsn, parsed.username, match.group(0)


async def _verify_restore_target(container):
    assert (await _docker("inspect", "--format", "{{.Name}}", container)).strip().decode() == "/" + container
    if container in _VECTOR_IMAGES:
        image = (await _docker("inspect", "--format", "{{.Config.Image}}", container)).strip().decode()
        binding = (await _docker("port", container, "5432/tcp")).strip().decode()
        assert image == _VECTOR_IMAGES[container]
        assert binding == f"127.0.0.1:{_CONTAINERS[container]}"


def _promote_restored_backup(authority, store, *, backup_id, anchor, organizations, encrypted_backup,
                            signer, journal_key, checkpoint_key, policy_key, policy_cipher):
    from src.core.gdpr.checkpoint_pages import read_any_checkpoint

    inventory = json.dumps(dict(version=1, installation_id=str(authority.installation_id), backups=[
        dict(backup_id=backup_id, manifest_sha256=sha256(encrypted_backup).hexdigest(),
             completed_at=datetime.now(timezone.utc).isoformat(), organizations=list(map(str, organizations)),
             anchor=dict(sequence=anchor.sequence, sha256=anchor.sha256), exception=None)])).encode()
    inventory_digest = sha256(inventory).hexdigest()
    keys = dict(verification_key=checkpoint_key, encryption_key=journal_key)
    candidates = {}
    for org in organizations:
        old = authority.checkpoint_pin(org, backup_id)
        payload = read_any_checkpoint(store, authority.installation_id, org, backup_id, old, **keys)
        candidate = dict(payload, version="c24-restore-v5", sequence=old.sequence+1,
                         ordinary_booking_purges=payload.get("ordinary_booking_purges", []))
        candidates[org, backup_id] = publish_checkpoint_pages(store, authority.installation_id, candidate,
                                                               previous_pin=old, **keys)
    authority.promote_paged_checkpoints(store, inventory, candidates=candidates,
        latest_inventory_sha256=inventory_digest, trusted_public_key=signer.public_key(),
        journal_encryption_key=journal_key, checkpoint_verification_key=checkpoint_key,
        checkpoint_encryption_key=journal_key, policy_verification_key=policy_key, policy_encryption_key=policy_cipher)
    return inventory, inventory_digest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("published_v5", "scope"),
    [(False, "contact"), (True, "contact"), (True, "organization")],
    ids=["v3", "v5", "v5-organization"],
)
async def test_old_encrypted_backup_requires_signed_erasure_replay(
    restore_target, state_env, monkeypatch, tmp_path, published_v5, scope,
):
    container, dsn, principal, source_db = restore_target
    await _verify_restore_target(container)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    env = state_env
    org = env["org"]
    subject_id = None
    if scope == "contact":
        await populated(env)
        subject_id = await subject(env)
    await env["pool"].execute(
        """UPDATE messages SET status='handled', handling_type='ai',
            content_text=COALESCE(content_text,'synthetic restore fixture')
            WHERE organization_id=$1 AND id=$2""", org, env["message"],
    )
    await approve_policies(env["pool"], org, organization=scope == "organization")
    await approve_conversation(env["pool"], org)
    await approve_technical_receipts(env["pool"], org)
    requester = "synthetic-restore"
    foreign = (await _organization(env["pool"]))["id"]
    foreign_message = await _inbound_message(env["pool"], foreign, canale="whatsapp")
    foreign_original = await env["pool"].fetchval("SELECT content FROM messages WHERE id=$1", foreign_message)

    if scope == "organization":
        await add_membership(env["pool"], foreign, env["actor"])

    async def assert_shared_access(database, expected_tenants):
        assert await database.fetchval("SELECT EXISTS(SELECT FROM auth.users WHERE id=$1)", env["auth"])
        assert {row["organization_id"] for row in await database.fetch(
            """SELECT organization_id FROM organization_memberships
               JOIN user_profiles ON user_profiles.id=organization_memberships.user_id
               WHERE user_profiles.auth_user_id=$1""", env["auth"],
        )} == expected_tenants

    plaintext = await _docker("exec", container, "pg_dump", "-U", principal, "-d", source_db,
                              "--no-owner", "--no-privileges", "--format=plain")
    cipher = Fernet(Fernet.generate_key())
    encrypted_snapshot = cipher.encrypt(plaintext)
    snapshot_sha = sha256(plaintext).digest()
    del plaintext
    assert encrypted_snapshot and b"CREATE TABLE" not in encrypted_snapshot

    installation, signer = uuid4(), Ed25519PrivateKey.generate()
    policy_key, policy_cipher = secrets.token_bytes(32), Fernet.generate_key()
    policy_trust = dict(installation_id=installation, backup_id="synthetic-backup",
                        verification_key=policy_key, encryption_key=policy_cipher)
    async with env["pool"].acquire() as conn, conn.transaction(readonly=True):
        old_policy_pin, old_policy_ciphertext = await capture_policy_state(conn, org, sequence=0, **policy_trust)
    hold = uuid4()
    async with env["pool"].acquire() as conn, conn.transaction():
        await add_hold(conn, org, hold)
        await hold_event(conn, org, hold, "reviewed")
        await hold_event(conn, org, hold, "released")
    operation = await Operations(env["pool"]).create(
        org, requester, uuid4(), kind="erase", scope=scope, subject_id=subject_id,
    )
    monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")
    await Deletion(env["pool"]).advance(org, operation, requester)
    barrier = await env["pool"].fetchrow(
        """SELECT identifier_digest,key_id FROM gdpr_erasure_barriers
           WHERE organization_id=$1 AND operation_id=$2 AND scope=$3""", org, operation, scope,
    )
    assert barrier, (await Operations(env["pool"]).get(org, operation, requester))["manifest"]
    if scope == "organization":
        await assert_shared_access(env["pool"], {foreign})
    signed_subject = str(subject_id) if subject_id is not None else None
    anchor = JournalCheckpoint(0, "0" * 64)
    record = {"sequence": 1, "organization_id": str(org), "operation_id": str(operation),
              "scope": scope, "subject_id": signed_subject, **dict(barrier)}
    journal = json.dumps({"version": 1, "installation_id": str(installation),
                          "previous_sha256": anchor.sha256, "entries": [record]}).encode()
    signature = signer.sign(b"melpis.restore.erasure-journal.v1\0" + journal)
    tip = JournalCheckpoint(1, sha256(journal).hexdigest())
    checkpoint_key = secrets.token_bytes(32)
    checkpoint_payload = {"version": "c24-restore-v3", "organization_id": str(org),
                          "backup_id": "synthetic-backup", "sequence": 1, "complete": True,
                          "erasures": [{"operation_id": str(operation), "scope": scope,
                                        "subject_id": signed_subject}], "stay_purges": [], "receipt_prunes": []}
    checkpoint_raw = json.dumps(checkpoint_payload, sort_keys=True, separators=(",", ":"),
                                allow_nan=False).encode()
    checkpoint = {"payload": checkpoint_payload,
                  "signature": hmac.new(checkpoint_key, checkpoint_raw, sha256).hexdigest()}

    # Independent authority and ciphertext store are separate even in the
    # synthetic drill; verification must not derive the latest tip from a bucket.
    object_root, authority_root = tmp_path / "journal", tmp_path / "operator"
    object_root.mkdir(mode=0o700)
    authority_root.mkdir(mode=0o700)
    store = LocalJournalStore(object_root)
    authority = IndependentTipStore.initialize(authority_root / "tip.sqlite", installation)
    journal_key = Fernet.generate_key()
    source_trust = dict(trusted_public_key=signer.public_key(), journal_encryption_key=journal_key)
    async with env["pool"].acquire() as conn:
        async with conn.transaction():
            with pytest.raises(RestoreBlocked, match="Read-only source authority"):
                await capture_erasure_journal_delta(
                    conn, authority, store, expected_organizations=[org, foreign], **source_trust)
        async with conn.transaction(readonly=True):
            with pytest.raises(RestoreBlocked, match="organization inventory mismatch"):
                await capture_erasure_journal_delta(
                    conn, authority, store, expected_organizations=[org], **source_trust)
            prior_tip, delta = await capture_erasure_journal_delta(
                conn, authority, store, expected_organizations=[org, foreign], **source_trust)
            assert await capture_erasure_journal_delta_paged(
                conn, authority, store, expected_organizations=[org, foreign], max_entries=1,
                max_segments=1, **source_trust) == (prior_tip, delta, 0)
    assert prior_tip == anchor
    assert delta == ({k: v for k, v in record.items() if k != "sequence"},)
    published = publish_segment(store, installation, prior_tip, list(delta),
                                signer=signer, encryption_key=journal_key)
    authority.advance(store, anchor, published, trusted_public_key=signer.public_key(),
                      encryption_key=journal_key)
    async with env["pool"].acquire() as conn, conn.transaction(readonly=True):
        assert await capture_erasure_journal_delta(
            conn, authority, store, expected_organizations=[org, foreign], **source_trust) == (published, ())
        assert await capture_erasure_journal_delta_paged(
            conn, authority, store, expected_organizations=[org, foreign], max_segments=1,
            **source_trust) == (published, (), 0)
    for payload in (checkpoint_payload, dict(checkpoint_payload, organization_id=str(foreign), erasures=[])):
        pin = publish_checkpoint(store, installation, payload,
                                 verification_key=checkpoint_key, encryption_key=journal_key)
        authority.advance_checkpoint(store, None, pin, verification_key=checkpoint_key,
                                     encryption_key=journal_key)
    authority.publish_policy(store, None, old_policy_pin, old_policy_ciphertext,
                             verification_key=policy_key, encryption_key=policy_cipher)
    async with env["pool"].acquire() as conn, conn.transaction(readonly=True):
        for tenant in (org, foreign):
            policy_pin, sealed_policy = await capture_policy_state(conn, tenant, sequence=1, **policy_trust)
            authority.publish_policy(store, old_policy_pin if tenant == org else None, policy_pin, sealed_policy,
                                     verification_key=policy_key, encryption_key=policy_cipher)
    with pytest.raises(RestoreBlocked):
        authority.publish_policy(store, None, old_policy_pin, old_policy_ciphertext,
                                 verification_key=policy_key, encryption_key=policy_cipher)
    inventory, inventory_digest = (None, None)
    if published_v5:
        inventory, inventory_digest = _promote_restored_backup(authority, store, backup_id="synthetic-backup",
            anchor=anchor, organizations=[org, foreign], encrypted_backup=encrypted_snapshot, signer=signer,
            journal_key=journal_key, checkpoint_key=checkpoint_key, policy_key=policy_key, policy_cipher=policy_cipher)

    restored_db = "melpis_c24_restore_dst_" + uuid4().hex[:12]
    await _docker("exec", container, "createdb", "-U", principal, restored_db)
    await _docker("exec", container, "psql", "-U", principal, "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                  "-c", f'REVOKE CONNECT ON DATABASE "{restored_db}" FROM PUBLIC')
    restored_sql = cipher.decrypt(encrypted_snapshot)
    assert sha256(restored_sql).digest() == snapshot_sha
    await _docker("exec", "-i", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
                  "-U", principal, "-d", restored_db, input_bytes=restored_sql)
    del restored_sql
    pool = await asyncpg.create_pool(dsn.rsplit("/", 1)[0] + "/" + restored_db, min_size=1, max_size=1)
    try:
        from src.core.gdpr.restore_coordinator import replay_primary_published_erasures, verify_primary_published_restore

        if scope == "organization":
            await assert_shared_access(pool, {org, foreign})

        async def verify_independent(organizations):
            async with pool.acquire() as conn, conn.transaction():
                return await verify_primary_published_restore(
                    conn, authority, store, backup_checkpoint=anchor,
                    backup_id="synthetic-backup", expected_organizations=organizations,
                    trusted_public_key=signer.public_key(), journal_encryption_key=journal_key,
                    checkpoint_verification_key=checkpoint_key,
                    checkpoint_encryption_key=journal_key,
                    recovery_inventory_bytes=inventory, latest_inventory_sha256=inventory_digest,
                )

        async def verify():
            async with pool.acquire() as conn, conn.transaction():
                return await verify_restore_journal_replay(
                    conn, org, journal, signature, installation_id=installation,
                    trusted_public_key=signer.public_key(), backup_checkpoint=anchor, independent_tip=tip,
                    stay_checkpoint=checkpoint, checkpoint_verification_key=checkpoint_key,
                    backup_id="synthetic-backup", minimum_sequence=1, checkpoint_sha256=sha256(checkpoint_raw).hexdigest(),
                )
        with pytest.raises(RestoreBlocked, match="has not been replayed"):
            await verify()
        with pytest.raises(RestoreBlocked):
            await verify_independent([org, foreign])
        before = await pool.fetchval("SELECT messages_used_this_period FROM organizations WHERE id=$1", org)
        assert not await pool.fetchval("SELECT EXISTS(SELECT 1 FROM gdpr_operations WHERE organization_id=$1 AND id=$2)", org, operation)
        assert await pool.fetchval("SELECT count(*) FROM gdpr_retention_hold_events WHERE organization_id=$1", org) == 0
        async def replay():
            return await replay_primary_published_erasures(
                pool, authority, store, backup_checkpoint=anchor, backup_id="synthetic-backup",
                expected_organizations=[org, foreign], trusted_public_key=signer.public_key(),
                journal_encryption_key=journal_key, checkpoint_verification_key=checkpoint_key,
                checkpoint_encryption_key=journal_key, quarantine_database=restored_db,
                policy_verification_key=policy_key,
                policy_encryption_key=policy_cipher,
                ordinary_inventory_bytes=inventory, latest_inventory_sha256=inventory_digest,
            )
        if published_v5:
            later_hold = uuid4()
            async with env["pool"].acquire() as conn, conn.transaction():
                await add_hold(conn, org, later_hold)

            async def update_policy(sequence):
                previous = authority.policy_pin(org, "synthetic-backup")
                async with env["pool"].acquire() as conn, conn.transaction(readonly=True):
                    current, ciphertext = await capture_policy_state(conn, org, sequence=sequence, **policy_trust)
                authority.publish_policy(store, previous, current, ciphertext,
                                         verification_key=policy_key, encryption_key=policy_cipher)

            await update_policy(2)
            with pytest.raises(RestoreBlocked, match="blocked by current policy or evidence"):
                await replay()
            assert not await pool.fetchval("SELECT EXISTS(SELECT FROM gdpr_erasure_barriers WHERE organization_id=$1 AND operation_id=$2)", org, operation)
            if scope == "organization":
                await assert_shared_access(pool, {org, foreign})
            async with env["pool"].acquire() as conn, conn.transaction():
                await hold_event(conn, org, later_hold, "reviewed")
                await hold_event(conn, org, later_hold, "released")
            await update_policy(3)
        assert (await replay())["traffic_authorized"] is False
        assert await pool.fetchval("SELECT count(*) FROM gdpr_retention_hold_events WHERE organization_id=$1", org) == (4 if published_v5 else 2)
        proof = await verify()
        assert proof["verified_erasures"] == 1 and proof["traffic_authorized"] is False
        assert await pool.fetchval("SELECT messages_used_this_period FROM organizations WHERE id=$1", org) == before
        await replay()
        assert (await verify())["verified_erasures"] == 1
        independent = await verify_independent([org, foreign])
        assert independent["traffic_authorized"] is False
        assert independent["verified_organizations"] == 2
        assert await pool.fetchval("SELECT content FROM messages WHERE id=$1", foreign_message) == foreign_original
        if scope == "organization":
            await assert_shared_access(pool, {foreign})
        with pytest.raises(RestoreBlocked):
            await verify_independent([org])
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.migration_limit(97)
@pytest.mark.parametrize("published_v5", [False, True], ids=["v3", "v5"])
async def test_ordinary_retention_is_reapplied_after_older_encrypted_snapshot(restore_target, state_env, tmp_path, published_v5):
    container, dsn, principal, source_db = restore_target
    await _verify_restore_target(container)
    env = state_env
    pool, org, conversation, source = (env[key] for key in ("pool", "org", "conv", "message"))
    request, conclusion = await historical_fence(env)
    await upgrade_fence_retention(pool)
    await approve_messages(pool, org)
    await approve_technical_receipts(pool, org)
    plaintext = await _docker("exec", container, "pg_dump", "-U", principal, "-d", source_db,
                              "--no-owner", "--no-privileges", "--format=plain")
    cipher = Fernet(Fernet.generate_key())
    encrypted_snapshot = cipher.encrypt(plaintext)
    digest = sha256(plaintext).digest()
    del plaintext

    person = await subject(env)
    checkpoint, key, receipt_checkpoint_sha = signed_prune_checkpoint(org, [(request, source, conversation, person)])
    # Simulate the independent encrypted recovery store before any fence
    # deletion, then load the authenticated record back from that store.
    journal_key = Fernet.generate_key()
    journal_path = tmp_path / "receipt-checkpoint.enc"
    journal_trust = dict(verification_key=key, backup_id="synthetic-retention-backup",
                         minimum_sequence=1, checkpoint_sha256=receipt_checkpoint_sha, encryption_key=journal_key)
    assert retain_receipt_prune_checkpoint(journal_path, org, checkpoint, **journal_trust) == receipt_checkpoint_sha
    with pytest.raises(RestoreBlocked, match="Unable to retain encrypted"):
        retain_receipt_prune_checkpoint(journal_path, org, checkpoint, **journal_trust)
    checkpoint = json.loads(Fernet(journal_key).decrypt(journal_path.read_bytes()))
    installation = uuid4()
    object_root, authority_root = tmp_path / "journal", tmp_path / "operator"
    object_root.mkdir(mode=0o700)
    authority_root.mkdir(mode=0o700)
    store = LocalJournalStore(object_root)
    authority = IndependentTipStore.initialize(authority_root / "tip.sqlite", installation)
    signer = Ed25519PrivateKey.generate()
    policy_key, policy_cipher = secrets.token_bytes(32), Fernet.generate_key()
    from src.core.gdpr.source_checkpoint import bind_inventory_covered_receipts, capture_committed_checkpoint

    async def expire(target):
        assert await target.fetchval("SELECT c24_purge_source($1,$2)", org, source)
        assert await target.fetchval("SELECT c24_cleanup_receipt($1,$2)", org, request)
        assert not await target.fetchval("SELECT c24_cleanup_suppression($1,$2)", org, conversation)
        async with target.acquire() as conn, conn.transaction(readonly=True):
            with pytest.raises(RestoreBlocked, match="Pending receipt"):
                await capture_committed_checkpoint(conn, authority, store, org, "pending-backup",
                    sequence=1, verification_key=key, encryption_key=journal_key, pending_requests=[uuid4()])
            captured_pending = await capture_committed_checkpoint(conn, authority, store, org, "pending-backup",
                sequence=1, verification_key=key, encryption_key=journal_key, pending_requests=[request])
            assert captured_pending["receipt_prunes"] == checkpoint["payload"]["receipt_prunes"]
        from datetime import datetime, timezone
        inventory = dict(version=1, installation_id=str(installation), backups=[])
        pins = {}
        for number, backup in enumerate(("pending-backup-a", "pending-backup-b"), 1):
            candidate = dict(captured_pending, backup_id=backup)
            if number == 2:
                candidate["receipt_prunes"] = []
            pin = publish_checkpoint(store, installation, candidate, verification_key=key, encryption_key=journal_key)
            authority.advance_checkpoint(store, None, pin, verification_key=key, encryption_key=journal_key)
            pins[backup] = pin
            async with target.acquire() as conn, conn.transaction(readonly=True):
                policy_pin, policy_data = await capture_policy_state(conn, org, installation_id=installation,
                    backup_id=backup, sequence=1, verification_key=policy_key, encryption_key=policy_cipher)
            authority.publish_policy(store, None, policy_pin, policy_data,
                verification_key=policy_key, encryption_key=policy_cipher)
            inventory["backups"].append(dict(backup_id=backup, manifest_sha256=str(number)*64,
                completed_at=datetime.now(timezone.utc).isoformat(), organizations=[str(org)],
                anchor=dict(sequence=0, sha256="0"*64), exception=None))
        inventory_bytes = json.dumps(inventory).encode()
        coverage_trust = dict(latest_inventory_sha256=sha256(inventory_bytes).hexdigest(),
            trusted_public_key=signer.public_key(), journal_encryption_key=journal_key,
            checkpoint_verification_key=key, checkpoint_encryption_key=journal_key,
            policy_verification_key=policy_key, policy_encryption_key=policy_cipher)
        async with target.acquire() as conn, conn.transaction():
            with pytest.raises(RestoreBlocked, match="differ across recoverable backups"):
                await bind_inventory_covered_receipts(conn, authority, store, org, inventory_bytes, **coverage_trust)
            assert await conn.fetchval("SELECT count(*) FROM c24_receipt_prune_checkpoint_bindings WHERE organization_id=$1", org) == 0
        previous = pins["pending-backup-b"]
        updated = publish_checkpoint(store, installation, dict(captured_pending, backup_id=previous.backup_id, sequence=2),
            verification_key=key, encryption_key=journal_key, previous_pin=previous)
        authority.advance_checkpoint(store, previous, updated, verification_key=key, encryption_key=journal_key)
        async with target.acquire() as conn, conn.transaction():
            assert await bind_inventory_covered_receipts(conn, authority, store, org, inventory_bytes, **coverage_trust) == 1
        async with target.acquire() as conn, conn.transaction():
            assert await bind_retained_receipt_prune_checkpoint(conn, org, journal_path, **journal_trust) == 1
        assert await target.fetchval("SELECT c24_cleanup_suppression($1,$2)", org, conversation)
        audit, recorded_conclusion = await target.fetchrow(
            """SELECT audit_id,c24_concluded_at FROM c24_suppression_lifecycle_audit
               WHERE organization_id=$1 AND source_record_id=$2""",
            org, request,
        )
        assert recorded_conclusion == conclusion
        assert await target.fetchval("SELECT c24_cleanup_suppression_audit($1,$2)", org, audit)

    async def reconciled(target):
        return await target.fetchval(
            """SELECT m.content_purged_at IS NOT NULL AND m.ai_reply_cache IS NULL
                 AND NOT EXISTS(SELECT FROM simulation_requests s WHERE s.organization_id=m.organization_id
                    AND s.c24_source_message_id=m.id AND s.response IS NOT NULL AND s.response<>'{}'::jsonb)
                 AND NOT EXISTS(SELECT FROM booking_conversation_requests r
                    WHERE r.organization_id=m.organization_id AND r.request_id=$3)
                 AND NOT EXISTS(SELECT FROM c24_source_fences f
                    WHERE f.organization_id=m.organization_id AND f.request_id=$3)
                 AND NOT EXISTS(SELECT FROM c24_suppression_lifecycle_audit a
                    WHERE a.organization_id=m.organization_id AND a.source_record_id=$3)
               FROM messages m WHERE m.organization_id=$1 AND m.id=$2""",
            org, source, request,
        )

    await expire(pool)
    assert await reconciled(pool)
    async def verify(target, evidence, pinned_sha):
        async with target.acquire() as conn, conn.transaction():
            return await verify_restore_checkpoint(
                conn, org, evidence, verification_key=key,
                backup_id="synthetic-retention-backup", minimum_sequence=1, checkpoint_sha256=pinned_sha,
            )

    before = await verify(pool, checkpoint, receipt_checkpoint_sha)
    assert before["traffic_authorized"] is False
    omitted, _, omitted_sha = signed_prune_checkpoint(org, [], key=key, sequence=2)
    with pytest.raises(RestoreBlocked, match="commitments do not match"):
        await verify(pool, omitted, omitted_sha)
    anchor = authority.current()
    pin = publish_checkpoint(store, installation, checkpoint["payload"],
                             verification_key=key, encryption_key=journal_key)
    authority.advance_checkpoint(store, None, pin, verification_key=key, encryption_key=journal_key)
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        with pytest.raises(RestoreBlocked, match="historical receipt tuple"):
            await capture_committed_checkpoint(conn, authority, store, org, "next-backup",
                sequence=2, verification_key=key, encryption_key=journal_key)
        captured = await capture_committed_checkpoint(conn, authority, store, org, "next-backup",
            sequence=2, verification_key=key, encryption_key=journal_key, prior_pin=pin)
        assert captured["receipt_prunes"] == checkpoint["payload"]["receipt_prunes"]
        policy_pin, sealed_policy = await capture_policy_state(
            conn, org, installation_id=installation, backup_id=pin.backup_id,
            sequence=1, verification_key=policy_key, encryption_key=policy_cipher)
    authority.publish_policy(store, None, policy_pin, sealed_policy,
                             verification_key=policy_key, encryption_key=policy_cipher)
    inventory, inventory_digest = (None, None)
    if published_v5:
        inventory, inventory_digest = _promote_restored_backup(authority, store, backup_id=pin.backup_id,
            anchor=anchor, organizations=[org], encrypted_backup=encrypted_snapshot, signer=signer,
            journal_key=journal_key, checkpoint_key=key, policy_key=policy_key, policy_cipher=policy_cipher)
    restored_db = "melpis_c24_restore_dst_" + uuid4().hex[:12]
    await _docker("exec", container, "createdb", "-U", principal, restored_db)
    await _docker("exec", container, "psql", "-U", principal, "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                  "-c", f'REVOKE CONNECT ON DATABASE "{restored_db}" FROM PUBLIC')
    restored_sql = cipher.decrypt(encrypted_snapshot)
    assert sha256(restored_sql).digest() == digest
    await _docker("exec", "-i", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
                  "-U", principal, "-d", restored_db, input_bytes=restored_sql)
    del restored_sql
    restored = await asyncpg.create_pool(dsn.rsplit("/", 1)[0] + "/" + restored_db, min_size=1, max_size=1)
    try:
        assert not await reconciled(restored)
        with pytest.raises(RestoreBlocked, match="Receipt expiry commitments do not match"):
            await verify(restored, checkpoint, receipt_checkpoint_sha)
        legacy = dict(checkpoint["payload"], version="c24-restore-v2")
        del legacy["receipt_prunes"]
        canonical = json.dumps(legacy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        legacy_checkpoint = {"payload": legacy, "signature": hmac.new(key, canonical, sha256).hexdigest()}
        with pytest.raises(RestoreBlocked, match="Authenticated receipt expiry checkpoint required"):
            await verify(restored, legacy_checkpoint, None)
        from src.core.gdpr.restore_coordinator import replay_primary_published_erasures

        async def replay():
            return await replay_primary_published_erasures(
                restored, authority, store, backup_checkpoint=anchor, backup_id=pin.backup_id,
                expected_organizations=[org], trusted_public_key=signer.public_key(),
                journal_encryption_key=journal_key, checkpoint_verification_key=key,
                checkpoint_encryption_key=journal_key, quarantine_database=restored_db,
                policy_verification_key=policy_key, policy_encryption_key=policy_cipher,
                ordinary_inventory_bytes=inventory, latest_inventory_sha256=inventory_digest)
        assert (await replay())["traffic_authorized"] is False
        assert await reconciled(restored)
        assert await verify(restored, checkpoint, receipt_checkpoint_sha) == before
        assert (await replay())["traffic_authorized"] is False
        assert not await restored.fetchval("SELECT c24_cleanup_suppression($1,$2)", org, conversation)
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="delivery identity is immutable"):
            await restored.execute("UPDATE messages SET wam_id=NULL WHERE organization_id=$1 AND id=$2", org, source)
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("published_v5", "conversation_linked"),
                         [(False, False), (True, False), (True, True)],
                         ids=["v4", "v5", "v5-conversation"])
async def test_managed_ordinary_booking_older_backup_replay(restore_target, pg_pool, tmp_path, monkeypatch,
                                                         published_v5, conversation_linked):
    from src.core.gdpr.restore_coordinator import replay_primary_published_erasures, verify_paged_restore_candidates
    from src.core.gdpr.source_checkpoint import (
        capture_committed_checkpoint, capture_due_ordinary_bookings, ordinary_rehearsal_statement,
        purge_inventory_covered_ordinary_bookings,
    )
    from tests.core.gdpr.test_ordinary_booking_retention_plan import _candidate

    container, dsn, principal, source_db = restore_target
    await _verify_restore_target(container)
    backup_cipher = Fernet(Fernet.generate_key())
    early_backup = None

    async def dump_image():
        plaintext = await _docker("exec", container, "pg_dump", "-U", principal, "-d", source_db,
                                  "--no-owner", "--no-privileges", "--format=plain")
        return backup_cipher.encrypt(plaintext), sha256(plaintext).digest()

    async def capture_early_backup():
        nonlocal early_backup
        early_backup = await dump_image()

    env, booking, request_ids = await _candidate(
        pg_pool, conversation_linked=conversation_linked,
        before_completion=capture_early_backup if conversation_linked else None)
    org = env.org
    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED","true")
    installation = uuid4()
    objects, pins = tmp_path/"objects", tmp_path/"pins"
    objects.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    store=LocalJournalStore(objects)
    authority=IndependentTipStore.initialize(pins/"authority.sqlite",installation)
    signer=Ed25519PrivateKey.generate()
    key,checkpoint_cipher,journal_key=secrets.token_bytes(32),Fernet.generate_key(),Fernet.generate_key()
    policy_key,policy_cipher=secrets.token_bytes(32),Fernet.generate_key()
    backup_id="synthetic-ordinary-v4"
    anchor=authority.current()
    async with pg_pool.acquire() as conn, conn.transaction(readonly=True):
        due, cursor = await capture_due_ordinary_bookings(conn, org, limit=1)
        assert due == (booking,) and cursor is None
        payload=await capture_committed_checkpoint(conn,authority,store,org,backup_id,
            sequence=1,verification_key=key,encryption_key=checkpoint_cipher,pending_ordinary_bookings=due)
        policy_pin,policy_data=await capture_policy_state(conn,org,installation_id=installation,
            backup_id=backup_id,sequence=1,verification_key=policy_key,encryption_key=policy_cipher)
    assert payload["version"]=="c24-restore-v4"
    assert payload["ordinary_booking_purges"][0]["request_ids"]==sorted(map(str,request_ids))
    conversation = payload["ordinary_booking_purges"][0].get("conversation_id")
    assert (conversation is not None) == conversation_linked
    encrypted, plain_digest = await dump_image()

    def inventory_for(image):
        return json.dumps(dict(version=1, installation_id=str(installation), backups=[
            dict(backup_id=backup_id, manifest_sha256=sha256(image).hexdigest(),
                 completed_at=datetime.now(timezone.utc).isoformat(), organizations=[str(org)],
                 anchor=dict(sequence=anchor.sequence, sha256=anchor.sha256), exception=None)])).encode()

    inventory = inventory_for(encrypted)
    inventory_digest=sha256(inventory).hexdigest()
    pin=publish_checkpoint(store,installation,payload,verification_key=key,encryption_key=checkpoint_cipher)
    authority.advance_checkpoint(store,None,pin,verification_key=key,encryption_key=checkpoint_cipher)
    authority.publish_policy(store,None,policy_pin,policy_data,verification_key=policy_key,encryption_key=policy_cipher)
    trust=dict(trusted_public_key=signer.public_key(),journal_encryption_key=journal_key,
        checkpoint_verification_key=key,checkpoint_encryption_key=checkpoint_cipher,
        policy_verification_key=policy_key,policy_encryption_key=policy_cipher)

    async def restore_backup_image(image=encrypted, digest=plain_digest, inventory_bytes=inventory):
        assert sha256(image).hexdigest() == json.loads(inventory_bytes)["backups"][0]["manifest_sha256"]
        target = "melpis_c24_restore_dst_" + uuid4().hex[:12]
        await _docker("exec", container, "createdb", "-U", principal, target)
        await _docker("exec", container, "psql", "-U", principal, "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                      "-c", f'REVOKE CONNECT ON DATABASE "{target}" FROM PUBLIC')
        plaintext = backup_cipher.decrypt(image)
        assert sha256(plaintext).digest() == digest
        await _docker("exec", "-i", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
                      "-U", principal, "-d", target, input_bytes=plaintext)
        return target

    async with pg_pool.acquire() as conn, conn.transaction():
        with pytest.raises(RestoreBlocked, match="rehearsal required"):
            await purge_inventory_covered_ordinary_bookings(
                conn, authority, store, org, inventory, latest_inventory_sha256=inventory_digest, **trust)
    assert await pg_pool.fetchval("SELECT EXISTS(SELECT FROM bookings WHERE organization_id=$1 AND id=$2)", org, booking)

    if early_backup is not None:
        early_inventory = inventory_for(early_backup[0])
        early_db = await restore_backup_image(*early_backup, early_inventory)
        try:
            early_pool = await asyncpg.create_pool(dsn.rsplit("/", 1)[0] + "/" + early_db, min_size=1, max_size=1)
            try:
                assert await early_pool.fetchval(
                    "SELECT EXISTS(SELECT FROM bookings WHERE organization_id=$1 AND id=$2)", org, booking)
                assert not await early_pool.fetchval(
                    """SELECT EXISTS(SELECT FROM ordinary_booking_completion_evidence
                    WHERE organization_id=$1 AND booking_id=$2)""", org, booking)
                with pytest.raises(RestoreBlocked, match="binding rejected"):
                    await replay_primary_published_erasures(
                        early_pool, authority, store, backup_checkpoint=anchor, backup_id=backup_id,
                        expected_organizations=[org], quarantine_database=early_db,
                        ordinary_inventory_bytes=early_inventory,
                        latest_inventory_sha256=sha256(early_inventory).hexdigest(), **trust)
                assert await early_pool.fetchval("SELECT count(*) FROM ordinary_booking_purge_bindings") == 0
            finally:
                await early_pool.close()
        finally:
            await _docker("exec", container, "dropdb", "-U", principal, early_db)
        async with pg_pool.acquire() as conn, conn.transaction():
            with pytest.raises(RestoreBlocked, match="rehearsal required"):
                await purge_inventory_covered_ordinary_bookings(
                    conn, authority, store, org, early_inventory,
                    latest_inventory_sha256=sha256(early_inventory).hexdigest(), **trust)

    rehearsal_db = await restore_backup_image()
    try:
        rehearsal = await asyncpg.create_pool(dsn.rsplit("/", 1)[0] + "/" + rehearsal_db, min_size=1, max_size=1)
        try:
            result = await replay_primary_published_erasures(
                rehearsal, authority, store, backup_checkpoint=anchor, backup_id=backup_id,
                expected_organizations=[org], quarantine_database=rehearsal_db,
                ordinary_inventory_bytes=inventory, latest_inventory_sha256=inventory_digest, **trust)
            assert result["traffic_authorized"] is False
            assert not await rehearsal.fetchval(
                "SELECT EXISTS(SELECT FROM bookings WHERE organization_id=$1 AND id=$2)", org, booking)
        finally:
            await rehearsal.close()
    finally:
        await _docker("exec", container, "dropdb", "-U", principal, rehearsal_db)

    statement_args = (org, inventory_digest, anchor, {backup_id: (pin, policy_pin)})
    rehearsal_signature = signer.sign(ordinary_rehearsal_statement(*statement_args))
    wrong_inventory_signature = signer.sign(ordinary_rehearsal_statement(org, "0"*64, anchor, {backup_id: (pin, policy_pin)}))
    omitted_backup_signature = signer.sign(ordinary_rehearsal_statement(org, inventory_digest, anchor, {}))
    async with pg_pool.acquire() as conn, conn.transaction():
        with pytest.raises(RestoreBlocked, match="signature invalid"):
            await purge_inventory_covered_ordinary_bookings(
                conn, authority, store, org, inventory, latest_inventory_sha256=inventory_digest,
                rehearsal_proof=rehearsal_signature, **trust)
    authority.approve_segment_epoch(anchor, signer.public_key(), journal_key)
    async with pg_pool.acquire() as conn, conn.transaction():
        for invalid in (wrong_inventory_signature, omitted_backup_signature):
            with pytest.raises(RestoreBlocked, match="signature invalid"):
                await purge_inventory_covered_ordinary_bookings(
                    conn, authority, store, org, inventory, latest_inventory_sha256=inventory_digest,
                    rehearsal_proof=invalid, **trust)
        assert await purge_inventory_covered_ordinary_bookings(conn,authority,store,org,inventory,
            latest_inventory_sha256=inventory_digest,rehearsal_proof=rehearsal_signature,**trust)==1
    assert not await pg_pool.fetchval("SELECT EXISTS(SELECT FROM bookings WHERE organization_id=$1 AND id=$2)",org,booking)
    async with pg_pool.acquire() as conn,conn.transaction(readonly=True):
        committed=await capture_committed_checkpoint(conn,authority,store,org,backup_id,
            sequence=2,verification_key=key,encryption_key=checkpoint_cipher,prior_pin=pin)
        paged = await capture_committed_checkpoint(conn, authority, store, org, backup_id, sequence=2,
            verification_key=key, encryption_key=checkpoint_cipher, prior_pin=pin, paged_candidate=True)
    assert committed["ordinary_booking_purges"]==payload["ordinary_booking_purges"]
    assert paged == dict(committed, version="c24-restore-v5")
    candidate = publish_checkpoint_pages(store, installation, paged,
        verification_key=key, encryption_key=checkpoint_cipher, previous_pin=pin)
    if published_v5:
        authority.promote_paged_checkpoints(store, inventory, latest_inventory_sha256=inventory_digest,
                                           candidates={(org, backup_id): candidate}, **trust)
        assert authority.checkpoint_pin(org, backup_id) == candidate
    restored_db = await restore_backup_image()
    restored=await asyncpg.create_pool(dsn.rsplit("/",1)[0]+"/"+restored_db,min_size=1,max_size=1)
    try:
        assert await restored.fetchval("SELECT EXISTS(SELECT FROM bookings WHERE organization_id=$1 AND id=$2)",org,booking)
        replay_args=dict(backup_checkpoint=anchor,backup_id=backup_id,expected_organizations=[org],
                         quarantine_database=restored_db,ordinary_inventory_bytes=inventory,
                         latest_inventory_sha256=inventory_digest,**trust)
        candidate_args = dict(backup_checkpoint=anchor, backup_id=backup_id, expected_organizations=[org],
            quarantine_database=restored_db, candidates={org: candidate}, **{
                name: value for name, value in trust.items() if not name.startswith("policy_")})
        async with restored.acquire() as conn, conn.transaction(readonly=True):
            with pytest.raises(RestoreBlocked):
                await verify_paged_restore_candidates(conn, authority, store, **candidate_args)
        with pytest.raises(RestoreBlocked):
            await replay_primary_published_erasures(restored,authority,store,**dict(replay_args,latest_inventory_sha256="0"*64))
        assert await restored.fetchval("SELECT EXISTS(SELECT FROM bookings WHERE organization_id=$1 AND id=$2)",org,booking)
        for _ in range(2):
            assert (await replay_primary_published_erasures(restored,authority,store,**replay_args))["traffic_authorized"] is False
        assert not await restored.fetchval("SELECT EXISTS(SELECT FROM bookings WHERE organization_id=$1 AND id=$2)",org,booking)
        assert await restored.fetchval("SELECT count(*) FROM booking_stay_tombstones WHERE organization_id=$1 AND booking_id=$2",org,booking)==len(request_ids)
        if conversation_linked:
            assert await restored.fetchval("SELECT EXISTS(SELECT FROM conversations WHERE organization_id=$1 AND id=$2)", org, conversation)
        if not published_v5:
            async with restored.acquire() as conn, conn.transaction(readonly=True):
                proof = await verify_paged_restore_candidates(conn, authority, store, **candidate_args)
                assert proof["verified_paged_candidates"] == 1 and proof["traffic_authorized"] is False
                assert proof["authority_advanced"] is False
                missing = dict(committed, version="c24-restore-v5", sequence=3, erasures=[
                    dict(operation_id=str(uuid4()), scope="organization", subject_id=None)])
                missing_pin = publish_checkpoint_pages(store, installation, missing,
                    verification_key=key, encryption_key=checkpoint_cipher, previous_pin=pin)
                with pytest.raises(RestoreBlocked, match="Newer erasure journal has not been replayed"):
                    await verify_paged_restore_candidates(conn, authority, store,
                        **dict(candidate_args, candidates={org: missing_pin}))
            assert authority.checkpoint_pin(org, backup_id) == pin
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("history", ["with-preimage", "before-source", "later-erasure"])
async def test_verified_orphan_proposal_is_erased_on_source_and_replayed_from_v5(
    restore_target, state_env, tmp_path, monkeypatch, history,
):
    from src.core.gdpr.orphan_proposal_expiry import expire_inventory_covered_orphan_proposals
    from src.core.gdpr.restore_coordinator import replay_primary_published_erasures
    from src.core.gdpr.source_checkpoint import capture_committed_checkpoint
    from tests.core.gdpr.test_orphan_proposal_plan import _known_orphan

    container, dsn, principal, source_db = restore_target
    await _verify_restore_target(container)
    env = state_env
    backup_predates_source = history == "before-source"
    pool, org = env["pool"], env["org"]
    original_message = env["message"]
    plain = None
    backup_completed_at = None
    if backup_predates_source:
        plain = await _docker("exec", container, "pg_dump", "-U", principal, "-d", source_db,
                              "--no-owner", "--no-privileges", "--format=plain")
        backup_completed_at = datetime.now(timezone.utc) - timedelta(days=40)
        env["message"] = await _inbound_message(pool, org, canale="whatsapp")
        env["conv"] = await pool.fetchval("SELECT conversation_id FROM messages WHERE organization_id=$1 AND id=$2",
                                         org, env["message"])
    operation = await _known_orphan(env)
    source = env["message"]
    provider_id = await pool.fetchval("SELECT wam_id FROM messages WHERE organization_id=$1 AND id=$2", org, source)
    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED", "true")
    if history == "later-erasure":
        await approve_policies(pool, org)
        await approve_messages(pool, org)
        await approve_technical_receipts(pool, org)
        monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    objects, pins = tmp_path / "objects", tmp_path / "pins"
    objects.mkdir(mode=0o700)
    pins.mkdir(mode=0o700)
    installation = uuid4()
    authority = IndependentTipStore.initialize(pins / "authority.sqlite", installation)
    store = LocalJournalStore(objects)
    signer = Ed25519PrivateKey.generate()
    checkpoint_key, encryption_key, journal_key = secrets.token_bytes(32), Fernet.generate_key(), Fernet.generate_key()
    policy_key, policy_cipher = secrets.token_bytes(32), Fernet.generate_key()
    keys = dict(verification_key=checkpoint_key, encryption_key=encryption_key)
    trust = dict(trusted_public_key=signer.public_key(), journal_encryption_key=journal_key,
        checkpoint_verification_key=checkpoint_key, checkpoint_encryption_key=encryption_key,
        policy_verification_key=policy_key, policy_encryption_key=policy_cipher)
    anchor, backup_id = authority.current(), "synthetic-orphan-older"
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        legacy = await capture_committed_checkpoint(conn, authority, store, org, backup_id, sequence=1, **keys)
        planned = await capture_committed_checkpoint(conn, authority, store, org, backup_id, sequence=2,
            paged_candidate=True, pending_orphan_sources=[source], **keys)
        policy, sealed_policy = await capture_policy_state(conn, org, backup_id=backup_id, sequence=1,
            installation_id=installation, verification_key=policy_key, encryption_key=policy_cipher)
    assert len(planned["orphan_proposal_prunes"]) == 1
    if plain is None:
        plain = await _docker("exec", container, "pg_dump", "-U", principal, "-d", source_db,
                              "--no-owner", "--no-privileges", "--format=plain")
        backup_completed_at = datetime.now(timezone.utc)
    backup_cipher = Fernet(Fernet.generate_key())
    encrypted = backup_cipher.encrypt(plain)
    del plain
    inventory = json.dumps(dict(version=1, installation_id=str(installation), backups=[dict(
        backup_id=backup_id, manifest_sha256=sha256(encrypted).hexdigest(), completed_at=backup_completed_at.isoformat(),
        organizations=[str(org)], anchor=dict(sequence=anchor.sequence, sha256=anchor.sha256), exception=None)])).encode()
    digest = sha256(inventory).hexdigest()
    old = publish_checkpoint(store, installation, legacy, **keys)
    authority.advance_checkpoint(store, None, old, **keys)
    authority.publish_policy(store, None, policy, sealed_policy, verification_key=policy_key, encryption_key=policy_cipher)
    paged = publish_checkpoint_pages(store, installation, planned, previous_pin=old, **keys)
    authority.promote_paged_checkpoints(store, inventory, latest_inventory_sha256=digest,
        candidates={(org, backup_id): paged}, **trust)

    async def assert_expired(database):
        row = await database.fetchrow("""SELECT content_text,billed_at,ai_reply_cache ? 'booking_state_proposal' AS proposal
            FROM messages WHERE organization_id=$1 AND id=$2""", org, source)
        assert row["content_text"] and row["billed_at"] and row["proposal"] is False
        assert await database.fetchval("""SELECT response ? 'booking_state_proposal' FROM simulation_requests
            WHERE organization_id=$1 AND request_id=$2""", org, operation.id) is False
        assert await database.fetchval("SELECT messages_used_this_period FROM organizations WHERE id=$1", org) == 1

    async with pool.acquire() as conn, conn.transaction():
        assert await expire_inventory_covered_orphan_proposals(conn, authority, store, org, inventory,
            latest_inventory_sha256=digest, **trust) == 1
    await assert_expired(pool)
    erasure = None
    if history == "later-erasure":
        requester = "synthetic-orphan-erasure"
        erasure = await Operations(pool).create(org, requester, uuid4(), kind="erase", scope="contact",
                                                subject_id=await subject(env))
        await Deletion(pool).advance(org, erasure, requester)
        async with pool.acquire() as conn, conn.transaction(readonly=True):
            journal_tip, published, remaining = await publish_source_erasure_batch(
                conn, authority, store, expected_organizations=[org], signer=signer,
                journal_encryption_key=journal_key,
            )
        assert (journal_tip, published, remaining) == (authority.current(), 1, 0)
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        captured = await capture_committed_checkpoint(conn, authority, store, org, backup_id,
            sequence=3, paged_candidate=True, prior_pin=paged, **keys)
        assert captured["orphan_proposal_prunes"] == planned["orphan_proposal_prunes"]
    if erasure is not None:
        successor = publish_checkpoint_pages(store, installation, captured, previous_pin=paged, **keys)
        authority.promote_paged_checkpoints(store, inventory, latest_inventory_sha256=digest,
            candidates={(org, backup_id): successor}, **trust)
    # The independently pinned policy renews after the original expiry. The
    # historical evidence stays byte-for-byte immutable.
    await pool.execute("""INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
        retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
        VALUES($1,'booking_conversation_content','synthetic-renewal',true,30,false,'qa:renewal','qa',
               clock_timestamp(),clock_timestamp()+interval '7 days')""", org)
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        renewed, renewed_data = await capture_policy_state(conn, org, backup_id=backup_id, sequence=2,
            installation_id=installation, verification_key=policy_key, encryption_key=policy_cipher)
    authority.publish_policy(store, policy, renewed, renewed_data, verification_key=policy_key, encryption_key=policy_cipher)
    restored_db = "melpis_c24_restore_dst_" + uuid4().hex[:12]
    await _docker("exec", container, "createdb", "-U", principal, restored_db)
    await _docker("exec", container, "psql", "-U", principal, "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                  "-c", f'REVOKE CONNECT ON DATABASE "{restored_db}" FROM PUBLIC')
    await _docker("exec", "-i", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
                  "-U", principal, "-d", restored_db, input_bytes=backup_cipher.decrypt(encrypted))
    restored = await asyncpg.create_pool(dsn.rsplit("/", 1)[0] + "/" + restored_db, min_size=1, max_size=1)
    try:
        if not backup_predates_source:
            assert await restored.fetchval("""SELECT ai_reply_cache ? 'booking_state_proposal' FROM messages
                WHERE organization_id=$1 AND id=$2""", org, source)
        for _ in range(2):
            proof = await replay_primary_published_erasures(restored, authority, store, backup_checkpoint=anchor,
                backup_id=backup_id, expected_organizations=[org], quarantine_database=restored_db,
                ordinary_inventory_bytes=inventory, latest_inventory_sha256=digest, **trust)
            assert proof["traffic_authorized"] is False
        if backup_predates_source:
            assert await restored.fetchval("SELECT count(*) FROM c24_orphan_proposal_expirations WHERE organization_id=$1 AND source_message_id=$2",
                                           org, source) == 1
            assert not await restored.fetchval("SELECT EXISTS(SELECT FROM messages WHERE organization_id=$1 AND id=$2)", org, source)
            assert await restored.fetchval("SELECT messages_used_this_period FROM organizations WHERE id=$1", org) == 0
            with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="Expired orphan proposal"):
                await restored.execute("""UPDATE messages SET wam_id=$3,ai_reply_cache='{"booking_state_proposal":{}}'
                    WHERE organization_id=$1 AND id=$2""", org, original_message, provider_id)
        elif erasure is not None:
            assert await restored.fetchval("SELECT EXISTS(SELECT FROM gdpr_erasure_barriers WHERE organization_id=$1 AND operation_id=$2)",
                                           org, erasure)
            assert await restored.fetchval("SELECT content_text IS NULL AND billed_at IS NOT NULL FROM messages WHERE organization_id=$1 AND id=$2",
                                           org, source)
            assert await restored.fetchval("SELECT messages_used_this_period FROM organizations WHERE id=$1", org) == 1
        else:
            await assert_expired(restored)
    finally:
        await restored.close()
