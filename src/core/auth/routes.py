"""Endpoint BFF di autenticazione (/api/auth/*).

Il frontend si autentica qui: il backend scambia le credenziali con Supabase
Auth e restituisce la sessione in cookie HttpOnly+Secure+SameSite=Lax.
Nessun token transita dal client (niente localStorage, niente header Bearer).
"""

import hashlib
import math
import os
import re
import secrets
import time
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from src.core.auth import bff, throttle
from src.core.auth.audit import audit_log
from src.core.auth.csrf import clear_csrf_token, issue_csrf_token
from src.core.auth.denylist import is_token_revoked, revoke_token
from src.core.auth.dependencies import (
    get_current_user,
    get_repo,
    require_ruolo,
)
from src.core.gdpr import token_store

router = APIRouter(prefix="/api/auth", tags=["auth"])


# Durata trial alla creazione org (registrazione normale e primo accesso
# Google usano la stessa variabile d'ambiente).
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "7"))

# Anti brute-force su /api/auth/login: fallimenti consecutivi per IP.
# Contatori distribuiti (Redis quando RATE_LIMIT_BACKEND=redis): validi con
# più worker/repliche e sopravvivono ai restart. Finestra scorrevole.
_LOGIN_MAX_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_LOCKOUT_SECONDS = _LOGIN_WINDOW_SECONDS  # la finestra stessa è il lockout

# ── Google OAuth (PKCE server-side) ────────────────────────────────────
# Il verifier viaggia SOLO in cookie HttpOnly: il browser non vede mai il
# verifier (i token restano fuori da JS, come per il login BFF). Lo `state`
# OAuth è generato e validato internamente da Supabase Auth: passarne uno
# custom rompe il flusso (bad_oauth_state). Il callback applicativo è legato
# al verifier PKCE custodito nel nostro cookie HttpOnly.
# SameSite=Lax è richiesto: il callback arriva da un redirect top-level di
# Google, che i cookie Strict non includerebbero.
_OAUTH_VERIFIER_COOKIE = "wa_oauth_verifier"
_OAUTH_NEXT_COOKIE = "wa_oauth_next"
_OAUTH_STATE_MAX_AGE = 600  # 10 minuti per completare il round-trip
_MFA_RECENT_AUTH_SECONDS = 300
_MFA_PRIMARY_AUTH_METHODS = {
    "password",
    "oauth",
    "otp",
    "magiclink",
    "sso/saml",
    "web3",
    "recovery",
    "invite",
    "email/signup",
}


def _oauth_cookie_name(base: str) -> str:
    return f"__Host-{base}" if bff.cookie_secure() else base


def _delete_oauth_cookies(response: Response) -> None:
    for name in (
        _oauth_cookie_name(_OAUTH_VERIFIER_COOKIE),
        _oauth_cookie_name(_OAUTH_NEXT_COOKIE),
    ):
        response.delete_cookie(
            name,
            path="/",
            secure=bff.cookie_secure(),
            httponly=True,
            samesite="lax",
        )


def _google_error_redirect() -> RedirectResponse:
    response = RedirectResponse("/accedi/?errore=google", status_code=302)
    _delete_oauth_cookies(response)
    return response


def _safe_next(next_path: str | None) -> str:
    """Accetta solo path relativi interni (niente //host, backslash, scheme)."""
    if not next_path:
        return "/app/"
    # I valori cookie con caratteri speciali tornano quotati dal browser.
    path = next_path.strip('"')
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/app/"
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return "/app/"
    try:
        parsed = urlsplit(path)
    except ValueError:
        return "/app/"
    if parsed.scheme or parsed.netloc:
        return "/app/"
    if re.search(r"%(?:25)*(?:0[0-9a-f]|1[0-9a-f]|7f|2f|5c)", parsed.path, re.IGNORECASE):
        return "/app/"
    return path


class LoginRequest(BaseModel):
    email: str
    password: str


def _client_ip(request: Request) -> str:
    from src.core.auth.trusted_network import get_client_ip
    return str(get_client_ip(request) or "unknown")


def _login_throttle_key(ip: str) -> str:
    return f"auth:login-fail:{ip}"


async def _check_login_throttle(ip: str) -> None:
    if await throttle.is_throttled(
        _login_throttle_key(ip), _LOGIN_MAX_FAILURES, _LOGIN_WINDOW_SECONDS
    ):
        raise HTTPException(
            status_code=429,
            detail="Troppi tentativi di accesso. Riprova tra 15 minuti.",
        )


async def _record_login_failure(ip: str) -> None:
    await throttle.record_event(_login_throttle_key(ip), _LOGIN_LOCKOUT_SECONDS)


