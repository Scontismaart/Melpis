"""Real PostgreSQL proof of account-only feedback disclosure."""

import json
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest

from src.core.gdpr.exporter import Exporter
from tests.core.gdpr.test_exports_a06 import export_bytes


async def _profile(conn, auth_id):
    await conn.execute("INSERT INTO auth.users(id,email) VALUES($1,$2)", auth_id, f"{auth_id}@test.invalid")
    return await conn.fetchval("SELECT id FROM user_profiles WHERE auth_user_id=$1", auth_id)


async def _outbound(conn, org, phone="+15550001142"):
    contact, conversation, message = uuid4(), uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)", contact, org, phone,
    )
    await conn.execute(
        "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)", conversation, org, contact,
    )
    await conn.execute(
        """INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,content,content_text,status)
           VALUES($1,$2,$3,'outbound','text','{}','synthetic conversation text','sent')""",
        message, org, conversation,
    )
    return conversation, message


@pytest.mark.asyncio
async def test_account_export_contains_only_exact_staff_feedback_for_selected_tenant(
    pg_pool, sample_org, other_org, monkeypatch,
):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    org, foreign = sample_org["id"], other_org["id"]
    requester, colleague, outsider = uuid4(), uuid4(), uuid4()
    own_feedback = uuid4()
    async with pg_pool.acquire() as conn, conn.transaction():
        owner = await _profile(conn, requester)
        staff = await _profile(conn, colleague)
        no_membership = await _profile(conn, outsider)
        await conn.executemany(
            """INSERT INTO organization_memberships(organization_id,user_id,ruolo,joined_at)
               VALUES($1,$2,$3,now())""",
            [(org, owner, "owner"), (foreign, owner, "owner"), (org, staff, "staff")],
        )
        conversation, message = await _outbound(conn, org)
        other_conversation, other_message = await _outbound(conn, foreign)
        unrelated_conversation, unrelated_message = await _outbound(conn, foreign, "+15550001143")
        await conn.executemany(
            """INSERT INTO message_feedback(id,organization_id,conversation_id,message_id,source,value,created_by_user_id)
               VALUES($1,$2,$3,$4,$5,$6,$7)""",
            [
                (own_feedback, org, conversation, message, "staff_ui", "up", owner),
                (uuid4(), org, conversation, message, "staff_ui", "down", staff),
                (uuid4(), org, conversation, message, "staff_ui", "down", None),
                (uuid4(), org, conversation, message, "staff_ui", "down", no_membership),
                (uuid4(), org, conversation, message, "customer_emoji", "down", owner),
                (uuid4(), foreign, other_conversation, other_message, "staff_ui", "down", owner),
                (uuid4(), org, unrelated_conversation, unrelated_message, "staff_ui", "down", owner),
            ],
        )

    exporter = Exporter(pg_pool)
    result = await exporter.prepare(org, requester, scope="account", request_id=uuid4())
    document = json.loads(await export_bytes(exporter, org, result["export_id"], requester))
    feedback, = document["message_feedback"]
    assert set(feedback) == {"id", "source", "value", "created_at"}
    assert feedback["id"] == str(own_feedback)
    assert feedback["source"] == "staff_ui"
    assert feedback["value"] == "up"
    assert isinstance(feedback["created_at"], str) and feedback["created_at"]

    # The exporter itself must resolve the relationship in the selected tenant.
    # The public route additionally restricts it to an authenticated MFA owner.
    result = await exporter.prepare(org, outsider, scope="account", request_id=uuid4())
    document = json.loads(await export_bytes(exporter, org, result["export_id"], outsider))
    assert document["message_feedback"] == []
