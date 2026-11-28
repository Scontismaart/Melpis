import { useEffect, useRef, useState } from 'react';

const NAV = [
  { id: 'panoramica', label: 'Panoramica', title: 'Panoramica', icon: 'grid', group: 'main', view: true },
  { id: 'inbox', label: 'Inbox', title: 'Inbox', icon: 'inbox', group: 'main', view: true },
  { id: 'prenotazioni', label: 'Prenotazioni', title: 'Prenotazioni', icon: 'calendar', group: 'main', view: true },
  { id: 'recensioni', label: 'Recensioni', title: 'Recensioni & Feedback', icon: 'star', group: 'main', view: true },
  { id: 'team', label: 'Team & Collaboratori', title: 'Team & Collaboratori', icon: 'users', group: 'main' },
  { id: 'simulatore', label: 'Simulatore AI', title: 'Simulatore Assistente AI', icon: 'chat', group: 'assistant', view: true },
  { id: 'conoscenza', label: 'Conoscenza', title: 'Conoscenza', icon: 'book', group: 'assistant' },
  { id: 'configurazione', label: 'Configurazione AI', title: 'Configurazione AI', icon: 'robot', group: 'assistant' },
];

const KV = {
  grid: <><rect x="3.5" y="3.5" width="7.5" height="7.5" rx="1.5"/><rect x="13" y="3.5" width="7.5" height="7.5" rx="1.5"/><rect x="3.5" y="13" width="7.5" height="7.5" rx="1.5"/><rect x="13" y="13" width="7.5" height="7.5" rx="1.5"/></>,
  inbox: <><path d="M4 6.5h16v11H4v-11Z"/><path d="M4 8l8 6 8-6"/></>,
  calendar: <><path d="M7 3.5v3M17 3.5v3M4.5 9h15"/><path d="M6 5h12a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z"/></>,
  star: <path d="M12 3.5l2.66 5.4 5.96.87-4.31 4.2 1.02 5.93L12 17l-5.33 2.9 1.02-5.93-4.31-4.2 5.96-.87L12 3.5Z"/>,
  users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></>,
  chat: <path d="M4 5.5h16v10H9l-4 4v-4H4v-10Z"/>,
  book: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15.5H6.5A2.5 2.5 0 0 0 4 21V5.5Z"/><path d="M4 18.5A2.5 2.5 0 0 1 6.5 16H20M8.5 7.5h7"/></>,
  robot: <><rect x="3" y="6" width="18" height="14" rx="3"/><circle cx="8.5" cy="12" r="1.5"/><circle cx="15.5" cy="12" r="1.5"/><path d="M12 2v4M9 16h6"/></>,
  search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></>,
  bell: <><path d="M18 9.5a6 6 0 1 0-12 0v2.9c0 .6-.24 1.18-.66 1.6L4.1 15.24c-.63.63-.19 1.76.71 1.76h14.38c.9 0 1.34-1.13.71-1.76l-1.24-1.24a2.26 2.26 0 0 1-.66-1.6V9.5Z"/><path d="M10 20a2 2 0 0 0 4 0"/></>,
  headset: <><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></>,
  user: <><circle cx="12" cy="8" r="4"/><path d="M6 20v-2a6 6 0 0 1 12 0v2"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>,
  check: <path d="M20 6 9 17l-5-5"/>,
  refresh: <><path d="M4 12a8 8 0 0 1 13.7-5.6L20 8"/><path d="M20 4v4h-4"/><path d="M20 12a8 8 0 0 1-13.7 5.6L4 16"/><path d="M4 20v-4h4"/></>,
  globe: <><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 4 9 15 15 0 0 1-4 9 15 15 0 0 1-4-9 15 15 0 0 1 4-9z"/></>,
  send: <path d="M4 12 20 4l-6 16-2.5-6.5L4 12Z"/>,
  spark: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z"/>,
  bars: <path d="M4 20V10M9 20V4M14 20v-7M19 20V8"/>,
  chevron: <path d="m9 18 6-6-6-6"/>,
  chevronDown: <path d="m7 10 5 5 5-5"/>,
  arrowRight: <><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></>,
  dot: <circle cx="12" cy="12" r="4"/>,
};

function Icon({ name, className, width, height }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      width={width}
      height={height}
    >
      {KV[name]}
    </svg>
  );
}

function Spark({ variant }) {
  return (
    <span className={`mv2-ps-spark mv2-ps-spark-${variant}`} aria-hidden="true">
      <i /><i /><i /><i /><i /><i /><i />
    </span>
  );
}

const KPIS = [
  { icon: 'inbox', label: 'Conversazioni aperte', value: '42', trend: '+6 oggi', dir: 'up', variant: 'silver', iconVariant: 'silver', footer: '+18%' },
  { icon: 'headset', label: 'Risposte automatiche oggi', value: '186', trend: '+24 oggi', dir: 'up', variant: 'positive', iconVariant: 'positive', footer: '+12%' },
  { icon: 'clock', label: 'Tempo medio di risposta', value: '1m 48s', trend: '-22s oggi', dir: 'down', variant: 'positive', iconVariant: 'silver', footer: '-16%' },
  { icon: 'calendar', label: 'Prenotazioni confermate', value: '17', trend: '+3 oggi', dir: 'up', variant: 'positive', iconVariant: 'positive', footer: '+9%' },
  { icon: 'user', label: 'Escalation a operatore', value: '6', trend: 'in calo', dir: 'down', variant: 'amber', iconVariant: 'amber', footer: '-2' },
  { icon: 'star', label: 'Recensioni gestite', value: '23', trend: '+4 questa settimana', dir: 'up', variant: 'positive', iconVariant: 'positive', footer: '+4' },
  { icon: 'robot', label: 'Tasso di automazione', value: '78%', trend: '+5 punti', dir: 'up', variant: 'positive', iconVariant: 'positive', footer: '+5%' },
  { icon: 'check', label: 'Soddisfazione clienti', value: '96%', trend: '+1 punto', dir: 'up', variant: 'positive', iconVariant: 'positive', footer: '+1%' },
];

