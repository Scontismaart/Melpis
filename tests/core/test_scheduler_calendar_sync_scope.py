"""Calendar scheduler drains durable work and does not sweep history."""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_calendar_scheduler_processes_durable_queue_without_historical_sweep(monkeypatch):
    from src.core import scheduler

    monkeypatch.setenv("ENCRYPTION_KEY", "synthetic-test-key")
    monkeypatch.setattr(scheduler, "google_calendar_enabled", lambda: True)
    service = MagicMock()
    service.process_pending = AsyncMock(return_value=3)
    calendar_service = MagicMock(return_value=service)
    org_repo = MagicMock()
    monkeypatch.setattr(
        "src.core.db.repositories.organization_repo.OrganizationRepository",
        org_repo,
    )
    monkeypatch.setattr("src.core.calendar.GoogleCalendarService", calendar_service)
    pool = MagicMock()

    await scheduler._calendar_sync_job(pool)

    org_repo.assert_called_once_with(pool)
    calendar_service.assert_called_once_with(org_repo.return_value, "synthetic-test-key")
    service.process_pending.assert_awaited_once_with(limit=20)
    pool.acquire.assert_not_called()


@pytest.mark.asyncio
async def test_calendar_scheduler_skips_when_feature_disabled(monkeypatch):
    from src.core import scheduler

    monkeypatch.setattr(scheduler, "google_calendar_enabled", lambda: False)
    pool = MagicMock()

    await scheduler._calendar_sync_job(pool)

    pool.acquire.assert_not_called()
