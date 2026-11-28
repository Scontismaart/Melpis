"""Server-owned Team invitations. Tokens are capabilities, never memberships."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import smtplib
import uuid
from email.message import EmailMessage

from fastapi import HTTPException

from src.core.auth import bff
from src.core.auth.dependencies import get_http_client
from src.core.auth.signup_policy import beta_email_is_allowed
from src.core.billing.plans import PLANS
from src.core.billing.suspension import is_org_suspended
from src.core.notifications.email_service import _get_smtp_config
from src.core.rate_limit import get_rate_limiter
from src.core.security.docs import is_production

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,100}$")
_GENERIC = "Se l'invito è valido, riceverai le istruzioni all'indirizzo indicato."
_UNUSABLE = "Invito non valido o non più utilizzabile."


def team_seat_entitlement(org) -> tuple[bool, int | None]:
    if not org or is_org_suspended(org["subscription_status"], org["trial_end"]):
        return False, None
    plan_slug = org["plan"]
    if plan_slug is None and org["subscription_status"] == "trialing":
        plan_slug = "pro"
    plan = PLANS.get(plan_slug)
    if plan is None:
        return False, None
    limit = org["users_limit"]
    return True, plan.users_limit if limit is None else limit


def _dev_links_enabled() -> bool:
    return (
        not is_production()
        and os.getenv("APP_ENV", "").lower() in {"development", "test"}
        and os.getenv("TEAM_INVITE_DEV_LINKS", "").lower() == "true"
    )


def delivery_available() -> bool:
    return _dev_links_enabled() or all(
        os.getenv(name, "").strip()
        for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")
    )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _invite_url(token: str) -> str:
    # A fragment does not reach HTTP access logs or the reverse proxy.
    return f"{bff.public_app_url()}/app/#team-invite={token}"


async def send_invitation(email: str, invite_url: str) -> None:
    if _dev_links_enabled():
        return
    config = _get_smtp_config()
    if config is None:
        raise RuntimeError("Team SMTP not configured")
    msg = EmailMessage()
    msg["Subject"] = "Invito al team Melpis"
    msg["From"] = config["from_addr"]
    msg["To"] = email
    msg.set_content(
        "Hai ricevuto un invito a collaborare su Melpis.\n\n"
        f"Apri questo link entro 7 giorni: {invite_url}\n\n"
        "Accedi con lo stesso indirizzo email e confermalo se necessario "
        "per accettare l'invito.\n"
        "Se non lo aspettavi, ignora questa email."
    )

    def _send() -> None:
        with smtplib.SMTP(config["host"], config["port"], timeout=10) as smtp:
            smtp.starttls()
            smtp.login(config["user"], config["password"])
            smtp.send_message(msg)

    await asyncio.to_thread(_send)


async def verified_supabase_identity(token: str, expected_auth_user_id: str) -> str:
    """Read the current verified email from Auth, not a possibly stale JWT."""
    client = await get_http_client()
    try:
        response = await client.get(
            f"{bff._supabase_url()}/auth/v1/user",
            headers={"apikey": bff._anon_key(), "Authorization": f"Bearer {token}"},
        )
        if response.status_code != 200:
            raise HTTPException(401, "Sessione non valida")
        identity = response.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Verifica identità temporaneamente non disponibile") from exc
    if str(identity.get("id")) != str(expected_auth_user_id):
        raise HTTPException(401, "Sessione non valida")
    email = identity.get("email")
    if not isinstance(email, str) or not identity.get("email_confirmed_at"):
        raise HTTPException(403, "Conferma il tuo indirizzo email prima di accettare")
    return email.strip().lower()


async def _audit(conn, org_id, actor_id, auth_user_id, action, invitation_id) -> None:
    await conn.execute(
        """INSERT INTO audit_log
           (organization_id, user_id, auth_user_id, action, target_table, target_id)
           VALUES ($1::uuid, $2::uuid, $3, $4, 'team_invitations', $5::uuid)""",
        org_id, actor_id, str(auth_user_id), action, invitation_id,
    )


async def create_invitation(
    pool, *, org_id: str, actor: dict, email: str, role: str,
    expected_invitation_id: uuid.UUID | None = None,
) -> dict:
    if not delivery_available():
        raise HTTPException(503, "Inviti email non configurati")
    org_uuid = uuid.UUID(str(org_id))
    actor_uuid = uuid.UUID(str(actor["user_id"]))
    email = email.strip().lower()
    if not beta_email_is_allowed(email):
        raise HTTPException(403, "Destinatario non autorizzato per la beta")
    limiter = await get_rate_limiter()
    if await limiter.hit(f"team-invite-org:{org_uuid}", 30, 86400):
        raise HTTPException(429, "Troppi inviti; riprova più tardi")
    if await limiter.hit(f"team-invite-email:{org_uuid}:{_hash(email)}", 3, 86400):
        raise HTTPException(429, "Troppi inviti; riprova più tardi")
    token = secrets.token_urlsafe(32)
    invite_url = _invite_url(token)
    invitation_id = uuid.uuid4()
    async with pool.acquire() as conn, conn.transaction():
        org = await conn.fetchrow("SELECT id FROM organizations WHERE id = $1::uuid FOR UPDATE", org_uuid)
        if not org:
            raise HTTPException(403, "Organizzazione non disponibile")
        membership = await conn.fetchrow(
            """SELECT ruolo FROM organization_memberships
               WHERE organization_id = $1::uuid AND user_id = $2::uuid
                 AND joined_at IS NOT NULL FOR UPDATE""",
            org_uuid, actor_uuid,
        )
        if not membership or membership["ruolo"] not in {"owner", "manager"}:
            raise HTTPException(403, "Permesso Team non disponibile")
        if membership["ruolo"] == "manager" and role != "staff":
            raise HTTPException(403, "I manager possono invitare solo staff")
        if expected_invitation_id is not None:
            pending = await conn.fetchrow(
                """SELECT id FROM team_invitations
                   WHERE id = $1::uuid AND organization_id = $2::uuid
                     AND email = $3 AND ruolo = $4
                     AND consumed_at IS NULL AND revoked_at IS NULL
                     AND expires_at > NOW() FOR UPDATE""",
                expected_invitation_id, org_uuid, email, role,
            )
            if not pending:
                raise HTTPException(404, "Invito non trovato")
        await conn.execute(
            """UPDATE team_invitations SET revoked_at = NOW()
               WHERE organization_id = $1::uuid AND email = $2
                 AND consumed_at IS NULL AND revoked_at IS NULL""",
            org_uuid, email,
        )
        await conn.execute(
            """INSERT INTO team_invitations
               (id, organization_id, email, ruolo, token_hash, invited_by, expires_at)
               VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid, NOW() + INTERVAL '7 days')""",
            invitation_id, org_uuid, email, role, _hash(token), actor_uuid,
        )
        await _audit(conn, org_uuid, actor_uuid, actor["auth_user_id"], "team.invito_creato", invitation_id)

    try:
        await send_invitation(email, invite_url)
    except Exception as exc:
        # A failed delivery must not leave an apparently actionable invitation.
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE team_invitations SET revoked_at = NOW()
                   WHERE id = $1::uuid AND organization_id = $2::uuid
                     AND consumed_at IS NULL AND revoked_at IS NULL""",
                invitation_id, org_uuid,
            )
        raise HTTPException(503, "Invito non consegnato; riprova più tardi") from exc
    result = {"detail": _GENERIC}
    if _dev_links_enabled():
        result["test_link"] = invite_url
    return result