const TREND_DIR = { up: '↑', down: '↓', flat: '•' };

const WEEK = [['Lun', 24], ['Mar', 31], ['Mer', 28], ['Gio', 42], ['Ven', 38], ['Sab', 46], ['Dom', 19]];

const ATTENTION = [
  { icon: 'user', title: 'Escalation richiesta', note: 'Marco Bianchi vuole parlare con un operatore per la fattura.', time: '12 min' },
  { icon: 'star', title: 'Recensione da gestire', note: 'Federica Neri ha lasciato 2 stelle su Google.', time: '1 h' },
];

const ACTIVITY = [
  { icon: 'chat', title: 'Risposta automatica inviata', note: 'Giulia Rossi · disponibilità tavolo venerdì', time: '2 min' },
  { icon: 'calendar', title: 'Prenotazione confermata', note: 'Marta Greco · 14:00 · 2 persone', time: '18 min' },
  { icon: 'star', title: 'Bozza di risposta generata', note: 'Recensione Google di Chiara De Luca', time: '42 min' },
  { icon: 'user', title: 'Conversazione presa in carico', note: 'Sara Conti · operatore Luca', time: '1 h' },
];

const CHANNELS = [
  { name: 'WhatsApp Business', detail: 'Ultimo evento 2 min fa', state: 'Attivo', ok: true },
  { name: 'Instagram DM', detail: 'Ultimo evento 26 min fa', state: 'Attivo', ok: true },
  { name: 'Google Business', detail: 'Recensioni sincronizzate', state: 'Attivo', ok: true },
];

