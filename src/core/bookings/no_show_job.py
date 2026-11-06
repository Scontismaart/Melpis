from datetime import datetime
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)


async def mark_da_verificare_for_org(service, org_id, org_timezone: str = "Europe/Rome"):
    tz = ZoneInfo(org_timezone)
    today = datetime.now(tz).date()
    bookings = await service.repo.list_bookings_da_verificare(org_id, today)
    marked = []
    for b in bookings:
        try:
            updated = await service.repo.mark_legacy_booking_for_review(org_id, b["id"], today)
            if updated:
                marked.append(updated)
        except Exception:
            logger.error("Scheduled review failed org=%s booking=%s", org_id, b["id"])
    return marked
