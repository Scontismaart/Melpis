"""Reviewed recovery-only model identity; never infer arbitrary dated aliases.

The public catalog proves identity, not historical charges. Zero cost must also
come from the authenticated generation receipt for this particular attempt.
"""

from decimal import Decimal, InvalidOperation

from src.core.provider_accounting import provider_model_matches

_REQUESTED = "openrouter/xingchen-agi/xing4.0-29b-a4b:free"
_ACTUAL = "xingchen-agi/xing4.0-29b-a4b-20261015:free"
_SOURCE_SHA256 = "94d4db5f5a64d03c63dd64258047f94549284e6b30ce917e667a315cf1a2372e"


def reviewed_recovery_binding(requested, actual, provider_cost_usd):
    """Return provenance only for this reviewed pair and verified zero charge."""
    if requested != _REQUESTED or actual != _ACTUAL:
        return None
    if type(provider_cost_usd) not in {str, int, Decimal}:
        return None
    try:
        cost = Decimal(provider_cost_usd)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not cost.is_finite() or cost != 0:
        return None
    return {
        "model_binding_source": "openrouter_public_catalog_20261025",
        "model_binding_sha256": _SOURCE_SHA256,
        "pricing_source": "openrouter_generation_receipt_zero_cost",
        "pricing_model": _ACTUAL,
    }


def recovery_model_matches(requested, actual, provider_cost_usd):
    """Preserve existing identities; extend recovery with one reviewed pair."""
    return provider_model_matches(requested, actual) or (
        reviewed_recovery_binding(requested, actual, provider_cost_usd) is not None
    )
