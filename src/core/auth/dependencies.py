import os
import base64
import math
import re
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException, Request

from src.core.auth.denylist import is_token_revoked

# Firme JWT emesse da Supabase Auth: i progetti con le chiavi di firma
# asimmetriche (default 2025+) usano ES256, i piu' vecchi RS256. L'alg e'
# pinnato per chiave dal campo "alg" del JWKS: niente algorithm-switching.
SUPPORTED_JWT_ALGS = ("RS256", "ES256")
JWKS_CACHE: dict[str, Any] = {"keys": None, "expires_at": 0}
HTTP_CLIENT: httpx.AsyncClient | None = None
VALID_RUOLI = {"owner", "manager", "staff"}
ENV_LOADED = False


def _load_project_env() -> None:
    global ENV_LOADED
    if ENV_LOADED:
        return
    if os.getenv("PYTEST_CURRENT_TEST"):
        ENV_LOADED = True
        return
    project_root = Path(__file__).resolve().parents[3]
    load_dotenv(project_root / ".env", override=False)
    ENV_LOADED = True


def is_demo_mode() -> bool:
    _load_project_env()
    # Bloccante B1 (fail-closed): in produzione l'accesso anonimo demo e'
    # sempre negato, qualunque sia il valore di DEMO_MODE nell'ambiente.
    from src.core.security.docs import is_production
    if is_production():
        return False
    return os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")


async def get_http_client() -> httpx.AsyncClient:
    global HTTP_CLIENT
    if HTTP_CLIENT is None:
        HTTP_CLIENT = httpx.AsyncClient(timeout=10.0)
    return HTTP_CLIENT


async def close_http_client():
    global HTTP_CLIENT
    if HTTP_CLIENT is not None:
        await HTTP_CLIENT.aclose()
        HTTP_CLIENT = None


