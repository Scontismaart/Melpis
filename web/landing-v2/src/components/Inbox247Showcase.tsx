import React from 'react';

type Message = {
  from: 'customer' | 'ai';
  text: string;
};

type ThreadData = {
  initials: string;
  avatarClass: string;
  name: string;
  phone: string;
  channel: 'WhatsApp' | 'Instagram';
  sub: string;
  messages: Message[];
};

const THREADS: Record<string, ThreadData> = {
  giulia: {
    initials: 'GR',
    avatarClass: 'tilted-avatar-gr',
    name: 'Giulia Rossi',
    phone: '+39 340 123 4567',
    channel: 'WhatsApp',
    sub: "L'assistente sta gestendo la conversazione.",
    messages: [
      { from: 'customer', text: 'Buonasera, avete un tavolo per 4 persone venerdì sera?' },
      { from: 'ai', text: 'Buonasera Giulia! Certamente: per quante persone e a che ora preferisce?' },
      { from: 'customer', text: 'Siamo in 4, verso le 20:30 se possibile.' },
      { from: 'ai', text: 'Perfetto, ho segnato 4 persone venerdì alle 20:30. Confermo la prenotazione?' },
      { from: 'customer', text: 'Sì, confermate. Grazie mille!' },
    ],
  },
  marco: {
    initials: 'MB',
    avatarClass: 'tilted-avatar-mb',
    name: 'Marco Bianchi',
    phone: '+39 338 901 2345',
    channel: 'WhatsApp',
    sub: 'Escalation attiva · In attesa di un operatore di sala.',
    messages: [
      { from: 'customer', text: 'Vorrei parlare con una persona, ho una richiesta per una cena aziendale da 16 ospiti.' },
      { from: 'ai', text: 'Buonasera Marco! Per gruppi sopra gli 8 ospiti passo subito la conversazione a Luca, il nostro responsabile di sala.' },
    ],
  },
  elena: {
    initials: 'EM',
    avatarClass: 'tilted-avatar-em',
    name: 'Elena Moretti',
    phone: '@elena.moretti · Instagram',
    channel: 'Instagram',
    sub: "L'assistente sta gestendo la conversazione.",
    messages: [
      { from: 'customer', text: 'Ciao! Avete disponibilità per una cena sabato prossimo e piatti senza glutine?' },
      { from: 'ai', text: 'Ciao Elena! Sì, abbiamo disponibilità alle 19:45 o alle 21:30 e opzioni dedicate per celiaci nel nostro menù.' },
    ],
  },
  luca: {
    initials: 'LF',
    avatarClass: 'tilted-avatar-lf',
    name: 'Luca Ferri',
    phone: '+39 347 554 1122',
    channel: 'WhatsApp',
    sub: "L'assistente sta gestendo la conversazione.",
    messages: [
      { from: 'customer', text: 'Posso spostare la prenotazione di domani dalle 20:00 alle 21:00?' },
      { from: 'ai', text: 'Certamente Luca! Ho aggiornato il tuo tavolo per domani alle ore 21:00.' },
    ],
  },
  sara: {
    initials: 'SC',
    avatarClass: 'tilted-avatar-sc',
    name: 'Sara Conti',
    phone: '+39 320 887 6543',
    channel: 'WhatsApp',
    sub: 'Conversazione gestita da Luca · Operatore.',
    messages: [
      { from: 'customer', text: 'Grazie mille, gentilissimi come sempre! A venerdì.' },
      { from: 'ai', text: 'Grazie a te Sara, ti aspettiamo venerdì accanto alla finestra!' },
    ],
  },
};

