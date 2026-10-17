import pytest

from src.core.calendar.redirect_uri import is_valid_calendar_redirect_uri


@pytest.mark.parametrize(
    "uri",
    [
        "https://melpis.it/api/calendar/oauth2callback",
        "https://app.melpis.it/api/calendar/oauth2callback/",
    ],
)
def test_calendar_callback_accepts_registered_path_and_optional_trailing_slash(uri):
    assert is_valid_calendar_redirect_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "http://melpis.it/api/calendar/oauth2callback",
        "https:///api/calendar/oauth2callback",
        "https://user@melpis.it/api/calendar/oauth2callback",
        "https://melpis.it:99999/api/calendar/oauth2callback",
        "https://melpis.it/api/calendar/oauth2callback?tenant=1",
        "https://melpis.it/api/calendar/oauth2callback#fragment",
        "https://melpis_it/api/calendar/oauth2callback",
        "https://melpis.it/api/calendar/callback",
    ],
)
def test_calendar_callback_rejects_invalid_host_transport_or_uri_suffix(uri):
    assert not is_valid_calendar_redirect_uri(uri)
