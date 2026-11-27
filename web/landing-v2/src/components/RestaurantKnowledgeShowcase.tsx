import React from 'react';

export interface RestaurantKnowledgeShowcaseProps {
  variant?: 'restaurants' | 'hotel' | 'barbieri' | 'saloni';
}

export default function RestaurantKnowledgeShowcase({
  variant = 'restaurants',
}: RestaurantKnowledgeShowcaseProps) {
  const configs = {
    restaurants: {
      userEmail: 'osteria.demo@melpis.it',
      userRole: 'Proprietario',
      docs: 12,
      pages: 8,
      faqs: 5,
      total: 25,
      row1: { title: 'Menu alla carta & allergeni', meta: 'Documento PDF (2.1 MB) • Aggiornato 10 ott 2026' },
      row2: { title: 'Orari della cucina e turni sala', meta: 'Pagina web • Sincronizzato ieri alle 19:30' },
      row3: { title: 'Regole prenotazioni & gruppi oltre 8 persone', meta: 'FAQ • 14 risposte collegate • Verificato da Staff' },
      row4: { title: 'Politica cancellazione e richieste speciali sala', meta: 'Documento PDF (620 KB) • Aggiornato 5 ott 2026' },
    },
    hotel: {
      userEmail: 'reception.demo@melpis.it',
      userRole: 'Direzione Hotel',
      docs: 14,
      pages: 6,
      faqs: 8,
      total: 28,
      row1: { title: 'Guida ospiti, check-in e dotazioni camere', meta: 'Documento PDF (3.4 MB) • Aggiornato 12 ott 2026' },
      row2: { title: 'Orari colazione a buffet, spa & servizi', meta: 'Pagina web • Sincronizzato ieri alle 18:00' },
      row3: { title: 'Regolamento struttura, arrivi notturni & parcheggio', meta: 'FAQ • 18 risposte collegate • Verificato da Reception' },
      row4: { title: 'Listino tariffe camere, tasse di soggiorno & transfer', meta: 'Documento PDF (1.8 MB) • Aggiornato 8 ott 2026' },
    },
    barbieri: {
      userEmail: 'barber.demo@melpis.it',
      userRole: 'Titolare Salone',
      docs: 9,
      pages: 4,
      faqs: 6,
      total: 19,
      row1: { title: 'Listino tagli, modellatura barba & trattamenti', meta: 'Documento PDF (1.2 MB) • Aggiornato 14 ott 2026' },
      row2: { title: 'Orari salone e gestione poltrone', meta: 'Pagina web • Sincronizzato ieri alle 20:00' },
      row3: { title: 'Regole appuntamenti, ritardi & disdette', meta: 'FAQ • 12 risposte collegate • Verificato da Titolare' },
      row4: { title: 'Trattamenti cute, prodotti styling & panno caldo', meta: 'Documento PDF (850 KB) • Aggiornato 6 ott 2026' },
    },
    saloni: {
      userEmail: 'estetica.demo@melpis.it',
      userRole: 'Responsabile Centro',
      docs: 11,
      pages: 5,
      faqs: 7,
      total: 23,
      row1: { title: 'Listino trattamenti viso, corpo & cabina', meta: 'Documento PDF (2.5 MB) • Aggiornato 11 ott 2026' },
      row2: { title: 'Protocolli servizi, durate & tempi di posa', meta: 'Pagina web • Sincronizzato ieri alle 19:00' },
      row3: { title: 'Regole prenotazioni, percorsi & disdette', meta: 'FAQ • 16 risposte collegate • Verificato da Staff' },
      row4: { title: 'Schede prodotti cosmetici & autocura a casa', meta: 'Documento PDF (1.6 MB) • Aggiornato 9 ott 2026' },
    },
  };

  const cfg = configs[variant] || configs.restaurants;

  return (
    <div className="mv2-rest-kb-stage" aria-hidden="true">
      <div className="mv2-rest-kb-dashboard">
        {/* Sidebar */}
        <aside className="mv2-rest-kb-sidebar">
          <div className="mv2-rest-kb-brand">
            <span className="mv2-rest-kb-brand-mark">
              <svg width="18" height="13" viewBox="0 0 36 26" fill="none" aria-hidden="true" focusable="false">
                <path
                  d="M4 22V4L13 14L22 4V22"
                  stroke="#07090e"
                  strokeWidth="3.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <span className="mv2-rest-kb-brand-name">melpis</span>
          </div>

          <div className="mv2-rest-kb-nav-group">
            <div className="mv2-rest-kb-nav-label">Principale</div>

            <div className="mv2-rest-kb-nav-item">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="3" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="3" width="7" height="7" rx="1" />
                <rect x="3" y="14" width="7" height="7" rx="1" />
                <rect x="14" y="14" width="7" height="7" rx="1" />
              </svg>
              <span>Panoramica</span>
            </div>

            <div className="mv2-rest-kb-nav-item">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
              <span>Inbox</span>
              <span className="mv2-rest-kb-nav-badge">4</span>
            </div>

            <div className="mv2-rest-kb-nav-item">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="3" y="4" width="18" height="18" rx="2" />
                <line x1="16" y1="2" x2="16" y2="6" />
                <line x1="8" y1="2" x2="8" y2="6" />
                <line x1="3" y1="10" x2="21" y2="10" />
              </svg>
              <span>Prenotazioni</span>
            </div>

            <div className="mv2-rest-kb-nav-item">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
              </svg>
              <span>Recensioni</span>
            </div>

            <div className="mv2-rest-kb-nav-item">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                <path d="M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
              <span>Team &amp; Collaboratori</span>
            </div>
          </div>

          <div className="mv2-rest-kb-nav-group">
            <div className="mv2-rest-kb-nav-label">Assistente</div>

            <div className="mv2-rest-kb-nav-item">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                <path d="M8 9h8M8 13h5" />
              </svg>
              <span>Simulatore AI</span>
            </div>

            {/* Selected Knowledge Tab with ultramarine accent */}
            <div className="mv2-rest-kb-nav-item mv2-rest-kb-nav-item--active">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
              </svg>
              <span>Conoscenza</span>
            </div>

            <div className="mv2-rest-kb-nav-item">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="4" y="8" width="16" height="12" rx="2" />
                <path d="M12 4v4" />
                <circle cx="12" cy="4" r="1" />
                <path d="M9 13h.01M15 13h.01M9 17h6" />
              </svg>
              <span>Configurazione AI</span>
            </div>
          </div>

          <div className="mv2-rest-kb-user">
            <span className="mv2-rest-kb-user-avatar">{cfg.userRole.charAt(0)}</span>
            <div className="mv2-rest-kb-user-info">
              <strong>{cfg.userEmail}</strong>
              <small>Ruolo: {cfg.userRole}</small>
            </div>
          </div>
        </aside>

        {/* Main Dashboard Panel */}
        <div className="mv2-rest-kb-main">
          {/* Breadcrumbs */}
          <div className="mv2-rest-kb-breadcrumbs">
            <span>melpis</span>
            <span className="mv2-rest-kb-sep">&gt;</span>
            <strong>Documenti &amp; Conoscenza</strong>
          </div>

          <h3 className="mv2-rest-kb-title">Documenti &amp; Conoscenza</h3>
          <p className="mv2-rest-kb-subtitle">
            Aggiungi e gestisci le informazioni che Melpis utilizza per rispondere ai clienti.
          </p>

          {/* 3 KPI Summary Cards */}
          <div className="mv2-rest-kb-kpis">
            <div className="mv2-rest-kb-kpi-card">
              <div className="mv2-rest-kb-kpi-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="2" aria-hidden="true">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                </svg>
              </div>
              <div className="mv2-rest-kb-kpi-copy">
                <span className="mv2-rest-kb-kpi-val">{cfg.docs}</span>
                <span className="mv2-rest-kb-kpi-label">Documenti</span>
              </div>
            </div>

            <div className="mv2-rest-kb-kpi-card">
              <div className="mv2-rest-kb-kpi-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="2" aria-hidden="true">
                  <circle cx="12" cy="12" r="9" />
                  <line x1="3.6" y1="9" x2="20.4" y2="9" />
                  <line x1="3.6" y1="15" x2="20.4" y2="15" />
                  <path d="M12 3a16 16 0 0 0 0 18" />
                </svg>
              </div>
              <div className="mv2-rest-kb-kpi-copy">
                <span className="mv2-rest-kb-kpi-val">{cfg.pages}</span>
                <span className="mv2-rest-kb-kpi-label">Pagine web</span>
              </div>
            </div>

            <div className="mv2-rest-kb-kpi-card">
              <div className="mv2-rest-kb-kpi-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="2" aria-hidden="true">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
              </div>
              <div className="mv2-rest-kb-kpi-copy">
                <span className="mv2-rest-kb-kpi-val">{cfg.faqs}</span>
                <span className="mv2-rest-kb-kpi-label">FAQ</span>
              </div>
            </div>
          </div>

          {/* Search Row */}
          <div className="mv2-rest-kb-search-row">
            <div className="mv2-rest-kb-search-box">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="2" aria-hidden="true">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <span className="mv2-rest-kb-search-placeholder">Cerca documenti, pagine o FAQ...</span>
            </div>
            <div className="mv2-rest-kb-filter-btn">
              <span>Filtri</span>
              <span aria-hidden="true">&gt;</span>
            </div>
          </div>

          {/* Categories / Tabs */}
          <div className="mv2-rest-kb-tabs-row">
            <span className="mv2-rest-kb-tab-btn mv2-rest-kb-tab-btn--active">Tutti ({cfg.total})</span>
            <span className="mv2-rest-kb-tab-btn">Documenti ({cfg.docs})</span>
            <span className="mv2-rest-kb-tab-btn">Pagine web ({cfg.pages})</span>
            <span className="mv2-rest-kb-tab-btn">FAQ ({cfg.faqs})</span>
            <span className="mv2-rest-kb-tab-btn">Dati strutturati (0)</span>
          </div>

          {/* Knowledge Document Rows Contextualized */}
          <div className="mv2-rest-kb-list">
            {/* Row 1 */}
            <div className="mv2-rest-kb-row">
              <div className="mv2-rest-kb-row-left">
                <div className="mv2-rest-kb-icon-box mv2-rest-kb-icon--green">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                    <line x1="8" y1="13" x2="16" y2="13" />
                    <line x1="8" y1="17" x2="13" y2="17" />
                  </svg>
                </div>
                <div className="mv2-rest-kb-row-copy">
                  <div className="mv2-rest-kb-row-title">
                    <span>{cfg.row1.title}</span>
                  </div>
                  <div className="mv2-rest-kb-row-meta">{cfg.row1.meta}</div>
                </div>
              </div>
              <div className="mv2-rest-kb-row-right">
                <div className="mv2-rest-kb-switch mv2-rest-kb-switch--on">
                  <span className="mv2-rest-kb-switch-thumb" />
                </div>
              </div>
            </div>

            {/* Row 2 */}
            <div className="mv2-rest-kb-row">
              <div className="mv2-rest-kb-row-left">
                <div className="mv2-rest-kb-icon-box mv2-rest-kb-icon--blue">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#1a5cff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="9" />
                    <line x1="3.6" y1="9" x2="20.4" y2="9" />
                    <line x1="3.6" y1="15" x2="20.4" y2="15" />
                    <path d="M11.5 3a17 17 0 0 0 0 18" />
                  </svg>
                </div>
                <div className="mv2-rest-kb-row-copy">
                  <div className="mv2-rest-kb-row-title">
                    <span>{cfg.row2.title}</span>
                  </div>
                  <div className="mv2-rest-kb-row-meta">{cfg.row2.meta}</div>
                </div>
              </div>
              <div className="mv2-rest-kb-row-right">
                <div className="mv2-rest-kb-switch mv2-rest-kb-switch--on">
                  <span className="mv2-rest-kb-switch-thumb" />
                </div>
              </div>
            </div>

            {/* Row 3 */}
            <div className="mv2-rest-kb-row">
              <div className="mv2-rest-kb-row-left">
                <div className="mv2-rest-kb-icon-box mv2-rest-kb-icon--purple">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#8b5cf6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                    <path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 1.5-2.5 2-2.5 3" />
                  </svg>
                </div>
                <div className="mv2-rest-kb-row-copy">
                  <div className="mv2-rest-kb-row-title">
                    <span>{cfg.row3.title}</span>
                  </div>
                  <div className="mv2-rest-kb-row-meta">{cfg.row3.meta}</div>
                </div>
              </div>
              <div className="mv2-rest-kb-row-right">
                <div className="mv2-rest-kb-switch mv2-rest-kb-switch--on">
                  <span className="mv2-rest-kb-switch-thumb" />
                </div>
              </div>
            </div>

            {/* Row 4 */}
            <div className="mv2-rest-kb-row">
              <div className="mv2-rest-kb-row-left">
                <div className="mv2-rest-kb-icon-box mv2-rest-kb-icon--slate">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                    <line x1="8" y1="13" x2="16" y2="13" />
                  </svg>
                </div>
                <div className="mv2-rest-kb-row-copy">
                  <div className="mv2-rest-kb-row-title">
                    <span>{cfg.row4.title}</span>
                  </div>
                  <div className="mv2-rest-kb-row-meta">{cfg.row4.meta}</div>
                </div>
              </div>
              <div className="mv2-rest-kb-row-right">
                <div className="mv2-rest-kb-switch mv2-rest-kb-switch--off">
                  <span className="mv2-rest-kb-switch-thumb" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
