import assert from 'node:assert/strict';
import test from 'node:test';
import {
  LAST_STEP,
  cardActiveLabels,
  cardDoneLabels,
  cardLabels,
  cardOrder,
  cardSteps,
  defaultScenarioId,
  deriveRun,
  durationFor,
  reducedFactor,
  scenarioOrder,
  scenarios,
  sendStep,
  splitMessage,
  stepDurations,
  steps,
  verifications,
} from '../src/components/melpis-rest-flow-scenarios.mjs';

const scenarioText = (scenario) => JSON.stringify(scenario);

test('restaurant flow demo runs six ordered steps in 8-12s', () => {
  assert.equal(steps.length, 6);
  assert.equal(new Set(steps.map(s => s.id)).size, 6);
  assert.equal(new Set(steps.map(s => s.label)).size, 6);
  for (const step of steps) assert.ok(step.label && step.hint);
  assert.equal(stepDurations.length, 6);
  const total = stepDurations.reduce((sum, d) => sum + d, 0);
  assert.ok(total >= 8000 && total <= 12000, `duration between 8-12s, got ${total}`);
  assert.ok(reducedFactor < 1 && reducedFactor > 0);
  for (let s = 0; s <= LAST_STEP; s += 1) {
    assert.ok(durationFor(s, true) < durationFor(s, false));
  }
});

test('four restaurant scenarios are complete, synthetic and context-correct', () => {
  assert.deepEqual(scenarioOrder, ['posti_disponibili', 'allergeni', 'orario', 'evento']);
  assert.equal(defaultScenarioId, 'posti_disponibili');
  for (const id of scenarioOrder) {
    const sc = scenarios[id];
    for (const field of ['nav', 'title', 'context', 'message', 'ack', 'reply']) {
      assert.equal(typeof sc[field], 'string');
      assert.ok(sc[field].trim().length > 0);
    }
    assert.equal(sc.context, 'Ristorante');
    assert.ok(sc.client.name && sc.client.initials);
    assert.equal(sc.entities.length, 4);
    assert.ok(sc.knowledge.detail && sc.knowledge.note && sc.knowledge.source);
    assert.ok(sc.calendar.verdict);
    assert.equal(sc.outcome.rows.length, 4);
    assert.ok(sc.outcome.kind && sc.outcome.title && sc.outcome.status);
    assert.ok(sc.outcome.guard);
    assert.ok(sc.icon, `${id} must have an icon`);
    const txt = scenarioText(sc);
    assert.ok(!/https?:\/\//i.test(txt));
    assert.ok(!/[\w.+-]+@[\w-]+\.[a-z]{2,}|(?:\+39|0039)[\s\d]{6,}|\b\d{3}[\s.-]?\d{3}[\s.-]?\d{4}\b/i.test(txt));
  }
});

test('restaurant message highlights match entities accurately', () => {
  for (const id of scenarioOrder) {
    const sc = scenarios[id];
    for (const hl of sc.highlights) {
      assert.ok(sc.message.includes(hl), `${id} highlights "${hl}"`);
    }
    const runs = splitMessage(sc.message, sc.highlights);
    assert.equal(runs.map(r => r.text).join(''), sc.message);
    assert.equal(runs.filter(r => r.entity).length, sc.highlights.length);
  }
});

test('positive scenario confirms booking with verified capacity and client consent', () => {
  const idle = deriveRun('posti_disponibili', -1);
  assert.equal(idle.idle, true);
  assert.equal(idle.canBook, false);

  const done = deriveRun('posti_disponibili', LAST_STEP);
  assert.equal(done.complete, true);
  assert.equal(done.availabilityVerified, true);
  assert.equal(done.customerConfirmed, true);
  assert.equal(done.canBook, true);
  assert.equal(done.awaiting.pill, '✓ Confermato');
  assert.equal(done.followUpVisible, true);
});

test('allergen scenario verifies from owner knowledge base without hallucination', () => {
  const sc = scenarios.allergeni;
  assert.match(sc.knowledge.source, /Knowledge Base/);
  assert.match(sc.outcome.guard, /non inventa/);
  const done = deriveRun('allergeni', LAST_STEP);
  assert.equal(done.canBook, false);
  assert.equal(done.complete, true);
});

test('busy slot scenario offers alternatives without creating unconfirmed bookings', () => {
  const sc = scenarios.orario;
  assert.ok(sc.calendar.slots.some(s => s.alternative));
  const done = deriveRun('orario', LAST_STEP);
  assert.equal(done.canBook, false);
  assert.equal(done.complete, true);
  assert.equal(done.awaiting.label, 'In attesa di scelta');
});

test('large event scenario escalates to staff instead of auto-confirming', () => {
  const sc = scenarios.evento;
  assert.equal(sc.outcome.kind, 'escalation');
  const done = deriveRun('evento', LAST_STEP);
  assert.equal(done.canBook, false);
  assert.equal(done.complete, true);
  assert.equal(done.awaiting.label, 'In attesa dello staff');
});
