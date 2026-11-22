"""Fail-closed runtime flags for optional Google integrations."""

import os


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() == "true"


def google_calendar_enabled() -> bool:
    return _enabled("GOOGLE_CALENDAR_ENABLED")


def google_calendar_worker_enabled() -> bool:
    return _enabled("GOOGLE_CALENDAR_WORKER_ENABLED")


def google_business_enabled() -> bool:
    return _enabled("GOOGLE_BUSINESS_ENABLED")
