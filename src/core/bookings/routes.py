import logging
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from src.core.auth.audit import audit_log
from src.core.auth.dependencies import require_ruolo, require_mfa
from src.core.bookings import BookingNotFoundError, SlotPienoError
from src.core.bookings.allocation_contracts import AllocationRequest
from src.core.bookings.booking_mutation_contract import BookingMutation, BookingMutationRejected, apply_booking_mutation
from src.core.db.repositories.booking_allocation_repo import AllocationConflict, AllocationDenied, AllocationUnavailable
from src.core.db.repositories.booking_repo import BookingRepository, ManagedBookingMutationRequired
from src.models.schemas import DisponibilitaSlot, PrenotazioneModificaInput

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bookings", tags=["bookings"])
from src.core.bookings.catalog_api import router as catalog_router
router.include_router(catalog_router)
from src.core.bookings.owner_mutation_api import owner_mutations_enabled, router as owner_mutation_router
router.include_router(owner_mutation_router)


def _get_booking_service(request: Request):
    svc = getattr(request.app.state, "booking_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Booking service not available")
    return svc


async def _audit_booking(request: Request, user: dict, action: str, booking: dict) -> None:
    """Audit best-effort sulle azioni sensibili delle prenotazioni."""
    repo = getattr(request.app.state, "repo", None)
    org_id = user.get("organization_id")
    if repo is None or not org_id:
        return
    try:
        await audit_log(
            repo, org_id, action,
            user_id=user.get("user_id"),
            auth_user_id=user.get("auth_user_id"),
            target_table="bookings",
            target_id=str(booking.get("id")) if booking else None,
            details={"stato": booking.get("stato"), "cliente": booking.get("nome_cliente")},
        )
    except Exception as exc:  # l'audit non deve rompere l'azione
        logger.warning("audit booking fallito: %s", exc)


@router.get("/semaforo", response_model=list[DisponibilitaSlot])
async def semaforo(request: Request, data: str | None = None,
                   user: dict = Depends(require_ruolo("owner", "manager", "staff"))):
    service = _get_booking_service(request)
    org_id = user["organization_id"]
    if data:
        return await service.semaforo_giorno(org_id, data)
    return await service.prossimi_giorni_semaforo(org_id)


@router.get("/settings")
async def get_settings(request: Request,
                       user: dict = Depends(require_ruolo("owner", "manager"))):
    service = _get_booking_service(request)
    return await service.repo.get_booking_settings(user["organization_id"])


