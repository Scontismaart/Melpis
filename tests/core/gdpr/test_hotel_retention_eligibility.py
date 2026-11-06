"""Civil-anniversary eligibility for verified hotel completion instants."""
from datetime import datetime, timezone

import pytest


def instant(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("completed,zone,as_of,expected", [
    # Leap-day anniversary clamps to February 28, preserving local time.
    ("2024-02-29T11:00:00", "Europe/Rome", "2025-02-28T10:59:59", False),
    ("2024-02-29T11:00:00", "Europe/Rome", "2025-02-28T11:00:00", True),
    # A year later has a different UTC offset across DST.
    ("2023-03-29T10:00:00", "Europe/Rome", "2024-03-29T10:59:59", False),
    ("2023-03-29T10:00:00", "Europe/Rome", "2024-03-29T11:00:00", True),
    ("2024-01-01T00:00:00", "UTC", "2024-12-31T23:59:59", False),
    ("2024-01-01T00:00:00", "UTC", "2025-01-01T00:00:00", True),
])
async def test_hotel_civil_anniversary(pg_pool, completed, zone, as_of, expected):
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT public.hotel_completed_stay_retention_eligible($1,$2,$3)",
            instant(completed), zone, instant(as_of),
        ) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("completed,zone,as_of", [
    (None, "UTC", instant("2026-01-01T00:00:00")),
    (instant("2024-01-01T00:00:00"), None, instant("2026-01-01T00:00:00")),
    (instant("2024-01-01T00:00:00"), "invalid/zone", instant("2026-01-01T00:00:00")),
    (instant("2024-01-01T00:00:00"), "+01:00", instant("2026-01-01T00:00:00")),
    (instant("2024-01-01T00:00:00"), "UTC", None),
])
async def test_hotel_eligibility_fails_closed(pg_pool, completed, zone, as_of):
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT public.hotel_completed_stay_retention_eligible($1,$2,$3)",
            completed, zone, as_of,
        ) is False

