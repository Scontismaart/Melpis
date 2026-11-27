/**
 * Scenario data for the Melpis Barbieri Flow Demo (/barbieri/).
 *
 * Models the barbershop appointment pipeline:
 * Customer request -> Intent & entity analysis -> Barbershop Knowledge Base ->
 * Chair / Barber availability & calendar -> Reply generation -> Confirmation / Hand-off.
 */
import { bookingAllowed } from './booking-guard.mjs';

export const steps = [
  { id: 'received', label: 'Messaggio ricevuto', hint: 'La richiesta entra nella conversazione.' },
  { id: 'intent', label: 'Analisi intento', hint: 'Melpis riconosce servizio e orario richiesto.' },
  { id: 'knowledge', label: 'Knowledge Base', hint: 'Consulta listino servizi, durate e poltrone.' },
  { id: 'calendar', label: 'Disponibilità', hint: 'Verifica le poltrone libere in agenda.' },
  { id: 'reply', label: 'Risposta al cliente', hint: 'Prepara e invia la proposta di orario.' },
  { id: 'outcome', label: 'Esito', hint: 'Registra l’appuntamento confermato.' },
];

export const LAST_STEP = steps.length - 1;

export const stepDurations = [720, 1860, 1820, 2100, 1560, 1540];
export const reducedFactor = 0.22;

