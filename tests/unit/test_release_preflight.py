import importlib.util
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from dotenv import dotenv_values


_SPEC = importlib.util.spec_from_file_location(
    "release_preflight", Path("scripts/release_preflight.py")
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
validate = _MODULE.validate
STRIPE_PRICE_ENV_NAMES = _MODULE.STRIPE_PRICE_ENV_NAMES
IMAGE_REFERENCE_ENV_NAMES = _MODULE.IMAGE_REFERENCE_ENV_NAMES
LEGAL_IDENTITY_ENV_NAMES = _MODULE.LEGAL_IDENTITY_ENV_NAMES
LEGAL_REVIEW_APPROVAL_ENV_NAMES = _MODULE.LEGAL_REVIEW_APPROVAL_ENV_NAMES
LEGAL_DECISION_INTEGER_ENV_NAMES = _MODULE.LEGAL_DECISION_INTEGER_ENV_NAMES


def test_production_example_declares_safe_commercial_defaults():
    config = dotenv_values(".env.production.example")
    assert config["LAUNCH_PROFILE"] == "commercial_bootstrap"
    assert config["SANDBOX_ONLY"] == "false"
    assert config["GROQ_FREE_ACCOUNT_CONFIRMED"] == "false"
    assert config["GROQ_ZDR_CONFIRMED"] == "false"
    assert config["GOOGLE_CALENDAR_ENABLED"] == "false"
    assert config["GOOGLE_BUSINESS_ENABLED"] == "false"
    assert config["STRIPE_ANNUAL_PRICING_ENABLED"] == "false"
    assert config["STRIPE_PRICE_STARTER_YEARLY"] in (None, "")
    assert config["STRIPE_PRICE_PRO_YEARLY"] in (None, "")
    assert config["STRIPE_PRICE_BUSINESS_YEARLY"] in (None, "")
    assert validate(config)


def test_pricing_v1_annual_selector_is_hidden_until_explicit_public_config():
    html = Path("web/landing/index.html").read_text(encoding="utf-8")
    script = Path("web/landing/app.js").read_text(encoding="utf-8")
    css = Path("web/landing/style.css").read_text(encoding="utf-8")
    assert '<div class="billing-toggle-wrap" hidden>' in html
    assert ".billing-toggle-wrap[hidden]" in css
    assert "fetch('/api/billing/public-config'" in script
    assert "config.annual_pricing_enabled === true" in script
    assert "billingToggle.hidden = false" in script
    assert "billingToggle.hidden = true" in script


def test_general_example_is_an_explicit_disabled_sandbox():
    config = dotenv_values(".env.example")
    assert config["LAUNCH_PROFILE"] == "sandbox"
    assert config["SANDBOX_ONLY"] == "true"
    assert config["GOOGLE_CALENDAR_ENABLED"] == "false"
    assert config["GOOGLE_BUSINESS_ENABLED"] == "false"


def common_config():
    return {
        "APP_ENV": "production",
        "LLM_COST_POLICY": "free_only",
        "GROQ_FREE_ACCOUNT_CONFIRMED": "true",
        "GROQ_ZDR_CONFIRMED": "true",
        "AUTH_COOKIE_SECURE": "true",
        "RATE_LIMIT_BACKEND": "redis",
        "GOOGLE_CALENDAR_ENABLED": "false",
        "GOOGLE_BUSINESS_ENABLED": "false",
        "DATABASE_URL": "postgresql://u:p@db/x?sslmode=require",
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_ANON_KEY": "anon-unit-safe-value",
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "PUBLIC_APP_URL": "https://melpis.it",
        "REDIS_URL": "redis://valkey:6379/0",
        "CORS_ORIGINS": "https://melpis.it,https://app.melpis.it",
        "META_APP_SECRET": "meta-unit-safe-value",
        "META_VERIFY_TOKEN": "verify-unit-safe-value",
        "GROQ_API_KEY": "gsk_unit_safe_value",
        "MELPIS_API_IMAGE_REF": "ghcr.io/melpis/api@sha256:" + "a" * 64,
        "MELPIS_WEB_IMAGE_REF": "ghcr.io/melpis/web@sha256:" + "b" * 64,
        "CADDY_SITE_MODE": "temporary",
        "PUBLIC_HOST": "temporary.melpis.test",
        "LEGAL_ENTITY_NAME": "Melpis S.r.l.",
        "LEGAL_ENTITY_REGISTERED_OFFICE": "Via Roma 1, Roma",
        "LEGAL_ENTITY_VAT_NUMBER": "IT12345678901",
        "LEGAL_PRIVACY_CONTACT_EMAIL": "privacy@melpis.invalid",
        "LEGAL_FORUM": "Roma",
        "LEGAL_DOCUMENT_EFFECTIVE_DATE": "2026-09-21",
        "LEGAL_PUBLIC_DOCUMENTS_REVIEWED": "true",
        "LEGAL_PRIVACY_REVIEW_APPROVED": "true",
        "LEGAL_TERMS_REVIEW_APPROVED": "true",
        "LEGAL_DPA_REVIEW_APPROVED": "true",
        "LEGAL_PAYMENT_SUSPENSION_DAYS": "14",
        "LEGAL_ACCOUNT_TERMINATION_DAYS": "30",
        "STRIPE_WEBHOOK_SECRET": "whsec_unit_safe_value",
        "TRUSTED_PROXY_CIDRS": "172.30.0.0/24",
        "OPENROUTER_MODEL": "groq/openai/gpt-oss-20b",
        "OPENROUTER_MODEL_CHEAP": "groq/llama-3.1-8b-instant",
        "OPENROUTER_MODEL_PREMIUM": "groq/openai/gpt-oss-20b",
        "OPENROUTER_MODEL_FALLBACKS": (
            "groq/llama-3.1-8b-instant,groq/openai/gpt-oss-120b"
        ),
    }


def commercial_config():
    config = common_config()
    config.update(
        {
            "LAUNCH_PROFILE": "commercial_bootstrap",
            "SANDBOX_ONLY": "false",
            "STRIPE_ANNUAL_PRICING_ENABLED": "false",
            "STRIPE_SECRET_KEY": "sk_live_unit_safe_value",
            "STRIPE_PUBLISHABLE_KEY": "pk_live_unit_safe_value",
        }
    )
    config.update(
        {name: f"price_live_{name.lower()}" for name in _MODULE.STRIPE_MONTHLY_PRICE_ENV_NAMES}
    )
    return config


def sandbox_config():
    config = common_config()
    config.update(
        {
            "LAUNCH_PROFILE": "sandbox",
            "SANDBOX_ONLY": "true",
            "STRIPE_ANNUAL_PRICING_ENABLED": "false",
            "STRIPE_SECRET_KEY": "sk_test_unit_safe_value",
            "STRIPE_PUBLISHABLE_KEY": "pk_test_unit_safe_value",
            "WHATSAPP_TEST_RECIPIENTS": "39000000000",
        }
    )
    return config


def test_valid_commercial_bootstrap_configuration_passes():
    assert validate(commercial_config()) == []


def test_valid_sandbox_configuration_passes():
    assert validate(sandbox_config()) == []


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("DATABASE_URL", "postgresql://u:p@db/x?sslmode=disable#sslmode=require"),
        ("DATABASE_URL", "postgresql://u:sslmode=require@db/x?sslmode=disable"),
        ("DATABASE_URL", "postgresql://u:p@db/x?sslmode=require&sslmode=disable"),
        ("DATABASE_URL", "https://db/x?sslmode=require"),
        ("DATABASE_URL", "postgresql://u:p@db:notaport/x?sslmode=require"),
        ("DATABASE_URL", "postgresql://user:pass@db/x?sslmode=require"),
        ("SUPABASE_URL", "http://project.supabase.co"),
        ("SUPABASE_URL", "https://user:private@project.supabase.co"),
        ("SUPABASE_ANON_KEY", "changeme"),
        ("CORS_ORIGINS", "*"),
        ("CORS_ORIGINS", "http://melpis.it"),
        ("CORS_ORIGINS", "https://unapproved.invalid"),
        ("CORS_ORIGINS", "https://melpis.it/path"),
        ("CORS_ORIGINS", "https://user:private@melpis.it"),
        ("CORS_ORIGINS", ""),
        ("CSRF_TRUSTED_ORIGINS", "https://unapproved.invalid"),
        ("TRUSTED_PROXY_CIDRS", "::/0"),
        ("TRUSTED_PROXY_CIDRS", "0.0.0.0/1"),
        ("TRUSTED_PROXY_CIDRS", ""),
    ),
)
def test_network_config_fails_closed_in_preflight_and_runtime(monkeypatch, name, value):
    from src.core.startup_guard import assert_production_safe

    config = sandbox_config()
    config[name] = value
    assert any(name in finding for finding in validate(config))
    for key, item in config.items():
        monkeypatch.setenv(key, item)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    with pytest.raises(RuntimeError, match=name):
        assert_production_safe()


