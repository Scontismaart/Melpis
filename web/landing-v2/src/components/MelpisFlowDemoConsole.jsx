import { useEffect, useRef, useState } from 'react';
import * as defaultScenariosData from './melpis-flow-scenarios.mjs';
import * as restaurantScenariosData from './melpis-rest-flow-scenarios.mjs';
import * as saloniScenariosData from './melpis-saloni-flow-scenarios.mjs';
import * as hotelScenariosData from './melpis-hotel-flow-scenarios.mjs';
import * as barbieriScenariosData from './melpis-barbieri-flow-scenarios.mjs';

/**
 * Melpis Flow Demo console.
 *
 * The visitor sends one message and watches the whole flow run. The component is
 * a deterministic state machine: `step` is the single source of truth and every
 * animation only reflects it. Timers are cancellable, the sequence never overlaps
 * itself and everything resets when the scenario changes or the island unmounts.
 *
 * Supports variant="default" (home page) and variant="restaurants" (restaurant page).
 */

const symbolSrc = `${import.meta.env.BASE_URL}assets/mv2-symbol.svg`;

function ScenarioIcon({ name }) {
  if (name === 'calendar') {
    return (
      <svg className="mv2-flow-scen-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
        <line x1="16" y1="2" x2="16" y2="6" />
        <line x1="8" y1="2" x2="8" y2="6" />
        <line x1="3" y1="10" x2="21" y2="10" />
      </svg>
    );
  }
  if (name === 'cutlery') {
    return (
      <svg className="mv2-flow-scen-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M18 2v6a3 3 0 0 1-3 3 3 3 0 0 1-3-3V2" />
        <line x1="15" y1="2" x2="15" y2="22" />
        <path d="M4 2v4a3 3 0 0 0 3 3h0a3 3 0 0 0 3-3V2" />
        <line x1="7" y1="2" x2="7" y2="22" />
      </svg>
    );
  }
  if (name === 'clock') {
    return (
      <svg className="mv2-flow-scen-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10" />
        <polyline points="12 6 12 12 16 14" />
      </svg>
    );
  }
  if (name === 'star') {
    return (
      <svg className="mv2-flow-scen-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
      </svg>
    );
  }
  return null;
}

const formatter = (() => {
  try {
    return new Intl.DateTimeFormat('it-IT', { weekday: 'long', day: 'numeric', month: 'long' });
  } catch (error) {
    return null;
  }
})();

function tomorrowLabel() {
  if (!formatter) return '';
  try {
    return formatter.format(new Date(Date.now() + 86400000));
  } catch (error) {
    return '';
  }
}

