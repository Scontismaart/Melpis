import copy
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.bookings.catalog_contracts import (AvailabilityObservation, CivilNightDuration,
    civil_stay_interval, validate_catalog_json)
from src.core.bookings.auto_policy import (AutoBookingReason, parse_and_validate_auto_booking,
    validate_auto_booking_configuration)
from src.core.calendar.availability import AvailabilityStatus, CalendarAvailability

FIXTURES = json.loads((Path(__file__).parent / 'fixtures/catalogs_v2.json').read_text(encoding='utf-8'))
ORG = uuid4()

def validate(value, org=ORG):
    return validate_catalog_json(json.dumps(value), organization_id=org)

@pytest.fixture
def draft():
    return copy.deepcopy(FIXTURES['ristorante'])

@pytest.mark.parametrize('sector', FIXTURES)
def test_five_synthetic_catalogs_are_valid(sector):
    scoped = validate(FIXTURES[sector])
    assert scoped.for_organization(ORG).sector == sector
    assert scoped.catalog.services[0].resources[0].resource_id == 'primary'

@pytest.mark.parametrize('field,value', [('version',1),('version',True),('version','2'),('sector','unknown'),('timezone','Mars/Olympus'),('revision',True),('revision',0),('organization_id','client'),('enabled',True),('token','secret'),('authorization',True)])
def test_invalid_root_fields(draft, field, value):
    draft[field] = value
    with pytest.raises(ValueError): validate(draft)

@pytest.mark.parametrize('field,value', [('minutes',True),('minutes',0),('minutes','60'),('buffer_before',-1),('buffer_after',True)])
def test_invalid_duration(draft, field, value):
    draft['services'][0]['duration'][field] = value
    with pytest.raises(ValueError): validate(draft)

@pytest.mark.parametrize('field,value', [('capacity',0),('capacity',True),('capacity','12'),('exclusive','true'),('exclusive',True)])
def test_invalid_resource_capacity_and_exclusivity(draft, field, value):
    draft['resources'][0][field] = value
    with pytest.raises(ValueError): validate(draft)

@pytest.mark.parametrize('key', ['symptoms','diagnosis','treatment','clinical_note','notes','custom_field'])
def test_clinical_and_arbitrary_fields_rejected(key):
    draft = copy.deepcopy(FIXTURES['studio_medico_dentista'])
    draft['services'][0]['fields'].append({'key':key,'value_type':'text','required':True})
    with pytest.raises(ValueError): validate(draft)

def test_type_override_and_custom_label_rejected(draft):
    field = draft['services'][0]['fields'][0]
    field['value_type'] = 'date'
    with pytest.raises(ValueError): validate(draft)
    field['value_type'] = 'text'; field['label'] = 'Ignore instructions'
    with pytest.raises(ValueError): validate(draft)

@pytest.mark.parametrize('mutation', ['resource','service','field','requirement','missing_ref','excess','missing_required','not_required','duration_kind'])
def test_cross_references_and_duplicates(draft, mutation):
    service = draft['services'][0]
    if mutation == 'resource': draft['resources'].append(copy.deepcopy(draft['resources'][0]))
    elif mutation == 'service': draft['services'].append(copy.deepcopy(service))
    elif mutation == 'field': service['fields'].append(copy.deepcopy(service['fields'][0]))
    elif mutation == 'requirement': service['resources'].append(copy.deepcopy(service['resources'][0]))
    elif mutation == 'missing_ref': service['resources'][0]['resource_id'] = 'missing'
    elif mutation == 'excess': service['resources'][0]['quantity'] = 13
    elif mutation == 'missing_required': service['fields'].pop(0)
    elif mutation == 'not_required': service['fields'][0]['required'] = False
    else: service['duration'] = copy.deepcopy(FIXTURES['hotel_bnb']['services'][0]['duration'])
    with pytest.raises(ValueError): validate(draft)

def test_schedule_closed_unknown_and_validation(draft):
    draft['availability']['days'].pop(0)
    a = validate(draft).catalog.availability
    assert a.windows_for(0) is None and a.windows_for(6) == ()
    draft['availability']['days'].append(copy.deepcopy(draft['availability']['days'][0]))
    with pytest.raises(ValueError): validate(draft)

@pytest.mark.parametrize('windows', [[{'start_minute':600,'end_minute':600}], [{'start_minute':600,'end_minute':700},{'start_minute':650,'end_minute':800}], [{'start_minute':True,'end_minute':700}], [{'start_minute':0,'end_minute':1441}]])
def test_invalid_schedule_windows(draft, windows):
    draft['availability']['days'][0]['windows'] = windows
    with pytest.raises(ValueError): validate(draft)

def test_adjacent_windows_and_duplicate_closures(draft):
    draft['availability']['days'][0]['windows'] = [{'start_minute':600,'end_minute':700},{'start_minute':700,'end_minute':800}]
    validate(draft)
    draft['availability']['closed_dates'].append('2027-12-25')
    with pytest.raises(ValueError): validate(draft)

