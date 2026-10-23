import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from src.core.auth.audit import audit_log
from src.core.auth.dependencies import require_ruolo, require_mfa
from src.core.bookings import BookingNotFoundError, SlotPienoError
from src.models.schemas import DisponibilitaSlot, PrenotazioneModificaInput

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bookings", tags=["bookings"])


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
        booking = await service.repo.request_calendar_sync_retry(
            user["organization_id"], booking_id,
        )
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
    except SlotPienoError as e:
        raise HTTPException(status_code=409, detail={"messaggio": str(e), "alternative": e.alternative})
    except BookingNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await _audit_booking(request, user, "prenotazione.confermata", b)
    return b


@router.post("/{booking_id}/reject")
async def reject_booking(booking_id: str, body: dict | None = None, request: Request = None,
                         user: dict = Depends(require_ruolo("owner", "manager"))):
    service = _get_booking_service(request)
    body = body or {}
    try:
        b = await service.reject(user["organization_id"], booking_id, body.get("motivo", ""))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await _audit_booking(request, user, "prenotazione.rifiutata", b)
    return b


@router.post("/{booking_id}/cancel")
async def cancel_booking(booking_id: str, request: Request,
                         user: dict = Depends(require_ruolo("owner", "manager"))):
    service = _get_booking_service(request)
    b = await service.cancel(user["organization_id"], booking_id)
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    await _audit_booking(request, user, "prenotazione.annullata", b)
    return b


@router.post("/{booking_id}/mark-no-show")
async def mark_no_show(booking_id: str, request: Request,
                       user: dict = Depends(require_ruolo("owner", "manager"))):
    service = _get_booking_service(request)
    b = await service.mark_no_show(user["organization_id"], booking_id)
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    await _audit_booking(request, user, "prenotazione.no_show", b)
    return b


@router.post("/{booking_id}/mark-completed")
async def mark_completed(booking_id: str, request: Request,
                         user: dict = Depends(require_ruolo("owner", "manager"))):
    service = _get_booking_service(request)
    try:
        b = await service.mark_completed(user["organization_id"], booking_id)
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
