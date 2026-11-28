import { useEffect, useRef, useState } from 'react';

/**
 * Melpis Landing V2 — Section 04: "04 / UN AVVIO CONSAPEVOLE"
 * Editorial dark split canvas turned into a horizontal expanding glass accordion.
 *
 * Stage geometry:
 * - four fumé-glass panels always sit side by side in the same order (01 → 04);
 * - the selected panel expands, the other three stay narrow, and every width
 *   interpolates at the same time (flex-grow transition, no per-frame JS);
 * - one shared mountain panorama is anchored to the stage behind the four glass
 *   masks, so widening a panel reveals more of the same continuous photograph;
 * - the collapsed identity (number, icon, vertical title) lives in an
 *   aria-hidden rail that crossfades out while the detailed body fades in.
 *
 * Constraints kept:
 * - Scoped local state only: no global scroll hijacking, no layout remount.
 * - Zero inline style attributes (strict CSP compliant): every visual state is
 *   driven by classes and data attributes. The single runtime custom property
 *   (`--mv2-setup-stage-h`) only stores a measured height, never a per-frame
 *   animation value.
 * - All four panel bodies stay mounted with stable keys so local state (channel
 *   switch, quick test) survives every expansion.
 */

interface SetupRow {
  key: string;
  label: string;
  value: string;
}

interface StepMeta {
  index: number;
  num: string;
  title: string;
  desc: string;
  summary: string;
  icon: 'doc' | 'workflow' | 'link' | 'verify';
  rows: SetupRow[];
}

const STEPS_META: StepMeta[] = [
  {
    index: 0,
    num: '01',
    title: 'Le informazioni',
    desc: 'Contenuti, orari e regole che l’assistente può usare.',
    summary:
      'La base di conoscenza dell’assistente: ciò che sa, come lo dice e con quale tono.',
    icon: 'doc',
    rows: [
      { key: 'orari', label: 'Orari di apertura', value: 'Lun - Ven 09:00 - 19:00' },
      { key: 'menu', label: 'Menu e listino', value: 'Piatti, prezzi, allergeni' },
      { key: 'policy', label: 'Politiche di cancellazione', value: 'Modifiche e annullamenti' },
      { key: 'faq', label: 'FAQ e note utili', value: 'Risposte pronte, parcheggio' },
    ],
  },
  {
    index: 1,
    num: '02',
    title: 'Il percorso',
    desc: 'Richieste gestibili, escalation e regole operative.',
    summary:
      'Il confine tra ciò che l’assistente gestisce da solo e ciò che passa a una persona.',
    icon: 'workflow',
    rows: [
      { key: 'richieste', label: 'Richieste gestibili automaticamente', value: 'Prenotazioni, orari, disponibilità' },
      { key: 'persona', label: 'Quando coinvolgere una persona', value: 'Gruppi, reclami, casi dubbi' },
      { key: 'handoff', label: 'Escalation e handoff', value: 'Passaggio al team' },
      { key: 'regole', label: 'Regole operative e casi speciali', value: 'Priorità e istruzioni' },
    ],
  },
  {
    index: 2,
    num: '03',
    title: 'Il canale',
    desc: 'Connessione, autorizzazioni e verifica del canale.',
    summary:
      'Il collegamento al canale scelto, con accessi, stato e controlli prima dell’uso.',
    icon: 'link',
    rows: [
      { key: 'channel', label: 'Connessione WhatsApp Business', value: '' },
      { key: 'accessi', label: 'Accessi e autorizzazioni', value: 'Permessi amministratore' },
      { key: 'stato', label: 'Stato del collegamento', value: '' },
      { key: 'verifica', label: 'Verifica del canale', value: 'Messaggio di prova ricevuto' },
      { key: 'prerequisiti', label: 'Prerequisiti tecnici', value: 'Numero dedicato, profilo business' },
    ],
  },
  {
    index: 3,
    num: '04',
    title: 'La verifica',
    desc: 'Controllo finale, anteprima e test del flusso.',
    summary:
      'Nessuna attivazione al buio: si controlla, si prova, poi si conferma.',
    icon: 'verify',
    rows: [
      { key: 'config', label: 'Controllo della configurazione', value: 'Informazioni e percorso' },
      { key: 'preview', label: 'Preview delle risposte', value: 'Anteprima prima dell’invio' },
      { key: 'test', label: 'Test rapido del flusso', value: '' },
      { key: 'conferma', label: 'Conferma impostazioni', value: '' },
    ],
  },
];

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const StepIconSVG = ({ type }: { type: StepMeta['icon'] }) => {
  switch (type) {
    case 'doc':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="8" y1="13" x2="16" y2="13" />
          <line x1="8" y1="17" x2="13" y2="17" />
        </svg>
      );
    case 'workflow':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
          <circle cx="9" cy="7" r="3.2" />
          <path d="M3 20v-1.4A4.6 4.6 0 0 1 7.6 14h2.8a4.6 4.6 0 0 1 4.6 4.6V20" />
          <circle cx="18.5" cy="8.5" r="1.9" />
          <circle cx="18.5" cy="16.5" r="1.9" />
          <line x1="18.5" y1="10.4" x2="18.5" y2="14.6" />
        </svg>
      );
    case 'link':
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
          <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
          <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
        </svg>
      );
    case 'verify':
    default:
      return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
          <circle cx="12" cy="12" r="9" />
          <polyline points="8.5 12.2 10.8 14.5 15.8 9.5" />
        </svg>
      );
  }
};