async def accept_invitation(pool, *, raw_token: str, auth_user_id: str, access_token: str) -> dict:
    if not _TOKEN_RE.fullmatch(raw_token):
        raise HTTPException(400, _UNUSABLE)
    email = await verified_supabase_identity(access_token, auth_user_id)
    if not beta_email_is_allowed(email):
        raise HTTPException(400, _UNUSABLE)
    token_hash = _hash(raw_token)
    async with pool.acquire() as conn:
        lookup = await conn.fetchrow(
            "SELECT organization_id FROM team_invitations WHERE token_hash = $1", token_hash,
        )
        if not lookup:
            raise HTTPException(400, _UNUSABLE)
        org_id = lookup["organization_id"]
        async with conn.transaction():
            org = await conn.fetchrow(
                """SELECT plan, users_limit, subscription_status, trial_end FROM organizations WHERE id = $1::uuid
                   AND NOT EXISTS (SELECT FROM gdpr_erasure_barriers b
                       WHERE b.organization_id=$1::uuid AND b.scope='organization') FOR UPDATE""", org_id,
            )
            if not org:
                raise HTTPException(400, _UNUSABLE)
            invite = await conn.fetchrow(
                """SELECT id, email, ruolo, invited_by FROM team_invitations
                   WHERE token_hash = $1 AND organization_id = $2::uuid FOR UPDATE""",
                token_hash, org_id,
            )
            if not invite or invite["email"] != email:
                raise HTTPException(400, _UNUSABLE)
            live = await conn.fetchval(
                """SELECT EXISTS (
                   SELECT 1 FROM team_invitations
                   WHERE id = $1::uuid AND organization_id = $2::uuid
                     AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at > NOW())""",
                invite["id"], org_id,
            )
            if not live:
                raise HTTPException(400, _UNUSABLE)
            inviter = await conn.fetchrow(
                """SELECT ruolo FROM organization_memberships
                   WHERE organization_id = $1::uuid AND user_id = $2::uuid
                     AND joined_at IS NOT NULL FOR UPDATE""",
                org_id, invite["invited_by"],
            )
            if not inviter or inviter["ruolo"] not in {"owner", "manager"} or (
                inviter["ruolo"] == "manager" and invite["ruolo"] != "staff"
            ):
                raise HTTPException(400, _UNUSABLE)
            profile = await conn.fetchrow(
                "SELECT id FROM user_profiles WHERE auth_user_id = $1::uuid", uuid.UUID(auth_user_id),
            )
            if not profile:
                raise HTTPException(400, _UNUSABLE)
            existing = await conn.fetchval(
                """SELECT id FROM organization_memberships
                   WHERE organization_id = $1::uuid AND user_id = $2::uuid""",
                org_id, profile["id"],
            )
            if not existing:
                can_add, users_limit = team_seat_entitlement(org)
                if not can_add:
                    raise HTTPException(400, _UNUSABLE)
                count = await conn.fetchval(
                    "SELECT count(*) FROM organization_memberships WHERE organization_id = $1::uuid", org_id,
                )
                if users_limit is not None and count >= users_limit:
                    raise HTTPException(409, "Nessun posto disponibile nel team")
                await conn.execute(
                    """INSERT INTO organization_memberships
                       (organization_id, user_id, ruolo, invited_at, joined_at)
                       VALUES ($1::uuid, $2::uuid, $3, NOW(), NOW())""",
                    org_id, profile["id"], invite["ruolo"],
                )
            await conn.execute(
                """UPDATE team_invitations SET consumed_at = NOW()
                   WHERE id = $1::uuid AND organization_id = $2::uuid""",
                invite["id"], org_id,
            )
            await _audit(conn, org_id, profile["id"], auth_user_id, "team.invito_accettato", invite["id"])
    return {"detail": "Invito accettato", "organization_id": str(org_id)}
