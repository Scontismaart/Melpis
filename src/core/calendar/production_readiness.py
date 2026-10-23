"""Operator-asserted production gates for optional Google Calendar access.

Passing these configuration checks is not evidence that the Calendar rollout
certification is complete or that schema migration 064 is actually installed.
Runtime schema verification belongs to the database-backed readiness path.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse

from src.core.calendar.redirect_uri import is_valid_calendar_redirect_uri

CALENDAR_SCHEMA_VERSION = "064"

_PLACEHOLDER_MARKERS = (
    "<",
    ">",
    "xxx",
    "placeholder",
    "your-",
    "your_",
    "changeme",
    "change-me",
    "replace-me",
    "example",
    "dummy",
    "_here",
)


def _value(config: Mapping[str, object], name: str) -> str:
    value = config.get(name)
    return value.strip() if isinstance(value, str) else ""


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or any(marker in normalized for marker in _PLACEHOLDER_MARKERS)


def _same_https_origin(public_app_url: str, redirect_uri: str) -> bool:
    try:
        public = urlparse(public_app_url)
        redirect = urlparse(redirect_uri)
        public_port = public.port
        redirect_port = redirect.port
    except ValueError:
        return False
    if (
        public.scheme != "https"
        or not public.hostname
        or public.username is not None
        or public.password is not None
        or public.query
        or public.fragment
        or public_port not in (None, 443)
        or redirect.scheme != "https"
        or not redirect.hostname
        or redirect_port not in (None, 443)
    ):
        return False
    return public.hostname.lower() == redirect.hostname.lower()


def calendar_production_readiness_errors(
    config: Mapping[str, object], *, require_explicit_disabled: bool = False
) -> list[str]:
    """Return safe findings; do not print config values or perform provider I/O.

    Calendar is off by default. Enabling it requires affirmative operator
    assertions, OAuth and encryption configuration, and a registered strict
    HTTPS callback on the public app's origin.
    """
    errors: list[str] = []
    raw_enabled = _value(config, "GOOGLE_CALENDAR_ENABLED")
    enabled = raw_enabled.lower()
    if enabled != "true":
        if enabled not in {"", "false"}:
            errors.append("GOOGLE_CALENDAR_ENABLED: must be false or fully approved true")
        elif require_explicit_disabled and raw_enabled != "false":
            errors.append("GOOGLE_CALENDAR_ENABLED: must be explicitly false by default")
        return errors

    for name in (
        "GOOGLE_CALENDAR_OPERATIONAL_APPROVED",
        "GOOGLE_CALENDAR_WORKER_ENABLED",
    ):
        if _value(config, name).lower() != "true":
            errors.append(f"{name}: must be true before enabling Calendar")

    certification_ref = _value(config, "GOOGLE_CALENDAR_CERTIFICATION_REF")
    if _is_placeholder(certification_ref):
        errors.append("GOOGLE_CALENDAR_CERTIFICATION_REF: required non-placeholder operator assertion")

    schema_version = _value(config, "GOOGLE_CALENDAR_SCHEMA_VERSION")
    if schema_version != CALENDAR_SCHEMA_VERSION:
        errors.append(f"GOOGLE_CALENDAR_SCHEMA_VERSION: must be {CALENDAR_SCHEMA_VERSION}")

    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        if _is_placeholder(_value(config, name)):
            errors.append(f"{name}: required non-placeholder value when Calendar is enabled")

    encryption_key = _value(config, "ENCRYPTION_KEY")
    try:
        from cryptography.fernet import Fernet

        Fernet(encryption_key.encode("ascii"))
    except (ImportError, UnicodeEncodeError, TypeError, ValueError):
        errors.append("ENCRYPTION_KEY: valid Fernet key required when Calendar is enabled")

    public_app_url = _value(config, "PUBLIC_APP_URL")
    redirect_uri = _value(config, "GOOGLE_REDIRECT_URI")
    if (
        not is_valid_calendar_redirect_uri(redirect_uri)
        or not _same_https_origin(public_app_url, redirect_uri)
    ):
        errors.append(
            "GOOGLE_REDIRECT_URI: must be the registered strict HTTPS Calendar callback "
            "on the PUBLIC_APP_URL origin"
        )

    return errors
