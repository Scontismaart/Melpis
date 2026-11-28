/**
 * Scenario data for the Melpis Hotel Flow Demo (/hotel/).
 *
 * Models the hotel hospitality reservation pipeline:
 * Guest request -> Intent & entity analysis -> Hotel Knowledge Base ->
 * Room availability & dates -> Reply generation -> Confirmation / Hand-off.
 */
import { bookingAllowed } from './booking-guard.mjs';

export const steps = [
  { id: 'received', label: 'Messaggio ricevuto', hint: 'La richiesta dell’ospite entra in chat.' },
  { id: 'intent', label: 'Analisi intento', hint: 'Melpis riconosce date, tipologia e richieste.' },
  { id: 'knowledge', label: 'Disponibilità', hint: 'Verifica camere libere nel periodo indicato.' },
  { id: 'calendar', label: 'Regole e tariffe', hint: 'Consulta listini, politiche e servizi inclusi.' },
  { id: 'reply', label: 'Generazione risposta', hint: 'Prepara la proposta di soggiorno in più lingue.' },
  { id: 'outcome', label: 'Invio e conferma', hint: 'Registra la prenotazione della camera.' },
];

export const LAST_STEP = steps.length - 1;

export const stepDurations = [720, 1860, 1820, 2100, 1560, 1540];
export const reducedFactor = 0.22;

export const cardOrder = ['intent', 'knowledge', 'calendar', 'reply', 'dispatch'];
export const cardSteps = { intent: 1, knowledge: 2, calendar: 3, reply: 4, dispatch: 5 };
export const cardLabels = {
  intent: 'Analisi intento',
  knowledge: 'Disponibilità',
  calendar: 'Regole e tariffe',
  reply: 'Generazione risposta',
  dispatch: 'Invio e conferma',
};
export const cardActiveLabels = {
  intent: 'Riconoscimento delle date e della tipologia',
  knowledge: 'Verifica camere libere nel periodo',
  calendar: 'Controllo tariffe e politiche di soggiorno',
  reply: 'Composizione della proposta di prenotazione',
  dispatch: 'Registrazione della camera',
};
export const cardDoneLabels = {
  intent: 'Date e ospiti identificati',
  knowledge: 'Camera disponibile individuata',
  calendar: 'Tariffe e colazione verificate',
  reply: 'Proposta inviata all’ospite',
  dispatch: 'Confermato',
};

export const verifications = [
  { id: 'intent', atStep: 1 },
  { id: 'knowledge', atStep: 2 },
  { id: 'calendar', atStep: 3 },
];

export const sendStep = cardSteps.reply;

