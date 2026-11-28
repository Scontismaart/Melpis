/**
 * Scenario data for the Melpis Flow Demo — the Journey section (id `mv2-percorso`).
 *
 * Pure data and pure derivations only: no React, no DOM, no network, no timers.
 * Every string here is synthetic and reviewable in source. The demo shows how a
 * single customer message is processed; it never carries real customer data,
 * credentials or personal information.
 *
 * Safety invariant: `deriveRun()` can only report a confirmed illustrative booking
 * when the local `bookingAllowed()` gate passes. This script simulates one customer
 * message, so explicit customer consent never arrives and the gate always fails
 * closed — no privileged outcome is ever rendered from the simulated message alone.
 */
import { bookingAllowed } from './booking-guard.mjs';

export const steps = [
  { id: 'received', label: 'Messaggio ricevuto', hint: 'La richiesta entra nella conversazione.' },
  { id: 'intent', label: 'Analisi intento', hint: 'Melpis riconosce cosa chiede il cliente.' },
  { id: 'knowledge', label: 'Knowledge Base', hint: 'Consulta regole e informazioni configurate.' },
  { id: 'calendar', label: 'Disponibilità', hint: 'Verifica gli slot liberi nel calendario.' },
  { id: 'reply', label: 'Risposta al cliente', hint: 'Prepara e invia la risposta.' },
  { id: 'outcome', label: 'Esito', hint: 'Registra lo stato della richiesta.' },
];

export const LAST_STEP = steps.length - 1;

/** Per-step dwell time in ms — the whole automatic sequence runs in about 9.6s. */
export const stepDurations = [720, 1860, 1820, 2100, 1560, 1540];
/** Reduced-motion guests still see every step, just faster and without motion. */
export const reducedFactor = 0.22;

/**
 * The console's engine column: one card per real stage of the pipeline, in the
 * order the demo actually performs them. `cardSteps` is the step at which each
 * card starts working, so the visual order can never drift from the state machine.
 */
export const cardOrder = ['intent', 'knowledge', 'calendar', 'reply', 'dispatch'];
export const cardSteps = { intent: 1, knowledge: 2, calendar: 3, reply: 4, dispatch: 5 };
export const cardLabels = {
  intent: 'Analisi intento e parametri',
  knowledge: 'Knowledge Base',
  calendar: 'Calendario e disponibilità',
  reply: 'Generazione risposta',
  dispatch: 'Invio e conferma',
};
export const cardActiveLabels = {
  intent: 'Riconoscimento di intento e parametri',
  knowledge: 'Ricerca nelle informazioni configurate',
  calendar: 'Verifica degli slot del giorno',
  reply: 'Composizione del messaggio',
  dispatch: 'Preparazione dell’invio',
};
export const cardDoneLabels = {
  intent: 'Parametri riconosciuti',
  knowledge: 'Informazioni trovate',
  calendar: 'Disponibilità verificata',
  reply: 'Risposta preparata per il cliente',
  dispatch: 'In attesa',
};

/**
 * The checks the reply depends on. `calendar` is only required when the scenario
 * actually asks for a slot, which is what makes the escalation path honest.
 */
export const verifications = [
  { id: 'intent', atStep: 1 },
  { id: 'knowledge', atStep: 2 },
  { id: 'calendar', atStep: 3 },
];
/** The step at which the composed reply is handed to the client. */
export const sendStep = cardSteps.reply;

