"""Hand-labelled AI-Q1 input deltas; offline reducer contract, not model evaluation."""
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from src.core.bookings.catalog_contracts import validate_catalog_json
from src.core.bookings.conversation_state import new_state, parse_proposal_json, reduce_proposal
from tests.core.bookings.test_conversation_state import FIXTURES

# Explicit synthetic publication aliases, independently authored from utterances.
ALIASES = {
 'ristorante': {'menu degustazione':'menu_degustazione','terrazza':'terrazza'},
 'parrucchiere': {'taglio':'taglio','piega':'piega'},
 'centro_estetico': {'massaggio relax':'massaggio_relax','trattamento viso':'trattamento_viso'},
 'hotel_bnb': {'camera doppia':'camera_doppia','camera singola':'camera_singola'},
 'studio_medico_dentista': {'visita amministrativa':'visita_amministrativa','controllo amministrativo':'controllo_amministrativo'},
}
# Explicit deltas below come from customer utterances, not expected slots.
INPUTS = [
 ('restaurant-booking-01','ristorante',{'service_id':'menu_degustazione','appointment_date':'2026-11-12','appointment_time':'10:00','party_size':6}),
 ('restaurant-booking-02','ristorante',{'service_id':'terrazza','appointment_date':'2026-11-13','appointment_time':'15:00'}),
 ('restaurant-incomplete-01','ristorante',{'service_id':'menu_degustazione'}),
 ('restaurant-incomplete-02','ristorante',{'service_id':'terrazza','appointment_date':'2026-11-13'}),
 ('hair-booking-01','parrucchiere',{'service_id':'taglio','appointment_date':'2026-11-12','appointment_time':'10:00'}),
 ('hair-booking-02','parrucchiere',{'service_id':'piega','appointment_date':'2026-11-13','appointment_time':'15:00'}),
 ('hair-incomplete-01','parrucchiere',{'service_id':'taglio'}),
 ('hair-incomplete-02','parrucchiere',{'service_id':'piega','appointment_date':'2026-11-13'}),
 ('spa-booking-01','centro_estetico',{'service_id':'massaggio_relax','appointment_date':'2026-11-12','appointment_time':'10:00'}),
 ('spa-booking-02','centro_estetico',{'service_id':'trattamento_viso','appointment_date':'2026-11-13','appointment_time':'15:00'}),
 ('spa-incomplete-01','centro_estetico',{'service_id':'massaggio_relax'}),
 ('spa-incomplete-02','centro_estetico',{'service_id':'trattamento_viso','appointment_date':'2026-11-13'}),
 ('hotel-booking-01','hotel_bnb',{'service_id':'camera_doppia','arrival_date':'2026-10-24','departure_date':'2026-10-27','stay_nights':3,'room_count':2,'guest_count':4}),
 ('hotel-booking-02','hotel_bnb',{'service_id':'camera_singola','arrival_date':'2026-11-13','departure_date':'2026-11-15','room_count':1,'guest_count':1}),
 ('hotel-incomplete-01','hotel_bnb',{'service_id':'camera_doppia','arrival_date':'2026-11-12','guest_count':2}),
 ('hotel-incomplete-02','hotel_bnb',{'service_id':'camera_singola','arrival_date':'2026-11-13','room_count':1}),
 ('medical_admin-booking-01','studio_medico_dentista',{'service_id':'visita_amministrativa','appointment_date':'2026-11-12','appointment_time':'10:00'}),
 ('medical_admin-booking-02','studio_medico_dentista',{'service_id':'controllo_amministrativo','appointment_date':'2026-11-13','appointment_time':'15:00'}),
 ('medical_admin-incomplete-01','studio_medico_dentista',{'service_id':'visita_amministrativa'}),
 ('medical_admin-incomplete-02','studio_medico_dentista',{'service_id':'controllo_amministrativo','appointment_date':'2026-11-13'}),
]


def synthetic_catalog(sector, org):
    body=copy.deepcopy(FIXTURES[sector]); template=body['services'][0]
    if sector=='hotel_bnb':
        # Test-only catalog explicitly requests rooms and guests; original fixture does not.
        template['fields'] += [{'key':key,'value_type':'positive_integer','required':True} for key in ('room_count','guest_count')]
    body['services']=[]
    for label,identifier in ALIASES[sector].items():
        service=copy.deepcopy(template); service.update(service_id=identifier,name=label); body['services'].append(service)
    return validate_catalog_json(json.dumps(body),organization_id=org)


CASES=json.loads((Path(__file__).parents[3] / 'benchmark/ai_q1/conversations.json').read_text(encoding='utf-8-sig'))
BY_ID={case['id']:case for case in CASES}


