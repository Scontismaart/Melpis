import React, { useState, useEffect, useRef } from 'react';

interface ScenarioItem {
  id: string;
  title: string;
  description: string;
}

const SCENARIOS: ScenarioItem[] = [
  {
    id: 'concierge',
    title: 'Concierge di sala H24',
    description:
      'Risponde istantaneamente alle domande frequenti degli ospiti: orari della cucina, menu alla carta, percorsi degustazione, celiachia, allergeni, cani ammessi e parcheggio.',
  },
  {
    id: 'turni',
    title: 'Disponibilità e gestione doppi turni',
    description:
      'La demo simula la verifica degli slot e raccoglie la richiesta nel rispetto dei turni della sala (es. 19:30 e 21:30).',
  },
  {
    id: 'escalation',
    title: 'Escalation per tavolate ed eventi privati',
    description:
      'Quando arriva una richiesta per 10 persone, una festa di laurea o un menu personalizzato, Melpis raccoglie tutti i dettagli e passa la conversazione al responsabile di sala.',
  },
];

/* --------------------------------------------------------------------------
   VISTA 01: Inbox a 3 Colonne Dark (Concierge Menu & Allergeni)
   -------------------------------------------------------------------------- */
function Vista01Inbox() {
  return (
    <div className="mv2-scen-inbox-shell theme-dark">
      {/* Colonna 1: Lista conversazioni */}
      <div className="mv2-scen-inbox-list-col">
        <div className="mv2-scen-inbox-header">
          <div className="mv2-scen-inbox-title-row">
            <span className="mv2-scen-inbox-heading">Inbox</span>
            <span className="mv2-scen-inbox-count-badge">5</span>
          </div>

          <div className="mv2-scen-inbox-search-box">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <span>Cerca conversazione...</span>
          </div>
        </div>

        {/* Lista thread */}
        <div className="mv2-scen-inbox-threads">
          {/* Thread 1: Marco Rossi (Attivo) */}
          <div className="mv2-scen-thread-item is-active">
            <span className="mv2-scen-avatar">MR</span>
            <div className="mv2-scen-thread-content">
              <div className="mv2-scen-thread-top">
                <strong className="mv2-scen-thread-name">Marco Rossi</strong>
                <span className="mv2-scen-thread-time">2 min</span>
              </div>
              <div className="mv2-scen-thread-badges">
                <span className="mv2-scen-badge-sub">WhatsApp · AI</span>
              </div>
              <p className="mv2-scen-thread-snippet">Avete un menu senza glutine?</p>
            </div>
          </div>

          {/* Thread 2: Laura Conti */}
          <div className="mv2-scen-thread-item">
            <span className="mv2-scen-avatar">LC</span>
            <div className="mv2-scen-thread-content">
              <div className="mv2-scen-thread-top">
                <strong className="mv2-scen-thread-name">Laura Conti</strong>
                <span className="mv2-scen-thread-time">7 min</span>
              </div>
              <div className="mv2-scen-thread-badges">
                <span className="mv2-scen-badge-sub">WhatsApp · AI</span>
              </div>
              <p className="mv2-scen-thread-snippet">Quali sono gli allergeni nel menu...</p>
            </div>
          </div>

          {/* Thread 3: Andrea Pini */}
          <div className="mv2-scen-thread-item">
            <span className="mv2-scen-avatar">AP</span>
            <div className="mv2-scen-thread-content">
              <div className="mv2-scen-thread-top">
                <strong className="mv2-scen-thread-name">Andrea Pini</strong>
                <span className="mv2-scen-thread-time">19 min</span>
              </div>
              <div className="mv2-scen-thread-badges">
                <span className="mv2-scen-badge-sub">Instagram · AI</span>
              </div>
              <p className="mv2-scen-thread-snippet">A che ora chiude la cucina?</p>
            </div>
          </div>

          {/* Thread 4: Sara Verdi */}
          <div className="mv2-scen-thread-item">
            <span className="mv2-scen-avatar">SV</span>
            <div className="mv2-scen-thread-content">
              <div className="mv2-scen-thread-top">
                <strong className="mv2-scen-thread-name">Sara Verdi</strong>
                <span className="mv2-scen-thread-time">32 min</span>
              </div>
              <div className="mv2-scen-thread-badges">
                <span className="mv2-scen-badge-sub">WhatsApp · Operatore</span>
              </div>
              <p className="mv2-scen-thread-snippet">C'è parcheggio vicino al ristorante?</p>
            </div>
          </div>

          {/* Thread 5: Francesco De Luca */}
          <div className="mv2-scen-thread-item">
            <span className="mv2-scen-avatar">FD</span>
            <div className="mv2-scen-thread-content">
              <div className="mv2-scen-thread-top">
                <strong className="mv2-scen-thread-name">Francesco De Luca</strong>
                <span className="mv2-scen-thread-time">1 h</span>
              </div>
              <div className="mv2-scen-thread-badges">
                <span className="mv2-scen-badge-sub">WhatsApp · AI</span>
              </div>
              <p className="mv2-scen-thread-snippet">Fate un menu degustazione?</p>
            </div>
          </div>
        </div>
      </div>

      {/* Colonna 2: Chat aperta con Marco Rossi */}
      <div className="mv2-scen-inbox-chat-col">
        <div className="mv2-scen-chat-head">
          <span className="mv2-scen-avatar">MR</span>
          <div className="mv2-scen-chat-identity">
            <strong className="mv2-scen-chat-name">Marco Rossi</strong>
            <small className="mv2-scen-chat-sub">+39 347 123 4567</small>
          </div>
          <div className="mv2-scen-chat-head-tags">
            <span className="mv2-scen-pill-green">WhatsApp</span>
            <span className="mv2-scen-pill-gray">AI</span>
          </div>
        </div>

        <div className="mv2-scen-chat-assist-banner">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
            <polyline points="22,6 12,13 2,6" />
          </svg>
          <span>L'assistente sta gestendo la conversazione.</span>
        </div>

        <div className="mv2-scen-chat-bubbles-scroll">
          {/* Cliente */}
          <div className="mv2-scen-bubble-row is-client">
            <div className="mv2-scen-bubble is-client">
              Avete un menu senza glutine?
            </div>
          </div>

          {/* Melpis */}
          <div className="mv2-scen-bubble-row is-ai">
            <div className="mv2-scen-bubble is-ai">
              Certo! Abbiamo diverse proposte senza glutine, compresi antipasti, primi e secondi. Posso inviarti il menu dedicato?
            </div>
          </div>

          {/* Cliente */}
          <div className="mv2-scen-bubble-row is-client">
            <div className="mv2-scen-bubble is-client">
              Sì, grazie!
            </div>
          </div>

          {/* Melpis con Allegato */}
          <div className="mv2-scen-bubble-row is-ai">
            <div className="mv2-scen-bubble is-ai">
              <p className="mv2-rest-chat-menu-intro">Ecco il menu senza glutine aggiornato.</p>
              <div className="mv2-scen-pdf-card">
                <div className="mv2-scen-pdf-left">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                    <line x1="16" y1="13" x2="8" y2="13" />
                    <line x1="16" y1="17" x2="8" y2="17" />
                  </svg>
                  <span className="mv2-scen-pdf-title">Menu senza glutine (PDF)</span>
                </div>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <line x1="7" y1="17" x2="17" y2="7" />
                  <polyline points="7 7 17 7 17 17" />
                </svg>
              </div>
            </div>
          </div>

          {/* Cliente */}
          <div className="mv2-scen-bubble-row is-client">
            <div className="mv2-scen-bubble is-client">
              Perfetto! Quali sono gli orari della cucina?
            </div>
          </div>

          {/* Melpis */}
          <div className="mv2-scen-bubble-row is-ai">
            <div className="mv2-scen-bubble is-ai">
              La cucina è attiva tutti i giorni dalle 12:00 alle 14:30 e dalle 19:00 alle 22:30.
            </div>
          </div>

          {/* Cliente */}
          <div className="mv2-scen-bubble-row is-client">
            <div className="mv2-scen-bubble is-client">
              Ottimo, grazie mille!
            </div>
          </div>
        </div>

        {/* Footer Prendi in carico */}
        <div className="mv2-scen-chat-takeover-box">
          <p className="mv2-scen-takeover-note">Per rispondere manualmente, prendi prima in carico la conversazione.</p>
          <button type="button" className="mv2-scen-takeover-btn">Prendi in carico</button>
        </div>
      </div>

      {/* Colonna 3: Dettagli */}
      <div className="mv2-scen-inbox-details-col">
        <div className="mv2-scen-details-title">Dettagli</div>

        <div className="mv2-scen-details-section">
          <span className="mv2-scen-details-label">INFORMAZIONI</span>
          <dl className="mv2-scen-details-dl">
            <div className="mv2-scen-details-row">
              <dt>Numero</dt>
              <dd>+39 347 123 4567</dd>
            </div>
            <div className="mv2-scen-details-row">
              <dt>Canale</dt>
              <dd>WhatsApp</dd>
            </div>
            <div className="mv2-scen-details-row">
              <dt>Stato</dt>
              <dd>AI</dd>
            </div>
            <div className="mv2-scen-details-row">
              <dt>Tag</dt>
              <dd>Menu / Allergeni</dd>
            </div>
            <div className="mv2-scen-details-row">
              <dt>Ultima azione</dt>
              <dd>Risposta inviata</dd>
            </div>
            <div className="mv2-scen-details-row">
              <dt>Ultimo messaggio</dt>
              <dd>2 min fa</dd>
            </div>
          </dl>
        </div>

        <div className="mv2-scen-details-section">
          <span className="mv2-scen-details-label">AZIONI RAPIDE</span>
          <div className="mv2-scen-select-box">
            <span>Luca · Operatore</span>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </div>
          <button type="button" className="mv2-scen-details-btn">Prendi in carico</button>
        </div>

        <div className="mv2-scen-details-section">
          <span className="mv2-scen-details-label">NOTE AI</span>
          <p className="mv2-scen-note-text">
            Cliente interessato al menu senza glutine e agli orari della cucina.
          </p>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   VISTA 02: Prenotazioni & Gestione Turni (2 Pannelli Dark)
   -------------------------------------------------------------------------- */
function Vista02Turni() {
  return (
    <div className="mv2-scen-split-grid">
      {/* Pannello Sinistro: WhatsApp Chat */}
      <div className="mv2-scen-dark-card mv2-scen-dark-chat">
        {/* Header Ristorante */}
        <div className="mv2-scen-dark-chat-head">
          <div className="mv2-scen-dark-chat-head-left">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="mv2-scen-dark-back-arrow" aria-hidden="true">
              <polyline points="15 18 9 12 15 6" />
            </svg>
            <img
              src="/landing-v2/assets/ristoranti/hero-ristoranti.webp"
              alt="Ristorante Il Cortile"
              className="mv2-scen-dark-chat-avatar-img"
            />
            <div className="mv2-scen-dark-chat-identity">
              <strong className="mv2-scen-dark-chat-title">Ristorante Il Cortile</strong>
              <div className="mv2-scen-dark-chat-sub">
                <span className="mv2-scen-online-dot" />
                <span>Concierge Melpis</span>
              </div>
            </div>
          </div>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" className="mv2-scen-dark-chat-menu" aria-hidden="true">
            <circle cx="12" cy="5" r="1.5" />
            <circle cx="12" cy="12" r="1.5" />
            <circle cx="12" cy="19" r="1.5" />
          </svg>
        </div>

        {/* Stream Messaggi */}
        <div className="mv2-scen-dark-chat-stream">
          {/* Cliente */}
          <div className="mv2-scen-dark-msg-row is-client">
            <div className="mv2-scen-dark-user-avatar" aria-hidden="true">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
            </div>
            <div className="mv2-scen-dark-bubble is-client">
              <p className="mv2-scen-dark-bubble-text">
                Vorrei prenotare un tavolo per 2 persone stasera, alle 20:30.
              </p>
              <span className="mv2-scen-dark-time">19:05</span>
            </div>
          </div>

          {/* Melpis */}
          <div className="mv2-scen-dark-msg-row is-melpis">
            <span className="mv2-scen-dark-melpis-avatar" aria-hidden="true">M</span>
            <div className="mv2-scen-dark-bubble is-melpis">
              <p className="mv2-scen-dark-bubble-text">
                Alle 20:30 siamo al completo. Posso proporti un tavolo alle 19:30 oppure alle 21:30?
              </p>
              <span className="mv2-scen-dark-time">
                19:05 <span className="mv2-scen-check-blue">✓✓</span>
              </span>
            </div>
          </div>

          {/* Cliente */}
          <div className="mv2-scen-dark-msg-row is-client">
            <div className="mv2-scen-dark-user-avatar" aria-hidden="true">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
            </div>
            <div className="mv2-scen-dark-bubble is-client">
              <p className="mv2-scen-dark-bubble-text">
                Perfetto, alle 21:30 va benissimo.
              </p>
              <span className="mv2-scen-dark-time">19:06</span>
            </div>
          </div>

          {/* Melpis */}
          <div className="mv2-scen-dark-msg-row is-melpis">
            <span className="mv2-scen-dark-melpis-avatar" aria-hidden="true">M</span>
            <div className="mv2-scen-dark-bubble is-melpis">
              <p className="mv2-scen-dark-bubble-text">
                Fatto! Ho confermato la tua prenotazione per 2 persone alle 21:30. Ti invio il riepilogo qui sotto.
              </p>
              <span className="mv2-scen-dark-time">
                19:06 <span className="mv2-scen-check-blue">✓✓</span>
              </span>
            </div>
          </div>

          {/* Card Prenotazione Confermata */}
          <div className="mv2-scen-voucher-card">
            <div className="mv2-scen-voucher-header">
              <span className="mv2-scen-voucher-check" aria-hidden="true">
                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </span>
              <strong>Prenotazione confermata</strong>
            </div>
            <div className="mv2-scen-voucher-body">
              <div className="mv2-scen-voucher-row">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <rect x="3" y="4" width="18" height="18" rx="2" />
                  <line x1="16" y1="2" x2="16" y2="6" />
                  <line x1="8" y1="2" x2="8" y2="6" />
                  <line x1="3" y1="10" x2="21" y2="10" />
                </svg>
                <span>Oggi, 14 marzo</span>
              </div>
              <div className="mv2-scen-voucher-row">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                </svg>
                <span>2 persone</span>
              </div>
              <div className="mv2-scen-voucher-row">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
                <span>21:30</span>
              </div>
              <div className="mv2-scen-voucher-row">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M18 2v20M6 2v20M2 7h8a4 4 0 0 1-4 4v11" />
                </svg>
                <span>Ristorante Il Cortile</span>
              </div>
            </div>
            <div className="mv2-scen-voucher-foot">19:06</div>
          </div>
        </div>

        {/* Input Bar */}
        <div className="mv2-scen-dark-composer">
          <input
            type="text"
            className="mv2-scen-dark-input"
            placeholder="Scrivi un messaggio..."
            readOnly
          />
          <button type="button" className="mv2-scen-dark-clip" aria-label="Allega file">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          </button>
          <button type="button" className="mv2-scen-dark-send-btn" aria-label="Invia">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
            </svg>
          </button>
        </div>
      </div>

      {/* Pannello Destro: Disponibilità e Turni */}
      <div className="mv2-scen-dark-side-col">
        {/* Card 1: Disponibilità e turni */}
        <div className="mv2-scen-dark-card mv2-scen-dark-card--slots">
          <div className="mv2-scen-dark-card-head">
            <div className="mv2-scen-dark-card-head-title">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="3" y="4" width="18" height="18" rx="2" />
                <line x1="16" y1="2" x2="16" y2="6" />
                <line x1="8" y1="2" x2="8" y2="6" />
                <line x1="3" y1="10" x2="21" y2="10" />
              </svg>
              <strong>Disponibilità e turni</strong>
            </div>
          </div>
          <div className="mv2-scen-calendar-day-bar">
            <span>Oggi, 14 marzo</span>
            <div className="mv2-scen-calendar-arrows" aria-hidden="true">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <polyline points="15 18 9 12 15 6" />
              </svg>
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <polyline points="9 18 15 12 9 6" />
              </svg>
            </div>
          </div>

          {/* 1° turno */}
          <div className="mv2-scen-turn-block">
            <span className="mv2-scen-turn-label">1° turno</span>
            <div className="mv2-scen-slots-row">
              <button type="button" className="mv2-scen-slot-pill">19:00</button>
              <button type="button" className="mv2-scen-slot-pill is-active-blue">19:30</button>
              <button type="button" className="mv2-scen-slot-pill">20:00</button>
            </div>
          </div>

          {/* 2° turno */}
          <div className="mv2-scen-turn-block">
            <span className="mv2-scen-turn-label">2° turno</span>
            <div className="mv2-scen-slots-row">
              <button type="button" className="mv2-scen-slot-pill is-full">
                <span className="mv2-scen-slot-time">20:30</span>
                <span className="mv2-scen-slot-sub-full">Completo</span>
              </button>
              <button type="button" className="mv2-scen-slot-pill">21:00</button>
              <button type="button" className="mv2-scen-slot-pill is-selected-blue">21:30</button>
            </div>
          </div>
        </div>

        {/* Card 2: Riepilogo prenotazione */}
        <div className="mv2-scen-dark-card mv2-scen-dark-card--summary">
          <div className="mv2-scen-dark-card-head">
            <div className="mv2-scen-dark-card-head-title">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="3" y="4" width="18" height="18" rx="2" />
                <line x1="16" y1="2" x2="16" y2="6" />
                <line x1="8" y1="2" x2="8" y2="6" />
                <line x1="3" y1="10" x2="21" y2="10" />
              </svg>
              <strong>Riepilogo prenotazione</strong>
            </div>
            <span className="mv2-scen-badge-confirmed">
              <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              Confermata
            </span>
          </div>

          <div className="mv2-scen-summary-list">
            <div className="mv2-scen-summary-item">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
              </svg>
              <span>2 persone</span>
            </div>
            <div className="mv2-scen-summary-item">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="3" y="4" width="18" height="18" rx="2" />
                <line x1="16" y1="2" x2="16" y2="6" />
                <line x1="8" y1="2" x2="8" y2="6" />
                <line x1="3" y1="10" x2="21" y2="10" />
              </svg>
              <span>Oggi, 14 marzo</span>
            </div>
            <div className="mv2-scen-summary-item">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              <span>21:30</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   VISTA 03: Passaggio di Sala & Tavolate (2 Pannelli Dark)
   -------------------------------------------------------------------------- */
function Vista03Escalation() {
  return (
    <div className="mv2-scen-split-grid">
      {/* Pannello Sinistro: Chat */}
      <div className="mv2-scen-dark-card mv2-scen-dark-chat">
        {/* Header Cliente */}
        <div className="mv2-scen-dark-chat-head">
          <div className="mv2-scen-dark-chat-head-left">
            <div className="mv2-scen-dark-user-avatar" aria-hidden="true">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
            </div>
            <div className="mv2-scen-dark-chat-identity">
              <strong className="mv2-scen-dark-chat-title">Cliente</strong>
              <div className="mv2-scen-dark-chat-sub">Conversazione in corso</div>
            </div>
          </div>
        </div>

        {/* Stream Messaggi */}
        <div className="mv2-scen-dark-chat-stream">
          {/* Cliente */}
          <div className="mv2-scen-dark-msg-row is-client">
            <div className="mv2-scen-dark-bubble is-client">
              <p className="mv2-scen-dark-bubble-text">
                Buongiorno! Vorrei organizzare una cena di laurea per circa 12 persone. Avete disponibilità la prossima settimana?
              </p>
              <span className="mv2-scen-dark-time">19:24</span>
            </div>
          </div>

          {/* Melpis */}
          <div className="mv2-scen-dark-msg-row is-melpis">
            <span className="mv2-scen-dark-melpis-avatar" aria-hidden="true">M</span>
            <div className="mv2-scen-dark-bubble is-melpis">
              <p className="mv2-scen-dark-bubble-text">
                Grazie! Ho raccolto i dettagli e inoltrato la richiesta al nostro responsabile di sala, che vi ricontatterà a breve.
              </p>
              <span className="mv2-scen-dark-time">
                19:25 <span className="mv2-scen-check-blue">✓✓</span>
              </span>
            </div>
          </div>
        </div>

        {/* Input Bar */}
        <div className="mv2-scen-dark-composer">
          <input
            type="text"
            className="mv2-scen-dark-input"
            placeholder="Scrivi un messaggio..."
            readOnly
          />
          <button type="button" className="mv2-scen-dark-clip" aria-label="Allega file">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          </button>
          <button type="button" className="mv2-scen-dark-send-btn" aria-label="Invia">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
            </svg>
          </button>
        </div>
      </div>

      {/* Pannello Destro: Richiesta passata allo staff */}
      <div className="mv2-scen-dark-side-col">
        {/* Card 1: Richiesta per evento privato */}
        <div className="mv2-scen-dark-card mv2-scen-dark-card--slots">
          <div className="mv2-scen-dark-card-head">
            <div className="mv2-scen-dark-card-head-title">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                <path d="M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
              <strong>Richiesta per evento privato</strong>
            </div>
            <span className="mv2-scen-badge-escalated">
              <span className="mv2-scen-online-dot" />
              Inoltrata allo staff
            </span>
          </div>

          <div className="mv2-scen-escalation-subtitle">Riepilogo richiesta</div>

          <div className="mv2-scen-escalation-list">
            <div className="mv2-scen-escalation-item">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="3" y="4" width="18" height="18" rx="2" />
                <line x1="16" y1="2" x2="16" y2="6" />
                <line x1="8" y1="2" x2="8" y2="6" />
                <line x1="3" y1="10" x2="21" y2="10" />
              </svg>
              <div className="mv2-scen-escalation-text">
                <span className="mv2-scen-escalation-label">Data</span>
                <span className="mv2-scen-escalation-val">Gio 22 mag, sera</span>
              </div>
            </div>

            <div className="mv2-scen-escalation-item">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
              </svg>
              <div className="mv2-scen-escalation-text">
                <span className="mv2-scen-escalation-label">Ospiti</span>
                <span className="mv2-scen-escalation-val">Circa 12 persone</span>
              </div>
            </div>

            <div className="mv2-scen-escalation-item">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
              </svg>
              <div className="mv2-scen-escalation-text">
                <span className="mv2-scen-escalation-label">Note speciali</span>
                <span className="mv2-scen-escalation-val">Cena di laurea</span>
              </div>
            </div>

            <div className="mv2-scen-escalation-item">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M18 2v20M6 2v20M2 7h8a4 4 0 0 1-4 4v11" />
              </svg>
              <div className="mv2-scen-escalation-text">
                <span className="mv2-scen-escalation-label">Richieste menu</span>
                <span className="mv2-scen-escalation-val">Menu personalizzato (con opzioni vegetariane)</span>
              </div>
            </div>
          </div>
        </div>

        {/* Card 2: Passata al Responsabile di sala */}
        <div className="mv2-scen-dark-card mv2-scen-staff-handover-card">
          <div className="mv2-scen-staff-left">
            <img
              src="/landing-v2/assets/ristoranti/passaggio-staff.webp"
              alt="Responsabile di sala"
              className="mv2-scen-staff-avatar-img"
            />
            <div className="mv2-scen-staff-copy">
              <span className="mv2-scen-staff-label">Passata al</span>
              <strong className="mv2-scen-staff-role">Responsabile di sala</strong>
            </div>
          </div>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="mv2-scen-staff-chevron" aria-hidden="true">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   COMPONENT PRINCIPALE SHOWCASE (SCROLL-DRIVEN EXPERIENCE)
   -------------------------------------------------------------------------- */
interface RestaurantScenariosShowcaseProps {
  children?: React.ReactNode;
}

export default function RestaurantScenariosShowcase({ children }: RestaurantScenariosShowcaseProps) {
  const [mobileActiveIndex, setMobileActiveIndex] = useState(0);

  const trackRef = useRef<HTMLDivElement>(null);
  const stickyRef = useRef<HTMLDivElement>(null);

  const layer1Ref = useRef<HTMLDivElement>(null);
  const layer2Ref = useRef<HTMLDivElement>(null);
  const layer3Ref = useRef<HTMLDivElement>(null);

  const card1Ref = useRef<HTMLButtonElement>(null);
  const card2Ref = useRef<HTMLButtonElement>(null);
  const card3Ref = useRef<HTMLButtonElement>(null);

  const progressTrackRef = useRef<HTMLDivElement>(null);
  const progressFillRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const track = trackRef.current;
    const sticky = stickyRef.current;
    const layer1 = layer1Ref.current;
    const layer2 = layer2Ref.current;
    const layer3 = layer3Ref.current;
    const card1 = card1Ref.current;
    const card2 = card2Ref.current;
    const card3 = card3Ref.current;
    const progressTrack = progressTrackRef.current;
    const progressFill = progressFillRef.current;

    if (!track || !sticky || !layer1 || !layer2 || !layer3) return;

    const isMobile = () => window.innerWidth <= 960;
    const prefersReduced = () =>
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Quintic smootherstep easing (6x^5 - 15x^4 + 10x^3) for zero-jerk, silky transitions
    const smootherstep = (min: number, max: number, value: number) => {
      const x = Math.max(0, Math.min(1, (value - min) / (max - min)));
      return x * x * x * (x * (x * 6 - 15) + 10);
    };

    let rafId: number | null = null;
    let ticking = false;

    // Apply normalized progress [0, 1] directly to DOM properties (no React re-renders during scroll)
    const applyProgress = (p: number) => {
      // -----------------------------------------------------------------------
      // Silky Wide Transitions with gentle depth (scale + translateY + opacity)
      // Transition 1 -> 2: spans p in [0.16, 0.50] (center ~0.33)
      // Transition 2 -> 3: spans p in [0.50, 0.84] (center ~0.67)
      // -----------------------------------------------------------------------
      let op1 = 1;
      let y1 = 0;
      let s1 = 1;

      if (p > 0.16 && p < 0.50) {
        const t = smootherstep(0.16, 0.50, p);
        op1 = 1 - t;
        y1 = -8 * t;
        s1 = 1 - 0.015 * t;
      } else if (p >= 0.50) {
        op1 = 0;
        y1 = -8;
        s1 = 0.985;
      }

      let op2 = 0;
      let y2 = 8;
      let s2 = 0.985;

      if (p > 0.16 && p < 0.50) {
        const t = smootherstep(0.16, 0.50, p);
        op2 = t;
        y2 = 8 * (1 - t);
        s2 = 0.985 + 0.015 * t;
      } else if (p >= 0.50 && p < 0.84) {
        const t = smootherstep(0.50, 0.84, p);
        op2 = 1 - t;
        y2 = -8 * t;
        s2 = 1 - 0.015 * t;
      } else if (p >= 0.84) {
        op2 = 0;
        y2 = -8;
        s2 = 0.985;
      }

      let op3 = 0;
      let y3 = 8;
      let s3 = 0.985;

      if (p > 0.50 && p < 0.84) {
        const t = smootherstep(0.50, 0.84, p);
        op3 = t;
        y3 = 8 * (1 - t);
        s3 = 0.985 + 0.015 * t;
      } else if (p >= 0.84) {
        op3 = 1;
        y3 = 0;
        s3 = 1;
      }

      // Left Column Layer DOM updates (hardware-accelerated GPU translate3d + scale + opacity)
      layer1.style.opacity = op1.toFixed(3);
      layer1.style.transform = `translate3d(0, ${y1.toFixed(1)}px, 0) scale(${s1.toFixed(4)})`;
      layer1.style.visibility = op1 > 0.003 ? 'visible' : 'hidden';
      layer1.classList.toggle('is-interactive', op1 > 0.55);

      layer2.style.opacity = op2.toFixed(3);
      layer2.style.transform = `translate3d(0, ${y2.toFixed(1)}px, 0) scale(${s2.toFixed(4)})`;
      layer2.style.visibility = op2 > 0.003 ? 'visible' : 'hidden';
      layer2.classList.toggle('is-interactive', op2 > 0.55);

      layer3.style.opacity = op3.toFixed(3);
      layer3.style.transform = `translate3d(0, ${y3.toFixed(1)}px, 0) scale(${s3.toFixed(4)})`;
      layer3.style.visibility = op3 > 0.003 ? 'visible' : 'hidden';
      layer3.classList.toggle('is-interactive', op3 > 0.55);

      // Right Column Cards: continuous progressive active ratio (smooth bell-curve handoff)
      let r1 = 1;
      if (p > 0.16 && p < 0.44) {
        r1 = 1 - smootherstep(0.16, 0.44, p);
      } else if (p >= 0.44) {
        r1 = 0;
      }

      let r2 = 0;
      if (p > 0.20 && p < 0.46) {
        r2 = smootherstep(0.20, 0.46, p);
      } else if (p >= 0.46 && p <= 0.54) {
        r2 = 1;
      } else if (p > 0.54 && p < 0.80) {
        r2 = 1 - smootherstep(0.54, 0.80, p);
      } else if (p >= 0.80) {
        r2 = 0;
      }

      let r3 = 0;
      if (p > 0.56 && p < 0.84) {
        r3 = smootherstep(0.56, 0.84, p);
      } else if (p >= 0.84) {
        r3 = 1;
      }

      if (card1) {
        card1.style.setProperty('--active-ratio', r1.toFixed(3));
        card1.setAttribute('aria-selected', r1 > 0.5 ? 'true' : 'false');
      }
      if (card2) {
        card2.style.setProperty('--active-ratio', r2.toFixed(3));
        card2.setAttribute('aria-selected', r2 > 0.5 ? 'true' : 'false');
      }
      if (card3) {
        card3.style.setProperty('--active-ratio', r3.toFixed(3));
        card3.setAttribute('aria-selected', r3 > 0.5 ? 'true' : 'false');
      }

      // Minimal Ultramarine Scroll Progress Indicator update:
      // at p = 0: 0.24 (covers top card 1)
      // at p = 1: 1.00 (covers all 3 cards)
      if (progressFill) {
        const fillProgress = 0.24 + 0.76 * p;
        progressFill.style.transform = `scaleY(${fillProgress.toFixed(3)})`;
      }
    };

    const update = () => {
      ticking = false;
      if (isMobile() || prefersReduced()) return;

      const trackRect = track.getBoundingClientRect();
      const stickyRect = sticky.getBoundingClientRect();
      const totalScrollDistance = track.offsetHeight - sticky.offsetHeight;

      if (totalScrollDistance <= 0) return;

      // The sticky offset from the top of the viewport (matching CSS top: 84px)
      const stickyTopOffset = parseFloat(window.getComputedStyle(sticky).top) || 84;
      const scrolled = stickyTopOffset - trackRect.top;
      const progress = Math.max(0, Math.min(1, scrolled / totalScrollDistance));

      applyProgress(progress);

      // Indicator visibility lifecycle:
      // Appears smoothly as sticky engages, remains during sticky, dissolves when sticky leaves
      let indicatorOp = 0;
      if (scrolled >= -20 && scrolled <= totalScrollDistance + 40) {
        if (scrolled < 30) {
          indicatorOp = Math.max(0, (scrolled + 20) / 50);
        } else if (scrolled > totalScrollDistance - 30) {
          indicatorOp = Math.max(0, (totalScrollDistance + 40 - scrolled) / 70);
        } else {
          indicatorOp = 1;
        }
      }

      if (progressTrack) {
        progressTrack.style.opacity = indicatorOp.toFixed(3);
        progressTrack.style.visibility = indicatorOp > 0.01 ? 'visible' : 'hidden';
      }
    };

    const onScroll = () => {
      if (!ticking) {
        ticking = true;
        rafId = requestAnimationFrame(update);
      }
    };

    const onResize = () => {
      if (isMobile() || prefersReduced()) {
        layer1.style.opacity = '';
        layer1.style.transform = '';
        layer1.style.visibility = '';
        layer2.style.opacity = '';
        layer2.style.transform = '';
        layer2.style.visibility = '';
        layer3.style.opacity = '';
        layer3.style.transform = '';
        layer3.style.visibility = '';
        if (card1) card1.style.removeProperty('--active-ratio');
        if (card2) card2.style.removeProperty('--active-ratio');
        if (card3) card3.style.removeProperty('--active-ratio');
        if (progressTrack) {
          progressTrack.style.opacity = '';
          progressTrack.style.visibility = '';
        }
        if (progressFill) {
          progressFill.style.transform = '';
        }
      } else {
        update();
      }
    };

    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onResize, { passive: true });

    // Initial calculation on mount
    update();

    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onResize);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, []);

  const handleCardClick = (index: number) => {
    if (typeof window === 'undefined') return;

    if (window.innerWidth <= 960) {
      setMobileActiveIndex(index);
      return;
    }

    const track = trackRef.current;
    const sticky = stickyRef.current;
    if (!track || !sticky) return;

    const totalScrollDistance = track.offsetHeight - sticky.offsetHeight;
    if (totalScrollDistance <= 0) return;

    // Stable target centers for each scenario
    // 0: p = 0.08, 1: p = 0.50, 2: p = 0.92
    const targets = [0.08, 0.50, 0.92];
    const targetP = targets[index];
    const stickyTopOffset = parseFloat(window.getComputedStyle(sticky).top) || 84;

    const trackAbsTop = window.scrollY + track.getBoundingClientRect().top;
    const targetScrollY = trackAbsTop - stickyTopOffset + targetP * totalScrollDistance;

    window.scrollTo({
      top: targetScrollY,
      behavior: 'smooth',
    });
  };

  return (
    <div className="mv2-rest-scenarios-scroll-track" ref={trackRef}>
      <div className="mv2-rest-scenarios-sticky-wrapper" ref={stickyRef}>
        {children || (
          <div className="mv2-rest-editorial-header">
            <h2 className="mv2-rest-editorial-title mv2-rest-sr-only" id="mv2-rest-scenari-title">
              Configura una volta.<br />
              <span className="mv2-rest-highlight">Melpis gestisce il servizio.</span>
            </h2>
            <p className="mv2-rest-editorial-desc">
              I clienti vogliono risposte rapide su tavoli liberi, intolleranze, menu degustazione e orari della cucina.
              Melpis risponde subito e passa la parola allo staff solo quando serve.
            </p>
          </div>
        )}

        <div className="mv2-rest-scenarios-grid">
          {/* Left Column: Interactive Dark Window */}
          <div className="mv2-rest-scenarios-window-col">
            <div className="mv2-rest-mockup-frame mv2-rest-mockup-frame--scenario">
              {/* Canvas with stacked crossfade layers */}
              <div className="mv2-rest-mockup-canvas mv2-rest-mockup-canvas--scenario">
                <div
                  ref={layer1Ref}
                  className={`mv2-rest-scenario-layer mv2-rest-scenario-layer--1 ${mobileActiveIndex === 0 ? 'is-mobile-active' : ''}`}
                  id="scenario-panel-concierge"
                  role="tabpanel"
                  aria-labelledby="scenario-tab-concierge"
                >
                  <Vista01Inbox />
                </div>

                <div
                  ref={layer2Ref}
                  className={`mv2-rest-scenario-layer mv2-rest-scenario-layer--2 ${mobileActiveIndex === 1 ? 'is-mobile-active' : ''}`}
                  id="scenario-panel-turni"
                  role="tabpanel"
                  aria-labelledby="scenario-tab-turni"
                >
                  <Vista02Turni />
                </div>

                <div
                  ref={layer3Ref}
                  className={`mv2-rest-scenario-layer mv2-rest-scenario-layer--3 ${mobileActiveIndex === 2 ? 'is-mobile-active' : ''}`}
                  id="scenario-panel-escalation"
                  role="tabpanel"
                  aria-labelledby="scenario-tab-escalation"
                >
                  <Vista03Escalation />
                </div>
              </div>
            </div>
          </div>

          {/* Right Column: 3 Scenario Cards */}
          <div className="mv2-rest-scenarios-cards-col" role="tablist" aria-label="Scenari operativi ristorante">
            {/* Minimal Ultramarine Scroll Progress Indicator */}
            <div
              ref={progressTrackRef}
              className="mv2-rest-scroll-indicator"
              aria-hidden="true"
            >
              <div className="mv2-rest-scroll-indicator-rail">
                <div ref={progressFillRef} className="mv2-rest-scroll-indicator-fill" />
              </div>
            </div>

            {SCENARIOS.map((item, idx) => {
              const cardRef = idx === 0 ? card1Ref : idx === 1 ? card2Ref : card3Ref;
              const isSelectedMobile = idx === mobileActiveIndex;
              return (
                <button
                  key={item.id}
                  ref={cardRef}
                  type="button"
                  role="tab"
                  aria-selected={isSelectedMobile}
                  aria-controls={`scenario-panel-${item.id}`}
                  id={`scenario-tab-${item.id}`}
                  className={`mv2-rest-scenario-card ${isSelectedMobile ? 'is-active' : ''}`}
                  onClick={() => handleCardClick(idx)}
                >
                  <div className="mv2-rest-scenario-card-header">
                    <div className="mv2-rest-scenario-card-title-wrap">
                      <h3 className="mv2-rest-scenario-title">{item.title}</h3>
                    </div>
                    <span className="mv2-rest-scenario-badge-active" aria-hidden="true">
                      <span className="mv2-rest-active-dot" />
                      Attivo
                    </span>
                  </div>
                  <p className="mv2-rest-scenario-desc">{item.description}</p>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
