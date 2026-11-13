"""Private transaction-bound worker evidence and booking mutation.

The claim table has no runtime grants; enabling it requires a separate review.
Claim expiry never grants an AI call, quota renewal, or refund.
"""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from hashlib import sha256
from uuid import UUID, uuid4, uuid5
from weakref import WeakSet

from src.core.billing.quota_reservations import QuotaConflict, QuotaOperation, _period
from src.core.bookings.allocation_contracts import AllocationRequest
from src.core.bookings.auto_policy import (
    parse_and_validate_auto_booking,
    validate_auto_booking_configuration,
)
from src.core.bookings.conversation_state import (
    ConversationState,
    canonical,
    digest,
    state_digest,
)
from src.core.db.inbound_worker_provenance import private_booking_worker_enabled
from src.core.db.repositories.booking_allocation_repo import BookingAllocationRepository
from src.core.db.repositories.booking_catalog_repo import decode
from src.core.db.repositories.booking_conversation_repo import (
    BookingConversationRepository,
)
from src.core.db.repositories.booking_repo import BookingRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SectorSlot:
    date: date
    time: time
    party_size: int = 1
    capacity: int | None = None


@dataclass(frozen=True)
class _HotelSlot:
    date: date
    time: time
    departure: date
    room_count: int | None
    party_size: int
    capacity: None = None


class WorkerProofDenied(PermissionError):
    pass


_NAMESPACE = UUID('4ef0a571-e941-49ba-8d85-770e8a9340ba')


def _trusted_uuid(value):
    # asyncpg returns its own UUID scalar type; parse only already trusted
    # database/application identifiers into the stdlib UUID used by contracts.
    try:
        result = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise WorkerProofDenied('Invalid trusted UUID scope') from None
    if result.int == 0:
        raise WorkerProofDenied('Invalid trusted UUID scope')
    return result


async def _begin_database_job(conn, message_id, job_kind):
    """Map only an authorization-denial SQLSTATE to the worker's safe denial."""
    try:
        return await conn.fetchrow(
            'SELECT * FROM c24_private.c24_worker_begin_job($1,$2)', message_id, job_kind,
        )
    except Exception as exc:
        if getattr(exc, 'sqlstate', None) == '42501':
            raise WorkerProofDenied('Persisted booking authorization unavailable') from None
        raise


async def _require_private_identity(conn):
    if not conn.is_in_transaction() or not await conn.fetchval("""SELECT session_user=current_user
        AND current_user='melpis_c24_booking_worker'"""):
        raise WorkerProofDenied('Private booking worker identity unavailable')


async def _require_capability_issuer(conn):
    if not conn.is_in_transaction() or not await conn.fetchval("""SELECT
        session_user='postgres' AND current_user='postgres'
        AND has_function_privilege(current_user,
          'c24_private.c24_worker_issue_capability(uuid,uuid,uuid,bigint,uuid,jsonb,text,integer)',
          'EXECUTE')"""):
        raise WorkerProofDenied('Private booking capability issuer unavailable')


async def _renew_claim(pool, claim, *, lease_seconds=300):
    async with pool.acquire() as conn, conn.transaction(isolation='read_committed'):
        await _require_private_identity(conn)
        renewed_until = await conn.fetchval(
            'SELECT c24_private.c24_worker_renew_job($1,$2,$3,$4,$5)',
            _trusted_uuid(claim.organization_id), _trusted_uuid(claim.message_id),
            _trusted_uuid(claim.claim_id), claim.generation, lease_seconds,
        )
        if (not isinstance(renewed_until, datetime)
                or renewed_until.tzinfo is None
                or renewed_until <= datetime.now(timezone.utc)):
            raise WorkerProofDenied('Booking claim lease lost')


async def _lease_heartbeat(pool, claim, stop_event):
    try:
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10)
                return
            except asyncio.TimeoutError:
                await _renew_claim(pool, claim)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Store only a non-sensitive status in the event; callers inspect it
        # before issuing or consuming the capability.
        stop_event.set()
        raise


def _check_heartbeat(task):
    if task.done() and not task.cancelled():
        try:
            task.result()
        except Exception:
            raise WorkerProofDenied('Booking claim lease lost') from None


@asynccontextmanager
async def _keep_claim_live(pool, claim):
    await _renew_claim(pool, claim)
    stop_event = asyncio.Event()
    heartbeat = asyncio.create_task(_lease_heartbeat(pool, claim, stop_event))
    try:
        yield heartbeat
    finally:
        stop_event.set()
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


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
    __slots__ = ('_owner', '_connection', '_transaction_id', '_task', '_claim', '_proof', '_kind',
                 '_database_id', '_payload', '_payload_hash', '__weakref__')
    def __init__(self, owner, connection, transaction_id, task, claim, proof, kind='c24',
                 database_id=None, payload=None, payload_hash=None):
        self._owner, self._connection, self._transaction_id, self._task = owner, connection, transaction_id, task
        self._claim, self._proof = claim, proof
        self._kind = kind
        self._database_id, self._payload, self._payload_hash = database_id, payload, payload_hash


