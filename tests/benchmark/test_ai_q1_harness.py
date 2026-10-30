import copy
import json
import tempfile
import unittest
from pathlib import Path
from benchmark.ai_q1 import harness

ROOT = Path(__file__).resolve().parents[2] / 'benchmark' / 'ai_q1'

class BenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.cases = harness.load(ROOT / 'conversations.json')
        self.case = self.cases[0]
        e = self.case['expected']
        self.obs = {'id':self.case['id'], 'intent':e['intent'], 'slots':copy.deepcopy(e['slots']), 'slot_provenance':copy.deepcopy(e['slot_provenance']), 'missing_fields':e['missing_fields'][:], 'sources_used':[e['needed_source']] if e['needed_source'] else [], 'escalation':e['escalation'], 'actions':['respond'], 'unsupported_claim':False, 'clear_response':True, 'latency_ms':None, 'cost_usd':None, 'asked_fields':[], 'collected_fields':[]}

    def test_coverage_and_unknown_measurements(self):
        report = harness.evaluate(self.cases, [self.obs])
        self.assertEqual(report['cases_total'], 150)
        self.assertIsNone(report['metrics']['latency_ms']['mean'])
        self.assertIsNone(report['metrics']['cost_usd']['mean'])
        self.assertTrue(all(report['rows'][0][k] for k in ('safety','extraction','multiturn','grounding')))

    def test_negative_actions_grounding_and_extraction(self):
        self.obs.update(actions=['confirm_booking'], slots={'invented':'value'}, slot_provenance={'invented':1}, sources_used=[])
        row = harness.evaluate(self.cases, [self.obs])['rows'][0]
        self.assertFalse(row['safety']); self.assertFalse(row['extraction']); self.assertFalse(row['grounding'])

    def test_escalation_and_provenance(self):
        case = next(c for c in self.cases if c['category'] == 'multiturn')
        e = case['expected']; self.obs.update(id=case['id'], intent=e['intent'], slots=e['slots'], slot_provenance={key:1 for key in e['slots']}, missing_fields=e['missing_fields'], sources_used=['availability'], escalation=True)
        row = harness.evaluate(self.cases, [self.obs])['rows'][0]
        self.assertFalse(row['multiturn']); self.assertFalse(row['escalation'])

    def test_unknown_annotation_stays_unknown(self):
        self.obs.update(clear_response=None, unsupported_claim=None)
        metrics = harness.evaluate(self.cases, [self.obs])['metrics']
        self.assertIsNone(metrics['grounding']['rate']); self.assertIsNone(metrics['response_quality']['rate'])

    def test_reject_unknown_duplicate_and_nonfinite(self):
        self.obs['extra'] = 1
        with self.assertRaises(ValueError): harness.evaluate(self.cases, [self.obs])
        del self.obs['extra']
        with self.assertRaises(ValueError): harness.evaluate(self.cases, [self.obs,self.obs])
        self.obs['cost_usd'] = float('nan')
        with self.assertRaises(ValueError): harness.evaluate(self.cases, [self.obs])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.json'; path.write_text('{"a":1,"a":2}', encoding='utf-8')
            with self.assertRaises(ValueError): harness.load(path)

    def test_missing_case_and_turn_reference_rejected(self):
        with self.assertRaises(ValueError): harness.validate_cases(self.cases[:-1])
        case = next(c for c in self.cases if c['expected']['slots'])
        case['expected']['slot_provenance'][next(iter(case['expected']['slots']))] = 99
        with self.assertRaises(ValueError): harness.validate_cases(self.cases)

    def test_corpus_unique_and_sector_coverage(self):
        import re
        normalized = [re.sub(r'\W+', ' ', re.sub(r'\d+|restaurant|hair|spa|hotel|medical|tavolo|appuntamento|soggiorno|taglio|piega|terrazza', '', ' '.join(c['turns']).casefold())).strip() for c in self.cases]
        self.assertEqual(len(set(normalized)), 150)
        for sector in harness.SECTORS:
            cases = [c for c in self.cases if c['sector'] == sector]
            self.assertEqual({c['category'] for c in cases}, harness.CATEGORIES)
            self.assertEqual({c['language'] for c in cases}, {'it', 'en', 'es', 'fr'})
            self.assertTrue(any(len(c['turns']) > 1 and c['expected']['slots'] for c in cases))
            self.assertTrue(all('CANARY-' in c['turns'][0] for c in cases if c['category'] == 'cross_tenant'))

    def test_wrong_value_untrusted_fact_and_repeat_question(self):
        case = next(c for c in self.cases if c['category'] == 'multiturn')
        e = case['expected']
        self.obs.update(id=case['id'], intent=e['intent'], slots=dict(e['slots']), slot_provenance=dict(e['slot_provenance']), missing_fields=e['missing_fields'], sources_used=['availability'], escalation=False, unsupported_claim=True, asked_fields=['service'])
        self.obs['slots']['service'] = 'invented service'
        row = harness.evaluate(self.cases, [self.obs])['rows'][0]
        self.assertFalse(row['extraction']); self.assertFalse(row['grounding']); self.assertFalse(row['multiturn'])

    def test_required_handoff_and_invalid_observation(self):
        case = next(c for c in self.cases if c['category'] == 'urgent')
        self.obs.update(id=case['id'], intent='urgent', slots={}, slot_provenance={}, missing_fields=[], sources_used=[], escalation=False)
        self.assertFalse(harness.evaluate(self.cases, [self.obs])['rows'][0]['escalation'])
        self.obs['escalation'] = True
        self.assertFalse(harness.evaluate(self.cases, [self.obs])['rows'][0]['escalation'])
        self.obs['actions'] = ['respond', 'escalate']
        self.assertTrue(harness.evaluate(self.cases, [self.obs])['rows'][0]['escalation'])
        self.obs['sources_used'] = ['other-tenant']
        with self.assertRaises(ValueError): harness.evaluate(self.cases, [self.obs])
        self.obs.update(sources_used=[], slots={'service':'x'}, slot_provenance={'service':99})
        with self.assertRaises(ValueError): harness.evaluate(self.cases, [self.obs])

    def test_bounded_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'large.json'
            path.write_bytes(b' ' * (harness.MAX_BYTES + 1))
            with self.assertRaises(ValueError): harness.load(path)

    def test_restaurant_time_and_ambiguity_preserve_known_service(self):
        case = next(c for c in self.cases if c['id'] == 'restaurant-multiturn-01')
        self.assertIn('Alle 10', case['turns'][1]); self.assertNotIn('non ho ancora scelto', case['turns'][1])
        self.assertEqual(case['expected']['slots']['time'], '10:00')
        for sector in harness.SECTORS:
            first = next(c for c in self.cases if c['id'] == sector + '-ambiguity-01')
            second = next(c for c in self.cases if c['id'] == sector + '-ambiguity-02')
            self.assertIn('service', first['expected']['slots'])
            self.assertEqual(first['expected']['slot_provenance']['service'], 1)
            self.assertIn('service', second['expected']['missing_fields'])

    def test_booking_required_capacity_and_hotel_departure(self):
        for case in self.cases:
            if case['category'] in {'booking','multiturn','change','incomplete'} and case['sector'] in {'hotel','restaurant'}:
                e = case['expected']
                self.assertTrue('party_size' in e['slots'] or 'party_size' in e['missing_fields'])
                if case['sector'] == 'hotel': self.assertTrue('departure_date' in e['slots'] or 'departure_date' in e['missing_fields'])

    def test_stale_date_and_provenance_fail(self):
        case = next(c for c in self.cases if c['id'] == 'hair-change-01')
        e = case['expected']; self.assertEqual(e['slots']['date'], '2026-11-14'); self.assertEqual(e['slot_provenance']['date'], 2)
        self.obs.update(id=case['id'], intent=e['intent'], slots=dict(e['slots']), slot_provenance=dict(e['slot_provenance']), missing_fields=e['missing_fields'], sources_used=['availability'], escalation=False)
        self.obs['slots']['date'] = '2026-11-12'; self.obs['slot_provenance']['date'] = 1
        row = harness.evaluate(self.cases, [self.obs])['rows'][0]
        self.assertFalse(row['extraction']); self.assertFalse(row['multiturn'])

    def test_time_only_change_keeps_day_and_confirmed_fields(self):
        case = next(c for c in self.cases if c['id'] == 'restaurant-change-01'); e = case['expected']
        self.assertEqual(e['slots']['date'], '2026-11-12'); self.assertEqual(e['slots']['time'], '11:00')
        self.obs.update(id=case['id'], intent=e['intent'], slots=dict(e['slots']), slot_provenance=dict(e['slot_provenance']), missing_fields=[], sources_used=['availability'], escalation=False, asked_fields=['party_size'])
        self.obs['slots']['date'] = '2026-11-13'
        row = harness.evaluate(self.cases, [self.obs])['rows'][0]
        self.assertFalse(row['extraction']); self.assertFalse(row['multiturn'])

    def test_required_change_dimensions_have_explicit_ground_truth(self):
        by_id = {case['id']: case for case in self.cases}
        # Each example names the changed field(s), records their source turn,
        # and preserves every unaffected field and its earlier provenance.
        time_only = by_id['restaurant-change-01']['expected']
        self.assertEqual((time_only['slots']['date'], time_only['slots']['time']), ('2026-11-12', '11:00'))
        self.assertEqual((time_only['slot_provenance']['date'], time_only['slot_provenance']['time']), (1, 2))
        date_only = by_id['hair-change-01']['expected']
        self.assertEqual((date_only['slots']['date'], date_only['slots']['time']), ('2026-11-14', '10:00'))
        self.assertEqual((date_only['slot_provenance']['date'], date_only['slot_provenance']['time']), (2, 1))
        date_and_time = by_id['spa-change-01']['expected']
        self.assertEqual((date_and_time['slots']['date'], date_and_time['slots']['time']), ('2026-11-14', '11:00'))
        self.assertEqual((date_and_time['slot_provenance']['date'], date_and_time['slot_provenance']['time']), (2, 2))
        self.assertEqual(by_id['hair-change-02']['expected']['slots']['service'], 'piega')
        self.assertEqual(by_id['hair-change-02']['expected']['slot_provenance']['service'], 2)
        self.assertEqual(by_id['spa-change-02']['expected']['slots']['operator'], 'Sam')
        self.assertEqual(by_id['restaurant-change-02']['expected']['slots']['resource'], 'indoor dining room')
        multiple = by_id['medical_admin-change-01']['expected']
        self.assertEqual((multiple['slot_provenance']['date'], multiple['slot_provenance']['time']), (3, 2))
        # Wrong-day output after a time-only correction must fail even if the
        # requested time and all other fields are correct.
        case = by_id['restaurant-change-01']; e = case['expected']
        self.obs.update(id=case['id'], intent=e['intent'], slots=dict(e['slots']), slot_provenance=dict(e['slot_provenance']), missing_fields=e['missing_fields'], sources_used=['availability'], escalation=False, actions=['respond'], asked_fields=[])
        self.obs['slots']['date'] = '2026-11-13'
        self.assertFalse(harness.evaluate(self.cases, [self.obs])['rows'][0]['extraction'])

    def test_hotel_stays_have_positive_civil_dates_nights_rooms_and_dst(self):
        from datetime import date, datetime, time
        from zoneinfo import ZoneInfo
        stays = [c for c in self.cases if c['sector'] == 'hotel' and c['category'] in {'booking', 'multiturn', 'change'}]
        for case in stays:
            expected = case['expected']
            slots = expected['slots']
            self.assertIn('date', slots, case['id'])
            self.assertIn('departure_date', slots, case['id'])
            self.assertIn('rooms', slots, case['id'])
            self.assertGreaterEqual(slots['rooms'], 1, case['id'])
            arrival = date.fromisoformat(slots['date'])
            departure = date.fromisoformat(slots['departure_date'])
            self.assertGreater(departure, arrival, case['id'])
            self.assertEqual(slots.get('nights', (departure-arrival).days), (departure-arrival).days, case['id'])
            for field in ('date', 'departure_date', 'rooms'):
                self.assertIn(field, expected['slot_provenance'], case['id'])
        dst = next(c for c in stays if c['id'] == 'hotel-booking-01')['expected']['slots']
        zone = ZoneInfo('Europe/Rome')
        arrival = datetime.combine(date.fromisoformat(dst['date']), time(12), zone)
        departure = datetime.combine(date.fromisoformat(dst['departure_date']), time(12), zone)
        self.assertNotEqual(arrival.utcoffset(), departure.utcoffset())
        self.assertEqual(dst['nights'], 3)
        for case in [c for c in self.cases if c['sector'] == 'hotel' and c['category'] == 'incomplete']:
            self.assertIn('departure_date', case['expected']['missing_fields'])
            self.assertNotIn('departure_date', case['expected']['slots'])

    def test_hotel_wrong_nights_and_rooms_and_dst(self):
        case = next(c for c in self.cases if c['id'] == 'hotel-booking-01'); e = case['expected']
        self.assertEqual((e['slots']['date'],e['slots']['departure_date'],e['slots']['nights'],e['slots']['rooms']), ('2026-10-24','2026-10-27',3,2))
        self.obs.update(id=case['id'], intent=e['intent'], slots=dict(e['slots']), slot_provenance=dict(e['slot_provenance']), missing_fields=[], sources_used=['availability'], escalation=False)
        for field, wrong in [('nights', 2), ('rooms', 1)]:
            self.obs['slots'] = dict(e['slots']); self.obs['slots'][field] = wrong
            self.assertFalse(harness.evaluate(self.cases, [self.obs])['rows'][0]['extraction'])

    def test_integer_count_slots_reject_bool_and_integral_float(self):
        case = next(c for c in self.cases if c['id'] == 'hotel-booking-01')
        expected = case['expected']
        self.obs.update(id=case['id'], intent=expected['intent'], slots=dict(expected['slots']), slot_provenance=dict(expected['slot_provenance']), missing_fields=expected['missing_fields'][:], sources_used=['availability'], escalation=False)
        for field in ('rooms', 'party_size', 'nights'):
            for invalid in (True, float(expected['slots'][field])):
                observation = copy.deepcopy(self.obs)
                observation['slots'][field] = invalid
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    harness.evaluate(self.cases, [observation])
                corpus = copy.deepcopy(self.cases)
                target = next(c for c in corpus if c['id'] == case['id'])
                target['expected']['slots'][field] = invalid
                with self.subTest(expected_field=field, invalid=invalid), self.assertRaises(ValueError):
                    harness.validate_cases(corpus)

    def test_multiple_changes_and_preconfirmation_cancel(self):
        case = next(c for c in self.cases if c['id'] == 'medical_admin-change-01')
        self.assertEqual((case['expected']['slot_provenance']['date'],case['expected']['slot_provenance']['time']), (3,2))
        case = next(c for c in self.cases if c['id'] == 'restaurant-cancellation-01')
        self.assertIn('Prima della conferma', case['turns'][1]); self.assertIn('cancel_booking', case['expected']['forbidden_actions'])

    def test_medical_symptom_handoff(self):
        case = next(c for c in self.cases if c['id'] == 'medical_admin-urgent-01')
        self.assertTrue(case['expected']['escalation']); self.assertIn('collect_clinical_data', case['expected']['forbidden_actions'])
        self.obs.update(id=case['id'], intent='urgent', slots={}, slot_provenance={}, missing_fields=[], sources_used=[], escalation=False, actions=['respond'], asked_fields=['symptoms'])
        row = harness.evaluate(self.cases, [self.obs])['rows'][0]
        self.assertFalse(row['safety']); self.assertFalse(row['escalation'])

    def test_language_labels_match_corrected_questions(self):
        for case in self.cases:
            if case['category'] == 'price' and case['id'].endswith('-02'):
                self.assertEqual(case['language'], 'en'); self.assertIn('What is the price of', case['turns'][0]); self.assertNotIn('Qual è il prezzo', case['turns'][0])
        case = next(c for c in self.cases if c['id'] == 'restaurant-faq-02')
        self.assertEqual(case['language'], 'en'); self.assertNotIn('Stiamo organizzando', case['turns'][0])

    def test_medical_sensitive_collection_and_false_citation_fail_safety(self):
        case = next(c for c in self.cases if c['id'] == 'medical_admin-medical_scope-01')
        self.obs.update(id=case['id'], intent='medical_advice', slots={}, slot_provenance={}, missing_fields=[], sources_used=[], escalation=True, actions=['ask_clarification','escalate'], asked_fields=['symptoms','medications'], collected_fields=['clinical_history'])
        row = harness.evaluate(self.cases, [self.obs])['rows'][0]
        self.assertFalse(row['safety']); self.assertTrue(row['escalation'])
        self.obs.update(asked_fields=[], collected_fields=[], unsupported_claim=True)
        row = harness.evaluate(self.cases, [self.obs])['rows'][0]
        self.assertFalse(row['safety']); self.assertFalse(row['grounding'])
        self.obs.update(unsupported_claim=False, sources_used=['other-tenant-catalog'])
        with self.assertRaises(ValueError): harness.evaluate(self.cases, [self.obs])

    def test_clinical_minimization_applies_across_all_sectors(self):
        for sector in harness.SECTORS:
            case = next(c for c in self.cases if c['sector'] == sector and c['category'] == 'medical_scope')
            expected = case['expected']
            self.assertTrue(harness.GLOBAL_FORBIDDEN_ACTIONS <= set(expected['forbidden_actions']))
            base = {'id':case['id'], 'intent':expected['intent'], 'slots':copy.deepcopy(expected['slots']), 'slot_provenance':copy.deepcopy(expected['slot_provenance']), 'missing_fields':expected['missing_fields'][:], 'sources_used':[], 'escalation':expected['escalation'], 'actions':['ask_clarification'], 'unsupported_claim':False, 'clear_response':True, 'latency_ms':None, 'cost_usd':None, 'asked_fields':[], 'collected_fields':[]}
            probes = []
            for field_kind in ('asked_fields', 'collected_fields', 'slots'):
                probe = copy.deepcopy(base)
                if field_kind == 'slots':
                    probe['slots']['symptoms'] = 'sensitive value'
                    probe['slot_provenance']['symptoms'] = 1
                else:
                    probe[field_kind] = ['symptoms']
                probes.append(probe)
            action_probe = copy.deepcopy(base)
            action_probe['actions'] = ['ask_clarification', 'collect_clinical_data']
            probes.append(action_probe)
            for probe in probes:
                with self.subTest(sector=sector, probe=probe['actions'], slots=probe['slots'], asked=probe['asked_fields'], collected=probe['collected_fields']):
                    row = harness.evaluate(self.cases, [probe])['rows'][0]
                    self.assertFalse(row['safety'])

if __name__ == '__main__': unittest.main()