export const scenarios = {
  check_in_bagagli: {
    nav: 'Orari check-in',
    icon: 'clock',
    title: 'Check-in e deposito bagagli',
    context: 'Hotel',
    client: { name: 'Luca Moretti', initials: 'LM' },
    message: 'Buonasera, a che ora possiamo effettuare il check-in?',
    highlights: ['effettuare il check-in'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Orari check-in' },
      { key: 'time', label: 'Check-in standard', value: 'Dalle 15:00' },
      { key: 'baggage', label: 'Deposito bagagli', value: 'Disponibile gratuito' },
      { key: 'reception', label: 'Presidio', value: 'Reception attiva 24h' },
    ],
    knowledge: {
      detail: 'Guida ospiti · Orari e servizi hotel',
      note: 'Check-in standard dalle 15:00. Deposito bagagli gratuito disponibile prima del check-in.',
      source: 'Knowledge Base · Regolamento Hotel',
    },
    calendar: {
      requested: null,
      verdict: 'Non richiesto per questa richiesta',
      slots: [],
    },
    ack: 'Consulto gli orari di check-in e le informazioni sul deposito bagagli della struttura.',
    reply: 'Il check-in è disponibile dalle 15:00. Se prevedete di arrivare prima, posso raccogliere la vostra richiesta e segnalarla alla reception.',
    followUp: [
      { role: 'client', text: 'Arriveremo alle 12:30. È possibile lasciare i bagagli?' },
      { role: 'assistant', text: 'Certamente, la struttura dispone di un deposito bagagli. Potete utilizzarlo prima del check-in.' },
    ],
    modules: {
      nlp: 'Check-in · dalle 15:00',
      kb: 'Deposito bagagli gratuito 24h',
      cal: 'Nessun vincolo calendario',
    },
    outcome: {
      kind: 'informativa',
      title: 'Informazioni fornite',
      detail: 'Orari di check-in e dettagli deposito bagagli inviati dalla Knowledge Base ufficiale.',
      rows: [
        ['Ospite', 'Luca Moretti'],
        ['Richiesta', 'Orari check-in & bagagli'],
        ['Fonte', 'Knowledge Base verificata'],
        ['Stato', 'Risposta inviata'],
      ],
      status: 'Informazioni fornite',
      statusPill: '✓ Inviato',
      footer: 'Estratto dalle policy della struttura.',
      guard: 'Melpis fornisce orari e servizi esatti configurati dalla direzione dell’hotel.',
    },
    partySize: 1,
    capacity: 1,
    calendarVerified: true,
  },

  disponibilita_camere: {
    nav: 'Disponibilità camere',
    icon: 'calendar',
    title: 'Disponibilità soggiorno',
    context: 'Hotel',
    client: { name: 'Luca Conti', initials: 'LC' },
    message: 'Avete una camera matrimoniale dal 10 al 12 maggio?',
    highlights: ['camera matrimoniale', '10 al 12 maggio'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Prenotazione camera' },
      { key: 'room', label: 'Tipologia', value: 'Matrimoniale Comfort' },
      { key: 'dates', label: 'Date', value: '10–12 maggio (2 notti)' },
      { key: 'guests', label: 'Ospiti', value: '2 persone' },
    ],
    knowledge: {
      detail: 'Listino camere & dotazioni',
      note: 'Camera Matrimoniale Comfort vista parco, colazione a buffet inclusa.',
      source: 'Knowledge Base · Hotel',
    },
    calendar: {
      requested: '10-12 mag',
      verdict: 'Camera 204 disponibile',
      slots: [
        { time: '08-10 mag', state: 'busy' },
        { time: '10-12 mag', state: 'free', target: true },
        { time: '12-14 mag', state: 'free' },
      ],
    },
    ack: 'Verifico la disponibilità delle camere e il piano tariffario per le date richieste.',
    reply: 'Sì, abbiamo disponibilità per una camera matrimoniale in quelle date. Vuoi procedere con la prenotazione?',
    followUp: [
      { role: 'client', text: 'Sì, procediamo pure con la prenotazione.' },
      { role: 'assistant', text: 'Ottimo Luca! Camera matrimoniale 204 riservata dal 10 al 12 maggio. Riceverai la conferma su WhatsApp!' },
    ],
    modules: {
      nlp: 'Matrimoniale · 10-12 maggio',
      kb: 'Comfort vista parco · colazione inc.',
      cal: 'Camera 204 libera',
    },
    outcome: {
      kind: 'confermata',
      title: 'Prenotazione confermata',
      detail: 'Camera riservata dal 10 al 12 maggio nel sistema dell’hotel.',
      rows: [
        ['Ospite', 'Luca Conti'],
        ['Camera', 'Matrimoniale Comfort (204)'],
        ['Date', '10–12 maggio (2 notti)'],
        ['Canale', 'WhatsApp Business'],
      ],
      status: 'Confermato',
      statusPill: '✓ Confermato',
      footer: 'Registrata nel calendario dimostrativo dell’hotel.',
      guard: 'Melpis riserva le camere solo previa verifica della reale disponibilità a gestionale.',
    },
    partySize: 2,
    capacity: 2,
    calendarVerified: true,
  },

  servizi: {
    nav: "Servizi dell'hotel",
    icon: 'cutlery',
    title: 'Informazioni servizi',
    context: 'Hotel',
    client: { name: 'Martina Rinaldi', initials: 'MR' },
    message: 'La colazione è inclusa? A che ora apre la spa?',
    highlights: ['colazione', 'inclusa', 'spa'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Richiesta info servizi' },
      { key: 'breakfast', label: 'Colazione', value: 'A buffet (07:30–10:30)' },
      { key: 'spa', label: 'Orario Spa', value: '09:00–20:00 tutti i giorni' },
      { key: 'amenities', label: 'Kit benessere', value: 'Incluso' },
    ],
    knowledge: {
      detail: 'Guida servizi e orari della struttura',
      note: 'Colazione a buffet inclusa con opzioni dolci, salate e biologiche. Spa aperta 09:00–20:00.',
      source: 'Knowledge Base · Servizi Hotel',
    },
    calendar: {
      requested: null,
      verdict: 'Non richiesto per questa richiesta',
      slots: [],
    },
    ack: 'Consulto le schede informative su colazione e orari spa della struttura.',
    reply: 'La colazione a buffet è inclusa nel soggiorno (dalle 07:30 alle 10:30). La nostra spa è aperta tutti i giorni dalle 09:00 alle 20:00 con kit benessere fornito in camera!',
    followUp: [
      { role: 'client', text: 'Perfetto, grazie mille per le informazioni!' },
      { role: 'assistant', text: 'Di nulla Martina! Se desideri prenotare un massaggio siamo a disposizione.' },
    ],
    modules: {
      nlp: 'Colazione & Spa · orari',
      kb: 'Buffet 07:30-10:30 · Spa 09:00-20:00',
      cal: 'Nessun vincolo calendario',
    },
    outcome: {
      kind: 'informativa',
      title: 'Informazioni fornite',
      detail: 'Dettagli su orari e servizi inviati dalla Knowledge Base ufficiale.',
      rows: [
        ['Ospite', 'Martina Rinaldi'],
        ['Richiesta', 'Colazione & Orari Spa'],
        ['Fonte', 'Knowledge Base verificata'],
        ['Stato', 'Risposta inviata'],
      ],
      status: 'Informazioni fornite',
      statusPill: '✓ Inviato',
      footer: 'Estratto dalle policy della struttura.',
      guard: 'Melpis fornisce orari e servizi esatti configurati dalla direzione dell’hotel.',
    },
    partySize: 1,
    capacity: 1,
    calendarVerified: true,
  },

  richieste_speciali: {
    nav: 'Richieste speciali',
    icon: 'clock',
    title: 'Late check-in e culla',
    context: 'Hotel',
    client: { name: 'Paolo Neri', initials: 'PN' },
    message: 'Possiamo fare il check-in tardivo verso le 23:30 e avere una culla?',
    highlights: ['check-in tardivo', '23:30', 'culla'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Late check-in + culla' },
      { key: 'eta', label: 'Orario arrivo', value: '23:30' },
      { key: 'special', label: 'Dotazione extra', value: 'Culla neonato' },
      { key: 'status', label: 'Accoglienza', value: 'Reception 24h attiva' },
    ],
    knowledge: {
      detail: 'Policy arrivi e dotazioni infanzia',
      note: 'Reception attiva 24 ore su 24. Culla disponibile gratuitamente per bambini fino a 3 anni.',
      source: 'Policy operative · Hotel',
    },
    calendar: {
      requested: null,
      verdict: 'Disponibilità culla verificata',
      slots: [],
    },
    ack: 'Verifico la presenza del portiere notturno e la disponibilità delle culle in camera.',
    reply: 'Certamente Paolo, la reception è attiva 24 ore su 24 per il late check-in e abbiamo predisposto la culla in camera senza costi aggiuntivi.',
    followUp: [
      { role: 'client', text: 'Ottimo, grazie per l’assistenza rapida!' },
      { role: 'assistant', text: 'Vi aspettiamo stasera! Buon viaggio verso la struttura.' },
    ],
    modules: {
      nlp: 'Late check-in 23:30 · Culla',
      kb: 'Reception 24h · Culla gratuita',
      cal: 'Dotazione approvata',
    },
    outcome: {
      kind: 'speciale',
      title: 'Richiesta speciale registrata',
      detail: 'Annotato arrivo notturno alle 23:30 con culla montata in camera.',
      rows: [
        ['Ospite', 'Paolo Neri'],
        ['Arrivo previsto', '23:30 (Late check-in)'],
        ['Dotazione', 'Culla neonato inclusa'],
        ['Stato', 'Trasmesso alla reception'],
      ],
      status: 'Richiesta annotata',
      statusPill: '✓ Notificato',
      footer: 'Sincronizzato con il turno di reception.',
      guard: 'Le preferenze e gli orari speciali vengono trasmessi automaticamente al personale di servizio.',
    },
    partySize: 2,
    capacity: 3,
    calendarVerified: true,
  },
};

export const scenarioOrder = ['check_in_bagagli', 'disponibilita_camere', 'servizi', 'richieste_speciali'];
export const defaultScenarioId = 'check_in_bagagli';

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
    ? 'Pronto: il messaggio dell’ospite avvia il flusso.'
    : complete
      ? scenario.outcome.status
      : verificationsComplete
        ? 'Verifiche completate: la risposta è pronta per l’ospite.'
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
