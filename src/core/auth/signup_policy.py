"""Server-side beta enrollment policy for new tenant access."""

from __future__ import annotations

import os

_LOCAL_ENVIRONMENTS = {"development", "dev", "local", "test", "testing"}
_ALLOWLIST_ENV = "MELPIS_BETA_ALLOWED_EMAILS"


def beta_email_is_allowed(email: object) -> bool:
    """Allow only exact, operator-listed addresses outside local development.

    Unknown/non-local environments and an unset allowlist deny new enrollment.
    Existing tenant memberships remain governed by normal authorization checks.
    """
    environment = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", ""))
    if environment.strip().lower() in _LOCAL_ENVIRONMENTS:
        return True
    if not isinstance(email, str):
        return False
    candidate = email.strip().lower()
    if not candidate:
        return False
    allowed = {
        entry.strip().lower()
        for entry in os.getenv(_ALLOWLIST_ENV, "").split(",")
        if entry.strip()
    }
    return candidate in allowed
