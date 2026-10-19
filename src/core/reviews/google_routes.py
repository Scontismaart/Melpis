import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from google_auth_oauthlib.flow import Flow

from src.api.routes.common import check_feature_blocked_by_plan
from src.core.auth.dependencies import require_ruolo, require_mfa
from src.core.auth.oauth_callback import safe_oauth_callback
from src.core.auth.oauth_state import (
    create_bound_oauth_nonce, exchange_google_oauth_token, is_bound_oauth_nonce,
    oauth_pkce_verifier, oauth_start_owner, validate_oauth_callback_context,
)
from src.core.google_feature_flags import google_business_enabled
from src.core.reviews.google_service import GoogleBusinessService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reviews/google", tags=["reviews-google"])

SCOPES = ["https://www.googleapis.com/auth/business.manage"]
NONCE_TTL_MINUTES = 10
FRONTEND_REDIRECT = "/app/"


def _get_client_config():
    return {
        "web": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def _make_flow():
    if not google_business_enabled():
        raise RuntimeError("Google Business disabled")
    redirect_uri = os.environ["GOOGLE_REVIEWS_REDIRECT_URI"]
    return Flow.from_client_config(
        _get_client_config(),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )


def _get_service(request: Request) -> GoogleBusinessService:
    _require_business_enabled()
    svc = getattr(request.app.state, "reviews_service", None)
    if svc is None:
        svc = GoogleBusinessService(
            repo=request.app.state.repo,
            encryption_key=os.getenv("ENCRYPTION_KEY", ""),
        )
        request.app.state.reviews_service = svc
    return svc


class GoogleReviewsSettingsInput(BaseModel):
    account_name: str | None = None
    location_name: str | None = None


def _require_business_enabled() -> None:
    if not google_business_enabled():
        raise HTTPException(status_code=503, detail="Google Business disabled")


@router.get("/auth")
async def google_reviews_auth(
    request: Request,
    user: dict = Depends(oauth_start_owner),
    mfa: dict = Depends(require_mfa()),
):
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(400, "X-Organization-Id header required")
    _require_business_enabled()

    repo = getattr(request.app.state, "repo", None)
    blocco = await check_feature_blocked_by_plan(repo, org_id, "recensioni")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)

    nonce = create_bound_oauth_nonce("reviews_google", org_id, user)
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO oauth_nonces (nonce, organization_id, created_at) VALUES ($1, $2, NOW())",
            nonce, org_id,
        )

    flow = _make_flow()
    flow.code_verifier = oauth_pkce_verifier("reviews_google", org_id, nonce)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=f"{org_id}:{nonce}",
    )
    return RedirectResponse(url=auth_url)


