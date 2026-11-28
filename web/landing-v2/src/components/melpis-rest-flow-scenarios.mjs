/**
 * Scenario data for the Melpis Restaurant Flow Demo (pagina /ristoranti/).
 *
 * Models the restaurant reservation pipeline:
 * Customer request -> Intent & entity analysis -> Owner Knowledge Base ->
 * Calendar & real-time availability -> Reply generation -> Confirmation / Hand-off.
 *
 * Four selectable scenarios:
 * 1. Posti disponibili (2 guests tomorrow at 12:00 -> slot verified -> confirmed)
 * 2. Allergeni e menù (gluten-free & lactose inquiry -> verified from owner KB)
 * 3. Orario occupato (4 guests tonight at 20:30 -> busy -> alternative slots proposed)
 * 4. Evento su richiesta (8 guests birthday dinner -> group rules -> hand-off to staff)
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

/** Per-step dwell time in ms (~9.5s total run). */
export const stepDurations = [720, 1860, 1820, 2100, 1560, 1540];
export const reducedFactor = 0.22;

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
  knowledge: 'Ricerca nelle informazioni del locale',
  calendar: 'Verifica degli slot del ristorante',
  reply: 'Composizione della risposta',
  dispatch: 'Preparazione della conferma',
};
export const cardDoneLabels = {
  intent: 'Parametri riconosciuti',
  knowledge: 'Informazioni trovate',
  calendar: 'Disponibilità verificata',
  reply: 'Risposta preparata per il cliente',
  dispatch: 'Confermato',
};

export const verifications = [
  { id: 'intent', atStep: 1 },
  { id: 'knowledge', atStep: 2 },
  { id: 'calendar', atStep: 3 },
];

export const sendStep = cardSteps.reply;