export default function Inbox247Showcase({
  isActiveStage = true,
  onPointerEnter,
}: {
  isActiveStage?: boolean;
  onPointerEnter?: () => void;
}) {
  const currentThread = THREADS.giulia;

  return (
    <section
      className={`mv2-cap-tilted-feature left-section-item mv2-capabilities-conversation ${
        isActiveStage ? 'stage-active-card' : ''
      }`}
      data-mv2-cap-panel="chat"
      aria-labelledby="headlineContainer"
      onPointerEnter={onPointerEnter}
    >
      {/* 1. Blocco Tipografico Superiore (Fedele al prototipo) */}
      <header className="editorial-headline">
        <h2 id="headlineContainer">
          <span className="headline-muted">Rispondi ai clienti</span>
          <span className="headline-bright">24/7—in tempo reale,</span>
          <span className="headline-muted">senza perdere prenotazioni</span>
        </h2>
      </header>

      {/* 2. Mockup Prospettico Inclinato della Inbox di Melpis (Showcase Statico) */}
      <div className="perspective-viewport" aria-hidden="true">
        <div id="tiltedInbox" className="tilted-inbox-rig theme-dark">
          {/* Linee guida prolungate a sinistra come nel concept */}
          <div className="architectural-guide-top" aria-hidden="true" />
          <div className="architectural-guide-sub" aria-hidden="true" />

          {/* ================================================================
               PANNELLO SINISTRO: COLONNA INBOX MELPIS
             ================================================================ */}
          <div className="inbox-pane-left">
            {/* Titolo Inbox + Contatore 5 */}
            <div className="inbox-header-row">
              <div className="inbox-title-wrap">
                <span className="inbox-title-text">Inbox</span>
                <span className="inbox-count-badge">5</span>
              </div>
              <div className="inbox-header-actions">
                <span className="inbox-header-action-item">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" focusable="false">
                    <path d="M4 6h16M7 12h10M10 18h4" />
                  </svg>
                  <span>Tutti</span>
                </span>
                <span className="inbox-header-action-item">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" focusable="false">
                    <path d="M7 15l5 5 5-5M7 9l5-5 5 5" />
                  </svg>
                  <span>Recenti</span>
                </span>
              </div>
            </div>

            {/* Barra Filtri Canali e Stato */}
            <div className="inbox-filters-wrap">
              <div className="inbox-filters-row">
                <span className="filter-pill active">
                  Tutti
                </span>
                <span className="filter-pill">
                  <span className="filter-pill-dot dot-green" aria-hidden="true" />
                  <span>WhatsApp 4</span>
                </span>
                <span className="filter-pill">
                  <span>Instagram 1</span>
                </span>
              </div>

              <div className="inbox-filters-row">
                <span className="filter-pill">🤖 AI</span>
                <span className="filter-pill">👤 Operatore</span>
                <span className="filter-pill">
                  <span className="filter-pill-dot dot-rose" aria-hidden="true" />
                  <span>Escalated 6</span>
                </span>
                <span className="filter-pill">✓ Risolte</span>
              </div>
            </div>

            {/* Barra Cerca */}
            <div className="inbox-search-bar" aria-hidden="true">
              <span className="inbox-search-left">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" focusable="false">
                  <circle cx="11" cy="11" r="8" />
                  <path d="M21 21l-4.35-4.35" />
                </svg>
                <span>Cerca conversazione...</span>
              </span>
              <span className="inbox-new-badge">1 Nuova ↓</span>
            </div>

            {/* LISTA DELLE 5 CONVERSAZIONI (Statiche - Illustrative) */}
            <div className="inbox-threads-list">
              {/* 1. Giulia Rossi */}
              <div className="conv-row is-active">
                <div className="conv-row-top">
                  <div className="conv-row-user">
                    <span className="conv-avatar tilted-avatar-gr" aria-hidden="true">GR</span>
                    <span className="conv-name">Giulia Rossi</span>
                  </div>
                  <div className="conv-badge-wrap">
                    <span className="conv-tag-badge conv-tag-wa">WhatsApp · AI</span>
                    <span className="signal-bars" aria-hidden="true">
                      <span className="bar-green-1" />
                      <span className="bar-green-2" />
                      <span className="bar-dim-3" />
                    </span>
                  </div>
                </div>
                <div className="conv-row-bottom">
                  <p className="conv-snippet">Vorrei prenotare un tavolo per 4 per...</p>
                  <span className="conv-time">01:16</span>
                </div>
              </div>

              {/* 2. Marco Bianchi */}
              <div className="conv-row">
                <div className="conv-row-top">
                  <div className="conv-row-user">
                    <span className="conv-avatar tilted-avatar-mb" aria-hidden="true">MB</span>
                    <span className="conv-name">Marco Bianchi</span>
                  </div>
                  <div className="conv-badge-wrap">
                    <span className="filter-pill-dot dot-rose" aria-hidden="true" />
                    <span className="conv-tag-escalation">Escalation</span>
                    <span className="conv-time">00:48</span>
                  </div>
                </div>
                <div className="conv-row-bottom">
                  <p className="conv-snippet">Vorrei parlare con una persona, ho ...</p>
                </div>
              </div>

              {/* 3. Elena Moretti */}
              <div className="conv-row">
                <div className="conv-row-top">
                  <div className="conv-row-user">
                    <span className="conv-avatar tilted-avatar-em" aria-hidden="true">EM</span>
                    <span className="conv-name">Elena Moretti</span>
                  </div>
                  <div className="conv-badge-wrap">
                    <span className="conv-tag-simple">Instagram · AI</span>
                    <span className="signal-bars" aria-hidden="true">
                      <span className="bar-white-1" />
                      <span className="bar-white-2" />
                      <span className="bar-dim-white-3" />
                    </span>
                    <span className="conv-time">23:37</span>
                  </div>
                </div>
                <div className="conv-row-bottom">
                  <p className="conv-snippet">Ciao! Avete disponibilità per una co...</p>
                </div>
              </div>

              {/* 4. Luca Ferri */}
              <div className="conv-row">
                <div className="conv-row-top">
                  <div className="conv-row-user">
                    <span className="conv-avatar tilted-avatar-lf" aria-hidden="true">LF</span>
                    <span className="conv-name">Luca Ferri</span>
                  </div>
                  <div className="conv-badge-wrap">
                    <span className="conv-tag-simple">WhatsApp · AI</span>
                    <span className="conv-time">03:24</span>
                  </div>
                </div>
                <div className="conv-row-bottom">
                  <p className="conv-snippet">Posso spostare la prenotazione di d...</p>
                </div>
              </div>

              {/* 5. Sara Conti */}
              <div className="conv-row">
                <div className="conv-row-top">
                  <div className="conv-row-user">
                    <span className="conv-avatar tilted-avatar-sc" aria-hidden="true">SC</span>
                    <span className="conv-name">Sara Conti</span>
                  </div>
                  <div className="conv-badge-wrap">
                    <span className="conv-tag-simple">Operatore</span>
                    <span className="conv-time">06:12</span>
                  </div>
                </div>
                <div className="conv-row-bottom">
                  <p className="conv-snippet">Grazie mille, gentilissimi come semp...</p>
                </div>
              </div>
            </div>
          </div>

          {/* ================================================================
               PANNELLO DESTRO: CHAT CONVERSAZIONE (Preview che sfuma)
             ================================================================ */}
          <div className="inbox-pane-right" aria-label={`Messaggi con ${currentThread.name}`}>
            {/* Header Conversazione Attiva */}
            <div className="chat-preview-header">
              <div className="chat-preview-user">
                <span className={`chat-preview-avatar ${currentThread.avatarClass}`} aria-hidden="true">
                  {currentThread.initials}
                </span>
                <div>
                  <div className="chat-preview-name">{currentThread.name}</div>
                  <div className="chat-preview-phone">{currentThread.phone}</div>
                </div>
              </div>

              <div className="chat-preview-tags">
                <span className="chat-tag-channel">
                  {currentThread.channel === 'WhatsApp' ? '💬 WhatsApp' : '📸 Instagram'}
                </span>
                <span className="chat-tag-ai">AI</span>
              </div>
            </div>

            {/* Sotto-barra di stato */}
            <div className="chat-sub-status">
              <span>✉</span>
              <span>{currentThread.sub}</span>
            </div>

            {/* Messaggi della chat */}
            <div className="chat-messages-container" aria-live="polite">
              {currentThread.messages.map((m, idx) => (
                <div
                  key={idx}
                  className={m.from === 'customer' ? 'chat-msg-row-customer' : 'chat-msg-row-ai'}
                >
                  <div className={m.from === 'customer' ? 'bubble-customer' : 'bubble-ai'}>
                    {m.text}
                  </div>
                </div>
              ))}
            </div>

            {/* Barra inferiore Prendi in carico */}
            <div className="chat-takeover-bar">
              <div className="chat-takeover-hint">
                Per rispondere manualmente, prendi prima in carico la conversazione.
              </div>
              <div className="chat-takeover-btn">Prendi in carico</div>
            </div>
          </div>
        </div>

        {/* Maschere di dissolvenza su bordi laterali e inferiore */}
        <div className="vignette-fade-left" aria-hidden="true" />
        <div className="vignette-fade-right" aria-hidden="true" />
        <div className="vignette-fade-bottom" aria-hidden="true" />
      </div>
    </section>
  );
}
