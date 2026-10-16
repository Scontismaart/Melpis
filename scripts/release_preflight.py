"""Read-only configuration checks for explicit commercial or sandbox releases.

Run with --env-file .env.production; values and secrets are never printed.
Passing this check is not evidence that a cloud account or free quota exists.
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.release_config import network_config_errors


COMMERCIAL_BOOTSTRAP_PROFILE = "commercial_bootstrap"
SANDBOX_PROFILE = "sandbox"
STRIPE_MONTHLY_PRICE_ENV_NAMES = (
    "STRIPE_PRICE_STARTER",
    "STRIPE_PRICE_PRO",
    "STRIPE_PRICE_BUSINESS",
)
STRIPE_YEARLY_PRICE_ENV_NAMES = (
    "STRIPE_PRICE_STARTER_YEARLY",
    "STRIPE_PRICE_PRO_YEARLY",
    "STRIPE_PRICE_BUSINESS_YEARLY",
)
STRIPE_PRICE_ENV_NAMES = STRIPE_MONTHLY_PRICE_ENV_NAMES + STRIPE_YEARLY_PRICE_ENV_NAMES
MODEL_ENV_NAMES = (
    "AI_MODEL", "AI_MODEL_CHEAP", "AI_MODEL_PREMIUM", "AI_MODEL_FALLBACKS", "AI_MODEL_INTENT",
    "OPENROUTER_MODEL",
    "OPENROUTER_MODEL_CHEAP",
    "OPENROUTER_MODEL_PREMIUM",
    "OPENROUTER_MODEL_FALLBACKS",
    "OPENROUTER_MODEL_INTENT",
)
FREE_MODELS = frozenset(
    {
        "groq/llama-3.1-8b-instant",
        "groq/openai/gpt-oss-20b",
        "groq/openai/gpt-oss-120b",
    }
)
_OPENROUTER_FREE_MODEL = re.compile(r"^openrouter/[^/:\s]+/[^/:\s]+:free$")


def is_openrouter_free_model(model: str) -> bool:
    return bool(_OPENROUTER_FREE_MODEL.fullmatch(model))
IMAGE_REFERENCE_ENV_NAMES = ("MELPIS_API_IMAGE_REF", "MELPIS_WEB_IMAGE_REF")
LEGAL_IDENTITY_ENV_NAMES = (
    "LEGAL_ENTITY_NAME",
    "LEGAL_ENTITY_REGISTERED_OFFICE",
    "LEGAL_ENTITY_VAT_NUMBER",
    "LEGAL_PRIVACY_CONTACT_EMAIL",
    "LEGAL_FORUM",
    "LEGAL_DOCUMENT_EFFECTIVE_DATE",
)
LEGAL_REVIEW_APPROVAL_ENV_NAMES = (
    "LEGAL_PUBLIC_DOCUMENTS_REVIEWED",
    "LEGAL_PRIVACY_REVIEW_APPROVED",
    "LEGAL_TERMS_REVIEW_APPROVED",
    "LEGAL_DPA_REVIEW_APPROVED",
)
LEGAL_DECISION_INTEGER_ENV_NAMES = (
    "LEGAL_PAYMENT_SUSPENSION_DAYS",
    "LEGAL_ACCOUNT_TERMINATION_DAYS",
)
_DIGEST_IMAGE_REFERENCE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}$"
)
_FQDN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}$"
)
_FINAL_CADDY_HOSTS = frozenset({"melpis.it", "app.melpis.it"})
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
    "user:pass@",
    "username:password@",
)


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or any(
        marker in normalized for marker in _PLACEHOLDER_MARKERS
    )


def _require(config, errors, name, *, prefix=None, min_length=1):
    value = str(config.get(name) or "").strip()
    if _is_placeholder(value):
        errors.append(f"{name}: missing or placeholder")
    elif prefix is not None and not value.startswith(prefix):
        errors.append(f"{name}: must start with {prefix}")
    elif len(value) < min_length:
        errors.append(f"{name}: invalid format")


def _require_digest_image_reference(config, errors, name):
    value = str(config.get(name) or "").strip()
    if _is_placeholder(value) or not _DIGEST_IMAGE_REFERENCE.fullmatch(value):
        errors.append(f"{name}: must be repository@sha256:<64 lowercase hex>")


def _require_temporary_host(config, errors):
    value = str(config.get("PUBLIC_HOST") or "").strip().lower()
    if _is_placeholder(value):
        errors.append("PUBLIC_HOST: missing or placeholder")
    elif value in _FINAL_CADDY_HOSTS:
        errors.append("PUBLIC_HOST: temporary mode cannot use melpis.it or app.melpis.it")
    elif not _FQDN.fullmatch(value):
        errors.append("PUBLIC_HOST: must be a valid temporary FQDN")


def validate(config):
    errors = []
    profile = str(config.get("LAUNCH_PROFILE") or "").strip().lower()

    required = (
        "DATABASE_URL",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "ENCRYPTION_KEY",
        "PUBLIC_APP_URL",
        "REDIS_URL",
        "META_APP_SECRET",
        "META_VERIFY_TOKEN",
    )
    for name in required:
        _require(config, errors, name)
    redis = urlparse(str(config.get("REDIS_URL") or "").strip())
    if redis.scheme not in {"redis", "rediss"} or not redis.hostname:
        errors.append("REDIS_URL: valid redis:// or rediss:// endpoint required")
    provider = str(config.get("AI_PROVIDER") or "groq").strip().lower()
    if provider == "groq":
        _require(config, errors, "GROQ_API_KEY", prefix="gsk_", min_length=16)
    elif provider == "openrouter":
        _require(config, errors, "OPENROUTER_API_KEY", min_length=16)
        _require(config, errors, "AI_MODEL", prefix="openrouter/")
    else:
        errors.append("AI_PROVIDER: unsupported free-only provider")
    if str(config.get("AI_BASE_URL") or "").strip() or str(config.get("AI_API_KEY") or "").strip():
        errors.append("AI_BASE_URL/AI_API_KEY: forbidden in free-only profile")
    for name in IMAGE_REFERENCE_ENV_NAMES:
        _require_digest_image_reference(config, errors, name)
    # Public legal copy is deliberately tokenized rather than fabricated. A
    # production release must supply every reviewed identity value.
    for name in LEGAL_IDENTITY_ENV_NAMES:
        _require(config, errors, name)
    # These deliberately require an affirmative, reviewed decision. They are
    # separate from public identity data so a populated template is never
    # mistaken for legal approval.
    for name in LEGAL_REVIEW_APPROVAL_ENV_NAMES:
        if config.get(name) != "true":
            errors.append(f"{name}: must be true after legal review")
    for name in LEGAL_DECISION_INTEGER_ENV_NAMES:
        value = str(config.get(name) or "").strip()
        if not re.fullmatch(r"[1-9][0-9]{0,3}", value):
            errors.append(f"{name}: explicit 1..9999-day legal decision required")

    caddy_site_mode = str(config.get("CADDY_SITE_MODE") or "").strip()
    if caddy_site_mode not in {"temporary", "final"}:
        errors.append("CADDY_SITE_MODE: must be temporary or final")
    elif caddy_site_mode == "temporary":
        _require_temporary_host(config, errors)

    expected = {
        "APP_ENV": "production",
        "LLM_COST_POLICY": "free_only",
        "AUTH_COOKIE_SECURE": "true",
        "RATE_LIMIT_BACKEND": "redis",
        "GOOGLE_CALENDAR_ENABLED": "false",
        "GOOGLE_BUSINESS_ENABLED": "false",
    }
    for name, expected_value in expected.items():
        if config.get(name) != expected_value:
            errors.append(f"{name}: must be {expected_value}")
    if provider == "groq":
        for name in ("GROQ_FREE_ACCOUNT_CONFIRMED", "GROQ_ZDR_CONFIRMED"):
            if config.get(name) != "true":
                errors.append(f"{name}: must be true")

    if str(config.get("DEMO_MODE", "")).lower() in {"true", "1", "yes"}:
        errors.append("DEMO_MODE: forbidden")

    if profile == COMMERCIAL_BOOTSTRAP_PROFILE:
        if config.get("SANDBOX_ONLY") != "false":
            errors.append("SANDBOX_ONLY: must be false for commercial_bootstrap")
        _require(config, errors, "STRIPE_SECRET_KEY", prefix="sk_live_", min_length=16)
        _require(
            config, errors, "STRIPE_PUBLISHABLE_KEY", prefix="pk_live_", min_length=16
        )
        _require(
            config, errors, "STRIPE_WEBHOOK_SECRET", prefix="whsec_", min_length=16
        )
        annual_enabled = str(config.get("STRIPE_ANNUAL_PRICING_ENABLED") or "").strip().lower()
        if annual_enabled != "false":
            errors.append("STRIPE_ANNUAL_PRICING_ENABLED: must be false for commercial_bootstrap")
        for name in STRIPE_MONTHLY_PRICE_ENV_NAMES:
            _require(config, errors, name, prefix="price_", min_length=12)
    elif profile == SANDBOX_PROFILE:
        if config.get("SANDBOX_ONLY") != "true":
            errors.append("SANDBOX_ONLY: must be true for sandbox")
        _require(config, errors, "STRIPE_SECRET_KEY", prefix="sk_test_", min_length=16)
        _require(
            config, errors, "STRIPE_PUBLISHABLE_KEY", prefix="pk_test_", min_length=16
        )
        _require(
            config, errors, "STRIPE_WEBHOOK_SECRET", prefix="whsec_", min_length=16
        )
        _require(config, errors, "WHATSAPP_TEST_RECIPIENTS")
        if str(config.get("STRIPE_ANNUAL_PRICING_ENABLED") or "").strip().lower() != "false":
            errors.append("STRIPE_ANNUAL_PRICING_ENABLED: must be false in sandbox")
    else:
        errors.append("LAUNCH_PROFILE: must be commercial_bootstrap or sandbox")

    for name in MODEL_ENV_NAMES:
        models = (str(config.get(name) or "")).split(",")
        allowed = (lambda model: model in FREE_MODELS) if provider == "groq" else is_openrouter_free_model
        if any(not allowed(model.strip()) for model in models if model.strip()):
            errors.append(f"{name}: model outside free allowlist")

    errors.extend(network_config_errors(config))

    try:
        from cryptography.fernet import Fernet

        Fernet(config.get("ENCRYPTION_KEY") or "")
    except (ValueError, TypeError):
        errors.append("ENCRYPTION_KEY: invalid Fernet key")

    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error("Environment file not found")
    findings = validate(dotenv_values(args.env_file))
    for finding in findings:
        print(f"BLOCK: {finding}")
    print(
        "Configuration blocked"
        if findings
        else "Configuration passes; external release evidence still required"
    )
    raise SystemExit(bool(findings))
