"""Private, dormant typed booking transaction; no route or worker access."""
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.core.bookings.allocation_contracts import AllocationRequest, derive_allocation, semantic_request_hash
from src.core.db.repositories.booking_allocation_repo import (
    AllocationConflict, AllocationDenied, AllocationUnavailable, BookingAllocationRepository, _booking_fingerprint, _uuid,
)
from src.core.db.repositories.booking_repo import BookingRepository


class BookingMutationRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class NewBookingLineage:
    contact_id: UUID | None
    conversation_id: UUID | None
    source_message_id: UUID | None = None

    def __post_init__(self):
        if any(value is not None and type(value) is not UUID for value in (self.contact_id, self.conversation_id, self.source_message_id)):
            raise ValueError('Trusted lineage requires UUID values')


@dataclass(frozen=True)
class BookingMutation:
    """Explicit command shape for the currently supported dark operations."""
    operation: str
    nome_cliente: str
    telefono: str
    note: str
    data: str | None = None
    ora: str | None = None
    coperti: int | None = None
    notice: bool = False
    arrival_date: str | None = None
    departure_date: str | None = None
    expected_stay_revision: int | None = None

    def __post_init__(self):
        if self.operation not in {"create", "edit_contact", "reschedule", "cancel", "reject", "no_show", "create_stay", "reschedule_stay", "confirm", "complete", "reactivate"}:
            raise BookingMutationRejected("Unsupported booking operation")
        if any(type(v) is not str for v in (self.nome_cliente, self.telefono, self.note)) or type(self.notice) is not bool:
            raise ValueError("Invalid mutation fields")
        if self.operation in {"create", "reschedule", "create_stay", "reschedule_stay"}:
            from datetime import date, time
            if type(self.data) is not str or type(self.ora) is not str or type(self.coperti) is not int or self.coperti <= 0:
                raise ValueError("Invalid booking fields")
            date.fromisoformat(self.data)
            parsed = time.fromisoformat(self.ora)
            if parsed.tzinfo or parsed.second or parsed.microsecond:
                raise ValueError("Invalid booking time")
            if self.operation == 'reschedule' and (self.notice or any((self.nome_cliente, self.telefono, self.note))):
                raise ValueError('Reschedule cannot change contact or send notice')
        elif self.data is not None or self.ora is not None or self.coperti is not None:
            raise ValueError("Contact edit cannot change scheduling")
        if self.operation in {'cancel', 'reject', 'no_show'} and (self.notice or any((self.nome_cliente, self.telefono, self.note))):
            raise ValueError('Status operation cannot change contact or send notice')
        if self.operation in {'confirm', 'complete', 'reactivate'} and (any((self.nome_cliente, self.telefono, self.note)) or (self.operation != 'confirm' and self.notice)):
            raise ValueError('Occupied transition cannot change contact or send completion notice')
        if self.operation in {'create_stay', 'reschedule_stay'}:
            from datetime import date
            if (type(self.arrival_date) is not str or type(self.departure_date) is not str
                or date.fromisoformat(self.arrival_date) >= date.fromisoformat(self.departure_date)
                or self.data != self.arrival_date or self.notice
                or (self.operation == 'create_stay' and self.expected_stay_revision is not None)
                or (self.operation == 'reschedule_stay' and (type(self.expected_stay_revision) is not int or self.expected_stay_revision < 1
                    or any((self.nome_cliente, self.telefono, self.note))))):
                raise ValueError('Invalid stay command')
        elif self.arrival_date is not None or self.departure_date is not None or self.expected_stay_revision is not None:
            raise ValueError('Stay details require a stay command')

    def digest(self, lineage):
        from dataclasses import asdict
        from hashlib import sha256
        import json
        command = asdict(self)
        for field in ('arrival_date', 'departure_date', 'expected_stay_revision'):
            if command[field] is None:
                del command[field]
        origin = asdict(lineage) if lineage else None
        if origin is not None and origin['source_message_id'] is None:
            del origin['source_message_id']
        # Preserve the original v1 create/contact receipt contract. Adding
        # optional stay fields must not change hashes for existing commands.
        payload = {"version": 1, "mutation": command, "lineage": origin}
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