export const cardOrder = ['intent', 'knowledge', 'calendar', 'reply', 'dispatch'];
export const cardSteps = { intent: 1, knowledge: 2, calendar: 3, reply: 4, dispatch: 5 };
export const cardLabels = {
  intent: 'Analisi intento e parametri',
  knowledge: 'Servizi e poltrone',
  calendar: 'Calendario e disponibilità',
  reply: 'Generazione risposta',
  dispatch: 'Invio e conferma',
};
export const cardActiveLabels = {
  intent: 'Riconoscimento del servizio richiesto',
  knowledge: 'Verifica durata e poltrona assegnabile',
  calendar: 'Controllo degli slot liberi sulle poltrone',
  reply: 'Composizione della proposta oraria',
  dispatch: 'Registrazione dell’appuntamento',
};
export const cardDoneLabels = {
  intent: 'Servizio e orario riconosciuti',
  knowledge: 'Durata e listino verificati',
  calendar: 'Poltrona e orario individuati',
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
    nav: 'Taglio e barba',
    icon: 'calendar',
    title: 'Prenotazione taglio e barba',
    context: 'Barber Shop',
    client: { name: 'Alessandro Conti', initials: 'AC' },
    message: 'Ciao, quanto costa taglio più barba e avete posto domani?',
    highlights: ['taglio più barba', 'domani'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Info prezzo + Appuntamento' },
      { key: 'service', label: 'Servizi', value: 'Taglio + Barba (45 min · 35 €)' },
      { key: 'date', label: 'Giorno', value: 'Domani' },
      { key: 'time', label: 'Orario', value: '17:30' },
    ],
    knowledge: {
      detail: 'Listino servizi · Barber Shop',
      note: 'Combo Taglio & Barba: costo 35 €, durata 45 min su Poltrona 1 o 2.',
      source: 'Knowledge Base · Barber Shop',
    },
    calendar: {
      requested: '17:30',
      verdict: '17:30 disponibile su Poltrona 1',
      slots: [
        { time: '16:00', state: 'busy' },
        { time: '16:45', state: 'busy' },
        { time: '17:30', state: 'free', target: true },
        { time: '18:15', state: 'free' },
        { time: '19:00', state: 'busy' },
      ],
    },
    ack: 'Consulto il listino servizi e verifico le poltrone libere per domani.',
    reply: 'Il servizio taglio e barba costa 35 € e dura circa 45 minuti. Posso verificare gli orari disponibili nel calendario: domani alle 17:30 la Poltrona 1 è libera. Ti confermo l’appuntamento?',
    followUp: [
      { role: 'client', text: 'Sì, confermo grazie!' },
      { role: 'assistant', text: 'Perfetto Alessandro, appuntamento registrato per domani alle 17:30. Ti aspettiamo!' },
    ],
    modules: {
      nlp: 'Taglio + Barba · domani 17:30',
      kb: 'Combo Taglio & Barba · 45 min',
      cal: 'Poltrona 1 libera alle 17:30',
    },
    outcome: {
      kind: 'confermata',
      title: 'Appuntamento confermato',
      detail: 'Appuntamento registrato su Poltrona 1 nel calendario del negozio.',
      rows: [
        ['Cliente', 'Alessandro Conti'],
        ['Servizio', 'Taglio + Regolazione barba'],
        ['Quando', 'domani · 17:30'],
        ['Postazione', 'Poltrona 1'],
      ],
      status: 'Confermato',
      statusPill: '✓ Confermato',
      footer: 'Registrato nel calendario dimostrativo della barberia.',
      guard: 'Melpis propone solo slot liberi verificati e conferma l’appuntamento su assenso del cliente.',
    },
    partySize: 1,
    capacity: 2,
    calendarVerified: true,
  },

  trattamenti: {
    nav: 'Listino e panno caldo',
    icon: 'cutlery',
    title: 'Informazioni e listino',
    context: 'Barber Shop',
    client: { name: 'Matteo Riva', initials: 'MR' },
    message: 'Quanto costa la rasatura tradizionale con panno caldo?',
    highlights: ['rasatura tradizionale con panno caldo'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Richiesta tariffe e durata' },
      { key: 'service', label: 'Servizio', value: 'Trattamento Barba Executive' },
      { key: 'duration', label: 'Durata', value: 'Circa 30 minuti' },
      { key: 'price', label: 'Prezzo', value: '22 €' },
    ],
    knowledge: {
      detail: 'Listino servizi e trattamenti barba',
      note: 'Trattamento Barba Executive con doppio panno caldo e oli essenziali: 30 min, prezzo 22 €.',
      source: 'Knowledge Base · Listino servizi',
    },
    calendar: {
      requested: null,
      verdict: 'Non richiesto per questa richiesta',
      slots: [],
    },
    ack: 'Consulto il listino trattamenti della barberia.',
    reply: 'La rasatura tradizionale con doppio panno caldo e oli essenziali viene 22 € e dura circa 30 minuti. Vuoi che verifichi le disponibilità per oggi o domani?',
    followUp: [
      { role: 'client', text: 'Grazie mille per le informazioni, vi ricontatto a breve!' },
      { role: 'assistant', text: 'A tua completa disposizione! Puoi scriverci in qualsiasi momento.' },
    ],
    modules: {
      nlp: 'Rasatura con panno caldo',
      kb: 'Barba Executive · 30 min · 22 €',
      cal: 'Nessun vincolo orario',
    },
    outcome: {
      kind: 'informativa',
      title: 'Informazioni fornite',
      detail: 'Risposta inviata attingendo rigorosamente dal listino ufficiale del negozio.',
      rows: [
        ['Cliente', 'Matteo Riva'],
        ['Servizio', 'Barba Executive con panno caldo'],
        ['Listino', '22 € (30 min)'],
        ['Stato', 'Risposta inviata'],
      ],
      status: 'Informazioni fornite',
      statusPill: '✓ Inviato',
      footer: 'Dati estratti dalla Knowledge Base certificata della barberia.',
      guard: 'Melpis risponde solo con durate e prezzi configurati dal titolare, senza approssimazioni.',
    },
    partySize: 1,
    capacity: 1,
    calendarVerified: true,
  },

  modifica: {
    nav: 'Orario occupato',
    icon: 'clock',
    title: 'Gestione slot occupati',
    context: 'Barber Shop',
    client: { name: 'Davide Neri', initials: 'DN' },
    message: 'Avete posto per un taglio oggi alle 18:30?',
    highlights: ['taglio', 'oggi alle 18:30'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Prenotazione taglio' },
      { key: 'service', label: 'Servizio', value: 'Taglio classico' },
      { key: 'date', label: 'Giorno', value: 'Oggi' },
      { key: 'time', label: 'Orario richiesto', value: '18:30' },
    ],
    knowledge: {
      detail: 'Regole di prenotazione',
      note: 'Taglio classico: 30 minuti. Slot minimi ogni 30 minuti.',
      source: 'Knowledge Base · Barber Shop',
    },
    calendar: {
      requested: '18:30',
      verdict: '18:30 al completo; 17:45 e 19:15 liberi',
      slots: [
        { time: '17:00', state: 'busy' },
        { time: '17:45', state: 'free' },
        { time: '18:30', state: 'busy', target: true },
        { time: '19:15', state: 'free' },
      ],
    },
    ack: 'Verifico la disponibilità delle poltrone per oggi pomeriggio.',
    reply: 'Ciao Davide! Alle 18:30 tutte le poltrone sono già occupate. Ho disponibilità oggi alle 17:45 oppure alle 19:15. Uno di questi orari può andarti bene?',
    followUp: [
      { role: 'client', text: 'Le 19:15 è perfetto, grazie.' },
      { role: 'assistant', text: 'Ottimo Davide, prenotato per oggi alle 19:15. A più tardi!' },
    ],
    modules: {
      nlp: 'Taglio · oggi alle 18:30',
      kb: 'Taglio classico · 30 min',
      cal: '18:30 occupato · 17:45 e 19:15 liberi',
    },
    outcome: {
      kind: 'modifica',
      title: 'Alternativa proposta e confermata',
      detail: 'Slot alternativo concordato senza sovrapposizioni in agenda.',
      rows: [
        ['Cliente', 'Davide Neri'],
        ['Servizio', 'Taglio classico'],
        ['Orario concordato', 'oggi · 19:15'],
        ['Stato', 'Confermato alternativo'],
      ],
      status: 'Riprogrammato',
      statusPill: '✓ Aggiornato',
      footer: 'Sincronizzato con l’agenda delle poltrone.',
      guard: 'Melpis controlla l’effettiva disponibilità prima di confermare qualsiasi spostamento orario.',
    },
    partySize: 1,
    capacity: 2,
    calendarVerified: true,
  },

  disponibilita: {
    nav: 'Doppia poltrona / Staff',
    icon: 'star',
    title: 'Richiesta da concordare',
    context: 'Barber Shop',
    client: { name: 'Marco Ferri', initials: 'MF' },
    message: 'Siamo in due (io e mio figlio), vorremmo venire sabato mattina insieme. È possibile?',
    highlights: ['due persone', 'sabato mattina', 'insieme'],
    entities: [
      { key: 'intent', label: 'Intento', value: 'Prenotazione multipla simultanea' },
      { key: 'people', label: 'Persone', value: '2 (adulto + bambino)' },
      { key: 'date', label: 'Giorno', value: 'Sabato mattina' },
      { key: 'rule', label: 'Policy', value: 'Doppia poltrona simultanea richiede conferma dello staff' },
    ],
    knowledge: {
      detail: 'Regole su appuntamenti doppi',
      note: 'Le prenotazioni su due poltrone in contemporanea il sabato mattina richiedono validazione del team.',
      source: 'Policy Barber Shop',
    },
    calendar: {
      requested: 'Sabato mattina',
      verdict: 'Passaggio all’operatore per coordinamento poltrone',
      slots: [
        { time: '10:00', state: 'busy' },
        { time: '10:45', state: 'free' },
        { time: '11:30', state: 'free' },
      ],
    },
    ack: 'Identifico la richiesta di due poltrone contemporanee e coinvolgo lo staff.',
    reply: 'Ciao Marco! Per prenotare due poltrone in contemporanea sabato mattina passo subito la richiesta al team, così verifichiamo la sincronia delle poltrone e ti ricontattiamo qui a brevissimo.',
    followUp: [
      { role: 'client', text: 'Grazie mille, rimango in attesa!' },
      { role: 'assistant', text: 'Richiesta inoltrata allo staff. Ti scriveremo appena verificate le poltrone.' },
    ],
    modules: {
      nlp: '2 tagli simultanei · sabato mattina',
      kb: 'Regola sabato mattina · approvazione staff',
      cal: 'Passaggio al team per assegnazione',
    },
    outcome: {
      kind: 'escalation',
      title: 'Richiesta passata allo staff',
      detail: 'Notifica inviata al team per coordinare le due poltrone contemporaneamente.',
      rows: [
        ['Cliente', 'Marco Ferri'],
        ['Richiesta', '2 tagli simultanei (adulto + bambino)'],
        ['Quando', 'sabato mattina'],
        ['Stato', 'In attesa operatore'],
      ],
      status: 'Passato allo staff',
      statusPill: 'In attesa team',
      footer: 'Notifica inviata all’inbox degli operatori.',
      guard: 'Melpis non inventa slot simultanei quando le regole del negozio impongono la supervisione dello staff.',
    },
    partySize: 2,
    capacity: 2,
    calendarVerified: false,
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
