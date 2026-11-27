import React, { useState, useEffect, useRef } from 'react';

interface ScenarioItem {
  id: string;
  title: string;
  description: string;
}

const SCENARIOS: ScenarioItem[] = [
  {
    id: 'concierge',
    title: 'Concierge digitale H24',
    description:
      'Risponde istantaneamente alle domande degli ospiti: orari di check-in e check-out, colazione, Wi-Fi, parcheggio, deposito bagagli e servizi della struttura.',
  },
  {
    id: 'richieste-soggiorno',
    title: 'Richieste di soggiorno e preventivi',
    description:
      'Raccoglie date del soggiorno, numero di ospiti, tipologia di camera ed eventuali preferenze, trasferendo la richiesta completa allo staff senza improvvisare conferme.',
  },
  {
    id: 'richieste-speciali',
    title: 'Richieste speciali e intervento reception',
    description:
      'Per late check-in, culle, arrivi anticipati o modifiche di prenotazione, Melpis raccoglie i dettagli e notifica immediatamente il personale della reception.',
  },
];

/* --------------------------------------------------------------------------
   VISTA 01: Inbox a 3 Colonne Dark (Concierge H24 - Check-in & Bagagli)
   -------------------------------------------------------------------------- */
function Vista01Inbox() {
  return (
    <div className="mv2-scen-inbox-shell theme-dark">
      {/* Colonna 1: Lista conversazioni */}
      <div className="mv2-scen-inbox-list-col">
        <div className="mv2-scen-inbox-header">
          <div className="mv2-scen-inbox-title-row">
            <span className="mv2-scen-inbox-heading">Inbox Reception</span>
            <span className="mv2-scen-inbox-count-badge">5</span>
          </div>

          <div className="mv2-scen-inbox-search-box">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <span>Cerca ospite...</span>
          </div>
        </div>

        {/* Lista thread */}
        <div className="mv2-scen-inbox-threads">
          {/* Thread 1: Luca Moretti (Attivo) */}
          <div className="mv2-scen-thread-item is-active">
            <span className="mv2-scen-avatar">LM</span>
            <div className="mv2-scen-thread-content">
              <div className="mv2-scen-thread-top">
                <strong className="mv2-scen-thread-name">Luca Moretti</strong>
                <span className="mv2-scen-thread-time">2 min</span>
              </div>
              <div className="mv2-scen-thread-badges">
                <span className="mv2-scen-badge-sub">WhatsApp · AI Concierge</span>
              </div>
              <p className="mv2-scen-thread-snippet">A che ora possiamo fare check-in?</p>
            </div>
          </div>

          {/* Thread 2: Elena Rinaldi */}
          <div className="mv2-scen-thread-item">
            <span className="mv2-scen-avatar">ER</span>
            <div className="mv2-scen-thread-content">
              <div className="mv2-scen-thread-top">
                <strong className="mv2-scen-thread-name">Elena Rinaldi</strong>
                <span className="mv2-scen-thread-time">8 min</span>
              </div>
              <div className="mv2-scen-thread-badges">
                <span className="mv2-scen-badge-sub">WhatsApp · AI</span>
              </div>
              <p className="mv2-scen-thread-snippet">La colazione è inclusa nel prezzo?</p>
            </div>
          </div>

          {/* Thread 3: Thomas Weber */}
          <div className="mv2-scen-thread-item">
            <span className="mv2-scen-avatar">TW</span>
            <div className="mv2-scen-thread-content">
              <div className="mv2-scen-thread-top">
                <strong className="mv2-scen-thread-name">Thomas Weber</strong>
                <span className="mv2-scen-thread-time">21 min</span>
              </div>
              <div className="mv2-scen-thread-badges">
                <span className="mv2-scen-badge-sub">Booking · AI</span>
              </div>
              <p className="mv2-scen-thread-snippet">Do you have on-site parking?</p>
            </div>
          </div>

          {/* Thread 4: Giulia Conti */}
          <div className="mv2-scen-thread-item">
            <span className="mv2-scen-avatar">GC</span>
            <div className="mv2-scen-thread-content">
              <div className="mv2-scen-thread-top">
                <strong className="mv2-scen-thread-name">Giulia Conti</strong>
                <span className="mv2-scen-thread-time">35 min</span>
              </div>
              <div className="mv2-scen-thread-badges">
                <span className="mv2-scen-badge-sub">WhatsApp · Reception</span>
              </div>
              <p className="mv2-scen-thread-snippet">Richiesta culla in camera 208</p>
            </div>
          </div>

          {/* Thread 5: David Smith */}
          <div className="mv2-scen-thread-item">
            <span className="mv2-scen-avatar">DS</span>
            <div className="mv2-scen-thread-content">
              <div className="mv2-scen-thread-top">
                <strong className="mv2-scen-thread-name">David Smith</strong>
                <span className="mv2-scen-thread-time">1 h</span>
              </div>
              <div className="mv2-scen-thread-badges">
                <span className="mv2-scen-badge-sub">WhatsApp · AI</span>
              </div>
              <p className="mv2-scen-thread-snippet">What time does the spa open?</p>
            </div>
          </div>
        </div>
      </div>

      {/* Colonna 2: Chat aperta con Luca Moretti */}
      <div className="mv2-scen-inbox-chat-col">
        <div className="mv2-scen-chat-head">
          <span className="mv2-scen-avatar">LM</span>
          <div className="mv2-scen-chat-identity">
            <strong className="mv2-scen-chat-name">Luca Moretti</strong>
            <small className="mv2-scen-chat-sub">+39 347 889 0123</small>
          </div>
          <div className="mv2-scen-chat-head-tags">
            <span className="mv2-scen-pill-green">WhatsApp</span>
            <span className="mv2-scen-pill-gray">AI Concierge</span>
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
          {/* Ospite */}
          <div className="mv2-scen-bubble-row is-client">
            <div className="mv2-scen-bubble is-client">
              Buonasera, a che ora possiamo effettuare il check-in?
            </div>
          </div>

          {/* Melpis */}
          <div className="mv2-scen-bubble-row is-ai">
            <div className="mv2-scen-bubble is-ai">
              Il check-in è disponibile dalle 15:00. Se prevedete di arrivare prima, posso raccogliere la vostra richiesta e segnalarla alla reception.
            </div>
          </div>

          {/* Ospite */}
          <div className="mv2-scen-bubble-row is-client">
            <div className="mv2-scen-bubble is-client">
              Arriveremo alle 12:30. È possibile lasciare i bagagli?
            </div>
          </div>

          {/* Melpis con Allegato */}
          <div className="mv2-scen-bubble-row is-ai">
            <div className="mv2-scen-bubble is-ai">
              <p className="mv2-scen-mb-0-8">
                Certamente, la struttura dispone di un deposito bagagli gratuito. Potete utilizzarlo prima del check-in.
              </p>
              <div className="mv2-scen-pdf-card">
                <div className="mv2-scen-pdf-left">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                    <line x1="16" y1="13" x2="8" y2="13" />
                    <line x1="16" y1="17" x2="8" y2="17" />
                  </svg>
                  <span className="mv2-scen-pdf-title">Guida Servizi &amp; Orari Struttura (PDF)</span>
                </div>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <line x1="7" y1="17" x2="17" y2="7" />
                  <polyline points="7 7 17 7 17 17" />
                </svg>
              </div>
            </div>
          </div>

          {/* Ospite */}
          <div className="mv2-scen-bubble-row is-client">
            <div className="mv2-scen-bubble is-client">
              Perfetto, grazie mille! C'è anche parcheggio disponibile?
            </div>
          </div>

          {/* Melpis */}
          <div className="mv2-scen-bubble-row is-ai">
            <div className="mv2-scen-bubble is-ai">
              Sì, disponiamo di un parcheggio privato coperto e videosorvegliato all'interno della struttura, accessibile 24 ore su 24.
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
        <div className="mv2-scen-details-title">Scheda Ospite</div>

        <div className="mv2-scen-details-section">
          <span className="mv2-scen-details-label">INFORMAZIONI</span>
          <dl className="mv2-scen-details-dl">
            <div className="mv2-scen-details-row">
              <dt>Ospite</dt>
              <dd>Luca Moretti</dd>
            </div>
            <div className="mv2-scen-details-row">
              <dt>Canale</dt>
              <dd>WhatsApp</dd>
            </div>
            <div className="mv2-scen-details-row">
              <dt>Stato</dt>
              <dd>AI Concierge</dd>
            </div>
            <div className="mv2-scen-details-row">
              <dt>Tag</dt>
              <dd>Check-in / Deposito</dd>
            </div>
            <div className="mv2-scen-details-row">
              <dt>Struttura</dt>
              <dd>Hotel &amp; Resort</dd>
            </div>
          </dl>
        </div>

        <div className="mv2-scen-details-section">
          <span className="mv2-scen-details-label">NOTE RECEPTION</span>
          <p className="mv2-scen-details-notes">
            Arrivo previsto ore 12:30 con richiesta deposito bagagli. Camera pronta per le 15:00.
          </p>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   VISTA 02: Richieste di soggiorno e date (Hotel)
   -------------------------------------------------------------------------- */
function Vista02Soggiorno() {
  return (
    <div className="mv2-scen-cal-shell theme-dark">
      <div className="mv2-scen-cal-topbar">
        <div className="mv2-scen-cal-view-toggle">
          <span className="mv2-scen-cal-toggle-pill is-active">Richiesta Soggiorno</span>
          <span className="mv2-scen-cal-toggle-pill">Disponibilità Periodo</span>
        </div>
        <div className="mv2-scen-cal-status-tag">
          <span className="mv2-scen-cal-dot" />
          <span>In verifica reception</span>
        </div>
      </div>

      <div className="mv2-scen-cal-main-grid">
        {/* Sinistra: Parametri soggiorno raccolti da Melpis */}
        <div className="mv2-scen-cal-slots-panel">
          <div className="mv2-scen-cal-panel-title">Dati soggiorno raccolti dall'assistente</div>

          <div className="mv2-scen-hotel-params-grid">
            <div className="mv2-scen-hotel-param-card">
              <span className="mv2-scen-hotel-param-label">Check-in / Out</span>
              <div className="mv2-scen-hotel-param-val">14–17 Giu (3 notti)</div>
            </div>

            <div className="mv2-scen-hotel-param-card">
              <span className="mv2-scen-hotel-param-label">Ospiti</span>
              <div className="mv2-scen-hotel-param-val">2 adulti + 1 bimbo</div>
            </div>

            <div className="mv2-scen-hotel-param-card">
              <span className="mv2-scen-hotel-param-label">Camera richiesta</span>
              <div className="mv2-scen-hotel-param-val">Matrimoniale Comfort</div>
            </div>

            <div className="mv2-scen-hotel-param-card">
              <span className="mv2-scen-hotel-param-label">Trattamento</span>
              <div className="mv2-scen-hotel-param-val">B&amp;B con colazione</div>
            </div>
          </div>

          <div className="mv2-scen-cal-slot is-selected mv2-scen-mb-8">
            <div className="mv2-scen-slot-time">Periodo: 14–17 Giugno</div>
            <div className="mv2-scen-slot-info">
              <span className="mv2-scen-slot-tag is-free">Camere da verificare a PMS</span>
              <span className="mv2-scen-slot-seats">Richiesta inviata a booking desk</span>
            </div>
          </div>

          <p className="mv2-scen-slot-note">
            Nota trasparenza: Melpis non inventa disponibilità inesistenti. Raccoglie tutti i requisiti e li trasferisce al tuo staff per la conferma finale.
          </p>
        </div>

        {/* Destra: Scheda riepilogativa preventivo */}
        <div className="mv2-scen-cal-summary-card">
          <div className="mv2-scen-summary-kicker">Riepilogo preventivo</div>
          <h4 className="mv2-scen-summary-title">Richiesta soggiorno da staff</h4>

          <dl className="mv2-scen-summary-dl">
            <div className="mv2-scen-summary-row">
              <dt>Ospite</dt>
              <dd>Andrea Valli</dd>
            </div>
            <div className="mv2-scen-summary-row">
              <dt>Contatto</dt>
              <dd>+39 333 456 7890</dd>
            </div>
            <div className="mv2-scen-summary-row">
              <dt>Periodo</dt>
              <dd>14–17 Giu (3 notti)</dd>
            </div>
            <div className="mv2-scen-summary-row">
              <dt>Preferenze</dt>
              <dd>Piano alto, culla in camera</dd>
            </div>
          </dl>

          <div className="mv2-scen-summary-badge">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
              <polyline points="20 6 9 17 4 12" />
            </svg>
            <span>Dettagli inoltrati alla reception</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   VISTA 03: Richieste speciali e intervento umano (Hotel)
   -------------------------------------------------------------------------- */
function Vista03Escalation() {
  return (
    <div className="mv2-scen-esc-shell theme-dark">
      <div className="mv2-scen-esc-banner">
        <div className="mv2-scen-esc-badge">
          <span className="mv2-scen-esc-pulse" />
          <span>Richiesta speciale · Richiede intervento reception</span>
        </div>
        <p className="mv2-scen-esc-desc">
          L'ospite richiede un late check-in notturno e servizi extra. Melpis notifica il portiere per presa in carico manuale.
        </p>
      </div>

      <div className="mv2-scen-esc-cards-row">
        {/* Card 1: Dettaglio Richiesta speciale */}
        <div className="mv2-scen-esc-card">
          <div className="mv2-scen-esc-card-kicker">Dettagli ospite</div>
          <h4 className="mv2-scen-esc-card-title">Elena Rinaldi · Camera 208</h4>

          <dl className="mv2-scen-esc-dl">
            <div className="mv2-scen-esc-row">
              <dt>Arrivo previsto</dt>
              <dd>Oggi, ore 23:45 (Volo in ritardo)</dd>
            </div>
            <div className="mv2-scen-esc-row">
              <dt>Dotazione richiesta</dt>
              <dd>Culla neonato + bollitore tisane</dd>
            </div>
            <div className="mv2-scen-esc-row">
              <dt>Canale</dt>
              <dd>WhatsApp Business</dd>
            </div>
            <div className="mv2-scen-esc-row">
              <dt>Stato</dt>
              <dd className="mv2-scen-highlight-blue">In attesa operatore</dd>
            </div>
          </dl>
        </div>

        {/* Card 2: Azione Reception */}
        <div className="mv2-scen-esc-card is-highlight">
          <div className="mv2-scen-esc-card-kicker">Azione staff reception</div>
          <h4 className="mv2-scen-esc-card-title">Presa in carico portiere</h4>

          <p className="mv2-scen-esc-card-body">
            Il portiere di notte riceve la segnalazione prioritaria per predisporre le chiavi e confermare la culla pronta in camera.
          </p>

          <div className="mv2-scen-esc-actions">
            <button type="button" className="mv2-scen-esc-btn is-approve">
              Conferma late check-in
            </button>
            <button type="button" className="mv2-scen-esc-btn is-chat">
              Entra nella chat WhatsApp
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   MAIN SHOWCASE COMPONENT WITH SCROLL ANIMATION
   -------------------------------------------------------------------------- */
export interface HotelScenariosShowcaseProps {
  children?: React.ReactNode;
}

export default function HotelScenariosShowcase({ children }: HotelScenariosShowcaseProps) {
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
    if (typeof window === 'undefined') return undefined;

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

    if (!track || !sticky || !layer1 || !layer2 || !layer3) return undefined;

    let rafId: number | null = null;
    let ticking = false;

    const isMobile = () => window.innerWidth <= 960;
    const prefersReduced = () =>
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const smootherstep = (edge0: number, edge1: number, x: number) => {
      const v = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
      return v * v * v * (v * (v * 6 - 15) + 10);
    };

    const applyProgress = (p: number) => {
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

      const stickyTopOffset = parseFloat(window.getComputedStyle(sticky).top) || 84;
      const scrolled = stickyTopOffset - trackRect.top;
      const progress = Math.max(0, Math.min(1, scrolled / totalScrollDistance));

      applyProgress(progress);

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
        {children}

        <div className="mv2-rest-scenarios-grid">
          {/* Left Column: Interactive Dark Window */}
          <div className="mv2-rest-scenarios-window-col">
            <div className="mv2-rest-mockup-frame mv2-rest-mockup-frame--scenario">
              <div className="mv2-rest-mockup-canvas mv2-rest-mockup-canvas--scenario">
                <div
                  ref={layer1Ref}
                  className={`mv2-rest-scenario-layer mv2-rest-scenario-layer--1 ${mobileActiveIndex === 0 ? 'is-mobile-active' : ''}`}
                  id={`scenario-panel-${SCENARIOS[0].id}`}
                  role="tabpanel"
                  aria-labelledby={`scenario-tab-${SCENARIOS[0].id}`}
                >
                  <Vista01Inbox />
                </div>

                <div
                  ref={layer2Ref}
                  className={`mv2-rest-scenario-layer mv2-rest-scenario-layer--2 ${mobileActiveIndex === 1 ? 'is-mobile-active' : ''}`}
                  id={`scenario-panel-${SCENARIOS[1].id}`}
                  role="tabpanel"
                  aria-labelledby={`scenario-tab-${SCENARIOS[1].id}`}
                >
                  <Vista02Soggiorno />
                </div>

                <div
                  ref={layer3Ref}
                  className={`mv2-rest-scenario-layer mv2-rest-scenario-layer--3 ${mobileActiveIndex === 2 ? 'is-mobile-active' : ''}`}
                  id={`scenario-panel-${SCENARIOS[2].id}`}
                  role="tabpanel"
                  aria-labelledby={`scenario-tab-${SCENARIOS[2].id}`}
                >
                  <Vista03Escalation />
                </div>
              </div>
            </div>
          </div>

          {/* Right Column: 3 Scenario Cards */}
          <div className="mv2-rest-scenarios-cards-col" role="tablist" aria-label="Scenari operativi hotel">
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
