import { useEffect, useState } from 'react';

const views = [
  ['overview', 'Panoramica'],
  ['inbox', 'Inbox'],
  ['bookings', 'Prenotazioni'],
  ['reviews', 'Recensioni'],
  ['knowledge', 'Knowledge Base'],
  ['team', 'Team'],
];

const conversations = [
  { name: 'Giulia R.', subject: 'Tavolo per due stasera', time: '10:42', initials: 'GR', text: 'Certo, per che ora preferisci?', reply: 'Avete disponibilità per due persone stasera verso le 20?' },
  { name: 'Marco L.', subject: 'Orari di apertura', time: '10:18', initials: 'ML', text: 'Siamo aperti fino alle 23:00.', reply: 'Ciao, oggi siete aperti nel pomeriggio?' },
  { name: 'Sara B.', subject: 'Informazioni sul menu', time: '09:56', initials: 'SB', text: 'Il team può aiutarti con questa richiesta.', reply: 'Avete opzioni senza glutine?' },
];

const knowledgeItems = [
  { title: 'Orari e contatti', group: 'Informazioni attività', body: 'Lun–Ven, 09:00–23:00. Sabato, 09:00–13:00. Domenica chiuso. Per richieste specifiche, il team è a disposizione.' },
  { title: 'Politica di cancellazione', group: 'FAQ', body: 'Le prenotazioni possono essere modificate o annullate contattando l’attività con almeno 24 ore di anticipo.' },
  { title: 'Menu della settimana', group: 'Documenti', body: 'Una selezione stagionale aggiornata dal team. Per allergeni o ingredienti specifici, chiedi conferma al personale.' },
];

