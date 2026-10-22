"""Opt-in encrypted backup/restore proof against the isolated A06 test database.

This proves one coherent snapshot can be encrypted, restored and checked. It is
not evidence that an older backup includes newer erasure journals or is safe to
restore without the separate quarantine/reconciliation gate.
"""
import hashlib
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import asyncpg
import pytest
from cryptography.fernet import Fernet

from src.core.gdpr.guard import ErasureBlocked, check_contact_intake, identifier_fingerprint


_EXPECTED_CONTAINER = "melpis-a06-pg-20261022"
_EXPECTED_DSN = "postgresql://test:test@127.0.0.1:55435/melpis_a06_test"


def _docker(*args, input_bytes=None):
    result = subprocess.run(
        ["docker", *args], input=input_bytes, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        # Deliberately do not include stdout/stderr: those streams may contain
        # SQL, backup bytes, paths or connection details.
        pytest.fail("synthetic backup/restore container command failed")
    return result.stdout


@pytest.mark.asyncio
async def test_encrypted_synthetic_pg_backup_restore_preserves_barrier_billing_and_export(
    pg_pool, sample_org, monkeypatch,
):
    if os.getenv("GDPR_BACKUP_RESTORE_TEST") != "1":
        pytest.skip("set GDPR_BACKUP_RESTORE_TEST=1 for the isolated local backup/restore proof")
    if os.getenv("GDPR_BACKUP_RESTORE_CONTAINER") != _EXPECTED_CONTAINER:
        pytest.fail("backup/restore test requires the exact named synthetic container opt-in")
    if os.getenv("TEST_DB_DSN") != _EXPECTED_DSN:
        pytest.fail("backup/restore test is restricted to the named synthetic source database")

    container_name = _docker(
        "inspect", "--format", "{{.Name}}", _EXPECTED_CONTAINER,
    ).decode("ascii").strip()
    assert container_name == "/" + _EXPECTED_CONTAINER

    org, contact_id, operation_id, export_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    phone = "+15550009991"
    fernet_key = Fernet.generate_key()
    fernet = Fernet(fernet_key)
    monkeypatch.setenv("ENCRYPTION_KEY", fernet_key.decode("ascii"))
    digest, key_id = identifier_fingerprint(org, phone)
    export_plaintext = b'{"synthetic":"encrypted-export-proof","scope":"organization"}'
    encrypted_chunk = fernet.encrypt(export_plaintext)

    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO organizations(id,name) VALUES($1,'Synthetic backup proof')", org,
        )
        await conn.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
            contact_id, org, phone,
        )
        await conn.execute(
            """INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,
                   kind,scope,status,manifest)
               VALUES($1,$2,'backup-proof',$3,$4,'erase','contact','partial','{}')""",
            operation_id, org, uuid.uuid4(), hashlib.sha256(b"synthetic erase request").hexdigest(),
        )
        await conn.execute(
            """INSERT INTO gdpr_operation_steps(organization_id,operation_id,name,status,attempts)
               VALUES($1,$2,'local','confirmed',1)""", org, operation_id,
        )
        await conn.execute(
            """INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope,subject_id,
                   identifier_digest,key_id) VALUES($1,$2,'contact',$3,$4,$5)""",
            org, operation_id, contact_id, digest, key_id,
        )
        await conn.execute(
            "UPDATE organizations SET messages_used_this_period=37 WHERE id=$1", org,
        )
        await conn.execute(
            "INSERT INTO usage_events(organization_id,event_type,quantity,metadata) "
            "VALUES($1,'synthetic.backup.billing',37,'{}')", org,
        )
        await conn.execute(
            """INSERT INTO gdpr_operations(id,organization_id,requester,request_id,request_hash,
                   kind,scope,status,manifest,expires_at)
               VALUES($1,$2,'backup-proof',$3,$4,'export','organization','ready',
                      '{"chunks":1,"bytes":64}',NOW()+INTERVAL '15 minutes')""",
            export_id, org, uuid.uuid4(), hashlib.sha256(b"synthetic export request").hexdigest(),
        )
        await conn.execute(
            """INSERT INTO gdpr_export_chunks(organization_id,operation_id,sequence,ciphertext,digest)
               VALUES($1,$2,0,$3,$4)""",
            org, export_id, encrypted_chunk, hashlib.sha256(encrypted_chunk).hexdigest(),
        )

    source_dump = _docker(
        "exec", _EXPECTED_CONTAINER, "pg_dump", "-U", "test", "-d", "melpis_a06_test",
        "--format=plain", "--no-owner", "--no-privileges",
    )
    assert source_dump
    encrypted_backup = fernet.encrypt(source_dump)
    assert b"Synthetic backup proof" not in encrypted_backup
    assert phone.encode("ascii") not in encrypted_backup
    assert fernet_key not in encrypted_backup

    restore_db = "melpis_a06_restore_" + uuid.uuid4().hex[:16]
    assert restore_db.startswith("melpis_a06_restore_")
    assert all(char.isalnum() or char == "_" for char in restore_db)

    with tempfile.NamedTemporaryFile(prefix="gdpr-a06-", suffix=".fernet", delete=False) as backup_file:
        backup_path = Path(backup_file.name)
        backup_file.write(encrypted_backup)
    try:
        encrypted_on_disk = backup_path.read_bytes()
        assert encrypted_on_disk == encrypted_backup
        assert source_dump[:128] not in encrypted_on_disk
        assert phone.encode("ascii") not in encrypted_on_disk
        assert fernet_key not in encrypted_on_disk
        restored_dump = fernet.decrypt(encrypted_on_disk)
        assert restored_dump == source_dump

        _docker(
            "exec", "-e", "PGPASSWORD=test", _EXPECTED_CONTAINER,
            "createdb", "-U", "test", restore_db,
        )
        _docker(
            "exec", "-i", "-e", "PGPASSWORD=test", _EXPECTED_CONTAINER,
            "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "test", "-d", restore_db,
            input_bytes=restored_dump,
        )
    finally:
        backup_path.unlink(missing_ok=True)

    restore_dsn = _EXPECTED_DSN.rsplit("/", 1)[0] + "/" + restore_db
    restored = await asyncpg.connect(restore_dsn)
    try:
        assert await restored.fetchval(
            "SELECT messages_used_this_period FROM organizations WHERE id=$1", org,
        ) == 37
        assert await restored.fetchval(
            "SELECT quantity FROM usage_events WHERE organization_id=$1 "
            "AND event_type='synthetic.backup.billing'", org,
        ) == 37
        stored_ciphertext = await restored.fetchval(
            "SELECT ciphertext FROM gdpr_export_chunks WHERE organization_id=$1 AND operation_id=$2",
            org, export_id,
        )
        assert fernet.decrypt(stored_ciphertext) == export_plaintext
        with pytest.raises(ErasureBlocked):
            async with restored.transaction():
                await check_contact_intake(restored, org, phone)
    finally:
        await restored.close()

    # Keep the uniquely named synthetic restore database as a non-secret proof
    # artifact. No source or pre-existing database is dropped or modified here.
    print(f"Synthetic restored database retained: {restore_db}")
