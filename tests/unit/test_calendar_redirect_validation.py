import pytest

from src.core.calendar.redirect_uri import is_valid_calendar_redirect_uri


@pytest.mark.parametrize(
    "uri",
    [
        "https://melpis.it/api/calendar/oauth2callback",
        "https://melpis.it:443/api/calendar/oauth2callback",
        "https://app.melpis.it/api/calendar/oauth2callback/",
    ],
)
def test_calendar_callback_accepts_registered_path_and_optional_trailing_slash(uri):
    assert is_valid_calendar_redirect_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:4187/api/calendar/oauth2callback",
        "http://127.0.0.1:4187/api/calendar/oauth2callback",
        "http://[::1]:4187/api/calendar/oauth2callback",
    ],
)
def test_calendar_callback_allows_exact_loopback_http_only_when_enabled(uri):
    assert not is_valid_calendar_redirect_uri(uri)
    assert is_valid_calendar_redirect_uri(uri, allow_loopback_http=True)


@pytest.mark.parametrize(
    "uri",
    [
        "http://melpis.it/api/calendar/oauth2callback",
        "https:///api/calendar/oauth2callback",
        "https://user@melpis.it/api/calendar/oauth2callback",
        "https://melpis.it:99999/api/calendar/oauth2callback",
        "https://melpis.it:/api/calendar/oauth2callback",
        "https://melpis.it:0443/api/calendar/oauth2callback",
        "https://melpis.it/api/calendar/oauth2callback?tenant=1",
        "https://melpis.it/api/calendar/oauth2callback#fragment",
        "https://melpis_it/api/calendar/oauth2callback",
        "https://melpis.it/api/calendar/callback",
        "https://melpis.it:4187/api/calendar/oauth2callback",
        "https://melpis.it/api/calendar/oauth2callback//",
        "https://melpis.it/api/calendar/oauth2callback?",
        "https://melpis.it/api/calendar/oauth2callback#",
        "http://melpis.it:4187/api/calendar/oauth2callback",
        "http://localhost:4188/api/calendar/oauth2callback",
        "http://localhost:04187/api/calendar/oauth2callback",
        "http://127.0.0.2:4187/api/calendar/oauth2callback",
        " http://localhost:4187/api/calendar/oauth2callback",
    ],
)
def test_calendar_callback_rejects_invalid_host_transport_or_uri_suffix(uri):
    assert not is_valid_calendar_redirect_uri(uri)
    assert not is_valid_calendar_redirect_uri(uri, allow_loopback_http=True)
