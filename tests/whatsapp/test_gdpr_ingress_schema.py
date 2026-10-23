"""Regression coverage for the WhatsApp fixture's GDPR ingress schema."""
import hashlib
import uuid

import pytest
from cryptography.fernet import Fernet

from src.core.gdpr.guard import ErasureBlocked, identifier_fingerprint
from src.core.db.repositories.webhook_inbox_repo import WebhookInboxRepository


@pytest.mark.asyncio
async def test_erased_sender_is_suppressed_before_inbox_or_contact_insert(pg_pool, repo, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    organization_id, other_org = uuid.uuid4(), uuid.uuid4()
    subject_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    request_id = uuid.uuid4()
    phone = "+15550001234"
    digest, key_id = identifier_fingerprint(organization_id, phone)

    async with pg_pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO organizations(id,name) VALUES($1,'Synthetic GDPR fixture')",
            [(organization_id,), (other_org,)],
        )
        await conn.execute(
            """INSERT INTO gdpr_operations
               (id, organization_id, requester, request_id, request_hash, kind, scope, subject_id, status)
               VALUES ($1,$2,'synthetic-ingress-test',$3,$4,'erase','contact',$5,'partial')""",
            operation_id, organization_id, request_id, hashlib.sha256(b"synthetic-ingress").hexdigest(), subject_id,
        )
        await conn.execute(
            """INSERT INTO gdpr_erasure_barriers
               (organization_id, operation_id, scope, subject_id, identifier_digest, key_id)
               VALUES ($1,$2,'contact',$3,$4,$5)""",
            organization_id, operation_id, subject_id, digest, key_id,
        )

    payload = {
        "entry": [{"changes": [{"field": "messages", "value": {
            "messages": [{"from": "15550001234", "id": "erased-wa-message"}],
            "contacts": [{"wa_id": "15550001234", "profile": {"name": "Erased Synthetic"}}],
            "statuses": [],
        }}]}],
    }
    await WebhookInboxRepository(pg_pool).enqueue(
        organization_id, "whatsapp", payload, "trace-erased-wa-fixture",
    )
    with pytest.raises(ErasureBlocked):
        await repo.get_or_create_contact(organization_id, phone)

    # The same identifier in a different tenant is not suppressed.
    await WebhookInboxRepository(pg_pool).enqueue(
        other_org, "whatsapp", payload, "trace-other-wa-fixture",
    )
    accepted_contact = await repo.get_or_create_contact(other_org, phone)
    assert accepted_contact["organization_id"] == other_org

    async with pg_pool.acquire() as conn:
        inbox_count = await conn.fetchval(
            "SELECT count(*) FROM meta_webhook_inbox WHERE organization_id=$1", organization_id,
        )
        contact_count = await conn.fetchval(
            "SELECT count(*) FROM contacts WHERE organization_id=$1 AND phone_number=$2",
            organization_id, phone,
        )
        other_inbox_count = await conn.fetchval(
            "SELECT count(*) FROM meta_webhook_inbox WHERE organization_id=$1", other_org,
        )

    assert inbox_count == 0
    assert contact_count == 0
    assert other_inbox_count == 1
