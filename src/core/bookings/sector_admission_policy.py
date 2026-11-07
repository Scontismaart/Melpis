"""Deterministic eligibility for future hair/spa automatic admission.

This policy is deliberately inert: it does not prove writer reconciliation,
claim resources or authorize a mutation. Callers must establish those properties
and customer confirmation in the same transaction before applying allocation.
"""
from datetime import datetime, timezone
from typing import Mapping

from src.core.bookings.allocation_contracts import AllocationRequest, derive_allocation
from src.core.bookings.catalog_contracts import CatalogDraft, MinuteDuration
from src.core.db.repositories.booking_allocation_repo import BookingAllocationRepository


class SectorAdmissionDenied(ValueError):
    """An explicit, current administrative admission policy is unavailable."""


def validate_sector_admission(config: Mapping, catalog: CatalogDraft,
                              request: AllocationRequest, *, now: datetime):
    """Return typed bounds/claims only for a strictly opted-in local appointment.

    The published catalog determines duration, opening windows and resources.
    The model and customer cannot choose policy, resource claims or authority.
    """
    policy = config.get('c24_sector_auto') if isinstance(config, Mapping) else None
    if (not isinstance(policy, dict)
            or set(policy) != {'enabled', 'version', 'sector', 'authority'}
            or policy['enabled'] is not True
            or type(policy['version']) is not int or policy['version'] != 1
            or policy['sector'] not in {'parrucchiere', 'centro_estetico'}
            or policy['sector'] != catalog.sector or policy['authority'] != 'internal'):
        raise SectorAdmissionDenied('Sector admission policy unavailable')
    if (type(request) is not AllocationRequest or request.action != 'allocate'
            or request.expected_allocation_revision != 0
            or request.expected_catalog_revision != catalog.revision
            or request.appointment_date is None or request.appointment_time is None
            or request.party_size is not None or request.room_count is not None):
        raise SectorAdmissionDenied('Appointment unavailable')
    service = next((s for s in catalog.services if s.service_id == request.service_id), None)
    if (service is None or not isinstance(service.duration, MinuteDuration)
            or service.handoff.staff_review_required or service.handoff.party_size_above is not None):
        raise SectorAdmissionDenied('Service requires human review')
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise SectorAdmissionDenied('Current time unavailable')
    try:
        start, end, claims = derive_allocation(request, catalog)
        fits = BookingAllocationRepository._weekly_fits(catalog.availability, start, end, catalog.timezone)
    except (ValueError, TypeError, OverflowError):
        raise SectorAdmissionDenied('Appointment unavailable') from None
    if start <= now.astimezone(timezone.utc) or fits is not True or not claims:
        raise SectorAdmissionDenied('Appointment unavailable')
    return start, end, claims
