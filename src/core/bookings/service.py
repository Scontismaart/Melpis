from contextlib import nullcontext
from datetime import datetime, date, time, timedelta, timezone
import logging
import unicodedata

from src.models.schemas import DisponibilitaSlot

logger = logging.getLogger(__name__)

STATI_OCCUPATI = {"in_attesa", "confermata", "da_verificare", "completata"}
STATI_LIBERI = {"cancellata", "cancellato", "rifiutata", "no_show"}
SLOT_ORE = [f"{h:02d}:00" for h in range(24)]
# Stessa durata evento usata dal push Google (calendar/service.py).
DEFAULT_SLOT_MINUTES = 60


class SlotPienoError(ValueError):
    def __init__(self, message, alternative=None):
        super().__init__(message)
        self.alternative = alternative or []


class BookingNotFoundError(ValueError):
    pass


class BookingService:
    def __init__(
        self,
        repo=None,
        whatsapp_service=None,
        app_config=None,
        calendar_service=None,
        booking_repo=None,
        org_repo=None,
        booking_router=None,
    ):
        target_repo = booking_repo or repo
        from src.core.db.repositories.booking_repo import BookingRepository
        if hasattr(target_repo, "booking_repo") and isinstance(getattr(target_repo, "booking_repo", None), BookingRepository):
            target_repo = target_repo.booking_repo
        self.repo = target_repo
        self.booking_repo = target_repo
        self.org_repo = org_repo or getattr(repo, "org_repo", None)
        self.whatsapp = whatsapp_service
        self.app_config = app_config
        self.calendar_service = calendar_service
        self.booking_router = booking_router

    def _slot_lock(self, org_id, data, ora):
        """Lock consultivo per fascia oraria se il repo lo supporta
        (CoreRepository); fallback no-op per repo demo/fake nei test."""
        lock_fn = getattr(self.repo, "slot_lock", None)
        if callable(lock_fn):
            ctx = lock_fn(org_id, data, ora)
            if hasattr(ctx, "__aenter__") or hasattr(ctx, "__enter__"):
                return ctx
        return nullcontext()

    async def _google_slot_occupato(self, org_id, data, ora) -> bool:
        """True se un evento Google Calendar copre lo slot [ora, ora+60min).
        Best-effort e fail-open: senza calendar service, su errore o con
        metodo assente (repo fake nei test) la risposta è False — la
        capacità DB resta la fonte autorevole della disponibilità."""
        if not self.calendar_service:
            return False
        if not hasattr(self.calendar_service, "get_busy_intervals"):
            return False
        try:
            busy = await self.calendar_service.get_busy_intervals(org_id, data)
        except Exception:
            logger.warning("calendar=freebusy_check_fail org_id=%s data=%s", org_id, data)
            return False
        ora_str = ora if isinstance(ora, str) else ora.strftime("%H:%M")
        slot_start = datetime.fromisoformat(f"{data}T{ora_str}:00")
        slot_end = slot_start + timedelta(minutes=DEFAULT_SLOT_MINUTES)
        try:
            for bs, be in busy:
                if slot_start < be and bs < slot_end:
                    return True
        except TypeError:
            # Intervallo con tzinfo inatteso da un'implementazione del
            # servizio calendar: fail-open, non bloccare la prenotazione.
            logger.warning("calendar=freebusy_interval_type org_id=%s", org_id)
            return False
        return False

    # ── Disponibilità ──────────────────────────────────────────

    async def _coperti_prenotati(self, org_id, data, ora, exclude_booking_id=None):
        bookings = await self.repo.list_bookings(org_id, data)
        fascia = f"{int(ora[:2]):02d}:00"
        occupati = 0
        for b in bookings:
            if exclude_booking_id is not None and str(b["id"]) == str(exclude_booking_id):
                continue
            if b["stato"] in STATI_LIBERI:
                continue
            ora_b = b["ora"]
            if isinstance(ora_b, time):
                ora_b = ora_b.strftime("%H:%M")
            b_fascia = f"{int(ora_b[:2]):02d}:00"
            if b_fascia == fascia:
                occupati += (b["coperti"] or 0)
        return occupati

    async def _get_capienze(self, org_id):
        settings = await self.repo.get_booking_settings(org_id)
        if settings and settings.get("capienze_orarie"):
            return settings["capienze_orarie"]
        return {f: 40 for f in SLOT_ORE}

    async def verifica_disponibilita(self, org_id, data, ora, coperti=None,
                                     exclude_booking_id=None):
        prenotati = await self._coperti_prenotati(
            org_id, data, ora, exclude_booking_id=exclude_booking_id
        )
        fascia = f"{int(ora[:2]):02d}:00"
        capienze = await self._get_capienze(org_id)
        massimi = capienze.get(fascia, 40)
        liberi = max(massimi - prenotati, 0)
        alternative = []
        if coperti and coperti > liberi:
            alternative = [
                f for f in SLOT_ORE
                if f != fascia and (capienze.get(f, 0) - await self._coperti_prenotati(
                    org_id, data, f, exclude_booking_id=exclude_booking_id
                )) >= coperti
            ][:2]
        if liberi <= 0:
            stato = "rosso"
        elif liberi <= max(4, round(massimi * 0.2)):
            stato = "giallo"
        else:
            stato = "verde"
        return DisponibilitaSlot(
            data=data, ora=ora,
            coperti_massimi=massimi, coperti_prenotati=prenotati,
            coperti_liberi=liberi, stato=stato, alternative=alternative,
        )

    async def semaforo_giorno(self, org_id, data):
        capienze = await self._get_capienze(org_id)
        bookings = await self.repo.list_bookings(org_id, data)
        slots = []
        for fascia in SLOT_ORE:
            if capienze.get(fascia, 0) <= 0:
                continue
            prenotati = 0
            for b in bookings:
                if b["stato"] in STATI_LIBERI:
                    continue
                ora_b = b["ora"]
                if isinstance(ora_b, time):
                    ora_b = ora_b.strftime("%H:%M")
                b_fascia = f"{int(ora_b[:2]):02d}:00"
                if b_fascia == fascia:
                    prenotati += (b["coperti"] or 0)
            massimi = capienze[fascia]
            liberi = max(massimi - prenotati, 0)
            if liberi <= 0:
                stato = "rosso"
            elif liberi <= max(4, round(massimi * 0.2)):
                stato = "giallo"
            else:
                stato = "verde"
            slots.append(DisponibilitaSlot(
                data=data, ora=fascia,
                coperti_massimi=massimi, coperti_prenotati=prenotati,
                coperti_liberi=liberi, stato=stato, alternative=[],
            ))
        return slots

    async def prossimi_giorni_semaforo(self, org_id, giorni=7):
        from datetime import timedelta
        oggi = datetime.now().date()
        slots = []
        for offset in range(giorni):
            giorno = (oggi + timedelta(days=offset)).strftime("%Y-%m-%d")
            slots.extend(await self.semaforo_giorno(org_id, giorno))
        return slots

    # ── Creazione ──────────────────────────────────────────────

    async def create_booking(self, org_id=None, nome_cliente="", data=None, ora=None, coperti=1,
                              telefono="", note="", tipo_evento="", origine="Dashboard",
                              richiede_intervento=False, id_conversazione="", source_message_id=None,
                              organization_id=None, verticale=None, external_service_id=None):
        org_id = org_id or organization_id
        # Replay must resolve persisted success before checking a now-full slot.
        lookup = getattr(self.repo, "get_booking_for_message", None)
        if source_message_id and lookup:
            existing = await lookup(org_id, source_message_id)
            if isinstance(existing, dict) and existing.get("id"):
                return existing
        values = self._validated_booking_values(
            nome_cliente, telefono, data, ora, coperti, note
        )
        nome_cliente = values["nome_cliente"]
        telefono = values["telefono"]
        data = values["data"]
        ora = values["ora"]
        coperti = values["coperti"]
        note = values["note"]
        # Eventi esterni su Google Calendar (creati fuori dal sistema) bloccano
        # lo slot come quelli interni. Check pre-lock: chiamata di rete fuori
        # dalla sezione critica. Fail-open documentato in _google_slot_occupato.
        if await self._google_slot_occupato(org_id, data, ora):
            disp = await self.verifica_disponibilita(org_id, data, ora, coperti)
            raise SlotPienoError(
                f"slot occupato su Google Calendar alle {ora}",
                alternative=disp.alternative,
            )
        # Check-then-insert sotto lock di fascia: senza serializzazione due
        # richieste concorrenti possono superare entrambe la verifica di
        # capienza e overbookare lo slot.
        async with self._slot_lock(org_id, data, ora):
            if source_message_id and lookup:
                existing = await lookup(org_id, source_message_id)
                if isinstance(existing, dict) and existing.get("id"):
                    return existing
            disp = await self.verifica_disponibilita(org_id, data, ora, coperti)
            if coperti > disp.coperti_liberi:
                raise SlotPienoError(
                    f"slot pieno per {coperti} coperti alle {ora}",
                    alternative=disp.alternative,
                )
            richiede_dep = await self._valuta_richiede_deposito(
                org_id, coperti=coperti, tipo_evento=tipo_evento, ora=ora, data=data
            )
            booking = await self.repo.create_booking(
                organization_id=org_id, nome_cliente=nome_cliente,
                telefono=telefono, data=data, ora=ora, coperti=coperti,
                note=note, tipo_evento=tipo_evento, stato="in_attesa", origine=origine,
                richiede_deposito=richiede_dep,
                richiede_intervento=richiede_intervento,
                id_conversazione=id_conversazione or None,
                source_message_id=source_message_id,
            )
        if self.calendar_service:
            try:
                await self.calendar_service.sync_booking_state(booking, org_id)
            except Exception:
                logger.error("calendar=sync_fail create_booking id=%s", booking.get("id"))
                booking["external_sync_status"] = "failed"
                booking["richiede_intervento"] = True

        # Sincronizzazione gestionale esterno tramite BookingAdapterRouter (Send-Then-Mark)
        if self.booking_router and org_id:
            try:
                from src.core.bookings.ports.base import CreateBookingRequest, CustomerResult
                from datetime import date as date_cls, time as time_cls
                d_val = date_cls.fromisoformat(data) if isinstance(data, str) else data
                if isinstance(ora, str):
                    h, m = ora.split(":")[:2]
                    t_val = time_cls(int(h), int(m))
                else:
                    t_val = ora
                resolved_verticale = verticale
                if resolved_verticale is None and self.org_repo:
                    try:
                        prof = await self.org_repo.get_org_business_profile(org_id)
                        if prof:
                            resolved_verticale = prof.get("verticale")
                    except Exception as e:
                        logger.warning("Recupero profilo per verticale fallito (fail-closed applicato): %s", e)

                if source_message_id:
                    idemp_key = f"ext-book:{org_id}:{source_message_id}"
                else:
                    # Senza source_message_id la chiave NON deve degenerare in un
                    # id effimero per riga (ucciderebbe il replay cross-retry):
                    # fallback deterministico sugli attributi del booking.
                    import hashlib

                    raw = "|".join([
                        str(org_id), str(d_val), str(t_val),
                        str(telefono or ""), str(nome_cliente or ""),
                        str(coperti),
                    ])
                    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
                    idemp_key = f"ext-book:{org_id}:attr-{digest}"
                req = CreateBookingRequest(
                    idempotency_key=idemp_key,
                    customer=CustomerResult(
                        customer_id=str(booking.get("id", "")),
                        nome=nome_cliente,
                        telefono=telefono,
                    ),
                    data=d_val,
                    ora_inizio=t_val,
                    coperti=coperti,
                    note=note,
                    origine=origine,
                    source_message_id=source_message_id,
                    internal_booking_id=booking.get("id"),
                    service_id=external_service_id,
                )
                ext_res = await self.booking_router.dispatch_create_booking(
                    org_id=org_id,
                    req=req,
                    verticale=resolved_verticale,
                )
                if not ext_res.success and ext_res.sync_status == "failed":
                    logger.warning(
                        "External booking sync failed for booking %s",
                        booking.get("id")
                    )
                    booking["external_sync_status"] = "failed"
                    booking["richiede_intervento"] = True
                else:
                    if booking.get("external_sync_status") != "failed":
                        booking["external_sync_status"] = ext_res.sync_status
                    booking["external_booking_id"] = ext_res.external_booking_id
            except Exception as e:
                logger.error("booking_router dispatch failed for booking %s error_type=%s", booking.get("id"), type(e).__name__)
                booking["external_sync_status"] = "failed"
                booking["richiede_intervento"] = True

        if booking.get("richiede_intervento"):
            await self.repo.mark_booking_requires_intervention(org_id, booking["id"])
        return booking

    @staticmethod
    def _validated_booking_values(nome_cliente, telefono, data, ora, coperti, note):
        if not isinstance(nome_cliente, str) or not nome_cliente.strip():
            raise ValueError("nome_cliente obbligatorio")
        if not isinstance(telefono, str):
            raise ValueError("telefono non valido")
        if not isinstance(note, str):
            raise ValueError("note non valide")
        if not isinstance(data, str):
            raise ValueError("data non valida")
        try:
            parsed_date = date.fromisoformat(data)
        except ValueError as exc:
            raise ValueError("data non valida: usare YYYY-MM-DD") from exc
        if not isinstance(ora, str):
            raise ValueError("ora non valida")
        try:
            ore, minuti = ora.split(":")
            parsed_ora = time(int(ore), int(minuti))
        except (ValueError, TypeError) as exc:
            raise ValueError("ora non valida: usare HH:MM") from exc
        if parsed_ora.second or parsed_ora.microsecond:
            raise ValueError("ora non valida: usare HH:MM")
        if not isinstance(coperti, int) or isinstance(coperti, bool) or coperti <= 0:
            raise ValueError("coperti deve essere un intero positivo")
        return {
            "nome_cliente": nome_cliente.strip(),
            "telefono": telefono.strip(),
            "data": parsed_date.isoformat(),
            "ora": f"{parsed_ora.hour:02d}:{parsed_ora.minute:02d}",
            "coperti": coperti,
            "note": note.strip(),
        }

    async def update_booking(self, org_id, booking_id, **changes):
        current = await self._get_booking_or_raise(org_id, booking_id)
        values = {
            "nome_cliente": changes.get("nome_cliente", current.get("nome_cliente", "")),
            "telefono": changes.get("telefono", current.get("telefono", "")),
            "data": changes.get("data", self._format_date(current.get("data"))),
            "ora": changes.get("ora", self._format_time(current.get("ora"))),
            "coperti": changes.get("coperti", current.get("coperti")),
            "note": changes.get("note", current.get("note", "")),
        }
        values = self._validated_booking_values(**values)
        schedule_changed = any(
            values[key] != self._format_booking_value(key, current.get(key))
            for key in ("data", "ora", "coperti")
        )
        new_status = "in_attesa" if schedule_changed else current["stato"]
        # Check Google PRIMA del lock: chiamata di rete fuori dalla sezione
        # critica (come in create_booking). Piccola finestra TOCTOU con
        # eventi Google aggiunti nel frattempo: stesso livello best-effort.
        if schedule_changed and await self._google_slot_occupato(
            org_id, values["data"], values["ora"]
        ):
            raise SlotPienoError(
                f"slot occupato su Google Calendar alle {values['ora']}",
            )
        # Stessa serializzazione di create_booking: la verifica di capienza e
        # l'UPDATE devono restare atomiche rispetto ad altre prenotazioni
        # concorrenti sulla fascia di destinazione.
        async with self._slot_lock(org_id, values["data"], values["ora"]):
            latest = await self._get_booking_or_raise(org_id, booking_id)
            if any(latest.get(key) != current.get(key) for key in ("stato", "data", "ora", "coperti", "updated_at")):
                raise ValueError("prenotazione modificata durante l'aggiornamento")
            if schedule_changed:
                disp = await self.verifica_disponibilita(
                    org_id, values["data"], values["ora"], values["coperti"],
                    exclude_booking_id=booking_id,
                )
                if values["coperti"] > disp.coperti_liberi:
                    raise SlotPienoError(
                        f"slot pieno per {values['coperti']} coperti alle {values['ora']}",
                        alternative=disp.alternative,
                    )
            updated = await self.repo.update_booking_details(
                org_id, booking_id, stato=new_status, expected=latest, **values
            )
        if not updated:
            raise ValueError(f"booking {booking_id} non trovato")
        if schedule_changed and self.whatsapp:
            try:
                sent = await self.send_booking_reconfirmation(org_id, updated)
                if not sent:
                    raise RuntimeError("notifica WhatsApp di riconferma non inviata")
            except Exception:
                logger.exception("booking=reconfirmation_failed id=%s", booking_id)
                old_day = self._format_date(current.get("data"))
                old_hour = self._format_time(current.get("ora"))
                async with self._slot_lock(org_id, old_day, old_hour):
                    still_current = await self.repo.get_booking(org_id, booking_id)
                    if still_current and all(still_current.get(key) == updated.get(key) for key in ("stato", "data", "ora", "coperti", "updated_at")):
                        available = True
                        if current["stato"] not in STATI_LIBERI:
                            disp = await self.verifica_disponibilita(
                                org_id, old_day, old_hour, current["coperti"], exclude_booking_id=booking_id)
                            available = current["coperti"] <= disp.coperti_liberi
                        if available:
                            await self.repo.update_booking_details(
                                org_id, booking_id, stato=current["stato"],
                                nome_cliente=current.get("nome_cliente", ""),
                                telefono=current.get("telefono", ""),
                                data=old_day, ora=old_hour,
                                coperti=current.get("coperti"), note=current.get("note", ""),
                                expected=still_current,
                            )
                        else:
                            await self.repo.mark_booking_requires_intervention(org_id, booking_id, expected=still_current)
                raise
        if self.calendar_service:
            try:
                await self.calendar_service.sync_booking_state(updated, org_id)
            except Exception:
                logger.exception("calendar=sync_fail update_booking id=%s", booking_id)
        return updated

    @staticmethod
    def _format_date(value):
        return value.isoformat() if isinstance(value, date) else str(value)

    @staticmethod
    def _format_time(value):
        return value.strftime("%H:%M") if isinstance(value, time) else str(value)[:5]

    @classmethod
    def _format_booking_value(cls, key, value):
        if key == "data":
            return cls._format_date(value)
        if key == "ora":
            return cls._format_time(value)
        return value

    async def _get_booking_or_raise(self, org_id, booking_id):
        b = await self.repo.get_booking(org_id, booking_id)
        if not b:
            raise BookingNotFoundError(f"booking {booking_id} non trovato")
        return b

    # ── WhatsApp ───────────────────────────────────────────────

    async def _load_tenant_config(self, org_id):
        if not self.app_config or not self.whatsapp:
            return None
        from src.whatsapp.config import load_tenant_config
        return await load_tenant_config(org_id, self.app_config, self.whatsapp.repo)

    async def _send_whatsapp(self, org_id, to_number, text, category="service",
                             idempotency_key=None, raise_on_error=False):
        if not self.whatsapp or not to_number:
            return False
        tenant = await self._load_tenant_config(org_id)
        if not tenant:
            return False
        try:
            await self.whatsapp.send_whatsapp_message(
                org_id=org_id, to_number=to_number,
                payload={"to": to_number, "type": "text", "text": {"body": text}},
                category=category, meta_client=None, tenant_config=tenant,
                idempotency_key=idempotency_key,
            )
            return True
        except Exception as e:
            logger.error("WhatsApp send failed for org %s: %s", org_id, type(e).__name__)
            if raise_on_error:
                raise
            return False

    async def send_booking_reconfirmation(self, org_id, booking):
        booking_id = str(booking["id"])
        data = self._format_date(booking["data"])
        ora = self._format_time(booking["ora"])
        text = (
            f"Ciao {booking['nome_cliente']}, abbiamo ricevuto una modifica alla tua "
            f"prenotazione del {data} alle {ora} per {booking['coperti']} persone. "
            "Per confermarla, rispondi SI."
        )
        key = f"booking-reconfirmation:{booking_id}:{data}:{ora}:{booking['coperti']}"
        return await self._send_whatsapp(
            org_id, booking.get("telefono", ""), text,
            idempotency_key=key, raise_on_error=True,
        )

    # ── Lifecycle ──────────────────────────────────────────────

    async def _transition_to_occupied(self, org_id, booking_id, stato):
        """Atomically admit a reactivated reservation without charging it twice."""
        b = await self._get_booking_or_raise(org_id, booking_id)
        async with self._slot_lock(org_id, self._format_date(b["data"]), self._format_time(b["ora"])):
            latest = await self._get_booking_or_raise(org_id, booking_id)
            if any(latest.get(key) != b.get(key) for key in ("stato", "data", "ora", "coperti", "updated_at")):
                raise ValueError("prenotazione modificata durante l'operazione")
            if latest["stato"] in STATI_LIBERI:
                disp = await self.verifica_disponibilita(
                    org_id, self._format_date(latest["data"]), self._format_time(latest["ora"]),
                    latest["coperti"], exclude_booking_id=booking_id)
                if latest["coperti"] > disp.coperti_liberi:
                    raise SlotPienoError(f"slot pieno per {latest['coperti']} coperti alle {latest['ora']}", disp.alternative)
            if stato == "completata":
                updated = await self.repo.mark_booking_completed(org_id, booking_id, latest)
            else:
                updated = await self.repo.update_booking_status(
                    org_id, booking_id, stato, expected_status=latest["stato"], expected=latest)
            if not updated:
                raise ValueError("prenotazione modificata durante l'operazione")
        return latest, updated

    async def confirm(self, org_id, booking_id):
        b, updated = await self._transition_to_occupied(org_id, booking_id, "confermata")
        msg = f"La tua prenotazione del {b['data']} alle {b['ora']} per {b['coperti']} persone e' confermata!"
        await self._send_whatsapp(org_id, b["telefono"], msg)
        if b.get("richiede_deposito"):
            cfg = await self._get_deposito_config(org_id)
            importo = (cfg or {}).get("importo_default", 10.0)
            valuta = (cfg or {}).get("valuta", "EUR")
            try:
                link = await self._genera_payment_link(org_id, booking_id, importo, valuta)
                async with self.repo.pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE bookings SET payment_link = $3,
                            payment_link_created_at = NOW(), payment_status = 'pending'
                        WHERE organization_id = $1 AND id = $2
                    """, org_id, booking_id, link)
                updated["payment_link"] = link
                updated["payment_status"] = "pending"
                await self._send_whatsapp(org_id, b["telefono"],
                    f"Per confermare, versa il deposito di {valuta} {importo:.2f}: {link}",
                    idempotency_key=f"booking-deposit:{org_id}:{booking_id}:{valuta.lower()}:{importo}",
                )
            except Exception as e:
                logger.error("Failed to generate payment link for booking %s: %s", booking_id, e)
        if self.calendar_service:
            try:
                await self.calendar_service.sync_booking_state(updated, org_id)
            except Exception:
                logger.exception("calendar=sync_fail confirm id=%s", booking_id)
        return updated

    async def reject(self, org_id, booking_id, motivo=""):
        b = await self._get_booking_or_raise(org_id, booking_id)
        updated = await self.repo.update_booking_status(org_id, booking_id, "rifiutata")
        msg = f"La tua prenotazione del {b['data']} alle {b['ora']} non puo' essere confermata."
        if motivo:
            msg += f" Motivo: {motivo}"
        await self._send_whatsapp(org_id, b["telefono"], msg)
        if self.calendar_service:
            try:
                await self.calendar_service.sync_booking_state(updated, org_id)
            except Exception:
                logger.exception("calendar=sync_fail reject id=%s", booking_id)
        return updated

    async def cancel(self, org_id, booking_id):
        booking = await self.repo.update_booking_status(org_id, booking_id, "cancellata")
        if self.booking_router and org_id and booking and booking.get("external_booking_id"):
            try:
                from src.core.bookings.ports.base import CancelBookingRequest
                cancel_req = CancelBookingRequest(
                    idempotency_key=f"ext-cancel:{org_id}:{booking_id}",
                    external_booking_id=str(booking["external_booking_id"]),
                )
                await self.booking_router.dispatch_cancel_booking(org_id, cancel_req)
            except Exception as e:
                logger.error("External cancel failed for booking %s: %s", booking_id, e)
        if self.calendar_service:
            try:
                await self.calendar_service.sync_booking_state(booking, org_id)
            except Exception:
                logger.exception("calendar=sync_fail cancel id=%s", booking_id)
        return booking

    async def mark_no_show(self, org_id, booking_id):
        async with self.repo.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE bookings SET stato = 'no_show', no_show_at = NOW(), updated_at = NOW()
                WHERE organization_id = $1 AND id = $2
                RETURNING *
            """, org_id, booking_id)
            booking = dict(row) if row else None
        if booking and self.calendar_service:
            try:
                await self.calendar_service.sync_booking_state(booking, org_id)
            except Exception:
                logger.exception("calendar=sync_fail mark_no_show id=%s", booking_id)
        return booking

    async def mark_completed(self, org_id, booking_id):
        _, booking = await self._transition_to_occupied(org_id, booking_id, "completata")
        if booking and self.calendar_service:
            try:
                await self.calendar_service.sync_booking_state(booking, org_id)
            except Exception:
                logger.exception("calendar=sync_fail mark_completed id=%s", booking_id)
        return booking

    async def aggiorna_impostazioni(self, org_id, capienze_orarie=None,
                                     coperti_massimi=40, fasce_orarie=None, config=None):
        from src.core.bookings.auto_policy import validate_auto_booking_configuration
        if not hasattr(self.repo, "booking_settings_lock"):
            from src.core.bookings.memory_repo import InMemoryBookingRepo
            if not isinstance(self.repo, InMemoryBookingRepo):
                raise RuntimeError("Booking settings require transactional repository")
            if isinstance(config, dict) and isinstance(config.get("a05"), dict) and config["a05"].get("enabled"):
                raise ValueError("A05 requires transactional persistence")
            current = await self.repo.get_booking_settings(org_id) or {}
            merged = dict(current.get("config") or {})
            merged.update(config or {})
            if capienze_orarie is not None or fasce_orarie is not None:
                await self.repo.upsert_booking_settings(
                    org_id, fasce_orarie=fasce_orarie or current.get("fasce_orarie") or SLOT_ORE,
                    capienze_orarie=capienze_orarie or current.get("capienze_orarie")
                        or {slot: coperti_massimi for slot in SLOT_ORE},
                )
            return await self.repo.upsert_booking_settings_config(org_id, merged)
        async with self.repo.booking_settings_lock(org_id) as organization:
            current = await self.repo.get_booking_settings(org_id) or {}
            merged = dict(current.get("config") or {})
            if config is not None:
                if not isinstance(config, dict):
                    raise ValueError("Invalid booking configuration")
                merged.update(config)
            capacities = (capienze_orarie if capienze_orarie is not None
                          else current.get("capienze_orarie") or {})
            intervals = (fasce_orarie if fasce_orarie is not None
                         else current.get("fasce_orarie") or SLOT_ORE)
            profile = organization.get("business_profile") or {}
            if isinstance(profile, str):
                import json
                profile = json.loads(profile)
            decision = validate_auto_booking_configuration(
                {"config": merged, "capienze_orarie": capacities},
                organization.get("timezone"), profile.get("verticale"),
            )
            if not decision.valid:
                raise ValueError(f"Invalid A05 booking policy: {decision.reason.value}")
            return await self.repo.save_booking_settings_locked(
                org_id, slot_minutes=current.get("slot_minutes") or 60,
                fasce_orarie=intervals, capienze_orarie=capacities, config=merged,
            )

    async def process_pending_calendar_sync(self, limit=10):
        """Best-effort optional sync; internal booking stays authoritative."""
        if not self.calendar_service or not hasattr(self.repo, "claim_calendar_sync"):
            return 0
        claimed = await self.repo.claim_calendar_sync(limit)
        for item in claimed:
            org_id = item["organization_id"]
            event_id = None
            error_code = "provider_not_confirmed"
            try:
                booking = await self.repo.get_booking(org_id, item["internal_booking_id"])
                if booking and booking["stato"] == "confermata":
                    event_id = await self.calendar_service.create_event(booking, org_id)
                    # A cancellation may race the network call. Reconcile
                    # against the current local authority after the call.
                    current = await self.repo.get_booking(org_id, item["internal_booking_id"])
                    if current and current["stato"] != "confermata" and event_id:
                        await self.calendar_service.delete_event(current, org_id)
                        event_id = None
            except Exception as exc:
                error_code = "provider_exception"
                logger.warning(
                    "calendar=optional_sync_retry org_id=%s booking_id=%s error_type=%s",
                    org_id, item["internal_booking_id"], type(exc).__name__,
                )
            await self.repo.finish_calendar_sync(
                org_id, item["id"], item["claim_number"],
                event_id=event_id, success=event_id is not None,
                error_code=error_code,
            )
        return len(claimed)

    # ── Reminder ──────────────────────────────────────────────

    REMINDER_CONFIRM_KEYWORDS = {"si", "yes"}
    REMINDER_REJECT_KEYWORDS = {"no"}

    async def handle_reminder_reply(self, org_id, from_number, text):
        # Replies are accepted only as complete, explicit yes/no messages.
        normalized = unicodedata.normalize("NFKD", text or "").casefold()
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        # A question mark changes a yes/no into a question; never authorize a
        # state transition from "si?" or "no?".
        normalized = normalized.strip().strip(".,!;:")
        confirmed = normalized in self.REMINDER_CONFIRM_KEYWORDS
        rejected = normalized in self.REMINDER_REJECT_KEYWORDS
        phone = (from_number or "").strip()
        repo = getattr(self.repo, "booking_repo", self.repo)
        pending = await repo.list_pending_reminders_for_phone(org_id, phone)
        if not pending:
            # A duplicate delivery after the conditional update is an idempotent replay.
            if confirmed or rejected:
                status = "rejected" if rejected else "confirmed"
                prior = await repo.list_reminder_replies_for_phone(org_id, phone, status)
                if prior:
                    await self._send_reminder_result(org_id, phone, prior[0]["id"], status)
                    return status
            return None
        if len(pending) != 1:
            await self._send_whatsapp(
                org_id, phone,
                "Non riesco a verificare automaticamente la prenotazione. Un operatore ti ricontattera' per aiutarti.",
                idempotency_key=self._reminder_handoff_key(org_id, phone, pending),
            )
            return "handoff"

        if not confirmed and not rejected:
            await self._send_whatsapp(
                org_id, phone,
                "Non riesco a verificare automaticamente la prenotazione. Un operatore ti ricontattera' per aiutarti.",
                idempotency_key=self._reminder_handoff_key(org_id, phone, pending),
            )
            return "handoff"

        b = pending[0]
        status = "rejected" if rejected else "confirmed"
        updated = await repo.apply_reminder_reply(
            org_id, b["id"], status, datetime.now(timezone.utc)
        )
        if not updated:
            # Another reply won the race. Do not repeat the outbound side effect.
            return status if await repo.list_reminder_replies_for_phone(
                org_id, phone, status
            ) else "handoff"
        if confirmed:
            await self._send_reminder_result(org_id, phone, b["id"], "confirmed")
            return "confirmed"
        else:
            await self._send_reminder_result(org_id, phone, b["id"], "rejected")
            return "rejected"

    async def _send_reminder_result(self, org_id, phone, booking_id, status):
        text = ("Grazie, la tua prenotazione e' confermata! Ti aspettiamo."
                if status == "confirmed" else
                "Prenotazione cancellata. Per qualsiasi altra richiesta, siamo a disposizione.")
        return await self._send_whatsapp(
            org_id, phone, text,
            idempotency_key=f"reminder-response:{org_id}:{booking_id}:{status}",
        )

    @staticmethod
    def _reminder_handoff_key(org_id, phone, bookings):
        booking_ids = ",".join(sorted(str(booking["id"]) for booking in bookings))
        return f"reminder-handoff:{org_id}:{phone}:{booking_ids}"

    # ── Deposito ───────────────────────────────────────────────

    async def _get_deposito_config(self, org_id):
        settings = await self.repo.get_booking_settings(org_id)
        if not settings:
            return None
        config = settings.get("config") or {}
        return config.get("deposito")

    async def _valuta_richiede_deposito(self, org_id, coperti=0, tipo_evento="", ora="", data=""):
        cfg = await self._get_deposito_config(org_id)
        if not cfg or not cfg.get("enabled"):
            return False
        criteri = cfg.get("criteri") or {}
        coperti_min = criteri.get("coperti_min")
        if coperti_min is not None and coperti >= coperti_min:
            return True
        tipi = criteri.get("tipi_evento") or []
        if tipo_evento and tipo_evento in tipi:
            return True
        fasce = criteri.get("fasce") or []
        ora_fascia = f"{int(ora[:2]):02d}:00"
        if ora_fascia in fasce:
            return True
        date_specifiche = criteri.get("date") or []
        if data in date_specifiche:
            return True
        return False

    async def _genera_payment_link(self, org_id, booking_id, importo, valuta="EUR"):
        from decimal import Decimal, ROUND_HALF_UP
        import os
        from src.core.billing.routes import _get_stripe, _stripe_call
        st = _get_stripe()
        amount = int((Decimal(str(importo)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        currency = valuta.lower()
        if amount <= 0 or currency != "eur":
            raise ValueError("Invalid deposit amount or currency")
        # Freeze server-owned payment facts before calling the provider.
        async with self.repo.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE bookings SET deposit_amount_minor = $3, deposit_currency = $4
                WHERE id = $1::uuid AND organization_id = $2::uuid
                  AND (deposit_amount_minor IS NULL OR (deposit_amount_minor = $3 AND deposit_currency = $4))
                RETURNING id
            """, str(booking_id), str(org_id), amount, currency)
            if not row:
                raise ValueError("Booking missing or deposit facts changed")
        base = os.getenv("APP_BASE_URL", "https://app.melpis.it").rstrip("/")
        link = await _stripe_call(st.checkout.Session.create,
            mode="payment",
            line_items=[{
                "price_data": {
                    "unit_amount": amount,
                    "currency": currency,
                    "product_data": {"name": "Deposito prenotazione"},
                },
                "quantity": 1,
            }],
            metadata={"booking_id": str(booking_id), "organization_id": str(org_id)},
            success_url=base + "/?deposit=success",
            cancel_url=base + "/?deposit=cancelled",
            idempotency_key=f"deposit:{org_id}:{booking_id}:{amount}:{currency}",
        )
        async with self.repo.pool.acquire() as conn:
            await conn.execute("""
                UPDATE bookings SET deposit_session_id = $3
                WHERE id = $1::uuid AND organization_id = $2::uuid
            """, str(booking_id), str(org_id), link.id)
        return link.url