@router.put("/settings")
async def update_settings(body: dict, request: Request,
                          user: dict = Depends(require_ruolo("owner", "manager"))):
    if "catalog_v2" in body or any(key.startswith("catalog_") for key in body):
        raise HTTPException(422, "Usa l'editor dedicato per il catalogo")
    service = _get_booking_service(request)
    try:
        return await service.aggiorna_impostazioni(
            user["organization_id"],
            capienze_orarie=body.get("capienze_orarie"),
            config=body.get("config"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
async def list_bookings(request: Request, data: str | None = None,
                        user: dict = Depends(require_ruolo("owner", "manager", "staff"))):
    service = _get_booking_service(request)
    return await service.repo.list_bookings(user["organization_id"], data)


@router.post("/{booking_id}/calendar-sync/retry")
async def retry_calendar_sync(booking_id: str, request: Request,
                              user: dict = Depends(require_ruolo("owner")),
                              mfa: dict = Depends(require_mfa())):
    service = _get_booking_service(request)
    try:
        if getattr(service, "calendar_service", None) is None:
            raise HTTPException(503, "Calendar service unavailable")
        retried = await service.calendar_service.request_booking_retry(user["organization_id"], booking_id)
        booking = await service.repo.get_booking(user["organization_id"], booking_id) if retried else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid booking ID") from exc
    if booking is None:
        raise HTTPException(status_code=409, detail="No retryable calendar sync")
    await _audit_booking(request, user, "calendar_sync_retry", booking)
    return {"status": "pending"}


@router.post("")
async def create_booking(body: dict, request: Request,
                         user: dict = Depends(require_ruolo("owner", "manager"))):
    service = _get_booking_service(request)
    try:
        b = await service.create_booking(
            org_id=user["organization_id"],
            nome_cliente=body.get("nome_cliente"),
            telefono=body.get("telefono", ""),
            data=body.get("data"),
            ora=body.get("ora"),
            coperti=body.get("coperti"),
            note=body.get("note", ""),
            tipo_evento=body.get("tipo_evento", ""),
            origine=body.get("origine", "Dashboard"),
        )
    except SlotPienoError as e:
        raise HTTPException(status_code=409, detail={
            "messaggio": str(e),
            "alternative": e.alternative,
        })
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await _audit_booking(request, user, "prenotazione.creata_manualmente", b)
    return b


@router.get("/{booking_id}")
async def get_booking(booking_id: str, request: Request,
                      user: dict = Depends(require_ruolo("owner", "manager", "staff"))):
    service = _get_booking_service(request)
    b = await service.repo.get_booking(user["organization_id"], booking_id)
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    return b


@router.put("/{booking_id}")
async def update_booking(booking_id: str, body: PrenotazioneModificaInput,
                         request: Request,
                         user: dict = Depends(require_ruolo("owner", "manager"))):
    service = _get_booking_service(request)
    try:
        updated = await service.update_booking(
            user["organization_id"], booking_id,
            **body.model_dump(exclude_unset=True),
        )
    except ManagedBookingMutationRequired:
        raise HTTPException(409, "Managed booking requires a typed mutation") from None
    except SlotPienoError as e:
        raise HTTPException(status_code=409, detail={
            "messaggio": str(e),
            "alternative": e.alternative,
        })
    except BookingNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await _audit_booking(request, user, "prenotazione.modificata", updated)
    return updated


@router.post("/{booking_id}/confirm")
async def confirm_booking(booking_id: str, request: Request,
                          user: dict = Depends(require_ruolo("owner", "manager"))):
    service = _get_booking_service(request)
    try:
        b = await service.confirm(user["organization_id"], booking_id)
    except ManagedBookingMutationRequired:
        raise HTTPException(409, "Managed booking requires a typed mutation") from None
    except SlotPienoError as e:
        raise HTTPException(status_code=409, detail={"messaggio": str(e), "alternative": e.alternative})
    except BookingNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await _audit_booking(request, user, "prenotazione.confermata", b)
    return b


@router.post("/{booking_id}/reject")
async def reject_booking(
    booking_id: str, body: dict | None = None, request: Request = None,
    user: dict = Depends(require_ruolo("owner", "manager")),
    idempotency_key: str | None = Header(default=None),
    x_booking_allocation_revision: str | None = Header(default=None),
    x_booking_catalog_revision: str | None = Header(default=None),
):
    service = _get_booking_service(request)
    if any(value is not None for value in (idempotency_key, x_booking_allocation_revision, x_booking_catalog_revision)):
        if body:
            raise HTTPException(422, "Managed rejection does not support a reason")
        b, replayed = await _mark_managed_booking(
            service, user, booking_id, idempotency_key,
            x_booking_allocation_revision, x_booking_catalog_revision, "reject",
        )
    else:
        replayed = False
        try:
            b = await service.reject(user["organization_id"], booking_id, (body or {}).get("motivo", ""))
        except ManagedBookingMutationRequired:
            raise HTTPException(409, "Managed booking requires a typed mutation") from None
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    if not replayed:
        await _audit_booking(request, user, "prenotazione.rifiutata", b)
    return b


@router.post("/{booking_id}/cancel")
async def cancel_booking(
    booking_id: str, request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
    idempotency_key: str | None = Header(default=None),
    x_booking_allocation_revision: str | None = Header(default=None),
    x_booking_catalog_revision: str | None = Header(default=None),
):
    service = _get_booking_service(request)
    if any(value is not None for value in (
        idempotency_key, x_booking_allocation_revision, x_booking_catalog_revision,
    )):
        b, replayed = await _mark_managed_booking(
            service, user, booking_id, idempotency_key,
            x_booking_allocation_revision, x_booking_catalog_revision, "cancel",
        )
    else:
        replayed = False
        try:
            b = await service.cancel(user["organization_id"], booking_id)
        except ManagedBookingMutationRequired:
            raise HTTPException(409, "Managed booking requires an idempotency key and both allocation revisions") from None
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    if not replayed:
        await _audit_booking(request, user, "prenotazione.annullata", b)
    return b


async def _mark_managed_booking(service, user, booking_id, key, allocation_revision, catalog_revision, operation):
    if not owner_mutations_enabled():
        raise HTTPException(409, "Managed booking mutations are unavailable")
    if user.get("ruolo") != "owner":
        raise HTTPException(403, "Owner required for a managed booking")
    await require_mfa()(user)
    repo = service.repo
    if not isinstance(repo, BookingRepository) or repo.pool is None:
        raise HTTPException(503, "Booking database unavailable")
    try:
        if not all(
            value and len(value) <= 19 and value.isascii() and value.isdecimal()
            and 0 < int(value) <= 2**63 - 1
            for value in (allocation_revision, catalog_revision)
        ):
            raise ValueError("Invalid revision")
        if not isinstance(key, str):
            raise ValueError("Invalid idempotency key")
        command = AllocationRequest(
            request_id=UUID(key), booking_id=UUID(booking_id), action="cancel",
            expected_allocation_revision=int(allocation_revision),
            expected_catalog_revision=int(catalog_revision),
        )
        result = await apply_booking_mutation(
            repo.pool, user["organization_id"], user["auth_user_id"], command,
            mutation=BookingMutation(operation, "", "", ""),
        )
        return await repo.get_booking(user["organization_id"], command.booking_id), result["replayed"]
    except AllocationConflict:
        raise HTTPException(409, "Booking request conflicts with current state") from None
    except AllocationDenied:
        raise HTTPException(403, "Booking operation unavailable") from None
    except (BookingMutationRejected, ValueError, TypeError):
        raise HTTPException(422, "Invalid booking mutation key or revision") from None
    except (AllocationUnavailable, asyncpg.PostgresError, OSError, TimeoutError):
        raise HTTPException(503, "Booking operation unavailable; retry the same request") from None


@router.post("/{booking_id}/mark-no-show")
async def mark_no_show(
    booking_id: str, request: Request,
    user: dict = Depends(require_ruolo("owner", "manager")),
    idempotency_key: str | None = Header(default=None),
    x_booking_allocation_revision: str | None = Header(default=None),
    x_booking_catalog_revision: str | None = Header(default=None),
):
    service = _get_booking_service(request)
    if any(value is not None for value in (
        idempotency_key, x_booking_allocation_revision, x_booking_catalog_revision,
    )):
        booking, replayed = await _mark_managed_booking(
            service, user, booking_id, idempotency_key,
            x_booking_allocation_revision, x_booking_catalog_revision, "no_show",
        )
    else:
        replayed = False
        try:
            booking = await service.mark_no_show(user["organization_id"], booking_id)
        except ManagedBookingMutationRequired:
            raise HTTPException(409, "Managed booking requires an idempotency key and both allocation revisions") from None
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if not replayed:
        await _audit_booking(request, user, "prenotazione.no_show", booking)
    return booking


@router.post("/{booking_id}/mark-completed")
async def mark_completed(booking_id: str, request: Request,
                         user: dict = Depends(require_ruolo("owner", "manager"))):
    service = _get_booking_service(request)
    try:
        b = await service.mark_completed(user["organization_id"], booking_id)
    except ManagedBookingMutationRequired:
        raise HTTPException(409, "Managed booking requires a typed mutation") from None
    except SlotPienoError as e:
        raise HTTPException(status_code=409, detail={"messaggio": str(e), "alternative": e.alternative})
    except BookingNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    await _audit_booking(request, user, "prenotazione.completata", b)
    return b
