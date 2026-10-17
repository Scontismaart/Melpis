"""Fail-fast launch-profile checks for production startup (B1/B4)."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from src.core.cost_policy import FREE_MODELS, is_openrouter_free_model


COMMERCIAL_BOOTSTRAP_PROFILE = "commercial_bootstrap"
SANDBOX_PROFILE = "sandbox"
STRIPE_PRICE_ENV_NAMES = (
    "STRIPE_PRICE_STARTER",
    "STRIPE_PRICE_PRO",
    "STRIPE_PRICE_BUSINESS",
    "STRIPE_PRICE_STARTER_YEARLY",
    "STRIPE_PRICE_PRO_YEARLY",
    "STRIPE_PRICE_BUSINESS_YEARLY",
)
MODEL_ENV_NAMES = (
    "AI_MODEL",
    "AI_MODEL_CHEAP",
    "AI_MODEL_PREMIUM",
    "AI_MODEL_FALLBACKS",
    "AI_MODEL_INTENT",
    "OPENROUTER_MODEL",
    "OPENROUTER_MODEL_CHEAP",
    "OPENROUTER_MODEL_PREMIUM",
    "OPENROUTER_MODEL_FALLBACKS",
    "OPENROUTER_MODEL_INTENT",
)
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


def _require_safe_value(
    name: str, *, prefix: str | None = None, min_length: int = 1
) -> str:
    value = os.getenv(name, "").strip()
    if _is_placeholder(value):
        raise RuntimeError(f"AVVIO BLOCCATO: {name} mancante o placeholder")
    if prefix is not None and not value.startswith(prefix):
        raise RuntimeError(f"AVVIO BLOCCATO: {name} deve iniziare con {prefix}")
    if len(value) < min_length:
        raise RuntimeError(f"AVVIO BLOCCATO: {name} ha un formato non valido")
    return value


def _assert_free_groq_profile() -> None:
    if os.getenv("LLM_COST_POLICY", "").strip().lower() != "free_only":
        raise RuntimeError("AVVIO BLOCCATO: LLM_COST_POLICY deve essere free_only")
    provider = os.getenv("AI_PROVIDER", "groq").strip().lower()
    if provider not in {"groq", "openrouter"}:
        raise RuntimeError("AVVIO BLOCCATO: provider non autorizzato nel profilo EUR 0")
    if provider == "groq" and os.getenv("GROQ_FREE_ACCOUNT_CONFIRMED", "").strip().lower() != "true":
        raise RuntimeError("AVVIO BLOCCATO: account Groq FREE non confermato")
    if provider == "groq" and os.getenv("GROQ_ZDR_CONFIRMED", "").strip().lower() != "true":
        raise RuntimeError("AVVIO BLOCCATO: Groq ZDR non confermato")
    if provider == "groq":
        _require_safe_value("GROQ_API_KEY", prefix="gsk_", min_length=16)
    else:
        _require_safe_value("OPENROUTER_API_KEY", min_length=16)
        _require_safe_value("AI_MODEL", prefix="openrouter/")
    if os.getenv("AI_BASE_URL", "").strip() or os.getenv("AI_API_KEY", "").strip():
        raise RuntimeError("AVVIO BLOCCATO: endpoint/chiave AI custom non autorizzati nel profilo EUR 0")

    for name in MODEL_ENV_NAMES:
        configured = os.getenv(name, "")
        models = (model.strip() for model in configured.split(","))
        allowed = (lambda model: model in FREE_MODELS) if provider == "groq" else is_openrouter_free_model
        if any(not allowed(model) for model in models if model):
            raise RuntimeError(
                f"AVVIO BLOCCATO: {name} contiene un modello fuori allowlist"
            )


def _assert_stripe_profile(profile: str) -> None:
    sandbox_only = os.getenv("SANDBOX_ONLY", "").strip().lower()
    if profile == COMMERCIAL_BOOTSTRAP_PROFILE:
        if sandbox_only != "false":
            raise RuntimeError(
                "AVVIO BLOCCATO: commercial_bootstrap richiede SANDBOX_ONLY=false"
            )
        _require_safe_value("STRIPE_SECRET_KEY", prefix="sk_live_", min_length=16)
        _require_safe_value("STRIPE_PUBLISHABLE_KEY", prefix="pk_live_", min_length=16)
        _require_safe_value("STRIPE_WEBHOOK_SECRET", prefix="whsec_", min_length=16)
        for name in STRIPE_PRICE_ENV_NAMES:
            _require_safe_value(name, prefix="price_", min_length=12)
        return

    if sandbox_only != "true":
        raise RuntimeError("AVVIO BLOCCATO: sandbox richiede SANDBOX_ONLY=true")
    _require_safe_value("STRIPE_SECRET_KEY", prefix="sk_test_", min_length=16)
    _require_safe_value("STRIPE_PUBLISHABLE_KEY", prefix="pk_test_", min_length=16)
    _require_safe_value("STRIPE_WEBHOOK_SECRET", prefix="whsec_", min_length=16)


def assert_production_safe() -> None:
    from src.core.security.docs import is_production

    stripe_secret = os.getenv("STRIPE_SECRET_KEY", "").strip()
    stripe_publishable = os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()
    if not is_production():
        if stripe_secret.startswith("sk_live_") or stripe_publishable.startswith(
            "pk_live_"
        ):
            raise RuntimeError(
                "AVVIO BLOCCATO: credenziali Stripe live non autorizzate fuori produzione."
            )
        return

    if os.getenv("AUTH_COOKIE_SECURE", "true").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        raise RuntimeError(
            "AVVIO BLOCCATO: AUTH_COOKIE_SECURE deve essere true in produzione"
        )
    public_app_url = urlparse(os.getenv("PUBLIC_APP_URL", "").strip())
    if public_app_url.scheme != "https" or not public_app_url.netloc:
        raise RuntimeError(
            "AVVIO BLOCCATO: PUBLIC_APP_URL deve usare HTTPS in produzione"
        )

    demo = os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")
    if demo:
        raise RuntimeError(
            "AVVIO BLOCCATO: DEMO_MODE attivo con APP_ENV=production (B1). "
            "Rimuovi DEMO_MODE dalla configurazione di produzione."
        )

    profile = os.getenv("LAUNCH_PROFILE", "").strip().lower()
    if profile not in {COMMERCIAL_BOOTSTRAP_PROFILE, SANDBOX_PROFILE}:
        raise RuntimeError(
            "AVVIO BLOCCATO: LAUNCH_PROFILE deve essere commercial_bootstrap o sandbox"
        )

    _assert_free_groq_profile()
    _assert_stripe_profile(profile)

    for name in ("GOOGLE_CALENDAR_ENABLED", "GOOGLE_BUSINESS_ENABLED"):
        if os.getenv(name, "false").strip().lower() != "false":
            raise RuntimeError(f"AVVIO BLOCCATO: {name} deve restare false al lancio")

    key = os.getenv("ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "AVVIO BLOCCATO: ENCRYPTION_KEY mancante in produzione (B4). "
            'Genera con: python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    from cryptography.fernet import Fernet

    try:
        Fernet(key)
    except Exception as exc:
        raise RuntimeError(
            "AVVIO BLOCCATO: ENCRYPTION_KEY non e' una chiave Fernet valida (B4)."
        ) from exc

    from src.core.release_config import network_config_errors

    findings = network_config_errors(os.environ)
    if findings:
        raise RuntimeError("AVVIO BLOCCATO: " + "; ".join(findings))