async def _record_login_success(ip: str) -> None:
    await throttle.clear_events(_login_throttle_key(ip))


def _set_session_cookies(response: Response, data: dict) -> None:
    common = {
        "httponly": True,
        "secure": bff.cookie_secure(),
        "samesite": "lax",
        "path": "/",
    }
    response.set_cookie(bff.access_cookie_name(), data["access_token"], **common)
    response.set_cookie(bff.refresh_cookie_name(), data["refresh_token"], **common)


def _clear_session_cookies(response: Response) -> None:
    for name in (bff.access_cookie_name(), bff.refresh_cookie_name()):
        response.delete_cookie(
            name,
            path="/",
            secure=bff.cookie_secure(),
            httponly=True,
            samesite="lax",
        )
    clear_csrf_token(response)


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    ip = _client_ip(request)
    await _check_login_throttle(ip)
    try:
        data = await bff.login(body.email.strip(), body.password)
    except HTTPException:
        await _record_login_failure(ip)
        raise
    user = data.get("user") if isinstance(data, dict) else None
    subject = user.get("id") if isinstance(user, dict) else None
    if not subject or await get_repo(request).get_auth_access_allowed(subject) is not True:
        raise HTTPException(403, "Accesso account non consentito")
    await _record_login_success(ip)
    _set_session_cookies(response, data)
    csrf_token = issue_csrf_token(response)
    return {"ok": True, "email": body.email.strip(), "csrf_token": csrf_token}


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    rt = request.cookies.get(bff.refresh_cookie_name())
    at = request.cookies.get(bff.access_cookie_name())
    if not rt or not at or await is_token_revoked(rt) or await is_token_revoked(at):
        raise HTTPException(status_code=401, detail="Sessione scaduta")

    # Verifica la firma del vecchio access token anche se è scaduto: il refresh
    # deve ruotare la stessa identità senza aprire una nuova sessione implicita.
    from src.core.auth.dependencies import verify_supabase_jwt

    try:
        previous_claims = await verify_supabase_jwt(at, allow_expired=True)
    except HTTPException as exc:
        raise HTTPException(status_code=401, detail="Sessione scaduta") from exc
    previous_user_id = previous_claims.get("sub")
    if not previous_user_id:
        raise HTTPException(status_code=401, detail="Sessione scaduta")
    if await get_repo(request).get_auth_access_allowed(previous_user_id) is not True:
        raise HTTPException(403, "Accesso account non consentito")

    # user_key anonimo: digest del token, mai il token grezzo in memoria
    user_key = hashlib.sha256(rt.encode()).hexdigest()
    data = await bff.refresh(rt, user_key)
    await revoke_token(rt)
    await revoke_token(at)

    refreshed_user = data.get("user") if isinstance(data, dict) else None
    new_access = data.get("access_token") if isinstance(data, dict) else None
    new_refresh = data.get("refresh_token") if isinstance(data, dict) else None
    if (
        not isinstance(refreshed_user, dict)
        or str(refreshed_user.get("id") or "") != str(previous_user_id)
        or not new_access
        or not new_refresh
    ):
        # La rotazione remota è già avvenuta: non consegnare token incoerenti.
        # Anche il vecchio access token viene invalidato: la sessione rifiutata
        # non deve rimanere utilizzabile fino alla sua scadenza JWT.
        if new_access:
            await revoke_token(str(new_access))
            await bff.logout(str(new_access), scope="local")
        if new_refresh:
            await revoke_token(str(new_refresh))
        raise HTTPException(status_code=401, detail="Sessione non valida")

    _set_session_cookies(response, data)
    csrf_token = issue_csrf_token(response)
    return {"ok": True, "csrf_token": csrf_token}


