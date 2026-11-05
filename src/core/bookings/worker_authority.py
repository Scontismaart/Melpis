"""Private transaction-bound worker evidence; no runtime booking integration.

The claim table has no runtime grants; enabling it requires a separate review.
Claim expiry never grants an AI call, quota renewal, or refund.
"""
import asyncio
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4, uuid5
from weakref import WeakSet

from src.core.bookings.auto_policy import validate_auto_booking_configuration
from src.core.bookings.conversation_state import ConversationState, canonical, state_digest
from src.core.billing.quota_reservations import QuotaConflict, QuotaOperation, _period
from src.core.bookings.conversation_state import digest
from src.core.db.repositories.booking_allocation_repo import BookingAllocationRepository
from src.core.db.repositories.booking_catalog_repo import decode
from src.core.db.repositories.booking_conversation_repo import BookingConversationRepository


class WorkerProofDenied(PermissionError):
    pass


_NAMESPACE = UUID('4ef0a571-e941-49ba-8d85-770e8a9340ba')


def _trusted_uuid(value):
    if not isinstance(value, UUID) or value.int == 0:
        raise WorkerProofDenied('Invalid trusted UUID scope')
    return UUID(str(value))


def service_receipt_v2(org, message, operation, canonical_hash):
    """Stable service identity, separate from existing human v1 receipt hashes."""
    try:
        org, message = _trusted_uuid(org), _trusted_uuid(message)
    except WorkerProofDenied:
        raise ValueError('Invalid scoped service operation') from None
    if operation != 'create':
        raise ValueError('Invalid scoped service operation')
    if type(canonical_hash) is not str or len(canonical_hash) != 64 or any(c not in '0123456789abcdef' for c in canonical_hash):
        raise ValueError('Invalid canonical digest')
    base = f'{org}:{message}:{operation}'
    request = uuid5(_NAMESPACE, 'request:' + base)
    booking = uuid5(_NAMESPACE, 'booking:' + base)
    payload = {'version': 2, 'principal': 'private_booking_service', 'organization_id': str(org),
               'message_id': str(message), 'operation': operation, 'request_id': str(request),
               'booking_id': str(booking), 'canonical_response_hash': canonical_hash}
    return request, booking, sha256(canonical(payload).encode()).hexdigest()


@dataclass(frozen=True, repr=False)
class _Claim:
    organization_id: UUID
    message_id: UUID
    claim_id: UUID
    generation: int


class _Capability:
    __slots__ = ('_owner', '_connection', '_transaction_id', '_task', '_claim', '_proof', '__weakref__')
    def __init__(self, owner, connection, transaction_id, task, claim, proof):
        self._owner, self._connection, self._transaction_id, self._task = owner, connection, transaction_id, task
        self._claim, self._proof = claim, proof