@pytest.mark.parametrize('case_id,sector,fields',INPUTS,ids=[row[0] for row in INPUTS])
def test_hand_labelled_booking_and_incomplete_slots(case_id,sector,fields):
    case=BY_ID[case_id]; assert len(case['turns'])==1
    org=uuid4(); cat=synthetic_catalog(sector,org); message=uuid4()
    payload={'intent':'booking','slots':[{'field':key,'value':value} for key,value in fields.items()]}
    proposal=parse_proposal_json(json.dumps(payload))
    state=reduce_proposal(new_state(org,uuid4(),cat),proposal,source_message_id=message,catalog=cat,expected_revision=0)
    actual={s.field:s for s in state.slots}; expected=case['expected']
    mapping={'service':'service_id','date':'arrival_date' if sector=='hotel_bnb' else 'appointment_date',
             'time':'appointment_time','departure_date':'departure_date','party_size':'guest_count' if sector=='hotel_bnb' else 'party_size',
             'rooms':'room_count','nights':'stay_nights'}
    for source,value in expected['slots'].items():
        field=mapping[source]
        assert actual[field].value == (ALIASES[sector][value] if source=='service' else value)
        assert actual[field].source_message_id==message and actual[field].origin=='ai_extracted'
    for source in expected['missing_fields']:
        assert mapping[source] in state.missing_fields
    assert set(actual)=={mapping[key] for key in expected['slots']}
    assert not state.details_confirmed and not state.confirmation_authorized

CANCELLATIONS = [
 ('restaurant-cancellation-01','ristorante','menu_degustazione','2026-11-12','10:00'),
 ('restaurant-cancellation-02','ristorante','terrazza','2026-11-13',None),
 ('hair-cancellation-01','parrucchiere','taglio','2026-11-12','10:00'),
 ('hair-cancellation-02','parrucchiere','piega','2026-11-13',None),
 ('spa-cancellation-01','centro_estetico','massaggio_relax','2026-11-12','10:00'),
 ('spa-cancellation-02','centro_estetico','trattamento_viso','2026-11-13',None),
 ('medical_admin-cancellation-01','studio_medico_dentista','visita_amministrativa','2026-11-12','10:00'),
 ('medical_admin-cancellation-02','studio_medico_dentista','controllo_amministrativo','2026-11-13',None),
]


@pytest.mark.parametrize('case_id,sector,service,day,clock',CANCELLATIONS,ids=[x[0] for x in CANCELLATIONS])
def test_hand_labelled_cancellation_is_only_an_intent(case_id,sector,service,day,clock):
    case=BY_ID[case_id]; assert len(case['turns'])==2
    org=uuid4(); cat=synthetic_catalog(sector,org); state=new_state(org,uuid4(),cat); first,second=uuid4(),uuid4()
    fields={'service_id':service,'appointment_date':day}
    if clock is not None: fields['appointment_time']=clock
    initial=parse_proposal_json(json.dumps({'intent':'booking','slots':[{'field':k,'value':v} for k,v in fields.items()]}))
    state=reduce_proposal(state,initial,source_message_id=first,catalog=cat,expected_revision=0)
    state=reduce_proposal(state,parse_proposal_json('{"intent":"cancel"}'),source_message_id=second,catalog=cat,expected_revision=1)
    actual={slot.field:slot for slot in state.slots}; expected=case['expected']
    assert expected['intent']=='cancel_request' and state.intent=='cancel'
    assert actual['service_id'].value==ALIASES[sector][expected['slots']['service']]
    assert actual['appointment_date'].value==expected['slots']['date']
    if 'time' in expected['slots']: assert actual['appointment_time'].value==expected['slots']['time']
    assert all(slot.source_message_id==first and slot.origin=='ai_extracted' for slot in state.slots)
    assert not state.details_confirmed and not state.confirmation_authorized


AMBIGUITIES = [
 ('restaurant-ambiguity-01','ristorante','menu_degustazione'),('restaurant-ambiguity-02','ristorante',None),
 ('hair-ambiguity-01','parrucchiere','taglio'),('hair-ambiguity-02','parrucchiere',None),
 ('spa-ambiguity-01','centro_estetico','massaggio_relax'),('spa-ambiguity-02','centro_estetico',None),
 ('medical_admin-ambiguity-01','studio_medico_dentista','visita_amministrativa'),('medical_admin-ambiguity-02','studio_medico_dentista',None),
]


@pytest.mark.parametrize('case_id,sector,service',AMBIGUITIES,ids=[x[0] for x in AMBIGUITIES])
def test_hand_labelled_ambiguity_does_not_choose_date_or_time(case_id,sector,service):
    case=BY_ID[case_id]; org=uuid4(); cat=synthetic_catalog(sector,org)
    slots=[] if service is None else [{'field':'service_id','value':service}]
    proposal=parse_proposal_json(json.dumps({'intent':'booking','slots':slots}))
    state=reduce_proposal(new_state(org,uuid4(),cat),proposal,source_message_id=uuid4(),catalog=cat,expected_revision=0)
    actual={slot.field:slot.value for slot in state.slots}; expected=case['expected']
    assert 'date' not in expected['slots'] and 'time' not in expected['slots']
    assert 'appointment_date' not in actual and 'appointment_time' not in actual
    if service is None: assert 'service_id' in state.missing_fields
    else:
        assert actual['service_id']==ALIASES[sector][expected['slots']['service']]
        assert {'appointment_date','appointment_time'} <= set(state.missing_fields)
    assert not state.details_confirmed