export const scenarios = {
  tavolo: {
    nav: 'Posti disponibili',
    title: 'Prenotazione tavolo',
    context: 'Ristorante',
    client: { name: 'Marco Rossi', initials: 'MR' },
    message: 'Ehi ciao, vorrei prenotare un tavolo per 2 persone domani alle 12:00.',
    highlights: ['2 persone', 'domani', '12:00'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Prenotazione tavolo' },
      { key: 'party', label: 'Ospiti', value: '2 persone' },
      { key: 'date', label: 'Data', value: 'domani' },
      { key: 'time', label: 'Orario', value: '12:00' },
    ],
    knowledge: {
      detail: 'Capienza tavoli · fascia pranzo',
      note: 'Fino a 8 coperti per tavolo nella fascia richiesta.',
      source: 'Regole di servizio · Ristorante',
    },
    calendar: {
      requested: '12:00',
      verdict: 'Slot disponibile',
      slots: [
        { time: '11:30', state: 'busy' },
        { time: '12:00', state: 'free', target: true },
        { time: '12:30', state: 'free' },
        { time: '13:00', state: 'free' },
        { time: '13:30', state: 'busy' },
      ],
    },
    ack: 'Controllo le regole del servizio e la disponibilità per il giorno richiesto.',
    reply: 'Per 2 persone domani alle 12:00 il tavolo risulta libero. Tengo lo slot da parte: confermi la prenotazione?',
    modules: {
      nlp: 'Prenotazione tavolo · 2 persone',
      kb: 'Capienza tavoli · fascia pranzo',
      cal: '12:00 disponibile',
    },
    outcome: {
      kind: 'proposta',
      title: 'Disponibilità confermata',
      detail: 'Tavolo per 2 tenuto per domani alle 12:00, in attesa della conferma del cliente.',
      rows: [
        ['Cliente', 'Marco Rossi'],
        ['Persone', '2'],
        ['Quando', 'domani · 12:00'],
      ],
      status: 'In attesa di conferma',
      guard: 'Nessuna prenotazione viene registrata senza la conferma esplicita del cliente.',
    },
    partySize: 2,
    capacity: 2,
    calendarVerified: true,
  },

  kb_allergia: {
    nav: 'Allergeni e menu',
    title: 'Informazione e disponibilità',
    context: 'Ristorante',
    client: { name: 'Giulia Lombardi', initials: 'GL' },
    message: 'Ciao, il dolce della casa contiene frutta secca? Siamo 4 per domenica alle 20:30.',
    highlights: ['frutta secca', '4', 'domenica', '20:30'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Informazione allergeni' },
      { key: 'party', label: 'Ospiti', value: '4 persone' },
      { key: 'date', label: 'Data', value: 'domenica' },
      { key: 'time', label: 'Orario', value: '20:30' },
    ],
    knowledge: {
      detail: 'Scheda allergeni · dolce della casa',
      note: 'La ricetta segnala mandorle tra gli ingredienti.',
      source: 'Scheda allergeni · Cucina',
    },
    calendar: {
      requested: '20:30',
      verdict: 'Slot disponibile',
      slots: [
        { time: '19:30', state: 'busy' },
        { time: '20:00', state: 'free' },
        { time: '20:30', state: 'free', target: true },
        { time: '21:00', state: 'free' },
        { time: '21:30', state: 'busy' },
      ],
    },
    ack: 'Cerco la scheda degli allergeni e controllo il tavolo per la data richiesta.',
    reply: 'Il dolce della casa contiene mandorle: lo segnalo in cucina per l’allergia. Per 4 persone domenica alle 20:30 il tavolo è libero, vuoi che lo tenga?',
    modules: {
      nlp: 'Informazione allergeni · 4 persone',
      kb: 'Scheda allergeni · mandorle',
      cal: '20:30 disponibile',
    },
    outcome: {
      kind: 'informativo',
      title: 'Informazione fornita',
      detail: 'Risposta presa dalla scheda allergeni configurata, senza garantie oltre i dati disponibili.',
      rows: [
        ['Cliente', 'Giulia Lombardi'],
        ['Persone', '4'],
        ['Quando', 'domenica · 20:30'],
      ],
      status: 'Tavolo da tenere',
      guard: 'Le informazioni arrivano solo dalle schede configurate: nessuna promessa oltre i dati disponibili.',
    },
    partySize: 4,
    capacity: 4,
    calendarVerified: true,
  },

  slot_pieno: {
    nav: 'Orario occupato',
    title: 'Proposta alternativa',
    context: 'Salone',
    client: { name: 'Alessandro Verdi', initials: 'AV' },
    message: 'Avete posto per un taglio uomo domani alle 18:00?',
    highlights: ['taglio uomo', 'domani', '18:00'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Prenotazione appuntamento' },
      { key: 'party', label: 'Servizio', value: 'Taglio uomo' },
      { key: 'date', label: 'Data', value: 'domani' },
      { key: 'time', label: 'Orario', value: '18:00' },
    ],
    knowledge: {
      detail: 'Agenda servizi · taglio uomo',
      note: 'Un solo operatore per slot, durata stimata 30 minuti.',
      source: 'Regole agenda · Salone',
    },
    calendar: {
      requested: '18:00',
      verdict: 'Slot occupato',
      slots: [
        { time: '17:00', state: 'busy' },
        { time: '17:30', state: 'free', alternative: true },
        { time: '18:00', state: 'busy', target: true },
        { time: '18:30', state: 'free', alternative: true },
        { time: '19:00', state: 'busy' },
      ],
    },
    ack: 'Controllo l’agenda dell’operatore per il servizio e l’orario richiesti.',
    reply: 'Alle 18:00 è già occupato. Ho libero alle 17:30 oppure alle 18:30: quale preferisci?',
    modules: {
      nlp: 'Appuntamento · taglio uomo',
      kb: 'Agenda servizi · un operatore',
      cal: '18:00 occupato',
    },
    outcome: {
      kind: 'alternativa',
      title: 'Orario occupato',
      detail: 'Proposta alternativa inviata al cliente, in attesa della sua scelta.',
      rows: [
        ['Cliente', 'Alessandro Verdi'],
        ['Servizio', 'Taglio uomo'],
        ['Proposta', '17:30 oppure 18:30'],
      ],
      status: 'In attesa di scelta',
      guard: 'Nessun appuntamento viene creato prima che il cliente scelga l’alternativa.',
    },
    partySize: 1,
    capacity: 0,
    calendarVerified: false,
  },

  escalation: {
    nav: 'Evento su richiesta',
    title: 'Escalation a operatore',
    context: 'Eventi',
    client: { name: 'Studio Ferrari', initials: 'SF' },
    message: 'Organizziamo una cena per 28 ospiti il mese prossimo: che menù potete proporre?',
    highlights: ['28 ospiti', 'il mese prossimo'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Richiesta evento' },
      { key: 'party', label: 'Ospiti', value: '28' },
      { key: 'date', label: 'Data', value: 'da definire' },
      { key: 'time', label: 'Orario', value: 'non indicato' },
    ],
    knowledge: {
      detail: 'Nessuna regola per eventi oltre 20 ospiti',
      note: 'La richiesta esce dal perimetro configurato.',
      source: 'Knowledge Base · regola mancante',
    },
    calendar: {
      requested: null,
      verdict: 'Non richiesto',
      slots: [],
    },
    ack: 'Controllo le regole configurate per gli eventi numerosi.',
    reply: 'Per un evento da 28 ospiti non ho una regola dedicata: passo la richiesta a un operatore che ti risponderà.',
    modules: {
      nlp: 'Richiesta evento · 28 ospiti',
      kb: 'Regola mancante oltre 20 ospiti',
      cal: 'Non richiesto',
    },
    outcome: {
      kind: 'escalation',
      title: 'Escalation al team',
      detail: 'Conversazione assegnata a un operatore, da prendere in carico.',
      rows: [
        ['Cliente', 'Studio Ferrari'],
        ['Ospiti', '28'],
        ['Coda', 'Da prendere in carico'],
      ],
      status: 'In attesa di un operatore',
      guard: 'Quando le informazioni non bastano, Melpis non inventa: passa la conversazione a una persona.',
    },
    partySize: null,
    capacity: null,
    calendarVerified: false,
  },
};