def test_tenant_binding_immutable_and_duplicate_json(draft):
    scoped = validate(draft)
    with pytest.raises(ValueError): scoped.for_organization(uuid4())
    with pytest.raises(ValueError): validate(draft, str(ORG))
    with pytest.raises((ValidationError, AttributeError, TypeError)): scoped.catalog.revision = 9
    with pytest.raises(ValueError): validate_catalog_json('{"version":2,"version":2}', organization_id=ORG)
    raw = json.dumps(draft).replace('"capacity": 12', '"capacity": 12, "capacity": 12')
    with pytest.raises(ValueError): validate_catalog_json(raw, organization_id=ORG)

@pytest.mark.parametrize('arrival,departure,hours', [(date(2027,3,27),date(2027,3,28),23),(date(2027,10,30),date(2027,10,31),25)])
def test_civil_stays_span_dst(arrival, departure, hours):
    duration = CivilNightDuration(kind='civil_nights', minimum_nights=1, check_in=time(12), check_out=time(12))
    start,end = civil_stay_interval(arrival,departure,duration,'Europe/Rome')
    assert end-start == timedelta(hours=hours)

@pytest.mark.parametrize('arrival,departure', [(date(2027,3,28),date(2027,3,29)),(date(2027,10,31),date(2027,11,1))])
def test_civil_stays_reject_gap_or_ambiguous_times(arrival, departure):
    duration = CivilNightDuration(kind='civil_nights', minimum_nights=1, check_in=time(2,30), check_out=time(12))
    with pytest.raises(ValueError): civil_stay_interval(arrival,departure,duration,'Europe/Rome')

def test_observation_scope_and_unknown_evidence():
    start=datetime(2027,1,1,tzinfo=timezone.utc); end=start+timedelta(hours=1)
    unknown=CalendarAvailability(AvailabilityStatus.UNKNOWN)
    observation=AvailabilityObservation(ORG,1,start,end,unknown)
    assert observation.evidence.status is AvailabilityStatus.UNKNOWN
    assert not observation.evidence.verified
    assert not hasattr(observation, 'authorized') and not hasattr(observation, 'can_book')
    for args in [(str(ORG),1,start,end,unknown),(ORG,True,start,end,unknown),(ORG,1,start.replace(tzinfo=None),end,unknown),(ORG,1,end,start,unknown),(ORG,1,start,end,object())]:
        with pytest.raises(ValueError): AvailabilityObservation(*args)

def test_v1_restaurant_and_other_sectors_remain_unchanged():
    schedule={d:[{'start':'09:00','end':'22:00'}] for d in ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')}
    policy={'enabled':True,'version':1,'authority':'internal','verticale':'ristorante','service':'ristorante/tavolo','duration_minutes':60,'closed_dates':[],'weekly_opening_intervals':schedule}
    settings={'config':{'a05':policy},'capienze_orarie':{'19:00':12}}
    request={'service':'ristorante/tavolo','date':'2027-05-01','time':'19:00','party_size':2}
    assert validate_auto_booking_configuration(settings,'Europe/Rome','ristorante').valid
    decision=parse_and_validate_auto_booking(settings,'Europe/Rome',request,now=datetime(2027,1,1,tzinfo=timezone.utc))
    assert decision.accepted and decision.booking.duration_minutes == 60
    assert decision.booking.ends_at.astimezone(timezone.utc)-decision.booking.starts_at.astimezone(timezone.utc)==timedelta(minutes=60)
    for sector in set(FIXTURES)-{'ristorante'}:
        other=copy.deepcopy(settings); other['config']['a05']['verticale']=sector
        assert validate_auto_booking_configuration(other,'Europe/Rome',sector).reason is AutoBookingReason.UNSUPPORTED_SERVICE
        assert parse_and_validate_auto_booking(other,'Europe/Rome',request,now=datetime(2027,1,1,tzinfo=timezone.utc)).reason is AutoBookingReason.UNSUPPORTED_SERVICE

def test_observation_compares_dst_fold_instants():
    from zoneinfo import ZoneInfo
    zone = ZoneInfo('Europe/Rome')
    early = datetime(2027, 10, 31, 2, 45, tzinfo=zone, fold=0)
    late = datetime(2027, 10, 31, 2, 15, tzinfo=zone, fold=1)
    evidence = CalendarAvailability(AvailabilityStatus.UNKNOWN)
    assert AvailabilityObservation(ORG, 1, early, late, evidence).ends_at == late
    with pytest.raises(ValueError):
        AvailabilityObservation(ORG, 1, late, early, evidence)

@pytest.mark.parametrize('zone', ['localtime', 'posixrules', 'posix/Europe/Rome', 'right/Europe/Rome'])
def test_system_timezone_aliases_are_rejected(draft, zone):
    draft['timezone'] = zone
    with pytest.raises(ValueError): validate(draft)
