"""Private booking process startup and shutdown, without provider calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from src.api import main
from src.core.bookings.private_worker_lifecycle import assert_private_booking_ready


class Pool:
    def __init__(self, ready):
        self.conn = SimpleNamespace(fetchval=AsyncMock(return_value=ready))
        self.close = AsyncMock()

    def acquire(self):
        class Acquire:
            async def __aenter__(inner):
                return self.conn
            async def __aexit__(inner, *_):
                return False
        return Acquire()


@pytest.fixture
def app_setup(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test:test@localhost/test')
    monkeypatch.setenv('C24_PRIVATE_BOOKING_DATABASE_URL', 'postgresql://worker:worker@localhost/test')
    monkeypatch.delenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', raising=False)
    monkeypatch.delenv('META_APP_SECRET', raising=False)
    monkeypatch.delenv('META_VERIFY_TOKEN', raising=False)
    monkeypatch.setattr('src.core.startup_guard.assert_production_safe', Mock())
    for name in ('start_worker', 'stop_email_worker', 'avvia_scheduler', 'ferma_scheduler', 'reset_memory_rate_limiter'):
        monkeypatch.setattr(main, name, Mock())
    monkeypatch.setattr(main, 'close_http_client', AsyncMock())
    monkeypatch.setattr(main, 'close_rate_limiter', AsyncMock())
    monkeypatch.setattr('src.core.documenti.isolated.shutdown_local_workers', AsyncMock())
    repo = SimpleNamespace(org_repo=object(), doc_repo=object(), billing_repo=SimpleNamespace(drain_governance_outbox=AsyncMock(return_value=0)), conv_repo=object())
    monkeypatch.setattr(main, 'CoreRepository', Mock(return_value=repo))
    monkeypatch.setattr(main, 'WhatsAppRepository', Mock(return_value=object()))
    monkeypatch.setattr(main, 'GoogleCalendarService', Mock(return_value=object()))
    monkeypatch.setattr(main, 'google_calendar_enabled', Mock(return_value=False))
    monkeypatch.setattr('src.core.bookings.BookingService', Mock(return_value=SimpleNamespace(process_pending_calendar_sync=AsyncMock())))
    monkeypatch.setattr('src.whatsapp.service.WhatsAppService', Mock(return_value=SimpleNamespace(fast_path_match=Mock())))
    monkeypatch.setattr('src.core.db.repositories.booking_repo.BookingRepository', Mock(return_value=object()))
    monkeypatch.setattr('src.core.db.repositories.organization_repo.OrganizationRepository', Mock(return_value=object()))
    monkeypatch.setattr('src.integrations.airtable.wiring.build_airtable_tool_factory', Mock(return_value=None))
    monkeypatch.setattr('src.core.receptionist.conversation_orchestrator.ConversationOrchestrator', Mock(return_value=object()))
    monkeypatch.setattr('src.whatsapp.inbound_processor.InboundProcessor', Mock(return_value=SimpleNamespace(process_next_batch=AsyncMock())))
    monkeypatch.setattr('src.whatsapp.retry_worker.RetryWorker', Mock(return_value=SimpleNamespace(process_next_batch=AsyncMock())))
    pool = Pool(True)
    worker_pool = Pool(True)
    monkeypatch.setattr('asyncpg.create_pool', AsyncMock(return_value=pool))
    monkeypatch.setattr('src.core.bookings.private_worker_lifecycle.create_private_booking_pool', AsyncMock(return_value=worker_pool))
    monkeypatch.setattr('src.core.bookings.private_worker_lifecycle.assert_private_booking_ready', AsyncMock())
    pool.worker_pool = worker_pool
    return pool


@pytest.mark.asyncio
async def test_default_disabled_does_not_check_or_start(app_setup):
    app = FastAPI()
    async with main.lifespan(app):
        assert app.state.private_booking_task is None
        app_setup.conn.fetchval.assert_not_awaited()
    app_setup.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_enabled_starts_one_task_and_shutdown_cancels(app_setup, monkeypatch):
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    started, stopped = asyncio.Event(), asyncio.Event()
    async def loop(pool):
        assert pool is app_setup.worker_pool
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    runner = Mock(side_effect=loop)
    monkeypatch.setattr('src.core.bookings.private_worker_lifecycle.run_private_booking_worker', runner)
    app = FastAPI()
    async with main.lifespan(app):
        await asyncio.wait_for(started.wait(), 1)
        assert app.state.private_booking_task is not None
        runner.assert_called_once_with(app_setup.worker_pool)
    assert stopped.is_set()
    assert app.state.private_booking_task.cancelled()
    app_setup.close.assert_awaited_once()
    app_setup.worker_pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_enabled_wrong_identity_fails_closed_and_closes_pool(app_setup, monkeypatch):
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    from src.core.bookings import private_worker_lifecycle
    private_worker_lifecycle.assert_private_booking_ready.side_effect = RuntimeError('readiness failed')
    with pytest.raises(RuntimeError, match='readiness failed'):
        async with main.lifespan(FastAPI()):
            pytest.fail('startup must fail')
    app_setup.close.assert_awaited_once()
    app_setup.worker_pool.close.assert_awaited_once()
    main.stop_email_worker.assert_called_once()


@pytest.mark.asyncio
async def test_enabled_without_dedicated_dsn_fails_closed(app_setup, monkeypatch):
    monkeypatch.setenv('C24_PRIVATE_BOOKING_WORKER_ENABLED', 'true')
    monkeypatch.delenv('C24_PRIVATE_BOOKING_DATABASE_URL')
    with pytest.raises(RuntimeError, match='readiness failed'):
        async with main.lifespan(FastAPI()):
            pytest.fail('worker must not reuse the API database identity')
    app_setup.close.assert_awaited_once()
    app_setup.worker_pool.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_readiness_rejects_missing_schema_and_database_errors():
    pool = Pool(None)
    with pytest.raises(RuntimeError, match='readiness failed'):
        await assert_private_booking_ready(pool)
    pool.conn.fetchval.side_effect = RuntimeError('secret database detail')
    with pytest.raises(RuntimeError, match='readiness failed') as error:
        await assert_private_booking_ready(pool)
    assert 'secret' not in str(error.value)
