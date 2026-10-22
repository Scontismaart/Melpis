"""PostgreSQL integration tests for tombstone-aware inbound data handling."""
import json
import uuid

import pytest
from cryptography.fernet import Fernet

from src.core.db.repositories.webhook_inbox_repo import WebhookInboxRepository
from src.core.gdpr.deletion import Deletion
from src.core.gdpr.guard import ErasureBlocked


@pytest.fixture(autouse=True)
def _synthetic_erasure_enabled(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")


async def _approve(pool, org, *, organization=False):
    categories = ["contact_content", "transient_content", "cutover"]
    if organization:
        categories += ["knowledge_content", "organization_profile"]
    async with pool.acquire() as conn:
        for category in categories:
            await conn.execute(
                """INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
                       retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
                   VALUES($1,$2,'ingress-synthetic',TRUE,30,FALSE,'test:ingress','qa-a06',
                          NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 day')""",
                org, category,
            )


async def _contact(pool, org, phone, *, wam_id=None):
    contact, conversation, message = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)", contact, org, phone,
        )
        await conn.execute(
            "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)", conversation, org, contact,
        )
        await conn.execute(
            """INSERT INTO messages(id,organization_id,conversation_id,wam_id,direction,message_type,
                   content,content_text,status,handling_type)
               VALUES($1,$2,$3,$4,'inbound','text','{}'::jsonb,'synthetic contact text','handled','human')""",
            message, org, conversation, wam_id,
        )
    return contact, conversation, message


async def _erase_contact(pool, org, phone, *, requester="ingress-owner", wam_id=None):
    await _approve(pool, org)
    contact, conversation, message = await _contact(pool, org, phone, wam_id=wam_id)
    result = await Deletion(pool).request(
        org, requester, uuid.uuid4(), scope="contact", subject_id=contact,
    )
    assert result["status"] == "partial"
    return contact, conversation, message


def _wa_payload(*, messages=(), contacts=(), statuses=()):
    return {
        "entry": [{"changes": [{"field": "messages", "value": {
            "messages": list(messages), "contacts": list(contacts), "statuses": list(statuses),
        }}]}],
    }


async def _inbox_rows(pool, org):
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id,payload,status FROM meta_webhook_inbox WHERE organization_id=$1 ORDER BY created_at,id",
            org,
        )


@pytest.mark.asyncio
async def test_mixed_whatsapp_batch_filters_erased_sender_and_profiles_but_keeps_valid_contact(
    pg_pool, sample_org,
):
    org = sample_org["id"]
    await _erase_contact(pg_pool, org, "+15550001111")
    inbox = WebhookInboxRepository(pg_pool)
    payload = _wa_payload(
        messages=[{"from": "15550001111", "id": "erased-inbound"},
                  {"from": "15550002222", "id": "valid-inbound"}],
        contacts=[{"wa_id": "15550001111", "profile": {"name": "Erased Synthetic"}},
                  {"wa_id": "15550002222", "profile": {"name": "Valid Synthetic"}}],
    )

    await inbox.enqueue(org, "whatsapp", payload, "trace-mixed")
    rows = await _inbox_rows(pg_pool, org)
    assert len(rows) == 1
    stored = json.loads(rows[0]["payload"]) if isinstance(rows[0]["payload"], str) else rows[0]["payload"]
    value = stored["entry"][0]["changes"][0]["value"]
    assert [message["id"] for message in value["messages"]] == ["valid-inbound"]
    assert [contact["wa_id"] for contact in value["contacts"]] == ["15550002222"]
    assert "Erased Synthetic" not in str(stored)


@pytest.mark.asyncio
async def test_unknown_status_id_uses_erased_recipient_and_preserves_valid_recipient(
    pg_pool, sample_org,
):
    org = sample_org["id"]
    await _erase_contact(pg_pool, org, "+15550003333")
    payload = _wa_payload(statuses=[
        {"id": "unknown-erased-wamid", "recipient_id": "15550003333", "status": "delivered"},
        {"id": "unknown-valid-wamid", "recipient_id": "15550004444", "status": "delivered"},
    ])

    await WebhookInboxRepository(pg_pool).enqueue(org, "whatsapp", payload, "trace-status")
    rows = await _inbox_rows(pg_pool, org)
    assert len(rows) == 1
    stored = rows[0]["payload"] if isinstance(rows[0]["payload"], dict) else json.loads(rows[0]["payload"])
    statuses = stored["entry"][0]["changes"][0]["value"]["statuses"]
    assert [status["id"] for status in statuses] == ["unknown-valid-wamid"]


@pytest.mark.asyncio
async def test_known_wamid_without_recipient_falls_back_to_erased_message_subject(pg_pool, sample_org):
    org = sample_org["id"]
    _, _, _ = await _erase_contact(pg_pool, org, "+15550005555", wam_id="wam-erased-known")
    payload = _wa_payload(statuses=[
        {"id": "wam-erased-known", "status": "read"},
        {"id": "wam-unknown-valid", "status": "read", "recipient_id": "15550006666"},
    ])

    await WebhookInboxRepository(pg_pool).enqueue(org, "whatsapp", payload, "trace-known-wamid")
    rows = await _inbox_rows(pg_pool, org)
    assert len(rows) == 1
    stored = rows[0]["payload"] if isinstance(rows[0]["payload"], dict) else json.loads(rows[0]["payload"])
    statuses = stored["entry"][0]["changes"][0]["value"]["statuses"]
    assert [status["id"] for status in statuses] == ["wam-unknown-valid"]


