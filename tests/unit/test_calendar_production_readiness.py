from cryptography.fernet import Fernet
import pytest

from src.core.calendar.production_readiness import (
    calendar_production_readiness_errors,
)


def valid_calendar_config():
    return {
        "GOOGLE_CALENDAR_ENABLED": "true",
        "GOOGLE_CALENDAR_OPERATIONAL_APPROVED": "true",
        "GOOGLE_CALENDAR_CERTIFICATION_REF": "C03-calendar-cert-2026-10",
        "GOOGLE_CALENDAR_SCHEMA_VERSION": "064",
        "GOOGLE_CALENDAR_WORKER_ENABLED": "true",
        "GOOGLE_CLIENT_ID": "calendar-client.apps.googleusercontent.test",
        "GOOGLE_CLIENT_SECRET": "synthetic-calendar-secret-value",
        "ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "PUBLIC_APP_URL": "https://app.example.test",
        "GOOGLE_REDIRECT_URI": "https://app.example.test/api/calendar/oauth2callback",
    }


@pytest.mark.parametrize("value", [None, "false"])
def test_calendar_is_disabled_without_approval(value):
    config = {} if value is None else {"GOOGLE_CALENDAR_ENABLED": value}
    assert calendar_production_readiness_errors(config) == []


def test_release_preflight_preserves_explicit_disabled_default():
    assert calendar_production_readiness_errors({}, require_explicit_disabled=True) == [
        "GOOGLE_CALENDAR_ENABLED: must be explicitly false by default"
    ]
    assert calendar_production_readiness_errors(
        {"GOOGLE_CALENDAR_ENABLED": "FALSE"}, require_explicit_disabled=True
    ) == ["GOOGLE_CALENDAR_ENABLED: must be explicitly false by default"]


def test_fully_asserted_calendar_configuration_passes_without_provider_io():
    assert calendar_production_readiness_errors(valid_calendar_config()) == []


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("GOOGLE_CALENDAR_OPERATIONAL_APPROVED", "false"),
        ("GOOGLE_CALENDAR_CERTIFICATION_REF", ""),
        ("GOOGLE_CALENDAR_SCHEMA_VERSION", "063"),
        ("GOOGLE_CALENDAR_WORKER_ENABLED", "false"),
        ("GOOGLE_CLIENT_ID", ""),
        ("GOOGLE_CLIENT_SECRET", "placeholder"),
        ("ENCRYPTION_KEY", "invalid-fernet-key"),
        ("GOOGLE_REDIRECT_URI", "https://other.example.test/api/calendar/oauth2callback"),
        ("GOOGLE_REDIRECT_URI", "https://app.example.test/api/calendar/callback"),
        ("GOOGLE_REDIRECT_URI", "http://app.example.test/api/calendar/oauth2callback"),
    ),
)
def test_calendar_activation_rejects_incomplete_or_unsafe_configuration(name, value):
    config = valid_calendar_config()
    config[name] = value
    assert any(name in finding for finding in calendar_production_readiness_errors(config))


def test_calendar_activation_does_not_accept_placeholder_approval_reference():
    config = valid_calendar_config()
    config["GOOGLE_CALENDAR_CERTIFICATION_REF"] = "example-certification"
    assert "GOOGLE_CALENDAR_CERTIFICATION_REF" in "; ".join(
        calendar_production_readiness_errors(config)
    )


def test_calendar_activation_rejects_invalid_or_non_boolean_enable_flag():
    assert calendar_production_readiness_errors({"GOOGLE_CALENDAR_ENABLED": "yes"}) == [
        "GOOGLE_CALENDAR_ENABLED: must be false or fully approved true"
    ]
