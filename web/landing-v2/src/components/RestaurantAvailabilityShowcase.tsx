import React from 'react';

export default function RestaurantAvailabilityShowcase() {
  return (
    <div className="mv2-rest-avail-showcase" aria-hidden="true">
      <div className="mv2-rest-avail-card">
        {/* ====================================================================
            LEFT SUB-COLUMN: Dark Conversation & Availability Panel
            ==================================================================== */}
        <div className="mv2-rest-avail-dark-pane">
          {/* Header */}
          <div className="mv2-rest-avail-chat-header">
            <div className="mv2-rest-avail-chat-user">
              <span className="mv2-rest-avail-chat-avatar">MR</span>
              <div className="mv2-rest-avail-chat-user-copy">
                <strong className="mv2-rest-avail-chat-name">Marco Rossi</strong>
                <span className="mv2-rest-avail-chat-sub">WhatsApp Business</span>
              </div>
            </div>
            <span className="mv2-rest-avail-chat-badge">
              <span className="mv2-rest-avail-online-dot" />
              <span>WhatsApp</span>
            </span>
          </div>

          {/* Chat Messages */}
          <div className="mv2-rest-avail-chat-stream">
            {/* 1. Cliente */}
            <div className="mv2-rest-avail-msg-row is-client">
              <div className="mv2-rest-avail-bubble is-client">
                <p className="mv2-rest-avail-bubble-text">
                  Vorrei prenotare un tavolo per 2 persone stasera alle 20:30.
                </p>
                <span className="mv2-rest-avail-msg-time">19:05</span>
              </div>
            </div>

            {/* 2. Melpis AI */}
            <div className="mv2-rest-avail-msg-row is-melpis">
              <span className="mv2-rest-avail-melpis-avatar">M</span>
              <div className="mv2-rest-avail-bubble is-melpis">
                <p className="mv2-rest-avail-bubble-text">
                  Alle 20:30 siamo al completo. Posso proporti 19:30 oppure 21:30?
                </p>
                <span className="mv2-rest-avail-msg-time">
                  19:05 <span className="mv2-rest-avail-ticks">✓✓</span>
                </span>
              </div>
            </div>

            {/* 3. Cliente */}
            <div className="mv2-rest-avail-msg-row is-client">
              <div className="mv2-rest-avail-bubble is-client">
                <p className="mv2-rest-avail-bubble-text">21:30 va bene.</p>
                <span className="mv2-rest-avail-msg-time">19:06</span>
              </div>
            </div>

            {/* 4. Melpis AI */}
            <div className="mv2-rest-avail-msg-row is-melpis">
              <span className="mv2-rest-avail-melpis-avatar">M</span>
              <div className="mv2-rest-avail-bubble is-melpis">
                <p className="mv2-rest-avail-bubble-text">
                  Perfetto, confermo il tavolo per 2 persone alle 21:30.
                </p>
                <span className="mv2-rest-avail-msg-time">
                  19:06 <span className="mv2-rest-avail-ticks">✓✓</span>
                </span>
              </div>
            </div>
          </div>

          {/* Availability Chips Strip */}
          <div className="mv2-rest-avail-chips-panel">
            <div className="mv2-rest-avail-chips-header">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="3" y="4" width="18" height="18" rx="2" />
                <line x1="16" y1="2" x2="16" y2="6" />
                <line x1="8" y1="2" x2="8" y2="6" />
                <line x1="3" y1="10" x2="21" y2="10" />
              </svg>
              <span>Disponibilità turni stasera</span>
            </div>

            <div className="mv2-rest-avail-chips-row">
              {/* Chip 1: 19:30 Disponibile */}
              <div className="mv2-rest-avail-chip">
                <span className="mv2-rest-avail-chip-time">19:30</span>
                <span className="mv2-rest-avail-chip-label">Disponibile</span>
              </div>

              {/* Chip 2: 20:30 Completo */}
              <div className="mv2-rest-avail-chip is-full">
                <span className="mv2-rest-avail-chip-time">20:30</span>
                <span className="mv2-rest-avail-chip-badge-full">Completo</span>
              </div>

              {/* Chip 3: 21:30 Confermato (Active Blue) */}
              <div className="mv2-rest-avail-chip is-confirmed">
                <span className="mv2-rest-avail-chip-time">21:30</span>
                <span className="mv2-rest-avail-chip-badge-confirmed">Confermato ✓</span>
              </div>
            </div>
          </div>

          {/* Small Confirmation Strip */}
          <div className="mv2-rest-avail-voucher">
            <div className="mv2-rest-avail-voucher-icon">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
            <div className="mv2-rest-avail-voucher-copy">
              <strong>Tavolo per 2 persone</strong>
              <span>Oggi • 21:30 • Sincronizzato con il calendario</span>
            </div>
          </div>
        </div>

        {/* ====================================================================
            RIGHT SUB-COLUMN: Three Vertical Information Boxes
            ==================================================================== */}
        <div className="mv2-rest-avail-info-column">
          {/* Box 1: Verifica disponibilità */}
          <div className="mv2-rest-avail-info-box">
            <div className="mv2-rest-avail-box-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="3" y="4" width="18" height="18" rx="2" />
                <line x1="16" y1="2" x2="16" y2="6" />
                <line x1="8" y1="2" x2="8" y2="6" />
                <line x1="3" y1="10" x2="21" y2="10" />
                <polyline points="9 16 11.5 18.5 16 14" />
              </svg>
            </div>
            <h4 className="mv2-rest-avail-box-title">Verifica disponibilità</h4>
            <p className="mv2-rest-avail-box-desc">
              Controlla in tempo reale i posti liberi nel calendario della sala.
            </p>
          </div>

          {/* Box 2: Propone alternative */}
          <div className="mv2-rest-avail-info-box">
            <div className="mv2-rest-avail-box-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="12" cy="12" r="9" />
                <polyline points="12 6 12 12 16 14" />
                <path d="M16 18l4-4-4-4" />
              </svg>
            </div>
            <h4 className="mv2-rest-avail-box-title">Propone alternative</h4>
            <p className="mv2-rest-avail-box-desc">
              Se un turno è pieno, suggerisce subito un orario disponibile.
            </p>
          </div>

          {/* Box 3: Conferma il tavolo */}
          <div className="mv2-rest-avail-info-box">
            <div className="mv2-rest-avail-box-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <polyline points="16 11 18 13 22 9" />
              </svg>
            </div>
            <h4 className="mv2-rest-avail-box-title">Conferma il tavolo</h4>
            <p className="mv2-rest-avail-box-desc">
              Raccoglie i dettagli della prenotazione e accompagna il cliente fino alla conferma.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
