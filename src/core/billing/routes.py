import os
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from src.core.auth.dependencies import require_ruolo, require_mfa
from src.core.auth.audit import audit_log
from src.core.billing.plans import PLANS, TRIAL_MESSAGES_LIMIT
from src.core.billing.webhook_handler import handle_stripe_webhook

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])


def _annual_pricing_enabled() -> bool:
    return os.getenv("STRIPE_ANNUAL_PRICING_ENABLED", "").strip().lower() == "true"


@router.get("/public-config")
async def get_public_billing_config():
    # Deliberately expose only the feature boolean; never publish Price IDs or
    # Stripe credentials. Missing/invalid values remain disabled.
    return {"annual_pricing_enabled": _annual_pricing_enabled()}


class CheckoutSessionRequest(BaseModel):
    plan: str
    success_url: str
    cancel_url: str
    interval: str = "monthly"  # "monthly" | "yearly"


def _resolve_price_id(plan, interval: str) -> str:
    if interval not in ("monthly", "yearly"):
        raise ValueError(f"interval non valido: {interval}")
    if interval == "yearly" and not _annual_pricing_enabled():
        return ""
    return plan.stripe_price_id_yearly if interval == "yearly" else plan.stripe_price_id


def _get_stripe():
    import stripe

    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    profile = os.getenv("LAUNCH_PROFILE", "").strip().lower()
    sandbox_only = os.getenv("SANDBOX_ONLY", "true").strip().lower()
    app_env = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).lower()
    if profile == "commercial_bootstrap":
        if (
            app_env != "production"
            or sandbox_only != "false"
            or not key.startswith("sk_live_")
        ):
            raise HTTPException(
                status_code=503,
                detail="Commercial bootstrap requires production Stripe live mode",
            )
    elif profile == "sandbox":
        if sandbox_only != "true" or not key.startswith("sk_test_"):
            raise HTTPException(
                status_code=503, detail="Sandbox requires Stripe test mode"
            )
    elif not key.startswith("sk_test_"):
        raise HTTPException(
            status_code=503,
            detail="Stripe live mode requires the explicit commercial_bootstrap profile",
        )
    stripe.api_key = key
    return stripe


async def _stripe_call(call, *args, **kwargs):
    return await asyncio.to_thread(call, *args, **kwargs)


@router.post("/create-checkout-session")
async def create_checkout_session(
    req: CheckoutSessionRequest,
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    _mfa: dict = Depends(require_mfa()),
):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    if req.plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {req.plan}")

    org_id = user["organization_id"]
    org = await repo.get_organization_billing(org_id)

    plan = PLANS[req.plan]
    try:
        price_id = _resolve_price_id(plan, req.interval)
    except ValueError:
        raise HTTPException(status_code=400, detail="interval non valido: monthly|yearly")
    if not price_id:
        raise HTTPException(
            status_code=503,
            detail=f"Stripe price ID ({req.interval}) non configurato per: {req.plan}",
        )

    from src.core.billing.checkout import create_durable_checkout, validate_redirect_url
    # PUBLIC_APP_URL is the configured same-origin host, including temporary
    # deployments. Keep redirects restricted to that single explicit origin.
    base = os.getenv("PUBLIC_APP_URL", "https://melpis.it").strip().rstrip("/")
    return await create_durable_checkout(repo, org_id, request.headers.get("Idempotency-Key"), {
        "price_id": price_id, "plan": req.plan, "interval": req.interval,
        "success_url": validate_redirect_url(req.success_url, base),
        "cancel_url": validate_redirect_url(req.cancel_url, base),
    }, _get_stripe(), _stripe_call)


@router.post("/create-portal-session")
async def create_portal_session(
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    _mfa: dict = Depends(require_mfa()),
):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    org_id = user["organization_id"]
    org = await repo.get_organization_billing(org_id)
    customer_id = org.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer found")

    st = _get_stripe()
    return_url = os.getenv("STRIPE_RETURN_URL", "http://localhost:5173/settings/billing")
    session = await _stripe_call(
        st.billing_portal.Session.create,
        customer=customer_id,
        return_url=return_url,
    )

    from src.api.routes.common import audit_event
    await audit_event(request, user, "billing.portal_session_created", "organizations", str(org_id))

    return {"url": session.url}


@router.get("/subscription")
async def get_subscription(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    org_id = user["organization_id"]
    billing = await repo.get_organization_billing(org_id)
    plan = billing.get("plan")
    status = billing.get("subscription_status")
    messages_limit = billing.get("messages_limit")
    users_limit = billing.get("users_limit")
    if (status == "trialing" or not plan):
        if messages_limit is None:
            messages_limit = TRIAL_MESSAGES_LIMIT
        if users_limit is None:
            users_limit = 3

    return {
        "plan": plan,
        "subscription_status": status,
        "messages_limit": messages_limit,
        "users_limit": users_limit,
        "whatsapp_numbers_limit": billing.get("whatsapp_numbers_limit") or 1,
        "messages_used_this_period": billing.get("messages_used_this_period"),
        "current_period_start": billing.get("current_period_start"),
        "current_period_end": billing.get("current_period_end"),
        "trial_start": billing.get("trial_start"),
        "trial_end": billing.get("trial_end"),
    }


@router.get("/usage")
async def get_usage(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    org_id = user["organization_id"]
    billing = await repo.get_organization_billing(org_id)
    limit = billing.get("messages_limit")
    if limit is None and (billing.get("subscription_status") == "trialing" or not billing.get("plan")):
        limit = TRIAL_MESSAGES_LIMIT
    used = billing.get("messages_used_this_period", 0) or 0

    return {
        "messages_used": used,
        "messages_limit": limit,
        "percentage": round((used / limit * 100), 1) if limit and limit > 0 else 0,
    }


@router.post("/webhook")
async def billing_webhook(request: Request):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    body = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    import stripe
    try:
        event = stripe.Webhook.construct_event(body, sig_header, secret)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    trial_days = request.app.state.billing_config.stripe_trial_days
    result = await handle_stripe_webhook(event.to_dict_recursive(), repo, trial_days)
    if result is None:
        return {"status": "ignored"}
    # Audit is committed atomically by the handler, not duplicated here.
    return result
