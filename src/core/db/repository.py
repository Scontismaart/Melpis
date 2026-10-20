from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from src.core.db.repositories.billing_repo import BillingRepository
from src.core.db.repositories.booking_repo import BookingRepository
from src.core.db.repositories.contact_repo import ContactRepository
from src.core.db.repositories.conversation_repo import ConversationRepository
from src.core.db.repositories.document_repo import DocumentRepository
from src.core.db.repositories.message_repo import MessageRepository
from src.core.db.repositories.organization_repo import OrganizationRepository
from src.core.db.repositories.review_repo import ReviewRepository
from src.core.db.scoping import TenantScopedRepository, system_scope


class CoreRepository(TenantScopedRepository):
    """Facade di retrocompatibilità per CoreRepository.

    Delega tutte le chiamate ai repository specializzati per dominio:
    - OrganizationRepository
    - ContactRepository
    - ConversationRepository
    - MessageRepository
    - BookingRepository
    - DocumentRepository
    - ReviewRepository
    - BillingRepository
    """

    _CAMPI_REVIEW_AGGIORNABILI = ReviewRepository._CAMPI_REVIEW_AGGIORNABILI

    def __init__(self, pool):
        self.pool = pool
        self._org_repo = OrganizationRepository(pool)
        self._booking_repo = BookingRepository(pool)
        self._doc_repo = DocumentRepository(pool)
        self._review_repo = ReviewRepository(pool)
        self._billing_repo = BillingRepository(pool)
        self._contact_repo = ContactRepository(pool)
        self._conv_repo = ConversationRepository(pool)
        self._msg_repo = MessageRepository(pool)

        # Accesso diretto ai sotto-repository per migrazione progressiva
        self.org_repo = self._org_repo
        self.booking_repo = self._booking_repo
        self.doc_repo = self._doc_repo
        self.review_repo = self._review_repo
        self.billing_repo = self._billing_repo
        self.contact_repo = self._contact_repo
        self.conv_repo = self._conv_repo
        self.msg_repo = self._msg_repo

    def __getattr__(self, name: str):
        if name in (
            "_org_repo", "_booking_repo", "_doc_repo", "_review_repo",
            "_billing_repo", "_contact_repo", "_conv_repo", "_msg_repo",
            "org_repo", "booking_repo", "doc_repo", "review_repo",
            "billing_repo", "contact_repo", "conv_repo", "msg_repo",
        ):
            pool = getattr(self, "pool", None)
            if pool is not None:
                self._org_repo = OrganizationRepository(pool)
                self._booking_repo = BookingRepository(pool)
                self._doc_repo = DocumentRepository(pool)
                self._review_repo = ReviewRepository(pool)
                self._billing_repo = BillingRepository(pool)
                self._contact_repo = ContactRepository(pool)
                self._conv_repo = ConversationRepository(pool)
                self._msg_repo = MessageRepository(pool)
                self.org_repo = self._org_repo
                self.booking_repo = self._booking_repo
                self.doc_repo = self._doc_repo
                self.review_repo = self._review_repo
                self.billing_repo = self._billing_repo
                self.contact_repo = self._contact_repo
                self.conv_repo = self._conv_repo
                self.msg_repo = self._msg_repo
                return getattr(self, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    # ── Bookings ──────────────────────────────────────────────

    @asynccontextmanager
    async def slot_lock(self, organization_id, data, ora):
        """Lock consultivo transazionale su una fascia oraria (anti double-booking)."""
        async with self._booking_repo.slot_lock(organization_id, data, ora):
            yield

    async def create_booking(self, organization_id, nome_cliente, data, ora, coperti,
                             telefono="", note="", stato="in_attesa", origine="Dashboard",
                             richiede_intervento=False, id_conversazione=None,
                             contact_id=None, richiede_deposito=False,
                             completata_at=None, tipo_evento="", source_message_id=None):
        return await self._booking_repo.create_booking(
            organization_id, nome_cliente, data, ora, coperti,
            telefono=telefono, note=note, stato=stato, origine=origine,
            richiede_intervento=richiede_intervento, id_conversazione=id_conversazione,
            contact_id=contact_id, richiede_deposito=richiede_deposito,
            completata_at=completata_at, tipo_evento=tipo_evento, source_message_id=source_message_id,
        )

    async def get_booking(self, organization_id, booking_id):
        return await self._booking_repo.get_booking(organization_id, booking_id)

    async def list_bookings(self, organization_id, data=None):
        return await self._booking_repo.list_bookings(organization_id, data=data)

    async def update_booking_status(self, organization_id, booking_id, stato, expected_status=None, expected=None):
        return await self._booking_repo.update_booking_status(
            organization_id, booking_id, stato, expected_status=expected_status, expected=expected)

    async def mark_booking_completed(self, organization_id, booking_id, expected):
        return await self._booking_repo.mark_booking_completed(organization_id, booking_id, expected)

    async def update_booking_details(self, organization_id, booking_id,
                                     nome_cliente, telefono, data, ora,
                                     coperti, note, stato, expected=None):
        return await self._booking_repo.update_booking_details(
            organization_id, booking_id, nome_cliente, telefono, data, ora, coperti, note, stato,
            expected=expected,
        )

    async def update_booking_payment(self, organization_id, booking_id,
                                      payment_status, session_id=None):
        return await self._booking_repo.update_booking_payment(
            organization_id, booking_id, payment_status, session_id=session_id
        )

    async def list_bookings_by_stato(self, organization_id, stato):
        return await self._booking_repo.list_bookings_by_stato(organization_id, stato)

    async def list_bookings_for_reminder(self, organization_id, target_date):
        return await self._booking_repo.list_bookings_for_reminder(organization_id, target_date)

    async def update_booking_reminder_status(self, organization_id, booking_id,
                                             reminder_status, responded_at=None):
        return await self._booking_repo.update_booking_reminder_status(
            organization_id, booking_id, reminder_status, responded_at=responded_at
        )

    async def list_bookings_da_verificare(self, organization_id, target_date):
        return await self._booking_repo.list_bookings_da_verificare(organization_id, target_date)

    async def upsert_booking_settings_config(self, organization_id, config):
        return await self._booking_repo.upsert_booking_settings_config(organization_id, config)

    async def get_booking_settings(self, organization_id):
        return await self._booking_repo.get_booking_settings(organization_id)

    async def upsert_booking_settings(self, organization_id, fasce_orarie,
                                       capienze_orarie, slot_minutes=60):
        return await self._booking_repo.upsert_booking_settings(
            organization_id, fasce_orarie, capienze_orarie, slot_minutes=slot_minutes
        )

    # ── Reviews ───────────────────────────────────────────────

    async def create_review(self, organization_id, testo,
                             valutazione_stelle=None, fonte="manuale",
                             autore="", contact_id=None,
                             external_id=None, bozza_risposta="",
                             sentiment="", categoria="",
                             richiede_revisione_urgente=False,
                             stato="nuova"):
        return await self._review_repo.create_review(
            organization_id, testo, valutazione_stelle=valutazione_stelle, fonte=fonte,
            autore=autore, contact_id=contact_id, external_id=external_id,
            bozza_risposta=bozza_risposta, sentiment=sentiment, categoria=categoria,
            richiede_revisione_urgente=richiede_revisione_urgente, stato=stato,
        )

    async def get_review(self, organization_id, review_id):
        return await self._review_repo.get_review(organization_id, review_id)

    async def get_review_by_external_id(self, organization_id, external_id):
        return await self._review_repo.get_review_by_external_id(organization_id, external_id)

    async def list_reviews(self, organization_id, stato=None, fonte=None,
                           page=1, limit=20):
        return await self._review_repo.list_reviews(
            organization_id, stato=stato, fonte=fonte, page=page, limit=limit
        )

    async def count_reviews(self, organization_id, stato=None, fonte=None):
        return await self._review_repo.count_reviews(
            organization_id, stato=stato, fonte=fonte
        )

    async def update_review(self, organization_id, review_id, **kwargs):
        return await self._review_repo.update_review(organization_id, review_id, **kwargs)

    async def approve_review(self, organization_id, review_id):
        return await self._review_repo.approve_review(organization_id, review_id)

    async def get_review_analytics(self, organization_id, giorni=90):
        return await self._review_repo.get_review_analytics(organization_id, giorni=giorni)

    # ── Knowledge Base (RAG) ──────────────────────────────────

    async def create_document(self, organization_id, nome, tipo="upload",
                              fonte="", caricato_il=None, is_active=True,
                              stato="indicizzata", errore="", metadata=None):
        return await self._doc_repo.create_document(
            organization_id, nome, tipo=tipo, fonte=fonte, caricato_il=caricato_il,
            is_active=is_active, stato=stato, errore=errore, metadata=metadata,
        )

    async def get_document(self, organization_id, document_id):
        return await self._doc_repo.get_document(organization_id, document_id)

    async def update_document(self, organization_id, document_id, **fields):
        return await self._doc_repo.update_document(organization_id, document_id, **fields)

    async def toggle_document_active(self, organization_id, document_id):
        return await self._doc_repo.toggle_document_active(organization_id, document_id)

    async def delete_document_chunks(self, organization_id, document_id):
        return await self._doc_repo.delete_document_chunks(organization_id, document_id)

    async def add_chunk(self, organization_id, document_id, chunk_index,
                        content, embedding, metadata=None):
        return await self._doc_repo.add_chunk(
            organization_id, document_id, chunk_index, content, embedding, metadata=metadata,
        )

    async def search_similar(self, organization_id, embedding, k=5, only_active=True):
        return await self._doc_repo.search_similar(
            organization_id, embedding, k=k, only_active=only_active
        )

    async def list_documents(self, organization_id):
        return await self._doc_repo.list_documents(organization_id)

    async def get_ui_summary(self, organization_id):
        return await self._doc_repo.get_ui_summary(organization_id)

    async def count_chunks(self, organization_id):
        return await self._doc_repo.count_chunks(organization_id)

    async def list_sources(self, organization_id, tipo=None):
        return await self._doc_repo.list_sources(organization_id, tipo=tipo)

    async def list_all_active_chunks(self, organization_id):
        return await self._doc_repo.list_all_active_chunks(organization_id)

    async def delete_document(self, organization_id, document_id):
        return await self._doc_repo.delete_document(organization_id, document_id)

    async def faq_cache_invalidate(self, organization_id) -> int:
        return await self._doc_repo.faq_cache_invalidate(organization_id)

    # ── Business Profile & Onboarding ─────────────────────────

    @staticmethod
    def _json_fields_onboarding(result: dict) -> dict:
        return OrganizationRepository._json_fields_onboarding(result)

    async def get_onboarding_profile(self, organization_id):
        return await self._org_repo.get_onboarding_profile(organization_id)

    async def save_onboarding_profile(self, organization_id, verticale, nome_attivita,
                                      orari, tono, servizi, regole_escalation,
                                      whatsapp_collegato, documenti_importati, profilo,
                                      lingue_supportate=None, lingua_default=None,
                                      descrizione=""):
        return await self._org_repo.save_onboarding_profile(
            organization_id, verticale, nome_attivita, orari, tono, servizi,
            regole_escalation, whatsapp_collegato, documenti_importati, profilo,
            lingue_supportate=lingue_supportate, lingua_default=lingua_default,
            descrizione=descrizione,
        )

    async def get_org_business_profile(self, organization_id):
        return await self._org_repo.get_org_business_profile(organization_id)

    async def update_org_business_profile(self, organization_id, business_profile: dict):
        return await self._org_repo.update_org_business_profile(organization_id, business_profile)

    # ── Email Configurations ──────────────────────────────────

    async def add_email_config(self, organization_id, indirizzo, is_active=True):
        return await self._org_repo.add_email_config(organization_id, indirizzo, is_active=is_active)

    async def list_email_configs(self, organization_id):
        return await self._org_repo.list_email_configs(organization_id)

    async def remove_email_config(self, organization_id, indirizzo):
        return await self._org_repo.remove_email_config(organization_id, indirizzo)

    # ── Usage events ──────────────────────────────────────────

    async def record_usage(self, organization_id, event_type, quantity=1, metadata=None):
        return await self._billing_repo.record_usage(
            organization_id, event_type, quantity=quantity, metadata=metadata
        )

    async def get_usage_by_month(self, organization_id, year, month):
        return await self._billing_repo.get_usage_by_month(organization_id, year, month)

    async def get_usage_summary(self, organization_id, year, month):
        return await self._billing_repo.get_usage_summary(organization_id, year, month)

    # ── Auth & Memberships ────────────────────────────────────

    async def get_membership_by_auth(self, auth_user_id: str, organization_id: str) -> dict | None:
        return await self._org_repo.get_membership_by_auth(auth_user_id, organization_id=organization_id)

    @system_scope("risoluzione multi-org da JWT validato server-side")
    async def get_memberships_by_auth(self, auth_user_id: str) -> list[dict]:
        return await self._org_repo.get_memberships_by_auth(auth_user_id)

    async def get_organization(self, organization_id: uuid.UUID | str) -> dict | None:
        return await self._org_repo.get_organization(organization_id)

    # ── Billing & Abbonamento ─────────────────────────────────

    async def get_organization_billing(self, organization_id: uuid.UUID | str) -> dict:
        return await self._billing_repo.get_organization_billing(organization_id)

    async def update_organization_billing(
        self, organization_id: uuid.UUID | str, data: dict
    ) -> dict:
        return await self._billing_repo.update_organization_billing(organization_id, data)

    async def set_subscription_status(
        self, organization_id: uuid.UUID | str, status: str
    ) -> None:
        return await self._billing_repo.set_subscription_status(organization_id, status)

    async def increment_message_usage(
        self, organization_id: uuid.UUID | str
    ) -> int | None:
        raise RuntimeError("Direct message usage increments are disabled; use the atomic A04 commit")

    async def reset_message_usage(
        self,
        organization_id: uuid.UUID | str,
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        return await self._billing_repo.reset_message_usage(organization_id, period_start, period_end)

    async def process_stripe_event(
        self, event_id: str, organization_id: uuid.UUID | str
    ) -> bool:
        return await self._billing_repo.process_stripe_event(event_id, organization_id)

    async def process_stripe_event_in_tx(
        self, conn, event_id: str, organization_id: uuid.UUID | str
    ) -> bool:
        return await self._billing_repo.process_stripe_event_in_tx(conn, event_id, organization_id)

    async def update_plan_limits(
        self, organization_id: uuid.UUID | str, plan_slug: str
    ) -> dict:
        return await self._billing_repo.update_plan_limits(organization_id, plan_slug)

    @system_scope("risoluzione tenant da stripe_customer_id platform-unique")
    async def get_organization_by_stripe_customer(
        self, stripe_customer_id: str
    ) -> dict | None:
        return await self._billing_repo.get_organization_by_stripe_customer(stripe_customer_id)

    # ── GDPR ─────────────────────────────────────────────────────

    async def get_contacts_by_org(self, organization_id: uuid.UUID | str) -> list[dict]:
        return await self._contact_repo.get_contacts_by_org(organization_id)

    async def get_conversations_by_org(self, organization_id: uuid.UUID | str) -> list[dict]:
        return await self._conv_repo.get_conversations_by_org(organization_id)

    async def get_messages_by_org(self, organization_id: uuid.UUID | str) -> list[dict]:
        return await self._msg_repo.get_messages_by_org(organization_id)

    async def claim_message_for_processing(self, msg_id: str, org_id: str) -> dict:
        return await self._msg_repo.claim_message_for_processing(msg_id, org_id)

    async def reserve_simulation_request(
        self, organization_id, auth_user_id, request_id, payload_hash
    ) -> dict:
        return await self._msg_repo.reserve_simulation_request(
            organization_id, auth_user_id, request_id, payload_hash
        )

    async def complete_simulation_request(
        self, organization_id, auth_user_id, request_id, payload_hash, claim_token, response
    ) -> bool:
        return await self._msg_repo.complete_simulation_request(
            organization_id, auth_user_id, request_id, payload_hash, claim_token, response
        )

    async def fail_simulation_request(
        self, organization_id, auth_user_id, request_id, payload_hash, claim_token
    ) -> bool:
        return await self._msg_repo.fail_simulation_request(
            organization_id, auth_user_id, request_id, payload_hash, claim_token
        )

    async def get_simulation_requests_by_org(self, organization_id) -> list[dict]:
        return await self._msg_repo.get_simulation_requests_by_org(organization_id)

    async def purge_simulation_requests(self, retention_days: int = 30) -> int:
        return await self._msg_repo.purge_simulation_requests(retention_days)

    async def get_organization_owners(self, org_id: str) -> list[dict]:
        return await self._org_repo.get_organization_owners(org_id)

    @system_scope("root PK delete, cascade DB, endpoint owner-only")
    async def delete_organization(self, organization_id: uuid.UUID | str) -> None:
        return await self._org_repo.delete_organization(organization_id)

    # ── Registrazione (system scope: l'org non esiste ancora) ──

    async def get_auth_access_allowed(self, auth_user_id: str) -> bool:
        return await self._org_repo.get_auth_access_allowed(auth_user_id)

    async def disable_auth_access(self, auth_user_id: str) -> bool:
        return await self._org_repo.disable_auth_access(auth_user_id)

    async def create_organization_with_owner(
        self,
        auth_user_id: str,
        nome_attivita: str,
        trial_days: int = 7,
    ) -> dict:
        return await self._org_repo.create_organization_with_owner(
            auth_user_id, nome_attivita, trial_days=trial_days
        )

    @system_scope("provisioning JIT org al primo accesso OAuth")
    async def get_or_create_organization_with_owner(
        self,
        auth_user_id: str,
        nome_attivita: str,
        trial_days: int = 7,
    ) -> dict:
        return await self._org_repo.get_or_create_organization_with_owner(
            auth_user_id, nome_attivita, trial_days=trial_days
        )
