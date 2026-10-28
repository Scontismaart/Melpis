"""Owner-managed catalogue API; publication never activates booking V2."""
from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.core.auth.dependencies import require_mfa, require_ruolo
from src.core.bookings.catalog_contracts import CatalogDraft
from src.core.db.repositories.booking_catalog_repo import (
    BookingCatalogRepository, CatalogConflict, CatalogUnavailable,
)
from src.core.db.repositories.booking_repo import BookingRepository

router = APIRouter(prefix="/catalog", tags=["bookings"])


class RevisionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    request_id: UUID


class DraftRequest(RevisionRequest):
    catalog: CatalogDraft


async def parse_body(request, model):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 524288:
            raise HTTPException(413, "Catalogo troppo grande: riduci la configurazione")
        raw.extend(chunk)
    def unique_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate key")
            value[key] = item
        return value
    try:
        text = raw.decode("utf-8")
        json.loads(text, object_pairs_hook=unique_keys)
        return model.model_validate_json(text)
    except (ValueError, RecursionError, UnicodeError, ValidationError):
        raise HTTPException(422, "Catalogo non valido: controlla campi, risorse, orari e revisione") from None


def repository(request):
    service = getattr(request.app.state, "booking_service", None)
    repo = getattr(service, "repo", None)
    if not isinstance(repo, BookingRepository):
        raise HTTPException(503, "Catalogo non disponibile: serve il database configurato")
    return BookingCatalogRepository(repo)


def capabilities(state, user):
    return {**state, "capabilities": {
        "can_save": user.get("ruolo") == "owner",
        "can_publish": user.get("ruolo") == "owner" and user.get("aal") == "aal2",
    }}


async def execute(operation):
    try:
        return await operation
    except CatalogConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except CatalogUnavailable:
        raise HTTPException(503, "Catalogo non disponibile: verifica lo stato dell'ambiente") from None
    except (ValueError, TypeError):
        raise HTTPException(422, "Configurazione catalogo non valida o incoerente con il profilo") from None
    except (asyncpg.PostgresError, OSError, TimeoutError):
        raise HTTPException(503, "Catalogo non disponibile: riprova senza cambiare richiesta") from None


@router.get("")
async def get_catalog(request: Request, user: dict = Depends(require_ruolo("owner", "manager"))):
    return capabilities(await execute(repository(request).read(user["organization_id"])), user)


@router.get("/audit")
async def catalog_audit(request: Request, user: dict = Depends(require_ruolo("owner", "manager"))):
    return await execute(repository(request).audit(user["organization_id"]))


@router.put("/draft")
async def save_catalog_draft(request: Request, user: dict = Depends(require_ruolo("owner"))):
    body = await parse_body(request, DraftRequest)
    state = await execute(repository(request).write(
        user["organization_id"], user["user_id"], user["auth_user_id"], action="draft_saved",
        expected_revision=body.expected_revision, request_id=body.request_id,
        catalog=body.catalog.model_dump(mode="json"),
    ))
    return capabilities(state, user)


@router.post("/publish")
async def publish_catalog(request: Request, user: dict = Depends(require_ruolo("owner")),
                          mfa: dict = Depends(require_mfa())):
    body = await parse_body(request, RevisionRequest)
    state = await execute(repository(request).write(
        user["organization_id"], user["user_id"], user["auth_user_id"], action="published",
        expected_revision=body.expected_revision, request_id=body.request_id,
    ))
    return capabilities(state, user)
