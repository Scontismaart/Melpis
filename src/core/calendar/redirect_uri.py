"""Shared strict validation for the registered Google Calendar OAuth callback."""

import re
from urllib.parse import urlparse

CALENDAR_OAUTH_CALLBACK_PATH = "/api/calendar/oauth2callback"
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def is_valid_calendar_redirect_uri(value: str, *, allow_loopback_http: bool = False) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(character.isspace() for character in value) or "?" in value or "#" in value:
        return False
    try:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        port = parsed.port  # Access validates malformed ports.
    except ValueError:
        return False
    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path not in {CALENDAR_OAUTH_CALLBACK_PATH, f"{CALENDAR_OAUTH_CALLBACK_PATH}/"}
    ):
        return False

    if parsed.scheme == "http":
        loopback_authorities = {
            "localhost:4187",
            "127.0.0.1:4187",
            "[::1]:4187",
        }
        return bool(
            allow_loopback_http
            and host.lower() in {"localhost", "127.0.0.1", "::1"}
            and port == 4187
            and parsed.netloc.lower() in loopback_authorities
        )

    if parsed.scheme != "https" or (port is not None and port != 443):
        return False
    if parsed.netloc.lower() not in {host, f"{host}:443"}:
        return False
    labels = host.split(".")
    return bool(host) and all(_HOST_LABEL.fullmatch(label) for label in labels)