@pytest.mark.parametrize("mode", ("require", "verify-ca", "verify-full"))
def test_network_config_accepts_effective_tls_modes(monkeypatch, mode):
    from src.core.startup_guard import assert_production_safe

    config = sandbox_config()
    config["DATABASE_URL"] = f"postgresql://u:p@db/x?sslmode={mode}"
    assert validate(config) == []
    for key, item in config.items():
        monkeypatch.setenv(key, item)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert_production_safe()


def test_network_failures_never_echo_configuration_values():
    from src.core.release_config import network_config_errors

    config = sandbox_config()
    config.update({
        "DATABASE_URL": "postgresql://u:private-value@db/x?sslmode=disable",
        "SUPABASE_URL": "https://u:private-value@project.supabase.co",
        "CORS_ORIGINS": "https://u:private-value@melpis.it",
    })
    findings = network_config_errors(config)
    assert len(findings) == 3
    assert "private-value" not in " ".join(findings)


def test_temporary_public_host_requires_its_own_explicit_origin():
    from src.core.release_config import network_config_errors

    config = sandbox_config()
    config["PUBLIC_APP_URL"] = "https://beta.melpis.test"
    assert any("CORS_ORIGINS" in finding for finding in network_config_errors(config))
    config["CORS_ORIGINS"] = "https://beta.melpis.test"
    assert network_config_errors(config) == []


