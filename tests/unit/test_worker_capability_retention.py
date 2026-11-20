from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.core import retention_job, scheduler


@pytest.mark.asyncio
async def test_capability_scrub_requires_postgres_and_installed_function():
    connection = MagicMock()
    connection.fetchval = AsyncMock(side_effect=[True, True, 100, 3, 65])
    acquired = MagicMock()
    acquired.__aenter__ = AsyncMock(return_value=connection)
    acquired.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = acquired

    assert await retention_job.scrub_expired_worker_capability_payloads(pool) == {
        "worker_capability_payloads_scrubbed": 103, "oldest_overdue_seconds": 65,
    }
    assert connection.fetchval.await_count == 5
    assert "c24_worker_scrub_expired_capabilities" in connection.fetchval.await_args_list[2].args[0]
    assert "MIN(expires_at)" in connection.fetchval.await_args.args[0]


@pytest.mark.asyncio
async def test_capability_scrub_fails_closed_when_runtime_role_is_not_postgres():
    connection = MagicMock()
    connection.fetchval = AsyncMock(side_effect=[False, True])
    acquired = MagicMock()
    acquired.__aenter__ = AsyncMock(return_value=connection)
    acquired.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = acquired

    assert await retention_job.scrub_expired_worker_capability_payloads(pool) is None
    assert connection.fetchval.await_count == 2


def test_capability_scrub_has_dedicated_schedule_without_primary_retention(monkeypatch):
    monkeypatch.delenv("GDPR_RETENTION_EXECUTION_ENABLED", raising=False)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler.BackgroundScheduler, "start", lambda self: None)
    shutdown = Mock()
    monkeypatch.setattr(scheduler.BackgroundScheduler, "shutdown", shutdown)
    monkeypatch.setattr(scheduler, "_purge_local_simulator_history", lambda: None)

    scheduler.avvia_scheduler()

    job = scheduler._scheduler.get_job("gdpr_worker_capability_scrub")
    assert job.trigger.interval.total_seconds() == 60
    assert job.coalesce is True
    assert job.max_instances == 1
    assert job.func is not scheduler._scheduler.get_job("retention_giornaliero").func
    assert job.func is not scheduler._scheduler.get_job("gdpr_export_expiry").func

    scheduler.ferma_scheduler()
    shutdown.assert_called_once_with(wait=False)
    assert scheduler._scheduler is None
