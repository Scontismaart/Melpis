"""B5: griglia prezzi Essenziale/Crescita/Scala + supporto annuale."""
import json
import re
from pathlib import Path

import pytest
from src.core.billing.plans import PLANS


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_prezzi_canonici():
    assert PLANS["starter"].price_monthly_eur == 29
    assert PLANS["pro"].price_monthly_eur == 69
    assert PLANS["business"].price_monthly_eur == 149


def test_prezzi_annuali_configurati():
    assert PLANS["starter"].price_yearly_eur == 288   # 24*12
    assert PLANS["pro"].price_yearly_eur == 708       # 59*12
    assert PLANS["business"].price_yearly_eur == 1548 # 129*12


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


def test_resolve_price_id_annual_disabled_by_default(monkeypatch):
    from src.core.billing.routes import _resolve_price_id
    monkeypatch.delenv("STRIPE_ANNUAL_PRICING_ENABLED", raising=False)
    p = PLANS["pro"]
    monkeypatch.setattr(p, "stripe_price_id", "price_m")
    monkeypatch.setattr(p, "stripe_price_id_yearly", "price_y")
    assert _resolve_price_id(p, "monthly") == "price_m"
    assert _resolve_price_id(p, "yearly") == ""


def test_resolve_price_id_annual_enabled(monkeypatch):
    from src.core.billing.routes import _resolve_price_id
    monkeypatch.setenv("STRIPE_ANNUAL_PRICING_ENABLED", "true")
    p = PLANS["pro"]
    monkeypatch.setattr(p, "stripe_price_id", "price_m")
    monkeypatch.setattr(p, "stripe_price_id_yearly", "price_y")
    assert _resolve_price_id(p, "monthly") == "price_m"
    assert _resolve_price_id(p, "yearly") == "price_y"


def test_resolve_price_id_intervallo_invalido():
    from src.core.billing.routes import _resolve_price_id
    with pytest.raises(ValueError):
        _resolve_price_id(PLANS["pro"], "settimanale")