function Icon({ name }) {
  const paths = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    inbox: <><path d="M4 5h16v14H4z"/><path d="M4 14h4l2 2h4l2-2h4"/></>,
    bookings: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18M8 14h3M8 17h6"/></>,
    reviews: <><path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9L12 3Z"/></>,
    knowledge: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21V5.5Z"/><path d="M4 17a2 2 0 0 1 2-2h14M8 7h8M8 10h7"/></>,
    team: <><circle cx="9" cy="8" r="3"/><path d="M3.5 20v-1.5a5.5 5.5 0 0 1 11 0V20zM16 5.5a3 3 0 0 1 0 5.8M17 14a4.5 4.5 0 0 1 3.5 4.4V20h-3"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function Overview() {
  return <div className="mv2-ps-view mv2-ps-overview">
    <div className="mv2-ps-view-heading"><div><p className="mv2-ps-overline">LUNEDÌ 12 OTTOBRE 2026</p><h3>Panoramica</h3><p>Un riepilogo dell’attività, oggi.</p></div><span className="mv2-ps-local-pill"><i /> Locale demo</span></div>
    <div className="mv2-ps-metrics">
      <article className="mv2-ps-metric"><span>Messaggi ricevuti</span><strong>24</strong><small>Oggi · esempio</small><span className="mv2-ps-spark" aria-hidden="true"><i/><i/><i/><i/><i/><i/><i/><i/></span></article>
      <article className="mv2-ps-metric"><span>Gestiti assistente</span><strong>17</strong><small>Oggi · esempio</small><span className="mv2-ps-spark mv2-ps-spark-mid" aria-hidden="true"><i/><i/><i/><i/><i/><i/><i/><i/></span></article>
      <article className="mv2-ps-metric"><span>Girati a umano</span><strong>7</strong><small>Oggi · esempio</small><span className="mv2-ps-spark mv2-ps-spark-low" aria-hidden="true"><i/><i/><i/><i/><i/><i/><i/><i/></span></article>
    </div>
    <div className="mv2-ps-overview-grid">
      <section className="mv2-ps-surface mv2-ps-attention"><div className="mv2-ps-section-title"><div><h4>Da seguire</h4><p>Richieste che possono coinvolgere il team</p></div><span>02</span></div>
        <article className="mv2-ps-task"><span className="mv2-ps-task-icon">↗</span><div><strong>Richiesta speciale</strong><p>Giulia R. · Inbox</p></div><span className="mv2-ps-task-time">10:42</span></article>
        <article className="mv2-ps-task"><span className="mv2-ps-task-icon">↗</span><div><strong>Conferma informazione</strong><p>Sara B. · Inbox</p></div><span className="mv2-ps-task-time">09:56</span></article>
      </section>
      <section className="mv2-ps-surface mv2-ps-activity"><div className="mv2-ps-section-title"><div><h4>Attività recente</h4><p>Movimenti illustrativi</p></div><span className="mv2-ps-more">•••</span></div>
        <article><span className="mv2-ps-activity-mark">↗</span><p><strong>Assistente</strong> ha risposto a Marco L.<small>Orari di apertura · 10:18</small></p></article>
        <article><span className="mv2-ps-activity-mark">◷</span><p><strong>Nuova prenotazione</strong> aggiunta alla giornata<small>Oggi, ore 20:00 · 09:42</small></p></article>
      </section>
    </div>
  </div>;
}

function Inbox({ hydrated }) {
  const [selected, setSelected] = useState(0);
  const conversation = conversations[selected];
  return <div className="mv2-ps-view mv2-ps-inbox-view">
    <div className="mv2-ps-view-heading"><div><p className="mv2-ps-overline">MESSAGGI</p><h3>Inbox</h3><p>Conversazioni dimostrative, in un unico posto.</p></div><span className="mv2-ps-local-pill"><i /> Locale demo</span></div>
    <div className="mv2-ps-inbox-layout">
      <div className="mv2-ps-conversation-list" role="group" aria-label="Seleziona una conversazione">{conversations.map((item, index) => <button key={item.initials} className={`mv2-ps-conversation${selected === index ? ' mv2-is-selected' : ''}`} type="button" aria-pressed={selected === index} disabled={!hydrated} onClick={() => setSelected(index)}><span className="mv2-ps-person-avatar">{item.initials}</span><span className="mv2-ps-conversation-copy"><strong>{item.name}</strong><span>{item.subject}</span><small>{item.text}</small></span><time>{item.time}</time></button>)}</div>
      <section className="mv2-ps-chat" aria-label={`Conversazione con ${conversation.name}`}><header className="mv2-ps-chat-head"><span className="mv2-ps-person-avatar">{conversation.initials}</span><span><strong>{conversation.name}</strong><small>Conversazione dimostrativa</small></span><span className="mv2-ps-channel-label">Messaggio</span></header><div className="mv2-ps-chat-body"><p className="mv2-ps-day-label">OGGI</p><div className="mv2-ps-message mv2-ps-message-in">{conversation.reply}<time>{conversation.time}</time></div><div className="mv2-ps-message mv2-ps-message-out">{conversation.text}<time>{conversation.time}</time></div></div><footer className="mv2-ps-handoff"><span className="mv2-ps-handoff-avatar">T</span><span><strong>Il team può prendere in carico</strong><small>Indicatore di gestione · esempio</small></span><span className="mv2-ps-handoff-state">Team</span></footer></section>
    </div>
  </div>;
}

function Bookings({ hydrated }) {
  const [selected, setSelected] = useState(1);
  const rows = [
    { time: '18:30', name: 'Luca M.', detail: '2 persone · Tavolo interno', initials: 'LM' },
    { time: '20:00', name: 'Elena P.', detail: '4 persone · Tavolo esterno', initials: 'EP' },
    { time: '20:30', name: 'Davide C.', detail: '2 persone · Tavolo interno', initials: 'DC' },
  ];
  const booking = rows[selected];
  return <div className="mv2-ps-view"><div className="mv2-ps-view-heading"><div><p className="mv2-ps-overline">AGENDA</p><h3>Prenotazioni</h3><p>Una giornata di appuntamenti dimostrativi.</p></div><span className="mv2-ps-local-pill"><i /> Locale demo</span></div>
    <div className="mv2-ps-bookings-layout"><section className="mv2-ps-surface mv2-ps-day-list"><div className="mv2-ps-day-header"><span><small>LUNEDÌ</small><strong>12 <em>ottobre</em></strong></span><span className="mv2-ps-day-count">3 prenotazioni</span></div>{rows.map((row, index) => <button type="button" key={row.initials} className={`mv2-ps-booking-row${selected === index ? ' mv2-is-selected' : ''}`} aria-pressed={selected === index} disabled={!hydrated} onClick={() => setSelected(index)}><time>{row.time}</time><span className="mv2-ps-booking-line"/><span className="mv2-ps-person-avatar">{row.initials}</span><span className="mv2-ps-booking-copy"><strong>{row.name}</strong><small>{row.detail}</small></span><span aria-hidden="true">›</span></button>)}</section>
      <section className="mv2-ps-surface mv2-ps-booking-detail"><p className="mv2-ps-overline">DETTAGLIO · ESEMPIO</p><span className="mv2-ps-detail-avatar">{booking.initials}</span><h4>{booking.name}</h4><p className="mv2-ps-detail-sub">Prenotazione dimostrativa</p><dl><div><dt>Orario</dt><dd>{booking.time}</dd></div><div><dt>Persone</dt><dd>{booking.detail.split(' ')[0]}</dd></div><div><dt>Disponibilità</dt><dd><span className="mv2-ps-neutral-dot"/> Indicata</dd></div><div><dt>Gestione</dt><dd>Team Melpis</dd></div></dl><p className="mv2-ps-detail-note">Anteprima locale: nessuna prenotazione viene creata o modificata.</p></section>
    </div>
  </div>;
}

function Reviews() {
  return <div className="mv2-ps-view"><div className="mv2-ps-view-heading"><div><p className="mv2-ps-overline">REPUTAZIONE</p><h3>Recensioni</h3><p>Bozze di risposta e recensioni dimostrative.</p></div><span className="mv2-ps-local-pill"><i /> Locale demo</span></div>
    <div className="mv2-ps-review-list"><article className="mv2-ps-surface mv2-ps-review"><header><span className="mv2-ps-person-avatar">AC</span><div><strong>Andrea C.</strong><small>Recensione dimostrativa · 2 giorni fa</small></div><span className="mv2-ps-stars" aria-label="5 stelle">★★★★★</span></header><p>“Accoglienza molto cordiale e servizio attento. Torneremo volentieri.”</p><div className="mv2-ps-draft"><div><span className="mv2-ps-draft-icon">✦</span><strong>Bozza di risposta</strong><span className="mv2-ps-draft-status">Non pubblicata</span></div><p>Grazie per aver condiviso la tua esperienza. Ci fa piacere che l’accoglienza sia stata positiva: speriamo di rivedervi presto!</p></div></article>
      <article className="mv2-ps-surface mv2-ps-review"><header><span className="mv2-ps-person-avatar">MF</span><div><strong>Martina F.</strong><small>Recensione dimostrativa · 5 giorni fa</small></div><span className="mv2-ps-stars" aria-label="4 stelle">★★★★☆</span></header><p>“Ambiente curato e piatti interessanti. Tempi un po’ lunghi.”</p><div className="mv2-ps-published-note"><span className="mv2-ps-neutral-dot"/> Stato pubblicata · simulazione</div></article>
    </div>
  </div>;
}

function Knowledge({ hydrated }) {
  const [selected, setSelected] = useState(0);
  const item = knowledgeItems[selected];
  return <div className="mv2-ps-view"><div className="mv2-ps-view-heading"><div><p className="mv2-ps-overline">CONTENUTI</p><h3>Knowledge Base</h3><p>Le informazioni a cui l’assistente può fare riferimento.</p></div><span className="mv2-ps-local-pill"><i /> Locale demo</span></div>
    <div className="mv2-ps-knowledge-layout"><nav className="mv2-ps-knowledge-list" aria-label="Voci della Knowledge Base">{knowledgeItems.map((entry, index) => <button type="button" key={entry.title} disabled={!hydrated} aria-pressed={selected === index} className={`mv2-ps-knowledge-item${selected === index ? ' mv2-is-selected' : ''}`} onClick={() => setSelected(index)}><span className="mv2-ps-knowledge-glyph">{index === 2 ? '▤' : '≡'}</span><span><small>{entry.group}</small><strong>{entry.title}</strong></span><span aria-hidden="true">›</span></button>)}</nav><article className="mv2-ps-surface mv2-ps-knowledge-detail"><span className="mv2-ps-knowledge-category">{item.group}</span><h4>{item.title}</h4><p>{item.body}</p><div className="mv2-ps-content-source"><span className="mv2-ps-neutral-dot"/> Informazione dimostrativa <span>·</span> Aggiornata dal team</div></article></div>
  </div>;
}

function Team() {
  const members = [
    { name: 'Elena Rossi', role: 'Titolare', initials: 'ER', activity: 'Inbox · oggi 10:42' },
    { name: 'Paolo Bianchi', role: 'Staff', initials: 'PB', activity: 'Prenotazioni · oggi 09:58' },
    { name: 'Marta Conti', role: 'Staff', initials: 'MC', activity: 'Inbox · ieri 18:16' },
  ];
  return <div className="mv2-ps-view"><div className="mv2-ps-view-heading"><div><p className="mv2-ps-overline">PERSONE</p><h3>Team</h3><p>Persone e attività recenti nell’esempio.</p></div><span className="mv2-ps-local-pill"><i /> Locale demo</span></div>
    <div className="mv2-ps-team-intro mv2-ps-surface"><span className="mv2-ps-team-icon"><Icon name="team"/></span><span><strong>Il lavoro resta condiviso</strong><small>Il team può seguire conversazioni e attività dal pannello.</small></span><span className="mv2-ps-team-total">03 persone</span></div>
    <div className="mv2-ps-team-list">{members.map(member => <article className="mv2-ps-surface mv2-ps-team-member" key={member.initials}><span className="mv2-ps-person-avatar">{member.initials}</span><span className="mv2-ps-member-copy"><strong>{member.name}</strong><small>{member.activity}</small></span><span className="mv2-ps-role">{member.role}</span></article>)}</div>
  </div>;
}

const panels = { overview: Overview, inbox: Inbox, bookings: Bookings, reviews: Reviews, knowledge: Knowledge, team: Team };

export default function ProductShowcase() {
  const [active, setActive] = useState('overview');
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  useEffect(() => {
    let disposed = false;
    let media;
    import('gsap').then(({ gsap }) => {
      const hero = document.getElementById('mv2-inizio');
      if (disposed || !hero) return;
      media = gsap.matchMedia(hero);
      media.add('(prefers-reduced-motion: no-preference)', () => {
        gsap.fromTo('.mv2-hero-heading', { opacity: 0.96, y: 6 }, { opacity: 1, y: 0, duration: 0.62, ease: 'power2.out', clearProps: 'transform,opacity' });
        gsap.fromTo('.mv2-ps-frame', { opacity: 0.96, y: 6 }, { opacity: 1, y: 0, duration: 0.66, delay: 0.08, ease: 'power2.out', clearProps: 'transform,opacity' });
      });
    });
    return () => { disposed = true; media?.revert(); };
  }, []);
  const ActivePanel = panels[active];
  const activeTitle = views.find(([id]) => id === active)?.[1] ?? 'Panoramica';

  return <section className="mv2-ps-frame" aria-label="Anteprima interattiva del pannello Melpis">
    <aside className="mv2-ps-sidebar" aria-label="Sezioni dimostrative">
      <div className="mv2-ps-side-brand"><img src="/landing-v2/assets/mv2-symbol.svg" alt="" width="30" height="22"/><span>melpis</span><small>DEMO</small></div>
      <div className="mv2-ps-side-group"><span className="mv2-ps-side-label">PRINCIPALE</span><div className="mv2-ps-nav-list" role="group" aria-label="Sezioni del pannello">{views.map(([id, label]) => <button key={id} className={`mv2-ps-nav-button${active === id ? ' mv2-is-active' : ''}`} type="button" disabled={!hydrated} aria-pressed={active === id} onClick={() => setActive(id)}><Icon name={id}/><span>{label}</span>{id === 'inbox' && <small>3</small>}</button>)}</div></div>
      <div className="mv2-ps-side-bottom"><span className="mv2-ps-side-label">ASSISTENTE</span><div className="mv2-ps-assistant-row"><span className="mv2-ps-assistant-mark">✦</span><span><strong>Assistente AI</strong><small>Informazioni attività</small></span></div><div className="mv2-ps-side-user"><span className="mv2-ps-user-avatar">M</span><span><strong>Melpis demo</strong><small>Spazio dimostrativo</small></span><span className="mv2-ps-user-more">•••</span></div></div>
    </aside>
    <div className="mv2-ps-workspace">
      <header className="mv2-ps-topbar"><div className="mv2-ps-breadcrumb"><span>Workspace</span><span aria-hidden="true">/</span><strong>{activeTitle}</strong></div><div className="mv2-ps-topbar-meta"><span className="mv2-ps-topbar-local"><i/> Locale demo</span><span className="mv2-ps-topbar-avatar" aria-hidden="true">M</span></div></header>
      <div className="mv2-ps-mobile-nav"><label className="mv2-ps-sr-only" htmlFor="mv2-ps-view-select">Seleziona una sezione del pannello</label><select id="mv2-ps-view-select" value={active} disabled={!hydrated} onChange={event => setActive(event.target.value)}>{views.map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></div>
      <p className="mv2-ps-sr-only" aria-live="polite" aria-atomic="true">Sezione selezionata: {activeTitle}</p>
      <div className="mv2-ps-content" key={active}>
        <ActivePanel hydrated={hydrated} />
        <noscript><p className="mv2-ps-noscript">Questa anteprima è consultabile senza JavaScript; la navigazione tra sezioni richiede JavaScript.</p></noscript>
      </div>
    </div>
  </section>;
}
