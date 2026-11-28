/**
 * Scenario data for the Melpis Saloni di Bellezza Flow Demo (/saloni/).
 *
 * Models the beauty salon appointment pipeline:
 * Customer request -> Intent & entity analysis -> Salon Knowledge Base ->
 * Calendar & staff availability -> Reply generation -> Confirmation / Hand-off.
 */
import { bookingAllowed } from './booking-guard.mjs';

export const steps = [
  { id: 'received', label: 'Messaggio ricevuto', hint: 'La richiesta entra nella conversazione.' },
  { id: 'intent', label: 'Analisi intento', hint: 'Melpis riconosce cosa chiede il cliente.' },
  { id: 'knowledge', label: 'Knowledge Base', hint: 'Consulta listino trattamenti e durate.' },
  { id: 'calendar', label: 'Disponibilità', hint: 'Verifica gli orari liberi in agenda.' },
  { id: 'reply', label: 'Risposta al cliente', hint: 'Prepara e invia la proposta di appuntamento.' },
  { id: 'outcome', label: 'Esito', hint: 'Registra l’appuntamento confermato.' },
];

export const LAST_STEP = steps.length - 1;

export const stepDurations = [720, 1860, 1820, 2100, 1560, 1540];
export const reducedFactor = 0.22;

export const cardOrder = ['intent', 'knowledge', 'calendar', 'reply', 'dispatch'];
export const cardSteps = { intent: 1, knowledge: 2, calendar: 3, reply: 4, dispatch: 5 };
export const cardLabels = {
  intent: 'Analisi intento e parametri',
  knowledge: 'Trattamenti e regole',
  calendar: 'Calendario e disponibilità',
  reply: 'Generazione risposta',
  dispatch: 'Invio e conferma',
};
export const cardActiveLabels = {
  intent: 'Riconoscimento del trattamento richiesto',
  knowledge: 'Verifica durata e tariffe nel listino',
  calendar: 'Controllo degli slot liberi nel salone',
  reply: 'Composizione della proposta oraria',
  dispatch: 'Registrazione dell’appuntamento',
};
export const cardDoneLabels = {
  intent: 'Trattamento e giorno riconosciuti',
  knowledge: 'Durata e listino verificati',
  calendar: 'Slot disponibili individuati',
  reply: 'Proposta inviata al cliente',
  dispatch: 'Confermato',
};

export const verifications = [
  { id: 'intent', atStep: 1 },
  { id: 'knowledge', atStep: 2 },
  { id: 'calendar', atStep: 3 },
];

export const sendStep = cardSteps.reply;

