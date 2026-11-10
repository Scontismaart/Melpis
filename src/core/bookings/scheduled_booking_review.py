"""Execute the narrow scheduled booking review against the isolated SQL role."""
import logging

import asyncpg

logger = logging.getLogger(__name__)


async def run_scheduled_booking_reviews(pool) -> int:
    after_org = after_booking = None
    marked = 0
    while candidates := await pool.fetch(
        "SELECT * FROM public.list_scheduled_booking_reviews($1, $2)", after_org, after_booking,
    ):
        for candidate in candidates:
            after_org, after_booking = candidate["organization_id"], candidate["booking_id"]
            try:
                if await pool.fetchval(
                    "SELECT public.apply_scheduled_booking_review($1, $2)", after_org, after_booking,
                ):
                    marked += 1
            except (asyncpg.PostgresError, OSError, TimeoutError):
                logger.warning("Scheduled booking review failed org=%s booking=%s", after_org, after_booking)
    return marked