def _nome_attivita_da_utente(user: dict) -> str:
    """Nome organizzazione per il provisioning JIT: full name Google,
    altrimenti prefisso email. Trim + limite 120 come la validazione
    della registrazione normale."""
    meta = user.get("user_metadata") if isinstance(user, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    email = user.get("email") or ""
    nome = (
        (meta.get("full_name") or "").strip()
        or (meta.get("name") or "").strip()
        or email.split("@")[0].strip()
        or "La mia attività"
    )
    return nome[:120] or "La mia attività"


@router.get("/google/start")
async def google_start(next: str | None = None):
    """Avvio login Google: genera il PKCE verifier, lo mette in cookie
    HttpOnly di breve durata e reindirizza al authorize endpoint Supabase.
    Lo `state` OAuth non va passato: lo genera Supabase internamente."""
    next_path = _safe_next(next)
    code_verifier = secrets.token_urlsafe(48)
    redirect_to = f"{bff.public_app_url()}/api/auth/google/callback"
    authorize_url = bff.google_authorize_url(
        redirect_to, bff.pkce_challenge(code_verifier)
    )

    # I cookie vanno messi sulla redirect stessa: FastAPI non unisce gli
    # header del parametro `response` quando si torna una Response diretta.
    redirect = RedirectResponse(authorize_url, status_code=302)
    common = {
        "httponly": True,
        "secure": bff.cookie_secure(),
        "samesite": "lax",
        "path": "/",
        "max_age": _OAUTH_STATE_MAX_AGE,
    }
    redirect.set_cookie(_oauth_cookie_name(_OAUTH_VERIFIER_COOKIE), code_verifier, **common)
    # Il path di destinazione post-login viaggia nel cookie (non nell'URL di
    # authorize: redirect_to deve restare identico agli URL consentiti Supabase).
    redirect.set_cookie(_oauth_cookie_name(_OAUTH_NEXT_COOKIE), next_path, **common)
    return redirect


@router.get("/google/callback")
async def google_callback(request: Request):
    """Callback Supabase PKCE: scambia il code monouso col verifier HttpOnly.

    Supabase valida il provider state prima di emettere il code; il callback
    applicativo può ricevere il solo code. Anomalie terminano a
    /accedi/?errore=google senza sessione (fail-closed).
    """
    # Google/Supabase possono rimandare un errore OAuth (access denied ecc.)
    if request.query_params.get("error") or request.query_params.get("error_code"):
        return _google_error_redirect()

    code_values = request.query_params.getlist("code")
    state_values = request.query_params.getlist("state")
    # Supabase valida il proprio state prima di emettere il code; il callback
    # PKCE può quindi essere code-only. Non accettiamo parametri ambigui o
    # valori malformati e non aggiungiamo uno state custom al redirect OAuth.
    if len(code_values) != 1 or len(state_values) > 1:
        return _google_error_redirect()
    if state_values and (
        not state_values[0]
        or len(state_values[0]) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in state_values[0])
    ):
        return _google_error_redirect()

    auth_code = code_values[0]
    cookie_verifier = request.cookies.get(_oauth_cookie_name(_OAUTH_VERIFIER_COOKIE))
    cookie_next = _safe_next(request.cookies.get(_oauth_cookie_name(_OAUTH_NEXT_COOKIE)))
    if not auth_code or not cookie_verifier:
        return _google_error_redirect()

    try:
        data = await bff.exchange_pkce(auth_code, cookie_verifier)

        # Primo accesso Google: Supabase ha creato l'utente (e il trigger DB
        # il profilo), ma organizzazione + membership esistono solo se l'utente
        # e' passato dalla registrazione email/password. Senza provisioning,
        # /me risponderebbe 403 e il frontend rispedirebbe al login: loop.
        # Idempotente: se ha gia' una org non fa nulla.
        user = data.get("user") if isinstance(data.get("user"), dict) else {}
        auth_user_id = user.get("id")
        if not auth_user_id:
            return _google_error_redirect()
        # The repository checks durable access even when memberships exist.
        await get_repo(request).get_or_create_organization_with_owner(
            str(auth_user_id), _nome_attivita_da_utente(user), TRIAL_DAYS,
        )
    except HTTPException:
        return _google_error_redirect()
    except RuntimeError:
        return _google_error_redirect()
    except Exception:
        return _google_error_redirect()

    target_url = cookie_next
    redirect = RedirectResponse(target_url, status_code=302)
    _set_session_cookies(redirect, data)
    issue_csrf_token(redirect)

    # I cookie temporanei OAuth vanno consumati: non riutilizzabili.
    _delete_oauth_cookies(redirect)

    return redirect


@router.post("/logout")
async def logout(request: Request, response: Response):
    at = request.cookies.get(bff.access_cookie_name())
    rt = request.cookies.get(bff.refresh_cookie_name())
    if at:
        await revoke_token(at)
        await bff.logout(at, scope="local")
    if rt:
        await revoke_token(rt)
    _clear_session_cookies(response)
    response.delete_cookie(
        token_store.DOWNLOAD_COOKIE,
        path="/api/gdpr/download",
        secure=bff.cookie_secure(),
        httponly=True,
        samesite="strict",
    )
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(require_ruolo("owner", "manager", "staff"))):
    return {
        "email": user.get("email"),
        "organization_id": user.get("organization_id"),
        "ruolo": user.get("ruolo"),
        "user_id": user.get("user_id"),
        "source": user.get("source"),
    }


# ── Sicurezza account: cambio password/email (proxy Supabase) ──────────
# La sessione valida nel cookie HttpOnly è la prova d'identità: Supabase
# non richiede la password corrente per il cambio. Rate limit per IP anti
# abuso; policy password identica alla registrazione (register.py).

