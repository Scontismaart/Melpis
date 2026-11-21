"""Team & Multi-User Management API Routes (Invarianti 1, 2, 8).

Gestisce:
- Elenco membri del team dell'organizzazione (/api/team/members)
- Aggiunta/invito collaboratori con enforcement rigido di users_limit per piano
- Rimozione collaboratori e gestione ruoli (manager, staff)
"""

import logging
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from src.api.dependencies import get_pool, require_ruolo
from src.api.routes.common import audit_event
from src.core.auth.dependencies import get_current_user, get_token
from src.core.billing.plans import PLANS
from src.core.team_invitations import accept_invitation, create_invitation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/team", tags=["team"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AddTeamMemberInput(BaseModel):
    email: str
    ruolo: Literal["manager", "staff"] = "staff"

    @field_validator("email")
    @classmethod
    def validate_email_fmt(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Indirizzo email non valido")
        return v


class UpdateRoleInput(BaseModel):
    ruolo: Literal["manager", "staff"]


class AcceptTeamInviteInput(BaseModel):
    token: str


class TeamMemberItem(BaseModel):
    id: str
    user_id: str
    nome: str
    email: str
    ruolo: str
    joined_at: str | None = None


class TeamListResponse(BaseModel):
    members: list[TeamMemberItem]
    total: int
    users_limit: int | None
    can_add_more: bool


@router.get("/members", response_model=TeamListResponse)
async def list_team_members(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager", "staff")),
):
    """Restituisce l'elenco dei membri dell'organizzazione, il limite del piano e la disponibilità."""
    org_id = user["organization_id"]
    pool = get_pool(request)

    async with pool.acquire() as conn:
        org_row = await conn.fetchrow(
            "SELECT plan, users_limit, subscription_status FROM organizations WHERE id = $1::uuid",
            uuid.UUID(org_id),
        )
        plan = org_row["plan"] if org_row else None
        # Se non c'è un piano o l'organizzazione è in prova gratuita, applichiamo il tier 'pro' (3 utenti)
        effective_plan = plan or "pro"
        plan_def = PLANS.get(effective_plan)
        if org_row and org_row["users_limit"] is not None:
            users_limit = org_row["users_limit"]
        elif plan_def:
            users_limit = plan_def.users_limit
        else:
            users_limit = 3

        rows = await conn.fetch("""
            SELECT om.id, om.user_id, up.nome, up.email, om.ruolo, om.joined_at
            FROM organization_memberships om
            JOIN user_profiles up ON up.id = om.user_id
            WHERE om.organization_id = $1::uuid
              AND NOT EXISTS (SELECT 1 FROM gdpr_erasure_barriers b
                 WHERE b.organization_id=$1::uuid AND b.scope='organization')
            ORDER BY
              CASE om.ruolo WHEN 'owner' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END,
              om.joined_at ASC
        """, uuid.UUID(org_id))

    members = [
        TeamMemberItem(
            id=str(r["id"]),
            user_id=str(r["user_id"]),
            nome=r["nome"] or r["email"].split("@")[0],
            email=r["email"],
            ruolo=r["ruolo"],
            joined_at=r["joined_at"].isoformat() if r["joined_at"] else None,
        )
        for r in rows
    ]
    total = len(members)
    can_add_more = (users_limit is None) or (total < users_limit)

    return TeamListResponse(
        members=members,
        total=total,
        users_limit=users_limit,
        can_add_more=can_add_more,
    )


@router.post("/members")
async def add_team_member(
    body: AddTeamMemberInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Compatibilità API: crea solo un invito pendente, mai una membership."""
    org_id = user["organization_id"]
    pool = get_pool(request)

    # I manager possono invitare solo staff.
    if user["ruolo"] == "manager" and body.ruolo != "staff":
        raise HTTPException(
            status_code=403,
            detail="I manager possono aggiungere solo collaboratori con ruolo 'staff'.",
        )

    return await create_invitation(pool, org_id=org_id, actor=user, email=body.email, role=body.ruolo)


@router.post("/invitations/accept")
async def accept_team_invitation(
    body: AcceptTeamInviteInput,
    request: Request,
    user: dict = Depends(get_current_user),
    access_token: str | None = Depends(get_token),
):
    if user.get("source") != "jwt" or not access_token:
        raise HTTPException(401, "Accedi per accettare l'invito")
    return await accept_invitation(
        get_pool(request), raw_token=body.token,
        auth_user_id=user["auth_user_id"], access_token=access_token,
    )


@router.get("/organizations")
async def list_my_organizations(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Resolve only memberships belonging to the verified JWT subject."""
    if user.get("source") != "jwt":
        raise HTTPException(401, "Sessione utente richiesta")
    async with get_pool(request).acquire() as conn:
        rows = await conn.fetch(
            """SELECT om.organization_id, o.name, om.ruolo
               FROM organization_memberships om
               JOIN user_profiles up ON up.id = om.user_id
               JOIN organizations o ON o.id = om.organization_id
               WHERE up.auth_user_id = $1::uuid AND om.joined_at IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM gdpr_erasure_barriers b
                    WHERE b.organization_id=om.organization_id AND b.scope='organization')
               ORDER BY om.joined_at DESC, om.organization_id""",
            uuid.UUID(user["auth_user_id"]),
        )
    return {"organizations": [
        {"id": str(row["organization_id"]), "name": row["name"], "ruolo": row["ruolo"]}
        for row in rows
    ]}


@router.get("/invitations")
async def list_team_invitations(
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    async with get_pool(request).acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, email, ruolo, created_at, expires_at
               FROM team_invitations WHERE organization_id = $1::uuid
                 AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at > NOW()
               ORDER BY created_at DESC LIMIT 100""",
            uuid.UUID(user["organization_id"]),
        )
    return {"invitations": [
        {"id": str(row["id"]), "email": row["email"], "ruolo": row["ruolo"],
         "created_at": row["created_at"].isoformat(), "expires_at": row["expires_at"].isoformat()}
        for row in rows
    ]}


@router.delete("/invitations/{invitation_id}")
async def revoke_team_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    org_id = uuid.UUID(user["organization_id"])
    async with get_pool(request).acquire() as conn, conn.transaction():
        await conn.execute("SELECT id FROM organizations WHERE id = $1::uuid FOR UPDATE", org_id)
        actor = await conn.fetchrow(
            """SELECT ruolo FROM organization_memberships WHERE organization_id = $1::uuid
               AND user_id = $2::uuid AND joined_at IS NOT NULL FOR UPDATE""",
            org_id, uuid.UUID(user["user_id"]),
        )
        if not actor or actor["ruolo"] not in {"owner", "manager"}:
            raise HTTPException(403, "Permesso Team non disponibile")
        row = await conn.fetchrow(
            """UPDATE team_invitations SET revoked_at = NOW()
                WHERE id = $1::uuid AND organization_id = $2::uuid
                  AND consumed_at IS NULL AND revoked_at IS NULL
                  AND ($3 = 'owner' OR ruolo = 'staff')
                RETURNING id""",
            invitation_id, org_id, actor["ruolo"],
        )
        if row:
            await conn.execute(
                """INSERT INTO audit_log
                   (organization_id, user_id, auth_user_id, action, target_table, target_id)
                   VALUES ($1::uuid, $2::uuid, $3, 'team.invito_revocato', 'team_invitations', $4::uuid)""",
                org_id, uuid.UUID(user["user_id"]), str(user["auth_user_id"]), invitation_id,
            )
    return {"detail": "Invito revocato"}


@router.post("/invitations/{invitation_id}/resend")
async def resend_team_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    org_id = user["organization_id"]
    async with get_pool(request).acquire() as conn:
        row = await conn.fetchrow(
            """SELECT email, ruolo FROM team_invitations
               WHERE id = $1::uuid AND organization_id = $2::uuid
                 AND consumed_at IS NULL AND revoked_at IS NULL""",
            invitation_id, uuid.UUID(org_id),
        )
    if not row:
        raise HTTPException(404, "Invito non trovato")
    return await create_invitation(
        get_pool(request), org_id=org_id, actor=user,
        email=row["email"], role=row["ruolo"],
        expected_invitation_id=invitation_id,
    )


@router.delete("/members/{member_user_id}")
async def remove_team_member(
    member_user_id: str,
    request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
):
    """Rimuove un collaboratore dall'organizzazione."""
    org_id = user["organization_id"]
    pool = get_pool(request)

    try:
        t_uid = uuid.UUID(member_user_id)
    except ValueError:
        raise HTTPException(400, "ID utente non valido")

    if str(t_uid) == str(user.get("user_id")):
        raise HTTPException(400, "Non puoi rimuovere te stesso dal team.")

    async with pool.acquire() as conn:
        target = await conn.fetchrow("""
            SELECT om.id, om.ruolo, up.email
            FROM organization_memberships om
            JOIN user_profiles up ON up.id = om.user_id
            WHERE om.organization_id = $1::uuid AND om.user_id = $2::uuid
        """, uuid.UUID(org_id), t_uid)

        if not target:
            raise HTTPException(404, "Collaboratore non trovato nell'organizzazione.")

        if target["ruolo"] == "owner":
            raise HTTPException(400, "Non è possibile rimuovere il proprietario dell'organizzazione.")

        if user["ruolo"] == "manager" and target["ruolo"] == "manager":
            raise HTTPException(403, "I manager possono rimuovere solo membri dello staff.")

        await conn.execute(
            "DELETE FROM organization_memberships WHERE organization_id = $1::uuid AND user_id = $2::uuid",
            uuid.UUID(org_id), t_uid,
        )

    await audit_event(
        request,
        user,
        "team.membro_rimosso",
        target_table="organization_memberships",
        target_id=str(target["id"]),
        details={"user_id": member_user_id, "email": target["email"], "ruolo": target["ruolo"]},
    )

    return {"detail": f"Collaboratore {target['email']} rimosso con successo."}


@router.patch("/members/{member_user_id}")
async def update_team_member_role(
    member_user_id: str,
    body: UpdateRoleInput,
    request: Request,
    user: dict = Depends(require_ruolo("owner")),
):
    """Modifica il ruolo di un collaboratore (solo proprietario)."""
    org_id = user["organization_id"]
    pool = get_pool(request)

    try:
        t_uid = uuid.UUID(member_user_id)
    except ValueError:
        raise HTTPException(400, "ID utente non valido")

    async with pool.acquire() as conn:
        target = await conn.fetchrow(
            "SELECT id, ruolo FROM organization_memberships WHERE organization_id = $1::uuid AND user_id = $2::uuid",
            uuid.UUID(org_id), t_uid,
        )
        if not target:
            raise HTTPException(404, "Collaboratore non trovato.")

        if target["ruolo"] == "owner":
            raise HTTPException(400, "Non è possibile modificare il ruolo del proprietario.")

        await conn.execute(
            "UPDATE organization_memberships SET ruolo = $3 WHERE organization_id = $1::uuid AND user_id = $2::uuid",
            uuid.UUID(org_id), t_uid, body.ruolo,
        )

    await audit_event(
        request,
        user,
        "team.ruolo_aggiornato",
        target_table="organization_memberships",
        target_id=str(target["id"]),
        details={"user_id": member_user_id, "vecchio_ruolo": target["ruolo"], "nuovo_ruolo": body.ruolo},
    )

    return {"detail": f"Ruolo aggiornato a '{body.ruolo}'."}
