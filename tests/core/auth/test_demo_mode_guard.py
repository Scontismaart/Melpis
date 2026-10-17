"""Fail-closed startup checks for production launch profiles."""

import pytest
from cryptography.fernet import Fernet

from src.core.auth import dependencies
from src.core.startup_guard import STRIPE_PRICE_ENV_NAMES, assert_production_safe


@pytest.fixture(autouse=True)
def sandbox_production_baseline(monkeypatch):
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x?sslmode=require")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-unit-safe-value")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "172.30.0.0/24")
    monkeypatch.delenv("CSRF_TRUSTED_ORIGINS", raising=False)
    monkeypatch.setenv("LAUNCH_PROFILE", "sandbox")
    monkeypatch.setenv("SANDBOX_ONLY", "true")
    monkeypatch.setenv("LLM_COST_POLICY", "free_only")
    monkeypatch.setenv("GROQ_FREE_ACCOUNT_CONFIRMED", "true")
    monkeypatch.setenv("GROQ_ZDR_CONFIRMED", "true")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_unit_safe_value")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit_safe_value")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_unit_safe_value")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_unit_safe_value")
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "false")
    monkeypatch.setenv("GOOGLE_BUSINESS_ENABLED", "false")
    for name in (
        "OPENROUTER_MODEL",
        "OPENROUTER_MODEL_CHEAP",
        "OPENROUTER_MODEL_PREMIUM",
        "OPENROUTER_MODEL_FALLBACKS",
        "OPENROUTER_MODEL_INTENT",
    ):
        monkeypatch.delenv(name, raising=False)


def _configure_commercial(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LAUNCH_PROFILE", "commercial_bootstrap")
    monkeypatch.setenv("SANDBOX_ONLY", "false")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_unit_safe_value")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_live_unit_safe_value")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_live_unit_safe_value")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    for name in STRIPE_PRICE_ENV_NAMES:
        monkeypatch.setenv(name, f"price_live_{name.lower()}")


def test_demo_mode_ignorato_in_produzione(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "true")
    assert dependencies.is_demo_mode() is False


def test_demo_mode_attivo_in_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_MODE", "1")
    assert dependencies.is_demo_mode() is True


def test_startup_bloccato_demo_in_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "1")
    with pytest.raises(RuntimeError, match="B1"):
        assert_production_safe()


def test_startup_ok_in_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    assert_production_safe()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    (
        ("AUTH_COOKIE_SECURE", "false", "AUTH_COOKIE_SECURE"),
        ("PUBLIC_APP_URL", "http://app.example.test", "PUBLIC_APP_URL"),
    ),
)
def test_startup_bloccato_cookie_o_url_non_https(monkeypatch, name, value, message):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=message):
        assert_production_safe()


def test_startup_bloccato_senza_encryption_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="B4"):
        assert_production_safe()


def test_startup_bloccato_chiave_invalida(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY", "non-una-chiave")
    with pytest.raises(RuntimeError, match="B4"):
        assert_production_safe()


def test_sandbox_startup_accepts_test_credentials(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert_production_safe()


def test_startup_bloccato_stripe_live_in_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_unit_safe_value")
    with pytest.raises(RuntimeError, match="credenziali Stripe live"):
        assert_production_safe()


def test_startup_ok_stripe_test_in_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    assert_production_safe()


def test_commercial_bootstrap_accepts_stripe_live_and_free_groq(monkeypatch):
    _configure_commercial(monkeypatch)
    assert_production_safe()


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("STRIPE_SECRET_KEY", "sk_test_unit_safe_value"),
        ("STRIPE_PUBLISHABLE_KEY", "pk_test_unit_safe_value"),
    ),
)
def test_commercial_bootstrap_rejects_mixed_stripe_modes(monkeypatch, name, value):
    _configure_commercial(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        assert_production_safe()


def test_sandbox_rejects_live_stripe_credentials(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_unit_safe_value")
    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        assert_production_safe()


@pytest.mark.parametrize("name", STRIPE_PRICE_ENV_NAMES)
def test_commercial_bootstrap_rejects_missing_live_price(monkeypatch, name):
    _configure_commercial(monkeypatch)
    monkeypatch.delenv(name)
    with pytest.raises(RuntimeError, match=name):
        assert_production_safe()


@pytest.mark.parametrize("name", ("GROQ_FREE_ACCOUNT_CONFIRMED", "GROQ_ZDR_CONFIRMED"))
def test_commercial_bootstrap_requires_groq_confirmations(monkeypatch, name):
    _configure_commercial(monkeypatch)
    monkeypatch.setenv(name, "false")
    with pytest.raises(RuntimeError, match="Groq"):
        assert_production_safe()


def test_commercial_bootstrap_rejects_paid_llm_policy(monkeypatch):
    _configure_commercial(monkeypatch)
    monkeypatch.setenv("LLM_COST_POLICY", "standard")
    with pytest.raises(RuntimeError, match="free_only"):
        assert_production_safe()


def test_commercial_bootstrap_rejects_non_allowlisted_model(monkeypatch):
    _configure_commercial(monkeypatch)
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o")
    with pytest.raises(RuntimeError, match="allowlist"):
        assert_production_safe()


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("GROQ_API_KEY", "gsk_placeholder"),
        ("STRIPE_SECRET_KEY", "sk_live_replace-me"),
        ("STRIPE_WEBHOOK_SECRET", "whsec_xxx"),
    ),
)
def test_commercial_bootstrap_rejects_placeholder_secrets(monkeypatch, name, value):
    _configure_commercial(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="placeholder"):
        assert_production_safe()


def test_commercial_bootstrap_rejects_short_placeholder_key(monkeypatch):
    _configure_commercial(monkeypatch)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_no")
    with pytest.raises(RuntimeError, match="formato non valido"):
        assert_production_safe()


@pytest.mark.parametrize("name", ("GOOGLE_CALENDAR_ENABLED", "GOOGLE_BUSINESS_ENABLED"))
def test_launch_profiles_keep_google_integrations_disabled(monkeypatch, name):
    _configure_commercial(monkeypatch)
    monkeypatch.setenv(name, "true")
    with pytest.raises(RuntimeError, match=name):
        assert_production_safe()
