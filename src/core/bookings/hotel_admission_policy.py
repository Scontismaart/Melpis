"""Deterministic catalog admission for explicitly opted-in hotel stays.

This is not authorization: a caller must additionally prove the customer
confirmation, tenant provenance, consent, and guarded allocation transaction.
"""
from datetime import datetime, timezone
from typing import Mapping

from src.core.bookings.allocation_contracts import AllocationRequest, derive_allocation
from src.core.bookings.catalog_contracts import CatalogDraft, CivilNightDuration
from src.core.bookings.sector_admission_policy import SectorAdmissionDenied
from src.core.db.repositories.booking_allocation_repo import BookingAllocationRepository


def validate_hotel_admission(config: Mapping, catalog: CatalogDraft,
                             request: AllocationRequest, *, guest_count: int, now: datetime):
    policy = config.get('c24_sector_auto') if isinstance(config, Mapping) else None
    if (not isinstance(policy, dict) or set(policy) != {'enabled', 'version', 'sector', 'authority'}
            or policy['enabled'] is not True or type(policy['version']) is not int or policy['version'] != 1
            or policy['sector'] != 'hotel_bnb' or catalog.sector != 'hotel_bnb'
            or policy['authority'] != 'internal'):
        raise SectorAdmissionDenied('Hotel admission policy unavailable')
    if (type(request) is not AllocationRequest or request.action != 'allocate'
            or request.expected_allocation_revision != 0 or request.expected_catalog_revision != catalog.revision
            or request.arrival_date is None or request.departure_date is None
            or request.appointment_date is not None or request.appointment_time is not None
            or request.party_size is not None):
        raise SectorAdmissionDenied('Stay unavailable')
    service = next((item for item in catalog.services if item.service_id == request.service_id), None)
    if (service is None or not isinstance(service.duration, CivilNightDuration)
            or service.handoff.staff_review_required or service.handoff.party_size_above is not None):
        raise SectorAdmissionDenied('Stay requires human review')
    if not any(field.key == 'guest_count' and field.required for field in service.fields):
        raise SectorAdmissionDenied('Guest count must be required by the published service')
    resources = {item.resource_id: item for item in catalog.resources}
    if not any(resources[item.resource_id].kind == 'room' for item in service.resources):
        raise SectorAdmissionDenied('Hotel room resources unavailable')
    if type(guest_count) is not int or guest_count < 1:
        raise SectorAdmissionDenied('Verified guest count unavailable')
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise SectorAdmissionDenied('Current time unavailable')
    try:
        start, end, claims = derive_allocation(request, catalog)
        fits = BookingAllocationRepository._weekly_fits(catalog.availability, start, end, catalog.timezone)
    except (ValueError, TypeError, OverflowError):
        raise SectorAdmissionDenied('Stay unavailable') from None
    room_claims = [claim for claim in claims if claim[1] == 'room']
    # Separate room-resource IDs may represent overlapping inventory; their
    # guest capacities cannot safely be added without a disjointness contract.
    if len(room_claims) != 1:
        raise SectorAdmissionDenied('Room occupancy capacity is unproven')
    room = resources[room_claims[0][0]]
    if room.max_occupants is None or guest_count > room_claims[0][3] * room.max_occupants:
        raise SectorAdmissionDenied('Room occupancy capacity is unavailable')
    if start <= now.astimezone(timezone.utc) or fits is not True or not claims:
        raise SectorAdmissionDenied('Stay unavailable')
    return start, end, claims
