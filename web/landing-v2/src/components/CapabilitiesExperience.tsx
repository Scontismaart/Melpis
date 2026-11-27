import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import Inbox247Showcase from './Inbox247Showcase.tsx';

/**
 * Melpis — Dark Product Capabilities.
 *
 * Derived 1:1 from melpis_feature_section_1_1.tsx (structure, copy, proportions,
 * SVG assets, 3D dashboard, micro-interactions). Adapted for the Landing V2:
 * - no standalone navbar / 270vh scroll track / HUD / architecture modal;
 * - no global page-scroll progress: the chart and the dashboard read this section only;
 * - no remote font or GSAP CDN; transforms are written with the Web Animations API
 *   and requestAnimationFrame, respecting prefers-reduced-motion;
 * - no <form> element (the page ships without forms) and no backend calls;
 * - the simulated booking is confirmed only after an explicit quick-reply choice
 *   and only while the two knowledge sources it depends on are enabled.
 */

type KnowledgeSource = {
  id: string;
  title: string;
  category: 'Documenti' | 'Pagine web' | 'FAQ' | 'Dati strutturati';
  meta: string;
  enabled: boolean;
  iconType: 'doc-green' | 'web-blue' | 'faq-purple' | 'doc-slate' | 'bell-purple';
  usedInStage: 1 | 2 | 3;
};

type FocusStage = 1 | 2;
type ChatMessage = { role: 'user' | 'ai'; text: string; time: string };

const INITIAL_DOCS: KnowledgeSource[] = [
  {
    id: 'menu-trattamenti',
    title: 'Menu trattamenti',
    category: 'Documenti',
    meta: 'Documento · Aggiornato 10 ott 2026',
    enabled: true,
    iconType: 'doc-green',
    usedInStage: 1,
  },
  {
    id: 'orari-disponibilita',
    title: 'Orari e disponibilità',
    category: 'Pagine web',
    meta: 'Pagina web · Aggiornato 8 ott 2026',
    enabled: true,
    iconType: 'web-blue',
    usedInStage: 1,
  },
  {
    id: 'regole-prenotazioni',
    title: 'Regole sulle prenotazioni',
    category: 'FAQ',
    meta: 'FAQ · 9 domande',
    enabled: true,
    iconType: 'faq-purple',
    usedInStage: 1,
  },
  {
    id: 'listino-prezzi',
    title: 'Listino prezzi',
    category: 'Documenti',
    meta: 'Documento · Aggiornato 5 ott 2026',
    enabled: true,
    iconType: 'doc-slate',
    usedInStage: 2,
  },
  {
    id: 'politiche-cancellazione',
    title: 'Politiche di cancellazione',
    category: 'FAQ',
    meta: 'FAQ · 12 domande',
    enabled: true,
    iconType: 'faq-purple',
    usedInStage: 3,
  },
  {
    id: 'info-struttura',
    title: 'Informazioni sulla struttura',
    category: 'Pagine web',
    meta: 'Pagina web · Aggiornato 4 ott 2026',
    enabled: false,
    iconType: 'bell-purple',
    usedInStage: 3,
  },
];

const TABS = [
  { id: 'Tutti', label: 'Tutti (25)' },
  { id: 'Documenti', label: 'Documenti (12)' },
  { id: 'Pagine web', label: 'Pagine web (8)' },
  { id: 'FAQ', label: 'FAQ (5)' },
  { id: 'Dati strutturati', label: 'Dati strutturati (0)' },
] as const;

const CHART_POINTS = [
  { x: 4, y: 74, hour: '00:00', count: 14 },
  { x: 33, y: 66, hour: '03:00', count: 26 },
  { x: 62, y: 57, hour: '06:00', count: 38 },
  { x: 91, y: 49, hour: '09:00', count: 54 },
  { x: 120, y: 41, hour: '12:00', count: 69 },
  { x: 149, y: 32, hour: '15:00', count: 86 },
  { x: 178, y: 24, hour: '18:00', count: 102 },
  { x: 207, y: 15, hour: '21:00', count: 115 },
  { x: 234, y: 7, hour: '24:00', count: 128 },
];


const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

const reduceMotion = () =>
  typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;


