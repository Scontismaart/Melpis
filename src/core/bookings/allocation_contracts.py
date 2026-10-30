"""Pure, dark allocation contracts. No admission or runtime integration."""
from datetime import date, time, timedelta, timezone
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID
from pydantic import field_validator, model_validator
from src.core.bookings.catalog_contracts import Contract, Identifier, Positive, NonNegative, CatalogDraft, MinuteDuration, CivilNightDuration, civil_stay_interval
from src.core.calendar.availability import local_instant

class AllocationRequest(Contract):
    request_id: UUID
    booking_id: UUID
    action: Literal['allocate', 'replace', 'cancel']
    expected_allocation_revision: NonNegative
    expected_catalog_revision: Positive
    service_id: Identifier | None = None
    appointment_date: date | None = None
    appointment_time: time | None = None
    arrival_date: date | None = None
    departure_date: date | None = None
    party_size: Positive | None = None
    room_count: Positive | None = None

    @field_validator('appointment_time')
    @classmethod
    def local_minute(cls, value):
        if value is not None and (value.tzinfo is not None or value.second or value.microsecond):
            raise ValueError('Local minute time required')
        return value

    @model_validator(mode='after')
    def shape(self):
        appointment = self.appointment_date is not None and self.appointment_time is not None
        stay = self.arrival_date is not None and self.departure_date is not None
        supplied = (self.appointment_date, self.appointment_time, self.arrival_date, self.departure_date)
        if self.action == 'cancel':
            if self.service_id is not None or any(x is not None for x in supplied) or self.party_size is not None or self.room_count is not None:
                raise ValueError('Cancellation cannot carry allocation details')
        elif not self.service_id or appointment == stay or sum(x is not None for x in supplied) != 2:
            raise ValueError('Exactly one complete temporal form is required')
        elif stay and self.departure_date <= self.arrival_date:
            raise ValueError('Invalid civil stay')
        return self

def parse_request_json(payload: str) -> AllocationRequest:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate request key')
            result[key] = value
        return result
    json.loads(payload, object_pairs_hook=unique)
    return AllocationRequest.model_validate_json(payload)

def semantic_request_hash(request: AllocationRequest, *, organization_id: UUID, actor_user_id: UUID) -> str:
    """Stable replay hash of original request, never current derived catalog state."""
    if type(organization_id) is not UUID or type(actor_user_id) is not UUID:
        raise ValueError('Trusted organization and actor UUIDs required')
    data = {'version': 1, 'organization_id': str(organization_id), 'actor_user_id': str(actor_user_id), 'request': request.model_dump(mode='json')}
    return sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()

def derive_allocation(request: AllocationRequest, catalog: CatalogDraft):
    """Derive bounds and resource quantities only; never asserts availability."""
    if request.action == 'cancel':
        raise ValueError('Cancellation has no new allocation')
    if request.expected_catalog_revision != catalog.revision:
        raise ValueError('Catalog revision mismatch')
    service = next((s for s in catalog.services if s.service_id == request.service_id), None)
    if service is None:
        raise ValueError('Unknown service')
    duration = service.duration
    if isinstance(duration, MinuteDuration) and request.appointment_date is not None:
        start = local_instant(request.appointment_date.isoformat(), request.appointment_time.isoformat(), catalog.timezone).astimezone(timezone.utc)
        end = start + timedelta(minutes=duration.minutes + duration.buffer_after)
        start -= timedelta(minutes=duration.buffer_before)
    elif isinstance(duration, CivilNightDuration) and request.arrival_date is not None:
        start, end = civil_stay_interval(request.arrival_date, request.departure_date, duration, catalog.timezone)
        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    else:
        raise ValueError('Temporal form does not match service')
    resources = {r.resource_id: r for r in catalog.resources}
    claims = []
    exclusive_rooms = sum(1 for req in service.resources if resources[req.resource_id].kind == 'room' and resources[req.resource_id].exclusive)
    if exclusive_rooms and request.room_count is not None and request.room_count != exclusive_rooms:
        raise ValueError('Explicit room count mismatch')
    for requirement in service.resources:
        resource = resources[requirement.resource_id]
        quantity = requirement.quantity
        if resource.kind == 'seat_pool':
            if request.party_size is None:
                raise ValueError('Party size required')
            quantity *= request.party_size
        elif resource.kind == 'room' and not resource.exclusive:
            if request.room_count is None:
                raise ValueError('Room count required')
            quantity *= request.room_count
        if resource.exclusive and quantity != 1:
            raise ValueError('Exclusive resource quantity must be one')
        claims.append((resource.resource_id, resource.kind, resource.exclusive, quantity))
    return start, end, tuple(sorted(claims))