async def _get_supabase_jwks() -> list[dict]:
    supabase_url = os.getenv("SUPABASE_URL")
    if not supabase_url:
        raise HTTPException(500, "SUPABASE_URL non configurato")
    now = time.time()
    if JWKS_CACHE["keys"] and now < JWKS_CACHE["expires_at"]:
        return JWKS_CACHE["keys"]
    client = await get_http_client()
    try:
        resp = await client.get(f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json")
        resp.raise_for_status()
        data = resp.json()
        keys = data["keys"]
        if not isinstance(keys, list) or any(not isinstance(key, dict) for key in keys):
            raise ValueError("Invalid JWKS")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(503, "Chiavi di verifica non disponibili") from exc
    JWKS_CACHE["keys"] = keys
    JWKS_CACHE["expires_at"] = now + 300
    return keys


async def verify_supabase_jwt(token: str, *, allow_expired: bool = False) -> dict:
    import jwt
    from jwt.exceptions import ExpiredSignatureError, PyJWTError

    expected_aud = os.getenv("SUPABASE_JWT_AUD", "authenticated").strip()
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    if not supabase_url or not expected_aud:
        raise HTTPException(500, "Configurazione JWT non valida")
    expected_iss = f"{supabase_url}/auth/v1"

    def has_canonical_segments(compact: str) -> bool:
        # PyJWT accepts padded segment encodings. Since token revocation is
        # keyed by the raw compact JWT, accepting alternate spellings would
        # let a valid signature evade the denylist's exact-token lookup.
        if not isinstance(compact, str):
            return False
        segments = compact.split(".")
        if len(segments) != 3 or any(not segment or not re.fullmatch(r"[A-Za-z0-9_-]+", segment)
                                      for segment in segments):
            return False
        try:
            for segment in segments:
                raw = base64.b64decode(
                    segment + "=" * (-len(segment) % 4), altchars=b"-_", validate=True
                )
                if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != segment:
                    return False
        except (ValueError, TypeError):
            return False
        return True

    def validate_claim_types(payload: dict) -> dict:
        for claim in ("exp", "iat", "nbf"):
            if claim not in payload and claim != "nbf":
                raise ValueError(f"Missing {claim}")
            if claim not in payload:
                continue
            value = payload[claim]
            if (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not _is_finite_numeric_date(value)
            ):
                raise ValueError(f"Invalid {claim}")
        if not isinstance(payload.get("sub"), str) or not payload["sub"].strip():
            raise ValueError("Invalid sub")
        return payload

    def _is_finite_numeric_date(value: int | float) -> bool:
        try:
            return math.isfinite(value)
        except (OverflowError, TypeError, ValueError):
            return False

    if not has_canonical_segments(token):
        raise HTTPException(403, "Token JWT non valido")
    try:
        header = jwt.get_unverified_header(token)
    except (PyJWTError, TypeError, ValueError):
        raise HTTPException(403, "Token JWT non valido") from None
    alg = header.get("alg")
    if alg not in SUPPORTED_JWT_ALGS:
        raise HTTPException(403, "Token JWT non valido")

    kid = header.get("kid")
    if "kid" in header and (not isinstance(kid, str) or not kid):
        raise HTTPException(403, "Token JWT non valido")

    try:
        jwks = await _get_supabase_jwks()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Chiavi di verifica non disponibili") from exc
    if not isinstance(jwks, list) or any(not isinstance(key, dict) for key in jwks):
        raise HTTPException(403, "Token JWT non valido")

    def eligible(jwk: dict) -> bool:
        kty = jwk.get("kty")
        if not isinstance(kty, str):
            return False
        bound_alg = {"RSA": "RS256", "EC": "ES256"}.get(kty)
        if bound_alg != alg or (kty == "EC" and jwk.get("crv") != "P-256"):
            return False
        if jwk.get("alg") not in (None, bound_alg) or jwk.get("use") not in (None, "sig"):
            return False
        key_ops = jwk.get("key_ops")
        return key_ops is None or (isinstance(key_ops, list) and "verify" in key_ops)

    if kid is not None:
        matches = [key for key in jwks if key.get("kid") == kid]
        if len(matches) != 1 or not eligible(matches[0]):
            raise HTTPException(403, "Token JWT non valido")
        candidates = matches
    else:
        candidates = [key for key in jwks if eligible(key)]
        if len(candidates) != 1:
            raise HTTPException(403, "Token JWT non valido")

    try:
        from jwt import PyJWK

        bound_alg = {"RSA": "RS256", "EC": "ES256"}[candidates[0]["kty"]]
        signing_key = PyJWK.from_dict(candidates[0], algorithm=bound_alg).key
        options = {
            "verify_signature": True,
            "verify_exp": True,
            "verify_iat": True,
            "verify_nbf": True,
            "verify_iss": True,
            "verify_aud": True,
            "require": ["exp", "iat", "iss", "aud", "sub"],
        }
        try:
            return validate_claim_types(jwt.decode(
                token, signing_key, algorithms=[bound_alg], audience=expected_aud,
                issuer=expected_iss, options=options,
            ))
        except ExpiredSignatureError:
            # PyJWT valida exp prima di iss/aud. Ripetiamo l'intera verifica
            # firmata ignorando solo exp, prima di assegnare 401/identita'.
            try:
                payload = validate_claim_types(jwt.decode(
                    token, signing_key, algorithms=[bound_alg], audience=expected_aud,
                    issuer=expected_iss,
                    options={**options, "verify_exp": False},
                ))
            except PyJWTError:
                raise HTTPException(403, "Token JWT non valido") from None
            if allow_expired:
                return payload
            raise HTTPException(401, "Sessione scaduta: rinnova l'accesso") from None
    except HTTPException:
        raise
    except (PyJWTError, TypeError, ValueError, OverflowError, KeyError, AttributeError):
        raise HTTPException(403, "Token JWT non valido") from None


async def get_token(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
) -> str | None:
    if authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            raise HTTPException(status_code=401, detail="Credenziali non valide")
        return credential.strip()
    if x_api_key:
        return f"apikey:{x_api_key}"
    # BFF (task18): sessione in cookie HttpOnly+Secure+SameSite=Lax.
    # L'access token viaggia nel cookie, mai nel JS/localStorage.
    from src.core.auth import bff
    cookie_token = request.cookies.get(bff.access_cookie_name())
    if cookie_token:
        return cookie_token
    return None


async def get_current_user(
    request: Request,
    token: str | None = Depends(get_token),
) -> dict:
    if token is None:
        if not is_demo_mode():
            raise HTTPException(status_code=401, detail="Token o API Key richiesti")
        return {
            "auth_user_id": None,
            "organization_id": None,
            "ruolo": None,
            "source": "anonymous",
        }
    if token.startswith("apikey:"):
        # Service credentials never represent a user or a tenant membership.
        raise HTTPException(status_code=403, detail="Le API key non sono ammesse sulle API utente")
    if await is_token_revoked(token):
        raise HTTPException(status_code=401, detail="Sessione revocata: effettua di nuovo il login")
    payload = await verify_supabase_jwt(token)
    if await get_repo(request).get_auth_access_allowed(payload["sub"]) is not True:
        raise HTTPException(status_code=403, detail="Accesso account non consentito")
    return {
        "auth_user_id": payload["sub"],
        "email": payload.get("email"),
        "organization_id": None,
        "ruolo": None,
        "source": "jwt",
        # Stable across access-token refresh; internal OAuth session binding.
        "session_id": payload.get("session_id"),
        # Audit 1.4: aal (authenticator assurance level) serve per il gate
        # MFA sui Tier-1 sensibili (billing, GDPR hard-delete/export). Lo
        # estraiamo dal JWT qui, una sola volta, e require_mfa lo legge dal
        # dict utente: evita di rivalutare il claim su ogni endpoint.
        "aal": payload.get("aal"),
        # Used only by narrowly scoped privileged recovery routes which require
        # a fresh MFA step-up; the claim is trusted only after JWT verification.
        "auth_time": payload.get("auth_time"),
    }


def get_repo(request: Request):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(500, "Repository non inizializzato")
    return repo


async def get_organization_context(
    request: Request,
    current_user: dict = Depends(get_current_user),
    x_organization_id: str | None = Header(None),
) -> dict:
    if current_user.get("source") != "jwt":
        raise HTTPException(status_code=401, detail="Sessione utente richiesta")
    # Task18: risoluzione tenant server-side dall'identità nel JWT validato.
    # L'header X-Organization-Id NON è più fonte di fiducia per l'org: la
    # membership si ricava dal DB. Con 1 solo membership l'org è univoca e
    # l'header è ignorato del tutto. Con più membership l'header può solo
    # selezionare TRA le org di cui l'utente è davvero membro (mai fiducia
    # cieca su un id arbitrario).
    repo = get_repo(request)
    memberships = await repo.get_memberships_by_auth(current_user["auth_user_id"])
    if not memberships:
        # Auto-provisioning JIT dell'organizzazione per utenti autenticati
        try:
            email = current_user.get("email") or "La tua attività"
            nome_org = email.split("@")[0].replace(".", " ").title() if "@" in email else email
            trial_days = int(os.getenv("TRIAL_DAYS", "7"))
            await repo.get_or_create_organization_with_owner(
                str(current_user["auth_user_id"]),
                nome_org,
                trial_days,
            )
            memberships = await repo.get_memberships_by_auth(current_user["auth_user_id"])
        except Exception:
            pass

    if not memberships:
        raise HTTPException(403, "Non sei membro di nessuna organizzazione")
    if len(memberships) == 1:
        m = memberships[0]
        return {
            **current_user,
            "organization_id": str(m["organization_id"]),
            "ruolo": m["ruolo"],
            "user_id": str(m["user_id"]),
        }
    if not x_organization_id:
        raise HTTPException(403, "Seleziona un'organizzazione")
    for m in memberships:
        if str(m["organization_id"]) == x_organization_id:
            return {
                **current_user,
                "organization_id": x_organization_id,
                "ruolo": m["ruolo"],
                "user_id": str(m["user_id"]),
            }
    raise HTTPException(403, "Non sei membro di questa organizzazione")


async def get_optional_organization_context(request: Request) -> dict | None:
    """Risolve l'organization context in modo opzionale per endpoint ad accesso ibrido (es. simulatore).
    Supporta cookie HttpOnly BFF, Bearer token e dependency_overrides nei test.
    Restituisce un'identita' anonima solo in modalita' demo senza credenziali;
    credenziali presenti ma non valide e errori di membership vengono propagati."""
    if hasattr(request, "app") and hasattr(request.app, "dependency_overrides"):
        if get_organization_context in request.app.dependency_overrides:
            override = request.app.dependency_overrides[get_organization_context]
            import inspect
            res = override()
            if inspect.iscoroutine(res):
                return await res
            return res

    from src.core.auth import bff

    has_credentials = any((
        request.headers.get("Authorization") is not None,
        request.headers.get("X-API-Key") is not None,
        request.cookies.get(bff.access_cookie_name()) is not None,
        request.cookies.get(bff.refresh_cookie_name()) is not None,
    ))
    if not has_credentials:
        if not is_demo_mode():
            raise HTTPException(status_code=401, detail="Token o API Key richiesti")
        return {
            "auth_user_id": None,
            "organization_id": None,
            "ruolo": None,
            "source": "anonymous",
        }

    token = await get_token(
        request,
        authorization=request.headers.get("Authorization"),
        x_api_key=request.headers.get("X-API-Key"),
    )
    # A refresh cookie without its access cookie is still an attempted
    # authenticated session; this endpoint does not silently downgrade it.
    if not token:
        raise HTTPException(status_code=401, detail="Sessione non valida: effettua di nuovo il login")
    user = await get_current_user(request, token=token)
    if not user or user.get("source") == "anonymous":
        raise HTTPException(status_code=401, detail="Sessione utente richiesta")
    return await get_organization_context(
        request,
        current_user=user,
        x_organization_id=request.headers.get("X-Organization-Id"),
    )


def require_ruolo(*ruoli: str):
    invalid = set(ruoli) - VALID_RUOLI
    if invalid:
        raise ValueError(f"Ruoli non validi: {invalid}. Validi: {VALID_RUOLI}")
    async def _check(user: dict = Depends(get_organization_context)):
        if user.get("source") != "jwt":
            raise HTTPException(status_code=401, detail="Token o API Key richiesti")
        if user.get("ruolo") not in ruoli:
            raise HTTPException(
                403, f"Richiesto ruolo: {', '.join(ruoli)}"
            )
        return user
    return _check


# Audit 1.4: endpoint "sensibili" (Tier-1) che richiedono step-up a MFA.
# Operazioni irreversibili, finanziarie o di esfiltrazione PII: l'uso di una
# sessione rubata (cookie/JSON rubato) deve comunque bloccarsi se l'utente
# non ha fatto il secondo fattore. La lista va tenuta corta di proposito:
# ogni endpoint qui aggiunto rende il prodotto meno usabile da mobile, dove
# il titolare gestisce le urgenze HITL. Per questo HITL reply, booking,
# documenti NON sono in questa lista.
SENSITIVE_AAL2_PATHS = frozenset({
    "/api/billing/create-checkout-session",
    "/api/billing/create-portal-session",
    "/api/billing/provider-attempts/reconciliation/proposals",
    "/api/billing/provider-attempts/reconciliation/approvals",
    "/api/billing/provider-attempts/{attempt_id}/recover",
    "/api/gdpr/export",
    "/api/gdpr/download",
    "/api/gdpr/delete",
    "/api/calendar/auth",
    "/api/calendar/disconnect",
    "/api/calendar/settings",
    "/api/reviews/google/auth",
    "/api/reviews/google/settings",
    "/api/reviews/google/disconnect",
})


def require_mfa():
    """Dipendenza da combinare DOPO require_ruolo sui Tier-1.

    Esempio:
        user = Depends(require_ruolo("owner"))
        mfa  = Depends(require_mfa())

    Solo una sessione utente verificata con secondo fattore soddisfa il gate.
    """
    async def _check(user: dict = Depends(get_current_user)):
        # aal: Supabase popola "aal2" solo dopo verifica del secondo fattore.
        # Token legacy o sessioni senza MFA portano aal=None o "aal1".
        if user.get("source") != "jwt" or user.get("aal") != "aal2":
            raise HTTPException(
                status_code=403,
                detail="Autenticazione a due fattori (MFA) richiesta per questa operazione. "
                       "Abilitala in Impostazioni > Sicurezza e riprova.",
                headers={"X-MFA-Required": "true"},
            )
        return user
    return _check