class WorkerBookingAuthority:
    """Private capability mint. This object cannot execute a booking."""
    def __init__(self):
        self._issued = WeakSet()
        self._allocation = BookingAllocationRepository(None)

    async def claim(self, conn, org, message, *, lease_seconds=30):
        """Acquire a fenced lease only for persisted, committed inbound evidence."""
        if not conn.is_in_transaction() or type(lease_seconds) is not int or not 1 <= lease_seconds <= 60:
            raise WorkerProofDenied('Invalid claim scope')
        if await conn.fetchval('SHOW transaction_isolation') != 'read committed':
            raise WorkerProofDenied('Worker proof requires read committed isolation')
        org, message = _trusted_uuid(org), _trusted_uuid(message)
        if not await conn.fetchrow('SELECT id FROM organizations WHERE id=$1 FOR SHARE', org):
            raise WorkerProofDenied('Organization unavailable')
        await conn.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(org))
        source = await conn.fetchrow('''SELECT id,conversation_id FROM messages WHERE organization_id=$1 AND id=$2
            AND direction='inbound' AND deleted_at IS NULL FOR UPDATE''', org, message)
        if not source:
            raise WorkerProofDenied('Source unavailable')
        source, _, _ = await BookingConversationRepository._source(conn, org, source['conversation_id'], message)
        await BookingConversationRepository._canonical_response(conn, org, source)
        payload_hash = digest({'organization_id': str(org), 'message_id': str(message),
                               'channel': 'whatsapp', 'text': source['content_text']})
        operation = QuotaOperation(org, message, 'inbound', payload_hash)
        if not await conn.fetchrow('''SELECT o.operation_id FROM inbound_worker_operations o
            JOIN inbound_worker_receipts r ON r.organization_id=o.organization_id AND r.inbox_id=o.inbox_id AND r.message_id=o.message_id
            JOIN meta_webhook_inbox i ON i.id=r.inbox_id AND i.organization_id=r.organization_id
            WHERE o.organization_id=$1 AND r.organization_id=$1 AND i.organization_id=$1 AND o.message_id=$2 AND o.operation_id=$3
              AND i.status='completed' AND i.channel='whatsapp' FOR SHARE OF o, r, i''', org, message, operation.id):
            raise WorkerProofDenied('Trusted inbound provenance unavailable')
        claim_id = uuid4()
        row = await conn.fetchrow('''INSERT INTO booking_worker_claims(organization_id,message_id,conversation_id,claim_id,generation,lease_until)
            VALUES($1,$2,$5,$3,1,clock_timestamp()+make_interval(secs=>$4))
            ON CONFLICT(organization_id,message_id) DO UPDATE SET claim_id=EXCLUDED.claim_id,
                generation=booking_worker_claims.generation+1,lease_until=EXCLUDED.lease_until
            WHERE booking_worker_claims.lease_until<=clock_timestamp()
            RETURNING generation''', org, message, claim_id, lease_seconds, source['conversation_id'])
        if not row:
            raise WorkerProofDenied('Booking claim already held')
        return _Claim(org, message, claim_id, row['generation'])

    async def _proof(self, conn, claim, conversation, expected_revision, expected_catalog):
        if not conn.is_in_transaction() or type(claim) is not _Claim:
            raise WorkerProofDenied('Scoped transaction required')
        if await conn.fetchval('SHOW transaction_isolation') != 'read committed':
            raise WorkerProofDenied('Worker proof requires read committed isolation')
        conversation = _trusted_uuid(conversation)
        org, message = claim.organization_id, claim.message_id
        organization = await conn.fetchrow('SELECT * FROM organizations WHERE id=$1 FOR SHARE', org)
        if not organization:
            raise WorkerProofDenied('Organization unavailable')
        await conn.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(org))
        try:
            if organization['ai_accounting_blocked']:
                raise WorkerProofDenied('Current billing unavailable')
            _period(dict(organization), await conn.fetchval('SELECT clock_timestamp()'), org)
        except QuotaConflict:
            raise WorkerProofDenied('Current billing unavailable') from None
        pre = await conn.fetchrow('''SELECT snapshot,snapshot_hash,revision,catalog_revision FROM booking_conversation_states
            WHERE organization_id=$1 AND conversation_id=$2''', org, conversation)
        try:
            if not pre:
                raise ValueError()
            candidate = ConversationState.model_validate_json(canonical(decode(pre['snapshot'])))
            if (pre['revision'] != expected_revision or pre['catalog_revision'] != expected_catalog
                or candidate.revision != pre['revision'] or candidate.catalog_revision != pre['catalog_revision']
                or candidate.organization_id != org or candidate.conversation_id != conversation
                or state_digest(candidate) != pre['snapshot_hash']):
                raise ValueError()
            source_ids = {candidate.last_source_message_id} | {slot.source_message_id for slot in candidate.slots} | {slot.source_message_id for slot in candidate.superseded}
            if candidate.confirmation:
                source_ids |= {candidate.confirmation.source_message_id, candidate.confirmation.prompt_message_id}
            source_ids.add(message)
        except (TypeError, ValueError, KeyError):
            raise WorkerProofDenied('State preimage unavailable') from None
        rows = await conn.fetch('''SELECT id FROM messages WHERE organization_id=$1 AND conversation_id=$2
            AND id=ANY($3::uuid[]) ORDER BY id FOR SHARE''', org, conversation, sorted(source_ids, key=str))
        if {row['id'] for row in rows} != source_ids:
            raise WorkerProofDenied('State source unavailable')
        source, stamp, source_hash = await BookingConversationRepository._source(conn, org, conversation, message)
        current = await conn.fetchrow('''SELECT generation FROM booking_worker_claims
            WHERE organization_id=$1 AND message_id=$2 AND claim_id=$3 AND generation=$4 AND conversation_id=$5
              AND lease_until>clock_timestamp() FOR UPDATE''', org, message, claim.claim_id, claim.generation, conversation)
        if not current:
            raise WorkerProofDenied('Booking claim expired or changed')
        conv = await conn.fetchrow('SELECT * FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE', org, conversation)
        if not conv or conv['deleted_at'] is not None or conv['status'] != 'active' or conv['canale'] != 'whatsapp' or conv['contact_id'] != source['c24_original_subject_id']:
            raise WorkerProofDenied('Conversation unavailable')
        contact = await conn.fetchrow('SELECT * FROM contacts WHERE organization_id=$1 AND id=$2 FOR SHARE', org, conv['contact_id'])
        if not contact or contact['deleted_at'] is not None or contact['consent_status'] == 'withdrawn':
            raise WorkerProofDenied('Current consent unavailable')
        await self._allocation._gdpr(conn, org, conv['contact_id'])
        if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM booking_conversation_revocations WHERE organization_id=$1 AND conversation_id=$2)
            OR EXISTS(SELECT 1 FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND deleted_at IS NOT NULL)
            ''', org, conversation):
            raise WorkerProofDenied('Conversation evidence no longer current')
        import re
        other_sources = await conn.fetch('''SELECT id,content FROM messages WHERE organization_id=$1
            AND conversation_id=$2 AND direction='inbound' AND deleted_at IS NULL LIMIT 257 FOR SHARE''', org, conversation)
        if len(other_sources) > 256:
            raise WorkerProofDenied('Source ordering unavailable')
        for other in other_sources:
            if other['id'] == message:
                continue
            body = decode(other['content'])
            other_stamp = body.get('timestamp') if isinstance(body, dict) else None
            if type(other_stamp) is not str or not re.fullmatch(r'[0-9]{1,12}', other_stamp) or int(other_stamp) >= stamp:
                raise WorkerProofDenied('Source ordering unavailable')
        cached, response_hash = await BookingConversationRepository._canonical_response(conn, org, source)
        payload_hash = digest({'organization_id': str(org), 'message_id': str(message),
                               'channel': 'whatsapp', 'text': source['content_text']})
        operation = QuotaOperation(org, message, 'inbound', payload_hash)
        if not await conn.fetchrow('''SELECT o.operation_id FROM inbound_worker_operations o
            JOIN inbound_worker_receipts r ON r.organization_id=o.organization_id AND r.inbox_id=o.inbox_id AND r.message_id=o.message_id
            JOIN meta_webhook_inbox i ON i.id=r.inbox_id AND i.organization_id=r.organization_id
            WHERE o.organization_id=$1 AND r.organization_id=$1 AND i.organization_id=$1 AND o.message_id=$2 AND o.operation_id=$3
              AND i.status='completed' AND i.channel='whatsapp' FOR SHARE OF o, r, i''', org, message, operation.id):
            raise WorkerProofDenied('Trusted inbound provenance unavailable')
        await conn.fetchval('SELECT pg_advisory_xact_lock_shared(hashtext($1))', f'booking-capacity|{org}')
        settings = await conn.fetchrow('SELECT * FROM booking_settings WHERE organization_id=$1 FOR SHARE', org)
        catalog, sector, authority = await self._allocation._catalog(conn, org, organization, settings)
        config = decode(settings['config']) if settings else None
        if (not isinstance(config, dict) or not isinstance(config.get('a05'), dict)
            or config['a05'].get('enabled') is not True or authority != 'internal'
            or catalog.sector != sector or catalog.timezone != organization['timezone']
            or catalog.revision != expected_catalog
            or not validate_auto_booking_configuration({**dict(settings), 'config': config,
                'capienze_orarie': decode(settings['capienze_orarie'])}, organization['timezone'], sector).valid
            or cached.get('booking_catalog_revision') != catalog.revision or cached.get('richiede_umano') is not False):
            raise WorkerProofDenied('Current policy or catalog unavailable')
        post = await conn.fetchrow('''SELECT snapshot_hash,revision,catalog_revision FROM booking_conversation_states
            WHERE organization_id=$1 AND conversation_id=$2 FOR UPDATE''', org, conversation)
        if not post or any(post[k] != pre[k] for k in ('snapshot_hash','revision','catalog_revision')):
            raise WorkerProofDenied('State changed while locking')
        state, _ = await BookingConversationRepository(None)._load(conn, org, conversation)
        if not state or state.revision != expected_revision or state.catalog_revision != catalog.revision or state.last_source_message_id != message:
            raise WorkerProofDenied('Conversation state changed')
        if not await conn.fetchval('''SELECT lease_until>clock_timestamp() FROM booking_worker_claims
            WHERE organization_id=$1 AND message_id=$2 AND claim_id=$3 AND generation=$4''',
            org, message, claim.claim_id, claim.generation):
            raise WorkerProofDenied('Booking claim expired')
        return (org, conversation, message, conv['contact_id'], stamp, source_hash, response_hash,
                state.revision, state_digest(state), catalog.revision)

    async def mint(self, conn, claim, conversation, *, expected_state_revision, expected_catalog_revision):
        if type(expected_state_revision) is not int or expected_state_revision < 1 or type(expected_catalog_revision) is not int or expected_catalog_revision < 1:
            raise ValueError('Strict revision expectations required')
        proof = await self._proof(conn, claim, conversation, expected_state_revision, expected_catalog_revision)
        token = _Capability(self, conn, await conn.fetchval('SELECT txid_current()'), asyncio.current_task(), claim, proof)
        self._issued.add(token)
        return token

    async def revalidate(self, conn, capability):
        if (type(capability) is not _Capability or capability not in self._issued or capability._owner is not self
            or capability._connection is not conn or capability._task is not asyncio.current_task()
            or not conn.is_in_transaction() or await conn.fetchval('SELECT txid_current()') != capability._transaction_id):
            raise WorkerProofDenied('Capability scope changed')
        self._issued.remove(capability)
        prior = capability._proof
        current = await self._proof(conn, capability._claim, prior[1], prior[7], prior[9])
        if current != prior:
            raise WorkerProofDenied('Verified evidence changed')
        request, booking, digest = service_receipt_v2(prior[0], prior[2], 'create', prior[6])
        return {'request_id': request, 'booking_id': booking, 'semantic_hash': digest,
                'runtime_enabled': False, 'confirmation_authorized': False}
