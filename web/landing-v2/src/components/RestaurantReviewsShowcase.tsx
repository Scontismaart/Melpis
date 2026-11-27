import React from 'react';

// Star SVG helper (filled blue, half-filled, or dark/unfilled)
const StarIcon = ({ type = 'filled' }: { type?: 'filled' | 'half' | 'empty' }) => {
  if (type === 'half') {
    return (
      <svg width="11" height="11" viewBox="0 0 24 24" aria-hidden="true" className="mv2-rest-reviews-star-svg">
        <defs>
          <linearGradient id="melpisHalfStarGrad">
            <stop offset="65%" stopColor="#2B59FF" />
            <stop offset="65%" stopColor="#FFFFFF" />
          </linearGradient>
        </defs>
        <polygon
          points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"
          fill="url(#melpisHalfStarGrad)"
        />
      </svg>
    );
  }

  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 24 24"
      fill={type === 'filled' ? '#2B59FF' : 'rgba(255, 255, 255, 0.18)'}
      stroke="none"
      aria-hidden="true"
      className="mv2-rest-reviews-star-svg"
    >
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  );
};

export interface RestaurantReviewsShowcaseProps {
  variant?: 'restaurants' | 'hotel' | 'barbieri' | 'saloni';
}

export default function RestaurantReviewsShowcase({
  variant = 'restaurants',
}: RestaurantReviewsShowcaseProps) {
  const configs = {
    restaurants: {
      userRole: 'Proprietario',
      userEmail: 'osteria.demo@melpis.it',
      descSuffix: 'del tuo ristorante.',
      rev1: {
        initials: 'MV',
        author: 'Marco Verdi',
        rating: 5,
        time: '2 ore fa',
        snippet: 'Esperienza eccellente! Piatti curati, ...',
        fullText: 'Esperienza eccellente! Piatti curati, ingredienti di qualità e servizio impeccabile. Torneremo presto!',
      },
      rev2: {
        initials: 'LC',
        author: 'Laura Conti',
        rating: 5,
        time: '1 giorno fa',
        snippet: 'Ambiente molto accogliente e...',
      },
      rev3: {
        initials: 'GF',
        author: 'Giovanni Ferrari',
        rating: 4,
        time: '2 giorni fa',
        snippet: 'Buona cucina e servizio cordiale, ...',
      },
      rev4: {
        initials: 'ES',
        author: 'Elena Sartori',
        rating: 5,
        time: '3 giorni fa',
        snippet: 'Torneremo sicuramente, tutto perfetto!',
      },
      aiDraft: 'Grazie mille, Marco! Siamo davvero felici che tu abbia apprezzato la nostra cucina e il servizio. Ti aspettiamo presto per una nuova esperienza da noi!',
    },
    hotel: {
      userRole: 'Direzione Hotel',
      userEmail: 'reception.demo@melpis.it',
      descSuffix: 'del tuo hotel.',
      rev1: {
        initials: 'MA',
        author: 'Marco Alberti',
        rating: 5,
        time: '2 ore fa',
        snippet: 'Soggiorno impeccabile! Camera spaziosa...',
        fullText: 'Soggiorno impeccabile! Camera spaziosa, silenziosa e colazione a buffet straordinaria. Staff sempre gentile e disponibile. Torneremo sicuramente!',
      },
      rev2: {
        initials: 'LR',
        author: 'Laura Rinaldi',
        rating: 5,
        time: '1 giorno fa',
        snippet: 'Posizione fantastica e accoglienza calorosa...',
      },
      rev3: {
        initials: 'GM',
        author: 'Giovanni Moretti',
        rating: 4,
        time: '2 giorni fa',
        snippet: 'Ottima struttura, camera pulita e...',
      },
      rev4: {
        initials: 'EB',
        author: 'Elena Bianchi',
        rating: 5,
        time: '3 giorni fa',
        snippet: 'Tutto perfetto, dalla camera alla colazione!',
      },
      aiDraft: 'Grazie mille, Marco! Siamo felici che abbia apprezzato il comfort delle nostre camere e la colazione a buffet. Sarà un vero piacere riaccoglierla presto nel nostro hotel!',
    },
    barbieri: {
      userRole: 'Titolare Salone',
      userEmail: 'barber.demo@melpis.it',
      descSuffix: 'del tuo barber shop.',
      rev1: {
        initials: 'MR',
        author: 'Matteo Ricci',
        rating: 5,
        time: '2 ore fa',
        snippet: 'Taglio impeccabile e barba curata...',
        fullText: 'Taglio impeccabile e barba curata nei minimi dettagli. Puntualità perfetta, atmosfera professionale e grande competenza. Il miglior barbiere in città!',
      },
      rev2: {
        initials: 'DS',
        author: 'Davide Serra',
        rating: 5,
        time: '1 giorno fa',
        snippet: 'Ambiente curato, grande professionalità...',
      },
      rev3: {
        initials: 'AC',
        author: 'Andrea Conti',
        rating: 4,
        time: '2 giorni fa',
        snippet: 'Ottima cura della barba e prodotti...',
      },
      rev4: {
        initials: 'LF',
        author: 'Luca Ferri',
        rating: 5,
        time: '3 giorni fa',
        snippet: 'Esperienza eccellente, sfumatura perfetta!',
      },
      aiDraft: 'Grazie mille, Matteo! La precisione e la soddisfazione dei nostri clienti sono sempre al primo posto. Ci vediamo presto per il prossimo appuntamento!',
    },
    saloni: {
      userRole: 'Responsabile Centro',
      userEmail: 'estetica.demo@melpis.it',
      descSuffix: 'del tuo centro estetico.',
      rev1: {
        initials: 'CV',
        author: 'Chiara Valenti',
        rating: 5,
        time: '2 ore fa',
        snippet: 'Trattamento viso eccezionale! Pelle luminosa...',
        fullText: 'Trattamento viso eccezionale! Pelle luminosa e rilassata. Personale preparatissimo, accogliente e massima attenzione all\'igiene. Un vero momento di benessere!',
      },
      rev2: {
        initials: 'SM',
        author: 'Silvia Morandi',
        rating: 5,
        time: '1 giorno fa',
        snippet: 'Ambiente raffinato, pulizia impeccabile...',
      },
      rev3: {
        initials: 'GC',
        author: 'Giulia Colombo',
        rating: 4,
        time: '2 giorni fa',
        snippet: 'Ottima manicure semipermanente, molto...',
      },
      rev4: {
        initials: 'FR',
        author: 'Francesca Rossi',
        rating: 5,
        time: '3 giorni fa',
        snippet: 'Trattamenti di livello superiore, tutto perfetto!',
      },
      aiDraft: 'Grazie di cuore, Chiara! Siamo davvero felici che tu ti sia sentita coccolata e che i risultati del trattamento viso ti abbiano soddisfatta. Ti aspettiamo presto per il tuo prossimo momento di relax!',
    },
  };

  const cfg = configs[variant] || configs.restaurants;

  return (
    <div className="mv2-rest-reviews-stage" aria-label="Dashboard dimostrativa Recensioni Google">
      <div className="mv2-rest-reviews-card">
        {/* ==================================================================
            A. SIDEBAR AUTENTICA DASHBOARD MELPIS
            ================================================================== */}
        <aside className="mv2-rest-reviews-sidebar">
          {/* Logo Ufficiale Melpis con simbolo geometrico autentico */}
          <div className="mv2-rest-reviews-brand">
            <span className="mv2-rest-reviews-brand-mark" aria-hidden="true">
              <svg width="20" height="15" viewBox="0 0 200 144" fill="none" focusable="false">
                <defs>
                  <linearGradient id="revSymLeft" x1="10" y1="16" x2="100" y2="132" gradientUnits="userSpaceOnUse">
                    <stop stopColor="#64748B" />
                    <stop offset="0.48" stopColor="#94A3B8" />
                    <stop offset="1" stopColor="#475569" />
                  </linearGradient>
                  <linearGradient id="revSymSilver" x1="44" y1="22" x2="151" y2="117" gradientUnits="userSpaceOnUse">
                    <stop stopColor="#CBD5E1" />
                    <stop offset="0.48" stopColor="#FFFFFF" />
                    <stop offset="1" stopColor="#94A3B8" />
                  </linearGradient>
                  <linearGradient id="revSymRight" x1="118" y1="14" x2="190" y2="132" gradientUnits="userSpaceOnUse">
                    <stop stopColor="#FFFFFF" />
                    <stop offset="0.54" stopColor="#CBD5E1" />
                    <stop offset="1" stopColor="#64748B" />
                  </linearGradient>
                </defs>
                <path
                  d="M17 8h10.8c4.3 0 8.4 1.5 11.7 4.2L100 59l-17.5 15.2a9 9 0 0 1-11.4.2L35 47.6v82.1a9 9 0 0 1-9 9h-9a9 9 0 0 1-9-9V25A17 17 0 0 1 17 8Z"
                  fill="url(#revSymLeft)"
                />
                <path
                  d="m38.5 47.2 42.6 33.5a9 9 0 0 0 11.2-.2L100 74l20.5 16.1a9 9 0 0 0 11.2-.2L162 65.8v63.9a9 9 0 0 1-9 9h-9a9 9 0 0 1-9-9V94.1l-21.4 16.8a9 9 0 0 1-11.2 0L38.5 63.8V47.2Z"
                  fill="url(#revSymSilver)"
                />
                <path
                  d="M183 8h-10.8c-4.3 0-8.4 1.5-11.7 4.2L100 59l17.5 15.2a9 9 0 0 0 11.4.2L165 47.6v82.1a9 9 0 0 0 9 9h9a9 9 0 0 0 9-9V25a17 17 0 0 0-17-17Z"
                  fill="url(#revSymRight)"
                />
              </svg>
            </span>
            <span className="mv2-rest-reviews-brand-name">melpis</span>
          </div>

          {/* Navigazione divisa nei due gruppi ufficiali Melpis */}
          <nav className="mv2-rest-reviews-nav">
            {/* Gruppo 1: Principale */}
            <div className="mv2-rest-reviews-nav-group">
              <span className="mv2-rest-reviews-nav-label">Principale</span>

              <div className="mv2-rest-reviews-nav-item">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <rect x="3" y="3" width="7" height="7" rx="1" />
                  <rect x="14" y="3" width="7" height="7" rx="1" />
                  <rect x="3" y="14" width="7" height="7" rx="1" />
                  <rect x="14" y="14" width="7" height="7" rx="1" />
                </svg>
                <span>Panoramica</span>
              </div>

              <div className="mv2-rest-reviews-nav-item">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                <span>Inbox</span>
                <span className="mv2-rest-reviews-nav-badge">4</span>
              </div>

              <div className="mv2-rest-reviews-nav-item">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <rect x="3" y="4" width="18" height="18" rx="2" />
                  <line x1="16" y1="2" x2="16" y2="6" />
                  <line x1="8" y1="2" x2="8" y2="6" />
                  <line x1="3" y1="10" x2="21" y2="10" />
                </svg>
                <span>Prenotazioni</span>
              </div>

              <div className="mv2-rest-reviews-nav-item mv2-rest-reviews-nav-item--active">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="#2B59FF" stroke="#2B59FF" strokeWidth="1.5" aria-hidden="true">
                  <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                </svg>
                <span>Recensioni</span>
              </div>

              <div className="mv2-rest-reviews-nav-item">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                  <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                </svg>
                <span>Team</span>
              </div>
            </div>

            {/* Gruppo 2: Assistente */}
            <div className="mv2-rest-reviews-nav-group">
              <span className="mv2-rest-reviews-nav-label">Assistente</span>

              <div className="mv2-rest-reviews-nav-item">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M12 2L14.4 9.6L22 12L14.4 14.4L12 22L9.6 14.4L2 12L9.6 9.6L12 2Z" />
                </svg>
                <span>Simulatore AI</span>
              </div>

              <div className="mv2-rest-reviews-nav-item">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                  <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                </svg>
                <span>Conoscenza</span>
              </div>

              <div className="mv2-rest-reviews-nav-item">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <line x1="4" y1="21" x2="4" y2="14" />
                  <line x1="4" y1="10" x2="4" y2="3" />
                  <line x1="12" y1="21" x2="12" y2="12" />
                  <line x1="12" y1="8" x2="12" y2="3" />
                  <line x1="20" y1="21" x2="20" y2="16" />
                  <line x1="20" y1="12" x2="20" y2="3" />
                  <line x1="1" y1="14" x2="7" y2="14" />
                  <line x1="9" y1="8" x2="15" y2="8" />
                  <line x1="17" y1="16" x2="23" y2="16" />
                </svg>
                <span>Configurazione</span>
              </div>
            </div>
          </nav>

          {/* Account Utente in basso */}
          <div className="mv2-rest-reviews-user">
            <span className="mv2-rest-reviews-user-avatar">{cfg.userRole.charAt(0)}</span>
            <div className="mv2-rest-reviews-user-info">
              <strong>{cfg.userEmail}</strong>
              <small>{cfg.userRole}</small>
            </div>
          </div>
        </aside>

        {/* ==================================================================
            B. AREA DI LAVORO PRINCIPALE (STRUTTURA MELPIS)
            ================================================================== */}
        <main className="mv2-rest-reviews-main">
          {/* Topbar di contesto con breadcrumb */}
          <div className="mv2-rest-reviews-topbar">
            <div className="mv2-rest-reviews-breadcrumbs">
              <span>melpis</span>
              <span className="mv2-rest-reviews-crumb-sep">/</span>
              <span>Reputazione</span>
              <span className="mv2-rest-reviews-crumb-sep">/</span>
              <strong className="mv2-rest-reviews-crumb-active">Recensioni Google</strong>
            </div>
          </div>

          {/* Header Row con Google G, Titoli e Metriche KPI */}
          <div className="mv2-rest-reviews-header">
            <div className="mv2-rest-reviews-title-group">
              <div className="mv2-rest-reviews-title-row">
                <span className="mv2-rest-reviews-google-icon" aria-hidden="true">
                  <svg width="22" height="22" viewBox="0 0 24 24" focusable="false">
                    <path
                      fill="#4285F4"
                      d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                    />
                    <path
                      fill="#34A853"
                      d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                    />
                    <path
                      fill="#FBBC05"
                      d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
                    />
                    <path
                      fill="#EA4335"
                      d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
                    />
                  </svg>
                </span>
                <h3 className="mv2-rest-reviews-title">Recensioni Google</h3>
              </div>
              <p className="mv2-rest-reviews-desc">
                Gestisci le recensioni, genera risposte con l'AI e<br className="mv2-rest-reviews-desc-br" />
                {cfg.descSuffix}
              </p>
            </div>

            {/* KPI Rating Widget */}
            <div className="mv2-rest-reviews-kpi">
              <div className="mv2-rest-reviews-kpi-score">
                <div className="mv2-rest-reviews-kpi-val-row">
                  <span className="mv2-rest-reviews-kpi-num">4.8</span>
                  <span className="mv2-rest-reviews-kpi-den">/5</span>
                </div>
                <div className="mv2-rest-reviews-stars" aria-hidden="true">
                  <StarIcon type="filled" />
                  <StarIcon type="filled" />
                  <StarIcon type="filled" />
                  <StarIcon type="filled" />
                  <StarIcon type="half" />
                </div>
              </div>
              <div className="mv2-rest-reviews-kpi-divider" aria-hidden="true" />
              <div className="mv2-rest-reviews-kpi-count">
                <span className="mv2-rest-reviews-kpi-count-num">328</span>
                <span className="mv2-rest-reviews-kpi-label">recensioni</span>
              </div>
            </div>
          </div>

          {/* Filter Bar Row */}
          <div className="mv2-rest-reviews-filters">
            <div className="mv2-rest-reviews-tabs-wrapper">
              <button
                type="button"
                className="mv2-rest-reviews-tab mv2-rest-reviews-tab--active"
              >
                Tutte (328)
              </button>
              <button type="button" className="mv2-rest-reviews-tab">
                Da rispondere (12)
              </button>
              <button type="button" className="mv2-rest-reviews-tab">
                Risposte inviate
              </button>
            </div>

            <div className="mv2-rest-reviews-sort">
              <span>Più recenti</span>
              <svg
                width="10"
                height="10"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </div>
          </div>

          {/* Two-Column Workspace Content */}
          <div className="mv2-rest-reviews-grid">
            {/* Sotto-colonna sinistra: Elenco recensioni */}
            <div className="mv2-rest-reviews-list">
              {/* Card 1 (Selezionata) */}
              <div className="mv2-rest-review-card mv2-rest-review-card--active">
                <span className="mv2-rest-review-active-dot" aria-hidden="true" />
                <div className="mv2-rest-review-card-inner">
                  <span className="mv2-rest-review-avatar">{cfg.rev1.initials}</span>
                  <div className="mv2-rest-review-content">
                    <span className="mv2-rest-review-author">{cfg.rev1.author}</span>
                    <div className="mv2-rest-review-rating-row">
                      <div className="mv2-rest-reviews-stars">
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                      </div>
                      <span className="mv2-rest-review-time">{cfg.rev1.time}</span>
                    </div>
                    <p className="mv2-rest-review-snippet">
                      {cfg.rev1.snippet}
                    </p>
                  </div>
                </div>
              </div>

              {/* Card 2 */}
              <div className="mv2-rest-review-card">
                <div className="mv2-rest-review-card-inner">
                  <span className="mv2-rest-review-avatar">{cfg.rev2.initials}</span>
                  <div className="mv2-rest-review-content">
                    <span className="mv2-rest-review-author">{cfg.rev2.author}</span>
                    <div className="mv2-rest-review-rating-row">
                      <div className="mv2-rest-reviews-stars">
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                      </div>
                      <span className="mv2-rest-review-time">{cfg.rev2.time}</span>
                    </div>
                    <p className="mv2-rest-review-snippet">
                      {cfg.rev2.snippet}
                    </p>
                  </div>
                </div>
              </div>

              {/* Card 3 */}
              <div className="mv2-rest-review-card">
                <div className="mv2-rest-review-card-inner">
                  <span className="mv2-rest-review-avatar">{cfg.rev3.initials}</span>
                  <div className="mv2-rest-review-content">
                    <span className="mv2-rest-review-author">{cfg.rev3.author}</span>
                    <div className="mv2-rest-review-rating-row">
                      <div className="mv2-rest-reviews-stars">
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="empty" />
                      </div>
                      <span className="mv2-rest-review-time">{cfg.rev3.time}</span>
                    </div>
                    <p className="mv2-rest-review-snippet">
                      {cfg.rev3.snippet}
                    </p>
                  </div>
                </div>
              </div>

              {/* Card 4 */}
              <div className="mv2-rest-review-card">
                <div className="mv2-rest-review-card-inner">
                  <span className="mv2-rest-review-avatar">{cfg.rev4.initials}</span>
                  <div className="mv2-rest-review-content">
                    <span className="mv2-rest-review-author">{cfg.rev4.author}</span>
                    <div className="mv2-rest-review-rating-row">
                      <div className="mv2-rest-reviews-stars">
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                        <StarIcon type="filled" />
                      </div>
                      <span className="mv2-rest-review-time">{cfg.rev4.time}</span>
                    </div>
                    <p className="mv2-rest-review-snippet">
                      {cfg.rev4.snippet}
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* Sotto-colonna destra: Dettaglio recensione selezionata + Risposta AI */}
            <div className="mv2-rest-reviews-detail">
              {/* Review completa selezionata */}
              <div className="mv2-rest-detail-card">
                <div className="mv2-rest-detail-header">
                  <div className="mv2-rest-detail-author-group">
                    <span className="mv2-rest-review-avatar mv2-rest-review-avatar--lg">{cfg.rev1.initials}</span>
                    <div className="mv2-rest-review-meta">
                      <span className="mv2-rest-review-author mv2-rest-review-author--detail">{cfg.rev1.author}</span>
                      <div className="mv2-rest-review-rating-row">
                        <div className="mv2-rest-reviews-stars">
                          <StarIcon type="filled" />
                          <StarIcon type="filled" />
                          <StarIcon type="filled" />
                          <StarIcon type="filled" />
                          <StarIcon type="filled" />
                        </div>
                        <span className="mv2-rest-review-time">{cfg.rev1.time}</span>
                      </div>
                    </div>
                  </div>

                  <span className="mv2-rest-detail-link-icon" aria-hidden="true">
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                      <polyline points="15 3 21 3 21 9" />
                      <line x1="10" y1="14" x2="21" y2="3" />
                    </svg>
                  </span>
                </div>

                <p className="mv2-rest-detail-text">
                  {cfg.rev1.fullText}
                </p>
              </div>

              {/* Card Risposta suggerita dall'AI */}
              <div className="mv2-rest-ai-reply-card">
                <div className="mv2-rest-ai-reply-header">
                  <div className="mv2-rest-ai-reply-label">
                    <span className="mv2-rest-ai-sparkle" aria-hidden="true">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="#2B59FF" focusable="false">
                        <path d="M12 2L14.4 9.6L22 12L14.4 14.4L12 22L9.6 14.4L2 12L9.6 9.6L12 2Z" />
                      </svg>
                    </span>
                    <span className="mv2-rest-ai-reply-title">Risposta suggerita dall'AI</span>
                  </div>
                  <span className="mv2-rest-ai-pill">Bozza AI</span>
                </div>

                <p className="mv2-rest-ai-reply-body">
                  {cfg.aiDraft}
                </p>

                <div className="mv2-rest-ai-reply-actions">
                  <button type="button" className="mv2-rest-ai-btn mv2-rest-ai-btn--ghost">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M12 20h9" />
                      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
                    </svg>
                    <span>Modifica</span>
                  </button>

                  <button type="button" className="mv2-rest-ai-btn mv2-rest-ai-btn--secondary">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                    <span>Approva</span>
                  </button>

                  <button type="button" className="mv2-rest-ai-btn mv2-rest-ai-btn--primary">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <line x1="22" y1="2" x2="11" y2="13" />
                      <polygon points="22 2 15 22 11 13 2 9 22 2" />
                    </svg>
                    <span>Invia</span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
