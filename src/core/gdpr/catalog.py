"""Static, reviewed SQL projections for GDPR exports.

Every query is deliberately written out: never replace a projection with
``SELECT *`` or interpolate a table/column name from request data. ``$1`` is
always the organization UUID. Subject projections use ``$2`` as either a
contact UUID or authenticated account UUID, as named by the field.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Category:
    name: str
    query: str
    contact_query: str | None = None
    account_query: str | None = None
    exclusion_reason: str | None = None


CATEGORIES: tuple[Category, ...] = (
    Category("organizations", """
        SELECT id, name, created_at, updated_at, subscription_status, plan,
               messages_used_this_period, messages_limit, users_limit,
               whatsapp_numbers_limit, current_period_start, current_period_end,
               trial_start, trial_end, timezone, sla_minutes
        FROM organizations WHERE id = $1::uuid
    """),
    Category("whatsapp_accounts", """
        SELECT id, phone_number_id, waba_id, created_at, updated_at
        FROM whatsapp_accounts WHERE organization_id = $1::uuid
    """, exclusion_reason="access_token and verify_token are excluded."),
    Category("contacts", """
        SELECT id, phone_number, marketing_opt_out, opted_out_at, consent_status,
               consent_updated_at, ai_disclosure_sent_at, created_at, updated_at,
               deleted_at
        FROM contacts WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT id, phone_number, marketing_opt_out, opted_out_at, consent_status,
               consent_updated_at, ai_disclosure_sent_at, created_at, updated_at,
               deleted_at
        FROM contacts WHERE organization_id = $1::uuid AND id = $2::uuid
    """),
    Category("conversations", """
        SELECT id, contact_id, status, last_message_at, created_at, deleted_at,
               ticket_status, assigned_to, pending_staff_at, claimed_at, resolved_at,
               updated_at, version, canale
        FROM conversations WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT id, contact_id, status, last_message_at, created_at, deleted_at,
               ticket_status, pending_staff_at, claimed_at, resolved_at,
               updated_at, version, canale
        FROM conversations
        WHERE organization_id = $1::uuid AND contact_id = $2::uuid
    """),
    Category("messages", """
        SELECT id, conversation_id, wam_id, direction, message_type, content_text,
               status, handling_type, sent_at, delivered_at, read_at, created_at,
               updated_at, deleted_at, replied_at, billed_at
        FROM messages WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT m.id, m.conversation_id, m.wam_id, m.direction, m.message_type,
               m.status, m.handling_type, m.sent_at, m.delivered_at, m.read_at,
               m.created_at, m.updated_at, m.deleted_at, m.replied_at, m.billed_at
        FROM messages AS m
        JOIN conversations AS c ON c.id = m.conversation_id
                                AND c.organization_id = m.organization_id
        WHERE m.organization_id = $1::uuid
          AND c.organization_id = $1::uuid AND c.contact_id = $2::uuid
    """, exclusion_reason=
             "Unstructured message content is included for organization export only; subject export omits it because of third-party text risk."),
    Category("message_delivery_attempts", """
        SELECT a.id, a.message_id, a.attempt_number, a.status, a.next_retry_at,
               a.claimed_at, a.created_at
        FROM message_delivery_attempts AS a
        JOIN messages AS m ON m.id = a.message_id
        WHERE m.organization_id = $1::uuid
    """, contact_query="""
        SELECT a.id, a.message_id, a.attempt_number, a.status, a.next_retry_at,
               a.claimed_at, a.created_at
        FROM message_delivery_attempts AS a
        JOIN messages AS m ON m.id = a.message_id
        JOIN conversations AS c ON c.id = m.conversation_id
                                AND c.organization_id = m.organization_id
        WHERE m.organization_id = $1::uuid
          AND c.organization_id = $1::uuid AND c.contact_id = $2::uuid
    """, exclusion_reason="Unreviewed error_details JSON is excluded."),
    Category("contact_consent_log", """
        SELECT l.id, l.contact_id, l.event_type, l.method,
               l.triggering_message_id, l.created_at
        FROM contact_consent_log AS l
        JOIN contacts AS c ON c.id = l.contact_id
        WHERE c.organization_id = $1::uuid
    """, contact_query="""
        SELECT l.id, l.contact_id, l.event_type, l.method,
               l.triggering_message_id, l.created_at
        FROM contact_consent_log AS l
        JOIN contacts AS c ON c.id = l.contact_id
        WHERE c.organization_id = $1::uuid AND c.id = $2::uuid
    """, exclusion_reason="matched_text is free text and is excluded."),
    Category("whatsapp_templates", """
        SELECT id, name, language, category, status, created_at, updated_at
        FROM whatsapp_templates WHERE organization_id = $1::uuid
    """, exclusion_reason="Free-form components JSON is excluded."),
    Category("bookings", """
        SELECT id, contact_id, nome_cliente, telefono, data, ora, coperti, note,
               stato, origine, richiede_intervento, id_conversazione, created_at,
               updated_at, tipo_evento, richiede_deposito, payment_status,
               payment_link_created_at, reminder_sent_at, reminder_status,
               reminder_responded_at, completata_at, no_show_at, google_event_id,
               source_message_id, deposit_amount_minor, deposit_currency
        FROM bookings WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT id, contact_id, data, ora, coperti, stato, origine,
               richiede_intervento, created_at, updated_at, tipo_evento,
               richiede_deposito, payment_status, payment_link_created_at,
               reminder_sent_at, reminder_status, reminder_responded_at,
               completata_at, no_show_at, deposit_amount_minor, deposit_currency
        FROM bookings
        WHERE organization_id = $1::uuid AND contact_id = $2::uuid
    """, exclusion_reason=
             "Subject projection omits free-text name, phone, notes, conversation reference, and payment URLs/provider session IDs."),
    Category("booking_settings", """
        SELECT id, slot_minutes, updated_at
        FROM booking_settings WHERE organization_id = $1::uuid
    """, exclusion_reason="Unreviewed schedule and configuration JSON is excluded."),
    Category("reviews", """
        SELECT id, contact_id, testo, valutazione_stelle, fonte, autore, created_at,
               bozza_risposta, sentiment, categoria, richiede_revisione_urgente,
               stato, external_id, published_at, is_anonymized
        FROM reviews WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT id, contact_id, valutazione_stelle, fonte, created_at, published_at,
               is_anonymized
        FROM reviews WHERE organization_id = $1::uuid AND contact_id = $2::uuid
    """, exclusion_reason="Subject projection omits review text, author and generated response as free text."),
    Category("documents", """
        SELECT id, nome, tipo, fonte, is_active, stato, errore, caricato_il,
               updated_at, created_at
        FROM documents WHERE organization_id = $1::uuid
    """, exclusion_reason="No verified contact/account subject key; metadata JSON is excluded."),
    Category("document_chunks", """
        SELECT id, document_id, chunk_index, content, created_at
        FROM document_chunks WHERE organization_id = $1::uuid
    """, exclusion_reason="No verified contact/account subject key; embeddings and metadata JSON are excluded."),
    Category("email_configs", """
        SELECT id, indirizzo, is_active, created_at
        FROM email_configs WHERE organization_id = $1::uuid
    """),
    Category("usage_events", """
        SELECT id, event_type, quantity, metadata->>'provider' AS provider,
               metadata->>'requested_model' AS requested_model,
               metadata->>'actual_model' AS actual_model,
               metadata->>'task_type' AS task_type,
               metadata->>'reason' AS reason, metadata->>'outcome' AS outcome,
               metadata->>'accounting_status' AS accounting_status,
               metadata->>'prompt_tokens' AS prompt_tokens,
               metadata->>'completion_tokens' AS completion_tokens,
               metadata->>'total_tokens' AS total_tokens,
               metadata->>'latency_ms' AS latency_ms,
               metadata->>'estimated_cost_eur' AS estimated_cost_eur,
               metadata->>'usage_present' AS usage_present, billing_month, created_at
        FROM usage_events WHERE organization_id = $1::uuid
    """, exclusion_reason="Only named scalar accounting keys are selected from metadata; all other JSON is excluded."),
    Category("event_log", """
        SELECT id, source_table, source_id, tipo_evento, priorita, gestito_da_ai, created_at
        FROM event_log WHERE organization_id = $1::uuid
    """, exclusion_reason="Original text, AI response and details JSON are free-form and excluded."),
    Category("simulation_requests", """
        SELECT request_id, status, created_at, reserved_at, completed_at
        FROM simulation_requests WHERE organization_id = $1::uuid
    """, account_query="""
        SELECT request_id, status, created_at, reserved_at, completed_at
        FROM simulation_requests
        WHERE organization_id = $1::uuid AND auth_user_id = $2::uuid
    """, exclusion_reason="Request/claim tokens, payload hash, and unreviewed response JSON are excluded."),
    Category("user_profiles", """
        SELECT p.id, p.auth_user_id, p.email, p.nome, p.created_at, p.updated_at
        FROM user_profiles AS p
        JOIN organization_memberships AS om ON om.user_id = p.id
        WHERE om.organization_id = $1::uuid
    """, account_query="""
        SELECT id, auth_user_id, email, nome, created_at, updated_at
        FROM user_profiles AS p
        WHERE p.auth_user_id = $2::uuid
          AND EXISTS (
              SELECT 1 FROM organization_memberships AS om
              WHERE om.user_id = p.id AND om.organization_id = $1::uuid
          )
    """),
    Category("organization_memberships", """
        SELECT om.id, om.user_id, om.ruolo, om.invited_at, om.joined_at,
               p.auth_user_id, p.email, p.nome
        FROM organization_memberships AS om
        JOIN user_profiles AS p ON p.id = om.user_id
        WHERE om.organization_id = $1::uuid
    """, account_query="""
        SELECT om.id, om.organization_id, om.ruolo, om.invited_at, om.joined_at
        FROM organization_memberships AS om
        JOIN user_profiles AS p ON p.id = om.user_id
        WHERE om.organization_id = $1::uuid AND p.auth_user_id = $2::uuid
    """),
    Category("audit_log", """
        SELECT id, user_id, auth_user_id, action, target_table, target_id, created_at
        FROM audit_log WHERE organization_id = $1::uuid
    """, account_query="""
        SELECT id, user_id, auth_user_id, action, target_table, target_id, created_at
        FROM audit_log
        WHERE organization_id = $1::uuid AND auth_user_id = $2::text
    """, exclusion_reason="Details JSON is excluded."),
    Category("processed_stripe_events", """
        SELECT event_id, processed_at FROM processed_stripe_events
        WHERE organization_id = $1::uuid
    """),
    Category("webhook_idempotency", "", exclusion_reason=
             "Global table has no organization or verified subject key; event identifiers are excluded."),
    Category("google_calendar_credentials", """
        SELECT calendar_id, sync_enabled, last_sync_at, created_at, updated_at
        FROM google_calendar_credentials WHERE organization_id = $1::uuid
    """, exclusion_reason="Access/refresh tokens and token expiry are excluded."),
    Category("oauth_nonces", "", exclusion_reason=
             "Contains one-time security nonces; excluded."),
    Category("google_business_credentials", """
        SELECT account_name, location_name, last_sync_at, created_at, updated_at,
               review_page_account_name, review_page_location_name
        FROM google_business_credentials WHERE organization_id = $1::uuid
    """, exclusion_reason="Access/refresh tokens, token expiry and review page token are excluded."),
    Category("onboarding_profiles", """
        SELECT id, verticale, nome_attivita, whatsapp_collegato, created_at, updated_at,
               lingua_default
        FROM onboarding_profiles WHERE organization_id = $1::uuid
    """, exclusion_reason="Free-form profile, services, schedules, escalation and language JSON are excluded."),
    Category("instagram_accounts", """
        SELECT id, ig_user_id, created_at, updated_at
        FROM instagram_accounts WHERE organization_id = $1::uuid
    """, exclusion_reason="access_token is excluded; account identifiers are not customer-subject data."),
    Category("faq_cache", """
        SELECT id, question_text, answer_text, prompt_variant, hit_count,
               last_used_at, expires_at, created_at
        FROM faq_cache WHERE organization_id = $1::uuid
    """, exclusion_reason="No verified contact/account subject key; embeddings and question hash are excluded."),
    Category("message_feedback", """
        SELECT id, message_id, conversation_id, source, value, created_by_user_id, created_at
        FROM message_feedback WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT f.id, f.message_id, f.conversation_id, f.source, f.value, f.created_at
        FROM message_feedback AS f
        JOIN conversations AS c ON c.id = f.conversation_id
        WHERE f.organization_id = $1::uuid
          AND c.organization_id = $1::uuid AND c.contact_id = $2::uuid
    """),
    Category("weekly_report_log", """
        SELECT id, periodo_inizio, periodo_fine, inviato_at, stato
        FROM weekly_report_log WHERE organization_id = $1::uuid
    """, exclusion_reason="Recipient addresses and free-form failure reason are excluded."),
    Category("outbound_dedup", """
        SELECT message_id, created_at FROM outbound_dedup
        WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT d.message_id, d.created_at
        FROM outbound_dedup AS d
        JOIN messages AS m ON m.id = d.message_id
        JOIN conversations AS c ON c.id = m.conversation_id
        WHERE d.organization_id = $1::uuid
          AND m.organization_id = $1::uuid AND c.organization_id = $1::uuid
          AND c.contact_id = $2::uuid
    """, exclusion_reason="Cached response_text is free text and excluded."),
    Category("external_booking_credentials", """
        SELECT provider, is_active, created_at, updated_at
        FROM external_booking_credentials WHERE organization_id = $1::uuid
    """, exclusion_reason="Encrypted credentials and arbitrary config are excluded."),
    Category("external_booking_sync", """
        SELECT id, internal_booking_id, external_booking_id, provider, sync_status,
               retry_count, last_attempt_at, created_at, updated_at
        FROM external_booking_sync WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT s.id, s.internal_booking_id, s.external_booking_id, s.provider,
               s.sync_status, s.retry_count, s.last_attempt_at, s.created_at, s.updated_at
        FROM external_booking_sync AS s
        JOIN bookings AS b ON b.id = s.internal_booking_id
        WHERE s.organization_id = $1::uuid
          AND b.organization_id = $1::uuid AND b.contact_id = $2::uuid
    """, exclusion_reason="Idempotency key and free-form sync_error are excluded."),
    Category("airtable_connections", """
        SELECT id, token_type, base_id, base_name, scopes, is_active, created_at, updated_at
        FROM airtable_connections WHERE organization_id = $1::uuid
    """, exclusion_reason="Encrypted credentials and unreviewed metadata are excluded."),
    Category("airtable_field_mappings", "", exclusion_reason=
             "Contains arbitrary field mapping/configuration JSON; excluded."),
    Category("airtable_webhooks", """
        SELECT id, base_id, webhook_id, cursor, is_active, created_at, updated_at
        FROM airtable_webhooks WHERE organization_id = $1::uuid
    """, exclusion_reason="MAC secret, notification URL and arbitrary specification are excluded."),
    Category("airtable_webhook_events", """
        SELECT id, base_id, event_type, status, created_at, processed_at
        FROM airtable_webhook_events WHERE organization_id = $1::uuid
    """, exclusion_reason="External event ID, raw payload and error text are excluded."),
    Category("meta_webhook_inbox", """
        SELECT id, channel, status, attempts, created_at, completed_at
        FROM meta_webhook_inbox WHERE organization_id = $1::uuid
    """, exclusion_reason="Webhook payload, event key, trace ID, lease token and error are excluded."),
    Category("billing_checkout_intents", """
        SELECT id, created_at, expires_at FROM billing_checkout_intents
        WHERE organization_id = $1::uuid
    """, exclusion_reason="Request key, payload hash/body and checkout session URL are excluded."),
    Category("governance_outbox", """
        SELECT id, event_kind, created_at FROM governance_outbox
        WHERE organization_id = $1::uuid
    """, exclusion_reason="Unreviewed payload JSON is excluded."),
    Category("team_invitations", """
        SELECT id, email, ruolo, created_at, expires_at, consumed_at, revoked_at
        FROM team_invitations WHERE organization_id = $1::uuid
    """, account_query="""
        SELECT i.id, i.email, i.ruolo, i.created_at, i.expires_at,
               i.consumed_at, i.revoked_at
        FROM team_invitations AS i
        JOIN user_profiles AS p ON lower(btrim(p.email)) = i.email
        WHERE i.organization_id = $1::uuid AND p.auth_user_id = $2::uuid
    """, exclusion_reason="Token hash and inviter ID are excluded; account query only matches the authenticated profile email."),
    Category("auth_access_lifecycle", "", account_query="""
        SELECT a.auth_user_id, a.provisioned_at, a.disabled_at
        FROM auth_access_lifecycle AS a
        WHERE a.auth_user_id = $2::uuid
          AND EXISTS (
              SELECT 1 FROM user_profiles AS p
              JOIN organization_memberships AS om ON om.user_id = p.id
              WHERE p.auth_user_id = a.auth_user_id
                AND om.organization_id = $1::uuid
          )
    """, exclusion_reason="Account-scoped, not tenant-scoped; only the authenticated account may be queried."),
    Category("provider_attempt_runtime", """
        SELECT r.organization_id, r.attempt_id, r.worker_id, r.lease_epoch,
               r.lease_expires_at, r.dispatch_state, r.generation_id,
               r.provider_request_id, r.updated_at
        FROM provider_attempt_runtime AS r
        JOIN usage_events AS u ON u.id = r.attempt_id
                              AND u.organization_id = r.organization_id
        WHERE r.organization_id = $1::uuid AND u.organization_id = $1::uuid
    """, exclusion_reason="Operational provider IDs and worker fencing state are included; no customer content is stored here."),
    Category("provider_attempt_evidence", """
        SELECT e.id, e.organization_id, e.attempt_id, e.worker_id, e.lease_epoch,
               e.event_type, e.created_at
        FROM provider_attempt_evidence AS e
        JOIN usage_events AS u ON u.id = e.attempt_id
                              AND u.organization_id = e.organization_id
        WHERE e.organization_id = $1::uuid AND u.organization_id = $1::uuid
    """, exclusion_reason="details JSON is excluded; migration 061 documents evidence as transition/accounting data without customer content."),
    Category("booking_confirmation_outbox", """
        SELECT id, booking_id, inbound_message_id, outbound_message_id, status,
               created_at, scheduled_at
        FROM booking_confirmation_outbox WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT o.id, o.booking_id, o.inbound_message_id, o.outbound_message_id,
               o.status, o.created_at, o.scheduled_at
        FROM booking_confirmation_outbox AS o
        JOIN bookings AS b ON b.id = o.booking_id
        WHERE o.organization_id = $1::uuid
          AND b.organization_id = $1::uuid AND b.contact_id = $2::uuid
    """),
    Category("gdpr_operations", """
        SELECT id, kind, scope, subject_id, status, expires_at, created_at, updated_at
        FROM gdpr_operations WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT id, kind, scope, subject_id, status, expires_at, created_at, updated_at
        FROM gdpr_operations
        WHERE organization_id = $1::uuid AND scope = 'contact' AND subject_id = $2::uuid
    """, account_query="""
        SELECT id, kind, scope, subject_id, status, expires_at, created_at, updated_at
        FROM gdpr_operations
        WHERE organization_id = $1::uuid AND requester = $2::text
    """, exclusion_reason="requester, request ID/hash, manifest and session data are excluded."),
    Category("gdpr_operation_steps", """
        SELECT s.operation_id, s.name, s.status, s.attempts, s.fence, s.updated_at
        FROM gdpr_operation_steps AS s
        JOIN gdpr_operations AS o ON o.id = s.operation_id
                                  AND o.organization_id = s.organization_id
        WHERE s.organization_id = $1::uuid AND o.organization_id = $1::uuid
    """, contact_query="""
        SELECT s.operation_id, s.name, s.status, s.attempts, s.fence, s.updated_at
        FROM gdpr_operation_steps AS s
        JOIN gdpr_operations AS o ON o.id = s.operation_id
                                  AND o.organization_id = s.organization_id
        WHERE s.organization_id = $1::uuid AND o.organization_id = $1::uuid
          AND o.scope = 'contact' AND o.subject_id = $2::uuid
    """, account_query="""
        SELECT s.operation_id, s.name, s.status, s.attempts, s.fence, s.updated_at
        FROM gdpr_operation_steps AS s
        JOIN gdpr_operations AS o ON o.id = s.operation_id
                                  AND o.organization_id = s.organization_id
        WHERE s.organization_id = $1::uuid AND o.organization_id = $1::uuid
          AND o.requester = $2::text
    """, exclusion_reason="Step evidence JSON and lease data are excluded."),
    Category("gdpr_operation_events", """
        SELECT e.id, e.operation_id, e.event_type, e.created_at
        FROM gdpr_operation_events AS e
        JOIN gdpr_operations AS o ON o.id = e.operation_id
                                  AND o.organization_id = e.organization_id
        WHERE e.organization_id = $1::uuid AND o.organization_id = $1::uuid
    """, contact_query="""
        SELECT e.id, e.operation_id, e.event_type, e.created_at
        FROM gdpr_operation_events AS e
        JOIN gdpr_operations AS o ON o.id = e.operation_id
                                  AND o.organization_id = e.organization_id
        WHERE e.organization_id = $1::uuid AND o.organization_id = $1::uuid
          AND o.scope = 'contact' AND o.subject_id = $2::uuid
    """, account_query="""
        SELECT e.id, e.operation_id, e.event_type, e.created_at
        FROM gdpr_operation_events AS e
        JOIN gdpr_operations AS o ON o.id = e.operation_id
                                  AND o.organization_id = e.organization_id
        WHERE e.organization_id = $1::uuid AND o.organization_id = $1::uuid
          AND o.requester = $2::text
    """, exclusion_reason="Arbitrary event details JSON is excluded."),
    Category("gdpr_policies", """
        SELECT category, version, approval_reference, approved_at, valid_until,
               legal_hold, retention_days
        FROM gdpr_policies WHERE organization_id = $1::uuid
    """, exclusion_reason="Approver identity and erasure policy internals are excluded."),
    Category("gdpr_erasure_barriers", """
        SELECT operation_id, scope, subject_id, created_at
        FROM gdpr_erasure_barriers WHERE organization_id = $1::uuid
    """, contact_query="""
        SELECT operation_id, scope, subject_id, created_at
        FROM gdpr_erasure_barriers
        WHERE organization_id = $1::uuid AND scope = 'contact' AND subject_id = $2::uuid
    """, exclusion_reason="Identifier digest and key ID are excluded; no account subject projection."),
    Category("gdpr_export_chunks", "", exclusion_reason=
             "Encrypted recursive export artifacts are excluded from export."),
)
