"""PostgreSQL regression: erased contact data cannot enter Google booking confirmation."""

import uuid

import pytest
from cryptography.fernet import Fernet

from src.core.bookings.service import BookingService
from src.core.db.repositories.booking_repo import BookingRepository
from src.core.gdpr.deletion import Deletion
from src.core.gdpr.guard import ErasureBlocked

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("reset_db")]


async def _approve_contact_erasure(pool, organization_id):
    async with pool.acquire() as conn:
        for category in ("contact_content", "transient_content", "cutover"):
            await conn.execute(
                """INSERT INTO gdpr_policies(organization_id,category,version,erasure_allowed,
                       retention_days,legal_hold,approval_reference,approved_by,approved_at,valid_until)
                   VALUES($1,$2,'calendar-erasure-test',TRUE,30,FALSE,'test:calendar-erasure','qa-calendar',
                          NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 day')""",
                organization_id,
                category,
            )


async def _erase_phone(pool, organization_id, phone):
    contact_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contacts(id,organization_id,phone_number) VALUES($1,$2,$3)",
            contact_id,
            organization_id,
            phone,
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
               VALUES($1,$2,$3,'inbound','text','{}'::jsonb,'synthetic erased contact','handled','human')""",
            uuid.uuid4(),
            organization_id,
            conversation_id,
        )
    result = await Deletion(pool).request(
        organization_id, "calendar-erasure-owner", uuid.uuid4(),
        scope="contact", subject_id=contact_id,
    )
    assert result["status"] == "partial"
    return contact_id


@pytest.mark.asyncio
async def test_erasure_tombstone_blocks_manual_google_confirmation_before_side_effects(
    pg_pool, sample_org, monkeypatch,
):
    encryption_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("ENCRYPTION_KEY", encryption_key)
    monkeypatch.setenv("GDPR_ERASURE_EXECUTION_ENABLED", "true")
    organization_id = sample_org["id"]
    phone = "+1 (555) 000-4321"
    await _approve_contact_erasure(pg_pool, organization_id)
    erased_contact_id = await _erase_phone(pg_pool, organization_id, phone)

    async with pg_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO google_calendar_credentials
                   (organization_id,connection_epoch,connection_status,sync_enabled,calendar_id)
               VALUES($1,$2,'connected',TRUE,'primary')""",
            organization_id,
            uuid.uuid4(),
        )
        await conn.execute(
            """INSERT INTO booking_settings(organization_id,config)
               VALUES($1,'{"a05":{"authority":"google_authoritative","enabled":false}}'::jsonb)""",
            organization_id,
        )
        booking_id = await conn.fetchval(
            """INSERT INTO bookings
                   (organization_id,nome_cliente,telefono,data,ora,coperti,stato,origine)
               VALUES($1,'Synthetic manual booking',$2,CURRENT_DATE+5,'19:00',2,'in_attesa','Dashboard')
               RETURNING id""",
            organization_id,
            phone,
        )
        before = await conn.fetchrow(
            """SELECT b.calendar_confirm_pending,b.stato,s.sync_status,s.calendar_notification_channel,
                      s.calendar_dispatch_state,s.calendar_desired_revision,s.calendar_fence
               FROM bookings b JOIN external_booking_sync s
                 ON s.organization_id=b.organization_id AND s.internal_booking_id=b.id AND s.provider='google'
               WHERE b.organization_id=$1 AND b.id=$2""",
            organization_id,
            booking_id,
        )
        assert before is not None
        assert before["calendar_confirm_pending"] is False
        assert await conn.fetchval(
            "SELECT phone_number FROM contacts WHERE organization_id=$1 AND id=$2",
            organization_id,
            erased_contact_id,
        ) == f"erased:{erased_contact_id}"
        counts_before = await conn.fetchrow(
            """SELECT
                   (SELECT count(*) FROM contacts WHERE organization_id=$1) AS contacts,
                   (SELECT count(*) FROM conversations WHERE organization_id=$1) AS conversations,
                   (SELECT count(*) FROM messages WHERE organization_id=$1) AS messages,
                   (SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1) AS outbox""",
            organization_id,
        )

    # This is a local sentinel only; BookingService must never reach it because
    # the repository checks the HMAC tombstone before the confirmation write.
    from unittest.mock import AsyncMock
    calendar_service = type("CalendarProviderSentinel", (), {})()
    calendar_service.sync_booking_state = AsyncMock(side_effect=AssertionError("provider called before tombstone check"))
    whatsapp = type("WhatsAppSentinel", (), {})()
    whatsapp.send_whatsapp_message = AsyncMock(side_effect=AssertionError("notification sent before tombstone check"))
    service = BookingService(
        repo=BookingRepository(pg_pool),
        whatsapp_service=whatsapp,
        calendar_service=calendar_service,
    )

    with pytest.raises(ErasureBlocked):
        await service.confirm(organization_id, booking_id)

    calendar_service.sync_booking_state.assert_not_awaited()
    whatsapp.send_whatsapp_message.assert_not_awaited()
    async with pg_pool.acquire() as conn:
        after = await conn.fetchrow(
            """SELECT b.calendar_confirm_pending,b.stato,s.sync_status,s.calendar_notification_channel,
                      s.calendar_dispatch_state,s.calendar_desired_revision,s.calendar_fence
               FROM bookings b JOIN external_booking_sync s
                 ON s.organization_id=b.organization_id AND s.internal_booking_id=b.id AND s.provider='google'
               WHERE b.organization_id=$1 AND b.id=$2""",
            organization_id,
            booking_id,
        )
        counts_after = await conn.fetchrow(
            """SELECT
                   (SELECT count(*) FROM contacts WHERE organization_id=$1) AS contacts,
                   (SELECT count(*) FROM conversations WHERE organization_id=$1) AS conversations,
                   (SELECT count(*) FROM messages WHERE organization_id=$1) AS messages,
                   (SELECT count(*) FROM booking_confirmation_outbox WHERE organization_id=$1) AS outbox""",
            organization_id,
        )
        assert after == before
        assert counts_after == counts_before
        assert await conn.fetchval(
            "SELECT count(*) FROM contacts WHERE organization_id=$1 AND phone_number=$2",
            organization_id,
            phone,
        ) == 0