_ACCOUNT_CHANGE_MAX = 5
_ACCOUNT_CHANGE_WINDOW = 60 * 60
_PASSWORD_MIN = 10
_SPECIAL_RE = re.compile(r"[^A-Za-z0-9]")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PasswordChange(BaseModel):
    password: str
    current_password: str


class EmailChange(BaseModel):
    email: str


class MfaFactorRequest(BaseModel):
    factor_id: UUID


class MfaVerifyRequest(MfaFactorRequest):
    challenge_id: UUID
    code: str = Field(pattern=r"^\d{6}$")


async def _mfa_session(request: Request) -> tuple[str, dict]:
    """MFA management accepts only this browser's HttpOnly BFF session."""
    if request.headers.get("authorization") or request.headers.get("x-api-key"):
        raise HTTPException(400, "Usa la sessione browser per gestire MFA")
    token = request.cookies.get(bff.access_cookie_name())
    if not token:
        raise HTTPException(401, "Sessione scaduta: effettua di nuovo il login")
    if await is_token_revoked(token):
        raise HTTPException(401, "Sessione revocata: effettua di nuovo il login")

    from src.core.auth.dependencies import verify_supabase_jwt

    claims = await verify_supabase_jwt(token)
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise HTTPException(401, "Sessione non valida")
    if await get_repo(request).get_auth_access_allowed(claims["sub"]) is not True:
        raise HTTPException(403, "Accesso account non consentito")
    return token, claims


