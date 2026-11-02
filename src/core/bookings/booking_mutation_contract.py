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

    def __post_init__(self):
        if any(value is not None and type(value) is not UUID for value in (self.contact_id, self.conversation_id)):
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

    def __post_init__(self):
        if self.operation not in {"create", "edit_contact"}:
            raise BookingMutationRejected("Unsupported booking operation")
        if any(type(v) is not str for v in (self.nome_cliente, self.telefono, self.note)) or type(self.notice) is not bool:
            raise ValueError("Invalid mutation fields")
        if self.operation == "create":
            from datetime import date, time
            if type(self.data) is not str or type(self.ora) is not str or type(self.coperti) is not int or self.coperti <= 0:
                raise ValueError("Invalid booking fields")
            date.fromisoformat(self.data)
            parsed = time.fromisoformat(self.ora)
            if parsed.tzinfo or parsed.second or parsed.microsecond:
                raise ValueError("Invalid booking time")
        elif self.data is not None or self.ora is not None or self.coperti is not None:
            raise ValueError("Contact edit cannot change scheduling")

    def digest(self, lineage):
        from dataclasses import asdict
        from hashlib import sha256
        import json
        payload = {"version": 1, "mutation": asdict(self), "lineage": asdict(lineage) if lineage else None}
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


async def apply_booking_mutation(pool: Any, organization_id: Any, auth_user_id: Any,
                                 request: AllocationRequest, *,
                                 mutation: BookingMutation,
                                 new_booking: NewBookingLineage | None = None) -> dict:
    if not isinstance(request, AllocationRequest):
        raise ValueError('AllocationRequest required')
    if not isinstance(mutation, BookingMutation):
        raise ValueError('BookingMutation required')
    if (mutation.operation == 'create') != (new_booking is not None):
        raise ValueError('Mutation and lineage do not match')
    mutation_hash = mutation.digest(new_booking)
    org, auth = _uuid(organization_id), _uuid(auth_user_id)
    engine = BookingAllocationRepository(pool)
    expected = {'allocate': 'allocated', 'replace': 'replaced', 'cancel': 'cancelled'}
    async with pool.acquire() as conn, conn.transaction(isolation='read_committed'):
        orgrow = await conn.fetchrow('SELECT id,timezone,business_profile FROM organizations WHERE id=$1 FOR SHARE', org)
        if not orgrow:
            raise AllocationDenied('Organization unavailable')
        await conn.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended($1::text,606))', str(org))
        actor = await engine._actor(conn, org, auth)
        before = await conn.fetchrow('SELECT * FROM bookings WHERE organization_id=$1 AND id=$2', org, request.booking_id)
        if before is None:
            if new_booking is None or request.action != 'allocate':
                raise AllocationDenied('Booking unavailable')
            lineage_row = {'contact_id': new_booking.contact_id, 'id_conversazione': new_booking.conversation_id}
        else:
            lineage_row = before
        conversation, subject = await engine._lineage(conn, org, lineage_row)
        if before is not None and new_booking is not None:
            supplied = await engine._lineage(conn, org, {'contact_id': new_booking.contact_id, 'id_conversazione': new_booking.conversation_id})
            if supplied != (conversation, subject):
                raise AllocationDenied('Booking lineage differs')
        if conversation:
            conv = await conn.fetchrow('SELECT contact_id,deleted_at FROM conversations WHERE organization_id=$1 AND id=$2 FOR UPDATE', org, conversation)
            if not conv or conv['deleted_at'] is not None or conv['contact_id'] != subject:
                raise AllocationDenied('Conversation lineage unavailable')
        await engine._gdpr(conn, org, subject)
        if mutation.telefono:
            # Manual/unlinked bookings must respect suppression fingerprints too;
            # omitting a contact FK is not permission to recreate erased identity.
            from src.core.gdpr.guard import check_contact_intake
            await check_contact_intake(conn, org, mutation.telefono)
        await conn.fetchval('SELECT pg_advisory_xact_lock_shared(hashtext($1))', f'booking-capacity|{org}')
        settings = await conn.fetchrow('SELECT config FROM booking_settings WHERE organization_id=$1 FOR SHARE', org)
        catalog, sector, authority = await engine._catalog(conn, org, orgrow, settings)
        if catalog.sector != sector or catalog.timezone != orgrow['timezone'] or authority != 'internal':
            raise AllocationUnavailable('Catalog authority unavailable')
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
        if mutation.operation == 'create':
            from datetime import date, time
            if (request.action != 'allocate' or request.appointment_date != date.fromisoformat(mutation.data)
                    or request.appointment_time != time.fromisoformat(mutation.ora)
                    or (request.party_size is not None and request.party_size != mutation.coperti)):
                raise BookingMutationRejected('Allocation and booking differ')
            await conn.execute("""INSERT INTO bookings
                (id,organization_id,contact_id,id_conversazione,nome_cliente,telefono,data,ora,coperti,note,stato)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'in_attesa')""", request.booking_id, org,
                new_booking.contact_id, str(new_booking.conversation_id) if new_booking.conversation_id else None,
                mutation.nome_cliente, mutation.telefono, date.fromisoformat(mutation.data),
                time.fromisoformat(mutation.ora), mutation.coperti, mutation.note)
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
