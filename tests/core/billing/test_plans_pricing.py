"""Prezzi e limiti mensili Essenziale, Crescita e Scala."""
import json
import re
from pathlib import Path

from src.core.billing.plans import PLANS


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_prezzi_canonici():
    assert PLANS["starter"].price_monthly_eur == 29
    assert PLANS["pro"].price_monthly_eur == 69
    assert PLANS["business"].price_monthly_eur == 149


def test_prezzi_mensili_coerenti_tra_api_dashboard_e_locali():
    expected = {"starter": 29, "pro": 69, "business": 149}
    for slug, price in expected.items():
        assert PLANS[slug].price_monthly_eur == price

    dashboard_settings = (REPO_ROOT / "web" / "dashboard-settings.js").read_text(encoding="utf-8")
    assert "const accountPlans = [" in dashboard_settings, "account plan data remains owned by dashboard-settings.js"
    account_plans = dashboard_settings.split("const accountPlans = [", 1)[1].split("\n      ];", 1)[0]
    dashboard_prices = {
        slug: int(price)
        for slug, price in re.findall(r'slug: "(starter|pro|business)"[\s\S]*?prezzo: "€(\d+)"', account_plans)
    }
    assert dashboard_prices == expected

    for locale_root in ("locales", "web/landing/locales", "web/locales"):
        for locale in ("it", "en", "es", "fr", "de"):
            pricing = json.loads(
                (REPO_ROOT / locale_root / locale / "pricing.json").read_text(encoding="utf-8")
            )
            assert {
                "starter": pricing["plans"]["essential"]["price_monthly"],
                "pro": pricing["plans"]["growth"]["price_monthly"],
                "business": pricing["plans"]["scale"]["price_monthly"],
            } == expected

    from src.core.billing.routes import CheckoutSessionRequest

    assert CheckoutSessionRequest(
        plan="pro", success_url="https://qa.invalid/app/billing", cancel_url="https://qa.invalid/app/billing",
    ).interval == "monthly"


def test_nomi_commerciali():
    assert PLANS["starter"].name == "Essenziale"
    assert PLANS["pro"].name == "Crescita"
    assert PLANS["business"].name == "Scala"


def test_limiti_invariati():
    assert PLANS["starter"].messages_limit == 500
    assert PLANS["starter"].users_limit == 1
    assert PLANS["pro"].messages_limit == 2000
    assert PLANS["pro"].users_limit == 3
    assert PLANS["business"].messages_limit == 10000
    assert PLANS["business"].users_limit is None
    assert PLANS["pro"].has_reviews is True
    assert PLANS["business"].has_rag is True