@pytest.mark.parametrize("name", LEGAL_IDENTITY_ENV_NAMES)
def test_production_preflight_blocks_unresolved_legal_identity_tokens(name):
    config = commercial_config()
    config[name] = "<replace-me>"
    assert f"{name}: missing or placeholder" in validate(config)


@pytest.mark.parametrize("name", LEGAL_REVIEW_APPROVAL_ENV_NAMES)
def test_production_preflight_requires_explicit_legal_review(name):
    config = commercial_config()
    config[name] = "false"
    assert f"{name}: must be true after legal review" in validate(config)


@pytest.mark.parametrize("name", LEGAL_DECISION_INTEGER_ENV_NAMES)
def test_production_preflight_requires_explicit_legal_day_decisions(name):
    config = commercial_config()
    config[name] = "<review-me>"
    assert f"{name}: explicit 1..9999-day legal decision required" in validate(config)


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("STRIPE_SECRET_KEY", "sk_test_unit_safe_value"),
        ("STRIPE_PUBLISHABLE_KEY", "pk_test_unit_safe_value"),
    ),
)
def test_commercial_bootstrap_blocks_mixed_stripe_modes(name, value):
    config = commercial_config()
    config[name] = value
    assert any(name in finding for finding in validate(config))


def test_sandbox_blocks_live_stripe_credentials():
    config = sandbox_config()
    config["STRIPE_SECRET_KEY"] = "sk_live_unit_safe_value"
    assert any("STRIPE_SECRET_KEY" in finding for finding in validate(config))


@pytest.mark.parametrize("name", _MODULE.STRIPE_MONTHLY_PRICE_ENV_NAMES)
def test_commercial_bootstrap_requires_monthly_live_prices(name):
    config = commercial_config()
    del config[name]
    assert f"{name}: missing or placeholder" in validate(config)


def test_commercial_bootstrap_rejects_annual_pricing_in_v1_profile():
    config = commercial_config()
    config["STRIPE_ANNUAL_PRICING_ENABLED"] = "true"
    assert "STRIPE_ANNUAL_PRICING_ENABLED: must be false for commercial_bootstrap" in validate(config)


def test_commercial_bootstrap_requires_explicit_annual_flag():
    config = commercial_config()
    config.pop("STRIPE_ANNUAL_PRICING_ENABLED")
    assert "STRIPE_ANNUAL_PRICING_ENABLED: must be false for commercial_bootstrap" in validate(config)


def test_preflight_requires_redis_for_production():
    config = commercial_config()
    config.pop("REDIS_URL")
    assert any(finding.startswith("REDIS_URL:") for finding in validate(config))


@pytest.mark.parametrize("name", ("GROQ_FREE_ACCOUNT_CONFIRMED", "GROQ_ZDR_CONFIRMED"))
def test_profiles_require_groq_free_and_zdr_confirmations(name):
    config = commercial_config()
    config[name] = "false"
    assert f"{name}: must be true" in validate(config)