@router.get("/oauth2callback")
@safe_oauth_callback("reviews_google")
async def google_reviews_oauth2callback(request: Request):
    if not google_business_enabled():
        return RedirectResponse(
            url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=disabled"
        )
    state = request.query_params.get("state", "")
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if not state or ":" not in state:
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=invalid_state")

    org_id, nonce = state.split(":", 1)
    try:
        uuid.UUID(org_id)
    except ValueError:
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=invalid_state")
    if not is_bound_oauth_nonce(nonce):
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=invalid_state")
    if not await validate_oauth_callback_context(request, "reviews_google", org_id, nonce):
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=invalid_context")
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM oauth_nonces WHERE nonce = $1 AND organization_id = $2 RETURNING *",
            nonce, org_id,
        )
        if not row:
            logger.warning("business=nonce_not_found org_id=%s", org_id)
            return RedirectResponse(url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=invalid_nonce")

        created_at = row["created_at"]
        if created_at and datetime.now(timezone.utc) - created_at > timedelta(minutes=NONCE_TTL_MINUTES):
            logger.warning("business=nonce_expired org_id=%s", org_id)
            return RedirectResponse(url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=nonce_expired")

    # A provider denial still consumes state: callback nonces are one-shot.
    if error:
        logger.warning("business=oauth_error org_id=%s", org_id)
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=provider_denied")

    if not code:
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=missing_code")

    flow = _make_flow()
    flow.code_verifier = oauth_pkce_verifier("reviews_google", org_id, nonce)
    await exchange_google_oauth_token(flow, code, SCOPES)
    creds = flow.credentials

    if not creds.refresh_token:
        logger.error("business=no_refresh_token org_id=%s", org_id)
        return RedirectResponse(
            url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=no_refresh_token"
        )

    # Recheck live session/owner authorization after the provider exchange.
    if not await validate_oauth_callback_context(request, "reviews_google", org_id, nonce):
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?reviews_google=error&reason=invalid_context")

    service = _get_service(request)
    enc_access = service.encrypt_secret(creds.token)
    enc_refresh = service.encrypt_secret(creds.refresh_token)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO google_business_credentials
               (organization_id, access_token, refresh_token, token_expiry, updated_at)
               VALUES ($1, $2, $3, $4, NOW())
               ON CONFLICT (organization_id) DO UPDATE SET
                   access_token = EXCLUDED.access_token,
                   refresh_token = EXCLUDED.refresh_token,
                   token_expiry = EXCLUDED.token_expiry,
                   updated_at = NOW()""",
            org_id,
            enc_access,
            enc_refresh,
            creds.expiry,
        )

    logger.info("business=connected org_id=%s", org_id)
    return RedirectResponse(url=f"{FRONTEND_REDIRECT}?reviews_google=connected")


@router.get("/status")
async def google_reviews_status(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(400, "X-Organization-Id header required")
    _require_business_enabled()
    repo = getattr(request.app.state, "repo", None)
    blocco = await check_feature_blocked_by_plan(repo, org_id, "recensioni")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM google_business_credentials WHERE organization_id = $1",
            org_id,
        )
    if not row:
        return {"connected": False}
    return {
        "connected": True,
        # OAuth credentials alone do not prove Account/Location/Reviews access.
        "operational": bool(row["account_name"] and row["location_name"] and row["last_sync_at"]),
        "account_name": row["account_name"],
        "location_name": row["location_name"],
        "last_sync_at": row["last_sync_at"],
    }


@router.post("/sync")
async def google_reviews_sync(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(400, "X-Organization-Id header required")
    _require_business_enabled()
    repo = getattr(request.app.state, "repo", None)
    blocco = await check_feature_blocked_by_plan(repo, org_id, "recensioni")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    service = _get_service(request)
    try:
        result = await service.fetch_reviews(org_id, actor_auth_user_id=user.get("auth_user_id"))
    except Exception:
        trace_id = getattr(request.state, "trace_id", "unavailable")
        logger.error("business=sync_fail org_id=%s trace_id=%s", org_id, trace_id)
        raise HTTPException(502, detail="Sincronizzazione recensioni non completata.")
    if result.get("parziale"):
        return {**result, "detail": "Sincronizzazione parziale; alcune recensioni non sono state elaborate."}
    return result


@router.patch("/settings")
async def google_reviews_settings(
    body: GoogleReviewsSettingsInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    mfa: dict = Depends(require_mfa()),
):
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(400, "X-Organization-Id header required")
    _require_business_enabled()
    repo = getattr(request.app.state, "repo", None)
    blocco = await check_feature_blocked_by_plan(repo, org_id, "recensioni")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    pool = request.app.state.pool
    if body.account_name is None and body.location_name is None:
        raise HTTPException(400, "Nessun campo da aggiornare")
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE google_business_credentials
               SET account_name = COALESCE($2, account_name),
                   location_name = COALESCE($3, location_name),
                   review_page_token = NULL,
                   review_page_account_name = NULL,
                   review_page_location_name = NULL,
                   updated_at = NOW()
               WHERE organization_id = $1""",
            org_id, body.account_name, body.location_name,
        )
    return {"detail": "Impostazioni Google Business aggiornate"}


@router.delete("/disconnect")
async def google_reviews_disconnect(
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    mfa: dict = Depends(require_mfa()),
):
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(400, "X-Organization-Id header required")
    repo = getattr(request.app.state, "repo", None)
    blocco = await check_feature_blocked_by_plan(repo, org_id, "recensioni")
    if blocco:
        raise HTTPException(status_code=403, detail=blocco)
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM google_business_credentials WHERE organization_id = $1",
            org_id,
        )
    logger.info("business=disconnected org_id=%s", org_id)
    return {"detail": "Google Business disconnesso"}
