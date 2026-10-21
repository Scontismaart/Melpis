from __future__ import annotations

import json
import uuid
from typing import Any

from src.core.db.repositories.billing_repo import BillingRepository
from src.core.db.repositories.booking_repo import BookingRepository
from src.core.db.repositories.contact_repo import ContactRepository
from src.core.db.repositories.conversation_repo import ConversationRepository
from src.core.db.repositories.document_repo import DocumentRepository
from src.core.db.repositories.message_repo import MessageRepository
from src.core.db.repositories.organization_repo import OrganizationRepository
from src.core.db.scoping import TenantScopedRepository, system_scope

STATUS_RANK = {
    "queued": 0,
    "processing": 0,
    "sending_ambiguous": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    "failed": 4,
}


def apply_status_update(current_status: str, new_status: str) -> bool:
    if new_status == "failed":
        return True
    if new_status == "sending_ambiguous" and current_status in ("queued", "processing"):
        # Pre-mark Send-Then-Mark: mossa laterale consentita prima della chiamata Meta.
        return True
    return STATUS_RANK.get(new_status, 0) > STATUS_RANK.get(current_status, 0)


class Repository(TenantScopedRepository):
    """Facade di retrocompatibilità per WhatsApp Repository.

    Delega tutte le operazioni ai repository specializzati per dominio:
    - OrganizationRepository
    - ContactRepository
    - ConversationRepository
    - MessageRepository
    - BookingRepository
    - DocumentRepository
    - BillingRepository
    """

    def __init__(self, pool):
        self.pool = pool
        self._org_repo = OrganizationRepository(pool)
        self._contact_repo = ContactRepository(pool)
        self._conv_repo = ConversationRepository(pool)
        self._msg_repo = MessageRepository(pool)
        self._booking_repo = BookingRepository(pool)
        self._doc_repo = DocumentRepository(pool)
        self._billing_repo = BillingRepository(pool)

        # Accesso diretto ai sotto-repository per migrazione progressiva
        self.org_repo = self._org_repo
        self.contact_repo = self._contact_repo
        self.conv_repo = self._conv_repo
        self.msg_repo = self._msg_repo
        self.booking_repo = self._booking_repo
        self.doc_repo = self._doc_repo
        self.billing_repo = self._billing_repo

    async def record_processing_failure(self, message_id, organization_id, handling_type):
        return await self._msg_repo.record_processing_failure(message_id, organization_id, handling_type)

    async def purge_simulation_requests(self, retention_days: int = 30) -> int:
        return await self._msg_repo.purge_simulation_requests(retention_days)

    def __getattr__(self, name: str):
        if name in (
            "_org_repo", "_contact_repo", "_conv_repo", "_msg_repo",
            "_booking_repo", "_doc_repo", "_billing_repo",
            "org_repo", "contact_repo", "conv_repo", "msg_repo",
            "booking_repo", "doc_repo", "billing_repo",
        ):
            pool = getattr(self, "pool", None)
            if pool is not None:
                self._org_repo = OrganizationRepository(pool)
                self._contact_repo = ContactRepository(pool)
                self._conv_repo = ConversationRepository(pool)
                self._msg_repo = MessageRepository(pool)
                self._booking_repo = BookingRepository(pool)
                self._doc_repo = DocumentRepository(pool)
                self._billing_repo = BillingRepository(pool)
                self.org_repo = self._org_repo
                self.contact_repo = self._contact_repo
                self.conv_repo = self._conv_repo
                self.msg_repo = self._msg_repo
                self.booking_repo = self._booking_repo
                self.doc_repo = self._doc_repo
                self.billing_repo = self._billing_repo
                return getattr(self, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    # ── Tenant Resolution & Config ────────────────────────────

    @system_scope("tenant-resolution: lookup da webhook Meta (identita' platform-unique, pre-auth)")
    async def get_org_by_phone_number_id(self, phone_number_id: str):
        return await self._org_repo.get_org_by_phone_number_id(phone_number_id)

    @system_scope("tenant-resolution: lookup da webhook Meta (identita' platform-unique, pre-auth)")
    async def get_org_by_waba_id(self, waba_id: str):
        return await self._org_repo.get_org_by_waba_id(waba_id)

    @system_scope("tenant-resolution: lookup fan-out da webhook Meta (waba_id 1:N, pre-auth)")
    async def get_orgs_by_waba_id(self, waba_id: str) -> list:
        """Tutte le org su un waba_id (condiviso possibile): il chiamante applica
        ogni write a ciascuna org scoped sul proprio organization_id."""
        return await self._org_repo.get_orgs_by_waba_id(waba_id)

    async def get_org_subscription_state(self, org_id):
        return await self._billing_repo.get_org_subscription_state(org_id)

    async def record_usage(self, organization_id, event_type, quantity=1, metadata=None):
        return await self._billing_repo.record_usage(
            organization_id, event_type, quantity=quantity, metadata=metadata
        )

    async def get_tenant_config(self, org_id):
        return await self._org_repo.get_tenant_config(org_id)

    @staticmethod
    def encrypt_token(plaintext: str) -> str:
        return OrganizationRepository.encrypt_token(plaintext)

    @staticmethod
    def decrypt_token(ciphertext: str) -> str:
        return OrganizationRepository.decrypt_token(ciphertext)

    async def save_tenant_config(self, org_id, phone_number_id: str, waba_id: str, access_token: str):
        return await self._org_repo.save_tenant_config(org_id, phone_number_id, waba_id, access_token)

    async def delete_tenant_config(self, org_id) -> bool:
        return await self._org_repo.delete_tenant_config(org_id)

    async def get_org_business_profile(self, org_id):
        return await self._org_repo.get_org_business_profile(org_id)

    # ── Contacts & Consent ────────────────────────────────────

    async def get_or_create_contact(self, org_id, phone):
        return await self._contact_repo.get_or_create_contact(org_id, phone)

    async def get_or_create_conversation(self, org_id, contact_id, canale: str = "whatsapp"):
        return await self._conv_repo.get_or_create_conversation(org_id, contact_id, canale=canale)

    async def get_contact_prefs(self, org_id, phone):
        return await self._contact_repo.get_contact_prefs(org_id, phone)

    async def has_recent_whatsapp_inbound(self, org_id, phone: str) -> bool:
        return await self._contact_repo.has_recent_whatsapp_inbound(org_id, phone)

    async def record_consent_event(self, contact_id, event_type, method,
                                  triggering_message_id=None, matched_text=None, *,
                                  organization_id):
        return await self._contact_repo.record_consent_event(
            contact_id, event_type, method,
            triggering_message_id=triggering_message_id, matched_text=matched_text,
            organization_id=organization_id,
        )

    async def get_contact_consent(self, contact_id, organization_id) -> str | None:
        return await self._contact_repo.get_contact_consent(contact_id, organization_id)

    async def mark_ai_disclosure_sent(self, contact_id: uuid.UUID, organization_id) -> bool:
        return await self._contact_repo.mark_ai_disclosure_sent(contact_id, organization_id)

    # ── Messages & Delivery ───────────────────────────────────

    async def upsert_message(self, id, organization_id, conversation_id, wam_id, direction,
                             message_type, content, content_text, status, handling_type=None,
                             idempotency_key=None, conn=None):
        return await self._msg_repo.upsert_message(
            id, organization_id, conversation_id, wam_id, direction,
            message_type, content, content_text, status, handling_type=handling_type,
            idempotency_key=idempotency_key, conn=conn,
        )

    async def _upsert_message(self, conn, id, organization_id, conversation_id, wam_id, direction,
                              message_type, content, content_text, status, handling_type=None,
                              idempotency_key=None):
        return await self._msg_repo._upsert_message(
            conn, id, organization_id, conversation_id, wam_id, direction,
            message_type, content, content_text, status, handling_type=handling_type,
            idempotency_key=idempotency_key,
        )

    async def update_message_status(self, message_id, new_status, wam_id=None, error_code=None,
                                    error_title=None, error_details=None, biz_opaque_callback_data=None, *,
                                    organization_id):
        return await self._msg_repo.update_message_status(
            message_id, new_status, wam_id=wam_id, error_code=error_code,
            error_title=error_title, error_details=error_details,
            biz_opaque_callback_data=biz_opaque_callback_data,
            organization_id=organization_id,
        )

    async def update_message_status_by_wam_id(self, wam_id, new_status, error_code=None,
                                              error_title=None, error_details=None, *,
                                              organization_id):
        return await self._msg_repo.update_message_status_by_wam_id(
            wam_id, new_status, error_code=error_code,
            error_title=error_title, error_details=error_details,
            organization_id=organization_id,
        )

    async def claim_inbound_messages(self, limit=10):
        return await self._msg_repo.claim_inbound_messages(limit=limit)

    async def claim_outbound_delivery(self, message_id, *, organization_id):
        return await self._msg_repo.claim_outbound_delivery(message_id, organization_id=organization_id)

    async def try_mark_replied(self, message_id, handling_type: str | None = None, *,
                               organization_id):
        return await self._msg_repo.try_mark_replied(
            message_id, handling_type=handling_type, organization_id=organization_id
        )

    async def update_heartbeat(self, message_id, organization_id):
        return await self._msg_repo.update_heartbeat(message_id, organization_id)

    async def claim_delivery_attempts(self, limit=10):
        return await self._msg_repo.claim_delivery_attempts(limit=limit)

    async def insert_delivery_attempt(self, message_id, next_retry_at):
        return await self._msg_repo.insert_delivery_attempt(message_id, next_retry_at)

    async def enqueue_ready_booking_confirmations(self, limit=20):
        return await self._msg_repo.enqueue_ready_booking_confirmations(limit)

    async def update_delivery_attempt(self, attempt_id, status, error_details=None):
        return await self._msg_repo.update_delivery_attempt(attempt_id, status, error_details=error_details)

    async def reconstruct_payload_for_retry(self, message_id):
        return await self._msg_repo.reconstruct_payload_for_retry(message_id)

    async def reap_stale_claims(self, timeout_minutes=15, dead_letter_threshold=3):
        return await self._msg_repo.reap_stale_claims(
            timeout_minutes=timeout_minutes, dead_letter_threshold=dead_letter_threshold
        )

    async def delete_expired_messages(self, retention_days: int = 60) -> int:
        return await self._msg_repo.delete_expired_messages(retention_days=retention_days)

    async def purge_soft_deleted_messages(self, grace_days: int = 30) -> int:
        return await self._msg_repo.purge_soft_deleted_messages(grace_days=grace_days)

    async def cleanup_empty_conversations(self) -> int:
        return await self._conv_repo.cleanup_empty_conversations()

    async def get_outbound_dedup(self, organization_id, message_id) -> dict | None:
        return await self._msg_repo.get_outbound_dedup(organization_id, message_id)

    async def save_outbound_dedup(self, message_id: uuid.UUID, org_id: uuid.UUID, response_text: str):
        return await self._msg_repo.save_outbound_dedup(message_id, org_id, response_text)

    async def check_message_usage(self, org_id: uuid.UUID) -> dict | None:
        return await self._billing_repo.check_message_usage(org_id)

    async def increment_message_usage(self, org_id: uuid.UUID, conn=None) -> int:
        raise RuntimeError("Direct message usage increments are disabled; use the atomic A04 commit")

    async def _increment_message_usage(self, conn, org_id: uuid.UUID) -> int | None:
        raise RuntimeError("Direct message usage increments are disabled; use the atomic A04 commit")

    async def upsert_template(self, organization_id, name, language, category, status, components):
        return await self._org_repo.upsert_template(
            organization_id, name, language, category, status, components
        )

    async def update_template_status(self, organization_id, name, language, status,
                                     rejected_reason=None):
        return await self._org_repo.update_template_status(
            organization_id, name, language, status, rejected_reason=rejected_reason
        )

    async def search_similar(self, organization_id: str, embedding: list, k: int = 3) -> list[dict]:
        return await self._doc_repo.search_similar(organization_id, embedding, k=k)

    @staticmethod
    def _vec_str(embedding: list) -> str:
        return DocumentRepository._vec_str(embedding)

    async def faq_cache_lookup(self, organization_id: str, embedding: list,
                               max_distance: float = 0.08) -> dict | None:
        return await self._doc_repo.faq_cache_lookup(organization_id, embedding, max_distance=max_distance)

    async def faq_cache_store(self, organization_id: str, question_text: str,
                              answer_text: str, embedding: list,
                              prompt_variant: str = "control",
                              ttl_hours: int = 72) -> dict:
        return await self._doc_repo.faq_cache_store(
            organization_id, question_text, answer_text, embedding,
            prompt_variant=prompt_variant, ttl_hours=ttl_hours,
        )

    async def faq_cache_invalidate(self, organization_id: str) -> int:
        return await self._doc_repo.faq_cache_invalidate(organization_id)

    async def get_last_ai_outbound_message(self, organization_id, conversation_id):
        return await self._msg_repo.get_last_ai_outbound_message(organization_id, conversation_id)

    async def get_message_org_scoped(self, organization_id, message_id) -> dict | None:
        return await self._msg_repo.get_message_org_scoped(organization_id, message_id)

    async def registra_feedback(self, organization_id, message_id, conversation_id,
                                source: str, value: str, created_by_user_id=None) -> dict:
        return await self._msg_repo.registra_feedback(
            organization_id, message_id, conversation_id,
            source=source, value=value, created_by_user_id=created_by_user_id,
        )

    # ── HITL Tickets & Inbox ──────────────────────────────────

    async def list_tickets(self, org_id: str, status: str | None = None, priorita: str | None = None,
                           limit: int | None = None, offset: int = 0) -> list[dict]:
        return await self._conv_repo.list_tickets(org_id, status=status, priorita=priorita, limit=limit, offset=offset)

    async def get_conversation(self, conversation_id: str, organization_id) -> dict | None:
        return await self._conv_repo.get_conversation(conversation_id, organization_id)

    async def list_conversation_messages(self, org_id: str, conversation_id: str,
                                         limit: int = 50, offset: int = 0) -> list[dict]:
        return await self._conv_repo.list_conversation_messages(org_id, conversation_id, limit=limit, offset=offset)

    async def escalate_to_human(self, conversation_id: str, organization_id) -> dict | None:
        return await self._conv_repo.escalate_to_human(conversation_id, organization_id)

    async def claim_ticket(self, conversation_id: str, staff_user_id: str, expected_version: int,
                           organization_id) -> dict | None:
        return await self._conv_repo.claim_ticket(conversation_id, staff_user_id, expected_version, organization_id)

    async def release_ticket(self, conversation_id: str, staff_user_id: str,
                             organization_id) -> dict | None:
        return await self._conv_repo.release_ticket(conversation_id, staff_user_id, organization_id)

    async def resolve_ticket(self, conversation_id: str, staff_user_id: str,
                             organization_id) -> dict | None:
        return await self._conv_repo.resolve_ticket(conversation_id, staff_user_id, organization_id)

    async def list_team_members(self, org_id: str) -> list[dict]:
        return await self._org_repo.list_team_members(org_id)

    async def assign_ticket(self, conversation_id: str, staff_user_id: str, expected_version: int,
                            organization_id) -> dict | None:
        return await self._conv_repo.assign_ticket(conversation_id, staff_user_id, expected_version, organization_id)

    async def set_conversation_ai_active(self, conversation_id: str, organization_id) -> dict | None:
        return await self._conv_repo.set_conversation_ai_active(conversation_id, organization_id)

    async def check_idempotency(self, org_id: str, idempotency_key: str) -> dict | None:
        return await self._msg_repo.check_idempotency(org_id, idempotency_key)

    async def claim_message_and_check_quota(self, msg_id: str, org_id: str) -> dict:
        return await self._msg_repo.claim_message_and_check_quota(msg_id, org_id)

    async def claim_message_for_processing(self, msg_id: str, org_id: str) -> dict:
        return await self._msg_repo.claim_message_for_processing(msg_id, org_id)

    async def check_booking_exists(self, msg_id: str, org_id: str) -> bool:
        return await self._msg_repo.check_booking_exists(msg_id, org_id)

    async def get_booking_for_message(self, organization_id, source_message_id):
        return await self._booking_repo.get_booking_for_message(organization_id, source_message_id)

    async def save_ai_reply(self, msg_id: str, reply: dict | str, richiede_umano: bool = False,
                            motivo: str = "", *, organization_id) -> None:
        return await self._msg_repo.save_ai_reply(
            msg_id, reply, richiede_umano=richiede_umano, motivo=motivo,
            organization_id=organization_id,
        )

    async def mark_message_sent(self, msg_id: str, meta_message_id: str, organization_id) -> None:
        return await self._msg_repo.mark_message_sent(msg_id, meta_message_id, organization_id)
