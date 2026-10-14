import os
from dataclasses import dataclass

TRIAL_MESSAGES_LIMIT = 150


@dataclass
class Plan:
    name: str
    stripe_price_id: str
    messages_limit: int | None
    users_limit: int | None
    whatsapp_numbers_limit: int | None
    has_reviews: bool
    has_rag: bool
    price_monthly_eur: int
    stripe_price_id_yearly: str = ""
    price_yearly_eur: int = 0


PLANS: dict[str, Plan] = {
    "starter": Plan(
        name="Essenziale",
        stripe_price_id=os.getenv("STRIPE_PRICE_STARTER", ""),
        messages_limit=500,
        users_limit=1,
        whatsapp_numbers_limit=1,
        has_reviews=False,
        has_rag=False,
        price_monthly_eur=29,
        stripe_price_id_yearly=os.getenv("STRIPE_PRICE_STARTER_YEARLY", ""),
        price_yearly_eur=288,
    ),
    "pro": Plan(
        name="Crescita",
        stripe_price_id=os.getenv("STRIPE_PRICE_PRO", ""),
        messages_limit=2000,
        users_limit=3,
        whatsapp_numbers_limit=1,
        has_reviews=True,
        has_rag=False,
        price_monthly_eur=69,
        stripe_price_id_yearly=os.getenv("STRIPE_PRICE_PRO_YEARLY", ""),
        price_yearly_eur=708,
    ),
    "business": Plan(
        name="Scala",
        stripe_price_id=os.getenv("STRIPE_PRICE_BUSINESS", ""),
        messages_limit=10000,
        users_limit=None,
        whatsapp_numbers_limit=None,
        has_reviews=True,
        has_rag=True,
        price_monthly_eur=149,
        stripe_price_id_yearly=os.getenv("STRIPE_PRICE_BUSINESS_YEARLY", ""),
        price_yearly_eur=1548,
    ),
}


def get_plan(plan_slug: str) -> Plan:
    if plan_slug not in PLANS:
        raise ValueError(f"Unknown plan: {plan_slug}. Valid: {list(PLANS.keys())}")
    return PLANS[plan_slug]