export default function MelpisFlowDemoConsole({
  variant = 'default',
}) {
  const data =
    variant === 'restaurants' ? restaurantScenariosData :
    variant === 'saloni' ? saloniScenariosData :
    variant === 'hotel' ? hotelScenariosData :
    variant === 'barbieri' ? barbieriScenariosData :
    defaultScenariosData;
  const {
    cardActiveLabels,
    cardDoneLabels,
    cardLabels,
    cardOrder,
    defaultScenarioId,
    deriveRun,
    durationFor,
    scenarios,
    scenarioOrder,
    splitMessage,
    steps,
  } = data;

  const [scenarioId, setScenarioId] = useState(defaultScenarioId);
  const [step, setStep] = useState(-1);
  const [running, setRunning] = useState(false);
  const [reduced, setReduced] = useState(false);
  const [dayLabel, setDayLabel] = useState('');
  const timerRef = useRef(null);
  const run = deriveRun(scenarioId, step);
  const { scenario } = run;

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => setReduced(query.matches);
    sync();
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);

  useEffect(() => {
    if (!running) return undefined;
    if (step >= run.last) {
      setRunning(false);
      return undefined;
    }
    const delay = durationFor(step + 1, reduced);
    timerRef.current = window.setTimeout(() => setStep(value => Math.min(run.last, value + 1)), delay);
    return () => {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    };
  }, [running, step, run.last, reduced, scenarioId, durationFor]);

  useEffect(() => () => {
    window.clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  function chooseScenario(nextId) {
    if (nextId === scenarioId) return;
    window.clearTimeout(timerRef.current);
    timerRef.current = null;
    setRunning(false);
    setScenarioId(nextId);
    setStep(-1);
    setDayLabel('');
  }

  function send() {
    if (step >= 0 || running) return;
    setDayLabel(tomorrowLabel());
    setStep(0);
    setRunning(true);
  }

  function replay() {
    window.clearTimeout(timerRef.current);
    timerRef.current = null;
    setRunning(false);
    setStep(-1);
    setDayLabel('');
  }

  const messageRuns = splitMessage(scenario.message, scenario.highlights);
  const calendar = scenario.calendar;
  const outcome = scenario.outcome;
  const sent = run.sent;
  const typing = running && step >= 1 && step < 4;

  function cardDetail(key, state) {
    if (state === 'skipped') return 'Non richiesto per questa richiesta';
    if (state === 'pending') return 'In attesa';
    if (state === 'waiting') return outcome.status;
    if (state === 'active') return cardActiveLabels[key];
    if (key === 'intent') return scenario.modules.nlp;
    if (key === 'knowledge') return scenario.modules.kb;
    if (key === 'calendar') return scenario.modules.cal;
    return cardDoneLabels[key];
  }

  return (
    <div
      className={`mv2-flow-console ${variant === 'restaurants' ? 'mv2-flow-console--restaurants' : ''} ${variant !== 'default' ? `mv2-flow-console--sector mv2-flow-console--${variant}` : ''}`}
      data-variant={variant}
      data-reduced={reduced ? 'true' : 'false'}
      data-running={running ? 'true' : 'false'}
    >
      <div className="mv2-flow-shell" id="mv2-flow-shell">
        <span className="mv2-flow-sheen" aria-hidden="true" />
        <aside className="mv2-flow-rail" aria-label="Scenario e avanzamento">
          <div className="mv2-flow-rail-block">
            <p className="mv2-flow-rail-title">{variant === 'default' ? 'Scenario' : 'SCENARI'}</p>
            <div className="mv2-flow-scenarios" role="group" aria-label="Scenari della demo">
              {scenarioOrder.map(id => {
                const item = scenarios[id];
                return (
                  <button
                    type="button"
                    className="mv2-flow-scenario"
                    key={id}
                    data-active={id === scenarioId ? 'true' : 'false'}
                    aria-pressed={id === scenarioId}
                    onClick={() => chooseScenario(id)}
                  >
                    {item.icon && <ScenarioIcon name={item.icon} />}
                    <span className="mv2-flow-scenario-nav">{item.nav}</span>
                    {variant === 'default' && item.context && (
                      <span className="mv2-flow-scenario-context">{item.context}</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          <ol className="mv2-flow-steps" aria-label="Fasi della lavorazione">
            {steps.map((entry, index) => {
              const state = run.complete || index < run.step ? 'done' : index === run.step ? 'active' : 'pending';
              return (
                <li key={entry.id} data-state={state} aria-current={!run.complete && index === run.step ? 'step' : undefined}>
                  <span className="mv2-flow-step-index" aria-hidden="true">{index + 1}</span>
                  <span className="mv2-flow-step-text">
                    <span className="mv2-flow-step-label">{entry.label}</span>
                    <span className="mv2-flow-step-hint">{entry.hint}</span>
                  </span>
                </li>
              );
            })}
          </ol>

          <div className="mv2-flow-progress-wrap">
            <div
              className="mv2-flow-progress"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={steps.length}
              aria-valuenow={run.step + 1}
              aria-label="Avanzamento della demo"
            >
              {steps.map((entry, index) => (
                <span key={entry.id} data-state={index <= run.step ? 'filled' : 'empty'} aria-hidden="true" />
              ))}
            </div>
            <p className="mv2-flow-progress-text">{run.idle ? 'Pronto all’invio' : `Passo ${run.step + 1} di ${steps.length}`}</p>
          </div>
        </aside>

        <section className="mv2-flow-chat" aria-label="Conversazione dimostrativa">
          <header className="mv2-flow-chat-head">
            <span className="mv2-flow-avatar" aria-hidden="true">{scenario.client.initials}</span>
            <span className="mv2-flow-identity">
              <strong>{scenario.client.name}</strong>
              <small>{scenario.context} · WhatsApp Business</small>
            </span>
            <span className="mv2-flow-typing" data-visible={typing ? 'true' : 'false'}>sta scrivendo…</span>
          </header>

          <div className="mv2-flow-stream" role="log" aria-label="Messaggi della conversazione">
            {!sent && (
              <p className="mv2-flow-empty">Invia il messaggio in basso: Melpis analizza la richiesta, consulta la Knowledge Base e verifica la disponibilità.</p>
            )}

            {sent && (
              <p className="mv2-flow-chat-day">OGGI</p>
            )}

            {sent && (
              <div className="mv2-flow-bubble mv2-flow-bubble-client" data-kind="client">
                <span className="mv2-flow-avatar" aria-hidden="true">{scenario.client.initials}</span>
                <div className="mv2-flow-bubble-body">
                  <p className="mv2-flow-text">
                    {messageRuns.map((fragment, index) => fragment.entity
                      ? <mark className="mv2-flow-hl" key={`${fragment.entity}-${index}`}>{fragment.text}</mark>
                      : <span key={`text-${index}`}>{fragment.text}</span>)}
                  </p>
                </div>
              </div>
            )}

            {run.replyVisible && (
              <div className="mv2-flow-bubble mv2-flow-bubble-assistant mv2-flow-bubble-reply">
                <span className="mv2-flow-avatar mv2-flow-avatar-assistant" aria-hidden="true"><img src={symbolSrc} alt="" width={18} height={13} /></span>
                <div className="mv2-flow-bubble-body">
                  <small>Melpis</small>
                  <p className="mv2-flow-text">{scenario.reply}</p>
                </div>
              </div>
            )}

            {run.followUpVisible && scenario.followUp && scenario.followUp.map((msg, index) => (
              <div
                key={`followup-${index}`}
                className={`mv2-flow-bubble ${msg.role === 'client' ? 'mv2-flow-bubble-client' : 'mv2-flow-bubble-assistant mv2-flow-bubble-reply'} mv2-flow-bubble-followup`}
              >
                <span className={`mv2-flow-avatar ${msg.role === 'assistant' ? 'mv2-flow-avatar-assistant' : ''}`} aria-hidden="true">
                  {msg.role === 'client' ? scenario.client.initials : <img src={symbolSrc} alt="" width={18} height={13} />}
                </span>
                <div className="mv2-flow-bubble-body">
                  {msg.role === 'assistant' && <small>Melpis</small>}
                  <p className="mv2-flow-text">{msg.text}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="mv2-flow-composer">
            <label className="mv2-flow-sr" htmlFor="mv2-flow-input">Messaggio del cliente — demo</label>
            <div className="mv2-flow-composer-field">
              <input
                id="mv2-flow-input"
                className="mv2-flow-input"
                type="text"
                value={scenario.message}
                readOnly
                spellCheck="false"
              />
              <button
                type="button"
                className="mv2-flow-send"
                onClick={send}
                disabled={sent}
                aria-label={sent ? 'Inviato' : 'Invia messaggio'}
                title={sent ? 'Inviato' : 'Invia messaggio'}
              >
                <span className="mv2-flow-send-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M4.2 12 20 4.6l-3.1 14.8-4.5-5.2z" />
                    <path d="M12.4 14.2 20 4.6" />
                  </svg>
                </span>
              </button>
            </div>
            <p className="mv2-flow-composer-hint" aria-hidden="true">Demo interattiva · Dati simulati</p>
          </div>
        </section>

        <aside className="mv2-flow-engine" aria-label="Stato della richiesta">
          <header className="mv2-flow-engine-head">
            <p className="mv2-flow-rail-title">{variant === 'restaurants' ? 'STATO DELLA RICHIESTA' : 'Stato della richiesta'}</p>
            <span className="mv2-flow-status-pill" data-active={run.awaiting.active ? 'true' : 'false'}>
              {run.idle ? 'Pronto' : run.complete ? (run.awaiting.pill || run.awaiting.label) : 'In corso'}
            </span>
          </header>

          <p className="mv2-flow-activity">
            <span className="mv2-flow-activity-label" aria-hidden="true">Ora</span>
            {run.activity}
          </p>

          <ol className="mv2-flow-modules" aria-label="Fasi di lavorazione">
            {cardOrder.map((key, index) => {
              const state = run.cardState(key);
              return (
                <li className="mv2-flow-module" key={key} data-card={key} data-state={state}>
                  <span className="mv2-flow-module-head">
                    <span className="mv2-flow-module-index" aria-hidden="true">{index + 1}</span>
                    <span className="mv2-flow-module-label">{cardLabels[key]}</span>
                    <span className="mv2-flow-module-dot" aria-hidden="true" />
                  </span>
                  <span className="mv2-flow-module-state">{cardDetail(key, state)}</span>

                  {key === 'intent' && run.entitiesVisible && (
                    <div className="mv2-flow-entities-block">
                      <dl className="mv2-flow-entities">
                        {scenario.entities.map(entity => (
                          <div key={entity.key}>
                            <dt>{entity.label}</dt>
                            <dd>{entity.value}</dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  )}

                  {key === 'knowledge' && run.entitiesVisible && (
                    <div className="mv2-flow-knowledge-block">
                      <p className="mv2-flow-knowledge-note">{scenario.knowledge.note}</p>
                      <p className="mv2-flow-knowledge-source">{scenario.knowledge.source}</p>
                    </div>
                  )}

                  {key === 'calendar' && run.calendarVisible && calendar.requested && (
                    <div className="mv2-flow-slots-block">
                      <div className="mv2-flow-slots-head">
                        <span className="mv2-flow-rail-title">Slot del giorno</span>
                        <small>{dayLabel || 'domani'}</small>
                      </div>
                      <p className="mv2-flow-slots-verdict" data-negative={calendar.verdict === 'Slot occupato' || calendar.verdict.includes('completo') ? 'true' : 'false'}>{calendar.verdict}</p>
                      <ul className="mv2-flow-slots" aria-label="Slot del giorno">
                        {calendar.slots.map(slot => (
                          <li
                            key={slot.time}
                            data-state={slot.state}
                            data-target={slot.target ? 'true' : undefined}
                            data-alternative={slot.alternative ? 'true' : undefined}
                          >{slot.time}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {key === 'dispatch' && run.outcomeVisible && (
                    <div className={`mv2-flow-outcome ${outcome.kind === 'confermata' ? 'mv2-flow-outcome--confirmed' : ''}`} data-kind={outcome.kind}>
                      <header className="mv2-flow-outcome-head">
                        {outcome.kind === 'confermata' ? (
                          <div className="mv2-flow-outcome-success-title">
                            <span className="mv2-flow-outcome-check-icon" aria-hidden="true">
                              <svg viewBox="0 0 20 20" width="17" height="17" fill="currentColor">
                                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                              </svg>
                            </span>
                            <strong>{outcome.title}</strong>
                          </div>
                        ) : (
                          <>
                            <strong>{outcome.title}</strong>
                            <span className="mv2-flow-outcome-status">{outcome.status}</span>
                          </>
                        )}
                      </header>
                      {outcome.detail && <p className="mv2-flow-outcome-detail">{outcome.detail}</p>}
                      <dl className="mv2-flow-outcome-rows">
                        {outcome.rows.map(([label, value]) => (
                          <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
                        ))}
                      </dl>
                      {outcome.footer && (
                        <p className="mv2-flow-outcome-footer-note">{outcome.footer}</p>
                      )}
                      {outcome.guard && (
                        <p className="mv2-flow-outcome-guard">{outcome.guard}</p>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </aside>

        <footer className="mv2-flow-footer">
          <span className="mv2-flow-footer-meta">Demo interattiva · Dati simulati</span>
          <button type="button" className="mv2-flow-replay" onClick={replay} disabled={run.idle}>Rivedi la demo</button>
        </footer>
      </div>

      <p className="mv2-flow-live" role="status" aria-live="polite" aria-atomic="true">
        {run.idle
          ? `${scenario.nav}. Pronto: invia il messaggio per avviare la demo.`
          : run.complete
            ? `${outcome.title}. ${outcome.status}.`
            : `${steps[run.step].label}. Passo ${run.step + 1} di ${steps.length}.`}
      </p>
    </div>
  );
}