class WorkerBookingAuthority:
    """Bind private booking creation to current persisted evidence."""
    def __init__(self):
        self._issued = WeakSet()
        self._allocation = BookingAllocationRepository(None)
        self._cursor = None

    async def _confirmed_jobs(self, pool, limit):
        async with pool.acquire() as conn:
            if not await conn.fetchval("""SELECT session_user=current_user
                AND current_user='melpis_c24_booking_worker'"""):
                raise WorkerProofDenied('Private booking worker identity unavailable')
            after_message = self._cursor
            rows = await conn.fetch(
                'SELECT job_id FROM c24_private.c24_worker_next_jobs($1,$2)',
                after_message, limit,
            )
            return [row['job_id'] for row in rows]

    async def process_next_batch(self, pool, *, application_pool=None, limit=10):
        """Process persisted confirmations independently of inbound AI generation."""
        if not private_booking_worker_enabled():
            return 0
        if application_pool is None:
            raise WorkerProofDenied('Private booking capability issuer unavailable')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid booking worker batch size')
        jobs = await self._confirmed_jobs(pool, limit)
        if not jobs:
            self._cursor = None
            return 0
        created = 0
        for job_id in jobs:
            try:
                await self.process_confirmation(
                    pool, None, None, job_id, application_pool=application_pool,
                )
            except WorkerProofDenied:
                # A denial is safe to reconsider after a later policy/state change.
                pass
            except Exception as exc:
                # Retry on a later scan without starving other conversations.
                logger.error('Private booking job failed message_id=%s error_type=%s',
                             job_id, type(exc).__name__)
            else:
                created += 1
            self._cursor = job_id
        return created

    async def process_confirmation(self, pool, organization_id, conversation_id, confirmation_id,
                                   *, application_pool=None):
        """Resolve one persisted customer confirmation and commit its booking."""
        if not private_booking_worker_enabled():
            raise WorkerProofDenied('Private booking worker disabled')
        if application_pool is None:
            raise WorkerProofDenied('Private booking capability issuer unavailable')
        confirmation = _trusted_uuid(confirmation_id)
        expected_org = _trusted_uuid(organization_id) if organization_id is not None else None
        expected_conversation = _trusted_uuid(conversation_id) if conversation_id is not None else None
        if (expected_org is None) != (expected_conversation is None):
            raise WorkerProofDenied('Expected tenant and conversation scope must be supplied together')
        # Validate caller-supplied scope before c24_worker_begin_job creates a
        # durable lease. Otherwise a mismatched scope could pin a legitimate
        # tenant's confirmation until lease expiry, even though no booking is
        # authorized. The claim function still independently derives and checks
        # the same scope before returning a job.
        if expected_org is not None or expected_conversation is not None:
            try:
                async with application_pool.acquire() as app_conn:
                    async with app_conn.transaction(isolation='read_committed', readonly=True):
                        await _require_capability_issuer(app_conn)
                        persisted_scope = await app_conn.fetchrow('''
                            SELECT organization_id,conversation_id
                            FROM public.booking_conversation_requests
                            WHERE source_message_id=$1 AND organization_id=$2 AND conversation_id=$3
                              AND action='confirmation'
                              AND actor_kind='inbound_worker'
                        ''', confirmation, expected_org, expected_conversation)
            except WorkerProofDenied:
                raise
            except Exception:
                raise WorkerProofDenied('Confirmed proposal unavailable') from None
            if (not persisted_scope
                    or (expected_org is not None and persisted_scope['organization_id'] != expected_org)
                    or (expected_conversation is not None
                        and persisted_scope['conversation_id'] != expected_conversation)):
                raise WorkerProofDenied('Booking scope does not match persisted confirmation')
        async with pool.acquire() as conn, conn.transaction(isolation='read_committed'):
            await _require_private_identity(conn)
            job = await _begin_database_job(conn, confirmation, 'confirmation')
            if not job:
                raise WorkerProofDenied('Confirmed proposal unavailable')
            if job['status'] == 'completed':
                return job['booking_id']
            if job['status'] != 'claimed':
                raise WorkerProofDenied('Booking claim already held')
            org, conversation = job['organization_id'], job['conversation_id']
        if expected_org is not None and expected_org != org:
            raise WorkerProofDenied('Booking organization does not match persisted job')
        if expected_conversation is not None and expected_conversation != conversation:
            raise WorkerProofDenied('Booking conversation does not match persisted job')
        claim = _Claim(_trusted_uuid(org), _trusted_uuid(job['claim_message_id']),
                       _trusted_uuid(job['claim_id']), job['generation'])
        async with _keep_claim_live(pool, claim) as heartbeat:
            async with application_pool.acquire() as app_conn, app_conn.transaction(isolation='read_committed'):
                await _require_capability_issuer(app_conn)
                candidate = await app_conn.fetchrow("""SELECT revision,catalog_revision
                    FROM booking_conversation_states
                    WHERE organization_id=$1 AND conversation_id=$2 FOR SHARE""", org, conversation)
                if not candidate:
                    raise WorkerProofDenied('Confirmed proposal unavailable')
                proof = await self._proof(
                    app_conn, claim, conversation, candidate['revision'], candidate['catalog_revision'],
                    trusted_application=True,
                )
                capability = await self._issue_database_capability(
                    app_conn, claim, proof, 'confirmation', confirmation_message_id=confirmation,
                )
            _check_heartbeat(heartbeat)
            await _renew_claim(pool, claim)
            _check_heartbeat(heartbeat)
            async with pool.acquire() as conn, conn.transaction(isolation='read_committed'):
                await _require_private_identity(conn)
                return await self.create_booking(conn, capability)

    async def process_a05_booking(self, pool, organization_id, message_id, canonical_response,
                                 *, application_pool=None):
        """Commit an already A04-billed restaurant booking under private DB identity."""
        if not private_booking_worker_enabled():
            raise WorkerProofDenied('Private booking worker disabled')
        if application_pool is None:
            raise WorkerProofDenied('Private booking capability issuer unavailable')
        org, message = map(_trusted_uuid, (organization_id, message_id))
        async with pool.acquire() as conn, conn.transaction(isolation='read_committed'):
            await _require_private_identity(conn)
            job = await _begin_database_job(conn, message, 'a05')
            if not job or job['organization_id'] != org:
                raise WorkerProofDenied('Booking organization does not match persisted job')
            if job['status'] == 'completed':
                return job['booking_id']
            if job['status'] != 'claimed':
                raise WorkerProofDenied('Booking claim already held')
        claim = _Claim(_trusted_uuid(org), _trusted_uuid(job['claim_message_id']),
                       _trusted_uuid(job['claim_id']), job['generation'])
        async with _keep_claim_live(pool, claim) as heartbeat:
            async with application_pool.acquire() as app_conn, app_conn.transaction(isolation='read_committed'):
                await _require_capability_issuer(app_conn)
                proof = await self._proof_a05(
                    app_conn, claim, canonical_response, trusted_application=True,
                )
                capability = await self._issue_database_capability(app_conn, claim, proof, 'a05')
            _check_heartbeat(heartbeat)
            await _renew_claim(pool, claim)
            _check_heartbeat(heartbeat)
            async with pool.acquire() as conn, conn.transaction(isolation='read_committed'):
                await _require_private_identity(conn)
                return await self.create_booking(conn, capability)

    async def _proof_a05(self, conn, claim, supplied_response=None, *, trusted_application=False):
        """Revalidate A04, tenant intent, and the fixed restaurant/table policy."""
        if trusted_application:
            await _require_capability_issuer(conn)
        else:
            await _require_private_identity(conn)
        if type(claim) is not _Claim:
            raise WorkerProofDenied('Scoped A05 transaction required')
        org, message = _trusted_uuid(claim.organization_id), _trusted_uuid(claim.message_id)
        source_row = await conn.fetchrow('''SELECT m.id,m.organization_id,m.conversation_id,m.content,m.content_text,
                m.created_at,m.deleted_at,m.c24_original_subject_id,m.ai_reply_cache,m.billed_at,c.canale,o.business_profile,o.timezone,
                o.subscription_status,o.subscription_id,o.trial_start,o.trial_end,o.current_period_start,
                o.current_period_end,o.ai_accounting_blocked,clock_timestamp() AS verified_now
            FROM messages m JOIN conversations c ON c.organization_id=m.organization_id AND c.id=m.conversation_id
            JOIN organizations o ON o.id=m.organization_id
            WHERE m.organization_id=$1 AND c.organization_id=$1 AND o.id=$1 AND m.id=$2
              AND m.direction='inbound' AND m.deleted_at IS NULL FOR SHARE OF m,c,o''', org, message)
        if not source_row or not source_row['conversation_id'] or source_row['canale'] != 'whatsapp':
            raise WorkerProofDenied('A05 source unavailable')
        # Match the inbound writer's conversation lock: a cancellation already
        # stored wins; one arriving later is serialized after this booking txn.
        conversation = await conn.fetchrow('''SELECT id,contact_id,deleted_at,status,ticket_status
            FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE''', org, source_row['conversation_id'])
        if (not conversation or conversation['deleted_at'] is not None
                or conversation['contact_id'] != source_row['c24_original_subject_id']
                or conversation['status'] != 'active' or conversation['ticket_status'] != 'AI_ACTIVE'):
            raise WorkerProofDenied('A05 conversation unavailable')
        from src.core.db.repositories.booking_conversation_repo import (
            BookingConversationRepository,
        )
        response, response_hash = await BookingConversationRepository._canonical_response(
            conn, org, source_row, private_trust_epoch=True,
        )
        from src.models.schemas import RispostaOutput
        try:
            parsed = RispostaOutput.model_validate(response)
            if supplied_response is not None and parsed.model_dump(mode='json') != RispostaOutput.model_validate(supplied_response).model_dump(mode='json'):
                raise ValueError()
        except Exception:
            raise WorkerProofDenied('Canonical A04 response unavailable') from None
        proposal = parsed.prenotazione
        if parsed.richiede_umano or proposal is None or not (proposal.data and proposal.ora and proposal.coperti) or proposal.note:
            raise WorkerProofDenied('A05 proposal is not eligible')
        from src.core.db.repositories.booking_repo import BookingRepository
        from src.core.inbound.service import (
            InboundProcessingService,
            _booking_action_is_fresh,
        )
        verifier = InboundProcessingService.__new__(InboundProcessingService)
        verifier.repo = BookingRepository(None)
        source = await verifier._booking_context(org, dict(source_row), proposal, conn=conn,
                                                require_latest=True, require_explicit_party=True)
        if (source is None or not _booking_action_is_fresh(source, proposal)
                or source['booking_source_message_id'] != str(message)):
            raise WorkerProofDenied('Customer booking intent is not current')
        profile = source.get('business_profile') or {}
        profile = decode(profile) if isinstance(profile, str) else profile
        if not isinstance(profile, dict) or profile.get('verticale') != 'ristorante':
            raise WorkerProofDenied('A05 is restricted to restaurants')
        settings = await conn.fetchrow('SELECT * FROM booking_settings WHERE organization_id=$1 FOR SHARE', org)
        config = decode(settings['config']) if settings else None
        capacities = decode(settings['capienze_orarie']) if settings else None
        policy_settings = {'config': config, 'capienze_orarie': capacities}
        decision = parse_and_validate_auto_booking(policy_settings, source.get('timezone'), {
            'service':'ristorante/tavolo','date':proposal.data,'time':proposal.ora,
            'party_size':proposal.coperti,'duration_minutes':60}, now=source['verified_now'])
        if (not decision.accepted or decision.booking.authority not in {'internal','internal_calendar_sync','google_authoritative'}
                or not validate_auto_booking_configuration(policy_settings, source.get('timezone'),'ristorante').valid):
            raise WorkerProofDenied('Current A05 policy denied')
        organization = await conn.fetchrow('SELECT id,timezone,business_profile FROM organizations WHERE id=$1 FOR SHARE', org)
        catalog, sector, authority = await self._allocation._catalog(conn, org, organization, settings)
        from src.core.bookings.catalog_contracts import MinuteDuration
        eligible = [item for item in catalog.services if isinstance(item.duration, MinuteDuration)
                    and item.duration.minutes == 60
                    and any(resource.kind in {'table','seat_pool'}
                            and any(req.resource_id == resource.resource_id for req in item.resources)
                            for resource in catalog.resources)]
        if (sector != 'ristorante' or catalog.sector != 'ristorante' or catalog.timezone != source.get('timezone')
                or authority not in {'internal','internal_calendar_sync','google_authoritative'}
                or authority != decision.booking.authority or len(eligible) != 1):
            raise WorkerProofDenied('Published restaurant allocation catalog unavailable')
        service_id = eligible[0].service_id
        from src.core.bookings.allocation_contracts import derive_allocation
        request = AllocationRequest(request_id=uuid4(), booking_id=uuid4(), action='allocate',
            expected_allocation_revision=0, expected_catalog_revision=catalog.revision,
            service_id=service_id, appointment_date=decision.booking.date,
            appointment_time=decision.booking.time, party_size=proposal.coperti)
        try:
            derive_allocation(request, catalog)
        except (ValueError, TypeError, OverflowError):
            raise WorkerProofDenied('Restaurant allocation does not match published catalog') from None
        provenance = await conn.fetchrow('''SELECT o.operation_id,o.inbox_id FROM inbound_worker_operations o
            JOIN inbound_worker_receipts r ON r.organization_id=o.organization_id AND r.message_id=o.message_id AND r.inbox_id=o.inbox_id
            WHERE o.organization_id=$1 AND r.organization_id=$1 AND o.message_id=$2
            ORDER BY o.operation_id LIMIT 1 FOR SHARE OF o,r''', org, message)
        if not provenance:
            raise WorkerProofDenied('Committed ingress provenance unavailable')
        from src.core.bookings.conversation_state import digest
        text_hash = digest({'organization_id':str(org),'conversation_id':str(source['conversation_id']),
                            'message_id':str(message),'content_text':source_row['content_text']})
        return (org, _trusted_uuid(source['conversation_id']), message,
                _trusted_uuid(source_row['c24_original_subject_id']),
                source_row['created_at'].timestamp(), text_hash, response_hash, 1, response_hash,
                catalog.revision, provenance['inbox_id'], provenance['operation_id'],
                _SectorSlot(decision.booking.date, decision.booking.time, proposal.coperti, decision.booking.capacity),
                service_id, authority, source_row['canale'])

    async def claim(self, conn, org, message, *, lease_seconds=30):
        """Compatibility helper for tests and callers; claim creation is database-derived."""
        await _require_private_identity(conn)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 60:
            raise WorkerProofDenied('Invalid claim scope')
        if await conn.fetchval('SHOW transaction_isolation') != 'read committed':
            raise WorkerProofDenied('Worker proof requires read committed isolation')
        org, message = _trusted_uuid(org), _trusted_uuid(message)
        job = await _begin_database_job(conn, message, 'a05')
        if not job or job['organization_id'] != org or job['status'] != 'claimed':
            raise WorkerProofDenied('Booking claim already held')
        return _Claim(org, _trusted_uuid(job['claim_message_id']),
                      _trusted_uuid(job['claim_id']), job['generation'])

    async def _issue_database_capability(self, conn, claim, proof, job_kind,
                                         confirmation_message_id=None):
        """Issue a durable capability only after the trusted app pool proves the full action."""
        await _require_capability_issuer(conn)
        if job_kind not in {'a05', 'confirmation'} or type(claim) is not _Claim:
            raise WorkerProofDenied('Private booking capability scope unavailable')
        org, conversation, message, subject = map(_trusted_uuid, proof[:4])
        request_id, booking_id, service_digest = service_receipt_v2(org, message, 'create', proof[6])
        slot = proof[12]
        service_id = proof[13]
        authority = proof[14] if job_kind == 'a05' else 'internal'
        channel = proof[15] if job_kind == 'a05' else 'whatsapp'
        if channel != 'whatsapp' or authority not in {'internal', 'internal_calendar_sync', 'google_authoritative'}:
            raise WorkerProofDenied('Private booking authority unavailable')

        source = await conn.fetchrow('''SELECT content_text FROM messages
            WHERE organization_id=$1 AND conversation_id=$2 AND id=$3
              AND direction='inbound' AND deleted_at IS NULL FOR SHARE''', org, conversation, message)
        contact = await conn.fetchrow('''SELECT phone_number,consent_status,deleted_at FROM contacts
            WHERE organization_id=$1 AND id=$2 FOR SHARE''', org, subject)
        if not source or not contact or contact['deleted_at'] is not None or contact['consent_status'] == 'withdrawn':
            raise WorkerProofDenied('Current booking subject unavailable')
        from src.core.inbound.service import _customer_name_from_messages
        name = _customer_name_from_messages([source])
        phone = contact['phone_number']
        if not name or not phone:
            raise WorkerProofDenied('Verified customer details unavailable')

        temporal = ({'arrival_date': slot.date, 'departure_date': slot.departure,
                     'room_count': slot.room_count}
                    if isinstance(slot, _HotelSlot) else
                    {'appointment_date': slot.date, 'appointment_time': slot.time,
                     'party_size': slot.party_size if slot.capacity is not None else None})
        request = AllocationRequest(
            request_id=request_id, booking_id=booking_id, action='allocate',
            expected_allocation_revision=0, expected_catalog_revision=proof[9],
            service_id=service_id, **temporal,
        )
        from src.core.bookings.allocation_contracts import derive_allocation
        from src.core.db.repositories.booking_allocation_repo import _booking_fingerprint, _digest
        organization = await conn.fetchrow('''SELECT id,timezone,business_profile FROM organizations
            WHERE id=$1 FOR SHARE''', org)
        settings = await conn.fetchrow('SELECT * FROM booking_settings WHERE organization_id=$1 FOR SHARE', org)
        catalog, sector, current_authority = await self._allocation._catalog(conn, org, organization, settings)
        if (not organization or catalog.revision != proof[9] or current_authority != authority
                or catalog.sector != sector or catalog.timezone != organization['timezone']):
            raise WorkerProofDenied('Current allocation catalog unavailable')
        start, end, resource_claims = derive_allocation(request, catalog)
        google_pending = authority == 'google_authoritative'
        booking = {
            'id': str(booking_id), 'organization_id': str(org), 'contact_id': str(subject),
            'id_conversazione': str(conversation), 'nome_cliente': name, 'telefono': phone,
            'data': slot.date.isoformat(), 'ora': slot.time.isoformat(),
            'coperti': slot.party_size, 'note': '',
            'stato': 'in_attesa' if google_pending else 'confermata',
            'origine': 'WhatsApp', 'source_message_id': str(message),
            'calendar_confirm_pending': google_pending, 'richiede_intervento': google_pending,
            # The BEFORE INSERT trigger initializes Calendar fencing at 1.
            # Bind proof/fingerprint/effects to that canonical stored revision.
            'calendar_revision': 1, 'completata_at': None, 'no_show_at': None,
        }
        fingerprint = _booking_fingerprint({
            **booking, 'data': slot.date, 'ora': slot.time, 'contact_id': subject,
            'id_conversazione': str(conversation),
        })
        temporal_kind = 'civil_nights' if isinstance(slot, _HotelSlot) else 'minutes'
        allocation = {
            'input': request.model_dump(mode='json'),
            'request': {
                'organization_id': str(org), 'request_id': str(request_id),
                'booking_id': str(booking_id), 'actor_user_id': None,
                'action': 'allocate', 'payload_hash': _digest({
                    'version': 2, 'organization_id': str(org),
                    'service_digest': service_digest,
                    'request': request.model_dump(mode='json'),
                }),
                'outcome': 'allocated', 'applied_allocation_revision': 1,
                'catalog_revision': catalog.revision, 'mutation_hash': None,
                'actor_kind': 'inbound_worker', 'service_inbox_id': str(proof[10]),
                'service_message_id': str(message), 'service_operation_id': str(proof[11]),
            },
            'set': {
                'organization_id': str(org), 'booking_id': str(booking_id),
                'allocation_revision': 1, 'state': 'active',
                'catalog_revision': catalog.revision, 'service_id': service_id,
                'timezone': catalog.timezone, 'temporal_kind': temporal_kind,
                'starts_at': start.isoformat(), 'ends_at': end.isoformat(),
                'arrival_date': slot.date.isoformat() if isinstance(slot, _HotelSlot) else None,
                'departure_date': slot.departure.isoformat() if isinstance(slot, _HotelSlot) else None,
                'booking_fingerprint': fingerprint, 'booking_calendar_revision': 1,
            },
            'resources': [
                {
                    'organization_id': str(org), 'booking_id': str(booking_id),
                    'allocation_revision': 1, 'catalog_revision': catalog.revision,
                    'state': 'active', 'resource_id': resource_id,
                    'resource_kind': resource_kind, 'exclusive': exclusive,
                    'quantity': quantity, 'starts_at': start.isoformat(), 'ends_at': end.isoformat(),
                }
                for resource_id, resource_kind, exclusive, quantity in resource_claims
            ],
            'semantic_hash': _digest({
                'version': 2, 'organization_id': str(org), 'service_digest': service_digest,
                'request': request.model_dump(mode='json'),
            }),
        }
        stay = None
        if isinstance(slot, _HotelSlot):
            stay = {
                'organization_id': str(org), 'booking_id': str(booking_id),
                'original_subject_id': str(subject), 'original_conversation_id': str(conversation),
                'source_message_id': str(message), 'arrival_date': slot.date.isoformat(),
                'departure_date': slot.departure.isoformat(), 'revision': 1,
                'source_kind': 'inbound',
            }

        confirmation_id = UUID(str(confirmation_message_id)) if confirmation_message_id else None
        confirmation_inbox = None
        prompt_id = None
        if confirmation_id:
            row = await conn.fetchrow('''SELECT r.inbox_id,q.prompt_message_id
                FROM booking_conversation_requests q
                JOIN inbound_worker_receipts r ON r.organization_id=q.organization_id
                 AND r.message_id=q.source_message_id
                WHERE q.organization_id=$1 AND r.organization_id=$1 AND q.conversation_id=$2
                  AND q.source_message_id=$3 AND q.action='confirmation' ''', org, conversation, confirmation_id)
            if not row:
                raise WorkerProofDenied('Trusted confirmation lineage unavailable')
            confirmation_inbox, prompt_id = row['inbox_id'], row['prompt_message_id']

        outbound_message = None
        confirmation_outbox = None
        calendar = None
        if authority in {'internal_calendar_sync', 'google_authoritative'}:
            link = await conn.fetchrow('''SELECT connection_epoch,calendar_id,connection_status,sync_enabled
                FROM google_calendar_credentials WHERE organization_id=$1 FOR SHARE''', org)
            calendar = {
                'required': True,
                'idempotency_key': f'google-booking:{org}:{booking_id}',
                'action': 'authoritative' if google_pending else 'upsert',
                'desired_revision': 1,
                'authoritative': google_pending,
                'sync_status': ('pending' if link and link['connection_status'] == 'connected'
                                and link['sync_enabled'] else 'blocked'),
                'connection_epoch': str(link['connection_epoch']) if link else None,
                'calendar_id': link['calendar_id'] if link else None,
            }
        if not google_pending:
            if isinstance(slot, _HotelSlot):
                notice = f'Soggiorno confermato in Melpis: arrivo {slot.date.isoformat()}, partenza {slot.departure.isoformat()}.'
            else:
                notice = f'Prenotazione confermata in Melpis per il {slot.date.isoformat()} alle {slot.time.strftime("%H:%M")}, {slot.party_size} persone.'
            from uuid import uuid5
            outbound_id = uuid5(_NAMESPACE, f'outbound:{org}:{message}')
            idempotency_key = f'booking-confirm:{org}:{message}'
            outbound_payload = {
                'to': phone, 'type': 'text', 'text': {'body': notice},
                '_delivery_category': 'service',
            }
            outbound_message = {
                'id': str(outbound_id), 'organization_id': str(org),
                'conversation_id': str(conversation), 'wam_id': None,
                'direction': 'outbound', 'message_type': 'text',
                'content': outbound_payload, 'content_text': notice,
                'status': 'queued', 'handling_type': 'booking_confirmation',
                'idempotency_key': idempotency_key,
            }
            confirmation_outbox = {
                'organization_id': str(org), 'booking_id': str(booking_id),
                'inbound_message_id': str(message), 'outbound_message_id': str(outbound_id),
                'booking_revision': 1, 'status': 'waiting_for_reply',
            }
        payload = {
            'version': 1, 'job_kind': job_kind,
            'organization_id': str(org), 'message_id': str(message),
            'source_message_id': str(message), 'conversation_id': str(conversation),
            'subject_id': str(subject), 'operation_id': str(proof[11]),
            'inbox_id': str(proof[10]),
            'confirmation_message_id': str(confirmation_id) if confirmation_id else None,
            'confirmation_inbox_id': str(confirmation_inbox) if confirmation_inbox else None,
            'prompt_message_id': str(prompt_id) if prompt_id else None,
            'claim_id': str(claim.claim_id), 'generation': claim.generation,
            'booking_id': str(booking_id), 'booking': booking, 'stay': stay,
            'allocation': allocation,
            'effects': {
                'outbound_message': outbound_message,
                'confirmation_outbox': confirmation_outbox,
                'calendar': calendar,
            },
        }
        claim_row = await conn.fetchrow('''SELECT lease_until FROM booking_worker_claims
            WHERE organization_id=$1 AND message_id=$2 AND claim_id=$3 AND generation=$4
              AND lease_until>clock_timestamp() FOR UPDATE''',
            org, message, claim.claim_id, claim.generation)
        if not claim_row:
            raise WorkerProofDenied('Booking lease expired')
        payload['lease_until'] = claim_row['lease_until'].isoformat()
        payload_hash = await conn.fetchval(
            "SELECT encode(pg_catalog.sha256(convert_to($1::jsonb::text,'UTF8')),'hex')",
            canonical(payload),
        )
        capability_id = await conn.fetchval('''SELECT c24_private.c24_worker_issue_capability(
            $1,$2,$3,$4,$5,$6::jsonb,$7,20)''', org, message, claim.claim_id,
            claim.generation, booking_id, canonical(payload), payload_hash)
        capability = _Capability(
            self, None, None, asyncio.current_task(), claim, proof,
            kind='a05' if job_kind == 'a05' else 'c24',
            database_id=capability_id, payload=payload, payload_hash=payload_hash,
        )
        self._issued.add(capability)
        return capability

    async def _proof(self, conn, claim, conversation, expected_revision, expected_catalog,
                     *, trusted_application=False):
        if trusted_application:
            await _require_capability_issuer(conn)
        else:
            await _require_private_identity(conn)
        if type(claim) is not _Claim:
            raise WorkerProofDenied('Scoped transaction required')
        if await conn.fetchval('SHOW transaction_isolation') != 'read committed':
            raise WorkerProofDenied('Worker proof requires read committed isolation')
        conversation = _trusted_uuid(conversation)
        org, message = _trusted_uuid(claim.organization_id), _trusted_uuid(claim.message_id)
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
            confirmation = candidate.confirmation
            if (candidate.intent != 'booking' or candidate.missing_fields
                    or candidate.ambiguous_fields or candidate.handoff_reason is not None
                    or not candidate.details_confirmed or confirmation is None
                    or candidate.last_source_message_id != confirmation.source_message_id
                    or confirmation.source_message_id == message
                    or confirmation.prompt_revision != candidate.revision - 1):
                raise ValueError()
            source_ids = {candidate.last_source_message_id} | {slot.source_message_id for slot in candidate.slots} | {slot.source_message_id for slot in candidate.superseded}
            if candidate.confirmation:
                source_ids |= {candidate.confirmation.source_message_id, candidate.confirmation.prompt_message_id}
            source_ids.add(message)
        except (TypeError, ValueError, KeyError):
            raise WorkerProofDenied('State preimage unavailable') from None
        rows = await conn.fetch('''SELECT id FROM messages WHERE organization_id=$1 AND conversation_id=$2
            AND id=ANY($3::uuid[]) ORDER BY id FOR SHARE''', org, conversation, sorted(source_ids, key=str))
        found_source_ids = {_trusted_uuid(row['id']) for row in rows}
        if found_source_ids != source_ids:
            raise WorkerProofDenied('State source unavailable')
        source, stamp, source_hash = await BookingConversationRepository._source(conn, org, conversation, message)
        current = await conn.fetchrow('''SELECT generation FROM booking_worker_claims
            WHERE organization_id=$1 AND message_id=$2 AND claim_id=$3 AND generation=$4 AND conversation_id=$5
              AND lease_until>clock_timestamp() FOR UPDATE''', org, message, claim.claim_id, claim.generation, conversation)
        if not current:
            raise WorkerProofDenied('Booking claim expired or changed')
        conv = await conn.fetchrow('SELECT * FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE', org, conversation)
        if (not conv or conv['deleted_at'] is not None or conv['status'] != 'active'
                or conv['ticket_status'] != 'AI_ACTIVE' or conv['canale'] != 'whatsapp'
                or conv['contact_id'] != source['c24_original_subject_id']):
            raise WorkerProofDenied('Conversation unavailable')
        contact = await conn.fetchrow('SELECT * FROM contacts WHERE organization_id=$1 AND id=$2 FOR SHARE', org, conv['contact_id'])
        if not contact or contact['deleted_at'] is not None or contact['consent_status'] == 'withdrawn':
            raise WorkerProofDenied('Current consent unavailable')
        await self._allocation._gdpr(conn, org, conv['contact_id'])
        if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM booking_conversation_revocations WHERE organization_id=$1 AND conversation_id=$2)
            OR EXISTS(SELECT 1 FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND deleted_at IS NOT NULL)
            ''', org, conversation):
            raise WorkerProofDenied('Conversation evidence no longer current')
        confirmed, confirmed_stamp, _ = await BookingConversationRepository._source(
            conn, org, conversation, confirmation.source_message_id,
        )
        prompt = await conn.fetchrow('''SELECT direction,deleted_at,status,sent_at,content_text,wam_id,c24_original_subject_id
            FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND id=$3 FOR SHARE''',
            org, conversation, confirmation.prompt_message_id)
        if (not prompt or prompt['direction'] != 'outbound' or prompt['deleted_at'] is not None
                or prompt['status'] not in {'sent','delivered','read'} or not prompt['wam_id']
                or prompt['sent_at'] is None or prompt['c24_original_subject_id'] != conv['contact_id']
                or not isinstance(prompt['content_text'], str)
                or sha256(prompt['content_text'].encode()).hexdigest() != confirmation.prompt_body_hash
                or confirmed['content_text'] != f'CONFIRM {confirmation.prompt_message_id}'
                or not stamp <= int(prompt['sent_at'].timestamp()) < confirmed_stamp):
            raise WorkerProofDenied('Verified customer confirmation unavailable')
        if not await conn.fetchrow('''SELECT r.message_id FROM inbound_worker_receipts r
            JOIN meta_webhook_inbox i ON i.id=r.inbox_id AND i.organization_id=r.organization_id
            WHERE r.organization_id=$1 AND i.organization_id=$1 AND r.message_id=$2
              AND i.status='completed' AND i.channel='whatsapp' FOR SHARE OF r,i''',
            org, confirmation.source_message_id):
            raise WorkerProofDenied('Trusted customer confirmation ingress unavailable')
        import re
        other_sources = await conn.fetch('''SELECT id,content FROM messages WHERE organization_id=$1
            AND conversation_id=$2 AND direction='inbound' AND deleted_at IS NULL LIMIT 257 FOR SHARE''', org, conversation)
        if len(other_sources) > 256:
            raise WorkerProofDenied('Source ordering unavailable')
        for other in other_sources:
            if _trusted_uuid(other['id']) in {message, confirmation.source_message_id}:
                continue
            body = decode(other['content'])
            other_stamp = body.get('timestamp') if isinstance(body, dict) else None
            if type(other_stamp) is not str or not re.fullmatch(r'[0-9]{1,12}', other_stamp) or int(other_stamp) >= confirmed_stamp:
                raise WorkerProofDenied('Source ordering unavailable')
        cached, response_hash = await BookingConversationRepository._canonical_response(conn, org, source)
        payload_hash = digest({'organization_id': str(org), 'message_id': str(message),
                               'channel': 'whatsapp', 'text': source['content_text']})
        operation = QuotaOperation(UUID(str(org)), UUID(str(message)), 'inbound', payload_hash)
        provenance = await conn.fetchrow('''SELECT o.operation_id,o.inbox_id FROM inbound_worker_operations o
            JOIN inbound_worker_receipts r ON r.organization_id=o.organization_id AND r.inbox_id=o.inbox_id AND r.message_id=o.message_id
            JOIN meta_webhook_inbox i ON i.id=r.inbox_id AND i.organization_id=r.organization_id
            WHERE o.organization_id=$1 AND r.organization_id=$1 AND i.organization_id=$1 AND o.message_id=$2 AND o.operation_id=$3
              AND i.status='completed' AND i.channel='whatsapp' FOR SHARE OF o, r, i''', org, message, operation.id)
        if not provenance:
            raise WorkerProofDenied('Trusted inbound provenance unavailable')
        await conn.fetchval('SELECT pg_advisory_xact_lock_shared(hashtext($1))', f'booking-capacity|{org}')
        settings = await conn.fetchrow('SELECT * FROM booking_settings WHERE organization_id=$1 FOR SHARE', org)
        catalog, sector, authority = await self._allocation._catalog(conn, org, organization, settings)
        config = decode(settings['config']) if settings else None
        if (not isinstance(config, dict) or authority != 'internal'
            or catalog.sector != sector or catalog.timezone != organization['timezone']
            or catalog.revision != expected_catalog
            or cached.get('booking_catalog_revision') != catalog.revision or cached.get('richiede_umano') is not False):
            raise WorkerProofDenied('Current policy or catalog unavailable')
        if sector == 'ristorante':
            if (not isinstance(config.get('a05'), dict) or config['a05'].get('enabled') is not True
                or not validate_auto_booking_configuration({**dict(settings), 'config': config,
                    'capienze_orarie': decode(settings['capienze_orarie'])}, organization['timezone'], sector).valid):
                raise WorkerProofDenied('Current policy or catalog unavailable')
        elif sector in {'parrucchiere', 'centro_estetico', 'hotel_bnb', 'studio_medico_dentista'}:
            if not await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM booking_sector_admission_fences
                    WHERE organization_id=$1 AND sector=$2)''', org, sector):
                raise WorkerProofDenied('Sector admission fence unavailable')
        else:
            raise WorkerProofDenied('Current policy or catalog unavailable')
        post = await conn.fetchrow('''SELECT snapshot_hash,revision,catalog_revision FROM booking_conversation_states
            WHERE organization_id=$1 AND conversation_id=$2 FOR UPDATE''', org, conversation)
        if not post or any(post[k] != pre[k] for k in ('snapshot_hash','revision','catalog_revision')):
            raise WorkerProofDenied('State changed while locking')
        state, state_row = await BookingConversationRepository(None)._load(conn, org, conversation)
        if (not state or state.revision != expected_revision or state.catalog_revision != catalog.revision
                or state.last_source_message_id != confirmation.source_message_id or not state.details_confirmed):
            raise WorkerProofDenied('Conversation state changed')
        slots = {slot.field: slot.value for slot in state.slots}
        if sector == 'ristorante':
            slot_policy = parse_and_validate_auto_booking(
                {'config': config, 'capienze_orarie': decode(settings['capienze_orarie'])},
                organization['timezone'],
                {'service': 'ristorante/tavolo', 'date': slots.get('appointment_date'),
                 'time': slots.get('appointment_time'), 'party_size': slots.get('party_size'),
                 'duration_minutes': state.derived.duration_minutes},
                now=await conn.fetchval('SELECT clock_timestamp()'),
            )
            if not slot_policy.accepted:
                raise WorkerProofDenied('Current slot policy denied')
            slot = slot_policy.booking
        elif sector == 'hotel_bnb':
            from src.core.bookings.catalog_contracts import CivilNightDuration
            from src.core.bookings.hotel_admission_policy import (
                validate_hotel_admission,
            )
            try:
                arrival = date.fromisoformat(slots['arrival_date'])
                departure = date.fromisoformat(slots['departure_date'])
                room_count = slots.get('room_count')
                guests = slots['guest_count']
                if type(guests) is not int or guests < 1:
                    raise ValueError('Invalid guest count')
                service = next(item for item in catalog.services if item.service_id == slots.get('service_id'))
                if not isinstance(service.duration, CivilNightDuration):
                    raise ValueError('Invalid stay service')
                candidate_request = AllocationRequest(
                    request_id=uuid4(), booking_id=uuid4(), action='allocate',
                    expected_allocation_revision=0, expected_catalog_revision=catalog.revision,
                    service_id=service.service_id, arrival_date=arrival, departure_date=departure,
                    room_count=room_count,
                )
                validate_hotel_admission(config, catalog, candidate_request, guest_count=guests,
                                         now=await conn.fetchval('SELECT clock_timestamp()'))
                slot = _HotelSlot(arrival, service.duration.check_in, departure, room_count, guests)
            except (KeyError, ValueError, TypeError, StopIteration):
                raise WorkerProofDenied('Current stay policy denied') from None
        else:
            from src.core.bookings.sector_admission_policy import (
                SectorAdmissionDenied,
                validate_sector_admission,
            )
            try:
                day = date.fromisoformat(slots['appointment_date'])
                clock = time.fromisoformat(slots['appointment_time'])
                candidate_request = AllocationRequest(
                    request_id=uuid4(), booking_id=uuid4(), action='allocate',
                    expected_allocation_revision=0, expected_catalog_revision=catalog.revision,
                    service_id=slots.get('service_id'), appointment_date=day, appointment_time=clock,
                )
                validate_sector_admission(config, catalog, candidate_request,
                                          now=await conn.fetchval('SELECT clock_timestamp()'))
            except (KeyError, ValueError, TypeError, SectorAdmissionDenied):
                raise WorkerProofDenied('Current slot policy denied') from None
            slot = _SectorSlot(day, clock)
        if not await conn.fetchrow('''SELECT request_id FROM booking_conversation_requests
            WHERE organization_id=$1 AND conversation_id=$2 AND source_message_id=$3
              AND action='proposal' AND applied_revision=$4 AND canonical_response_hash=$5
              AND state_generation=$6 FOR SHARE''',
            org, conversation, message, confirmation.prompt_revision, response_hash, state_row['state_generation']):
            raise WorkerProofDenied('Confirmed proposal receipt unavailable')
        if not await conn.fetchval('''SELECT lease_until>clock_timestamp() FROM booking_worker_claims
            WHERE organization_id=$1 AND message_id=$2 AND claim_id=$3 AND generation=$4''',
            org, message, claim.claim_id, claim.generation):
            raise WorkerProofDenied('Booking claim expired')
        return (org, conversation, message, _trusted_uuid(conv['contact_id']), stamp, source_hash, response_hash,
                state.revision, state_digest(state), catalog.revision, provenance['inbox_id'],
                operation.id, slot, slots.get('service_id'))

    async def mint(self, conn, claim, conversation, *, expected_state_revision, expected_catalog_revision):
        await _require_private_identity(conn)
        if type(expected_state_revision) is not int or expected_state_revision < 1 or type(expected_catalog_revision) is not int or expected_catalog_revision < 1:
            raise ValueError('Strict revision expectations required')
        proof = await self._proof(conn, claim, conversation, expected_state_revision, expected_catalog_revision)
        token = _Capability(self, conn, await conn.fetchval('SELECT txid_current()'), asyncio.current_task(), claim, proof)
        self._issued.add(token)
        return token

    async def _consume(self, conn, capability):
        await _require_private_identity(conn)
        if (type(capability) is not _Capability or capability not in self._issued
                or capability._owner is not self or capability._task is not asyncio.current_task()
                or not conn.is_in_transaction()):
            raise WorkerProofDenied('Capability scope changed')
        self._issued.remove(capability)
        prior = capability._proof
        if capability._database_id is not None:
            if not isinstance(capability._payload, dict) or not capability._payload_hash:
                raise WorkerProofDenied('Database capability payload unavailable')
            consumed = await conn.fetchval(
                'SELECT c24_private.c24_worker_consume_capability($1)', capability._database_id,
            )
            consumed = decode(consumed)
            if not isinstance(consumed, dict) or canonical(consumed) != canonical(capability._payload):
                raise WorkerProofDenied('Database capability scope changed')
            current = (await self._proof_a05(conn, capability._claim)
                       if capability._kind == 'a05' else
                       await self._proof(conn, capability._claim, prior[1], prior[7], prior[9]))
            if current != prior:
                raise WorkerProofDenied('Verified evidence changed')
            return prior
        if (capability._connection is not conn
                or await conn.fetchval('SELECT txid_current()') != capability._transaction_id):
            raise WorkerProofDenied('Capability scope changed')
        current = (await self._proof_a05(conn, capability._claim)
                   if capability._kind == 'a05' else
                   await self._proof(conn, capability._claim, prior[1], prior[7], prior[9]))
        if current != prior:
            raise WorkerProofDenied('Verified evidence changed')
        return prior

    async def revalidate(self, conn, capability):
        prior = await self._consume(conn, capability)
        request, booking, digest = service_receipt_v2(prior[0], prior[2], 'create', prior[6])
        return {'request_id': request, 'booking_id': booking, 'semantic_hash': digest,
                'runtime_enabled': False, 'confirmation_authorized': False}

    async def _mark_sector_admission(self, conn, org, booking):
        await conn.execute('SELECT c24_private.c24_worker_mark_sector_insert($1,$2)', org, booking)

    async def create_booking(self, conn, capability):
        """Create a confirmed internal A05 booking from a single-use capability."""
        await _require_private_identity(conn)
        if not private_booking_worker_enabled():
            raise WorkerProofDenied('Private booking worker disabled')
        prior = await self._consume(conn, capability)
        if type(prior) is not tuple:
            raise WorkerProofDenied('Booking proof shape unavailable')
        if capability._kind == 'a05':
            if len(prior) != 16:
                raise WorkerProofDenied('A05 proof shape unavailable')
            inbox, operation, slot, service_id, authority, channel = prior[10:]
        elif capability._kind == 'c24':
            if len(prior) != 14:
                raise WorkerProofDenied('C2.4 proof shape unavailable')
            inbox, operation, slot, service_id = prior[10:]
            # Generic confirmed-state proof validates these server-side.
            # It never grants Google authority or a non-WhatsApp channel.
            authority, channel = 'internal', 'whatsapp'
        else:
            raise WorkerProofDenied('Unknown booking proof kind')
        if channel != 'whatsapp':
            raise WorkerProofDenied('Booking proof channel unavailable')
        if (capability._kind == 'a05'
                and authority not in {'internal', 'internal_calendar_sync', 'google_authoritative'}):
            raise WorkerProofDenied('A05 authority unavailable')
        org, conversation, message, subject = prior[:4]
        request_id, booking_id, semantic_hash = service_receipt_v2(org, message, 'create', prior[6])
        temporal = ({'arrival_date': slot.date, 'departure_date': slot.departure, 'room_count': slot.room_count}
                    if isinstance(slot, _HotelSlot) else
                    {'appointment_date': slot.date, 'appointment_time': slot.time,
                     'party_size': slot.party_size if slot.capacity is not None else None})
        request = AllocationRequest(
            request_id=request_id, booking_id=booking_id, action='allocate',
            expected_allocation_revision=0, expected_catalog_revision=prior[9],
            service_id=service_id, **temporal,
        )
        async with conn.transaction():
            # Restaurant admission still shares the legacy hourly capacity lock.
            # Fenced sectors already hold the tenant admission lock and use
            # resource locks; taking an hour lock after that fence would invert
            # the legacy writer's hour-then-fence order.
            if slot.capacity is not None:
                await conn.fetchval('SELECT pg_advisory_xact_lock(hashtext($1))',
                                    f'{org}|{slot.date.isoformat()}|{slot.time.hour}')
            existing = await conn.fetchval(
                'SELECT id FROM bookings WHERE organization_id=$1 AND source_message_id=$2',
                org, str(message),
            )
            if existing is not None:
                if existing != booking_id:
                    raise WorkerProofDenied('Booking source already used')
                result = await self._allocation._apply_worker_on_connection(
                    conn, org, message, inbox, operation, semantic_hash, request,
                )
                if not result['replayed'] or result['outcome'] != 'allocated':
                    raise WorkerProofDenied('Existing booking receipt unavailable')
                if not await conn.fetchval('SELECT c24_private.c24_worker_finish_job()'):
                    raise WorkerProofDenied('Booking capability finalization failed')
                return booking_id
            if slot.capacity is not None:
                used = await conn.fetchval("""SELECT COALESCE(SUM(coperti),0) FROM booking_availability
                    WHERE organization_id=$1 AND data=$2 AND EXTRACT(HOUR FROM ora)=$3
                      AND stato IN ('in_attesa','confermata','da_verificare','completata')""",
                    org, slot.date, slot.time.hour,
                )
                if used + slot.party_size > slot.capacity:
                    raise WorkerProofDenied('Slot capacity unavailable')
            # Bind the single booking identifier to this transaction capability
            # for every sector. The database wrapper also records a fenced
            # admission when the tenant has enabled that policy.
            await self._mark_sector_admission(conn, org, booking_id)
            # Only a customer-authored self-identification supplies the booking name.
            from src.core.inbound.service import _customer_name_from_messages
            source = await conn.fetchrow('''SELECT content_text FROM messages WHERE organization_id=$1
                AND conversation_id=$2 AND id=$3 AND direction='inbound' AND deleted_at IS NULL''',
                org, conversation, message)
            name = _customer_name_from_messages([source]) if source else None
            phone = await conn.fetchval('SELECT phone_number FROM contacts WHERE organization_id=$1 AND id=$2',
                                        org, subject)
            if not name or not phone:
                raise WorkerProofDenied('Verified customer details unavailable')
            from src.core.gdpr.guard import check_contact_intake
            await check_contact_intake(conn, org, phone)
            google_pending = authority == 'google_authoritative'
            await conn.execute("""INSERT INTO worker_booking_inserts
                (id,organization_id,contact_id,id_conversazione,nome_cliente,telefono,data,ora,coperti,
                 note,stato,origine,source_message_id,calendar_confirm_pending,richiede_intervento)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'',$11,'WhatsApp',$10,$12,$12)""",
                booking_id, org, subject, str(conversation), name, phone, slot.date, slot.time,
                slot.party_size, str(message), 'in_attesa' if google_pending else 'confermata', google_pending,
            )
            booking = await conn.fetchrow(
                'SELECT * FROM c24_private.bookings WHERE organization_id=$1 AND id=$2',
                org, booking_id,
            )
            if not booking:
                raise WorkerProofDenied('Scoped booking insert unavailable')
            if isinstance(slot, _HotelSlot):
                await conn.execute('''INSERT INTO worker_booking_stays_inserts(organization_id,booking_id,original_subject_id,
                    original_conversation_id,source_message_id,arrival_date,departure_date,revision)
                    VALUES($1,$2,$3,$4,$5,$6,$7,1)''',
                    org, booking_id, subject, conversation, message, slot.date, slot.departure)
            result = await self._allocation._apply_worker_on_connection(
                conn, org, message, inbox, operation, semantic_hash, request,
            )
            if result['replayed'] or result['outcome'] != 'allocated':
                raise WorkerProofDenied('Slot allocation unavailable')
            if isinstance(slot, _HotelSlot):
                from src.core.bookings.hotel_confirmation import (
                    enqueue_hotel_confirmation,
                )
                await enqueue_hotel_confirmation(
                    conn, dict(booking), message, slot.date, slot.departure, worker=True,
                    outbound_message_id=UUID(capability._payload['effects']['outbound_message']['id']),
                )
            elif google_pending:
                queued = await conn.fetchval(
                    'SELECT c24_private.c24_worker_attach_calendar_source($1,$2,$3)',
                    org, booking_id, message,
                )
                if not queued:
                    raise WorkerProofDenied('Authoritative Calendar job unavailable')
            else:
                await BookingRepository(None).enqueue_calendar_confirmation(
                    conn, dict(booking), message, 'whatsapp', worker=True,
                    outbound_message_id=UUID(capability._payload['effects']['outbound_message']['id']),
                )
            claim = capability._claim
            if not await conn.fetchval('''SELECT lease_until>clock_timestamp() FROM booking_worker_claims
                WHERE organization_id=$1 AND message_id=$2 AND claim_id=$3 AND generation=$4''',
                org, message, claim.claim_id, claim.generation):
                raise WorkerProofDenied('Booking claim expired')
            if not await conn.fetchval('SELECT c24_private.c24_worker_finish_job()'):
                raise WorkerProofDenied('Booking capability finalization failed')
            return booking_id
