"""Pure, dark C2.4 state contract checks using synthetic published catalogs."""
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from src.core.bookings.catalog_contracts import validate_catalog_json
from src.core.bookings.conversation_state import (
    StateConflict, confirm_details, explicit_customer_fields,
    medical_source_requires_handoff, new_state, parse_proposal_json,
    parse_structured_response_json, reduce_proposal, state_digest,
)

FIXTURES = json.loads((Path(__file__).parent / 'fixtures/catalogs_v2.json').read_text(encoding='utf-8'))


def catalog(sector='ristorante', *, org=None, edit=None):
    org = org or uuid4()
    body = copy.deepcopy(FIXTURES[sector])
    if edit: edit(body)
    return org, validate_catalog_json(json.dumps(body), organization_id=org)


def proposal(intent='booking', **fields):
    return parse_proposal_json(json.dumps({'intent': intent, 'slots': [{'field': k, 'value': v} for k, v in fields.items()]}))


def apply(state, cat, p, *, message=None, explicit=frozenset(), **kwargs):
    return reduce_proposal(state, p, source_message_id=message or uuid4(), catalog=cat,
                           expected_revision=state.revision, explicit_fields=explicit, **kwargs)


def values(state):
    return {s.field:s.value for s in state.slots}


def test_structured_crew_result_rejects_legacy_payload_and_duplicate_keys():
    from types import SimpleNamespace
    from src.core.crew_runner import _validate_structured_output

    org, scoped = catalog()
    base = {'risposta': 'Richiesta ricevuta.', 'richiede_umano': False, 'motivo': ''}
    raw = {**base, 'booking_state_proposal': {
        'intent': 'booking', 'slots': [{'field': 'service_id', 'value': 'standard'}],
    }}
    response = _validate_structured_output(SimpleNamespace(raw=json.dumps(raw)), scoped, org)
    assert response.booking_catalog_revision == scoped.catalog.revision
    assert response.prenotazione is None
    plain = _validate_structured_output(SimpleNamespace(raw=json.dumps(base)), scoped, org)
    assert plain.risposta == base['risposta']
    with pytest.raises(ValueError):
        _validate_structured_output(SimpleNamespace(raw=json.dumps({**raw, 'prenotazione': None})), scoped, org)
    with pytest.raises(ValueError):
        _validate_structured_output(SimpleNamespace(raw='{"risposta":"A","risposta":"B"}'), scoped, org)


@pytest.mark.parametrize('sector', FIXTURES)
def test_five_sectors_start_dark_and_unconfirmed(sector):
    org, cat = catalog(sector)
    state = new_state(org, uuid4(), cat)
    assert state.revision == 0 and state.runtime_enabled is False
    assert state.confirmation_authorized is False and not state.details_confirmed
    state = apply(state, cat, proposal(service_id='standard'))
    assert state.derived.resource_ids == ('primary',)
    assert state.runtime_enabled is False and not state.details_confirmed
    assert 'customer_name' in state.missing_fields


def test_time_correction_preserves_date_and_provenance():
    org,cat = catalog(); state = new_state(org,uuid4(),cat); first,second = uuid4(),uuid4()
    state = apply(state,cat,proposal(service_id='standard',appointment_date='2026-11-12',appointment_time='10:00',party_size=4),message=first,explicit=frozenset({'appointment_date'}))
    original = {s.field:s for s in state.slots}
    state = apply(state,cat,proposal(appointment_time='11:00'),message=second)
    after = {s.field:s for s in state.slots}
    assert after['appointment_date'] == original['appointment_date']
    assert after['appointment_date'].origin == 'customer_provided'
    assert after['appointment_time'].source_message_id == second and after['appointment_time'].origin == 'ai_extracted'
    assert after['party_size'] == original['party_size']
    assert any(x.field == 'appointment_time' and x.source_message_id == first for x in state.superseded)


