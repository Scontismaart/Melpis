import json
from uuid import uuid4
import pytest
from src.core.bookings.allocation_contracts import parse_request_json, semantic_request_hash

def payload():
    return {'request_id': str(uuid4()), 'booking_id': str(uuid4()), 'action': 'allocate', 'expected_allocation_revision': 0, 'expected_catalog_revision': 2, 'service_id': 'cut', 'appointment_date': '2026-10-28', 'appointment_time': '10:00', 'party_size': 6}

def test_request_rejects_duplicates_unknown_bool_and_offset():
    data = payload()
    with pytest.raises(ValueError): parse_request_json(json.dumps(data)[:-1] + ',"action":"cancel"}')
    for key, value in [('expected_allocation_revision', True), ('party_size', 0), ('appointment_time', '10:00+01:00'), ('unknown', 1)]:
        with pytest.raises(ValueError): parse_request_json(json.dumps({**data, key: value}))

def test_hash_stable_and_actor_booking_bound():
    data = payload(); request = parse_request_json(json.dumps(data)); org, actor = uuid4(), uuid4()
    digest = semantic_request_hash(request, organization_id=org, actor_user_id=actor)
    assert digest == semantic_request_hash(parse_request_json(json.dumps(data, sort_keys=True)), organization_id=org, actor_user_id=actor)
    assert digest != semantic_request_hash(request, organization_id=org, actor_user_id=uuid4())
    other = parse_request_json(json.dumps({**data, 'booking_id': str(uuid4())}))
    assert digest != semantic_request_hash(other, organization_id=org, actor_user_id=actor)

def test_cancel_rejects_details_and_freezes():
    data = payload(); data['action'] = 'cancel'
    with pytest.raises(ValueError): parse_request_json(json.dumps(data))
    for key in ('service_id','appointment_date','appointment_time','party_size'): data.pop(key)
    request = parse_request_json(json.dumps(data))
    with pytest.raises(ValueError): request.action = 'allocate'

def test_seat_pool_scales_and_dst_rejects():
    from pathlib import Path
    from src.core.bookings.catalog_contracts import validate_catalog_json
    from src.core.bookings.allocation_contracts import derive_allocation
    fixtures = json.loads((Path(__file__).parent / 'fixtures/catalogs_v2.json').read_text(encoding='utf-8'))
    catalog = validate_catalog_json(json.dumps(fixtures['ristorante']), organization_id=uuid4()).catalog
    service = catalog.services[0]
    data = payload(); data['service_id'] = service.service_id
    data['expected_catalog_revision'] = catalog.revision
    stale = {**data, 'expected_catalog_revision': catalog.revision + 1}
    with pytest.raises(ValueError, match='revision mismatch'):
        derive_allocation(parse_request_json(json.dumps(stale)), catalog)
    request = parse_request_json(json.dumps(data))
    _, _, claims = derive_allocation(request, catalog)
    for resource_id, kind, exclusive, quantity in claims:
        if kind == 'seat_pool':
            req = next(r for r in service.resources if r.resource_id == resource_id)
            assert quantity == 6 * req.quantity
    data['appointment_date'] = '2026-03-29'; data['appointment_time'] = '02:30'
    with pytest.raises(ValueError): derive_allocation(parse_request_json(json.dumps(data)), catalog)