async def _mfa_auth_request(
    method: str,
    path: str,
    token: str,
    payload: dict | None = None,
) -> dict:
    """Call only fixed Supabase Auth MFA paths; never expose upstream details."""
    import httpx

    client = await bff._client()
    try:
        resp = await client.request(
            method,
            f"{bff._supabase_url()}/auth/v1/{path.lstrip('/')}",
            json=payload,
            headers={
                "apikey": bff._anon_key(),
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
    except httpx.HTTPError:
        raise HTTPException(502, "Servizio autenticazione non raggiungibile")

    if resp.status_code == 401:
        raise HTTPException(401, "Sessione scaduta: effettua di nuovo il login")
    if resp.status_code == 403:
        raise HTTPException(403, "Operazione MFA non autorizzata")
    if resp.status_code == 404:
        raise HTTPException(404, "Fattore MFA non trovato")
    if resp.status_code == 422:
        if path.endswith("/verify"):
            raise HTTPException(422, "Codice MFA non valido o scaduto")
        raise HTTPException(422, "Impossibile completare la configurazione MFA")
    if resp.status_code == 429:
        raise HTTPException(429, "Troppe richieste MFA. Riprova tra poco")
    if resp.status_code >= 400:
        raise HTTPException(502, "Operazione MFA non riuscita")
    if not resp.content:
        return {}
    try:
        value = resp.json()
    except ValueError:
        raise HTTPException(502, "Risposta del servizio autenticazione non valida")
    if not isinstance(value, dict):
        raise HTTPException(502, "Risposta del servizio autenticazione non valida")
    return value


async def _mfa_user(token: str, expected_user_id: str) -> dict:
    user = await _mfa_auth_request("GET", "user", token)
    if str(user.get("id") or "") != expected_user_id:
        raise HTTPException(401, "Sessione non valida")
    return user


def _totp_factors(user: dict) -> list[dict]:
    factors = user.get("factors")
    if not isinstance(factors, list):
        return []
    return [
        factor for factor in factors
        if isinstance(factor, dict) and factor.get("factor_type") == "totp"
    ]


def _public_totp_factors(factors: list[dict]) -> list[dict]:
    return [
        {
            "id": factor.get("id"),
            "friendly_name": factor.get("friendly_name") or "Authenticator app",
            "status": factor.get("status"),
        }
        for factor in factors
        if factor.get("status") in {"verified", "unverified"}
        and isinstance(factor.get("id"), str)
    ]


def _owned_totp_factor(factors: list[dict], factor_id: str) -> dict:
    factor = next((f for f in factors if str(f.get("id")) == factor_id), None)
    if not factor or factor.get("status") not in {"verified", "unverified"}:
        raise HTTPException(404, "Fattore MFA non trovato")
    return factor


def _require_recent_auth(claims: dict) -> None:
    amr = claims.get("amr")
    if not isinstance(amr, list):
        raise HTTPException(428, "Per configurare MFA, esci e accedi di nuovo")

    def primary_timestamp(entry: object) -> int | float | None:
        if not isinstance(entry, dict):
            return None
        method = entry.get("method")
        timestamp = entry.get("timestamp")
        if (
            not isinstance(method, str)
            or method not in _MFA_PRIMARY_AUTH_METHODS
            or not isinstance(timestamp, (int, float))
            or isinstance(timestamp, bool)
        ):
            return None
        try:
            return timestamp if math.isfinite(timestamp) else None
        except OverflowError:
            return None

    timestamps = [timestamp for entry in amr if (timestamp := primary_timestamp(entry)) is not None]
    if not timestamps:
        raise HTTPException(428, "Per configurare MFA, esci e accedi di nuovo")
    age = time.time() - max(timestamps)
    if age < -60 or age > _MFA_RECENT_AUTH_SECONDS:
        raise HTTPException(428, "Per configurare MFA, esci e accedi di nuovo")


@router.get("/mfa")
async def mfa_status(request: Request):
    token, claims = await _mfa_session(request)
    user = await _mfa_user(token, claims["sub"])
    factors = _public_totp_factors(_totp_factors(user))
    return {"aal": claims.get("aal") or "aal1", "factors": factors}


@router.post("/mfa/enroll")
async def mfa_enroll(request: Request):
    token, claims = await _mfa_session(request)
    _require_recent_auth(claims)
    user = await _mfa_user(token, claims["sub"])
    factors = _totp_factors(user)
    if any(f.get("status") == "verified" for f in factors):
        raise HTTPException(409, "Un fattore MFA TOTP è già attivo")
    if any(f.get("status") == "unverified" for f in factors):
        raise HTTPException(409, "Annulla la configurazione MFA in sospeso prima di riprovare")

    result = await _mfa_auth_request(
        "POST",
        "factors",
        token,
        {"factor_type": "totp", "friendly_name": "Melpis authenticator", "issuer": "Melpis"},
    )
    totp = result.get("totp") if isinstance(result.get("totp"), dict) else {}
    qr_code = totp.get("qr_code")
    secret = totp.get("secret")
    factor_id = result.get("id")
    svg_qr_code = None
    if isinstance(qr_code, str):
        import base64
        import xml.etree.ElementTree as ET
        from urllib.parse import unquote_to_bytes

        svg_source = qr_code
        if qr_code.startswith("data:image/svg+xml;utf-8,"):
            try:
                svg_source = unquote_to_bytes(qr_code.removeprefix("data:image/svg+xml;utf-8,")).decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                svg_source = ""
        if (
            # GoTrue emits a rect-based QR (~320 KB), not a compact path SVG.
            0 < len(svg_source) <= 524_288
            and "<!DOCTYPE" not in svg_source.upper()
            and "<!ENTITY" not in svg_source.upper()
        ):
            try:
                root = ET.fromstring(svg_source)
            except ET.ParseError:
                root = None
            if root is not None:
                safe_tags = {"svg", "path", "rect"}
                safe_attributes = {
                    "svg": {"xmlns", "width", "height", "viewBox"},
                    "path": {"d", "fill", "fill-rule", "clip-rule"},
                    "rect": {"x", "y", "width", "height", "fill"},
                }
                nodes = list(root.iter())
                # Convert only GoTrue's two literal styles. Arbitrary CSS,
                # URLs and other attributes remain forbidden by the allowlist.
                for node in nodes:
                    style = node.attrib.get("style")
                    if node.tag == "{http://www.w3.org/2000/svg}rect" and style in {
                        "fill:black;stroke:none", "fill:white;stroke:none",
                    }:
                        node.attrib.pop("style")
                        node.set("fill", "black" if style.startswith("fill:black;") else "white")
                def safe_tag_name(node):
                    tag = node.tag
                    if not isinstance(tag, str):
                        return None
                    if tag.startswith("{http://www.w3.org/2000/svg}"):
                        return tag.rsplit("}", 1)[-1]
                    return tag if "}" not in tag else None

                is_safe_svg = (
                    safe_tag_name(root) == "svg"
                    and len(nodes) <= 10_000
                    and all(
                        safe_tag_name(node) in safe_tags
                        and set(node.attrib).issubset(safe_attributes[safe_tag_name(node)])
                        and not (node.text or "").strip()
                        and not (node.tail or "").strip()
                        for node in nodes
                    )
                )
                if is_safe_svg:
                    sanitized_root = ET.Element("svg", attrib={
                        key: value for key, value in root.attrib.items()
                        if key in safe_attributes["svg"] and key != "xmlns"
                    })
                    sanitized_root.set("xmlns", "http://www.w3.org/2000/svg")
                    for node in list(root):
                        sanitized_node = ET.SubElement(sanitized_root, safe_tag_name(node), attrib=node.attrib)
                        for child in list(node):
                            ET.SubElement(sanitized_node, safe_tag_name(child), attrib=child.attrib)
                    safe_svg = ET.tostring(sanitized_root, encoding="utf-8", xml_declaration=False)
                    encoded_svg = base64.b64encode(safe_svg).decode("ascii")
                    svg_qr_code = f"data:image/svg+xml;base64,{encoded_svg}"
    if (
        result.get("type") != "totp"
        or not isinstance(factor_id, str)
        or not re.fullmatch(r"[0-9a-fA-F-]{36}", factor_id)
        or not svg_qr_code
        or not isinstance(secret, str)
        or not secret
    ):
        raise HTTPException(502, "Impossibile avviare la configurazione MFA")
    # Enrollment secrets are intentionally returned only to the authenticated
    # browser response, protected by no-store; they are never logged or saved.
    return {"factor_id": factor_id, "qr_code": svg_qr_code, "secret": secret}


@router.post("/mfa/challenge")
async def mfa_challenge(body: MfaFactorRequest, request: Request):
    token, claims = await _mfa_session(request)
    user = await _mfa_user(token, claims["sub"])
    factor = _owned_totp_factor(_totp_factors(user), str(body.factor_id))
    if factor.get("status") == "unverified" and claims.get("aal") == "aal2":
        raise HTTPException(409, "La sessione non è valida per completare questo fattore")
    result = await _mfa_auth_request(
        "POST", f"factors/{body.factor_id}/challenge", token, {}
    )
    challenge_id = result.get("id")
    if not isinstance(challenge_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", challenge_id):
        raise HTTPException(502, "Impossibile avviare la verifica MFA")
    return {"challenge_id": challenge_id}


@router.post("/mfa/verify")
async def mfa_verify(body: MfaVerifyRequest, request: Request, response: Response):
    token, claims = await _mfa_session(request)
    user = await _mfa_user(token, claims["sub"])
    _owned_totp_factor(_totp_factors(user), str(body.factor_id))
    result = await _mfa_auth_request(
        "POST",
        f"factors/{body.factor_id}/verify",
        token,
        {"challenge_id": str(body.challenge_id), "code": body.code},
    )

    new_access = result.get("access_token")
    new_refresh = result.get("refresh_token")
    new_user = result.get("user")
    if (
        not isinstance(new_access, str)
        or not new_access
        or not isinstance(new_refresh, str)
        or not new_refresh
        or not isinstance(new_user, dict)
        or str(new_user.get("id") or "") != claims["sub"]
    ):
        if isinstance(new_access, str):
            await revoke_token(new_access)
        if isinstance(new_refresh, str):
            await revoke_token(new_refresh)
        raise HTTPException(502, "La verifica MFA non ha restituito una sessione valida")

    from src.core.auth.dependencies import verify_supabase_jwt

    try:
        new_claims = await verify_supabase_jwt(new_access)
    except HTTPException:
        await revoke_token(new_access)
        await revoke_token(new_refresh)
        raise HTTPException(502, "La sessione verificata non è valida")
    if new_claims.get("sub") != claims["sub"] or new_claims.get("aal") != "aal2":
        await revoke_token(new_access)
        await revoke_token(new_refresh)
        raise HTTPException(502, "La verifica MFA non ha elevato la sessione")

    # Supabase promotes this session and invalidates the user's other sessions.
    # Locally also denylist the replaced BFF credentials, except if Supabase
    # returned a token unchanged as part of the promoted session.
    if token != new_access:
        await revoke_token(token)
    old_refresh = request.cookies.get(bff.refresh_cookie_name())
    if old_refresh and old_refresh != new_refresh:
        await revoke_token(old_refresh)

    _set_session_cookies(response, result)
    csrf_token = issue_csrf_token(response)
    return {"ok": True, "aal": "aal2", "csrf_token": csrf_token}


@router.delete("/mfa/factors/{factor_id}")
async def mfa_cancel_pending(factor_id: UUID, request: Request):
    token, claims = await _mfa_session(request)
    user = await _mfa_user(token, claims["sub"])
    factor = _owned_totp_factor(_totp_factors(user), str(factor_id))
    if factor.get("status") != "unverified":
        raise HTTPException(409, "Puoi annullare solo una configurazione MFA non verificata")
    await _mfa_auth_request("DELETE", f"factors/{factor_id}", token)
    return {"ok": True}


def _account_throttle_key(ip: str) -> str:
    return f"auth:account:{ip}"


async def _check_account_throttle(ip: str) -> None:
    if await throttle.is_throttled(
        _account_throttle_key(ip), _ACCOUNT_CHANGE_MAX, _ACCOUNT_CHANGE_WINDOW
    ):
        raise HTTPException(
            429, "Troppe modifiche account. Riprova tra un'ora."
        )


async def _require_access_token(request: Request) -> str:
    token = request.cookies.get(bff.access_cookie_name())
    if not token:
        raise HTTPException(401, "Sessione scaduta: effettua di nuovo il login")
    await get_current_user(request, token=token)
    return token


async def _supabase_get_user(token: str) -> dict:
    import httpx

    client = await bff._client()
    try:
        resp = await client.get(
            f"{bff._supabase_url()}/auth/v1/user",
            headers={
                "apikey": bff._anon_key(),
                "Authorization": f"Bearer {token}",
            },
        )
    except httpx.HTTPError:
        raise HTTPException(502, "Servizio autenticazione non raggiungibile")
    if resp.status_code == 401:
        raise HTTPException(401, "Sessione scaduta: effettua di nuovo il login")
    if resp.status_code >= 400:
        raise HTTPException(502, "Impossibile recuperare i dati dell'account")
    return resp.json()


async def _supabase_update_user(token: str, payload: dict) -> dict:
    import httpx

    client = await bff._client()
    try:
        resp = await client.put(
            f"{bff._supabase_url()}/auth/v1/user",
            json=payload,
            headers={
                "apikey": bff._anon_key(),
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
    except httpx.HTTPError:
        raise HTTPException(502, "Servizio autenticazione non raggiungibile")
    if resp.status_code == 401:
        raise HTTPException(401, "Sessione scaduta: effettua di nuovo il login")
    if resp.status_code == 422:
        raise HTTPException(
            422, "Dati non validi (password troppo debole o email non accettata)"
        )
    if resp.status_code == 429:
        raise HTTPException(429, "Troppe richieste. Riprova tra qualche minuto")
    if resp.status_code >= 400:
        raise HTTPException(502, "Modifica non riuscita, riprova")
    return resp.json()


@router.post("/password")
async def change_password(body: PasswordChange, request: Request):
    ip = _client_ip(request)
    await _check_account_throttle(ip)
    await throttle.record_event(_account_throttle_key(ip), _ACCOUNT_CHANGE_WINDOW)

    pwd = body.password
    if len(pwd) < _PASSWORD_MIN or not _SPECIAL_RE.search(pwd):
        raise HTTPException(
            422,
            f"La password deve avere almeno {_PASSWORD_MIN} caratteri "
            "e includere almeno un carattere speciale (es. ! @ # $ %)",
        )
    if not body.current_password or not body.current_password.strip():
        raise HTTPException(400, "Inserisci la password attuale per confermare la modifica")

    token = await _require_access_token(request)

    # Re-autenticazione: verifichiamo crittograficamente la password attuale su Supabase
    user_info = await _supabase_get_user(token)
    user_email = user_info.get("email") if isinstance(user_info, dict) else None
    if user_email:
        try:
            await bff.login(user_email, body.current_password)
        except Exception:
            raise HTTPException(403, "La password attuale non è corretta")

    res_user = await _supabase_update_user(token, {"password": pwd})
    repo = getattr(request.app.state, "repo", None)
    auth_user_id = res_user.get("id") if isinstance(res_user, dict) else None
    if repo and auth_user_id:
        try:
            memberships = await repo.get_memberships_by_auth(str(auth_user_id))
            if memberships:
                org_id = str(memberships[0]["organization_id"])
                u_id = str(memberships[0]["user_id"])
                await audit_log(
                    repo,
                    organization_id=org_id,
                    action="account.password_cambiata",
                    user_id=u_id,
                    auth_user_id=str(auth_user_id),
                    target_table="user_profiles",
                    target_id=u_id,
                    details={"email": res_user.get("email"), "ip": ip},
                )
        except Exception:
            pass
    return {"ok": True, "message": "Password aggiornata"}


@router.post("/send-password-reset")
async def send_password_reset(request: Request):
    ip = _client_ip(request)
    await _check_account_throttle(ip)
    await throttle.record_event(_account_throttle_key(ip), _ACCOUNT_CHANGE_WINDOW)

    token = await _require_access_token(request)
    user = await _supabase_get_user(token)
    email = user.get("email") if isinstance(user, dict) else None
    if not email:
        raise HTTPException(400, "Email dell'account non trovata")

    import httpx
    client = await bff._client()
    try:
        resp = await client.post(
            f"{bff._supabase_url()}/auth/v1/recover",
            json={"email": email},
            headers={"apikey": bff._anon_key(), "Content-Type": "application/json"},
        )
    except httpx.HTTPError:
        raise HTTPException(502, "Servizio autenticazione non raggiungibile")
    if resp.status_code == 429:
        raise HTTPException(429, "Troppe richieste di reset. Riprova tra qualche minuto")
    if resp.status_code >= 400:
        raise HTTPException(502, "Impossibile inviare il link di reset, riprova più tardi")

    parts = email.split("@")
    if len(parts) == 2:
        uname = parts[0]
        masked_user = uname[:2] + "•••" if len(uname) > 2 else uname + "•••"
        email_masked = f"{masked_user}@{parts[1]}"
    else:
        email_masked = email

    return {
        "ok": True,
        "email_masked": email_masked,
        "message": f"Link di reset inviato a {email_masked}",
    }


@router.post("/email")
async def change_email(body: EmailChange, request: Request):
    ip = _client_ip(request)
    await _check_account_throttle(ip)
    await throttle.record_event(_account_throttle_key(ip), _ACCOUNT_CHANGE_WINDOW)

    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "Email non valida")
    token = await _require_access_token(request)
    user = await _supabase_update_user(token, {"email": email})
    # Con "Confirm email" attivo Supabase compila new_email e invia il link:
    # la vecchia email resta attiva fino alla conferma.
    conferma_richiesta = bool(user.get("new_email")) if isinstance(user, dict) else False
    auth_user_id = user.get("id") if isinstance(user, dict) else None
    repo = getattr(request.app.state, "repo", None)
    if repo and auth_user_id:
        try:
            memberships = await repo.get_memberships_by_auth(str(auth_user_id))
            if memberships:
                org_id = str(memberships[0]["organization_id"])
                u_id = str(memberships[0]["user_id"])
                await audit_log(
                    repo,
                    organization_id=org_id,
                    action="account.email_cambiata",
                    user_id=u_id,
                    auth_user_id=str(auth_user_id),
                    target_table="user_profiles",
                    target_id=u_id,
                    details={
                        "old_email": user.get("email"),
                        "new_email": email,
                        "conferma_richiesta": conferma_richiesta,
                        "ip": ip,
                    },
                )
        except Exception:
            pass
    return {
        "ok": True,
        "email": user.get("email") if isinstance(user, dict) else None,
        "conferma_richiesta": conferma_richiesta,
        "message": (
            "Controlla la nuova casella: ti è arrivato il link di conferma"
            if conferma_richiesta
            else "Email aggiornata"
        ),
    }


# ── Recupero password (recover + reset) ────────────────────────────────
# Recover: l'utente NON è autenticato, quindi niente sessione; Supabase
# invia l'email con il link di recovery. Risposta sempre identica a
# prescindere dall'esistenza dell'account: nessuna enumerazione.
# Reset: il link di recovery rimanda al frontend con access_token +
# type=recovery nell'hash dell'URL; il token viene usato come Bearer per
# PUT /auth/v1/user (stessa via del cambio password da autenticato).
# Rate limit condiviso con le altre modifiche account (5/ora/IP).


class RecoverRequest(BaseModel):
    email: str


class ResetPassword(BaseModel):
    access_token: str
    password: str


@router.post("/recover")
async def recover_password(body: RecoverRequest, request: Request):
    ip = _client_ip(request)
    await _check_account_throttle(ip)
    await throttle.record_event(_account_throttle_key(ip), _ACCOUNT_CHANGE_WINDOW)

    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "Email non valida")

    import httpx

    client = await bff._client()
    try:
        await client.post(
            f"{bff._supabase_url()}/auth/v1/recover",
            json={"email": email},
            headers={"apikey": bff._anon_key(), "Content-Type": "application/json"},
        )
    except httpx.HTTPError:
        # Nessuna enumerazione account: la risposta resta identica anche
        # se Supabase non è raggiungibile in questo momento.
        pass
    return {
        "ok": True,
        "message": "Se l'email e' registrata riceverai un link di recupero.",
    }


@router.post("/reset")
async def reset_password(body: ResetPassword, request: Request):
    ip = _client_ip(request)
    await _check_account_throttle(ip)
    await throttle.record_event(_account_throttle_key(ip), _ACCOUNT_CHANGE_WINDOW)

    pwd = body.password
    if len(pwd) < _PASSWORD_MIN or not _SPECIAL_RE.search(pwd):
        raise HTTPException(
            422,
            f"La password deve avere almeno {_PASSWORD_MIN} caratteri "
            "e includere almeno un carattere speciale (es. ! @ # $ %)",
        )
    await get_current_user(request, token=body.access_token)
    await _supabase_update_user(body.access_token, {"password": pwd})
    return {"ok": True, "message": "Password aggiornata, ora puoi accedere"}