const MelpisMark = ({ size = 22 }: { size?: number }) => {
  const uid = useId().replace(/:/g, '');
  const upper = `mv2-cap-logo-a-${uid}`;
  const lower = `mv2-cap-logo-b-${uid}`;

  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={upper} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="100%" stopColor="#9ca3af" />
        </linearGradient>
        <linearGradient id={lower} x1="0%" y1="100%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#6b7280" />
          <stop offset="100%" stopColor="#e5e7eb" />
        </linearGradient>
      </defs>
      <path d="M5 24.5V7.5L11.2 12.2V24.5H5Z" fill={`url(#${lower})`} />
      <path d="M5 7.5L16 16.8L21.2 12.2L11.2 4.5L5 7.5Z" fill={`url(#${upper})`} />
      <path d="M16 16.8L27 7.5V24.5H21.2V12.4L16 16.8Z" fill={`url(#${upper})`} />
    </svg>
  );
};

const SourceIcon = ({ type }: { type: KnowledgeSource['iconType'] }) => {
  switch (type) {
    case 'doc-green':
      return (
        <div className="kb-icon-box kb-icon-green">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#64d29a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="8" y1="13" x2="16" y2="13" />
            <line x1="8" y1="17" x2="13" y2="17" />
          </svg>
        </div>
      );
    case 'web-blue':
      return (
        <div className="kb-icon-box kb-icon-blue">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <circle cx="12" cy="12" r="9" />
            <line x1="3.6" y1="9" x2="20.4" y2="9" />
            <line x1="3.6" y1="15" x2="20.4" y2="15" />
            <path d="M11.5 3a17 17 0 0 0 0 18" />
            <path d="M12.5 3a17 17 0 0 1 0 18" />
          </svg>
        </div>
      );
    case 'doc-slate':
      return (
        <div className="kb-icon-box kb-icon-slate">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#cbd5e1" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="8" y1="13" x2="16" y2="13" />
            <line x1="8" y1="17" x2="14" y2="17" />
          </svg>
        </div>
      );
    case 'api-indigo':
    default:
      return (
        <div className={`kb-icon-box ${type === 'bell-purple' ? 'kb-icon-indigo' : 'kb-icon-purple'}`}>
          {type === 'bell-purple' ? (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#a5b4fc" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
              <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
              <path d="M13.73 21a2 2 0 0 1-3.46 0" />
            </svg>
          ) : (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#a78bfa" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              <path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 1.5-2.5 2-2.5 3" />
              <line x1="12" y1="15" x2="12.01" y2="15" />
            </svg>
          )}
        </div>
      );
  }
};

