"""Shared strict validation for the registered Google Calendar OAuth callback."""

import re
from urllib.parse import urlparse

CALENDAR_OAUTH_CALLBACK_PATH = "/api/calendar/oauth2callback"
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def is_valid_calendar_redirect_uri(value: str) -> bool:
    try:
        parsed = urlparse((value or "").strip())
        host = parsed.hostname or ""
        port = parsed.port  # Access validates malformed ports.
    except ValueError:
        return False
    labels = host.rstrip(".").split(".")
    valid_host = bool(host) and all(_HOST_LABEL.fullmatch(label) for label in labels)
    return bool(
        parsed.scheme == "https"
        and parsed.netloc
        and parsed.username is None
        and parsed.password is None
        and valid_host
        and (port is None or 1 <= port <= 65535)
        and parsed.path.rstrip("/") == CALENDAR_OAUTH_CALLBACK_PATH
        and not parsed.query
        and not parsed.fragment
    )
