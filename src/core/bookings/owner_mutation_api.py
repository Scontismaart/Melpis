"""Disabled-by-default owner bridge to the typed booking transaction."""
import os
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.core.auth.dependencies import require_mfa, require_ruolo
from src.core.bookings.allocation_contracts import AllocationRequest
from src.core.bookings.booking_mutation_contract import (
    BookingMutation, BookingMutationRejected, NewBookingLineage, apply_booking_mutation,
)
from src.core.bookings.catalog_api import parse_body
from src.core.db.repositories.booking_allocation_repo import AllocationConflict, AllocationDenied, AllocationUnavailable
from src.core.db.repositories.booking_repo import BookingRepository

router = APIRouter(tags=["bookings"])

def owner_mutations_enabled() -> bool:
    return os.getenv('BOOKING_TYPED_OWNER_MUTATIONS_ENABLED', '').strip().lower() == 'true'


class MutationBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    operation: Literal['create', 'edit_contact', 'reschedule', 'cancel', 'reject', 'no_show',
                       'create_stay', 'reschedule_stay', 'confirm', 'complete', 'reactivate']
    nome_cliente: str
    telefono: str
    note: str
    data: str | None = None
    ora: str | None = None
    coperti: int | None = Field(default=None, strict=True, gt=0)
    notice: bool = False
    arrival_date: str | None = None
    departure_date: str | None = None
    expected_stay_revision: int | None = Field(default=None, strict=True, ge=1)


class LineageBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    contact_id: UUID | None
    conversation_id: UUID | None
    source_message_id: UUID | None = None


class OwnerMutationBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    allocation: AllocationRequest
    mutation: MutationBody
    new_booking: LineageBody | None = None


@router.post('/typed-mutations')
async def owner_mutation(request: Request, user: dict = Depends(require_ruolo('owner')),
                         mfa: dict = Depends(require_mfa())):
    if not owner_mutations_enabled():
        raise HTTPException(404, 'Not found')
    body = await parse_body(request, OwnerMutationBody)
    service = getattr(request.app.state, 'booking_service', None)
    repo = getattr(service, 'repo', None)
    if not isinstance(repo, BookingRepository) or repo.pool is None:
        raise HTTPException(503, 'Booking database unavailable')
    try:
        mutation = BookingMutation(**body.mutation.model_dump())
        lineage = NewBookingLineage(**body.new_booking.model_dump()) if body.new_booking is not None else None
        return await apply_booking_mutation(repo.pool, user['organization_id'], user['auth_user_id'],
                                            body.allocation, mutation=mutation, new_booking=lineage)
    except AllocationConflict:
        raise HTTPException(409, 'Booking request conflicts with current state') from None
    except AllocationDenied:
        raise HTTPException(403, 'Booking operation unavailable') from None
    except (BookingMutationRejected, ValueError, TypeError):
        raise HTTPException(422, 'Invalid booking operation') from None
    except (AllocationUnavailable, asyncpg.PostgresError, OSError, TimeoutError):
        raise HTTPException(503, 'Booking operation unavailable; retry the same request') from None