export default function CapabilitiesExperience() {
  const rootRef = useRef<HTMLDivElement>(null);
  const bezelRef = useRef<HTMLDivElement>(null);
  const sheenRef = useRef<HTMLDivElement>(null);
  const chartLineRef = useRef<SVGPathElement>(null);
  const chartClipRef = useRef<SVGRectElement>(null);
  const chartDotRef = useRef<SVGCircleElement>(null);
  const counterRef = useRef<HTMLSpanElement>(null);

  const [docs, setDocs] = useState<KnowledgeSource[]>(INITIAL_DOCS);
  const [hoveredChartIdx, setHoveredChartIdx] = useState<number | null>(null);
  const [focusStage, setFocusStage] = useState<FocusStage>(1);

  const orariEnabled = docs.find((doc) => doc.id === 'orari-disponibilita')?.enabled ?? true;
  const rulesEnabled = docs.find((doc) => doc.id === 'regole-prenotazioni')?.enabled ?? true;
  const canConfirm = orariEnabled && rulesEnabled;
  const enabledSources = docs.filter((doc) => doc.enabled).length;

  const filteredDocs = docs;

  /* Local entrance timeline: the chart line, its endpoint and the counter scrub on the
     section's own reveal (driven by mv2-capabilities--visible), never on the page scroll
     position. The values are written straight to the DOM through refs so a 60fps scrub does
     not re-render the section and never needs an inline style attribute (CSP-safe). */
  useEffect(() => {
    const root = rootRef.current;
    const section = root?.closest('.mv2-capabilities');
    const line = chartLineRef.current;
    const clip = chartClipRef.current;
    const dot = chartDotRef.current;
    const counter = counterRef.current;
    if (!section || !line || !clip || !dot || !counter || reduceMotion()) return;

    let frame = 0;
    let started = false;
    let cancelled = false;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const totalSegments = CHART_POINTS.length - 1;

    const paint = (progress: number) => {
      const exact = progress * totalSegments;
      const lower = Math.min(Math.floor(exact), totalSegments - 1);
      const upper = Math.min(lower + 1, totalSegments);
      const fraction = exact - lower;
      const from = CHART_POINTS[lower];
      const to = CHART_POINTS[upper];
      const x = from.x + (to.x - from.x) * fraction;
      const y = from.y + (to.y - from.y) * fraction;

      line.style.strokeDashoffset = String(1 - progress);
      clip.setAttribute('width', String(Math.max(x + 2, 10)));
      dot.setAttribute('cx', x.toFixed(2));
      dot.setAttribute('cy', y.toFixed(2));
      counter.textContent = String(Math.round(128 * progress));
    };

    paint(0);

    const run = () => {
      if (started) return;
      started = true;
      const start = performance.now() + 180;
      const duration = 1050;

      const step = (now: number) => {
        if (cancelled) return;
        const raw = clamp01((now - start) / duration);
        paint(1 - Math.pow(1 - raw, 3));
        if (raw < 1) frame = requestAnimationFrame(step);
      };

      frame = requestAnimationFrame(step);
    };

    const observer = new MutationObserver(() => {
      if (section.classList.contains('mv2-capabilities--visible')) run();
    });

    if (section.classList.contains('mv2-capabilities--visible')) run();
    else observer.observe(section, { attributes: true, attributeFilter: ['class'] });

    const onMotionChange = (event: MediaQueryListEvent) => {
      if (event.matches) {
        cancelled = true;
        cancelAnimationFrame(frame);
        line.style.strokeDashoffset = '';
        clip.setAttribute('width', '240');
        dot.setAttribute('cx', String(CHART_POINTS[totalSegments].x));
        dot.setAttribute('cy', String(CHART_POINTS[totalSegments].y));
        counter.textContent = '128';
      }
    };
    media.addEventListener('change', onMotionChange);

    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      media.removeEventListener('change', onMotionChange);
    };
  }, []);

  /* Section-local 3D perspective: static fixed orientation matching reference.
     Mouse tracking, pointer parallax and dynamic RAF loops removed per design requirements. */

  const scrubChart = (index: number | null) => {
    setHoveredChartIdx(index);
    setFocusStage(2);
  };

  return (
    <div className="mv2-cap-exp" ref={rootRef}>
      <main className="mce-main-grid">
        <div className="left-column mv2-capabilities-story">
          {/* A — SHOWCASE CONVERSAZIONI 24/7 (Sostituzione Mini Chat — Concept Lemni-style) */}
          <Inbox247Showcase
            isActiveStage={focusStage === 1}
            onPointerEnter={() => setFocusStage(1)}
          />

          {/* B — AUTOMAZIONI */}
          <section
            className="automazioni-section left-section-item mv2-capabilities-metrics"
            data-mv2-cap-panel="metrics"
            aria-labelledby="mv2-cap-automations-title"
            onPointerEnter={() => setFocusStage(2)}
          >
            <div>
              <div className="section-eyebrow">AUTOMAZIONI</div>
              <h2 className="section-heading" id="mv2-cap-automations-title">
                Gestisci più richieste,<br />senza aumentare<br />il team.
              </h2>
              <p className="section-description">
                Melpis automatizza conversazioni, prenotazioni e supporto, mantenendo ogni risposta coerente con la tua attività e riducendo il carico di lavoro del team.
              </p>
            </div>

            <figure className="automazioni-chart-box">
              <div className="metric-top-row">
                <span className="metric-big-number" aria-hidden="true" ref={counterRef}>128</span>
                <span className="metric-green-pill">
                  +40%
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
                    <line x1="7" y1="17" x2="17" y2="7" />
                    <polyline points="7 7 17 7 17 17" />
                  </svg>
                </span>
              </div>
              <div className="metric-subtitle">Richieste gestite oggi</div>

              <div className="chart-svg-wrap">
                {hoveredChartIdx !== null && (
                  <div className="chart-hover-tooltip" aria-hidden="true">
                    Ore {CHART_POINTS[hoveredChartIdx].hour} ·{' '}
                    <strong>{CHART_POINTS[hoveredChartIdx].count} richieste</strong>
                  </div>
                )}

                <svg
                  viewBox="0 0 240 88"
                  width="100%"
                  overflow="visible"
                  role="img"
                  aria-label="Grafico dimostrativo: richieste gestite nell'arco della giornata, in crescita dalle 14 alle 128 unità."
                  onMouseLeave={() => setHoveredChartIdx(null)}
                >
                  <defs>
                    <linearGradient id="mv2-cap-chart-area" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#ffffff" stopOpacity="0.16" />
                      <stop offset="100%" stopColor="#ffffff" stopOpacity="0.01" />
                    </linearGradient>
                    <clipPath id="mv2-cap-chart-clip">
                      <rect ref={chartClipRef} x="0" y="0" width="240" height="88" />
                    </clipPath>
                    <filter id="mv2-cap-chart-glow" x="-60%" y="-60%" width="220%" height="220%">
                      <feGaussianBlur stdDeviation="3.5" result="blur" />
                      <feMerge>
                        <feMergeNode in="blur" />
                        <feMergeNode in="SourceGraphic" />
                      </feMerge>
                    </filter>
                  </defs>

                  <g clipPath="url(#mv2-cap-chart-clip)">
                    <path
                      d="M 4 74 L 33 66 L 62 57 L 91 49 L 120 41 L 149 32 L 178 24 L 207 15 L 234 7 L 234 82 L 4 82 Z"
                      fill="url(#mv2-cap-chart-area)"
                    />
                    {CHART_POINTS.map((point, index) => (
                      <line
                        key={`bar-${point.x}`}
                        className="mv2-cap-chart-bar"
                        x1={point.x}
                        y1={point.y}
                        x2={point.x}
                        y2="82"
                        stroke={hoveredChartIdx === index ? 'rgba(100, 210, 154, 0.65)' : 'rgba(255,255,255,0.09)'}
                        strokeWidth={hoveredChartIdx === index ? '1.8' : '1'}
                      />
                    ))}
                    <path
                      ref={chartLineRef}
                      className="mv2-cap-chart-line"
                      pathLength="1"
                      d="M 4 74 L 33 66 L 62 57 L 91 49 L 120 41 L 149 32 L 178 24 L 207 15 L 234 7"
                      fill="none"
                      stroke="#e5e7eb"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                    />
                  </g>

                  <path
                    d="M 4 74 L 33 66 L 62 57 L 91 49 L 120 41 L 149 32 L 178 24 L 207 15 L 234 7"
                    fill="none"
                    stroke="rgba(255,255,255,0.12)"
                    strokeWidth="1"
                    strokeDasharray="2 3"
                  />
                  <line x1="2" y1="82" x2="236" y2="82" stroke="rgba(255,255,255,0.12)" strokeWidth="1" />
                  <circle ref={chartDotRef} cx="234" cy="7" r="3.2" fill="#ffffff" filter="url(#mv2-cap-chart-glow)" />

                  {CHART_POINTS.map((point, index) => (
                    <rect
                      key={`hit-${point.x}`}
                      x={point.x - 13}
                      y="0"
                      width="26"
                      height="84"
                      fill="transparent"
                      onMouseEnter={() => scrubChart(index)}
                      aria-hidden="true"
                    />
                  ))}
                </svg>

                <div className="chart-x-labels" aria-hidden="true">
                  <span>00</span>
                  <span>06</span>
                  <span>12</span>
                  <span>18</span>
                  <span>24</span>
                </div>
              </div>

              <figcaption className="mce-data-note">Dati dimostrativi · non sono risultati di clienti reali</figcaption>
            </figure>
          </section>

          <p className="mce-demo-note">
            Simulazione illustrativa · nessun messaggio viene inviato
          </p>
        </div>

        {/* D/E — HEADLINE + 3D KNOWLEDGE DASHBOARD */}
        <div className="right-column mv2-capabilities-knowledge" data-mv2-cap-panel="knowledge">
          <div className="mv2-capabilities-cover">
            <div className="hero-copy-block hero-copy-anim">
              <div className="hero-eyebrow">CONVERSAZIONI E PRENOTAZIONI</div>
              <h2 className="hero-headline mv2-capabilities-headline" id="mv2-capabilities-title">
                <span className="mv2-capabilities-headline-primary">
                  <span className="hero-headline-white">Rispondi 24/7,</span>
                  <span className="hero-headline-muted">quando serve.</span>
                </span>
              </h2>
              <p className="hero-description mv2-capabilities-headline-secondary">
                Il team interviene solo quando conta. Melpis gestisce in autonomia richieste, prenotazioni e informazioni, utilizzando i tuoi dati e le tue regole.
              </p>
            </div>

            <div
              className="dashboard-3d-perspective-stage mv2-cap-dashboard"
              role="region"
              aria-label="Base di conoscenza: documenti, pagine web, FAQ e dati struttura della tua attività"
            >
              <div className="dashboard-3d-bezel" ref={bezelRef}>
                <div className="dashboard-glass-sheen" ref={sheenRef} aria-hidden="true" />

                <div className="dashboard-inner-window">
                  <aside className="dash-sidebar" aria-hidden="true">
                    <div className="dash-sidebar-brand">
                      <div className="mce-logo-box">
                        <MelpisMark size={15} />
                      </div>
                      <span>melpis</span>
                    </div>

                    <div className="dash-nav-scroll">
                      <div className="dash-nav-group-label">Principale</div>
                      {[
                        {
                          label: 'Panoramica',
                          badge: null,
                          icon: (
                            <>
                              <rect x="3" y="3" width="7" height="7" rx="1" />
                              <rect x="14" y="3" width="7" height="7" rx="1" />
                              <rect x="3" y="14" width="7" height="7" rx="1" />
                              <rect x="14" y="14" width="7" height="7" rx="1" />
                            </>
                          ),
                        },
                        {
                          label: 'Inbox',
                          badge: '4',
                          icon: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
                        },
                        {
                          label: 'Prenotazioni',
                          badge: null,
                          icon: (
                            <>
                              <rect x="3" y="4" width="18" height="18" rx="2" />
                              <line x1="16" y1="2" x2="16" y2="6" />
                              <line x1="8" y1="2" x2="8" y2="6" />
                              <line x1="3" y1="10" x2="21" y2="10" />
                            </>
                          ),
                        },
                        {
                          label: 'Recensioni',
                          badge: null,
                          icon: <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />,
                        },
                        {
                          label: 'Team & Collaboratori',
                          badge: null,
                          icon: (
                            <>
                              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                              <circle cx="9" cy="7" r="4" />
                              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                            </>
                          ),
                        },
                      ].map((item) => (
                        <div
                          key={item.label}
                          className="dash-nav-item"
                        >
                          <span className="dash-nav-item-left">
                            <svg className="dash-nav-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" focusable="false">{item.icon}</svg>
                            <span>{item.label}</span>
                          </span>
                          {item.badge && <span className="dash-badge-count">{item.badge}</span>}
                        </div>
                      ))}

                      <div className="dash-nav-group-label">Assistente</div>
                      {[
                        {
                          label: 'Simulatore AI',
                          icon: (
                            <>
                              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                              <path d="M8 9h8M8 13h5" />
                            </>
                          ),
                        },
                        {
                          label: 'Conoscenza',
                          icon: (
                            <>
                              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                            </>
                          ),
                        },
                        {
                          label: 'Configurazione AI',
                          icon: (
                            <>
                              <rect x="4" y="8" width="16" height="12" rx="2" />
                              <path d="M12 4v4" />
                              <circle cx="12" cy="4" r="1" />
                              <path d="M9 13h.01M15 13h.01M9 17h6" />
                            </>
                          ),
                        },
                      ].map((item) => (
                        <div
                          key={item.label}
                          className={`dash-nav-item ${item.label === 'Conoscenza' ? 'active' : ''}`}
                        >
                          <span className="dash-nav-item-left">
                            <svg className="dash-nav-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" focusable="false">{item.icon}</svg>
                            <span>{item.label}</span>
                          </span>
                        </div>
                      ))}
                    </div>

                    <div className="dash-side-user">
                      <span className="dash-side-user-avatar" aria-hidden="true">F</span>
                      <span className="dash-side-user-copy">
                        <strong>attività.demo@melpis.it</strong>
                        <small>Ruolo: Proprietario</small>
                      </span>
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" focusable="false">
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    </div>
                  </aside>

                  <div className="dash-main">
                    <div className="dash-breadcrumbs">
                      <span>melpis</span>
                      <span>&gt;</span>
                      <strong>Documenti &amp; Conoscenza</strong>
                    </div>

                    <h3 className="dash-title">Documenti &amp; Conoscenza</h3>
                    <p className="dash-subtitle">
                      Aggiungi e gestisci le informazioni che Melpis utilizza per rispondere ai clienti.
                    </p>

                    <div className="dash-kpi-grid">
                      <div className="dash-kpi-card">
                        <span className="dash-kpi-icon" aria-hidden="true">
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="2" focusable="false">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                            <polyline points="14 2 14 8 20 8" />
                          </svg>
                        </span>
                        <span className="dash-kpi-text">
                          <span className="dash-kpi-val">12</span>
                          <span className="dash-kpi-label">Documenti</span>
                        </span>
                      </div>

                      <div className="dash-kpi-card">
                        <span className="dash-kpi-icon" aria-hidden="true">
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="2" focusable="false">
                            <circle cx="12" cy="12" r="9" />
                            <line x1="3.6" y1="9" x2="20.4" y2="9" />
                            <line x1="3.6" y1="15" x2="20.4" y2="15" />
                            <path d="M12 3a16 16 0 0 0 0 18" />
                          </svg>
                        </span>
                        <span className="dash-kpi-text">
                          <span className="dash-kpi-val">8</span>
                          <span className="dash-kpi-label">Pagine web</span>
                        </span>
                      </div>

                      <div className="dash-kpi-card">
                        <span className="dash-kpi-icon" aria-hidden="true">
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="2" focusable="false">
                            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                          </svg>
                        </span>
                        <span className="dash-kpi-text">
                          <span className="dash-kpi-val">5</span>
                          <span className="dash-kpi-label">FAQ</span>
                        </span>
                      </div>
                    </div>

                    <div className="dash-search-row">
                      <div className="dash-search-box">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#6c7381" strokeWidth="2" aria-hidden="true" focusable="false">
                          <circle cx="11" cy="11" r="8" />
                          <line x1="21" y1="21" x2="16.65" y2="16.65" />
                        </svg>
                        <span className="dash-search-input dash-search-placeholder">
                          Cerca documenti, pagine o FAQ...
                        </span>
                      </div>
                      <span className="dash-filter-btn" aria-hidden="true">
                        <span>Filtri</span>
                        <span aria-hidden="true">&gt;</span>
                      </span>
                    </div>

                    <div className="dash-tabs-row" role="group" aria-label="Sezioni della dashboard dimostrativa" tabIndex={0}>
                      {TABS.map((tab) => (
                        <span
                          key={tab.id}
                          className={`dash-tab-btn ${tab.id === 'Tutti' ? 'active' : ''}`}
                        >
                          {tab.label}
                        </span>
                      ))}
                    </div>

                    <div className="dash-kb-list">
                      {filteredDocs.map((doc) => (
                        <div key={doc.id} className="dash-kb-row">
                          <div className="dash-kb-left">
                            <SourceIcon type={doc.iconType} />
                            <div>
                              <div className="dash-kb-title">
                                <span>{doc.title}</span>
                              </div>
                              <div className="dash-kb-meta">{doc.meta}</div>
                            </div>
                          </div>

                          <div className="dash-kb-right">
                            <span className="dots-btn" aria-hidden="true">
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false">
                                <circle cx="12" cy="5" r="1.8" />
                                <circle cx="12" cy="12" r="1.8" />
                                <circle cx="12" cy="19" r="1.8" />
                              </svg>
                            </span>
                          </div>
                        </div>
                      ))}
                      {filteredDocs.length === 0 && (
                        <p className="mce-empty-state">Nessuna fonte corrisponde alla ricerca o al filtro attivo.</p>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