def test_requested_catalog_operator_is_retained_when_service_needs_resolution():
    def edit(body):
        body['resources'].append({'resource_id':'other_operator', 'kind':'operator',
                                  'capacity':1, 'exclusive':True})
    org,cat = catalog('parrucchiere', edit=edit)
    state = apply(new_state(org,uuid4(),cat), cat,
                  proposal(service_id='standard',appointment_date='2026-11-12',appointment_time='10:00'))
    changed = apply(state,cat,proposal(resource_id='other_operator'))
    assert values(changed)['resource_id'] == 'other_operator'
    assert values(changed)['appointment_date'] == '2026-11-12'
    assert values(changed)['appointment_time'] == '10:00'
    assert 'service_id' in changed.ambiguous_fields
    assert not changed.details_confirmed and changed.confirmation_authorized is False


def test_date_correction_preserves_time_and_replay_conflicts():
    org,cat = catalog('parrucchiere'); state = new_state(org,uuid4(),cat)
    state = apply(state,cat,proposal(service_id='standard',appointment_date='2026-11-12',appointment_time='10:00'))
    message=uuid4(); change=proposal(appointment_date='2026-11-14')
    changed = apply(state,cat,change,message=message)
    assert values(changed)['appointment_time'] == '10:00'
    assert reduce_proposal(changed,change,source_message_id=message,catalog=cat,expected_revision=state.revision) == changed
    with pytest.raises(StateConflict): apply(changed,cat,proposal(appointment_date='2026-11-15'),message=message)
    with pytest.raises(StateConflict): reduce_proposal(changed,proposal(),source_message_id=uuid4(),catalog=cat,expected_revision=state.revision)


@pytest.mark.parametrize('raw', [
    '{"intent":"booking","origin":"customer_provided"}',
    '{"intent":"booking","confirmation_authorized":true}',
    '{"intent":"booking","slots":[{"field":"customer_name","value":"Jane"}]}',
    '{"intent":"booking","slots":[{"field":"symptoms","value":"pain"}]}',
    '{"intent":"booking","slots":[{"field":"party_size","value":true}]}',
    '{"intent":"booking","intent":"cancel"}',
])
def test_untrusted_proposal_rejects_privilege_clinical_and_raw_identity(raw):
    with pytest.raises(ValueError): parse_proposal_json(raw)


def test_contact_presence_boolean_and_narrow_explicit_evidence():
    org,cat = catalog(); p=proposal(customer_name=True,appointment_date='2026-11-12')
    evidence=explicit_customer_fields(p,'2026-11-12',cat.catalog)
    assert evidence == frozenset({'appointment_date'})
    state=apply(new_state(org,uuid4(),cat),cat,p,explicit=evidence)
    assert values(state)['customer_name'] is True
    assert 'Jane' not in state.model_dump_json()
    assert {s.field:s.origin for s in state.slots} == {'appointment_date':'customer_provided','customer_name':'ai_extracted'}


def test_tenant_mismatch_and_catalog_revision_change():
    org,cat=catalog(); state=apply(new_state(org,uuid4(),cat),cat,proposal(service_id='standard'))
    other,othercat=catalog()
    with pytest.raises(ValueError): new_state(org,uuid4(),othercat)
    with pytest.raises(ValueError): apply(state,othercat,proposal())
    def edit(c): c['revision']=2; c['services'][0]['duration']['minutes']=90
    _,newcat=catalog(org=org,edit=edit)
    before=state_digest(state); duration=state.derived.duration_minutes; resources=state.derived.resource_ids
    with pytest.raises(StateConflict): apply(state,newcat,proposal())
    assert state_digest(state) == before and state.catalog_revision == 1
    assert state.derived.duration_minutes == duration and state.derived.resource_ids == resources


@pytest.mark.parametrize('day', ['2026-03-29','2026-10-25'])
def test_ambiguous_and_nonexistent_local_times_remain_unresolved(day):
    org,cat=catalog('parrucchiere'); state=new_state(org,uuid4(),cat)
    state=apply(state,cat,proposal(service_id='standard',appointment_date=day,appointment_time='02:30'))
    assert 'appointment_time' in state.ambiguous_fields and 'appointment_time' in state.missing_fields
    assert state.derived.starts_at is None and not state.details_confirmed


