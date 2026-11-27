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
} from '../src/components/melpis-flow-scenarios.mjs';

const scenarioText = (scenario) => JSON.stringify(scenario);

test('the flow demo runs six ordered steps that finish inside a comfortable window', () => {
  assert.equal(steps.length, 6);
  assert.equal(new Set(steps.map(step => step.id)).size, 6, 'step ids stay unique');
  assert.equal(new Set(steps.map(step => step.label)).size, 6, 'step labels stay unique');
  for (const step of steps) assert.ok(step.label && step.hint, `step ${step.id} needs a label and a hint`);
  assert.equal(stepDurations.length, 6);
  const total = stepDurations.reduce((sum, duration) => sum + duration, 0);
  assert.ok(total >= 8000 && total <= 12000, `automatic sequence should run 8-12s, received ${total}ms`);
  assert.ok(stepDurations.every(duration => duration > 0));
  assert.ok(reducedFactor < 1 && reducedFactor > 0);
  for (let step = 0; step <= LAST_STEP; step += 1) {
    assert.ok(durationFor(step, true) < durationFor(step, false), `step ${step} must shorten under reduced motion`);
  }
});

test('the four scenarios are complete, synthetic and free of real contact data', () => {
  assert.deepEqual(scenarioOrder, Object.keys(scenarios));
  assert.ok(scenarioOrder.includes(defaultScenarioId));
  for (const id of scenarioOrder) {
    const scenario = scenarios[id];
    for (const field of ['nav', 'title', 'context', 'message', 'ack', 'reply']) {
      assert.equal(typeof scenario[field], 'string', `${id}.${field} must be a string`);
      assert.ok(scenario[field].trim().length > 0, `${id}.${field} must not be empty`);
    }
    assert.ok(scenario.client.name && scenario.client.initials, `${id} needs a synthetic client`);
    assert.equal(scenario.entities.length, 4, `${id} exposes the four recognised fields`);
    assert.ok(scenario.knowledge.detail && scenario.knowledge.note && scenario.knowledge.source, `${id} needs a knowledge entry with its source`);
    assert.ok(scenario.calendar.verdict, `${id} needs a calendar verdict`);
    assert.equal(scenario.outcome.rows.length, 3, `${id} outcome lists three rows`);
    assert.ok(scenario.outcome.kind && scenario.outcome.title && scenario.outcome.detail && scenario.outcome.status, `${id} outcome is incomplete`);
    assert.ok(scenario.outcome.guard, `${id} outcome states its guard`);
    assert.ok(Array.isArray(scenario.calendar.slots), `${id} calendar slots must be an array`);
    for (const key of ['nlp', 'kb', 'cal']) assert.ok(scenario.modules[key], `${id} must describe its ${key} module result`);
    const text = scenarioText(scenario);
    assert.ok(!/https?:\/\//i.test(text), `${id} must not carry external URLs`);
    assert.ok(!/[\w.+-]+@[\w-]+\.[a-z]{2,}|(?:\+39|0039)[\s\d]{6,}|\b\d{3}[\s.-]?\d{3}[\s.-]?\d{4}\b/i.test(text), `${id} must not carry contact data`);
  }
});

test('highlights come from the message only and never invent parameters', () => {
  for (const id of scenarioOrder) {
    const scenario = scenarios[id];
    for (const highlight of scenario.highlights) {
      assert.ok(scenario.message.includes(highlight), `${id} highlights "${highlight}" but the message does not contain it`);
    }
    const runs = splitMessage(scenario.message, scenario.highlights);
    assert.equal(runs.map(run => run.text).join(''), scenario.message, `${id} split must rebuild the message exactly`);
    assert.equal(runs.filter(run => run.entity).length, scenario.highlights.length, `${id} must highlight exactly its entities`);
    for (const entity of scenario.entities) assert.ok(entity.label && entity.value, `${id} entities need a label and a value`);
  }
  assert.deepEqual(splitMessage('nessun parametro', ['assente']), [{ text: 'nessun parametro', entity: null }]);
  assert.deepEqual(splitMessage('<b>ciao</b>', ['<b>', 'ciao']).map(run => run.text).join(''), '<b>ciao</b>', 'splitting keeps markup inert as text');
  const escalation = scenarios.escalation.entities;
  assert.equal(escalation.find(entity => entity.key === 'date').value, 'da definire', 'missing dates are declared, never guessed');
  assert.equal(escalation.find(entity => entity.key === 'time').value, 'non indicato', 'missing times are declared, never guessed');
});

test('the informational scenario answers from a configured sheet without inventing certifications', () => {
  const text = scenarioText(scenarios.kb_allergia);
  assert.match(text, /Scheda allergeni/);
  assert.ok(!/\bAIC\b|certificat|garantit[oa] senza|senza glutine garantito/i.test(text), 'allergen answers must not claim certifications');
  assert.match(scenarios.kb_allergia.outcome.guard, /schede configurate/i);
});

test('the state machine derives every step from a single integer, starting idle', () => {
  const idle = deriveRun(defaultScenarioId, -1);
  assert.equal(idle.idle, true);
  assert.equal(idle.sent, false);
  assert.equal(idle.entitiesVisible, false);
  assert.equal(idle.activityVisible, false);
  assert.equal(idle.calendarVisible, false);
  assert.equal(idle.replyVisible, false);
  assert.equal(idle.outcomeVisible, false);
  assert.equal(idle.complete, false);
  assert.deepEqual(cardOrder.map(key => idle.cardState(key)), ['pending', 'pending', 'pending', 'pending', 'pending']);

  const done = deriveRun(defaultScenarioId, LAST_STEP);
  assert.equal(done.complete, true);
  assert.equal(done.outcomeVisible, true);
  assert.equal(done.entitiesVisible && done.activityVisible && done.calendarVisible && done.replyVisible, true);
  assert.deepEqual(cardOrder.map(key => done.cardState(key)), ['done', 'done', 'done', 'done', 'waiting']);

  assert.equal(deriveRun(defaultScenarioId, 1).cardState('intent'), 'active');
  assert.equal(deriveRun(defaultScenarioId, 1).cardState('knowledge'), 'pending');
  assert.equal(deriveRun(defaultScenarioId, 2).cardState('intent'), 'done');
  assert.equal(deriveRun(defaultScenarioId, 2).cardState('knowledge'), 'active');
  assert.equal(deriveRun('missing-scenario', 0).scenario, scenarios[defaultScenarioId], 'unknown ids fall back to the default scenario');
  assert.equal(deriveRun(defaultScenarioId, 99).step, LAST_STEP, 'the state machine clamps out-of-range steps');
});

test('every card maps to a real pipeline stage and the calendar card can be skipped', () => {
  assert.deepEqual(cardOrder, ['intent', 'knowledge', 'calendar', 'reply', 'dispatch']);
  for (const key of cardOrder) {
    assert.ok(cardLabels[key] && cardActiveLabels[key] && cardDoneLabels[key], `${key} needs a label, an active label and a done label`);
    assert.ok(Number.isInteger(cardSteps[key]), `${key} needs a numeric step`);
  }
  assert.deepEqual(verifications.map(check => check.id), ['intent', 'knowledge', 'calendar']);
  assert.equal(sendStep, cardSteps.reply, 'the reply is composed exactly when its card starts');

  assert.equal(deriveRun('escalation', 4).cardState('calendar'), 'skipped', 'a scenario without a calendar request never claims a calendar check');
  assert.equal(deriveRun('slot_pieno', LAST_STEP).cardState('calendar'), 'done', 'an occupied slot is still a completed check');
  assert.equal(deriveRun(defaultScenarioId, 5).cardState('dispatch'), 'waiting', 'the send card starts only on the final step, where the flow stops and waits');
  assert.equal(deriveRun(defaultScenarioId, 4).cardState('dispatch'), 'pending', 'the send card is not reached before its step');
  assert.equal(cardOrder.map(key => deriveRun(defaultScenarioId, 5).cardState(key)).includes('active'), false, 'no card is left mid-flight once the flow stops');
});

test('the reply is gated on completed verifications, never on the step index alone', () => {
  for (const id of scenarioOrder) {
    for (let step = -1; step <= LAST_STEP; step += 1) {
      const run = deriveRun(id, step);
      assert.ok(!run.replyVisible || run.verificationsComplete, `${id} at step ${step} rendered a reply before its verifications completed`);
      for (const check of run.verifications) {
        assert.equal(typeof check.required, 'boolean', `${id} verification ${check.id} must declare whether it is required`);
        assert.equal(check.done, check.required && step >= check.atStep, `${id} verification ${check.id} is not derived from the step`);
        assert.equal(check.skipped, !check.required, `${id} verification ${check.id} skip flag is wrong`);
      }
      const calendar = run.verifications.find(check => check.id === 'calendar');
      if (id === 'escalation') assert.equal(calendar.required, false, 'the escalation scenario has no requested slot');
      else assert.equal(calendar.required, true, `${id} requests a slot, so the calendar check is required`);
      if (step < 0) assert.equal(run.verificationsComplete, false, `${id} must not report completed verifications while idle`);
    }
  }
  assert.equal(deriveRun('tavolo', LAST_STEP).replyVisible, true);
  assert.equal(deriveRun('tavolo', 3).replyVisible, false, 'verifications completing is not the same as the reply being sent');
  assert.equal(deriveRun('tavolo', sendStep).replyVisible, true, 'the reply may be sent once its verifications are done');
  assert.equal(deriveRun('escalation', 4).verificationsComplete, true, 'the escalation path completes without a calendar check');
});

test('the waiting state reuses each scenario own outcome, never a new vocabulary', () => {
  for (const id of scenarioOrder) {
    const done = deriveRun(id, LAST_STEP);
    assert.equal(done.awaiting.active, true, `${id} must stop and wait`);
    assert.equal(done.awaiting.label, scenarios[id].outcome.status, `${id} awaiting label must be its own status`);
    assert.equal(done.awaiting.kind, scenarios[id].outcome.kind, `${id} awaiting kind must be its own kind`);
    assert.equal(done.activity, scenarios[id].outcome.status, `${id} activity line settles on the waiting label`);
    assert.equal(deriveRun(id, 2).awaiting.active, false, `${id} must not wait before the run is complete`);
    assert.ok(done.activity.length > 0, `${id} activity line must always say something`);
  }
  assert.equal(deriveRun('slot_pieno', LAST_STEP).awaiting.label, 'In attesa di scelta');
  assert.equal(deriveRun('escalation', LAST_STEP).awaiting.label, 'In attesa di un operatore');
  assert.equal(deriveRun('kb_allergia', LAST_STEP).awaiting.label, 'Tavolo da tenere');
});

test('no scenario can ever report a booking from a single simulated customer message', () => {
  for (const id of scenarioOrder) {
    for (let step = -1; step <= LAST_STEP; step += 1) {
      const run = deriveRun(id, step);
      assert.equal(run.customerConfirmed, false, `${id} must never assume customer consent`);
      assert.equal(run.canBook, false, `${id} must fail closed at step ${step}`);
    }
    const outcome = scenarios[id].outcome;
    assert.ok(!/prenotazione confermata|appuntamento confermato|confermato/i.test(outcome.title), `${id} must not claim a confirmation`);
  }
  const tavolo = deriveRun('tavolo', LAST_STEP);
  assert.equal(tavolo.availabilityVerified, true, 'the positive scenario does verify availability');
  assert.equal(tavolo.canBook, false, 'verified availability alone is not enough to book');
  assert.equal(scenarios.tavolo.outcome.status, 'In attesa di conferma');
  assert.equal(scenarios.slot_pieno.outcome.status, 'In attesa di scelta');
  assert.equal(scenarios.escalation.outcome.status, 'In attesa di un operatore');
  assert.equal(deriveRun('escalation', LAST_STEP).availabilityVerified, false, 'the escalation scenario never verifies availability');
  assert.equal(deriveRun('slot_pieno', LAST_STEP).availabilityVerified, false, 'an unavailable slot is never verified');
  assert.equal(scenarios.escalation.outcome.kind, 'escalation');
  assert.match(scenarios.escalation.outcome.guard, /passa la conversazione a una persona/);
});