export const scenarioOrder = ['tavolo', 'kb_allergia', 'slot_pieno', 'escalation'];
export const defaultScenarioId = 'tavolo';

/**
 * Splits a message into runs so the console can highlight entities without ever
 * injecting HTML. Returns [{ text, entity }] where `entity` is the matched
 * highlight (or null). Pure and order-independent.
 */
export function splitMessage(message, highlights = []) {
  const marks = highlights
    .map(needle => ({ needle, index: message.indexOf(needle) }))
    .filter(mark => mark.index >= 0)
    .sort((a, b) => a.index - b.index);
  const runs = [];
  let cursor = 0;
  for (const mark of marks) {
    if (mark.index < cursor) continue;
    if (mark.index > cursor) runs.push({ text: message.slice(cursor, mark.index), entity: null });
    runs.push({ text: mark.needle, entity: mark.needle });
    cursor = mark.index + mark.needle.length;
  }
  if (cursor < message.length) runs.push({ text: message.slice(cursor), entity: null });
  return runs.length ? runs : [{ text: message, entity: null }];
}

/** Dwell time for the step that is about to start, honouring reduced motion. */
export function durationFor(step, reduced) {
  const base = stepDurations[step] ?? stepDurations[0];
  return Math.round(reduced ? base * reducedFactor : base);
}

