from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core import retention_job


@pytest.mark.asyncio
async def test_capability_scrub_requires_postgres_and_installed_function():
    connection = MagicMock()
    connection.fetchval = AsyncMock(side_effect=[True, True, 3])
    acquired = MagicMock()
    acquired.__aenter__ = AsyncMock(return_value=connection)
    acquired.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = acquired

    assert await retention_job._scrub_expired_worker_capability_payloads(pool) == 3
    assert connection.fetchval.await_count == 3
    assert "c24_worker_scrub_expired_capabilities" in connection.fetchval.await_args.args[0]


@pytest.mark.asyncio
async def test_capability_scrub_fails_closed_when_runtime_role_is_not_postgres():
    connection = MagicMock()
    connection.fetchval = AsyncMock(side_effect=[False, True])
    acquired = MagicMock()
    acquired.__aenter__ = AsyncMock(return_value=connection)
    acquired.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = acquired

    assert await retention_job._scrub_expired_worker_capability_payloads(pool) is None
    assert connection.fetchval.await_count == 2


@pytest.mark.asyncio
async def test_regular_retention_runs_capability_scrub_even_when_primary_retention_is_disabled(
    monkeypatch,
):
    monkeypatch.delenv("GDPR_RETENTION_EXECUTION_ENABLED", raising=False)
    scrub = AsyncMock(return_value=2)
    expire = AsyncMock(return_value=1)
    monkeypatch.setattr(retention_job, "_scrub_expired_worker_capability_payloads", scrub)
    monkeypatch.setattr(retention_job, "_expired_exports", expire)

    result = await retention_job.run_retention(object())

    assert result == {"exports_expired": 1, "messages_redacted": 0, "simulator_purged": 0}
    scrub.assert_awaited_once()
    expire.assert_awaited_once()
