"""Draft-only, disconnected conversation metadata. No operational writer calls.

New state and structured canonical-response retention await C05/A06 approval.
Only a real current owner/manager can call this administrative repository.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from src.core.billing.quota_reservations import QuotaOperation, _decode, _hash
from src.core.bookings.catalog_contracts import ScopedCatalog
from src.core.bookings.conversation_state import (
    ConversationState, StateConflict, canonical, confirm_details, digest,
    explicit_customer_fields, medical_source_requires_handoff, new_state,
    parse_proposal_json, reduce_proposal, render_confirmation_prompt, state_digest,
    validate_proposal_catalog,
)
from src.core.db.quota_provenance import require_quota_trust_epoch
from src.core.db.inbound_worker_provenance import private_booking_worker_enabled
from src.core.db.repositories.booking_allocation_repo import BookingAllocationRepository
from src.core.db.repositories.booking_catalog_repo import decode


class ConversationStateDenied(PermissionError):
    pass


class ConversationStateUnavailable(RuntimeError):
    pass


def _uuid(value):
    try:
        result = UUID(str(value))
        if result.int == 0: raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise ConversationStateDenied('Invalid trusted state context') from None


class BookingConversationRepository:
    def __init__(self, pool):
        self.pool = pool
        self._allocation = BookingAllocationRepository(pool)

    @asynccontextmanager
    async def confirmation_prompt_delivery_guard(self, organization_id, conversation_id, prompt_id):
        """Hold the authoritative conversation and consent through delivery."""
        prompt_id = _uuid(prompt_id)
        async with self._scope(organization_id, None, conversation_id, worker=True) as context:
            conn, org, conversation, _, scoped, _ = context
            state, row = await self._load(conn, org, conversation)
            if (not state or state.intent != 'booking' or state.confirmation
                    or state.missing_fields or state.ambiguous_fields or state.handoff_reason
                    or state.catalog_revision != scoped.catalog.revision):
                yield False
                return
            prompt = await conn.fetchrow("""SELECT content,content_text FROM messages
                WHERE organization_id=$1 AND conversation_id=$2 AND id=$3 AND direction='outbound'
                  AND handling_type='c24_confirmation_prompt' AND deleted_at IS NULL""",
                org, conversation, prompt_id,
            )
            payload = decode(prompt['content']) if prompt else {}
            phone = await conn.fetchval('SELECT phone_number FROM contacts WHERE organization_id=$1 AND id=$2',
                                        org, row['subject_id'])
            eligible = (isinstance(payload, dict) and bool(phone) and payload.get('to') == phone
                        and payload.get('_c24_subject_id') == str(row['subject_id'])
                        and payload.get('_c24_state_digest') == state_digest(state)
                        and payload.get('_c24_source_id') == str(state.last_source_message_id)
                        and prompt['content_text'] == render_confirmation_prompt(state, prompt_id))
            if eligible:
                eligible = not await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM messages m
                    JOIN messages origin ON origin.organization_id=m.organization_id
                     AND origin.conversation_id=m.conversation_id AND origin.id=$3
                    WHERE m.organization_id=$1 AND origin.organization_id=$1 AND m.conversation_id=$2
                      AND m.direction='inbound' AND m.id<>origin.id AND m.created_at>=origin.created_at)""",
                    org, conversation, state.last_source_message_id,
                )
            yield eligible

    @asynccontextmanager
    async def _scope(self, organization_id, auth_user_id, conversation_id, *, worker=False):
        org, conversation = map(_uuid, (organization_id, conversation_id))
        auth = None if worker else _uuid(auth_user_id)
        async with self.pool.acquire() as conn, conn.transaction(isolation='read_committed'):
            organization = await conn.fetchrow('SELECT * FROM organizations WHERE id=$1 FOR SHARE', org)
            if not organization:
                raise ConversationStateDenied('Organization unavailable')
            await conn.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(org))
            if worker:
                if not private_booking_worker_enabled() or not await conn.fetchval("""SELECT
                    current_user=pg_get_userbyid(relowner) FROM pg_class
                    WHERE oid='public.inbound_worker_operations'::regclass"""):
                    raise ConversationStateDenied('Private conversation worker unavailable')
                actor = None
            else:
                actor = await self._allocation._actor(conn, org, auth)
            conv = await conn.fetchrow('SELECT * FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE', org, conversation)
            if not conv or conv['deleted_at'] is not None or conv['status'] != 'active' or conv['canale'] != 'whatsapp':
                raise ConversationStateDenied('Live WhatsApp conversation required')
            if worker and conv['ticket_status'] != 'AI_ACTIVE':
                raise ConversationStateDenied('Conversation is with staff')
            contact = await conn.fetchrow('SELECT id,deleted_at,consent_status FROM contacts WHERE organization_id=$1 AND id=$2 FOR SHARE', org, conv['contact_id'])
            if not contact or contact['deleted_at'] is not None or contact['consent_status'] == 'withdrawn':
                raise ConversationStateDenied('Contact access unavailable')
            await self._allocation._gdpr(conn, org, conv['contact_id'])
            await conn.fetchval('SELECT pg_advisory_xact_lock_shared(hashtext($1))', f'booking-capacity|{org}')
            settings = await conn.fetchrow('SELECT config FROM booking_settings WHERE organization_id=$1 FOR SHARE', org)
            catalog, sector, authority = await self._allocation._catalog(conn, org, organization, settings)
            if catalog.sector != sector or catalog.timezone != organization['timezone']:
                raise ConversationStateUnavailable('Catalog scope changed')
            yield conn, org, conversation, actor, ScopedCatalog(org, catalog), authority

    @staticmethod
    async def _source(conn, org, conversation, source_message_id):
        source = await conn.fetchrow('''SELECT id,conversation_id,wam_id,content,content_text,
            direction,deleted_at,status,billed_at,ai_reply_cache,claimed_at,heartbeat_at,c24_original_subject_id,
            clock_timestamp() AS verified_now, to_jsonb(messages)->>'content_purged_at' AS content_purged_at FROM messages
            WHERE organization_id=$1 AND conversation_id=$2 AND id=$3 FOR SHARE''', org, conversation, source_message_id)
        if not source or source['direction'] != 'inbound' or source['deleted_at'] is not None or source['content_purged_at'] is not None:
            raise ConversationStateDenied('Live inbound source required')
        if source['c24_original_subject_id'] is None or source['c24_original_subject_id'] != await conn.fetchval(
                'SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2', org, conversation):
            raise ConversationStateDenied('Original source subject requires reconciliation')
        try:
            body = decode(source['content'])
            stamp = body['timestamp']
            text = source['content_text']
            if (not isinstance(body, dict) or type(stamp) is not str or not re.fullmatch(r'[0-9]{1,12}', stamp)
                    or int(stamp) <= 0 or int(stamp) > int(source['verified_now'].timestamp()) + 300
                    or not source['wam_id'] or body.get('id') != source['wam_id']
                    or body.get('type') != 'text' or type(text) is not str or len(text.encode()) > 65536
                    or body.get('text', {}).get('body') != text):
                raise ValueError()
            return source, int(stamp), digest(body)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ConversationStateUnavailable('Source timestamp or provenance is unavailable') from None

    @staticmethod
    async def _canonical_response(conn, org, source):
        """Read-only A04 proof, never reserve/renew/commit or a provider call."""
        source_id = _uuid(source['id'])
        payload_hash = digest({'organization_id': str(org), 'message_id': str(source_id),
                               'channel': 'whatsapp', 'text': source['content_text']})
        operation = QuotaOperation(org, source_id, 'inbound', payload_hash)
        epoch = await require_quota_trust_epoch(conn)
        entry = await conn.fetchrow('SELECT event_type,quantity,metadata FROM usage_events WHERE organization_id=$1 AND id=$2 FOR SHARE', org, operation.id)
        receipt = await conn.fetchrow('''SELECT payload_hash,claim_token,status,response FROM simulation_requests
            WHERE organization_id=$1 AND auth_user_id=$2 AND request_id=$3 FOR SHARE''', org, operation.actor_id, operation.id)
        try:
            metadata = _decode(entry, operation)
            cached = decode(source['ai_reply_cache'])
            stored = decode(receipt['response']) if receipt else None
            if (metadata['state'] != 'committed' or metadata['epoch'] != str(epoch)
                    or source['billed_at'] is None or not isinstance(cached, dict) or not isinstance(stored, dict)
                    or not receipt or receipt['status'] != 'completed'
                    or receipt['claim_token'] != UUID(metadata['claim_id'])
                    or receipt['payload_hash'].strip() != payload_hash
                    or _hash(cached) != metadata['response_hash'] or _hash(stored) != metadata['response_hash']
                    or len(canonical(cached).encode()) > 131072):
                raise ValueError()
            return cached, metadata['response_hash']
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
            raise ConversationStateUnavailable('Canonical A04 evidence requires reconciliation') from None

    @staticmethod
    async def _load(conn, org, conversation):
        # Parent source locks precede the child row lock, matching FK erasure.
        row = await conn.fetchrow("SELECT *, updated_at <= clock_timestamp() - interval '30 days' AS idle_expired FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2", org, conversation)
        if not row: return None, None
        if row['idle_expired']:
            raise ConversationStateUnavailable('Idle state requires lifecycle cleanup')
        try:
            state = ConversationState.model_validate_json(canonical(decode(row['snapshot'])))
            if (state.organization_id != org or state.conversation_id != conversation
                    or row['subject_id'] != await conn.fetchval('SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2', org, conversation)
                    or state.revision != row['revision'] or state.catalog_revision != row['catalog_revision']
                    or state.last_source_message_id != row['source_message_id']
                    or state_digest(state) != row['snapshot_hash']):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ConversationStateUnavailable('Stored state requires reconciliation') from None
        # Deletion, tenant transfer or source reassignment cannot make replay safe.
        source_ids = ({state.last_source_message_id} | {s.source_message_id for s in state.slots}
                      | {s.source_message_id for s in state.superseded}
                      | ({state.confirmation.source_message_id} if state.confirmation else set()))
        for source_id in sorted(source_ids, key=str):
            provenance = await conn.fetchrow('''SELECT direction,deleted_at,c24_original_subject_id,
                to_jsonb(messages)->>'content_purged_at' AS content_purged_at FROM messages
                WHERE organization_id=$1 AND conversation_id=$2 AND id=$3 FOR SHARE''', org, conversation, source_id)
            if (not provenance or provenance['direction'] != 'inbound' or provenance['deleted_at'] is not None
                    or provenance['c24_original_subject_id'] != row['subject_id']):
                raise ConversationStateUnavailable('Stored source provenance is unavailable')
            if provenance['content_purged_at'] is None:
                await BookingConversationRepository._source(conn, org, conversation, source_id)
        if state.confirmation:
            proof = state.confirmation
            prompt = await conn.fetchrow('''SELECT direction,status,sent_at,deleted_at,content_text,
                to_jsonb(messages)->>'content_purged_at' AS content_purged_at FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND id=$3 FOR SHARE''',
                org, conversation, proof.prompt_message_id)
            if prompt and prompt['content_purged_at'] is not None and prompt['deleted_at'] is None:
                state = state.model_copy(update={'confirmation': None})
            elif (not prompt or prompt['direction'] != 'outbound' or prompt['status'] not in {'sent','delivered','read'}
                    or prompt['sent_at'] is None or prompt['deleted_at'] is not None
                    or hashlib.sha256((prompt['content_text'] or '').encode()).hexdigest() != proof.prompt_body_hash):
                raise ConversationStateUnavailable('Confirmation evidence is no longer live')
        locked = await conn.fetchrow('SELECT snapshot_hash FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2 FOR UPDATE', org, conversation)
        if not locked or locked['snapshot_hash'] != row['snapshot_hash']:
            raise StateConflict('State lifecycle changed while reading')
        return state, row

    async def read(self, organization_id, auth_user_id, conversation_id):
        async with self._scope(organization_id, auth_user_id, conversation_id) as context:
            conn, org, conversation, _actor, catalog, _authority = context
            state, _ = await self._load(conn, org, conversation)
            return self._result(state, state.revision if state else None, False, catalog.catalog.revision)

    async def observe_candidate(self, organization_id, auth_user_id, conversation_id,
                                *, expected_state_revision, expected_catalog_revision,
                                expected_state_digest):
        """Diagnostic only. Own no writes and grant no operational admission."""
        from src.core.bookings.allocation_contracts import AllocationCandidate, derive_candidate
        from src.core.bookings.auto_policy import validate_auto_booking_configuration
        if (type(expected_state_revision) is not int or expected_state_revision < 1
                or type(expected_catalog_revision) is not int or expected_catalog_revision < 1
                or type(expected_state_digest) is not str
                or not re.fullmatch(r'[a-f0-9]{64}', expected_state_digest)):
            raise ValueError('Strict observation expectations required')
        async with self._scope(organization_id, auth_user_id, conversation_id) as context:
            conn, org, conversation, _actor, scoped, authority = context
            catalog = scoped.catalog
            state, row = await self._load(conn, org, conversation)
            if (not state or state.revision != expected_state_revision
                    or state.catalog_revision != expected_catalog_revision
                    or catalog.revision != expected_catalog_revision
                    or state_digest(state) != expected_state_digest):
                raise StateConflict('Observation snapshot changed')
            # All surviving slot sources, not just the most recent turn, must
            # still have the same committed canonical proposal and source body.
            proposals = {}
            source_ids = {slot.source_message_id for slot in state.slots}
            latest_receipt = await conn.fetchrow('''SELECT action FROM booking_conversation_requests
                WHERE organization_id=$1 AND conversation_id=$2 AND source_message_id=$3''',
                org, conversation, state.last_source_message_id)
            if not latest_receipt:
                raise ConversationStateUnavailable('Observation receipt unavailable')
            if latest_receipt['action'] == 'proposal':
                source_ids.add(state.last_source_message_id)
            for source_id in sorted(source_ids, key=str):
                source, _stamp, source_hash = await self._source(conn, org, conversation, source_id)
                cached, canonical_hash = await self._canonical_response(conn, org, source)
                receipt = await conn.fetchrow('''SELECT * FROM booking_conversation_requests
                    WHERE organization_id=$1 AND conversation_id=$2 AND source_message_id=$3''',
                    org, conversation, source_id)
                if (not receipt or receipt['action'] != 'proposal'
                        or receipt['canonical_response_hash'] != canonical_hash
                        or cached.get('booking_catalog_revision') != catalog.revision
                        or type(cached.get('booking_catalog_revision')) is not int
                        or cached.get('richiede_umano') is not False):
                    raise ConversationStateUnavailable('Observation canonical evidence changed')
                operation_hash = digest({'actor':str(receipt['actor_user_id']),
                    'conversation':str(conversation), 'source':str(source_id), 'source_hash':source_hash,
                    'canonical_hash':canonical_hash, 'expected_revision':receipt['applied_revision']-1,
                    'catalog':catalog.revision, 'action':'proposal', 'prompt':None, 'prompt_hash':None})
                if operation_hash != receipt['payload_hash']:
                    raise ConversationStateUnavailable('Observation source evidence changed')
                proposal = parse_proposal_json(canonical(cached.get('booking_state_proposal')))
                validate_proposal_catalog(proposal, catalog)
                proposals[source_id] = (proposal, receipt['applied_revision'],
                    explicit_customer_fields(proposal, source['content_text'], catalog))
            values = {slot.field:slot.value for slot in state.slots}
            for slot in state.slots:
                proposal, revision, explicit = proposals[slot.source_message_id]
                if slot.source_revision != revision or slot.catalog_revision != catalog.revision:
                    raise ConversationStateUnavailable('Slot evidence revision changed')
                if slot.origin == 'backend_derived':
                    from datetime import date, timedelta
                    if (slot.field != 'departure_date' or slot.derived_from != ('arrival_date','stay_nights')
                            or 'arrival_date' not in values or 'stay_nights' not in values
                            or slot.value != (date.fromisoformat(values['arrival_date'])
                                + timedelta(days=values['stay_nights'])).isoformat()):
                        raise ConversationStateUnavailable('Derived slot evidence changed')
                elif (slot.derived_from or not any(c.field == slot.field and c.value == slot.value for c in proposal.slots)
                        or (slot.origin == 'customer_provided' and slot.field not in explicit)):
                    raise ConversationStateUnavailable('Slot canonical provenance changed')
            settings = await conn.fetchrow('SELECT * FROM booking_settings WHERE organization_id=$1', org)
            organization = await conn.fetchrow('SELECT timezone,business_profile FROM organizations WHERE id=$1', org)
            config = decode(settings['config']) if settings else {}
            policy = config.get('a05', {}) if isinstance(config, dict) else {}
            opt_in = (isinstance(policy, dict) and policy.get('enabled') is True
                and validate_auto_booking_configuration({**dict(settings), 'config':config,
                    'capienze_orarie':decode(settings['capienze_orarie'])}, organization['timezone'], catalog.sector).valid)
            result = {'state_revision':state.revision, 'state_digest':state_digest(state),
                'catalog_revision':catalog.revision, 'allocation_observation':'unknown',
                'operational_availability':'unknown', 'opt_in_authorized':bool(opt_in),
                'authority':authority, 'reason':'legacy_occupancy_unreconciled' if opt_in else 'opt_in_missing',
                'runtime_enabled':False, 'confirmation_authorized':False}
            if state.intent != 'booking' or state.handoff_reason or state.missing_fields or state.ambiguous_fields:
                return {**result, 'reason':'state_requires_clarification'}
            # Newer/tied or malformed inbound order cannot silently reuse an old
            # state. This bounded diagnostic never substitutes created_at order.
            sources = await conn.fetch('''SELECT id,content FROM messages WHERE organization_id=$1
                AND conversation_id=$2 AND direction='inbound' AND deleted_at IS NULL LIMIT 257''', org, conversation)
            if len(sources) > 256:
                return {**result, 'reason':'source_order_uncertain'}
            for source in sources:
                if source['id'] == state.last_source_message_id: continue
                body = decode(source['content'])
                stamp = body.get('timestamp') if isinstance(body, dict) else None
                if (type(stamp) is not str or not re.fullmatch(r'[0-9]{1,12}', stamp)
                        or int(stamp) >= row['provider_timestamp']):
                    return {**result, 'reason':'source_order_uncertain'}
            try:
                candidate = AllocationCandidate.model_validate_json(canonical({
                    'expected_catalog_revision':catalog.revision,
                    **{key:value for key,value in values.items() if key in AllocationCandidate.model_fields}}))
                start, end, claims = derive_candidate(candidate, catalog)
            except (ValueError, TypeError, OverflowError):
                return {**result, 'reason':'candidate_unavailable'}
            # Explicit None means a new candidate: exclude no existing booking.
            # _availability is read-only and stays on this scoped connection.
            observation = await self._allocation._observe_candidate_locked(conn, org, catalog, candidate)
            return {**result, 'allocation_observation':observation,
                    'candidate':{'service_id':candidate.service_id, 'starts_at':start.isoformat(),
                                 'ends_at':end.isoformat(), 'claims':claims}}

    async def apply_from_a04(self, organization_id, auth_user_id, conversation_id, source_message_id,
                             *, request_id, expected_revision, expected_catalog_revision):
        return await self._apply(organization_id, auth_user_id, conversation_id, source_message_id,
            request_id=request_id, expected_revision=expected_revision,
            expected_catalog_revision=expected_catalog_revision, prompt_message_id=None)

    async def confirm(self, organization_id, auth_user_id, conversation_id, source_message_id,
                      prompt_message_id, *, request_id, expected_revision, expected_catalog_revision):
        return await self._apply(organization_id, auth_user_id, conversation_id, source_message_id,
            request_id=request_id, expected_revision=expected_revision,
            expected_catalog_revision=expected_catalog_revision, prompt_message_id=_uuid(prompt_message_id))

    async def apply_worker_source(self, organization_id, conversation_id, source_message_id):
        """Use only persisted inbound evidence and current server revisions."""
        org, conversation, source = map(_uuid, (organization_id, conversation_id, source_message_id))
        request = uuid5(NAMESPACE_URL, f'melpis:c24:conversation:{org}:{conversation}:{source}')
        return await self._apply(org, None, conversation, source, request_id=request,
                                 expected_revision=None, expected_catalog_revision=None,
                                 prompt_message_id=None, worker=True)

    async def _apply(self, organization_id, auth_user_id, conversation_id, source_message_id,
                     *, request_id, expected_revision, expected_catalog_revision, prompt_message_id, worker=False):
        source_id, request = map(_uuid, (source_message_id, request_id))
        if not worker and (type(expected_revision) is not int or expected_revision < 0
                           or type(expected_catalog_revision) is not int):
            raise ValueError('Strict expected revisions required')
        async with self._scope(organization_id, auth_user_id, conversation_id, worker=worker) as context:
            conn, org, conversation, actor, scoped, authority = context
            catalog = scoped.catalog
            if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM booking_conversation_revocations WHERE organization_id=$1 AND conversation_id=$2)
                OR EXISTS(SELECT 1 FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND deleted_at IS NOT NULL)''', org, conversation):
                raise ConversationStateDenied('Conversation metadata was revoked')
            source, provider_time, source_hash = await self._source(conn, org, conversation, source_id)
            if worker:
                prior = await conn.fetchrow('''SELECT applied_revision FROM booking_conversation_requests
                    WHERE organization_id=$1 AND conversation_id=$2 AND request_id=$3''', org, conversation, request)
                expected_revision = (prior['applied_revision'] - 1 if prior else await conn.fetchval(
                    'SELECT revision FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2',
                    org, conversation,
                ) or 0)
                expected_catalog_revision = catalog.revision
                match = re.fullmatch(r'CONFIRM ([0-9a-f-]{36})', source['content_text'])
                if match:
                    prompt_message_id = _uuid(match[1])
            action = 'proposal' if prompt_message_id is None else 'confirmation'
            if worker:
                provenance = await conn.fetch('''SELECT r.inbox_id,o.operation_id FROM inbound_worker_receipts r
                    JOIN meta_webhook_inbox i ON i.organization_id=r.organization_id AND i.id=r.inbox_id
                    LEFT JOIN inbound_worker_operations o ON o.organization_id=$1
                      AND o.inbox_id=r.inbox_id AND o.message_id=r.message_id AND $3='proposal'
                    WHERE r.organization_id=$1 AND i.organization_id=$1 AND r.message_id=$2
                      AND i.channel='whatsapp' AND i.status='completed'
                      AND ($3='confirmation' OR o.operation_id IS NOT NULL)
                    LIMIT 2 FOR SHARE OF r,i''', org, source_id, action)
                if len(provenance) != 1:
                    raise ConversationStateDenied('Unambiguous worker source unavailable')
                evidence = provenance[0]
            canonical_hash, proposal, prompt_hash = None, None, None
            prompt = None
            if action == 'proposal':
                cached, canonical_hash = await self._canonical_response(conn, org, source)
                try:
                    proposal = parse_proposal_json(canonical(cached['booking_state_proposal']))
                    response_revision = cached['booking_catalog_revision']
                    if type(response_revision) is not int or response_revision != expected_catalog_revision:
                        raise ValueError()
                except (KeyError, ValueError, TypeError):
                    raise ConversationStateUnavailable('Canonical structured proposal is unavailable') from None
            else:
                prompt = await conn.fetchrow('''SELECT direction,deleted_at,status,sent_at,content_text,wam_id
                    FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND id=$3 FOR SHARE''', org, conversation, prompt_message_id)
                if (not prompt or prompt['direction'] != 'outbound' or prompt['deleted_at'] is not None
                        or prompt['status'] not in {'sent','delivered','read'} or prompt['sent_at'] is None
                        or not prompt['wam_id'] or prompt_message_id.version != 4
                        or source['content_text'] != f'CONFIRM {prompt_message_id}'
                        or provider_time <= int(prompt['sent_at'].timestamp())):
                    raise ConversationStateDenied('Verified sent prompt and explicit later reply required')
                prompt_hash = hashlib.sha256(prompt['content_text'].encode()).hexdigest()
            operation_hash = digest({'actor':str(actor), 'conversation':str(conversation),
                'source':str(source_id), 'source_hash':source_hash, 'canonical_hash':canonical_hash,
                'expected_revision':expected_revision, 'catalog':expected_catalog_revision,
                'action':action, 'prompt':str(prompt_message_id) if prompt_message_id else None, 'prompt_hash':prompt_hash})
            await conn.fetchval('SELECT pg_advisory_xact_lock(hashtextextended($1,731))', f'conversation-state|{org}|{request}')
            previous = await conn.fetch('''SELECT * FROM booking_conversation_requests
                WHERE organization_id=$1 AND (request_id=$2 OR (conversation_id=$3 AND source_message_id=$4))''', org, request, conversation, source_id)
            state, row = await self._load(conn, org, conversation)
            if previous:
                if any(p['payload_hash'] != operation_hash or p['actor_user_id'] != actor for p in previous):
                    raise StateConflict('State request or source was used for another operation')
                if row is None or any(p.get('state_generation') != row.get('state_generation') for p in previous):
                    raise StateConflict('State request belongs to an expired conversation generation')
                return self._result(state, previous[0]['applied_revision'], True, catalog.revision)
            if expected_catalog_revision != catalog.revision:
                raise StateConflict('Published catalog revision changed')
            if state is None: state = new_state(org, conversation, scoped)
            if state.revision != expected_revision:
                raise StateConflict('Conversation state revision changed')
            if state.catalog_revision != catalog.revision:
                raise StateConflict('Catalog changed; explicit re-proposal requires review')
            if row and provider_time <= row['provider_timestamp']:
                raise StateConflict('Source order is tied, delayed or uncertain')
            if action == 'proposal':
                validate_proposal_catalog(proposal, catalog)
                updated = reduce_proposal(state, proposal, source_message_id=source_id, catalog=scoped,
                    expected_revision=expected_revision,
                    explicit_fields=explicit_customer_fields(proposal, source['content_text'], catalog),
                    medical_handoff=medical_source_requires_handoff(source['content_text'], catalog))
                if cached.get('richiede_umano') is not False:
                    updated = updated.model_copy(update={'handoff_reason':updated.handoff_reason or 'outside_scope'})
            else:
                if prompt['content_text'] not in (render_confirmation_prompt(state, prompt_message_id),
                        render_confirmation_prompt(state, prompt_message_id, legacy=True)):
                    raise StateConflict('Prompt does not match current state snapshot')
                updated = confirm_details(state, source_message_id=source_id, prompt_message_id=prompt_message_id,
                    prompt_body_hash=prompt_hash, expected_revision=expected_revision)
            stored_revision = await conn.fetchval('''INSERT INTO booking_conversation_states
                (organization_id,conversation_id,revision,catalog_revision,source_message_id,provider_timestamp,snapshot,snapshot_hash,subject_id)
                VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,
                  (SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2))
                ON CONFLICT(organization_id,conversation_id) DO UPDATE SET revision=EXCLUDED.revision,
                  catalog_revision=EXCLUDED.catalog_revision,source_message_id=EXCLUDED.source_message_id,
                  provider_timestamp=EXCLUDED.provider_timestamp,snapshot=EXCLUDED.snapshot,
                  snapshot_hash=EXCLUDED.snapshot_hash,updated_at=clock_timestamp()
                  WHERE booking_conversation_states.revision=$9 RETURNING revision''',
                org, conversation, updated.revision, catalog.revision, source_id, provider_time,
                canonical(updated.model_dump(mode='json')), state_digest(updated), expected_revision)
            if stored_revision != updated.revision:
                raise StateConflict('State compare-and-swap failed')
            if worker:
                await conn.execute('''INSERT INTO booking_conversation_requests
                    (organization_id,request_id,conversation_id,source_message_id,actor_user_id,actor_kind,
                     service_inbox_id,service_operation_id,action,
                     payload_hash,applied_revision,canonical_response_hash,prompt_message_id,prompt_body_hash)
                    VALUES($1,$2,$3,$4,NULL,'inbound_worker',$5,$6,$7,$8,$9,$10,$11,$12)''',
                    org, request, conversation, source_id, evidence['inbox_id'], evidence['operation_id'],
                    action, operation_hash, updated.revision,
                    canonical_hash, prompt_message_id, prompt_hash)
            else:
                await conn.execute('''INSERT INTO booking_conversation_requests
                (organization_id,request_id,conversation_id,source_message_id,actor_user_id,action,
                 payload_hash,applied_revision,canonical_response_hash,prompt_message_id,prompt_body_hash)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)''', org, request, conversation, source_id,
                    actor, action, operation_hash, updated.revision, canonical_hash, prompt_message_id, prompt_hash)
            if (worker and action == 'proposal' and authority == 'internal'
                    and catalog.sector == 'ristorante' and updated.intent == 'booking'
                    and not updated.handoff_reason and not updated.missing_fields
                    and not updated.ambiguous_fields and not updated.details_confirmed):
                from src.core.bookings.auto_policy import parse_and_validate_auto_booking
                settings = await conn.fetchrow('''SELECT config,capienze_orarie FROM booking_settings
                    WHERE organization_id=$1''', org)
                slots = {slot.field:slot.value for slot in updated.slots}
                decision = parse_and_validate_auto_booking(
                    {'config':decode(settings['config']), 'capienze_orarie':decode(settings['capienze_orarie'])}
                    if settings else {}, catalog.timezone,
                    {'service':'ristorante/tavolo', 'date':slots.get('appointment_date'),
                     'time':slots.get('appointment_time'), 'party_size':slots.get('party_size'),
                     'duration_minutes':updated.derived.duration_minutes},
                    now=await conn.fetchval('SELECT clock_timestamp()'),
                )
                if not decision.accepted:
                    return self._result(updated, updated.revision, False, catalog.revision)
                # The state, worker receipt and outbound attempt commit together.
                # The retry worker must hold the C2.4 delivery guard through send.
                destination = await conn.fetchval('''SELECT c.phone_number FROM contacts c
                    JOIN conversations v ON v.organization_id=c.organization_id AND v.contact_id=c.id
                    WHERE v.organization_id=$1 AND c.organization_id=$1 AND v.id=$2
                      AND c.deleted_at IS NULL''', org, conversation)
                if not destination:
                    raise ConversationStateUnavailable('Current destination unavailable')
                prompt_id = uuid4()
                body = render_confirmation_prompt(updated, prompt_id)
                key = f'c24-prompt:{org}:{conversation}:{source_id}'
                payload = {'to':destination, 'type':'text', 'text':{'body':body},
                    '_delivery_category':'service', '_c24_state_digest':state_digest(updated),
                    '_c24_source_id':str(source_id), '_c24_subject_id':str(await conn.fetchval(
                        'SELECT contact_id FROM conversations WHERE organization_id=$1 AND id=$2', org, conversation))}
                await conn.execute('''INSERT INTO messages
                    (id,organization_id,conversation_id,direction,message_type,content,content_text,
                     status,handling_type,idempotency_key)
                    VALUES($1,$2,$3,'outbound','text',$4::jsonb,$5,'queued','c24_confirmation_prompt',$6)''',
                    prompt_id, org, conversation, canonical(payload), body, key)
                await conn.execute('''INSERT INTO message_delivery_attempts (id,message_id,next_retry_at)
                    SELECT $2,m.id,clock_timestamp() FROM messages m
                    WHERE m.organization_id=$1 AND m.id=$3''', org, uuid4(), prompt_id)
            return self._result(updated, updated.revision, False, catalog.revision)

    @staticmethod
    def _result(state, applied_revision, replayed, current_catalog_revision):
        return {'state':state, 'applied_revision':applied_revision, 'replayed':replayed,
                'catalog_stale':state is None or state.catalog_revision != current_catalog_revision,
                'current_catalog_revision':current_catalog_revision,
                'details_confirmed_current':bool(state and state.details_confirmed and state.catalog_revision == current_catalog_revision),
                'runtime_enabled':False, 'confirmation_authorized':False}