/**
 * Derives everything the console renders for a (scenario, step) pair.
 * `step` is -1 while idle, then 0..LAST_STEP.
 */
export function deriveRun(scenarioId, step) {
  const scenario = scenarios[scenarioId] ?? scenarios[defaultScenarioId];
  const safeStep = Math.max(-1, Math.min(LAST_STEP, step));
  const complete = safeStep >= LAST_STEP;

  const checks = verifications.map(check => {
    const required = check.id !== 'calendar' || scenario.calendar.requested !== null;
    return {
      id: check.id,
      atStep: check.atStep,
      required,
      done: required && safeStep >= check.atStep,
      skipped: !required,
    };
  });
  const verificationsComplete = checks.every(check => !check.required || check.done);

  const cardState = (key) => {
    if (key === 'calendar' && scenario.calendar.requested === null) return 'skipped';
    // The send card starts on the last step, which is also where the run stops
    // and waits: it is never "done", the flow hands back to the client instead.
    if (key === 'dispatch') return safeStep >= cardSteps.dispatch ? 'waiting' : 'pending';
    const threshold = cardSteps[key];
    if (safeStep > threshold) return 'done';
    if (safeStep === threshold) return 'active';
    return 'pending';
  };

  const activity = safeStep < 0
    ? 'Pronto: il messaggio del cliente avvia il flusso.'
    : complete
      ? scenario.outcome.status
      : verificationsComplete
        ? 'Verifiche completate: la risposta è pronta per il cliente.'
        : scenario.ack;

  const availabilityVerified = safeStep >= 3 && scenario.calendarVerified === true;
  // The visitor sends exactly one message, so explicit customer consent never
  // arrives inside this script: the illustrative booking decision fails closed.
  const customerConfirmed = false;
  const canBook = bookingAllowed({
    capacity: scenario.capacity,
    partySize: scenario.partySize,
    availabilityVerified,
    customerConfirmed,
  });

  return {
    scenario,
    step: safeStep,
    last: LAST_STEP,
    idle: safeStep < 0,
    complete,
    sent: safeStep >= 0,
    activityVisible: safeStep >= 0,
    entitiesVisible: safeStep >= 1,
    calendarVisible: safeStep >= 3,
    // A reply may only exist when every required verification has completed: the
    // step index decides *when* it is composed, the checks decide *whether* it
    // may be shown at all.
    replyVisible: safeStep >= sendStep && verificationsComplete,
    outcomeVisible: complete,
    cardState,
    verifications: checks,
    verificationsComplete,
    awaiting: complete
      ? { active: true, kind: scenario.outcome.kind, label: scenario.outcome.status }
      : { active: false, kind: null, label: '' },
    activity,
    availabilityVerified,
    customerConfirmed,
    canBook,
  };
}
