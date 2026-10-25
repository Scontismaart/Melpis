"""Guard the approved B04 plan, trial, and integration copy contract."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.billing.plans import PLANS, TRIAL_MESSAGES_LIMIT


ROOT = Path(__file__).resolve().parents[3]
LOCALE_ROOTS = ("locales", "web/locales", "web/landing/locales")
LANGUAGES = ("it", "en", "es", "fr", "de")
PLAN_SLUGS = {"starter": "essential", "pro": "growth", "business": "scale"}
MONTHLY_PRICES = {"starter": 29, "pro": 69, "business": 149}
MONTHLY_LIMITS = {"starter": 500, "pro": 2000, "business": 10000}
RAG_COPY = {
    "it": "PDF/RAG incluso; quota file da definire",
    "en": "PDF/RAG included; file quota to be defined",
    "es": "PDF/RAG incluido; cuota de archivos por definir",
    "fr": "PDF/RAG inclus ; quota de fichiers à définir",
    "de": "PDF/RAG inklusive; Dateikontingent noch festzulegen",
}
CALENDAR_COPY = {
    "it": "Google Calendar opzionale; richiede configurazione autorizzata",
    "en": "Google Calendar is optional; authorized setup is required",
    "es": "Google Calendar es opcional; requiere configuración autorizada",
    "fr": "Google Calendar est facultatif ; une configuration autorisée est requise",
    "de": "Google Calendar ist optional; eine autorisierte Einrichtung ist erforderlich",
}
TRIAL_ORDER_COPY = {
    "it": "al primo limite raggiunto",
    "en": "whichever comes first",
    "es": "lo que ocurra primero",
    "fr": "selon la première limite atteinte",
    "de": "je nachdem, was zuerst eintritt",
}


def _pricing(root: str, language: str) -> dict:
    path = ROOT / root / language / "pricing.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_plan_catalog_and_trial_keep_the_approved_entitlements():
    assert TRIAL_MESSAGES_LIMIT == 150
    for slug, plan in PLANS.items():
        assert plan.price_monthly_eur == MONTHLY_PRICES[slug]
        assert plan.messages_limit == MONTHLY_LIMITS[slug]
        assert plan.has_rag is True


@pytest.mark.parametrize("locale_root", LOCALE_ROOTS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_pricing_locales_state_rag_and_calendar_copy_without_quotas(locale_root, language):
    pricing = _pricing(locale_root, language)

    for slug, plan in PLANS.items():
        card = pricing["plans"][PLAN_SLUGS[slug]]
        assert card["price_monthly"] == MONTHLY_PRICES[slug]
        features = card["features"]
        assert features.count(RAG_COPY[language]) == 1
        assert not any("R2" in item or re.search(r"\b(?:50\s*MB|100\s*MB|1\s*GB|5\s*GB)\b", item, re.I) for item in features)

    growth_features = pricing["plans"]["growth"]["features"]
    calendar_lines = [item for item in growth_features if "Google Calendar" in item]
    assert calendar_lines == [CALENDAR_COPY[language]]

    trial_copy = pricing["hero"]["subtitle"]
    assert "7" in trial_copy and "150" in trial_copy
    assert "PDF/RAG" in trial_copy
    assert TRIAL_ORDER_COPY[language] in trial_copy


def test_dashboard_account_plan_cards_match_rag_and_optional_calendar_copy():
    source = (ROOT / "web" / "dashboard-settings.js").read_text(encoding="utf-8")
    start = source.index("const accountPlans = [")
    end = source.index("\n      ];", start)
    account_plans = source[start:end]

    for slug in ("starter", "pro", "business"):
        match = re.search(
            rf'slug: "{slug}"[\s\S]*?features:\s*\[([\s\S]*?)\], popolare:',
            account_plans,
        )
        assert match, f"missing {slug} account plan"
        assert match.group(1).count(RAG_COPY["it"]) == 1
        assert "Conoscenza AI con documenti" not in match.group(1)

    assert account_plans.count(CALENDAR_COPY["it"]) == 2
    assert "Sincronizzazione Google Calendar" not in account_plans


@pytest.mark.asyncio
async def test_active_trial_maps_to_the_rag_enabled_plan(monkeypatch):
    from src.api.routes import common

    async def trial_snapshot(repo, organization_id):
        assert organization_id == "org-b04-test"
        return {
            "subscription_status": "trialing",
            "trial_end": datetime.now(timezone.utc) + timedelta(days=1),
            "plan": None,
        }

    monkeypatch.setattr(common, "get_billing_snapshot", trial_snapshot)
    assert await common.check_feature_blocked_by_plan(object(), "org-b04-test", "rag") is None