export const scenarios = {
  posti_disponibili: {
    nav: 'Posti disponibili',
    icon: 'calendar',
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
      verdict: '12:00 disponibile',
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
    followUp: [
      { role: 'client', text: 'Sì, confermo.' },
      { role: 'assistant', text: 'Perfetto, prenotazione confermata per domani alle 12:00. Ti aspettiamo!' },
    ],
    modules: {
      nlp: 'Prenotazione tavolo · 2 persone',
      kb: 'Capienza tavoli · fascia pranzo',
      cal: '12:00 disponibile',
    },
    outcome: {
      kind: 'confermata',
      title: 'Prenotazione confermata',
      detail: 'Tavolo per 2 registrato per domani alle 12:00 nel calendario del ristorante.',
      rows: [
        ['Cliente', 'Marco Rossi'],
        ['Persone', '2'],
        ['Quando', 'domani · 12:00'],
        ['Canale', 'WhatsApp Business'],
      ],
      status: 'Confermato',
      statusPill: '✓ Confermato',
      footer: 'Registrata nel calendario dimostrativo.',
      guard: 'Melpis conferma la prenotazione solo dopo aver verificato capienza e disponibilità reale.',
    },
    partySize: 2,
    capacity: 8,
    calendarVerified: true,
  },

  allergeni: {
    nav: 'Allergeni e menù',
    icon: 'cutlery',
    title: 'Allergeni e menù',
    context: 'Ristorante',
    client: { name: 'Giulia Colombo', initials: 'GC' },
    message: 'Buonasera, avete opzioni senza glutine o piatti senza lattosio?',
    highlights: ['senza glutine', 'senza lattosio'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Richiesta allergeni' },
      { key: 'diet', label: 'Esigenze', value: 'Glutine e lattosio' },
      { key: 'source', label: 'Fonte', value: 'Knowledge Base' },
      { key: 'status', label: 'Verifica', value: 'Schede certificate' },
    ],
    knowledge: {
      detail: 'Scheda allergeni cucina',
      note: 'Pasta e pane senza glutine con preparazione dedicata, secondi senza lattosio disponibili.',
      source: 'Knowledge Base · Scheda allergeni cucina',
    },
    calendar: {
      requested: null,
      verdict: 'Non richiesto per questa richiesta',
      slots: [],
    },
    ack: 'Consulto la Knowledge Base configurata con le schede allergeni del locale.',
    reply: 'Sì, abbiamo pasta e pane senza glutine con preparazione dedicata, oltre a opzioni senza lattosio. Segnaliamo la richiesta allo chef al vostro arrivo!',
    followUp: null,
    modules: {
      nlp: 'Allergeni · senza glutine e lattosio',
      kb: 'Scheda allergeni cucina · opzioni disponibili',
      cal: 'Non richiesto',
    },
    outcome: {
      kind: 'informativo',
      title: 'Informazione verificata',
      detail: 'Risposta fornita attingendo esclusivamente alle schede allergeni configurate nella Knowledge Base.',
      rows: [
        ['Cliente', 'Giulia Colombo'],
        ['Richiesta', 'Glutine e lattosio'],
        ['Fonte', 'Knowledge Base del locale'],
        ['Canale', 'WhatsApp Business'],
      ],
      status: 'Informazione fornita',
      statusPill: '✓ Informazione fornita',
      footer: 'Dati verificati dalla Knowledge Base del ristorante.',
      guard: 'Melpis risponde solo con informazioni certe configurate dal titolare: non inventa risposte sugli allergeni.',
    },
    partySize: null,
    capacity: null,
    calendarVerified: false,
  },

  orario: {
    nav: 'Orario occupato',
    icon: 'clock',
    title: 'Orario occupato',
    context: 'Ristorante',
    client: { name: 'Luca Moretti', initials: 'LM' },
    message: 'Vorrei un tavolo per 4 persone stasera alle 20:30.',
    highlights: ['4 persone', 'stasera', '20:30'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Prenotazione tavolo' },
      { key: 'party', label: 'Ospiti', value: '4 persone' },
      { key: 'date', label: 'Data', value: 'stasera' },
      { key: 'time', label: 'Orario', value: '20:30' },
    ],
    knowledge: {
      detail: 'Regole turni sala · Turno sera',
      note: 'Due turni serali: primo turno 19:30-21:00, secondo turno 21:15-23:30.',
      source: 'Regole di servizio · Turni sala',
    },
    calendar: {
      requested: '20:30',
      verdict: 'Slot 20:30 completo',
      slots: [
        { time: '19:30', state: 'busy' },
        { time: '20:00', state: 'free', alternative: true },
        { time: '20:30', state: 'busy', target: true },
        { time: '21:15', state: 'free', alternative: true },
        { time: '21:45', state: 'busy' },
      ],
    },
    ack: 'Controllo la disponibilità per stasera e cerco gli slot alternativi più vicini.',
    reply: 'Alle 20:30 siamo al completo per 4 persone. Ho disponibilità alle 20:00 oppure alle 21:15: quale orario preferite?',
    followUp: null,
    modules: {
      nlp: 'Prenotazione tavolo · 4 persone',
      kb: 'Regole turni sala · Turno sera',
      cal: '20:30 completo · alternative 20:00 / 21:15',
    },
    outcome: {
      kind: 'alternativa',
      title: 'Orario occupato',
      detail: 'Orario richiesto pieno: Melpis propone le fasce alternative libere più vicine.',
      rows: [
        ['Cliente', 'Luca Moretti'],
        ['Richiesta', '4 persone · 20:30'],
        ['Alternative', '20:00 oppure 21:15'],
        ['Canale', 'WhatsApp Business'],
      ],
      status: 'In attesa di scelta',
      statusPill: 'In attesa di scelta',
      footer: 'Alternative calcolate in tempo reale dal calendario.',
      guard: 'Nessuna prenotazione creata prima che il cliente abbia scelto l’orario alternativo.',
    },
    partySize: 4,
    capacity: 0,
    calendarVerified: false,
  },

  evento: {
    nav: 'Evento su richiesta',
    icon: 'star',
    title: 'Evento su richiesta',
    context: 'Ristorante',
    client: { name: 'Elena Romano', initials: 'ER' },
    message: 'Possiamo prenotare per 8 persone per una cena di compleanno sabato?',
    highlights: ['8 persone', 'cena di compleanno', 'sabato'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Richiesta gruppo' },
      { key: 'party', label: 'Ospiti', value: '8 persone' },
      { key: 'occasion', label: 'Occasione', value: 'Compleanno' },
      { key: 'rule', label: 'Regola', value: 'Tavoli > 6 coperti' },
    ],
    knowledge: {
      detail: 'Regole gruppi ed eventi',
      note: 'Tavolate oltre 6 persone richiedono approvazione del responsabile di sala per organizzazione coperti.',
      source: 'Knowledge Base · Regole gruppi ed eventi',
    },
    calendar: {
      requested: null,
      verdict: 'Richiede approvazione staff',
      slots: [],
    },
    ack: 'Riconosco una tavolata numerosa: consulto le regole del locale per i gruppi.',
    reply: 'Per tavolate da 8 persone verifichiamo la disposizione ideale in sala. Ho inoltrato la richiesta al nostro staff, che vi ricontatterà a breve!',
    followUp: null,
    modules: {
      nlp: 'Richiesta gruppo · 8 persone (Compleanno)',
      kb: 'Regola gruppi · approvazione responsabile',
      cal: 'Passaggio a staff',
    },
    outcome: {
      kind: 'escalation',
      title: 'Richiesta inoltrata allo staff',
      detail: 'Richiesta speciale oltre la capienza standard: assegnata allo staff per gestione diretta.',
      rows: [
        ['Cliente', 'Elena Romano'],
        ['Gruppo', '8 persone (Compleanno)'],
        ['Gestione', 'Passaggio allo staff'],
        ['Canale', 'WhatsApp Business'],
      ],
      status: 'In attesa dello staff',
      statusPill: 'In attesa dello staff',
      footer: 'Notifica inoltrata al gestionale del ristorante.',
      guard: 'Quando una richiesta esce dalle regole standard, Melpis non inventa: la passa allo staff.',
    },
    partySize: 8,
    capacity: null,
    calendarVerified: false,
  },
};

export const scenarioOrder = ['posti_disponibili', 'allergeni', 'orario', 'evento'];
export const defaultScenarioId = 'posti_disponibili';

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

export function durationFor(step, reduced) {
  const base = stepDurations[step] ?? stepDurations[0];
  return Math.round(reduced ? base * reducedFactor : base);
}

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
    if (key === 'dispatch') return safeStep >= cardSteps.dispatch ? 'done' : 'pending';
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
  const customerConfirmed = complete && scenario.outcome.kind === 'confermata';
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
    replyVisible: safeStep >= sendStep && verificationsComplete,
    followUpVisible: complete && Array.isArray(scenario.followUp),
    outcomeVisible: complete,
    cardState,
    verifications: checks,
    verificationsComplete,
    awaiting: complete
      ? { active: true, kind: scenario.outcome.kind, label: scenario.outcome.status, pill: scenario.outcome.statusPill }
      : { active: false, kind: null, label: '', pill: '' },
    activity,
    availabilityVerified,
    customerConfirmed,
    canBook,
  };
}
