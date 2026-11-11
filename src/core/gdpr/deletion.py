"""Resumable erasure with explicit policy and operational cutover gates.

No network side effects, billing changes or organization cascade deletes.
Local stages commit atomically with a durable production barrier. External
copies and legally retained records are reported separately and never guessed.
"""
import os
from uuid import UUID

from src.core.gdpr.c24_schema import conversation_schema
from src.core.gdpr.guard import identifier_fingerprint
from src.core.gdpr.operations import Operations, document, encode, event


class Deletion:
    def __init__(self, pool):
        self.pool = pool

    async def request(self, org, requester, request_id, *, scope='organization', subject_id=None):
        ops = Operations(self.pool)
        oid = await ops.create(org, requester, request_id, kind='erase', scope=scope, subject_id=subject_id)
        await self.advance(org, oid, requester)
        return await ops.get(org, oid, requester)

    async def advance(self, organization_id, operation_id, requester):
        org, oid = UUID(str(organization_id)), UUID(str(operation_id))
        async with self.pool.acquire() as conn, conn.transaction():
            # Match A03/A04 organization-first locking without blocking FK
            # KEY SHARE checks in intake writers already holding a shared fence.
            await conn.fetchrow('SELECT id FROM organizations WHERE id=$1 FOR NO KEY UPDATE', org)
            # All guarded writers acquire the shared counterpart before writes.
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1::text,606))', str(org))
            op = await conn.fetchrow(
                '''SELECT kind,scope,subject_id,status,manifest FROM gdpr_operations
                   WHERE organization_id=$1 AND id=$2 AND requester=$3 FOR UPDATE''', org, oid, str(requester),
            )
            if not op or op['kind'] != 'erase':
                raise LookupError('Erasure request not found')
            prior = await conn.fetchval(
                '''SELECT status FROM gdpr_operation_steps
                   WHERE organization_id=$1 AND operation_id=$2 AND name='local' ''', org, oid,
            )
            if prior == 'confirmed':
                return  # Never rerun committed mutations or provider calls.
            schema = await conversation_schema(conn)
            blockers = []
            if schema == 'partial':
                blockers.append('conversation_schema_incomplete')
            if schema == 'absent' and await conn.fetchval("""SELECT
                EXISTS(SELECT 1 FROM messages WHERE organization_id=$1
                       AND ai_reply_cache ? 'booking_state_proposal')
                OR EXISTS(SELECT 1 FROM simulation_requests WHERE organization_id=$1
                          AND response ? 'booking_state_proposal')""", org):
                blockers.append('unattributed_conversation_copy_review_required')
            if await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM external_booking_sync
                WHERE organization_id=$1 AND provider='google' AND calendar_dispatch_state='possibly_sent')""", org):
                blockers.append('calendar_sync_unresolved')
            if os.getenv('GDPR_ERASURE_EXECUTION_ENABLED') != 'true':
                blockers.append('operational_cutover_not_authorized')
            if op['scope'] == 'account':
                blockers.append('shared_account_and_auth_provider_review_required')
            policies = await conn.fetch(
                '''SELECT DISTINCT ON(category) category,version,erasure_allowed,legal_hold,approval_reference
                        ,valid_until>NOW() AS valid
                   FROM gdpr_policies WHERE organization_id=$1 AND approved_at<=NOW()
                   ORDER BY category,approved_at DESC,version DESC''', org,
            )
            policies = {p['category']: dict(p) for p in policies}
            required = {'contact_content', 'transient_content', 'cutover'}
            booking_stays_available = bool(await conn.fetchval(
                "SELECT to_regclass('public.booking_stays') IS NOT NULL",
            ))
            if booking_stays_available:
                if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM booking_stays
                    WHERE organization_id=$1 AND ($2::uuid IS NULL OR original_subject_id=$2))''', org, op['subject_id']):
                    required.add('booking_stay_content')
                    blockers.append('administrative_stay_purge_required')
            if op['scope'] == 'organization':
                required |= {'knowledge_content', 'organization_profile'}
            conversation_counts = {'states': 0, 'receipts': 0}
            if schema == 'ready':
                if op['scope'] == 'contact' and await conn.fetchval('''SELECT EXISTS(
                    SELECT 1 FROM simulation_requests WHERE organization_id=$1
                      AND response ? 'booking_state_proposal' AND c24_subject_id IS NULL)''', org):
                    # A historical or orphaned proposal cannot be attributed using
                    # today's contact linkage. Controller reconciliation is required.
                    blockers.append('unattributed_conversation_copy_review_required')
                conversation_counts = await conn.fetchrow(
                    '''SELECT
                      (SELECT count(*) FROM booking_conversation_states s
                       WHERE s.organization_id=$1 AND ($2::uuid IS NULL OR s.subject_id=$2)) AS states,
                      (SELECT count(*) FROM booking_conversation_requests r
                       LEFT JOIN booking_conversation_states s ON s.organization_id=r.organization_id
                         AND s.conversation_id=r.conversation_id AND s.organization_id=$1
                       WHERE r.organization_id=$1
                         AND ($2::uuid IS NULL OR COALESCE((to_jsonb(r)->>'original_subject_id')::uuid,s.subject_id)=$2)) AS receipts''', org, op['subject_id'],
                )
                if conversation_counts['receipts'] and await conn.fetchval(
                    '''SELECT EXISTS(SELECT 1 FROM pg_attribute
                       WHERE attrelid='public.booking_conversation_requests'::regclass
                         AND attname='original_subject_id' AND NOT attisdropped)''',
                ):
                    required.add('technical_receipts')
                    if not await conn.fetchval('''SELECT current_user=pg_get_userbyid(relowner)
                        FROM pg_class WHERE oid='public.c24_receipt_cleanup_capability'::regclass'''):
                        blockers.append('receipt_lifecycle_owner_required')
                    if await conn.fetchval('SELECT public.c24_receipts_held($1,$2)', org, op['subject_id']):
                        blockers.append('technical_receipts_held')
                # The policy covers *all* new copies, including a canonical response
                # which was persisted before any state repository call occurred.
                if conversation_counts['states'] or await conn.fetchval(
                    '''SELECT EXISTS(SELECT 1 FROM messages m
                       JOIN conversations c ON c.organization_id=m.organization_id AND c.id=m.conversation_id
                       WHERE m.organization_id=$1 AND c.organization_id=$1
                         AND ($2::uuid IS NULL OR c.contact_id=$2)
                         AND m.ai_reply_cache ? 'booking_state_proposal')
                     OR EXISTS(SELECT 1 FROM simulation_requests
                       WHERE organization_id=$1 AND response ? 'booking_state_proposal'
                         AND ($2::uuid IS NULL OR c24_subject_id=$2))''', org, op['subject_id'],
                ):
                    required.add('booking_conversation_content')
            for category in sorted(required):
                p = policies.get(category)
                if not p or not p['valid'] or p['legal_hold'] or not p['erasure_allowed']:
                    blockers.append('policy_required.' + category)
            # No age/lease heuristic can turn unknown provider consumption into
            # proof of no active work. All unresolved attempts block erasure.
            if await conn.fetchval(
                '''SELECT EXISTS(SELECT 1 FROM usage_events WHERE organization_id=$1
                     AND ((event_type='system.provider_attempt' AND metadata->>'accounting_status'='unresolved')
                       OR (event_type='system.quota_reservation' AND metadata->>'state'='reserved')))''', org,
            ):
                blockers.append('ai_attempt_or_quota_unresolved')
            if await conn.fetchval(
                '''SELECT EXISTS(SELECT 1 FROM messages WHERE organization_id=$1 AND status IN
                   ('processing','queued','sending_ambiguous','received_pending_ai'))''', org,
            ) or await conn.fetchval(
                '''SELECT EXISTS(SELECT 1 FROM meta_webhook_inbox WHERE organization_id=$1
                   AND status NOT IN ('completed','failed'))''', org,
            ):
                blockers.append('inbound_outbound_drain_required')
            if await conn.fetchval(
                '''SELECT EXISTS(SELECT 1 FROM bookings WHERE organization_id=$1
                   AND ($2::uuid IS NULL OR contact_id=$2) AND stato IN ('in_attesa','confermata'))''', org, op['subject_id'],
            ):
                blockers.append('active_booking_requires_controller_action')
            hotel_purge_required = blockers == ['administrative_stay_purge_required']
            if hotel_purge_required:
                # The organization advisory lock fences every conforming
                # writer while local GDPR redactions run. The durable barrier
                # is inserted below, immediately before the hotel capability
                # purge, because the ordinary redaction triggers reject writes
                # after that barrier exists.
                blockers.remove('administrative_stay_purge_required')
            if blockers:
                await conn.execute(
                    '''UPDATE gdpr_operations SET status='blocked',manifest=$3::jsonb,updated_at=NOW()
                       WHERE organization_id=$1 AND id=$2''', org, oid,
                    encode({'version': 'a06-v1', 'blockers': blockers, 'local_erasure': 'not_started',
                            'retained': ['billing', 'audit', 'provider_accounting', 'backups']}),
                )
                await event(conn, org, oid, 'erase.blocked', count=len(blockers))
                return
            # Capture provider dependencies BEFORE erasing locally identifiable
            # fields. Adapters must provide authenticated correlated receipts.
            providers = {'backups', 'worker_caches'}
            if (await conn.fetchval('SELECT EXISTS(SELECT 1 FROM whatsapp_accounts WHERE organization_id=$1)', org)
                    or await conn.fetchval('SELECT EXISTS(SELECT 1 FROM instagram_accounts WHERE organization_id=$1)', org)):
                providers.add('meta')
            if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM usage_events WHERE organization_id=$1 AND event_type=$2)', org, 'system.provider_attempt'):
                providers.add('ai')
            if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM google_calendar_credentials WHERE organization_id=$1)', org):
                providers.add('google')
            if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM google_business_credentials WHERE organization_id=$1)', org):
                providers.add('google_business')
            if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM airtable_connections WHERE organization_id=$1)', org):
                providers.add('airtable')
            if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM external_booking_credentials WHERE organization_id=$1)', org):
                providers.add('external_booking')
            # Configuration presence is evidence of possible copies, never
            # evidence that an external deletion has occurred. No values leave
            # this process and no remote account is modified here.
            for provider, setting in (('softr', 'SOFTR_WEBHOOK_URL'), ('sentry', 'SENTRY_DSN'),
                                      ('smtp', 'SMTP_HOST')):
                if os.getenv(setting):
                    providers.add(provider)
            if await conn.fetchval('SELECT stripe_customer_id IS NOT NULL FROM organizations WHERE id=$1', org):
                providers.add('stripe_retention_review')
            for provider in sorted(providers):
                await conn.execute(
                    '''INSERT INTO gdpr_operation_steps(organization_id,operation_id,name,status)
                       VALUES($1,$2,$3,'pending') ON CONFLICT DO NOTHING''', org, oid, 'provider.' + provider,
                )
            # Current configuration cannot prove the absence of historical
            # copies. Legal/subprocessor inventory review is an explicit hold,
            # not an adapter-callable stage or a guessed successful receipt.
            await conn.execute(
                '''INSERT INTO gdpr_operation_steps(organization_id,operation_id,name,status)
                   VALUES($1,$2,'provider.inventory_review','held') ON CONFLICT DO NOTHING''', org, oid,
            )
            # Any pre-erasure export may contain the subject: revoke snapshots
            # conservatively without deleting another subject's source records.
            await conn.execute(
                '''UPDATE gdpr_operations SET status='revoked',updated_at=NOW()
                   WHERE organization_id=$1 AND kind='export' AND status IN ('requested','ready','running')''', org,
            )
            await conn.execute('DELETE FROM gdpr_export_chunks WHERE organization_id=$1', org)
            subject = op['subject_id']
            contacts_count = await conn.fetchval(
                'SELECT count(*) FROM contacts WHERE organization_id=$1 AND ($2::uuid IS NULL OR id=$2)', org, subject,
            )
            subject_phone = await conn.fetchval(
                'SELECT phone_number FROM contacts WHERE organization_id=$1 AND id=$2', org, subject,
            ) if subject else None
            # Keep commercial, financial and security ledgers and their linkage.
            # Delete response/idempotency CONTENT, never reset the A04 counter.
            if subject is None:
                await conn.execute('DELETE FROM booking_confirmation_outbox WHERE organization_id=$1', org)
                await conn.execute('DELETE FROM outbound_dedup WHERE organization_id=$1', org)
                await conn.execute('DELETE FROM simulation_requests WHERE organization_id=$1', org)
                await conn.execute('DELETE FROM faq_cache WHERE organization_id=$1', org)
                await conn.execute('DELETE FROM document_chunks WHERE organization_id=$1', org)
                await conn.execute('DELETE FROM document_index_requests WHERE organization_id=$1', org)
                await conn.execute('DELETE FROM documents WHERE organization_id=$1', org)
                await conn.execute('DELETE FROM event_log WHERE organization_id=$1', org)
                await conn.execute('DELETE FROM meta_webhook_inbox WHERE organization_id=$1', org)
                await conn.execute('DELETE FROM airtable_webhook_events WHERE organization_id=$1', org)
                await conn.execute('DELETE FROM onboarding_profiles WHERE organization_id=$1', org)
                await conn.execute('UPDATE organizations SET business_profile=NULL WHERE id=$1', org)
            else:
                if schema == 'ready':
                    await conn.execute('DELETE FROM simulation_requests WHERE organization_id=$1 AND c24_subject_id=$2', org, subject)
                await conn.execute(
                    '''DELETE FROM booking_confirmation_outbox WHERE organization_id=$1 AND booking_id IN
                       (SELECT id FROM bookings WHERE organization_id=$1 AND contact_id=$2)''', org, subject,
                )
                await conn.execute(
                    '''DELETE FROM outbound_dedup WHERE organization_id=$1 AND message_id IN
                       (SELECT m.id FROM messages m JOIN conversations c ON c.id=m.conversation_id
                        AND c.organization_id=$1 WHERE m.organization_id=$1 AND c.contact_id=$2)''', org, subject,
                )
                await conn.execute(
                    '''DELETE FROM event_log WHERE organization_id=$1 AND source_id IN
                       (SELECT m.id FROM messages m JOIN conversations c ON c.id=m.conversation_id
                        AND c.organization_id=$1 WHERE m.organization_id=$1 AND c.contact_id=$2)''', org, subject,
                )
                # A04 inbound response keys are derived from immutable source IDs.
                from src.core.billing.quota_reservations import QuotaOperation
                messages = conn.cursor(
                    '''SELECT m.id FROM messages m JOIN conversations c ON c.id=m.conversation_id AND c.organization_id=$1
                       WHERE m.organization_id=$1 AND c.contact_id=$2''', org, subject,
                )
                async for message in messages:
                    operation = QuotaOperation(org, UUID(str(message['id'])), 'inbound', '0' * 64)
                    await conn.execute(
                        'DELETE FROM simulation_requests WHERE organization_id=$1 AND request_id=$2', org, operation.id,
                    )
            if booking_stays_available:
                # Keep hotel booking content until its scoped, policy-checked
                # purge capability runs below. On older schemas the relation
                # does not exist, so retain the legacy relational erasure path.
                await conn.execute(
                    '''UPDATE bookings SET nome_cliente='REDACTED',telefono='',note='',id_conversazione=NULL
                       WHERE organization_id=$1 AND ($2::uuid IS NULL OR contact_id=$2)
                         AND NOT EXISTS(SELECT 1 FROM booking_stays s
                           WHERE s.organization_id=$1 AND s.organization_id=bookings.organization_id
                             AND s.booking_id=bookings.id)''', org, subject,
                )
            else:
                await conn.execute(
                    '''UPDATE bookings SET nome_cliente='REDACTED',telefono='',note='',id_conversazione=NULL
                       WHERE organization_id=$1 AND ($2::uuid IS NULL OR contact_id=$2)''', org, subject,
                )
            await conn.execute(
                '''UPDATE reviews SET autore='REDACTED',testo='',bozza_risposta='',is_anonymized=TRUE
                   WHERE organization_id=$1 AND ($2::uuid IS NULL OR contact_id=$2)''', org, subject,
            )
            await conn.execute(
                '''DELETE FROM contact_consent_log AS l USING contacts AS c
                   WHERE l.contact_id=c.id AND c.organization_id=$1 AND ($2::uuid IS NULL OR c.id=$2)''', org, subject,
            )
            # Keep message identifiers needed by bookings/ledger FK chains, but
            # erase their content and derived feedback/delivery diagnostics.
            await conn.execute(
                '''DELETE FROM message_delivery_attempts AS a USING messages AS m
                   JOIN conversations AS c ON c.id=m.conversation_id AND c.organization_id=$1
                   WHERE a.message_id=m.id AND m.organization_id=$1
                     AND ($2::uuid IS NULL OR c.contact_id=$2)''', org, subject,
            )
            await conn.execute(
                '''DELETE FROM message_feedback WHERE organization_id=$1 AND conversation_id IN
                   (SELECT id FROM conversations WHERE organization_id=$1 AND ($2::uuid IS NULL OR contact_id=$2))''', org, subject,
            )
            await conn.execute(
                '''UPDATE messages SET content='{}',content_text=NULL,ai_reply_cache=NULL,error_details=NULL,
                       error_title=NULL,deleted_at=COALESCE(deleted_at,NOW())
                   WHERE organization_id=$1 AND conversation_id IN
                     (SELECT id FROM conversations WHERE organization_id=$1 AND ($2::uuid IS NULL OR contact_id=$2))''', org, subject,
            )
            await conn.execute(
                '''UPDATE contacts SET phone_number='erased:'||id::text,deleted_at=COALESCE(deleted_at,NOW())
                   WHERE organization_id=$1 AND ($2::uuid IS NULL OR id=$2)''', org, subject,
            )
            if subject:
                digest, key_id = identifier_fingerprint(org, subject_phone)
            else:
                digest, key_id = None, None
            await conn.execute(
                '''INSERT INTO gdpr_erasure_barriers(organization_id,operation_id,scope,subject_id,identifier_digest,key_id)
                   VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(organization_id,operation_id) DO NOTHING''', org, oid, op['scope'], subject, digest, key_id,
            )
            if hotel_purge_required:
                hotel_policy = policies['booking_stay_content']
                stays = await conn.fetch(
                    '''SELECT booking_id FROM booking_stays
                       WHERE organization_id=$1
                         AND ($2::uuid IS NULL OR original_subject_id=$2)
                       ORDER BY booking_id''', org, subject,
                )
                for stay in stays:
                    # The role switch is limited to this one capability call.
                    # On error the enclosing transaction rolls back both
                    # redactions, barrier, and any earlier hotel purges.
                    await conn.execute('SET LOCAL ROLE service_role')
                    await conn.fetchval(
                        '''SELECT public.c24_purge_hotel_for_erasure(
                             $1::uuid,$2::uuid,$3::uuid,$4::text)''',
                        org, stay['booking_id'], oid, str(hotel_policy['version']),
                    )
                    await conn.execute('RESET ROLE')
            await conn.execute(
                '''INSERT INTO gdpr_operation_steps(organization_id,operation_id,name,status,attempts)
                   VALUES($1,$2,'local','confirmed',1) ON CONFLICT(organization_id,operation_id,name)
                   DO UPDATE SET status='confirmed',attempts=gdpr_operation_steps.attempts+1,updated_at=NOW()''', org, oid,
            )
            manifest = {'version': 'a06-v1', 'local_erasure': 'approved_relational_data_erased',
                        'external_copies': 'pending_verified_receipts',
                        'retained': ['billing', 'audit', 'provider_accounting', 'shared_auth_accounts', 'backups',
                                     'gdpr_erasure_barriers', 'booking_conversation_revocations_pending_lifecycle',
                                     'conversation_suppression_lifecycle_audit_pending_expiry',
                                     'booking_stay_replay_fences_pending_policy'],
                        'subject_unstructured_data': 'controller_review_required' if subject else 'tenant_scoped',
                        'policies': {k: {'version': p['version'], 'reference': p['approval_reference']} for k,p in policies.items()}}
            await conn.execute(
                '''UPDATE gdpr_operations SET status='partial',manifest=$3::jsonb,updated_at=NOW()
                   WHERE organization_id=$1 AND id=$2''', org, oid, encode(manifest),
            )
            if subject:
                from src.core.gdpr.guard import filter_webhook_payload
                async for row in conn.cursor(
                    'SELECT id,payload FROM meta_webhook_inbox WHERE organization_id=$1 AND status=$2', org, 'completed',
                ):
                    clean = await filter_webhook_payload(conn, org, document(row['payload']))
                    await conn.execute(
                        '''UPDATE meta_webhook_inbox SET payload=$3::jsonb,last_error=NULL
                           WHERE organization_id=$1 AND id=$2''', org, row['id'], encode(clean or {}),
                    )
            await event(conn, org, oid, 'erase.local_committed', count=contacts_count,
                        conversation_states=conversation_counts['states'],
                        conversation_receipts=conversation_counts['receipts'])
        # Process-local simulator histories are keyed by staff/session, not a
        # verified data-subject ID. Clear this tenant's entire ephemeral cache
        # after any committed erasure rather than guessing which session may
        # contain the subject. Other process/worker copies remain a separately
        # evidenced lifecycle stage.
        from src.core.conversation_store import store
        store.erase_organization(str(org))
