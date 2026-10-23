"""A06 erasure waits for durable Calendar dispatch evidence to be resolved."""

import uuid

import pytest
from cryptography.fernet import Fernet

from src.core.calendar.repository import CalendarQueue
from src.core.gdpr.deletion import Deletion

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


async def _approve_contact_erasure(pool, organization_id):
    async with pool.acquire() as conn:
        for category in ("contact_content", "transient_content", "cutover"):
            await conn.execute(
                """INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
                       retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
                   VALUES($1,$2,'calendar-uncertainty-test',TRUE,30,FALSE,'test:calendar-uncertainty',
                          'qa-calendar',NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 day')""",
                organization_id,
                category,
            )


@pytest.mark.asyncio
async def test_erasure_blocks_possibly_sent_calendar_sync_until_verified_receipt(
    pg_pool, sample_org, monkeypatch,
):
    organization_id = sample_org["id"]
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")
    await _approve_contact_erasure(pg_pool, organization_id)

    contact_id, conversation_id, message_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    calendar_epoch = uuid.uuid4()
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
            contact_id,
            organization_id,
            "+15550007771",
        )
        await conn.execute(
            "INSERT INTO conversations(id,organization_id,contact_id) VALUES($1,$2,$3)",
            conversation_id,
            organization_id,
            contact_id,
        )
        await conn.execute(
            """INSERT INTO messages(id,organization_id,conversation_id,direction,message_type,
                   content,content_text,status,handling_type)
               VALUES($1,$2,$3,'inbound','text','{}'::jsonb,'synthetic retained until receipt','handled','human')""",
            message_id,
            organization_id,
            conversation_id,
        )
        await conn.execute(
            """INSERT INTO google_calendar_credentials
                   (organization_id,connection_epoch,connection_status,sync_enabled,calendar_id)
               VALUES($1,$2,'connected',TRUE,'primary')""",
            organization_id,
            calendar_epoch,
        )
        await conn.execute(
            """INSERT INTO booking_settings(organization_id,config)
               VALUES($1,'{"a05":{"authority":"internal_calendar_sync","enabled":false}}'::jsonb)""",
            organization_id,
        )
        booking_id = await conn.fetchval(
            """INSERT INTO bookings(organization_id,contact_id,nome_cliente,telefono,data,ora,coperti,stato,origine)
               VALUES($1,$2,'Synthetic erased contact','+15550007771',CURRENT_DATE+5,'19:00',2,'cancellata','Dashboard')
               RETURNING id""",
            organization_id,
            contact_id,
        )

    queue = CalendarQueue(pg_pool)
    [claim] = await queue.claim(organization_id=organization_id, booking_id=booking_id)
    assert await queue.dispatch(claim, "synthetic-calendar-payload-hash") is True
    async with pg_pool.acquire() as conn:
        sent = await conn.fetchrow(
            """SELECT sync_status,calendar_dispatch_state FROM external_booking_sync
               WHERE organization_id=$1 AND internal_booking_id=$2 AND provider='google'""",
            organization_id,
            booking_id,
        )
    assert dict(sent) == {"sync_status": "processing", "calendar_dispatch_state": "possibly_sent"}

    deletion = Deletion(pg_pool)
    request_id = uuid.uuid4()
    blocked = await deletion.request(
        organization_id,
        "calendar-erasure-owner",
        request_id,
        scope="contact",
        subject_id=contact_id,
    )
    assert blocked["status"] == "blocked"
    assert "calendar_sync_unresolved" in blocked["manifest"]["blockers"]
    assert blocked["manifest"]["local_erasure"] == "not_started"
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT phone_number FROM contacts WHERE organization_id=$1 AND id=$2",
            organization_id,
            contact_id,
        ) == "+15550007771"
        assert await conn.fetchval(
            "SELECT content_text FROM messages WHERE organization_id=$1 AND id=$2",
            organization_id,
            message_id,
        ) == "synthetic retained until receipt"
        assert await conn.fetchval(
            "SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1",
            organization_id,
        ) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM gdpr_operation_steps WHERE organization_id=$1 AND operation_id=$2 AND name='local'",
            organization_id,
            blocked["id"],
        ) == 0

    # A worker's verified delete receipt resolves uncertainty and permits the
    # existing blocked operation to resume; no Google request is made here.
    assert await queue.verified(claim, "synthetic-deleted-event", "synthetic-etag") is True
    resumed = await deletion.advance(organization_id, blocked["id"], "calendar-erasure-owner")
    assert resumed is None
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT phone_number LIKE 'erased:%' FROM contacts WHERE organization_id=$1 AND id=$2",
            organization_id,
            contact_id,
        ) is True
        assert await conn.fetchval(
            "SELECT content_text IS NULL FROM messages WHERE organization_id=$1 AND id=$2",
            organization_id,
            message_id,
        ) is True
        assert await conn.fetchval(
            "SELECT count(*) FROM gdpr_erasure_barriers WHERE organization_id=$1 AND subject_id=$2",
            organization_id,
            contact_id,
        ) == 1