def test_hotel_derived_departure_recomputes_after_arrival_change():
    org,cat=catalog('hotel_bnb'); state=new_state(org,uuid4(),cat)
    state=apply(state,cat,proposal(service_id='standard',arrival_date='2026-10-24',stay_nights=3))
    assert values(state)['departure_date'] == '2026-10-27'
    assert next(s for s in state.slots if s.field=='departure_date').origin == 'backend_derived'
    state=apply(state,cat,proposal(arrival_date='2026-10-25'))
    assert values(state)['departure_date'] == '2026-10-28' and state.derived.stay_nights == 3


def test_medical_handoff_is_sticky_and_no_clinical_fields():
    org,cat=catalog('studio_medico_dentista'); state=new_state(org,uuid4(),cat)
    assert medical_source_requires_handoff('dolore urgente',cat.catalog)
    state=apply(state,cat,proposal(intent='handoff'),medical_handoff=True)
    state=apply(state,cat,proposal(service_id='standard'))
    assert state.handoff_reason == 'medical_scope'
    assert 'dolore' not in state.model_dump_json()
    with pytest.raises(StateConflict): confirm_details(state,source_message_id=uuid4(),prompt_message_id=uuid4(),prompt_body_hash='a'*64,expected_revision=state.revision)


def test_confirmation_is_details_only_and_invalidated_by_correction():
    org,cat=catalog(); state=new_state(org,uuid4(),cat)
    state=apply(state,cat,proposal(service_id='standard',customer_name=True,appointment_date='2026-11-12',appointment_time='10:00',party_size=2))
    confirmed=confirm_details(state,source_message_id=uuid4(),prompt_message_id=uuid4(),prompt_body_hash='a'*64,expected_revision=state.revision)
    assert confirmed.details_confirmed and confirmed.confirmation_authorized is False
    changed=apply(confirmed,cat,proposal(appointment_time='11:00'))
    assert not changed.details_confirmed and changed.confirmation is None
    assert state_digest(changed) != state_digest(confirmed)


def test_dark_response_preserves_proposal_through_serialization():
    org,cat=catalog()
    raw=json.dumps({'risposta':'Serve conferma','richiede_umano':False,'motivo':'','booking_state_proposal':{'intent':'booking','slots':[{'field':'service_id','value':'standard'}]}})
    response=parse_structured_response_json(raw,catalog=cat,organization_id=org)
    assert response.model_dump()['booking_state_proposal']['slots'][0]['value'] == 'standard'
    assert response.booking_catalog_revision == cat.catalog.revision
    with pytest.raises(ValueError): parse_structured_response_json(raw,catalog=cat,organization_id=uuid4())

# Hand-labelled proposals are independent inputs, never copied from `expected`.
# The synthetic `standard` is an explicit test catalog mapping, not evidence
# that arbitrary user-facing service labels resolve in a real publication.
TRACES = [
 ('restaurant-multiturn-01','ristorante', [('2026-11-12',None),(None,'10:00')]),
 ('restaurant-multiturn-02','ristorante', [(None,'15:00'),('2026-11-13',None)]),
 ('restaurant-change-01','ristorante', [('2026-11-12','10:00'),(None,'11:00')]),
 ('restaurant-change-02','ristorante', [('2026-11-12','10:00'),(None,None)]),
 ('hair-multiturn-01','parrucchiere', [('2026-11-12',None),(None,'10:00')]),
 ('hair-multiturn-02','parrucchiere', [(None,'15:00'),('2026-11-13',None)]),
 ('hair-change-01','parrucchiere', [('2026-11-12','10:00'),('2026-11-14',None)]),
 ('hair-change-02','parrucchiere', [('2026-11-12','10:00'),(None,None)]),
 ('spa-multiturn-01','centro_estetico', [('2026-11-12',None),(None,'10:00')]),
 ('spa-multiturn-02','centro_estetico', [(None,'15:00'),('2026-11-13',None)]),
 ('spa-change-01','centro_estetico', [('2026-11-12','10:00'),('2026-11-14','11:00')]),
 ('spa-change-02','centro_estetico', [('2026-11-12','10:00'),(None,None)]),
 ('medical_admin-multiturn-01','studio_medico_dentista', [('2026-11-12',None),(None,'10:00')]),
 ('medical_admin-multiturn-02','studio_medico_dentista', [(None,'15:00'),('2026-11-13',None)]),
 ('medical_admin-change-01','studio_medico_dentista', [('2026-11-12','10:00'),(None,'11:00'),('2026-11-14',None)]),
 ('medical_admin-change-02','studio_medico_dentista', [('2026-11-12','10:00'),(None,'11:00')]),
]


