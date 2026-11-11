"""Pure eligibility checks do not grant mutation or prove writer fencing."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.core.bookings.allocation_contracts import AllocationRequest
from src.core.bookings.sector_admission_policy import SectorAdmissionDenied, validate_sector_admission
from tests.core.bookings.test_conversation_state import catalog


@pytest.mark.parametrize('sector', ['parrucchiere', 'centro_estetico', 'studio_medico_dentista'])
def test_opted_in_local_sector_uses_published_resources(sector):
    _, scoped = catalog(sector)
    current = scoped.catalog
    request = AllocationRequest(request_id=uuid4(), booking_id=uuid4(), action='allocate',
        expected_allocation_revision=0, expected_catalog_revision=current.revision, service_id='standard',
        appointment_date=datetime(2026, 11, 12).date(), appointment_time=datetime(2026, 11, 12, 10).time())
    policy = {'c24_sector_auto': {'enabled': True, 'version': 1, 'sector': sector, 'authority': 'internal'}}
    start, end, claims = validate_sector_admission(policy, current, request, now=datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert start < end and claims and claims[0][0] == 'primary'
    for invalid in ({}, {'c24_sector_auto': {**policy['c24_sector_auto'], 'authority': 'external_authoritative'}},
                    {'c24_sector_auto': {**policy['c24_sector_auto'], 'version': True}}):
        with pytest.raises(SectorAdmissionDenied):
            validate_sector_admission(invalid, current, request, now=datetime(2026, 11, 1, tzinfo=timezone.utc))
    with pytest.raises(SectorAdmissionDenied):
        validate_sector_admission(policy, current, request, now=datetime(2026, 11, 13, tzinfo=timezone.utc))
