import os
import uuid
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from google_auth_oauthlib.flow import Flow

from src.core.auth.dependencies import require_ruolo, require_mfa
from src.core.auth.oauth_callback import safe_oauth_callback
from src.core.auth.oauth_state import (
    create_bound_oauth_nonce, exchange_google_oauth_token, is_bound_oauth_nonce,
    oauth_pkce_verifier, oauth_start_owner, validate_oauth_callback_context,
)
from src.core.google_feature_flags import google_calendar_enabled
from src.core.calendar.redirect_uri import (
    CALENDAR_OAUTH_CALLBACK_PATH,
    is_valid_calendar_redirect_uri,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
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
    if not google_calendar_enabled():
        raise RuntimeError("Google Calendar disabled")
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "")
    non_production_environments = {"development", "local", "test", "staging"}
    configured_environments = [
        os.environ[name].strip().lower()
        for name in ("APP_ENV", "ENVIRONMENT")
        if name in os.environ
    ]
    allow_loopback_http = bool(configured_environments) and all(
        environment in non_production_environments
        for environment in configured_environments
    )
    if not is_valid_calendar_redirect_uri(
        redirect_uri,
        allow_loopback_http=allow_loopback_http,
    ):
        raise ValueError(
            "GOOGLE_REDIRECT_URI non valido: atteso percorso "
            f"'{CALENDAR_OAUTH_CALLBACK_PATH}', HTTPS su host pubblici oppure "
            "HTTP esclusivamente su localhost:4187 fuori da Production"
        )
    return Flow.from_client_config(
        _get_client_config(),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )


class CalendarSettingsInput(BaseModel):
    sync_enabled: bool | None = None
    calendar_id: str | None = None


def _require_calendar_enabled() -> None:
    if not google_calendar_enabled():
        raise HTTPException(status_code=503, detail="Google Calendar disabled")


@router.get("/auth")
async def calendar_auth(
    request: Request,
    user: dict = Depends(oauth_start_owner),
    mfa: dict = Depends(require_mfa()),
):
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(400, "X-Organization-Id header required")
    _require_calendar_enabled()

    nonce = create_bound_oauth_nonce("calendar", org_id, user)
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO oauth_nonces (nonce, organization_id, created_at) VALUES ($1, $2, NOW())",
            nonce, org_id,
        )

    flow = _make_flow()
    flow.code_verifier = oauth_pkce_verifier("calendar", org_id, nonce)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=f"{org_id}:{nonce}",
    )
    return RedirectResponse(url=auth_url)


@router.get("/oauth2callback")
@safe_oauth_callback("calendar")
async def calendar_oauth2callback(request: Request):
    if not google_calendar_enabled():
        return RedirectResponse(
            url=f"{FRONTEND_REDIRECT}?calendar=error&reason=disabled"
        )
    state = request.query_params.get("state", "")
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if not state or ":" not in state:
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=error&reason=invalid_state")

    org_id, nonce = state.split(":", 1)
    try:
        uuid.UUID(org_id)
    except ValueError:
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=error&reason=invalid_state")
    if not is_bound_oauth_nonce(nonce):
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=error&reason=invalid_state")
    if not await validate_oauth_callback_context(request, "calendar", org_id, nonce):
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=error&reason=invalid_context")
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM oauth_nonces WHERE nonce = $1 AND organization_id = $2 RETURNING *",
            nonce, org_id,
        )
        if not row:
            logger.warning("calendar=nonce_not_found org_id=%s", org_id)
            return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=error&reason=invalid_nonce")

        created_at = row["created_at"]
        if created_at and datetime.now(timezone.utc) - created_at > timedelta(minutes=NONCE_TTL_MINUTES):
            logger.warning("calendar=nonce_expired org_id=%s", org_id)
            return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=error&reason=nonce_expired")

    # A valid callback consumes state even when the provider denied access.
    if error:
        logger.warning("calendar=oauth_error org_id=%s", org_id)
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=error&reason=provider_denied")

    if not code:
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=error&reason=missing_code")

    flow = _make_flow()
    flow.code_verifier = oauth_pkce_verifier("calendar", org_id, nonce)
    # fetch_token e' una chiamata di rete sincrona bloccante: offload su
    # thread per non freeze-are l'event loop FastAPI.
    await exchange_google_oauth_token(flow, code, SCOPES)
    creds = flow.credentials

    # Google non rilascia refresh_token se la app e' in stato "Testing"
    # in Google Cloud Console e l'utente non e' un test user (o se non si
    # usa prompt=consent — che qui forziamo in /auth). Senza refresh_token
    # il sync e' inservibile: rifiuta subito invece di salvare credenziali
    # mute che crasheranno al primo refresh.
    if not creds.refresh_token:
        logger.error("calendar=no_refresh_token org_id=%s", org_id)
        return RedirectResponse(
            url=f"{FRONTEND_REDIRECT}?calendar=error&reason=no_refresh_token"
        )

    # Authorization may be revoked while Google's network exchange is running.
    if not await validate_oauth_callback_context(request, "calendar", org_id, nonce):
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=error&reason=invalid_context")

    # Cifra i token con la stessa Fernet del service. Il DB NON salva mai
    # token in chiaro: _get_credentials assume formato Fernet, quindi ogni
    # scrittura DEVE passare da qui.
    calendar_service = getattr(request.app.state, "calendar_service", None)
    if calendar_service is None:
        logger.error("calendar=service_unavailable org_id=%s", org_id)
        return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=error&reason=server_error")

    enc_access = calendar_service.encrypt_secret(creds.token)
    enc_refresh = calendar_service.encrypt_secret(creds.refresh_token)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO google_calendar_credentials
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

    logger.info("calendar=connected org_id=%s", org_id)
    return RedirectResponse(url=f"{FRONTEND_REDIRECT}?calendar=connected")


@router.get("/status")
async def calendar_status(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(400, "X-Organization-Id header required")
    _require_calendar_enabled()
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM google_calendar_credentials WHERE organization_id = $1",
            org_id,
        )
    if not row:
        return {"connected": False}
    return {
        "connected": True,
        "calendar_id": row["calendar_id"],
        "sync_enabled": row["sync_enabled"],
        "last_sync_at": row["last_sync_at"],
    }


@router.delete("/disconnect")
async def calendar_disconnect(
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    mfa: dict = Depends(require_mfa()),
):
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(400, "X-Organization-Id header required")
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM google_calendar_credentials WHERE organization_id = $1",
            org_id,
        )
        await conn.execute(
            "UPDATE bookings SET google_event_id = NULL WHERE organization_id = $1",
            org_id,
        )
    logger.info("calendar=disconnected org_id=%s", org_id)
    return {"detail": "Google Calendar disconnesso"}


@router.patch("/settings")
async def calendar_settings(
    body: CalendarSettingsInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
    mfa: dict = Depends(require_mfa()),
):
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(400, "X-Organization-Id header required")
    _require_calendar_enabled()
    pool = request.app.state.pool
    if body.sync_enabled is None and body.calendar_id is None:
        raise HTTPException(400, "Nessun campo da aggiornare")
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE google_calendar_credentials
               SET sync_enabled = COALESCE($2, sync_enabled),
                   calendar_id = COALESCE($3, calendar_id),
                   updated_at = NOW()
               WHERE organization_id = $1""",
            org_id, body.sync_enabled, body.calendar_id,
        )
    return {"detail": "Impostazioni calendario aggiornate"}