@pytest.mark.parametrize('case_id,sector,turns', TRACES)
def test_ai_q1_temporal_turn_proposals_against_ground_truth(case_id,sector,turns):
    cases=json.loads((Path(__file__).parents[3] / 'benchmark/ai_q1/conversations.json').read_text(encoding='utf-8-sig'))
    case=next(c for c in cases if c['id']==case_id)
    assert len(case['turns']) == len(turns)
    org,cat=catalog(sector); state=new_state(org,uuid4(),cat); messages=[]
    for index,(day,clock) in enumerate(turns):
        fields={}
        if index == 0: fields['service_id']='standard'
        if day is not None: fields['appointment_date']=day
        if clock is not None: fields['appointment_time']=clock
        message=uuid4(); messages.append(message)
        state=apply(state,cat,proposal(**fields),message=message)
        assert state.revision == index+1 and not state.details_confirmed
    actual={s.field:s for s in state.slots}
    for source,field in [('date','appointment_date'),('time','appointment_time')]:
        expected=case['expected']
        assert actual[field].value == expected['slots'][source]
        assert actual[field].source_message_id == messages[expected['slot_provenance'][source]-1]
        assert actual[field].origin == 'ai_extracted'


HOTEL_TRACES = [
 ('hotel-multiturn-01',[('2026-11-12','2026-11-14'),(None,None)]),
 ('hotel-multiturn-02',[(None,None),('2026-11-13','2026-11-16')]),
 ('hotel-change-01',[('2026-11-12','2026-11-14'),('2026-11-13','2026-11-15')]),
 ('hotel-change-02',[('2026-11-12','2026-11-14'),(None,None)]),
]


@pytest.mark.parametrize('case_id,turns',HOTEL_TRACES)
def test_ai_q1_hotel_civil_date_proposals_against_ground_truth(case_id,turns):
    cases=json.loads((Path(__file__).parents[3] / 'benchmark/ai_q1/conversations.json').read_text(encoding='utf-8-sig'))
    case=next(c for c in cases if c['id']==case_id)
    org,cat=catalog('hotel_bnb'); state=new_state(org,uuid4(),cat); messages=[]
    for index,(arrival,departure) in enumerate(turns):
        fields={}
        if index == 0: fields['service_id']='standard'
        if arrival is not None: fields['arrival_date']=arrival
        if departure is not None: fields['departure_date']=departure
        message=uuid4(); messages.append(message)
        state=apply(state,cat,proposal(**fields),message=message)
    actual={s.field:s for s in state.slots}
    for source,field in [('date','arrival_date'),('departure_date','departure_date')]:
        assert actual[field].value == case['expected']['slots'][source]
        assert actual[field].source_message_id == messages[case['expected']['slot_provenance'][source]-1]
    assert not state.details_confirmed


