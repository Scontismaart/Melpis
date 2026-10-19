import os
import asyncio
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from src.core.auth.dependencies import require_ruolo, require_mfa
from src.core.billing.plans import PLANS, TRIAL_MESSAGES_LIMIT
from src.core.billing.webhook_handler import handle_stripe_webhook

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])


def _annual_pricing_enabled() -> bool:
    if os.getenv("LAUNCH_PROFILE", "").strip().lower() == "commercial_bootstrap":
        return False
    return os.getenv("STRIPE_ANNUAL_PRICING_ENABLED", "").strip().lower() == "true"


class CheckoutSessionRequest(BaseModel):
    plan: str
    success_url: str
    cancel_url: str
    interval: str = "monthly"  # "monthly" | "yearly"


class ProviderEvidenceAttestations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_account_and_request_verified: StrictBool
    terminal_outcome_verified: StrictBool
    metrics_verified: StrictBool


class ProviderEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    evidence_object_id: str = Field(pattern=r"^a03e_[0-9a-f]{32}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestations: ProviderEvidenceAttestations


class ProviderEvidenceApprovalRequest(ProviderEvidenceRequest):
    proposal_version: StrictInt = Field(ge=1)


@router.get("/provider-attempts/recovery")
async def list_provider_attempt_recovery(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Expose only the current tenant's unresolved accounting attempts."""
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Provider accounting recovery unavailable")
    billing_repo = getattr(repo, "billing_repo", repo)
    try:
        attempts = await billing_repo.list_provider_attempt_recovery(user["organization_id"])
    except Exception:
        raise HTTPException(status_code=503, detail="Provider accounting recovery unavailable") from None
    return {"attempts": attempts}


@router.post(
    "/provider-attempts/{attempt_id}/recover",
    description=(
        "Fence an expired A03 worker and reconcile only proven pre-dispatch or provider-verified usage. "
        "One active owner/manager with fresh MFA may trigger the deterministic recovery; no metrics are accepted."
    ),
)
async def recover_provider_attempt(
    attempt_id: str,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
    mfa_user: dict = Depends(require_mfa()),
):
    """Reconcile one tenant's expired A03 attempt without clearing unknown spend."""
    try:
        attempt_uuid = uuid.UUID(attempt_id)
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(status_code=422, detail="Invalid provider attempt") from None
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Provider accounting recovery unavailable")
    billing_repo = getattr(repo, "billing_repo", repo)
    actor = user.get("auth_user_id")
    aal = mfa_user.get("aal")
    auth_time = mfa_user.get("auth_time")
    try:
        result = await billing_repo.recover_provider_attempt(
            user["organization_id"], attempt_uuid,
            actor_auth_user_id=actor, aal=aal, auth_time=auth_time,
        )
        if result.get("state") == "lookup_required":
            from src.core.provider_recovery import (
                configured_openrouter_key, fetch_openrouter_generation,
                verify_openrouter_generation,
            )

            api_key = configured_openrouter_key()
            raw = await fetch_openrouter_generation(api_key, result["generation_id"])
            evidence = verify_openrouter_generation(
                raw, generation_id=result["generation_id"],
                attempt_id=result["attempt_id"], requested_model=result["requested_model"],
                attempt_created_at=result["attempt_created_at"],
                expected_request_id=result["provider_request_id"],
            )
            reconciled = await billing_repo.finalize_openrouter_recovery(
                user["organization_id"], attempt_uuid,
                actor_auth_user_id=actor, aal=aal, auth_time=auth_time,
                recovery_worker_id=result["recovery_worker_id"],
                lease_epoch=result["lease_epoch"], evidence=evidence,
            )
            if reconciled:
                return {"state": "verified", "provider": "openrouter"}
            raise HTTPException(
                status_code=409,
                detail="Provider evidence changed or could not safely reconcile; accounting remains blocked",
            )
        if result.get("state") in {"not_sent", "verified"}:
            return {"state": result["state"]}
        if result.get("state") == "worker_active":
            raise HTTPException(status_code=409, detail="Provider worker lease is still active")
        if result.get("state") == "lookup_required":
            raise HTTPException(status_code=409, detail="Provider evidence is not available")
        if result.get("state") in {"uncertain", "raced"}:
            raise HTTPException(
                status_code=409,
                detail="Provider outcome is still uncertain; no verified evidence can clear the accounting hold",
            )
        return {"state": result.get("state", "unavailable")}
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(status_code=403, detail="Recent administrator MFA is required") from None
    except ValueError:
        raise HTTPException(status_code=409, detail="Provider evidence does not match this attempt") from None
    except Exception:
        # Network/provider exceptions are intentionally not logged with their
        # response body; the tenant hold remains fail-closed.
        raise HTTPException(
            status_code=503,
            detail="Provider reconciliation is unavailable; accounting remains blocked",
        ) from None


async def _verify_provider_evidence(request: Request, req: ProviderEvidenceRequest, user: dict):
    from src.core.provider_evidence import EvidenceUnavailable, verify_provider_evidence

    store = getattr(request.app.state, "provider_evidence_store", None)
    try:
        return await verify_provider_evidence(
            store, uuid.UUID(str(user["organization_id"])), req.evidence_object_id,
            req.evidence_sha256, uuid.UUID(req.attempt_id),
        )
    except EvidenceUnavailable:
        raise HTTPException(
            status_code=503,
            detail=(
                "Immutable provider evidence or trusted account configuration is unavailable; "
                "accounting remains blocked"
            ),
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Immutable provider evidence could not be verified; accounting remains blocked",
        ) from None


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
    base = os.getenv("APP_BASE_URL", "https://app.melpis.it")
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


@router.post(
    "/provider-attempts/reconciliation/proposals",
    description=(
        "Propose tenant-scoped A03 recovery using an immutable evidence artifact. "
        "Proposal does not resolve the attempt; a different active owner/manager must approve with AAL2."
    ),
)
async def propose_provider_attempt_reconciliation(
    req: ProviderEvidenceRequest,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
    mfa_user: dict = Depends(require_mfa()),
):
    """Create a proposal only; provider-attempt holds remain in force."""
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    billing_repo = getattr(repo, "billing_repo", repo)
    try:
        evidence = await _verify_provider_evidence(request, req, user)
        version = await billing_repo.propose_provider_reconciliation(
            user["organization_id"], req.attempt_id,
            actor_auth_user_id=user.get("auth_user_id"), aal=mfa_user.get("aal"),
            evidence=evidence, attestations=req.attestations.model_dump(),
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Administrator MFA and tenant access are required") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Provider accounting recovery is unavailable") from None
    if version is None:
        raise HTTPException(status_code=409, detail="Attempt is unavailable, non-terminal, or already proposed")
    return {"proposed": True, "proposal_version": version, "independent_approval_required": True}


@router.post(
    "/provider-attempts/reconciliation/approvals",
    description=(
        "Approve one existing proposal using the same immutable artifact. "
        "The approver must be a different active owner/manager with AAL2."
    ),
)
async def approve_provider_attempt_reconciliation(
    req: ProviderEvidenceApprovalRequest,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
    mfa_user: dict = Depends(require_mfa()),
):
    """Approve terminal evidence atomically; never reconciles in_flight rows."""
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    billing_repo = getattr(repo, "billing_repo", repo)
    try:
        evidence = await _verify_provider_evidence(request, req, user)
        reconciled = await billing_repo.approve_provider_reconciliation(
            user["organization_id"], req.attempt_id,
            actor_auth_user_id=user.get("auth_user_id"), aal=mfa_user.get("aal"),
            proposal_version=req.proposal_version, evidence=evidence,
            attestations=req.attestations.model_dump(),
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Administrator MFA and tenant access are required") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Provider accounting recovery is unavailable") from None
    if not reconciled:
        raise HTTPException(status_code=409, detail="Proposal is stale, non-terminal, mismatched, or already approved")
    return {"reconciled": True, "proposal_version": req.proposal_version}


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