def test_paid_ai_policy_is_blocked():
    config = commercial_config()
    config["LLM_COST_POLICY"] = "standard"
    assert "LLM_COST_POLICY: must be free_only" in validate(config)


def test_non_allowlisted_model_is_blocked():
    config = commercial_config()
    config["OPENROUTER_MODEL"] = "openai/gpt-4o"
    assert "OPENROUTER_MODEL: model outside free allowlist" in validate(config)


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("GROQ_API_KEY", "gsk_placeholder"),
        ("STRIPE_SECRET_KEY", "sk_live_replace-me"),
        ("STRIPE_WEBHOOK_SECRET", "whsec_xxx"),
    ),
)
def test_placeholder_shaped_production_secrets_are_blocked(name, value):
    config = commercial_config()
    config[name] = value
    assert f"{name}: missing or placeholder" in validate(config)


def test_short_placeholder_key_is_blocked():
    config = commercial_config()
    config["STRIPE_SECRET_KEY"] = "sk_live_no"
    assert "STRIPE_SECRET_KEY: invalid format" in validate(config)


def test_placeholder_database_credentials_are_blocked():
    config = commercial_config()
    config["DATABASE_URL"] = "postgresql://user:pass@host/db?sslmode=require"
    assert "DATABASE_URL: missing or placeholder" in validate(config)


@pytest.mark.parametrize("name", ("GOOGLE_CALENDAR_ENABLED", "GOOGLE_BUSINESS_ENABLED"))
def test_google_integrations_must_be_explicitly_disabled(name):
    config = commercial_config()
    config[name] = "true"
    assert f"{name}: must be false" in validate(config)


def test_unknown_or_implicit_profile_is_blocked():
    config = commercial_config()
    del config["LAUNCH_PROFILE"]
    assert any("LAUNCH_PROFILE" in finding for finding in validate(config))


def test_unbounded_proxy_configuration_is_blocked():
    config = commercial_config()
    config["TRUSTED_PROXY_CIDRS"] = "0.0.0.0/0"
    assert any("bounded proxy" in finding for finding in validate(config))


@pytest.mark.parametrize("name", IMAGE_REFERENCE_ENV_NAMES)
@pytest.mark.parametrize(
    "value",
    (
        "ghcr.io/melpis/api:sha-0123456789abcdef0123456789abcdef01234567",
        "ghcr.io/melpis/api@sha256:" + "A" * 64,
        "ghcr.io/melpis/api@sha256:" + "a" * 63,
    ),
)
def test_release_preflight_requires_digest_only_image_references(name, value):
    config = commercial_config()
    config[name] = value
    assert f"{name}: must be repository@sha256:<64 lowercase hex>" in validate(config)


def test_temporary_caddy_mode_requires_a_real_temporary_host():
    config = commercial_config()
    config["PUBLIC_HOST"] = ""
    assert "PUBLIC_HOST: missing or placeholder" in validate(config)


@pytest.mark.parametrize("host", ("melpis.it", "app.melpis.it"))
def test_temporary_caddy_mode_rejects_final_hosts(host):
    config = commercial_config()
    config["PUBLIC_HOST"] = host
    assert (
        "PUBLIC_HOST: temporary mode cannot use melpis.it or app.melpis.it"
        in validate(config)
    )


@pytest.mark.parametrize("host", ("https://temporary.melpis.test", "127.0.0.1", "temporary"))
def test_temporary_caddy_mode_requires_a_sane_fqdn(host):
    config = commercial_config()
    config["PUBLIC_HOST"] = host
    assert "PUBLIC_HOST: must be a valid temporary FQDN" in validate(config)


def test_release_preflight_blocks_unknown_caddy_site_mode():
    config = commercial_config()
    config["CADDY_SITE_MODE"] = "temporary-and-final"
    assert "CADDY_SITE_MODE: must be temporary or final" in validate(config)


def test_release_preflight_accepts_explicit_openrouter_free_profile():
    config = sandbox_config()
    config.update({"AI_PROVIDER": "openrouter", "AI_MODEL": "openrouter/vendor/model:free", "OPENROUTER_API_KEY": "offline-unit-safe-value"})
    for name in tuple(config):
        if name.startswith("OPENROUTER_MODEL"):
            config.pop(name)
    assert validate(config) == []
    config["AI_MODEL_FALLBACKS"] = "openrouter/vendor/paid"
    assert "AI_MODEL_FALLBACKS: model outside free allowlist" in validate(config)