const ArrowIcon = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <line x1="4" y1="12" x2="19" y2="12" />
    <polyline points="13 6 19 12 13 18" />
  </svg>
);

type TestState = 'idle' | 'running' | 'done';

export default function SetupExperience() {
  // Single source of truth: nav highlight, panel widths, panel styling and the
  // visible body are all derived from this one value.
  const [activeStep, setActiveStep] = useState<number>(0);
  const [whatsappConnected, setWhatsappConnected] = useState<boolean>(true);
  const [testState, setTestState] = useState<TestState>('idle');

  const accordionRef = useRef<HTMLDivElement | null>(null);
  const bodyRefs = useRef<Array<HTMLDivElement | null>>([]);
  const measuredHeight = useRef(0);

  const selectStep = (index: number) => {
    setActiveStep(index);
  };

  const focusStep = (index: number) => {
    selectStep(index);
    window.requestAnimationFrame(() => {
      document.getElementById(`mv2-setup-tab-${STEPS_META[index].num}`)?.focus();
    });
  };

  const handleTabKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    const last = STEPS_META.length - 1;
    let next: number | null = null;

    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
      next = index === last ? 0 : index + 1;
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
      next = index === 0 ? last : index - 1;
    } else if (event.key === 'Home') {
      next = 0;
    } else if (event.key === 'End') {
      next = last;
    }

    if (next === null) return;
    event.preventDefault();
    focusStep(next);
  };

  const runTest = () => {
    if (testState === 'running') return;

    if (prefersReducedMotion()) {
      setTestState('done');
      return;
    }

    setTestState('running');
    window.setTimeout(() => setTestState('done'), 900);
  };

  const rowValue = (key: string, fallback: string) => {
    switch (key) {
      case 'stato':
        return whatsappConnected ? 'Attivo e in ascolto' : 'In attesa di collegamento';
      case 'test':
        return testState === 'done'
          ? 'Superato'
          : testState === 'running'
            ? 'In corso'
            : 'Da eseguire';
      case 'conferma':
        return testState === 'done' ? 'Pronta' : 'In attesa di test';
      default:
        return fallback;
    }
  };

  /**
   * The stage height must fit the tallest of the four bodies without ever
   * changing between steps (no external layout shift). All bodies are mounted at
   * the open-panel width, so measuring their natural content height gives a
   * single, stable value per breakpoint. This is a measurement, not an
   * animation: widths keep animating in CSS only.
   */
  const measureStage = () => {
    const accordion = accordionRef.current;
    if (!accordion) return;

    let tallest = 0;

    bodyRefs.current.forEach((body) => {
      if (!body) return;
      const content = body.querySelector<HTMLElement>('.mv2-setup-panel-content');
      if (!content) return;

      const gap = parseFloat(window.getComputedStyle(content).rowGap) || 0;
      let height = 0;
      Array.from(content.children).forEach((child, index) => {
        height += child.getBoundingClientRect().height + (index > 0 ? gap : 0);
      });

      const bodyStyle = window.getComputedStyle(body);
      height += parseFloat(bodyStyle.paddingTop) + parseFloat(bodyStyle.paddingBottom);

      if (height > tallest) tallest = height;
    });

    if (tallest <= 0) return;

    const next = Math.ceil(tallest + 2);
    if (Math.abs(next - measuredHeight.current) > 1) {
      measuredHeight.current = next;
      accordion.style.setProperty('--mv2-setup-stage-h', `${next}px`);
    }
  };

  // Re-measure after every render: the quick test and the channel switch change
  // the height of some rows, so the stage has to follow them.
  useEffect(() => {
    measureStage();
  });

  useEffect(() => {
    const accordion = accordionRef.current;
    if (!accordion) return;

    let frame = 0;
    const schedule = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        measureStage();
      });
    };

    const observer = new ResizeObserver(schedule);
    observer.observe(accordion);
    bodyRefs.current.forEach((body) => {
      if (body) observer.observe(body);
    });

    window.addEventListener('resize', schedule);
    document.fonts?.ready.then(schedule).catch(() => {});

    return () => {
      observer.disconnect();
      window.removeEventListener('resize', schedule);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <>
      {/* Section ground stays outside the positioned container so it can never
          paint above the header: it is the first painted layer of #mv2-setup. */}
      <div className="mv2-setup-bg" aria-hidden="true" />

      <div className="mv2-setup-container" data-step={activeStep}>
        <header className="mv2-setup-header">
          <div className="mv2-setup-header-main">
            <p className="mv2-setup-eyebrow">UN AVVIO CONSAPEVOLE</p>
            <h2 className="mv2-setup-headline" id="mv2-setup-title">
              <span className="mv2-setup-headline-line mv2-setup-headline-line-1">
                Prima si configura.
              </span>
              <span className="mv2-setup-headline-line mv2-setup-headline-line-2">
                Poi si collega.
              </span>
            </h2>
          </div>
          <p className="mv2-setup-header-desc">
            Per usare i canali servono configurazione, accessi e autorizzazioni della
            piattaforma. I passaggi dipendono dalla tua attività e dal canale scelto.
          </p>
        </header>

        <div className="mv2-setup-body">
          <nav
            className="mv2-setup-tabs"
            role="tablist"
            aria-orientation="vertical"
            aria-label="Passaggi di configurazione"
          >
            {STEPS_META.map((step) => {
              const isActive = activeStep === step.index;

              return (
                <button
                  key={step.num}
                  type="button"
                  role="tab"
                  id={`mv2-setup-tab-${step.num}`}
                  aria-selected={isActive}
                  aria-controls={`mv2-setup-panel-${step.num}`}
                  tabIndex={isActive ? 0 : -1}
                  className={`mv2-setup-tab ${isActive ? 'is-active' : ''}`}
                  onClick={() => selectStep(step.index)}
                  onPointerEnter={(event) => {
                    if (event.pointerType === 'mouse') selectStep(step.index);
                  }}
                  onKeyDown={(event) => handleTabKeyDown(event, step.index)}
                >
                  <span className="mv2-setup-tab-icon" aria-hidden="true">
                    <StepIconSVG type={step.icon} />
                  </span>
                  <span className="mv2-setup-tab-body">
                    <span className="mv2-setup-tab-title">{step.title}</span>
                    <span className="mv2-setup-tab-desc">{step.desc}</span>
                  </span>
                  <span className="mv2-setup-tab-arrow" aria-hidden="true">
                    <ArrowIcon />
                  </span>
                </button>
              );
            })}
          </nav>

          <div className="mv2-setup-canvas" data-step={activeStep}>
            {/* One continuous panorama behind every glass surface. */}
            <div className="mv2-setup-stage" aria-hidden="true">
              <span className="mv2-setup-stage-media" />
              <span className="mv2-setup-stage-haze" />
            </div>

            <div className="mv2-setup-accordion" ref={accordionRef}>
              {STEPS_META.map((step) => {
                const isActive = step.index === activeStep;

                return (
                  <div
                    key={step.num}
                    className="mv2-setup-panel"
                    data-active={isActive ? 'true' : 'false'}
                    data-panel={step.num}
                  >
                    {/* Collapsed identity: icon, vertical title. */}
                    <div className="mv2-setup-rail" aria-hidden="true">
                      <span className="mv2-setup-rail-icon">
                        <StepIconSVG type={step.icon} />
                      </span>
                      <span className="mv2-setup-rail-title">{step.title}</span>
                    </div>

                    {/* Detailed body. Always mounted, always laid out at the
                        open-panel width so text never reflows while resizing. */}
                    <div
                      id={`mv2-setup-panel-${step.num}`}
                      role="tabpanel"
                      aria-labelledby={`mv2-setup-tab-${step.num}`}
                      aria-hidden={!isActive}
                      inert={!isActive ? true : undefined}
                      className="mv2-setup-panel-body"
                      ref={(element) => {
                        bodyRefs.current[step.index] = element;
                      }}
                    >
                      <div className="mv2-setup-panel-content">
                        <div className="mv2-setup-panel-head">
                          <p className="mv2-setup-panel-kicker">Passaggio</p>
                          <h3 className="mv2-setup-main-title">{step.title}</h3>
                          <p className="mv2-setup-subtitle">{step.summary}</p>
                        </div>

                        <ul className="mv2-setup-rows">
                          {step.rows.map((row) => (
                            <li className="mv2-setup-row" key={row.key}>
                              <span className="mv2-setup-row-mark" aria-hidden="true" />
                              <span className="mv2-setup-row-label">{row.label}</span>
                              {row.key === 'channel' ? (
                                <button
                                  type="button"
                                  className={`mv2-setup-status ${
                                    whatsappConnected ? 'is-connected' : 'is-off'
                                  }`}
                                  aria-pressed={whatsappConnected}
                                  onClick={() => setWhatsappConnected((prev) => !prev)}
                                >
                                  <span className="mv2-setup-status-dot" aria-hidden="true" />
                                  <span>{whatsappConnected ? 'Collegato' : 'Collega'}</span>
                                </button>
                              ) : (
                                <span className="mv2-setup-row-value">
                                  {rowValue(row.key, row.value)}
                                </span>
                              )}
                            </li>
                          ))}
                        </ul>

                        {step.index === 3 && (
                          <div className="mv2-setup-test">
                            <button
                              type="button"
                              className="mv2-setup-test-btn"
                              onClick={runTest}
                              disabled={testState === 'running'}
                            >
                              <span>
                                {testState === 'done'
                                  ? 'Ripeti il test'
                                  : testState === 'running'
                                    ? 'Test in corso'
                                    : 'Esegui test rapido'}
                              </span>
                              {testState === 'running' ? (
                                <span className="mv2-setup-typing" aria-hidden="true">
                                  <span className="mv2-setup-typing-dot" />
                                  <span className="mv2-setup-typing-dot" />
                                  <span className="mv2-setup-typing-dot" />
                                </span>
                              ) : (
                                <ArrowIcon size={14} />
                              )}
                            </button>
                            <p
                              className={`mv2-setup-test-result ${
                                testState === 'done' ? 'is-ok' : ''
                              }`}
                              role="status"
                            >
                              {testState === 'done'
                                ? 'Test superato: le risposte rispettano la configurazione.'
                                : 'Nessun dato reale viene inviato durante il test.'}
                            </p>
                          </div>
                        )}

                        <div className="mv2-setup-panel-foot">
                          <span>Passaggio guidato</span>
                          <span className="mv2-setup-ticks" aria-hidden="true">
                            {STEPS_META.map((tick) => (
                              <i
                                key={tick.num}
                                className={activeStep >= tick.index ? 'is-on' : ''}
                              />
                            ))}
                          </span>
                        </div>
                      </div>
                    </div>

                    {/* Hit target for a collapsed panel. A sibling (not a
                        wrapper) of the body, so no interactive nesting occurs. */}
                    {!isActive && (
                      <button
                        type="button"
                        className="mv2-setup-panel-hit"
                        aria-label={`${step.title} — apri questo passaggio`}
                        onClick={() => selectStep(step.index)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