async def apply_booking_mutation(pool: Any, organization_id: Any, auth_user_id: Any,
                                 request: AllocationRequest, *,
                                 mutation: BookingMutation,
                                 new_booking: NewBookingLineage | None = None) -> dict:
    if not isinstance(request, AllocationRequest):
        raise ValueError('AllocationRequest required')
    if not isinstance(mutation, BookingMutation):
        raise ValueError('BookingMutation required')
    if (mutation.operation in {'create', 'create_stay'}) != (new_booking is not None):
        raise ValueError('Mutation and lineage do not match')
    if new_booking is not None and new_booking.source_message_id is not None and mutation.operation != 'create_stay':
        raise ValueError('Source proof requires a stay command')
    mutation_hash = mutation.digest(new_booking)
    org, auth = _uuid(organization_id), _uuid(auth_user_id)
    engine = BookingAllocationRepository(pool)
    expected = {'allocate': 'allocated', 'replace': 'replaced', 'cancel': 'cancelled'}
    async with pool.acquire() as conn, conn.transaction(isolation='read_committed'):
        orgrow = await conn.fetchrow('SELECT id,timezone,business_profile FROM organizations WHERE id=$1 FOR SHARE', org)
        if not orgrow:
            raise AllocationDenied('Organization unavailable')
        await conn.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(org))
        if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM booking_stay_purge_fences WHERE organization_id=$1 AND booking_id=$2)
            OR EXISTS(SELECT 1 FROM booking_stay_tombstones WHERE organization_id=$1 AND request_id=$3)''', org, request.booking_id, request.request_id):
            raise AllocationDenied('Purged booking or request cannot be replayed')
        actor = await engine._actor(conn, org, auth)
        before = await conn.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', org, request.booking_id)
        if before is None:
            if new_booking is None or request.action != 'allocate':
                raise AllocationDenied('Booking unavailable')
            lineage_row = {'contact_id': new_booking.contact_id, 'id_conversazione': new_booking.conversation_id}
        else:
            lineage_row = before
        conversation, subject = await engine._lineage(conn, org, lineage_row)
        stay_transition = mutation.operation in {'confirm', 'complete', 'reactivate'} and await conn.fetchval(
            'SELECT EXISTS(SELECT 1 FROM booking_stays WHERE organization_id=$1 AND booking_id=$2)', org, request.booking_id)
        if mutation.operation in {'create_stay', 'reschedule_stay'} or stay_transition:
            source = new_booking.source_message_id if new_booking else await conn.fetchval(
                'SELECT source_message_id FROM booking_stays WHERE organization_id=$1 AND booking_id=$2', org, request.booking_id)
            proof = await conn.fetchrow('''SELECT id FROM messages WHERE organization_id=$1 AND conversation_id=$2 AND id=$3
                AND direction='inbound' AND deleted_at IS NULL AND c24_original_subject_id=$4 FOR SHARE''',
                org, conversation, source, subject)
            if not proof:
                raise AllocationDenied('Verified stay source required')
        if before is not None and new_booking is not None:
            supplied = await engine._lineage(conn, org, {'contact_id': new_booking.contact_id, 'id_conversazione': new_booking.conversation_id})
            if supplied != (conversation, subject):
                raise AllocationDenied('Booking lineage differs')
        if conversation:
            conv = await conn.fetchrow('SELECT contact_id,deleted_at FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE', org, conversation)
            if not conv or conv['deleted_at'] is not None or conv['contact_id'] != subject:
                raise AllocationDenied('Conversation lineage unavailable')
            if await engine._lineage(conn, org, lineage_row) != (conversation, subject):
                raise AllocationDenied('Conversation lineage changed')
        await engine._gdpr(conn, org, subject)
        if mutation.telefono:
            # Manual/unlinked bookings must respect suppression fingerprints too;
            # omitting a contact FK is not permission to recreate erased identity.
            from src.core.gdpr.guard import check_contact_intake
            await check_contact_intake(conn, org, mutation.telefono)
        await conn.fetchval('SELECT pg_advisory_xact_lock_shared(hashtext($1))', f'booking-capacity|{org}')
        settings = await conn.fetchrow('SELECT config FROM booking_settings WHERE organization_id=$1 FOR SHARE', org)
        catalog, sector, authority = await engine._catalog(conn, org, orgrow, settings)
        if catalog.sector != sector or catalog.timezone != orgrow['timezone'] or authority not in {'internal', 'internal_calendar_sync'}:
            raise AllocationUnavailable('Catalog authority unavailable')
        if sector == 'studio_medico_dentista' and mutation.note:
            raise BookingMutationRejected('Medical booking notes are not accepted')
        if mutation.operation in {'create_stay', 'reschedule_stay'}:
            from datetime import time
            from src.core.bookings.catalog_contracts import CivilNightDuration
            service = next((item for item in catalog.services if item.service_id == request.service_id), None)
            if (service is None or not isinstance(service.duration, CivilNightDuration)
                    or time.fromisoformat(mutation.ora) != service.duration.check_in):
                raise BookingMutationRejected('Stay check-in differs from the published service')
        locked = await conn.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2 FOR UPDATE', org, request.booking_id)
        if (before is None) != (locked is None):
            raise AllocationConflict('Booking changed')
        if locked:
            current_lineage = await engine._lineage(conn, org, locked)
            if current_lineage != (conversation, subject):
                raise AllocationDenied('Booking lineage changed')
            await engine._gdpr(conn, org, subject)
        await conn.fetchval('SELECT pg_advisory_xact_lock(hashtextextended($1,723))', f'allocation-request|{org}|{request.request_id}')
        previous = await conn.fetchrow('''SELECT booking_id,actor_user_id,action,payload_hash,outcome,
            applied_allocation_revision,catalog_revision,mutation_hash FROM booking_allocation_requests
            WHERE organization_id=$1 AND request_id=$2''', org, request.request_id)
        if previous:
            digest = semantic_request_hash(request, organization_id=org, actor_user_id=actor)
            if (previous['booking_id'] != request.booking_id or previous['actor_user_id'] != actor
                or previous['action'] != request.action or previous['payload_hash'] != digest
                or previous['mutation_hash'] != mutation_hash):
                raise AllocationConflict('Request identifier was used for a different operation')
            current_set = await conn.fetchrow('SELECT allocation_revision,state FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2', org, request.booking_id)
            return engine._result(previous['outcome'], previous['applied_allocation_revision'], current_set,
                                  previous['catalog_revision'], catalog.revision, True)
        if before is not None and new_booking is not None:
            raise AllocationConflict('Booking already exists')
        contact_preimage = None
        if mutation.operation in {'create', 'create_stay'}:
            from datetime import date, time
            is_stay = mutation.operation == 'create_stay'
            if (request.action != 'allocate' or (is_stay and (catalog.sector != 'hotel_bnb'
                    or request.arrival_date != date.fromisoformat(mutation.arrival_date)
                    or request.departure_date != date.fromisoformat(mutation.departure_date)))
                    or (not is_stay and (request.appointment_date != date.fromisoformat(mutation.data)
                    or request.appointment_time != time.fromisoformat(mutation.ora)))
                    or (request.party_size is not None and request.party_size != mutation.coperti)):
                raise BookingMutationRejected('Allocation and booking differ')
            if is_stay and (not new_booking.contact_id or not new_booking.conversation_id or not new_booking.source_message_id
                or not await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM messages WHERE organization_id=$1
                  AND conversation_id=$2 AND id=$3 AND direction='inbound' AND deleted_at IS NULL
                  AND c24_original_subject_id=$4)''', org, new_booking.conversation_id,
                    new_booking.source_message_id, new_booking.contact_id)):
                raise AllocationDenied('Verified stay source required')
            await conn.execute("""INSERT INTO bookings
                (id,organization_id,contact_id,id_conversazione,nome_cliente,telefono,data,ora,coperti,note,stato)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'in_attesa')""", request.booking_id, org,
                new_booking.contact_id, str(new_booking.conversation_id) if new_booking.conversation_id else None,
                mutation.nome_cliente, mutation.telefono, date.fromisoformat(mutation.data),
                time.fromisoformat(mutation.ora), mutation.coperti, mutation.note)
            if is_stay:
                await conn.execute('''INSERT INTO booking_stays(organization_id,booking_id,original_subject_id,
                    original_conversation_id,source_message_id,arrival_date,departure_date,revision)
                    VALUES($1,$2,$3,$4,$5,$6,$7,1)''', org, request.booking_id, new_booking.contact_id,
                    new_booking.conversation_id, new_booking.source_message_id,
                    date.fromisoformat(mutation.arrival_date), date.fromisoformat(mutation.departure_date))
        elif mutation.operation in {'confirm', 'complete', 'reactivate'}:
            reactivating = mutation.operation == 'reactivate'
            if request.action != ('allocate' if reactivating else 'replace'):
                raise BookingMutationRejected('Occupied transition requires matching allocation action')
            allocation = await conn.fetchrow('''SELECT * FROM booking_allocation_sets
                WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE''', org, request.booking_id)
            if (not allocation or allocation['state'] != ('cancelled' if reactivating else 'active')
                or allocation['allocation_revision'] != request.expected_allocation_revision
                or allocation['catalog_revision'] != catalog.revision or request.expected_catalog_revision != catalog.revision
                or allocation['booking_fingerprint'] != _booking_fingerprint(locked)
                or allocation['booking_calendar_revision'] != locked['calendar_revision']
                or allocation['timezone'] != catalog.timezone or allocation['service_id'] != request.service_id
                or locked['stato'] not in ({'cancellata','cancellato','rifiutata'} if reactivating else {'in_attesa', 'confermata', 'da_verificare'})
                or locked['completata_at'] is not None or locked['no_show_at'] is not None
                or (request.party_size is not None and request.party_size != locked['coperti'])):
                raise AllocationConflict('Occupied allocation preimage changed')
            if allocation['temporal_kind'] == 'civil_nights':
                stay = await conn.fetchrow('SELECT * FROM booking_stays WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE', org, request.booking_id)
                if (not stay or stay['original_subject_id'] != subject or stay['original_conversation_id'] != conversation
                    or (stay['arrival_date'],stay['departure_date']) != (allocation['arrival_date'],allocation['departure_date'])
                    or (request.arrival_date,request.departure_date) != (stay['arrival_date'],stay['departure_date'])):
                    raise AllocationConflict('Stay preimage changed')
            elif (allocation['temporal_kind'] != 'minutes' or request.appointment_date != locked['data']
                  or request.appointment_time != locked['ora']):
                raise AllocationConflict('Booking schedule changed')
            try:
                start, end, derived = derive_allocation(request, catalog)
            except (ValueError, TypeError, OverflowError):
                raise AllocationUnavailable('Allocation proposal is invalid') from None
            claims = await conn.fetch('''SELECT resource_id,resource_kind,exclusive,quantity,starts_at,ends_at
                FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id''', org, request.booking_id)
            await engine._take_locks(conn, org, request, claims, derived)
            claims = await conn.fetch('''SELECT resource_id,resource_kind,exclusive,quantity,starts_at,ends_at
                FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id''', org, request.booking_id)
            if ((bool(claims) if reactivating else not claims) or (allocation['starts_at'],allocation['ends_at']) != (start,end)
                or (not reactivating and [(c['resource_id'],c['resource_kind'],c['exclusive'],c['quantity']) for c in claims] != list(derived))
                or any((c['starts_at'],c['ends_at']) != (start,end) for c in claims)):
                raise AllocationConflict('Occupied allocation claims changed')
            contact_preimage = await engine._seal_contact_preimage(conn, org, request, mutation=mutation)
            if mutation.operation == 'complete':
                await conn.execute('''UPDATE bookings SET stato='completata',completata_at=NOW(),updated_at=NOW()
                    WHERE organization_id=$1 AND id=$2''', org, request.booking_id)
            else:
                await conn.execute('''UPDATE bookings SET stato=$3,updated_at=NOW()
                    WHERE organization_id=$1 AND id=$2''', org, request.booking_id, 'in_attesa' if reactivating else 'confermata')
        elif mutation.operation == 'reschedule_stay':
            from datetime import date, time
            stay = await conn.fetchrow('SELECT * FROM booking_stays WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE', org, request.booking_id)
            allocation = await conn.fetchrow('SELECT * FROM booking_allocation_sets WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE', org, request.booking_id)
            if (request.action != 'replace' or catalog.sector != 'hotel_bnb' or not stay or not allocation
                or stay['original_subject_id'] != subject or stay['original_conversation_id'] != conversation
                or stay['revision'] != mutation.expected_stay_revision or allocation['state'] != 'active'
                or allocation['allocation_revision'] != request.expected_allocation_revision
                or allocation['catalog_revision'] != catalog.revision or request.expected_catalog_revision != catalog.revision
                or allocation['service_id'] != request.service_id
                or allocation['booking_fingerprint'] != _booking_fingerprint(locked)
                or allocation['booking_calendar_revision'] != locked['calendar_revision']
                or allocation['timezone'] != catalog.timezone or allocation['temporal_kind'] != 'civil_nights'
                or (allocation['arrival_date'], allocation['departure_date']) != (stay['arrival_date'], stay['departure_date'])
                or request.arrival_date != date.fromisoformat(mutation.arrival_date)
                or request.departure_date != date.fromisoformat(mutation.departure_date)
                or locked['stato'] not in {'in_attesa', 'confermata'} or locked['completata_at'] is not None or locked['no_show_at'] is not None):
                raise AllocationConflict('Stay preimage changed')
            claims = await conn.fetch('''SELECT resource_id,resource_kind,exclusive,quantity,starts_at,ends_at
                FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id''', org, request.booking_id)
            try:
                old_request = AllocationRequest.model_validate({**request.model_dump(), 'service_id': allocation['service_id'],
                    'arrival_date': stay['arrival_date'], 'departure_date': stay['departure_date']})
                old_start, old_end, old_claims = derive_allocation(old_request, catalog)
                _, _, new_claims = derive_allocation(request, catalog)
            except (ValueError, TypeError, OverflowError):
                raise AllocationUnavailable('Stay allocation cannot be verified') from None
            if (not claims or (old_start,old_end) != (allocation['starts_at'],allocation['ends_at'])
                or [(c['resource_id'],c['resource_kind'],c['exclusive'],c['quantity']) for c in claims] != list(old_claims)
                or old_claims != new_claims
                or any((c['starts_at'],c['ends_at']) != (old_start,old_end) for c in claims)):
                raise AllocationConflict('Stay claims changed')
            await engine._take_locks(conn, org, request, claims, new_claims)
            contact_preimage = await engine._seal_contact_preimage(conn, org, request, mutation=mutation)
            await conn.execute('''UPDATE bookings SET data=$3,ora=$4,coperti=$5,stato='in_attesa',updated_at=NOW()
                WHERE organization_id=$1 AND id=$2''', org, request.booking_id, date.fromisoformat(mutation.data), time.fromisoformat(mutation.ora), mutation.coperti)
            await conn.execute('''UPDATE booking_stays SET arrival_date=$3,departure_date=$4,revision=revision+1
                WHERE organization_id=$1 AND booking_id=$2''', org, request.booking_id, request.arrival_date, request.departure_date)
        elif mutation.operation in {'reschedule', 'cancel', 'reject', 'no_show'}:
            from datetime import date, time
            is_reschedule = mutation.operation == 'reschedule'
            if request.action != ('replace' if is_reschedule else 'cancel'):
                raise BookingMutationRejected('Mutation and allocation action differ')
            if is_reschedule and (request.appointment_date != date.fromisoformat(mutation.data)
                or request.appointment_time != time.fromisoformat(mutation.ora)
                or (request.party_size is not None and request.party_size != mutation.coperti)):
                raise BookingMutationRejected('Allocation and booking differ')
            allocation = await conn.fetchrow('''SELECT * FROM booking_allocation_sets
                WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE''', org, request.booking_id)
            if (not allocation or allocation['state'] != 'active'
                or allocation['allocation_revision'] != request.expected_allocation_revision
                or allocation['catalog_revision'] != catalog.revision or request.expected_catalog_revision != catalog.revision
                or allocation['booking_fingerprint'] != _booking_fingerprint(locked)
                or allocation['booking_calendar_revision'] != locked['calendar_revision']
                or allocation['timezone'] != catalog.timezone
                or (allocation['temporal_kind'] != 'minutes' and (is_reschedule or allocation['temporal_kind'] != 'civil_nights'))
                or (locked['stato'] not in {'in_attesa', 'confermata'}
                    and (mutation.operation, locked['stato']) != ('no_show', 'da_verificare'))
                or locked['completata_at'] is not None or locked['no_show_at'] is not None):
                raise AllocationConflict('Active allocation preimage changed')
            if is_reschedule and (request.service_id != allocation['service_id'] or request.room_count is not None):
                raise BookingMutationRejected('Reschedule cannot change service or room requirements')
            claims = await conn.fetch('''SELECT resource_id,resource_kind,exclusive,quantity,starts_at,ends_at
                FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id''', org, request.booking_id)
            try:
                if allocation['temporal_kind'] == 'civil_nights':
                    stay = await conn.fetchrow('SELECT * FROM booking_stays WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE', org, request.booking_id)
                    if (not stay or stay['original_subject_id'] != subject or stay['original_conversation_id'] != conversation
                        or (stay['arrival_date'],stay['departure_date']) != (allocation['arrival_date'],allocation['departure_date'])):
                        raise AllocationConflict('Stay preimage changed')
                    temporal = {'appointment_date': None, 'appointment_time': None,
                                'arrival_date': stay['arrival_date'], 'departure_date': stay['departure_date']}
                else:
                    temporal = {'appointment_date': locked['data'], 'appointment_time': locked['ora'],
                                'arrival_date': None, 'departure_date': None}
                old_request = AllocationRequest.model_validate({**request.model_dump(), **temporal, 'action': 'replace',
                    'service_id': allocation['service_id'], 'party_size': locked['coperti'], 'room_count': None})
                old_start, old_end, old_derived = derive_allocation(old_request, catalog)
            except (ValueError, TypeError, OverflowError):
                raise AllocationUnavailable('Old allocation cannot be verified') from None
            if (not claims or old_start != allocation['starts_at'] or old_end != allocation['ends_at']
                or [(c['resource_id'], c['resource_kind'], c['exclusive'], c['quantity']) for c in claims] != list(old_derived)
                or any(c['starts_at'] != old_start or c['ends_at'] != old_end for c in claims)):
                raise AllocationConflict('Allocation claims unavailable')
            if is_reschedule:
                try:
                    _, _, target_claims = derive_allocation(request, catalog)
                except (ValueError, TypeError, OverflowError):
                    raise AllocationUnavailable('Allocation proposal is invalid') from None
                await engine._take_locks(conn, org, request, claims, target_claims)
                contact_preimage = await engine._seal_contact_preimage(conn, org, request, mutation=mutation)
                await conn.execute('''UPDATE bookings SET data=$3,ora=$4,coperti=$5,stato='in_attesa',updated_at=NOW()
                    WHERE organization_id=$1 AND id=$2''', org, request.booking_id,
                    date.fromisoformat(mutation.data), time.fromisoformat(mutation.ora), mutation.coperti)
            else:
                await engine._take_locks(conn, org, request, claims, ())
                contact_preimage = await engine._seal_contact_preimage(conn, org, request, mutation=mutation)
                status = {'cancel': 'cancellata', 'reject': 'rifiutata', 'no_show': 'no_show'}[mutation.operation]
                await conn.execute('''UPDATE bookings SET stato=$3,
                    no_show_at=CASE WHEN $3='no_show' THEN NOW() ELSE no_show_at END,updated_at=NOW()
                    WHERE organization_id=$1 AND id=$2''', org, request.booking_id, status)
        else:
            if (request.action not in {'allocate', 'replace'} or request.appointment_date != locked['data']
                    or request.appointment_time != locked['ora']
                    or (request.party_size is not None and request.party_size != locked['coperti'])):
                raise BookingMutationRejected('Allocation and existing booking differ')
            if request.action == 'replace':
                allocation = await conn.fetchrow('''SELECT * FROM booking_allocation_sets
                    WHERE organization_id=$1 AND booking_id=$2 FOR UPDATE''', org, request.booking_id)
                if (not allocation or allocation['state'] != 'active'
                    or allocation['allocation_revision'] != request.expected_allocation_revision
                    or allocation['catalog_revision'] != catalog.revision
                    or request.expected_catalog_revision != catalog.revision
                    or allocation['booking_fingerprint'] != _booking_fingerprint(locked)
                    or allocation['booking_calendar_revision'] != locked['calendar_revision']
                    or allocation['timezone'] != catalog.timezone or allocation['temporal_kind'] != 'minutes'
                    or locked['stato'] not in {'in_attesa', 'confermata'}
                    or locked['completata_at'] is not None or locked['no_show_at'] is not None):
                    raise AllocationConflict('Active allocation preimage changed')
                try:
                    start, end, derived = derive_allocation(request, catalog)
                except (ValueError, TypeError, OverflowError):
                    raise AllocationUnavailable('Allocation proposal is invalid') from None
                claims = await conn.fetch('''SELECT resource_id,resource_kind,exclusive,quantity,starts_at,ends_at
                    FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id''', org, request.booking_id)
                await engine._take_locks(conn, org, request, claims, derived)
                claims = await conn.fetch('''SELECT resource_id,resource_kind,exclusive,quantity,starts_at,ends_at
                    FROM booking_resource_allocations WHERE organization_id=$1 AND booking_id=$2 ORDER BY resource_id''', org, request.booking_id)
                actual = [(c['resource_id'], c['resource_kind'], c['exclusive'], c['quantity']) for c in claims]
                if (allocation['service_id'] != request.service_id or allocation['starts_at'] != start
                    or allocation['ends_at'] != end or allocation['arrival_date'] != request.arrival_date
                    or allocation['departure_date'] != request.departure_date or actual != list(derived)
                    or any(c['starts_at'] != start or c['ends_at'] != end for c in claims)):
                    raise BookingMutationRejected('Allocation preimage differs')
                contact_preimage = await engine._seal_contact_preimage(conn, org, request)
            await conn.execute("""UPDATE bookings SET nome_cliente=$3,telefono=$4,note=$5,updated_at=NOW()
                WHERE organization_id=$1 AND id=$2""", org, request.booking_id,
                mutation.nome_cliente, mutation.telefono, mutation.note)
        result = await engine._apply_on_connection(conn, org, auth, request, mutation_hash=mutation_hash,
                                                   contact_preimage=contact_preimage)
        if result['replayed'] or result['outcome'] != expected[request.action]:
            raise BookingMutationRejected('Allocation was not applied')
        if mutation.notice:
            booking = await conn.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', org, request.booking_id)
            if booking['stato'] != 'confermata':
                raise BookingMutationRejected('Confirmation notice requires a confirmed booking')
            await BookingRepository(pool).enqueue_calendar_confirmation(conn, dict(booking), None, 'whatsapp')
        return result
