"""BookingAdapterRouter — routing, 4 modalità operative e data minimization fail-closed (Task 4).

Componenti principali:
- BookingMode: Enum a 4 stati (authoritative, shadow, mirror, local_only).
- DataMinimizationPolicy: Invariante 6 GDPR. Sanitizzazione fail-closed delle note sanitarie:
  se l'attività è studio_medico (o se il verticale è sconosciuto/None per fallimento tecnico)
  e medical_dpa_signed è False, i dati clinici vengono rimossi a monte prima di raggiungere
  qualsiasi adapter esterno.
- BookingAdapterRouter: Dispatcher e gestore del pattern Send-Then-Mark.
"""
from __future__ import annotations

import logging
import uuid
from enum import Enum
from typing import Any

from src.core.bookings.adapters.fake_adapter import FakeBookingAdapter
from src.core.bookings.adapters.internal_adapter import InternalBookingAdapter
from src.core.bookings.adapters.simplybook_adapter import (
    CircuitOpenError,
    SimplyBookAdapter,
    SimplyBookTimeoutError,
)
from src.core.bookings.adapters.zak_adapter import ZakAdapter
from src.core.bookings.ports.base import (
    AvailabilityQuery,
    AvailabilityResult,
    BookingResult,
    BookingSystemPort,
    CancelBookingRequest,
    CreateBookingRequest,
)

logger = logging.getLogger(__name__)


# ── 1. Modalità Operative ────────────────────────────────────

class BookingMode(str, Enum):
    """Modalità operative per la sincronizzazione con gestionali esterni."""
    AUTHORITATIVE = "authoritative"  # Gestionale esterno comanda; errore -> escalation umana
    SHADOW = "shadow"                # DB locale comanda; chiamata esterna best-effort non bloccante
    MIRROR = "mirror"                # Dual-write coordinato (Send-Then-Mark)
    LOCAL_ONLY = "local_only"        # Esclusivamente DB locale, nessun gestionale esterno


# ── 2. Data Minimization Fail-Closed (Invariante 6 GDPR) ──────

class DataMinimizationPolicy:
    """Policy centrale di Data Minimization GDPR per dati sanitari / medici.

    Invariante 6 (Fail-Closed):
    - Se l'organizzazione appartiene a un settore medico o se il verticale
      non è determinabile con certezza (verticale is None per errori di rete o DB),
      e non risulta sottoscritto un accordo formale sul trattamento dati (medical_dpa_signed != True),
      le note libere vengono SBANCATE a monte prima di essere inoltrate all'esterno.
    - Se il verticale è chiaramente non-medico (es. ristorante, parrucchiere, hotel),
      le note operative sono preservate integralmente.
    """

    SENSITIVE_VERTICALS = frozenset({
        "studio_medico_dentista",
        "studio_medico",
        "medico",
        "clinica",
        "sanitario",
        "odontoiatra",
        "dentista",
        "fisioterapia",
    })

    # Only recognized non-medical sectors may preserve operational notes by
    # default. Unknown/malformed values must not become a medical bypass.
    NON_MEDICAL_VERTICALS = frozenset({
        "ristorante", "parrucchiere", "centro_estetico", "hotel_bnb", "hotel",
    })

    def apply(
        self,
        req: CreateBookingRequest,
        verticale: str | None,
        medical_dpa_signed: bool = False,
    ) -> CreateBookingRequest:
        """Restituisce una copia sanitizzata della richiesta se applicabile la minimizzazione."""
        normalized = verticale.strip().lower() if isinstance(verticale, str) else None
        is_medical = normalized not in self.NON_MEDICAL_VERTICALS

        # Invariante 6 (Whitelist rigorosa): autorizzazione concessa SOLO se medical_dpa_signed
        # è tassativamente True (bool). Qualsiasi valore diverso (None, False, stringa non vuota, 0)
        # attiva la minimizzazione fail-closed.
        has_signed_dpa = (medical_dpa_signed is True)

        if is_medical and not has_signed_dpa:
            # Minimizzazione attiva: azzera le note per evitare leak di sintomi o terapie
            logger.info(
                "GDPR Data Minimization applicata a req %s",
                req.idempotency_key,
            )
            return req.model_copy(update={"note": ""})

        return req


# ── 3. BookingAdapterRouter ──────────────────────────────────

