"""Draft-only, disconnected conversation metadata. No operational writer calls.

New state and structured canonical-response retention await C05/A06 approval.
Only a real current owner/manager can call this administrative repository.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID

from src.core.billing.quota_reservations import QuotaOperation, _decode, _hash
from src.core.bookings.catalog_contracts import ScopedCatalog
from src.core.bookings.conversation_state import (
    ConversationState, StateConflict, canonical, confirm_details, digest,
    explicit_customer_fields, medical_source_requires_handoff, new_state,
    parse_proposal_json, reduce_proposal, render_confirmation_prompt, state_digest,
    validate_proposal_catalog,
)
from src.core.db.quota_provenance import require_quota_trust_epoch
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
    async def _scope(self, organization_id, auth_user_id, conversation_id):
        org, auth, conversation = map(_uuid, (organization_id, auth_user_id, conversation_id))
        async with self.pool.acquire() as conn, conn.transaction(isolation='read_committed'):
            organization = await conn.fetchrow('SELECT * FROM organizations WHERE id=$1 FOR SHARE', org)
            if not organization:
                raise ConversationStateDenied('Organization unavailable')
            await conn.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(org))
            actor = await self._allocation._actor(conn, org, auth)
            conv = await conn.fetchrow('SELECT * FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE', org, conversation)
            if not conv or conv['deleted_at'] is not None or conv['status'] != 'active' or conv['canale'] != 'whatsapp':
                raise ConversationStateDenied('Live WhatsApp conversation required')
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
            direction,deleted_at,status,billed_at,ai_reply_cache,claimed_at,heartbeat_at,
            clock_timestamp() AS verified_now FROM messages
            WHERE organization_id=$1 AND conversation_id=$2 AND id=$3 FOR SHARE''', org, conversation, source_message_id)
        if not source or source['direction'] != 'inbound' or source['deleted_at'] is not None:
            raise ConversationStateDenied('Live inbound source required')
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
        row = await conn.fetchrow('SELECT * FROM booking_conversation_states WHERE organization_id=$1 AND conversation_id=$2', org, conversation)
        if not row: return None, None
        try:
            state = ConversationState.model_validate_json(canonical(decode(row['snapshot'])))
            if (state.organization_id != org or state.conversation_id != conversation
                    or state.revision != row['revision'] or state.catalog_revision != row['catalog_revision']
                    or state.last_source_message_id != row['source_message_id']
                    or state_digest(state) != row['snapshot_hash']):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ConversationStateUnavailable('Stored state requires reconciliation') from None
        # Deletion, tenant transfer or source reassignment cannot make replay safe.
        source_ids = {state.last_source_message_id} | {s.source_message_id for s in state.slots}
        for source_id in sorted(source_ids, key=str):
            await BookingConversationRepository._source(conn, org, conversation, source_id)
        if state.confirmation:
            proof = state.confirmation
            prompt = await conn.fetchrow('''SELECT direction,status,sent_at,deleted_at,content_text
                FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND id=$3 FOR SHARE''',
                org, conversation, proof.prompt_message_id)
            if (not prompt or prompt['direction'] != 'outbound' or prompt['status'] not in {'sent','delivered','read'}
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

    async def _apply(self, organization_id, auth_user_id, conversation_id, source_message_id,
                     *, request_id, expected_revision, expected_catalog_revision, prompt_message_id):
        source_id, request = map(_uuid, (source_message_id, request_id))
        if type(expected_revision) is not int or expected_revision < 0 or type(expected_catalog_revision) is not int:
            raise ValueError('Strict expected revisions required')
        async with self._scope(organization_id, auth_user_id, conversation_id) as context:
            conn, org, conversation, actor, scoped, _authority = context
            catalog = scoped.catalog
            source, provider_time, source_hash = await self._source(conn, org, conversation, source_id)
            action = 'proposal' if prompt_message_id is None else 'confirmation'
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
                if prompt['content_text'] != render_confirmation_prompt(state, prompt_message_id):
                    raise StateConflict('Prompt does not match current state snapshot')
                updated = confirm_details(state, source_message_id=source_id, prompt_message_id=prompt_message_id,
                    prompt_body_hash=prompt_hash, expected_revision=expected_revision)
            stored_revision = await conn.fetchval('''INSERT INTO booking_conversation_states
                (organization_id,conversation_id,revision,catalog_revision,source_message_id,provider_timestamp,snapshot,snapshot_hash)
                VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
                ON CONFLICT(organization_id,conversation_id) DO UPDATE SET revision=EXCLUDED.revision,
                  catalog_revision=EXCLUDED.catalog_revision,source_message_id=EXCLUDED.source_message_id,
                  provider_timestamp=EXCLUDED.provider_timestamp,snapshot=EXCLUDED.snapshot,
                  snapshot_hash=EXCLUDED.snapshot_hash,updated_at=clock_timestamp()
                  WHERE booking_conversation_states.revision=$9 RETURNING revision''',
                org, conversation, updated.revision, catalog.revision, source_id, provider_time,
                canonical(updated.model_dump(mode='json')), state_digest(updated), expected_revision)
            if stored_revision != updated.revision:
                raise StateConflict('State compare-and-swap failed')
            await conn.execute('''INSERT INTO booking_conversation_requests
                (organization_id,request_id,conversation_id,source_message_id,actor_user_id,action,
                 payload_hash,applied_revision,canonical_response_hash,prompt_message_id,prompt_body_hash)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)''', org, request, conversation, source_id,
                actor, action, operation_hash, updated.revision, canonical_hash, prompt_message_id, prompt_hash)
            return self._result(updated, updated.revision, False, catalog.revision)

    @staticmethod
    def _result(state, applied_revision, replayed, current_catalog_revision):
        return {'state':state, 'applied_revision':applied_revision, 'replayed':replayed,
                'catalog_stale':state is None or state.catalog_revision != current_catalog_revision,
                'current_catalog_revision':current_catalog_revision,
                'details_confirmed_current':bool(state and state.details_confirmed and state.catalog_revision == current_catalog_revision),
                'runtime_enabled':False, 'confirmation_authorized':False}