export const scenarios = {
  nuovo_appuntamento: {
    nav: 'Nuovo appuntamento',
    icon: 'calendar',
    title: 'Prenotazione appuntamento',
    context: 'Salone di bellezza',
    client: { name: 'Giulia Bianchi', initials: 'GB' },
    message: 'Buongiorno, quanto dura una pulizia viso e avete posto venerdì?',
    highlights: ['quanto dura una pulizia viso', 'venerdì'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Info durata + Appuntamento' },
      { key: 'service', label: 'Trattamento', value: 'Pulizia viso (60 min · 65 €)' },
      { key: 'date', label: 'Giorno', value: 'Venerdì pomeriggio' },
      { key: 'duration', label: 'Durata stimata', value: '60 minuti' },
    ],
    knowledge: {
      detail: 'Listino trattamenti · Centro estetico',
      note: 'Pulizia viso profonda: durata 60 min, costo 65 € con maschera inclusa.',
      source: 'Knowledge Base · Centro estetico',
    },
    calendar: {
      requested: '15:00',
      verdict: '15:00 e 17:30 disponibili',
      slots: [
        { time: '14:00', state: 'busy' },
        { time: '15:00', state: 'free', target: true },
        { time: '16:30', state: 'busy' },
        { time: '17:30', state: 'free' },
        { time: '19:00', state: 'busy' },
      ],
    },
    ack: 'Consulto la durata e il prezzo del trattamento e verifico l’agenda per venerdì.',
    reply: 'Il trattamento viso dura circa 60 minuti e costa 65 €. Posso controllare gli orari per venerdì: abbiamo posto alle 15:00 o alle 17:30. Quale preferisci?',
    followUp: [
      { role: 'client', text: 'Venerdì alle 15:00 va benissimo, grazie!' },
      { role: 'assistant', text: 'Perfetto Giulia, appuntamento registrato per venerdì alle 15:00 in Cabina 1. Ti aspettiamo!' },
    ],
    modules: {
      nlp: 'Pulizia viso · venerdì pomeriggio',
      kb: 'Pulizia viso · 60 min',
      cal: '15:00 e 17:30 liberi',
    },
    outcome: {
      kind: 'confermata',
      title: 'Appuntamento confermato',
      detail: 'Appuntamento inserito per venerdì alle 15:00 nel calendario del centro.',
      rows: [
        ['Cliente', 'Giulia Bianchi'],
        ['Trattamento', 'Pulizia viso (60 min)'],
        ['Quando', 'venerdì · 15:00'],
        ['Canale', 'WhatsApp Business'],
      ],
      status: 'Confermato',
      statusPill: '✓ Confermato',
      footer: 'Registrato nel calendario dimostrativo.',
      guard: 'Melpis propone solo slot liberi verificati e conferma l’appuntamento su assenso del cliente.',
    },
    partySize: 1,
    capacity: 2,
    calendarVerified: true,
  },

  trattamenti: {
    nav: 'Trattamenti e prezzi',
    icon: 'cutlery',
    title: 'Informazioni listino',
    context: 'Salone di bellezza',
    client: { name: 'Chiara Moretti', initials: 'CM' },
    message: 'Quanto dura il trattamento alla cheratina e quanto costa?',
    highlights: ['cheratina', 'quanto dura', 'costa'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Richiesta tariffe e durata' },
      { key: 'service', label: 'Servizio', value: 'Trattamento cheratina' },
      { key: 'duration', label: 'Durata', value: 'Circa 90 minuti' },
      { key: 'price', label: 'Prezzo', value: '65 €' },
    ],
    knowledge: {
      detail: 'Scheda trattamenti liscianti e anticrespo',
      note: 'Trattamento cheratina ristrutturante: tempo di esecuzione 90 min, prezzo 65 €.',
      source: 'Knowledge Base · Listino servizi',
    },
    calendar: {
      requested: null,
      verdict: 'Non richiesto per questa richiesta',
      slots: [],
    },
    ack: 'Consulto la Knowledge Base configurata con il listino trattamenti del salone.',
    reply: 'Il trattamento alla cheratina dura circa 90 minuti e ha un costo di 65 €. Vuoi verificare le disponibilità per fissare una seduta?',
    followUp: [
      { role: 'client', text: 'Grazie mille per le informazioni, vi ricontatto a breve!' },
      { role: 'assistant', text: 'A tua completa disposizione! Puoi scriverci in qualsiasi momento.' },
    ],
    modules: {
      nlp: 'Cheratina · durata e prezzo',
      kb: 'Cheratina · 90 min · 65 €',
      cal: 'Nessun vincolo orario',
    },
    outcome: {
      kind: 'informativa',
      title: 'Informazioni fornite',
      detail: 'Risposta inviata attingendo rigorosamente dal listino ufficiale del salone.',
      rows: [
        ['Cliente', 'Chiara Moretti'],
        ['Trattamento', 'Trattamento cheratina'],
        ['Listino', '65 € (90 min)'],
        ['Stato', 'Risposta inviata'],
      ],
      status: 'Informazioni fornite',
      statusPill: '✓ Inviato',
      footer: 'Dati estratti dalla Knowledge Base certificata del salone.',
      guard: 'Melpis risponde solo con durate e prezzi configurati dal titolare, senza approssimazioni.',
    },
    partySize: 1,
    capacity: 1,
    calendarVerified: true,
  },

  modifica: {
    nav: 'Modifica appuntamento',
    icon: 'clock',
    title: 'Modifica orario',
    context: 'Salone di bellezza',
    client: { name: 'Sara Ferri', initials: 'SF' },
    message: 'Ciao, ho un taglio domani alle 15:00, posso spostarlo alle 18:00?',
    highlights: ['taglio domani', 'spostarlo alle 18:00'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Modifica orario' },
      { key: 'appointment', label: 'Appuntamento', value: 'Taglio donna (domani)' },
      { key: 'newTime', label: 'Nuovo orario', value: '18:00' },
      { key: 'status', label: 'Fattibilità', value: 'Slot libero' },
    ],
    knowledge: {
      detail: 'Policy modifiche e disdette',
      note: 'Variazioni di orario consentite con preavviso se la postazione risulta disponibile.',
      source: 'Policy operative · Salone',
    },
    calendar: {
      requested: '18:00',
      verdict: '18:00 disponibile',
      slots: [
        { time: '16:30', state: 'busy' },
        { time: '17:15', state: 'busy' },
        { time: '18:00', state: 'free', target: true },
        { time: '18:45', state: 'busy' },
      ],
    },
    ack: 'Verifico la disponibilità dello slot alle 18:00 per la modifica dell’appuntamento.',
    reply: 'Certo Sara! Alle 18:00 lo slot è libero: ho aggiornato il tuo appuntamento per domani alle 18:00.',
    followUp: [
      { role: 'client', text: 'Perfetto, grazie mille per la flessibilità!' },
      { role: 'assistant', text: 'Di nulla Sara, ci vediamo domani alle 18:00!' },
    ],
    modules: {
      nlp: 'Spostamento taglio · ore 18:00',
      kb: 'Policy modifiche · consentita',
      cal: '18:00 libero',
    },
    outcome: {
      kind: 'modifica',
      title: 'Appuntamento riprogrammato',
      detail: 'Orario aggiornato in agenda salvando lo storico della modifica.',
      rows: [
        ['Cliente', 'Sara Ferri'],
        ['Trattamento', 'Taglio donna'],
        ['Nuovo orario', 'domani · 18:00'],
        ['Stato', 'Aggiornato in agenda'],
      ],
      status: 'Riprogrammato',
      statusPill: '✓ Aggiornato',
      footer: 'Sincronizzato con il calendario del salone.',
      guard: 'Melpis controlla l’effettiva disponibilità prima di confermare qualsiasi spostamento orario.',
    },
    partySize: 1,
    capacity: 2,
    calendarVerified: true,
  },

  disponibilita: {
    nav: 'Disponibilità',
    icon: 'star',
    title: 'Disponibilità cerimonia',
    context: 'Salone di bellezza',
    client: { name: 'Elena Vitali', initials: 'EV' },
    message: 'Avete posto per piega e trucco sabato mattina per una cerimonia?',
    highlights: ['piega e trucco', 'sabato mattina', 'cerimonia'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Richiesta pacchetto cerimonia' },
      { key: 'services', label: 'Servizi', value: 'Piega + Trucco cerimonia' },
      { key: 'date', label: 'Fascia richiesta', value: 'Sabato mattina' },
      { key: 'duration', label: 'Durata tot.', value: '105 minuti' },
    ],
    knowledge: {
      detail: 'Pacchetti cerimonie & make-up',
      note: 'Servizio combinato piega e make-up: richiede postazione dedicata e 105 min complessivi.',
      source: 'Knowledge Base · Salone',
    },
    calendar: {
      requested: '10:30',
      verdict: '10:30 disponibile',
      slots: [
        { time: '09:00', state: 'busy' },
        { time: '10:30', state: 'free', target: true },
        { time: '12:15', state: 'busy' },
      ],
    },
    ack: 'Verifico la disponibilità congiunta per postazione piega e postazione make-up.',
    reply: 'Sabato mattina alle 10:30 abbiamo disponibilità per eseguire sia piega che trucco. Vuoi confermare questo orario?',
    followUp: [
      { role: 'client', text: 'Sì grazie, confermo per le 10:30!' },
      { role: 'assistant', text: 'Fantastico Elena, appuntamento confermato per sabato alle 10:30. A presto!' },
    ],
    modules: {
      nlp: 'Piega + Trucco · sabato mattina',
      kb: 'Pacchetto cerimonia · 105 min',
      cal: '10:30 disponibile',
    },
    outcome: {
      kind: 'confermata',
      title: 'Appuntamento confermato',
      detail: 'Postazione piega e trucco bloccate per sabato alle 10:30.',
      rows: [
        ['Cliente', 'Elena Vitali'],
        ['Servizio', 'Piega + Trucco cerimonia'],
        ['Quando', 'sabato · 10:30'],
        ['Canale', 'WhatsApp Business'],
      ],
      status: 'Confermato',
      statusPill: '✓ Confermato',
      footer: 'Registrato nel calendario dimostrativo.',
      guard: 'Melpis riserva le postazioni solo se la durata congiunta dei trattamenti è compatibile.',
    },
    partySize: 1,
    capacity: 2,
    calendarVerified: true,
  },
};

export const scenarioOrder = ['nuovo_appuntamento', 'trattamenti', 'modifica', 'disponibilita'];
export const defaultScenarioId = 'nuovo_appuntamento';

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
