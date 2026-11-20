"""GDPR snapshot expiry is independent of the primary data-retention switch."""
from src.core import scheduler


def test_snapshot_expiry_has_dedicated_minute_schedule_with_primary_retention_disabled(monkeypatch):
    monkeypatch.delenv("GDPR_RETENTION_EXECUTION_ENABLED", raising=False)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    # Register real APScheduler jobs without running any actual background work.
    monkeypatch.setattr(scheduler.BackgroundScheduler, "start", lambda self: None)
    monkeypatch.setattr(scheduler, "_purge_local_simulator_history", lambda: None)

    scheduler.avvia_scheduler()

    job = scheduler._scheduler.get_job("gdpr_export_expiry")
    assert job is not None
    assert job.trigger.interval.total_seconds() == 60
    assert job.coalesce is True
    assert job.max_instances == 1
    assert job.func is not scheduler._scheduler.get_job("retention_giornaliero").func
