import asyncio

import pytest


def _enable(monkeypatch):
    monkeypatch.setenv("GDPR_RETENTION_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("GDPR_C24_OWNER_RETENTION_ENABLED", "true")
    monkeypatch.setenv("GDPR_C24_AUTO_RETENTION_ENABLED", "true")


def test_c24_retention_scheduler_is_disabled_by_default(monkeypatch):
    from src.core import scheduler

    for key in (
        "GDPR_RETENTION_EXECUTION_ENABLED",
        "GDPR_C24_OWNER_RETENTION_ENABLED",
        "GDPR_C24_AUTO_RETENTION_ENABLED",
        "GDPR_C24_OWNER_RETENTION_DSN",
    ):
        monkeypatch.delenv(key, raising=False)

    called = False

    async def unexpected(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(scheduler, "_con_pool_esimero", unexpected)
    scheduler._run_c24_retention_sweep()
    assert not called


def test_c24_retention_scheduler_never_falls_back_to_application_dsn(monkeypatch):
    from src.core import scheduler

    _enable(monkeypatch)
    monkeypatch.delenv("GDPR_C24_OWNER_RETENTION_DSN", raising=False)
    with pytest.raises(RuntimeError, match="dedicated connection"):
        scheduler._run_c24_retention_sweep()


def test_scheduler_preflights_enabled_c24_connection_before_initializing(monkeypatch):
    from src.core import scheduler

    _enable(monkeypatch)
    monkeypatch.delenv("GDPR_C24_OWNER_RETENTION_DSN", raising=False)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    with pytest.raises(RuntimeError, match="dedicated connection"):
        scheduler.avvia_scheduler()
    assert scheduler._scheduler is None


def test_c24_retention_scheduler_uses_only_dedicated_dsn(monkeypatch):
    from src.core import scheduler

    _enable(monkeypatch)
    monkeypatch.setenv("GDPR_C24_OWNER_RETENTION_DSN", "postgresql://retention.invalid/db")
    seen = {}

    async def capture(dsn):
        seen["dsn"] = dsn

    monkeypatch.setattr(scheduler, "_run_c24_retention_sweep_async", capture)
    scheduler._run_c24_retention_sweep()

    assert seen["dsn"] == "postgresql://retention.invalid/db"


def test_c24_retention_scheduler_redacts_connection_failures(monkeypatch, caplog):
    from src.core import scheduler

    _enable(monkeypatch)
    monkeypatch.setenv("GDPR_C24_OWNER_RETENTION_DSN", "postgresql://secret.invalid/db")

    async def fail(_dsn):
        raise ValueError("connection string contains secret.invalid")

    monkeypatch.setattr(scheduler, "_run_c24_retention_sweep_async", fail)
    with pytest.raises(RuntimeError, match="sweep failed") as raised:
        scheduler._run_c24_retention_sweep()
    assert "secret.invalid" not in caplog.text
    assert "secret.invalid" not in str(raised.value)


def test_c24_retention_job_logs_only_counts_and_gate_state(monkeypatch):
    from src.core import scheduler
    from src.core.gdpr import c24_retention_controller

    class Pool:
        pass

    async def sweep(pool):
        assert isinstance(pool, Pool)
        return {"enabled": True, "batch_clear": False}

    monkeypatch.setattr(c24_retention_controller, "run_c24_retention_sweep", sweep)
    asyncio.run(scheduler._c24_retention_job(Pool()))

