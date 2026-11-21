from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
import logging
from contextlib import asynccontextmanager
from src.core.db.repositories.booking_repo import BookingRepository

logger = logging.getLogger(__name__)

TIMEOUT_HOURS = 12


@asynccontextmanager
async def _delivery_guard(repo, org_id, booking_id):
    if isinstance(repo, BookingRepository):
        async with repo.reminder_delivery_guard(org_id, booking_id) as allowed:
            yield allowed
    else:
        yield True


async def send_reminders_for_org(service, org_id, org_timezone: str = "Europe/Rome"):
    tz = ZoneInfo(org_timezone)
    tomorrow = datetime.now(tz).date() + timedelta(days=1)
    bookings = await service.repo.list_bookings_for_reminder(org_id, tomorrow)
    sent = []
    for b in bookings:
        try:
            if hasattr(service.repo, "mark_reminder_sent"):
                marked = await service.repo.mark_reminder_sent(org_id, b["id"])
                if isinstance(service.repo, BookingRepository) and not marked:
                    continue
            else:
                async with service.repo.pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE bookings SET reminder_status = 'sent',
                            reminder_sent_at = NOW(), updated_at = NOW()
                        WHERE organization_id = $2 AND id = $1
                    """, b["id"], org_id)
            msg = (f"Ciao {b['nome_cliente']}! Confermi la prenotazione di domani "
                   f"alle {b['ora']} per {b['coperti']} persone? "
                   f"Rispondi 'Si' per confermare o 'No' per annullare.")
            async with _delivery_guard(service.repo, org_id, b["id"]) as allowed:
                if allowed:
                    await service._send_whatsapp(org_id, b["telefono"], msg)
                    sent.append(b)
        except Exception as e:
            logger.error("Reminder failed for booking %s: %s", b["id"], e)
    return sent


async def check_timeouts_for_org(service, org_id, org_timezone: str = "Europe/Rome"):
    tz = ZoneInfo(org_timezone)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=TIMEOUT_HOURS)
    if hasattr(service.repo, "list_reminders_timed_out"):
        rows = await service.repo.list_reminders_timed_out(org_id, cutoff, datetime.now(tz).date())
    else:
        async with service.repo.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM bookings
                WHERE organization_id = $1
                  AND reminder_status = 'sent'
                  AND reminder_sent_at <= $2
                  AND data >= $3::date
            """, org_id, cutoff, datetime.now(tz).date())
    flagged = []
    for b in rows:
        try:
            await service.repo.update_booking_reminder_status(
                org_id, b["id"], "flagged", datetime.now(timezone.utc)
            )
            flagged.append(b)
        except Exception as e:
            logger.error("Timeout check failed for booking %s: %s", b["id"], e)
    return flagged