class BookingAdapterRouter:
    """Router che seleziona l'adapter appropriato e gestisce il ciclo Send-Then-Mark.

    Garantisce:
    - Risoluzione tenant-isolated dell'adapter a partire da `external_booking_credentials`
    - Rispetto rigoroso della modalità operativa (authoritative vs shadow vs mirror)
    - Applicazione centralizzata della DataMinimizationPolicy prima del dispatch
    - Idempotenza a livello applicativo contro doppie prenotazioni
    """

    def __init__(self, repo=None, data_minimizer: DataMinimizationPolicy | None = None):
        self.repo = repo
        self.data_minimizer = data_minimizer or DataMinimizationPolicy()
        self._adapter_cache: dict[str, BookingSystemPort] = {}

    async def _persist_credential_update(
        self, org_uuid: uuid.UUID, update: dict[str, Any]
    ) -> None:
        """Callback on_token_refreshed: persiste i token ruotati cifrati at-rest.

        Senza questa persistenza un refresh riuscito vivrebbe solo in memoria e un
        restart renderebbe l'integrazione inutilizzabile (refresh_token ruotato perso).
        Mai loggare i valori (segreti): solo org e nomi-chiave.
        """
        if not self.repo:
            return
        persist_fn = getattr(self.repo, "update_credential_fields", None)
        if not callable(persist_fn):
            logger.warning(
                "Token refresh non persistito per org %s: repository senza update_credential_fields",
                org_uuid,
            )
            return
        try:
            await persist_fn(org_uuid, update)
            logger.info(
                "Token booking ruotati persistiti per org %s (chiavi: %s)",
                org_uuid,
                sorted(update.keys()),
            )
        except Exception as exc:
            logger.warning(
                "Persistenza token booking fallita per org %s: %s",
                org_uuid,
                exc,
            )

    async def resolve_adapter(
        self, org_id: uuid.UUID | str
    ) -> tuple[BookingSystemPort, BookingMode, dict[str, Any]]:
        """Recupera le credenziali dell'organizzazione e istanzia l'adapter corrispondente."""
        org_uuid = uuid.UUID(str(org_id))
        if not self.repo:
            return InternalBookingAdapter(), BookingMode.LOCAL_ONLY, {}

        creds = await self.repo.get_credentials(org_uuid)
        if not creds or not creds.get("is_active", True):
            return InternalBookingAdapter(), BookingMode.LOCAL_ONLY, {}

        provider = creds.get("provider", "").lower()
        config = creds.get("config") or {}
        mode_str = config.get("mode", BookingMode.AUTHORITATIVE.value)
        try:
            mode = BookingMode(mode_str)
        except ValueError:
            mode = BookingMode.AUTHORITATIVE

        if mode == BookingMode.MIRROR:
            logger.warning(
                "Modalità 'mirror' richiesta per org %s ma non ancora supportata "
                "(richiede webhook in ingresso dal gestionale). Fallback di sicurezza a 'authoritative'.",
                org_uuid,
            )
            mode = BookingMode.AUTHORITATIVE

        if provider == "simplybook":
            adapter = SimplyBookAdapter(
                company_login=creds.get("company_login", ""),
                api_key=creds.get("api_key", ""),
                timeout_seconds=float(config.get("timeout_seconds", 4.0)),
                circuit_breaker_threshold=int(config.get("circuit_breaker_threshold", 5)),
                circuit_breaker_reset_seconds=float(config.get("circuit_breaker_reset_seconds", 60.0)),
            )
            return adapter, mode, config

        if provider in ("zak", "wubook", "wubook_zak"):
            adapter = ZakAdapter(
                property_id=creds.get("property_id", ""),
                api_key=creds.get("api_key", ""),
                base_url=config.get("base_url", "https://api.wubook.net/zak/v1"),
                timeout_seconds=float(config.get("timeout_seconds", 5.0)),
            )
            return adapter, mode, config

        if provider in ("calcom", "cal.com", "cal_com"):
            from src.core.bookings.adapters.calcom_adapter import CalComAdapter
            adapter = CalComAdapter(
                api_key=creds.get("api_key", ""),
                base_url=config.get("base_url", "https://api.cal.com/v2"),
                event_type_id=config.get("event_type_id") or creds.get("event_type_id"),
                default_timezone=config.get("timezone", "Europe/Rome"),
                placeholder_email_domain=config.get("placeholder_email_domain", "noemail.invalid"),
                timeout_seconds=float(config.get("timeout_seconds", 5.0)),
                circuit_breaker_threshold=int(config.get("circuit_breaker_threshold", 5)),
                circuit_breaker_reset_seconds=float(config.get("circuit_breaker_reset_seconds", 60.0)),
            )
            return adapter, mode, config

        if provider in ("apaleo", "apaleo_pms"):
            from src.core.bookings.adapters.apaleo_adapter import ApaleoAdapter
            raw_creds = creds.get("credentials") if isinstance(creds.get("credentials"), dict) else {}
            client_id = creds.get("client_id") or raw_creds.get("client_id") or ""
            client_secret = creds.get("client_secret") or creds.get("api_key") or raw_creds.get("client_secret") or ""
            refresh_token = creds.get("refresh_token") or raw_creds.get("refresh_token")

            def _as_float(value: Any) -> float | None:
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            async def _persist_apaleo_tokens(update: dict[str, Any]) -> None:
                await self._persist_credential_update(org_uuid, update)

            property_id = creds.get("property_id") or raw_creds.get("property_id") or config.get("property_id")
            adapter = ApaleoAdapter(
                organization_id=org_uuid,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                property_id=property_id,
                base_url=config.get("base_url", "https://api.apaleo.com"),
                default_channel_code=config.get("channel_code", "Direct"),
                default_timezone=config.get("timezone", "Europe/Rome"),
                timeout_seconds=float(config.get("timeout_seconds", 8.0)),
                circuit_breaker_threshold=int(config.get("circuit_breaker_threshold", 5)),
                circuit_breaker_reset_seconds=float(config.get("circuit_breaker_reset_seconds", 60.0)),
                on_token_refreshed=_persist_apaleo_tokens,
            )
            # Warm-start: token persistito da un refresh precedente (sopravvive al restart).
            warm_token = raw_creds.get("access_token")
            warm_expires = _as_float(raw_creds.get("expires_at"))
            if warm_token and warm_expires:
                adapter.auth._access_token = str(warm_token)
                adapter.auth._expires_at = warm_expires
            return adapter, mode, config

        if provider in ("beds24", "beds24_v2"):
            from src.core.bookings.adapters.beds24_adapter import Beds24Adapter
            raw_creds = creds.get("credentials") if isinstance(creds.get("credentials"), dict) else {}
            token = creds.get("token") or creds.get("api_key") or raw_creds.get("token")
            refresh_token = creds.get("refresh_token") or raw_creds.get("refresh_token")
            invite_code = creds.get("invite_code") or raw_creds.get("invite_code")
            property_id = creds.get("property_id") or raw_creds.get("property_id") or config.get("property_id")

            def _as_float(value: Any) -> float | None:
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            async def _persist_beds24_tokens(update: dict[str, Any]) -> None:
                await self._persist_credential_update(org_uuid, update)

            adapter = Beds24Adapter(
                organization_id=org_uuid,
                token=token,
                refresh_token=refresh_token,
                invite_code=invite_code,
                property_id=property_id,
                base_url=config.get("base_url", "https://api.beds24.com/v2"),
                default_timezone=config.get("timezone", "Europe/Rome"),
                timeout_seconds=float(config.get("timeout_seconds", 8.0)),
                circuit_breaker_threshold=int(config.get("circuit_breaker_threshold", 5)),
                circuit_breaker_reset_seconds=float(config.get("circuit_breaker_reset_seconds", 60.0)),
                on_token_refreshed=_persist_beds24_tokens,
                initial_expires_at=_as_float(raw_creds.get("expires_at")),
            )
            return adapter, mode, config

        if provider == "fake":
            return FakeBookingAdapter(), mode, config

        return InternalBookingAdapter(), BookingMode.LOCAL_ONLY, config

    async def dispatch_create_booking(
        self,
        org_id: uuid.UUID | str,
        req: CreateBookingRequest,
        adapter: BookingSystemPort | None = None,
        mode: BookingMode | None = None,
        verticale: str | None = None,
        medical_dpa_signed: bool = False,
    ) -> BookingResult:
        """Esegue la creazione della prenotazione rispettando la modalità e il pattern Send-Then-Mark.

        Fasi:
        1. Claim atomico dello slot di idempotenza su external_booking_sync
           (sostituisce pre-check + prepare separati: un solo vincitore chiama
           l'esterna; replay 'synced' -> no-op, 'pending' -> guard)
        2. Data minimization a monte (Invariante 6)
        3. Invocazione adapter secondo la modalità (authoritative, shadow, mirror)
        4. Aggiornamento stato 'synced' o 'failed'
        """
        org_uuid = uuid.UUID(str(org_id))

        # 0. Risoluzione adapter e modalità se non forniti esplicitamente
        if adapter is None or mode is None:
            resolved_adapter, resolved_mode, config = await self.resolve_adapter(org_uuid)
            adapter = adapter or resolved_adapter
            mode = mode or resolved_mode
            medical_dpa_signed = config.get("medical_dpa_signed") is True

        if mode == BookingMode.LOCAL_ONLY:
            return BookingResult(
                success=True,
                stato="confermata",
                sync_status="local_only",
            )

        if mode == BookingMode.MIRROR:
            raise NotImplementedError(
                "Modalità 'mirror' non supportata senza webhook in ingresso dal gestionale esterno."
            )

        # 1. Claim atomico Send-Then-Mark (sostituisce pre-check + prepare
        # separati: il check-then-act non atomico permetteva doppie chiamate
        # esterne in race; qui un solo vincitore per (org, key) procede).
        provider_name = getattr(adapter, "provider_name", getattr(adapter, "_company_login", "external"))
        if self.repo:
            sync_row, acquired = await self.repo.claim_sync_slot(
                org_uuid,
                req.idempotency_key,
                str(provider_name),
                req.internal_booking_id,
            )
            if not acquired:
                sync_st = (sync_row or {}).get("sync_status")
                if sync_st == "synced":
                    logger.info(
                        "Replay idempotente rilevato (già sincronizzato) per org %s key %s",
                        org_uuid,
                        req.idempotency_key,
                    )
                    return BookingResult(
                        success=True,
                        external_booking_id=(sync_row or {}).get("external_booking_id"),
                        stato="confermata",
                        sync_status="synced",
                    )
                logger.warning(
                    "Replay idempotente concorrente rilevato (richiesta in corso) per org %s key %s",
                    org_uuid,
                    req.idempotency_key,
                )
                return BookingResult(
                    success=True,
                    stato="in_attesa",
                    sync_status="pending_retry",
                    error_message="Sincronizzazione già in corso",
                )
                # Riga 'failed' -> il claim l'ha gia' riportata a pending e
                # acquisita: si procede oltre (retry consentito).

        # 2. Data Minimization Fail-Closed a monte
        minimized_req = self.data_minimizer.apply(
            req, verticale=verticale, medical_dpa_signed=medical_dpa_signed
        )

        # 3. Invocazione in modalità SHADOW
        if mode == BookingMode.SHADOW:
            try:
                shadow_res = await adapter.create_booking(org_uuid, minimized_req)
                if self.repo:
                    await self.repo.record_sync_success(
                        org_uuid,
                        req.idempotency_key,
                        (shadow_res.external_booking_id or "") if shadow_res else "",
                    )
            except Exception as e:
                logger.warning("Shadow booking external call failed (non-blocking): %s", e)
                if self.repo:
                    await self.repo.record_sync_failure(
                        org_uuid,
                        req.idempotency_key,
                        str(e),
                    )
            return BookingResult(
                success=True,
                stato="confermata",
                sync_status="skipped_shadow",
            )

        # 5. Invocazione in modalità AUTHORITATIVE o MIRROR
        try:
            res = await adapter.create_booking(org_uuid, minimized_req)
            if res.success:
                if self.repo:
                    await self.repo.record_sync_success(
                        org_uuid,
                        req.idempotency_key,
                        res.external_booking_id or "",
                    )
                return res
            else:
                if self.repo:
                    await self.repo.record_sync_failure(
                        org_uuid,
                        req.idempotency_key,
                        res.error_message or "External booking rejected",
                    )
                return res

        except CircuitOpenError as coe:
            logger.error("Circuit breaker aperto per org %s: %s", org_uuid, coe)
            if self.repo:
                await self.repo.record_sync_failure(
                    org_uuid,
                    req.idempotency_key,
                    str(coe),
                )
            return BookingResult(
                success=False,
                stato="errore",
                error_code="circuit_open",
                error_message=str(coe),
                sync_status="failed",
            )

        except Exception as exc:
            logger.error("Errore durante chiamata adapter esterno per org %s: %s", org_uuid, exc)
            if self.repo:
                await self.repo.record_sync_failure(
                    org_uuid,
                    req.idempotency_key,
                    str(exc),
                )
            return BookingResult(
                success=False,
                stato="errore",
                error_code="network_error",
                error_message=str(exc),
                sync_status="failed",
            )

    async def dispatch_get_availability(
        self,
        org_id: uuid.UUID | str,
        query: AvailabilityQuery,
        adapter: BookingSystemPort | None = None,
        mode: BookingMode | None = None,
    ) -> AvailabilityResult:
        """Verifica la disponibilità reale sul gestionale esterno."""
        org_uuid = uuid.UUID(str(org_id))
        if adapter is None or mode is None:
            resolved_adapter, resolved_mode, _ = await self.resolve_adapter(org_uuid)
            adapter = adapter or resolved_adapter
            mode = mode or resolved_mode

        if mode == BookingMode.LOCAL_ONLY or isinstance(adapter, InternalBookingAdapter):
            return AvailabilityResult(success=True, slots=[])

        try:
            return await adapter.get_availability(org_uuid, query)
        except CircuitOpenError as coe:
            logger.warning("Circuit breaker aperto per availability org %s: %s", org_uuid, coe)
            return AvailabilityResult(
                success=False,
                slots=[],
                error_message="Circuit breaker aperto: gestionale non disponibile (richiede_umano)",
            )
        except Exception as exc:
            logger.warning("Verifica disponibilità esterna fallita per org %s: %s", org_uuid, exc)
            return AvailabilityResult(
                success=False,
                slots=[],
                error_message=str(exc),
            )

    async def dispatch_cancel_booking(
        self,
        org_id: uuid.UUID | str,
        req: CancelBookingRequest,
        adapter: BookingSystemPort | None = None,
        mode: BookingMode | None = None,
    ) -> BookingResult:
        """Annulla la prenotazione sul gestionale esterno (idempotente per chiave cancel)."""
        org_uuid = uuid.UUID(str(org_id))
        if adapter is None or mode is None:
            resolved_adapter, resolved_mode, _ = await self.resolve_adapter(org_uuid)
            adapter = adapter or resolved_adapter
            mode = mode or resolved_mode

        if mode == BookingMode.LOCAL_ONLY or isinstance(adapter, InternalBookingAdapter):
            return BookingResult(success=True, stato="cancellata", sync_status="local_only")

        # Claim atomico: doppio click / retry non duplicano la chiamata esterna.
        provider_name = getattr(adapter, "provider_name", getattr(adapter, "_company_login", "external"))
        if self.repo:
            sync_row, acquired = await self.repo.claim_sync_slot(
                org_uuid,
                req.idempotency_key,
                str(provider_name),
                None,
            )
            if not acquired:
                sync_st = (sync_row or {}).get("sync_status")
                if sync_st == "synced":
                    logger.info(
                        "Replay cancel idempotente (già cancellato) per org %s key %s",
                        org_uuid,
                        req.idempotency_key,
                    )
                    return BookingResult(success=True, stato="cancellata", sync_status="synced")
                logger.warning(
                    "Cancel concorrente rilevato (già in corso) per org %s key %s",
                    org_uuid,
                    req.idempotency_key,
                )
                return BookingResult(
                    success=True,
                    stato="in_attesa",
                    sync_status="pending_retry",
                    error_message="Cancellazione già in corso",
                )

        try:
            res = await adapter.cancel_booking(org_uuid, req)
            if self.repo:
                if res.success:
                    await self.repo.record_sync_success(
                        org_uuid,
                        req.idempotency_key,
                        res.external_booking_id or "",
                    )
                else:
                    await self.repo.record_sync_failure(
                        org_uuid,
                        req.idempotency_key,
                        res.error_message or "External cancel rejected",
                    )
            return res
        except Exception as exc:
            logger.error("Cancellazione esterna fallita per org %s: %s", org_uuid, exc)
            if self.repo:
                await self.repo.record_sync_failure(
                    org_uuid,
                    req.idempotency_key,
                    str(exc),
                )
            return BookingResult(
                success=False,
                stato="errore",
                error_code="cancel_failed",
                error_message=str(exc),
            )