def test_ambiguity_clears_prior_value_and_does_not_fabricate_choice():
    org,cat=catalog('parrucchiere'); state=new_state(org,uuid4(),cat)
    state=apply(state,cat,proposal(service_id='standard',appointment_date='2026-11-12',appointment_time='10:00'))
    unclear=parse_proposal_json('{"intent":"booking","ambiguous":["appointment_time"]}')
    state=apply(state,cat,unclear)
    assert 'appointment_time' not in values(state)
    assert 'appointment_time' in state.ambiguous_fields and 'appointment_time' in state.missing_fields
    assert values(state)['appointment_date'] == '2026-11-12'


def test_catalog_missing_required_does_not_disappear_when_model_omits_it():
    org,cat=catalog(); state=new_state(org,uuid4(),cat)
    state=apply(state,cat,proposal(service_id='standard',appointment_date='2026-11-12'))
    assert {'customer_name','appointment_time','party_size'} <= set(state.missing_fields)
    assert values(state)['appointment_date'] == '2026-11-12'
    assert not state.details_confirmed


def test_hotel_user_departure_correction_does_not_keep_old_derived_nights():
    org,cat=catalog('hotel_bnb'); state=new_state(org,uuid4(),cat)
    state=apply(state,cat,proposal(service_id='standard',arrival_date='2026-10-24',stay_nights=3))
    state=apply(state,cat,proposal(departure_date='2026-10-29'))
    assert values(state)['departure_date'] == '2026-10-29'
    assert 'stay_nights' not in values(state)
    assert state.derived.stay_nights == 5


def test_hotel_explicit_departure_promotes_matching_derived_value():
    org,cat=catalog('hotel_bnb'); state=new_state(org,uuid4(),cat)
    state=apply(state,cat,proposal(service_id='standard',arrival_date='2026-10-24',stay_nights=3))
    source=uuid4()
    state=apply(state,cat,proposal(departure_date='2026-10-27'),message=source,
                explicit=frozenset({'departure_date'}))
    departure=next(s for s in state.slots if s.field=='departure_date')
    assert departure.origin == 'customer_provided'
    assert departure.source_message_id == source and not departure.derived_from
    assert 'stay_nights' not in values(state)
    state=apply(state,cat,proposal(arrival_date='2026-10-25'))
    assert next(s for s in state.slots if s.field=='departure_date') == departure


@pytest.mark.parametrize('text', ['pain', 'allergia', 'fever', 'shortness of breath', 'dolore', 'これは予約です', 'unrecognized vocabulary'])
def test_medical_unknown_or_clinical_text_requires_handoff(text):
    _,cat=catalog('studio_medico_dentista')
    assert medical_source_requires_handoff(text,cat.catalog)


def test_medical_clear_administrative_request_can_proceed():
    _,cat=catalog('studio_medico_dentista')
    assert not medical_source_requires_handoff('vorrei un appuntamento amministrativo',cat.catalog)

# Whole-corpus boundary coverage only: an empty independent proposal carries no
# expected fields into the reducer. This is not a 150-case extraction score.
ALL_CASES=json.loads((Path(__file__).parents[3] / 'benchmark/ai_q1/conversations.json').read_text(encoding='utf-8-sig'))
SECTOR_MAP={'restaurant':'ristorante','hair':'parrucchiere','spa':'centro_estetico','hotel':'hotel_bnb','medical_admin':'studio_medico_dentista'}


@pytest.mark.parametrize('case',ALL_CASES,ids=lambda case: case['id'])
def test_ai_q1_each_case_unknown_proposal_never_invents_slots_or_confirmation(case):
    org,cat=catalog(SECTOR_MAP[case['sector']]); state=new_state(org,uuid4(),cat)
    for turn in case['turns']:
        medical=medical_source_requires_handoff(turn,cat.catalog)
        state=apply(state,cat,proposal(intent='unknown'),medical_handoff=medical)
        assert not state.slots and not state.details_confirmed
        assert state.runtime_enabled is False and state.confirmation_authorized is False
        assert turn not in state.model_dump_json()
    if case['sector']=='medical_admin' and case['category'] in {'urgent','medical_scope'}:
        assert state.handoff_reason == 'medical_scope'