@pytest.mark.asyncio
async def test_instagram_echo_uses_recipient_and_regular_message_uses_sender(pg_pool, sample_org):
    org = sample_org["id"]
    await _erase_contact(pg_pool, org, "+15550007777")
    payload = {"entry": [{"messaging": [
        {"sender": {"id": "business-account"}, "recipient": {"id": "15550007777"},
         "message": {"mid": "echo-to-erased", "is_echo": True}},
        {"sender": {"id": "15550007777"}, "recipient": {"id": "business-account"},
         "message": {"mid": "inbound-from-erased", "is_echo": False}},
        {"sender": {"id": "business-account"}, "recipient": {"id": "15550008888"},
         "message": {"mid": "echo-to-valid", "is_echo": True}},
        {"sender": {"id": "15550009999"}, "recipient": {"id": "business-account"},
         "message": {"mid": "inbound-from-valid", "is_echo": False}},
    ]}]}

    await WebhookInboxRepository(pg_pool).enqueue(org, "instagram", payload, "trace-instagram")
    rows = await _inbox_rows(pg_pool, org)
    assert len(rows) == 1
    stored = rows[0]["payload"] if isinstance(rows[0]["payload"], dict) else json.loads(rows[0]["payload"])
    ids = [message["message"]["mid"] for message in stored["entry"][0]["messaging"]]
    assert ids == ["echo-to-valid", "inbound-from-valid"]


@pytest.mark.asyncio
async def test_contact_erasure_sanitizes_completed_inbox_row_without_deleting_valid_batch(
    pg_pool, sample_org,
):
    org = sample_org["id"]
    await _approve(pg_pool, org)
    contact, _, _ = await _contact(pg_pool, org, "+15550001234")
    payload = _wa_payload(messages=[
        {"from": "15550001234", "id": "old-erased"},
        {"from": "15550004321", "id": "old-valid"},
    ])
    inbox = WebhookInboxRepository(pg_pool)
    await inbox.enqueue(org, "whatsapp", payload, "trace-before-erasure")
    async with pg_pool.acquire() as conn:
        row_id = await conn.fetchval("SELECT id FROM meta_webhook_inbox WHERE organization_id=$1", org)
        await conn.execute(
            "UPDATE meta_webhook_inbox SET status='completed',completed_at=NOW() WHERE id=$1", row_id,
        )
    result = await Deletion(pg_pool).request(
        org, "inbox-owner", uuid.uuid4(), scope="contact", subject_id=contact,
    )
    assert result["status"] == "partial"

    rows = await _inbox_rows(pg_pool, org)
    assert len(rows) == 1
    stored = rows[0]["payload"] if isinstance(rows[0]["payload"], dict) else json.loads(rows[0]["payload"])
    messages = stored.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [])
    assert [message["id"] for message in messages] == ["old-valid"]


@pytest.mark.asyncio
async def test_barrier_rejects_consent_delivery_profile_pii_but_allows_financial_counter(
    pg_pool, sample_org,
):
    org = sample_org["id"]
    contact, _, message = await _erase_contact(pg_pool, org, "+15550001357")
    async with pg_pool.acquire() as conn:
        with pytest.raises(Exception, match="GDPR erasure barrier"):
            await conn.execute(
                """INSERT INTO contact_consent_log(id,contact_id,event_type,method)
                   VALUES($1,$2,'opt_in','synthetic')""", uuid.uuid4(), contact,
            )
        with pytest.raises(Exception, match="GDPR erasure barrier"):
            await conn.execute(
                "INSERT INTO message_delivery_attempts(id,message_id) VALUES($1,$2)", uuid.uuid4(), message,
            )

    # Tenant aggregate counters remain writable while PII production is fenced.
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET messages_used_this_period=messages_used_this_period+1 WHERE id=$1", org,
        )
        await conn.execute(
            "INSERT INTO usage_events(organization_id,event_type,quantity,metadata) "
            "VALUES($1,'synthetic.billing.after_erasure',3,'{}')", org,
        )
        assert await conn.fetchval("SELECT messages_used_this_period FROM organizations WHERE id=$1", org) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM usage_events WHERE organization_id=$1 AND event_type='synthetic.billing.after_erasure'",
            org,
        ) == 1


@pytest.mark.asyncio
async def test_org_tombstone_blocks_profile_pii_write(
    pg_pool, sample_org,
):
    org = sample_org["id"]
    await _approve(pg_pool, org, organization=True)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET business_profile=$2::jsonb WHERE id=$1",
            org, '{"synthetic":"before erasure"}',
        )
    result = await Deletion(pg_pool).request(
        org, "org-ingress-owner", uuid.uuid4(), scope="organization", subject_id=None,
    )
    assert result["status"] == "partial"

    async with pg_pool.acquire() as conn:
        with pytest.raises(Exception, match="GDPR erasure barrier"):
            await conn.execute(
                "UPDATE organizations SET business_profile=$2::jsonb WHERE id=$1",
                org, '{"synthetic":"late profile PII"}',
            )


@pytest.mark.asyncio
async def test_unknown_contact_barrier_key_rotation_fails_closed(pg_pool, sample_org, monkeypatch):
    org = sample_org["id"]
    await _erase_contact(pg_pool, org, "+15550002468")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    await WebhookInboxRepository(pg_pool).enqueue(
        org, "whatsapp", _wa_payload(messages=[{"from": "15550002468", "id": "rotated-key"}]),
        "trace-rotated-key",
    )
    assert await _inbox_rows(pg_pool, org) == []