function Panoramica({ onSelect }) {
  return (
    <div className="mv2-ps-view mv2-ps-overview">
      <div className="mv2-ps-kpi-row">
        {KPIS.map(kpi => (
          <article className="mv2-ps-kpi" key={kpi.label}>
            <div className="mv2-ps-kpi-main">
              <span className={`mv2-ps-kpi-icon mv2-ps-kpi-icon-${kpi.iconVariant}`}><Icon name={kpi.icon} /></span>
              <span className="mv2-ps-kpi-info">
                <span className="mv2-ps-kpi-label">{kpi.label}</span>
                <span className="mv2-ps-kpi-value">{kpi.value}</span>
              </span>
            </div>
            <div className="mv2-ps-kpi-middle">
              <span className="mv2-ps-trend"><span className="mv2-ps-trend-icon">{TREND_DIR[kpi.dir]}</span>{kpi.trend}</span>
              <Spark variant={kpi.variant} />
            </div>
            <div className="mv2-ps-kpi-footer">
              <span>Ultimi 7 giorni</span>
              <span>{kpi.footer}</span>
            </div>
          </article>
        ))}
      </div>
      <div className="mv2-ps-panorama-grid">
        <section className="mv2-ps-surface mv2-ps-panel">
          <header className="mv2-ps-card-header">
            <span className="mv2-ps-card-title"><Icon name="bars" />Conversazioni · ultimi 7 giorni</span>
            <span className="mv2-ps-chart-total"><strong>228</strong> totali</span>
          </header>
          <div className="mv2-ps-chart">
            <div className="mv2-ps-chart-bars">
              {WEEK.map(([day, value]) => (
                <span className="mv2-ps-chart-col" key={day}>
                  <span className="mv2-ps-chart-value">{value}</span>
                  <span className="mv2-ps-chart-bar" aria-hidden="true" />
                </span>
              ))}
            </div>
            <div className="mv2-ps-chart-labels">
              {WEEK.map(([day]) => <span className="mv2-ps-chart-label" key={day}>{day}</span>)}
            </div>
          </div>
        </section>
        <section className="mv2-ps-surface mv2-ps-panel">
          <header className="mv2-ps-card-header">
            <span className="mv2-ps-card-title"><Icon name="bell" />Richiede la tua attenzione</span>
            <button className="mv2-ps-card-link" type="button" onClick={() => onSelect?.('inbox')}>
              <Icon name="inbox" width="15" height="15" />Vai all&rsquo;Inbox<Icon name="chevron" width="14" height="14" />
            </button>
          </header>
          <div className="mv2-ps-feed">
            {ATTENTION.map(item => (
              <article className="mv2-ps-feed-item" key={item.title}>
                <span className="mv2-ps-feed-mark mv2-ps-feed-mark-amber"><Icon name={item.icon} width="15" height="15" /></span>
                <span className="mv2-ps-feed-copy"><strong>{item.title}</strong><span>{item.note}</span></span>
                <time className="mv2-ps-feed-time">{item.time}</time>
              </article>
            ))}
          </div>
        </section>
        <section className="mv2-ps-surface mv2-ps-panel">
          <header className="mv2-ps-card-header">
            <span className="mv2-ps-card-title"><Icon name="clock" />Attività recente</span>
            <button className="mv2-ps-card-button" type="button">Visualizza tutte<Icon name="arrowRight" width="14" height="14" /></button>
          </header>
          <div className="mv2-ps-feed">
            {ACTIVITY.map(item => (
              <article className="mv2-ps-feed-item" key={item.title}>
                <span className="mv2-ps-feed-mark"><Icon name={item.icon} width="15" height="15" /></span>
                <span className="mv2-ps-feed-copy"><strong>{item.title}</strong><span>{item.note}</span></span>
                <time className="mv2-ps-feed-time">{item.time}</time>
              </article>
            ))}
          </div>
        </section>
        <section className="mv2-ps-surface mv2-ps-panel">
          <header className="mv2-ps-card-header">
            <span className="mv2-ps-card-title"><Icon name="globe" />Canali collegati</span>
            <span className="mv2-ps-chart-total"><strong>3</strong> attivi</span>
          </header>
          <div className="mv2-ps-feed">
            {CHANNELS.map(channel => (
              <article className="mv2-ps-feed-item" key={channel.name}>
                <span className="mv2-ps-feed-mark mv2-is-ok"><Icon name="check" width="15" height="15" /></span>
                <span className="mv2-ps-feed-copy"><strong>{channel.name}</strong><span>{channel.detail}</span></span>
                <span className="mv2-ps-badge mv2-ps-badge-ok">{channel.state}</span>
              </article>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

const CONVERSATIONS = [
  {
    id: 'giulia', name: 'Giulia Rossi', initials: 'GR', phone: '+39 340 123 4567', channel: 'WhatsApp',
    time: '28 ago', last: '28/08, 18:21', state: 'AI', escalated: false,
    preview: 'Vorrei prenotare un tavolo per 4 persone venerdì sera.',
    tag: 'Prenotazione', action: 'Risposta automatica inviata',
    note: 'Cliente abituale. Preferisce il tavolo vicino alla finestra.',
    messages: [
      { dir: 'in', text: 'Buonasera, avete un tavolo per 4 persone venerdì sera?' },
      { dir: 'out', text: 'Buonasera Giulia! Certamente: per quante persone e a che ora preferisce?' },
      { dir: 'in', text: 'Siamo in 4, verso le 20:30 se possibile.' },
      { dir: 'out', text: 'Perfetto, ho segnato 4 persone venerdì alle 20:30. Confermo la prenotazione?' },
      { dir: 'in', text: 'Sì, confermate. Grazie mille!' },
    ],
  },
  {
    id: 'marco', name: 'Marco Bianchi', initials: 'MB', phone: '+39 351 520 5809', channel: 'WhatsApp',
    time: '31 ago', last: '31/08, 10:04', state: 'Escalation', escalated: true,
    preview: 'Vorrei parlare con una persona, ho bisogno di una fattura.',
    tag: 'Fatturazione', action: 'Escalation all’operatore',
    note: 'Richiesta amministrativa fuori ambito: girata allo staff.',
    messages: [
      { dir: 'in', text: 'Buongiorno, mi serve la fattura della settimana scorsa.' },
      { dir: 'out', text: 'Buongiorno Marco! Per la fatturazione ti metto in contatto con un operatore.' },
      { dir: 'in', text: 'Grazie, resto in attesa.' },
    ],
  },
  {
    id: 'elena', name: 'Elena Moretti', initials: 'EM', phone: '+39 333 902 7741', channel: 'Instagram',
    time: '1 set', last: '01/09, 09:12', state: 'AI', escalated: false,
    preview: 'Ciao! Avete disponibilità per una consulenza la prossima settimana?',
    tag: 'Consulenza', action: 'Disponibilità proposta',
    note: 'Preferisce il pomeriggio dopo le 17:00.',
    messages: [
      { dir: 'in', text: 'Ciao! Avete disponibilità per una consulenza la prossima settimana?' },
      { dir: 'out', text: 'Ciao Elena! Certo: martedì alle 17:30 o giovedì alle 18:00. Quale preferisci?' },
      { dir: 'in', text: 'Martedì alle 17:30 va benissimo.' },
    ],
  },
  {
    id: 'luca', name: 'Luca Ferri', initials: 'LF', phone: '+39 328 441 6620', channel: 'WhatsApp',
    time: '1 set', last: '01/09, 11:38', state: 'AI', escalated: false,
    preview: 'Posso spostare la prenotazione di domani alle 19?',
    tag: 'Modifica', action: 'Prenotazione aggiornata',
    note: 'Spostamento gestito senza intervento umano.',
    messages: [
      { dir: 'in', text: 'Posso spostare la prenotazione di domani alle 19?' },
      { dir: 'out', text: 'Certo Luca, ho spostato la prenotazione di domani alle 19:00. Resto a disposizione per qualsiasi altra richiesta.' },
      { dir: 'in', text: 'Perfetto, grazie!' },
    ],
  },
  {
    id: 'sara', name: 'Sara Conti', initials: 'SC', phone: '+39 347 118 3390', channel: 'WhatsApp',
    time: '2 set', last: '02/09, 15:47', state: 'Operatore', escalated: false,
    preview: 'Grazie mille, gentilissimi come sempre!',
    tag: 'Feedback', action: 'Conversazione risolta',
    note: 'Cliente soddisfatta: recensione positiva in arrivo.',
    messages: [
      { dir: 'in', text: 'Volevo ringraziarvi per ieri sera, tutto perfetto.' },
      { dir: 'out', text: 'Grazie a te Sara! Se ti va, lasciaci una recensione: ci aiuta moltissimo.' },
      { dir: 'in', text: 'Grazie mille, gentilissimi come sempre!' },
    ],
  },
];

function Inbox({ hydrated }) {
  const [selected, setSelected] = useState(0);
  const conversation = CONVERSATIONS[selected];
  const details = [
    ['Numero', conversation.phone],
    ['Canale', conversation.channel],
    ['Stato', conversation.state],
    ['Tag', conversation.tag],
    ['Ultima azione', conversation.action],
    ['Ultimo messaggio', conversation.last],
  ];
  return (
    <div className="mv2-ps-view mv2-ps-inbox">
      <div className="mv2-ps-inbox-layout">
        <div className="mv2-ps-inbox-list-col">
          <header className="mv2-ps-inbox-head">
            <h3>Inbox</h3>
            <span className="mv2-ps-count-pill">5</span>
          </header>
          <div className="mv2-ps-filters" role="group" aria-label="Filtri canale">
            <button className="mv2-ps-chip mv2-is-active" type="button">Tutti</button>
            <button className="mv2-ps-chip" type="button"><span className="mv2-ps-wa-dot"><Icon name="chat" width="13" height="13" /></span>WhatsApp <i>4</i></button>
            <button className="mv2-ps-chip" type="button"><span className="mv2-ps-ig-dot"><Icon name="globe" width="13" height="13" /></span>Instagram <i>1</i></button>
          </div>
          <div className="mv2-ps-filters" role="group" aria-label="Filtri stato">
            <button className="mv2-ps-chip mv2-is-active" type="button">Tutti</button>
            <button className="mv2-ps-chip" type="button"><Icon name="robot" width="13" height="13" />AI</button>
            <button className="mv2-ps-chip" type="button"><Icon name="user" width="13" height="13" />Operatore</button>
            <button className="mv2-ps-chip" type="button"><span className="mv2-ps-dot mv2-ps-dot-red" />Escalated <b>6</b></button>
            <button className="mv2-ps-chip" type="button"><Icon name="check" width="13" height="13" />Risolte</button>
          </div>
          <span className="mv2-ps-search">
            <Icon name="search" width="15" height="15" />
            <input type="search" placeholder="Cerca conversazione…" aria-label="Cerca conversazione" />
          </span>
          <div className="mv2-ps-conversation-list" role="group" aria-label="Conversazioni">
            {CONVERSATIONS.map((row, index) => (
              <button
                key={row.id}
                className={`mv2-ps-conversation${selected === index ? ' mv2-is-selected' : ''}`}
                type="button"
                disabled={!hydrated}
                aria-pressed={selected === index}
                onClick={() => setSelected(index)}
              >
                <span className="mv2-ps-avatar">{row.initials}</span>
                <span className="mv2-ps-conv-copy">
                  <span className="mv2-ps-conv-top"><strong>{row.name}</strong><time>{row.time}</time></span>
                  <span className="mv2-ps-conv-meta"><span className={row.channel === 'Instagram' ? 'mv2-ps-ig-dot' : 'mv2-ps-wa-dot'}><Icon name={row.channel === 'Instagram' ? 'globe' : 'chat'} width="12" height="12" /></span>{row.channel}<span className="mv2-ps-meta-sep">·</span><Icon name={row.escalated ? 'user' : 'robot'} width="12" height="12" />{row.state}</span>
                  <span className="mv2-ps-conv-preview">{row.preview}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
        <section className="mv2-ps-chat" aria-label={`Conversazione con ${conversation.name}`}>
          <header className="mv2-ps-chat-head">
            <span className="mv2-ps-avatar">{conversation.initials}</span>
            <span className="mv2-ps-chat-id">
              <strong>{conversation.name}</strong>
              <small>{conversation.phone}</small>
            </span>
            <span className="mv2-ps-chat-pills">
              <span className="mv2-ps-pill mv2-ps-pill-wa"><Icon name={conversation.channel === 'Instagram' ? 'globe' : 'chat'} width="13" height="13" />{conversation.channel}</span>
              <span className="mv2-ps-pill">{conversation.state}</span>
            </span>
          </header>
          <p className="mv2-ps-status">
            <Icon name={conversation.escalated ? 'user' : 'inbox'} width="15" height="15" />
            {conversation.escalated
              ? 'Escalation consigliata: un operatore prenderà in carico la conversazione.'
              : 'L’assistente sta gestendo la conversazione.'}
          </p>
          <div className="mv2-ps-chat-body">
            <div className="mv2-ps-transcript" aria-label="Cronologia messaggi">
              {conversation.messages.map((message, index) => (
                <div
                  className={`mv2-ps-bubble${message.dir === 'in' ? ' mv2-ps-bubble-user' : ''}`}
                  key={index}
                >
                  {message.text}
                </div>
              ))}
            </div>
          </div>
          <footer className="mv2-ps-composer-note">
            <p>Per rispondere manualmente, prendi prima in carico la conversazione.</p>
            <button className="mv2-ps-primary" type="button">Prendi in carico</button>
          </footer>
        </section>
        <aside className="mv2-ps-details" aria-label="Dettagli conversazione">
          <h3>Dettagli</h3>
          <div className="mv2-ps-details-block">
            <h4>Informazioni</h4>
            <dl className="mv2-ps-dl">
              {details.map(([label, value]) => (
                <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
              ))}
            </dl>
          </div>
          <div className="mv2-ps-details-block">
            <h4>Azioni rapide</h4>
            <select className="mv2-ps-select" aria-label="Assegna a" defaultValue="luca">
              <option value="luca">Luca · Operatore</option>
              <option value="anna">Anna · Titolare</option>
            </select>
            <button className="mv2-ps-ghost" type="button">Prendi in carico</button>
          </div>
          <div className="mv2-ps-details-block">
            <h4>Note AI</h4>
            <p className="mv2-ps-muted">{conversation.note}</p>
          </div>
        </aside>
      </div>
    </div>
  );
}

const BOOKING_HOURS = [
  { time: '08:00' },
  { time: '10:00' },
  { time: '12:00', booking: 'Andrea Romano' },
  { time: '14:00', booking: 'Marta Greco' },
  { time: '16:00' },
  { time: '18:00', booking: 'Paolo Ricci' },
  { time: '20:00', booking: 'Sofia Leone' },
  { time: '22:00' },
];

const BOOKING_COLUMNS = ['Orario', 'Cliente', 'Persone', 'Servizio', 'Stato'];

const BOOKING_ROWS = [
  { time: '12:30', client: 'Andrea Romano', people: '2', service: 'Pranzo', status: 'confermata' },
  { time: '14:00', client: 'Marta Greco', people: '2', service: 'Pranzo', status: 'confermata' },
  { time: '18:45', client: 'Paolo Ricci', people: '2', service: 'Aperitivo', status: 'in attesa' },
  { time: '20:00', client: 'Sofia Leone', people: '3', service: 'Cena', status: 'confermata' },
];

const BOOKING_STATUS_CLASS = { confermata: 'mv2-ps-badge-ok', 'in attesa': 'mv2-ps-badge-wait', annullata: 'mv2-ps-badge-cancel' };

function Prenotazioni() {
  return (
    <div className="mv2-ps-view mv2-ps-bookings">
      <header className="mv2-ps-booking-head">
        <div className="mv2-ps-booking-heading">
          <div className="mv2-ps-booking-title-row">
            <h3>Prenotazioni</h3>
            <span className="mv2-ps-booking-pending"><strong>1</strong> <span>da confermare</span></span>
          </div>
          <p className="mv2-ps-booking-summary">4 prenotazioni · 9 coperti · 1 da confermare</p>
        </div>
        <div className="mv2-ps-booking-actions">
          <button className="mv2-ps-ghost" type="button"><span aria-hidden="true">⋯</span> Azioni</button>
          <button className="mv2-ps-primary" type="button">+ Nuova prenotazione</button>
        </div>
      </header>
      <div className="mv2-ps-booking-layout">
        <section className="mv2-ps-surface mv2-ps-booking-calendar">
          <div className="mv2-ps-booking-toolbar">
            <div className="mv2-ps-booking-nav" aria-label="Navigazione calendario">
              <button className="mv2-ps-booking-nav-btn" type="button" aria-label="Periodo precedente">
                <Icon name="chevron" width="16" height="16" className="mv2-ps-flip" />
              </button>
              <button className="mv2-ps-booking-nav-btn" type="button" aria-label="Periodo successivo">
                <Icon name="chevron" width="16" height="16" />
              </button>
              <span className="mv2-ps-booking-date"><Icon name="calendar" width="16" height="16" />1 ottobre 2026</span>
            </div>
            <div className="mv2-ps-booking-views" role="group" aria-label="Vista calendario">
              <button className="mv2-ps-booking-view mv2-is-active" type="button" aria-pressed="true">Giorno</button>
              <button className="mv2-ps-booking-view" type="button" aria-pressed="false">Settimana</button>
              <button className="mv2-ps-booking-view" type="button" aria-pressed="false">Mese</button>
            </div>
          </div>
          <div className="mv2-ps-booking-grid">
            <div className="mv2-ps-booking-days"><span>Gio 1</span></div>
            <div className="mv2-ps-booking-slots">
              {BOOKING_HOURS.map(hour => (
                <span className={`mv2-ps-booking-slot${hour.booking ? ' mv2-is-booked' : ''}`} key={hour.time}>
                  <i>{hour.time}</i>
                  {hour.booking && <span className="mv2-ps-slot-chip">{hour.booking}</span>}
                </span>
              ))}
            </div>
          </div>
        </section>
        <section className="mv2-ps-surface mv2-ps-booking-table">
          <header className="mv2-ps-card-header">
            <span className="mv2-ps-card-title">Prenotazioni del giorno</span>
            <span className="mv2-ps-count-pill">4</span>
          </header>
          <p className="mv2-ps-sub">Elenco cronologico dettagliato della data selezionata</p>
          <div className="mv2-ps-booking-table-wrap">
            <table className="mv2-ps-booking-table-el">
              <caption className="mv2-ps-sr-only">Prenotazioni del giorno nel pannello dimostrativo</caption>
              <thead>
                <tr>{BOOKING_COLUMNS.map(column => <th key={column} scope="col">{column}</th>)}</tr>
              </thead>
              <tbody>
                {BOOKING_ROWS.map(row => (
                  <tr key={row.time}>
                    <td>{row.time}</td>
                    <td>{row.client}</td>
                    <td>{row.people}</td>
                    <td>{row.service}</td>
                    <td><span className={`mv2-ps-badge ${BOOKING_STATUS_CLASS[row.status]}`}>{row.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

const REVIEW_TABS = [['Tutte', 23], ['Da approvare', 4], ['Approvate', 18], ['Richiede attenzione', 1]];

const REVIEW_STATUS_LABEL = { bozza: 'Bozza', approvata: 'Approvata', inviata: 'Inviata' };

const REVIEWS = [
  {
    author: 'Chiara De Luca', initials: 'CD', channel: 'Google', stars: 5, when: '2 giorni fa', status: 'inviata',
    text: 'Servizio impeccabile: l’assistente ha risposto subito e la prenotazione è arrivata in un attimo.',
    draft: 'Grazie di cuore, Chiara! Siamo felici che la prenotazione sia stata rapida e semplice. Ti aspettiamo presto.',
  },
  {
    author: 'Alessandro Rinaldi', initials: 'AR', channel: 'Meta', stars: 4, when: '5 giorni fa', status: 'approvata',
    text: 'Ottima esperienza e personale gentilissimo. Unico neo, un po’ di attesa prima di sedersi.',
    draft: 'Grazie Alessandro! Ci scusiamo per l’attesa: stiamo lavorando per rendere il servizio ancora più rapido. A presto!',
  },
  {
    author: 'Federica Neri', initials: 'FN', channel: 'Google', stars: 2, when: '1 settimana fa', status: 'bozza',
    text: 'Non ho ricevuto subito la conferma su WhatsApp e ho dovuto richiamare.',
    draft: 'Ciao Federica, ci dispiace per il disservizio. Abbiamo verificato le conferme automatiche e migliorato le notifiche. Grazie per il feedback.',
  },
];

const THEMES = [['Qualità del servizio', '92%'], ['Rapidità di risposta', '88%'], ['Atmosfera', '81%']];

function Recensioni() {
  return (
    <div className="mv2-ps-view mv2-ps-reviews">
      <div className="mv2-ps-reviews-grid">
        <div className="mv2-ps-reviews-main">
          <div className="mv2-ps-reviews-toolbar">
            <span className="mv2-ps-stat-pill">23 recensioni</span>
            <span className="mv2-ps-stat-pill mv2-ps-stat-stars"><span className="mv2-ps-star-fill"><Icon name="star" width="15" height="15" /></span>4.6</span>
            <button className="mv2-ps-icon-btn" type="button" aria-label="Aggiorna recensioni"><Icon name="refresh" width="17" height="17" /></button>
          </div>
          <section className="mv2-ps-surface mv2-ps-analyze">
            <h3>Analizza nuova recensione</h3>
            <p className="mv2-ps-sub">Incolla il testo per estrarre sentiment e generare la bozza di risposta con l&rsquo;AI</p>
            <textarea className="mv2-ps-textarea" placeholder="Incolla qui il testo della recensione ricevuta..." aria-label="Testo della recensione" />
            <div className="mv2-ps-field-row">
              <label className="mv2-ps-field">
                <span><Icon name="user" width="14" height="14" />Autore:</span>
                <input type="text" placeholder="Nome cliente (opzionale)" aria-label="Autore" />
              </label>
              <label className="mv2-ps-field">
                <span><Icon name="star" width="14" height="14" />Valutazione:</span>
                <select aria-label="Valutazione" defaultValue="none">
                  <option value="none">Selezione valutazione…</option>
                  <option value="5">5 stelle</option>
                  <option value="4">4 stelle</option>
                  <option value="3">3 stelle</option>
                </select>
              </label>
              <label className="mv2-ps-field">
                <span><Icon name="globe" width="14" height="14" />Canale:</span>
                <select aria-label="Canale" defaultValue="manuale">
                  <option value="manuale">Manuale / Diretto</option>
                  <option value="google">Google</option>
                  <option value="instagram">Instagram</option>
                </select>
              </label>
              <button className="mv2-ps-primary mv2-ps-analyze-btn" type="button"><Icon name="spark" width="16" height="16" />Analizza e genera bozza</button>
            </div>
          </section>
          <section className="mv2-ps-surface mv2-ps-history">
            <header className="mv2-ps-history-head">
              <h3>Storico Recensioni</h3>
              <p className="mv2-ps-sub">Consulta tutte le recensioni analizzate, lo stato delle risposte e filtra per sentiment</p>
            </header>
            <div className="mv2-ps-tabs" role="group" aria-label="Filtri recensioni">
              {REVIEW_TABS.map(([label, count], index) => (
                <button key={label} className={`mv2-ps-tab${index === 0 ? ' mv2-is-active' : ''}`} type="button">{label} <i>{count}</i></button>
              ))}
            </div>
            <div className="mv2-ps-history-filters">
              <span className="mv2-ps-search">
                <Icon name="search" width="15" height="15" />
                <input type="search" placeholder="Cerca per autore o testo..." aria-label="Cerca recensione" />
              </span>
              <select className="mv2-ps-select" aria-label="Filtra per canale" defaultValue="all">
                <option value="all">Tutti i canali</option>
                <option value="google">Google</option>
                <option value="meta">Meta</option>
              </select>
            </div>
            <div className="mv2-ps-review-list">
              {REVIEWS.map(review => (
                <article className="mv2-ps-review" key={review.author}>
                  <header className="mv2-ps-review-head">
                    <span className="mv2-ps-avatar">{review.initials}</span>
                    <span className="mv2-ps-review-who">
                      <strong>{review.author}</strong>
                      <small>{review.channel} · {review.when}</small>
                    </span>
                    <span className="mv2-ps-review-stars" aria-label={`${review.stars} stelle su 5`}>
                      {[0, 1, 2, 3, 4].map(index => (
                        <span key={index} className={index < review.stars ? 'mv2-ps-star-on' : undefined}><Icon name="star" width="14" height="14" /></span>
                      ))}
                    </span>
                  </header>
                  <p>{review.text}</p>
                  <div className="mv2-ps-draft">
                    <div className="mv2-ps-draft-head">
                      <span className="mv2-ps-draft-label"><Icon name="spark" width="13" height="13" />Bozza di risposta AI</span>
                      <span className={`mv2-ps-draft-status mv2-ps-draft-status-${review.status}`}>{REVIEW_STATUS_LABEL[review.status]}</span>
                    </div>
                    <p>{review.draft}</p>
                  </div>
                </article>
              ))}
            </div>
          </section>
        </div>
        <div className="mv2-ps-reviews-side">
          <section className="mv2-ps-surface mv2-ps-sentiment">
            <h3>Sentiment &amp; Valutazione</h3>
            <div className="mv2-ps-sentiment-score">4.6</div>
            <div className="mv2-ps-stars" aria-label="Valutazione media 4,6 su 5">
              {[0, 1, 2, 3, 4].map(index => <span key={index}><Icon name="star" width="18" height="18" /></span>)}
            </div>
            <p className="mv2-ps-muted mv2-ps-sentiment-note">Basato su 23 recensioni</p>
            <ul className="mv2-ps-sentiment-list">
              <li><span><span className="mv2-ps-dot mv2-ps-dot-positive" />Positive</span><strong>78%</strong></li>
              <li><span><span className="mv2-ps-dot mv2-ps-dot-neutral" />Neutre</span><strong>17%</strong></li>
              <li><span><span className="mv2-ps-dot mv2-ps-dot-red" />Negative</span><strong>5%</strong></li>
            </ul>
          </section>
          <section className="mv2-ps-surface mv2-ps-trends">
            <h3>Top Trends &amp; Temi</h3>
            <p className="mv2-ps-sub">Temi più ricorrenti nelle recensioni recenti</p>
            <ul className="mv2-ps-sentiment-list">
              {THEMES.map(([label, value]) => (
                <li key={label}><span>{label}</span><strong>{value}</strong></li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}

const SUGGESTIONS = [
  'Siete aperti domenica sera?',
  'Avete un tavolo per 4 persone domani?',
  "Ho un'allergia seria al glutine, cosa posso mangiare?",
  'Vorrei prenotare per 15 persone, è un compleanno',
];

const SIM_GREETING = 'Ciao! Sono qui per aiutarti a gestire le comunicazioni. Come posso esserti utile oggi?';

const SIM_REPLIES = [
  [/domenica/i, 'Sì, la domenica sera siamo aperti dalle 19:00 alle 23:30. Vuoi che ti riservi un tavolo?'],
  [/tavolo|persone|prenot/i, 'Certamente! Dimmi data, orario e numero di persone e confermo subito la prenotazione.'],
  [/glutine|allergi/i, 'Segnalo subito l’allergia in cucina: prepariamo piatti senza glutine dedicati. Ti confermo le opzioni disponibili.'],
  [/compleanno|15 persone|gruppo/i, 'Per i gruppi oltre 10 persone preparo una proposta dedicata. Ti mando le opzioni per il compleanno.'],
];

const SIM_FALLBACK = 'Grazie! Ho registrato la tua richiesta e ti rispondo con tutti i dettagli tra un istante.';

const SIM_TONES = ['professionale', 'amichevole', 'sintetico'];

function simReply(text) {
  const match = SIM_REPLIES.find(([pattern]) => pattern.test(text));
  return match ? match[1] : SIM_FALLBACK;
}

function Simulatore({ hydrated }) {
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState([]);
  const [tone, setTone] = useState('professionale');
  const [escalation, setEscalation] = useState(false);

  const send = () => {
    const text = draft.trim();
    if (!text) return;
    setMessages(current => [
      ...current,
      { role: 'user', text },
      { role: 'assistant', text: simReply(text) },
    ]);
    setDraft('');
  };

  return (
    <div className="mv2-ps-view mv2-ps-simulator">
      <p className="mv2-ps-sim-sub">Simula una conversazione come farebbe un cliente reale per verificare le risposte e il comportamento del tuo assistente AI.</p>
      <div className="mv2-ps-sim-toolbar" role="group" aria-label="Impostazioni simulazione">
        {SIM_TONES.map(item => (
          <button
            key={item}
            className={`mv2-ps-chip${tone === item ? ' mv2-is-active' : ''}`}
            type="button"
            disabled={!hydrated}
            aria-pressed={tone === item}
            onClick={() => setTone(item)}
          >
            Tono: {item}
          </button>
        ))}
        <button
          className={`mv2-ps-chip${escalation ? ' mv2-is-active' : ''}`}
          type="button"
          disabled={!hydrated}
          aria-pressed={escalation}
          onClick={() => setEscalation(value => !value)}
        >
          <Icon name="user" width="13" height="13" />Escalation automatica {escalation ? 'attiva' : 'disattiva'}
        </button>
      </div>
      <div className="mv2-ps-chat-card">
        <header className="mv2-ps-sim-head">
          <span className="mv2-ps-avatar mv2-ps-avatar-square">A</span>
          <span className="mv2-ps-sim-id">
            <strong>La tua attività</strong>
            <small className="mv2-ps-online"><span />online</small>
          </span>
        </header>
        <div className="mv2-ps-sim-body">
          <span className="mv2-ps-day-pill">Oggi</span>
          <p className="mv2-ps-sim-instructions"><strong>Istruzioni per te:</strong> interagisci con l&rsquo;assistente AI come farebbe un cliente. L&rsquo;assistente imparerà dalle risposte e migliorerà nel tempo.</p>
          <div className="mv2-ps-transcript" aria-live="polite">
            <div className="mv2-ps-bubble">{SIM_GREETING}</div>
            {messages.map((message, index) => (
              <div className={`mv2-ps-bubble${message.role === 'user' ? ' mv2-ps-bubble-user' : ''}`} key={index}>{message.text}</div>
            ))}
            {escalation && <p className="mv2-ps-sim-note">Escalation automatica attiva: le richieste fuori ambito verranno girate a un operatore.</p>}
            {messages.length > 0 && !escalation && <p className="mv2-ps-sim-note">Anteprima: le risposte vengono generate dall&rsquo;assistente nel pannello, non in questa pagina.</p>}
          </div>
        </div>
        <div className="mv2-ps-chips">
          {SUGGESTIONS.map(suggestion => (
            <button key={suggestion} className="mv2-ps-chip mv2-ps-chip-suggestion" type="button" disabled={!hydrated} onClick={() => setDraft(suggestion)}>{suggestion}</button>
          ))}
        </div>
        <div className="mv2-ps-sim-composer">
          <input
            className="mv2-ps-sim-input"
            type="text"
            value={draft}
            placeholder="Scrivi un messaggio…"
            aria-label="Scrivi un messaggio"
            disabled={!hydrated}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') send(); }}
          />
          <button className="mv2-ps-sim-send" type="button" aria-label="Invia messaggio" disabled={!hydrated || !draft.trim()} onClick={send}>
            <Icon name="send" width="18" height="18" />
          </button>
        </div>
      </div>
    </div>
  );
}

const VIEWS = { panoramica: Panoramica, inbox: Inbox, prenotazioni: Prenotazioni, recensioni: Recensioni, simulatore: Simulatore };

export default function ProductShowcase() {
  const [active, setActive] = useState('panoramica');
  const [hydrated, setHydrated] = useState(false);
  const frame = useRef(null);
  useEffect(() => setHydrated(true), []);

  /* The guided tour outside the island asks for a view with `mv2:view`; the island answers any
     user-initiated change with `mv2:user-interaction`, which hands the story over to the visitor. */
  useEffect(() => {
    const element = frame.current;
    if (!element) return undefined;
    const onView = event => {
      const next = event?.detail?.view;
      if (typeof next === 'string' && VIEWS[next]) setActive(next);
    };
    element.addEventListener('mv2:view', onView);
    return () => element.removeEventListener('mv2:view', onView);
  }, []);

  const selectView = (id, fromUser = false) => {
    if (!VIEWS[id]) return;
    setActive(id);
    if (fromUser) frame.current?.dispatchEvent(new CustomEvent('mv2:user-interaction', { bubbles: true }));
  };

  const activeEntry = NAV.find(entry => entry.id === active) ?? NAV[0];
  const ActiveView = VIEWS[activeEntry.id] ?? Panoramica;
  const topbarHidden = activeEntry.id === 'inbox';

  return (
    <section className="mv2-ps-frame" ref={frame} aria-label="Anteprima dimostrativa del pannello Melpis">
      <aside className="mv2-ps-sidebar" aria-label="Sezioni del pannello">
        <div className="mv2-ps-side-body">
          <div className="mv2-ps-nav-list" role="group" aria-label="Sezioni del pannello">
            <span className="mv2-ps-side-label">Principale</span>
            {NAV.map(entry => entry.group === 'main' && (
              <button
                key={entry.id}
                className={`mv2-ps-nav-button${active === entry.id ? ' mv2-is-active' : ''}`}
                type="button"
                disabled={!hydrated || !entry.view}
                aria-pressed={active === entry.id}
                aria-disabled={entry.view ? undefined : 'true'}
                onClick={() => selectView(entry.id, true)}
              >
                <span className="mv2-ps-nav-icon"><Icon name={entry.icon} /></span>
                <span>{entry.label}</span>
                {entry.badge && <small className="mv2-ps-nav-badge">{entry.badge}</small>}
              </button>
            ))}
            <span className="mv2-ps-side-label">Assistente</span>
            {NAV.map(entry => entry.group === 'assistant' && (
              <button
                key={entry.id}
                className={`mv2-ps-nav-button${active === entry.id ? ' mv2-is-active' : ''}`}
                type="button"
                disabled={!hydrated || !entry.view}
                aria-pressed={active === entry.id}
                aria-disabled={entry.view ? undefined : 'true'}
                onClick={() => selectView(entry.id, true)}
              >
                <span className="mv2-ps-nav-icon"><Icon name={entry.icon} /></span>
                <span>{entry.label}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="mv2-ps-side-user">
          <span className="mv2-ps-user-avatar" aria-hidden="true">F</span>
          <span className="mv2-ps-user-copy">
            <strong>attività.demo@melpis.it</strong>
            <small>Ruolo: Proprietario</small>
          </span>
          <Icon name="chevronDown" width="18" height="18" className="mv2-ps-user-chevron" />
        </div>
      </aside>
      <div className="mv2-ps-workspace">
        {!topbarHidden && (
          <header className="mv2-ps-topbar">
            <strong className="mv2-ps-topbar-title">{activeEntry.title}</strong>
            <span className="mv2-ps-topbar-search">
              <Icon name="search" width="16" height="16" />
              <input type="search" placeholder="Cerca ticket, prenotazioni, documenti…" aria-label="Ricerca globale" />
            </span>
            <span className="mv2-ps-topbar-meta">
              <span className="mv2-ps-topbar-date"><Icon name="calendar" width="18" height="18" /><span><strong>1 ottobre 2026</strong><small>Giovedì</small></span></span>
              <span className="mv2-ps-topbar-bell"><Icon name="bell" width="19" height="19" /><i>5</i></span>
            </span>
          </header>
        )}
        <p className="mv2-ps-sr-only" aria-live="polite" aria-atomic="true">Sezione selezionata: {activeEntry.title}</p>
        <p className="mv2-ps-sr-only">Anteprima dimostrativa a scopo illustrativo.</p>
        <div className="mv2-ps-content" key={active} role="group" aria-label="Contenuto della sezione" tabIndex={0}>
          <ActiveView hydrated={hydrated} onSelect={selectView} />
          <noscript><p className="mv2-ps-noscript">Questa anteprima è consultabile senza JavaScript; la navigazione tra sezioni richiede JavaScript.</p></noscript>
        </div>
      </div>
    </section>
  );
}
