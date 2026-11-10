"""Hotel policy uses catalog civil nights and never grants authority itself."""
import copy
import json
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.core.bookings.allocation_contracts import AllocationRequest
from src.core.bookings.catalog_contracts import validate_catalog_json
from src.core.bookings.hotel_admission_policy import validate_hotel_admission
from src.core.bookings.sector_admission_policy import SectorAdmissionDenied
from tests.core.bookings.test_allocation_engine_postgres import CATALOGS


def test_hotel_admission_preserves_dst_civil_nights_and_requires_opt_in():
    raw = copy.deepcopy(CATALOGS['hotel_bnb'])
    raw['resources'][0]['max_occupants'] = 2
    raw['services'][0]['fields'].append({'key':'guest_count', 'value_type':'positive_integer', 'required':True})
    for day in raw['availability']['days']:
        day['windows'] = [{'start_minute': 0, 'end_minute': 1440}]
    catalog = validate_catalog_json(json.dumps(raw), organization_id=uuid4()).catalog
    request = AllocationRequest(request_id=uuid4(), booking_id=uuid4(), action='allocate',
        expected_allocation_revision=0, expected_catalog_revision=catalog.revision, service_id='standard',
        arrival_date=date(2027, 10, 30), departure_date=date(2027, 11, 1), room_count=1)
    policy = {'c24_sector_auto': {'enabled': True, 'version': 1, 'sector': 'hotel_bnb', 'authority': 'internal'}}
    start, end, claims = validate_hotel_admission(policy, catalog, request, guest_count=2, now=datetime(2027, 10, 1, tzinfo=timezone.utc))
    assert (end - start).total_seconds() == 49 * 3600
    assert claims == (('primary', 'room', True, 1),)
    with pytest.raises(SectorAdmissionDenied):
        validate_hotel_admission({}, catalog, request, guest_count=2, now=datetime(2027, 10, 1, tzinfo=timezone.utc))
    with pytest.raises(SectorAdmissionDenied):
        validate_hotel_admission(policy, catalog, request, guest_count=2, now=datetime(2027, 11, 2, tzinfo=timezone.utc))


def test_hotel_policy_rejects_service_without_required_guest_count():
    raw = copy.deepcopy(CATALOGS['hotel_bnb'])
    catalog = validate_catalog_json(json.dumps(raw), organization_id=uuid4()).catalog
    request = AllocationRequest(request_id=uuid4(), booking_id=uuid4(), action='allocate',
        expected_allocation_revision=0, expected_catalog_revision=catalog.revision, service_id='standard',
        arrival_date=date(2027, 10, 30), departure_date=date(2027, 11, 1), room_count=1)
    policy = {'c24_sector_auto': {'enabled': True, 'version': 1, 'sector': 'hotel_bnb', 'authority': 'internal'}}
    with pytest.raises(SectorAdmissionDenied, match='Guest count'):
        validate_hotel_admission(policy, catalog, request, guest_count=2, now=datetime(2027, 10, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize('invalid', [0, -1, True, '2'])
def test_invalid_guest_counts_cannot_enter_state(invalid):
    from src.core.bookings.conversation_state import parse_proposal_json
    payload = {'intent':'booking', 'slots':[{'field':'guest_count','value':invalid}]}
    with pytest.raises(ValueError):
        parse_proposal_json(json.dumps(payload))

@pytest.mark.parametrize(('rooms', 'guests', 'occupants', 'accepted'), [
    (1, 2, 2, True), (1, 3, 2, False), (2, 4, 2, True),
    (2, 5, 2, False), (1, 1, None, False),
])
def test_hotel_capacity_is_based_on_allocated_room_units(rooms, guests, occupants, accepted):
    raw = copy.deepcopy(CATALOGS['hotel_bnb'])
    room = raw['resources'][0]
    room.update(capacity=3, exclusive=False)
    if occupants is not None:
        room['max_occupants'] = occupants
    raw['services'][0]['fields'].append({'key':'guest_count', 'value_type':'positive_integer', 'required':True})
    for day in raw['availability']['days']:
        day['windows'] = [{'start_minute':0, 'end_minute':1440}]
    catalog = validate_catalog_json(json.dumps(raw), organization_id=uuid4()).catalog
    request = AllocationRequest(request_id=uuid4(), booking_id=uuid4(), action='allocate',
        expected_allocation_revision=0, expected_catalog_revision=catalog.revision, service_id='standard',
        arrival_date=date(2027, 10, 30), departure_date=date(2027, 11, 1), room_count=rooms)
    policy = {'c24_sector_auto': {'enabled':True, 'version':1, 'sector':'hotel_bnb', 'authority':'internal'}}
    if accepted:
        _, _, claims = validate_hotel_admission(policy, catalog, request, guest_count=guests, now=datetime(2027, 10, 1, tzinfo=timezone.utc))
        assert claims[0][3] == rooms
    else:
        with pytest.raises(SectorAdmissionDenied, match='occupancy'):
            validate_hotel_admission(policy, catalog, request, guest_count=guests, now=datetime(2027, 10, 1, tzinfo=timezone.utc))


def test_multiple_room_resources_fail_closed_without_disjointness_proof():
    raw = copy.deepcopy(CATALOGS['hotel_bnb'])
    raw['resources'][0]['max_occupants'] = 2
    second = dict(raw['resources'][0], resource_id='other')
    raw['resources'].append(second)
    raw['services'][0]['resources'].append({'resource_id':'other', 'quantity':1})
    raw['services'][0]['fields'].append({'key':'guest_count', 'value_type':'positive_integer', 'required':True})
    for day in raw['availability']['days']:
        day['windows'] = [{'start_minute':0, 'end_minute':1440}]
    catalog = validate_catalog_json(json.dumps(raw), organization_id=uuid4()).catalog
    request = AllocationRequest(request_id=uuid4(), booking_id=uuid4(), action='allocate', expected_allocation_revision=0,
        expected_catalog_revision=catalog.revision, service_id='standard', arrival_date=date(2027,10,30), departure_date=date(2027,11,1))
    policy = {'c24_sector_auto': {'enabled':True, 'version':1, 'sector':'hotel_bnb', 'authority':'internal'}}
    with pytest.raises(SectorAdmissionDenied, match='unproven'):
        validate_hotel_admission(policy, catalog, request, guest_count=2, now=datetime(2027,10,1,tzinfo=timezone.utc))


@pytest.mark.parametrize('value', [0, -1, True, '2'])
def test_resource_occupancy_limit_is_strict_positive(value):
    raw = copy.deepcopy(CATALOGS['hotel_bnb'])
    raw['resources'][0]['max_occupants'] = value
    with pytest.raises(ValueError):
        validate_catalog_json(json.dumps(raw), organization_id=uuid4())


def test_occupancy_limit_only_applies_to_rooms_and_old_shape_is_preserved():
    raw = copy.deepcopy(CATALOGS['parrucchiere'])
    catalog = validate_catalog_json(json.dumps(raw), organization_id=uuid4()).catalog
    assert 'max_occupants' not in catalog.model_dump(mode='json')['resources'][0]
    raw['resources'][0]['max_occupants'] = 2
    with pytest.raises(ValueError):
        validate_catalog_json(json.dumps(raw), organization_id=uuid4())
