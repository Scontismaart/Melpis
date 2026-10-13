import { useEffect, useRef, useState } from 'react';
import { bookingAllowed } from './booking-guard.mjs';

const scenarios = {
  available: {
    label: 'Posti disponibili', capacity: 2,
    day: 'Martedì 13 ottobre 2026', time: '19:30',
    customer: 'Avete un tavolo per due martedì 13 ottobre alle 19:30?',
    steps: [
      ['Richiesta ricevuta', 'Due persone · 19:30'],
      ['Informazioni consultate', 'Regole dello slot'],
      ['Disponibilità verificata', '2 posti disponibili'],
      ['Conferma della cliente', 'Conferma ricevuta'],
      ['Prenotazione verificata', 'Disponibilità, capienza e conferma'],
      ['Prenotazione nell’esempio', 'Conversazione collegata in Inbox'],
    ],
  },
  unavailable: {
    label: 'Nessun posto', capacity: 0,
    day: 'Martedì 13 ottobre 2026', time: '19:30',
    customer: 'Avete un tavolo per due martedì 13 ottobre alle 19:30?',
    steps: [
      ['Richiesta ricevuta', 'Due persone · 19:30'],
      ['Disponibilità verificata', '0 posti disponibili'],
      ['Nessuna prenotazione', 'Capienza zero'],
    ],
  },
  team: {
    label: 'Da seguire dal team', capacity: null,
    customer: 'Per un evento, quali piatti sono disponibili?',
    steps: [
      ['Richiesta ricevuta', 'Menu per un evento'],
      ['Informazioni non sufficienti', 'Knowledge Base da verificare'],
      ['Conversazione da seguire', 'Da prendere in carico in Inbox'],
    ],
  },
};

const chapters = ['Richiesta', 'Verifica', 'Esito'];

function replyFor(scenarioId, step, scenario) {
  if (scenarioId === 'team') {
    return step === 0 ? 'Controllo le informazioni sul menu.' : step === 1
      ? 'Non ho informazioni sufficienti per rispondere con certezza.'
      : 'La richiesta è in Inbox per il team.';
  }
  if (scenarioId === 'unavailable') {
    return step === 0 ? 'Verifico la disponibilità per martedì 13 ottobre.' : step === 1
      ? 'Per lo slot richiesto risultano 0 posti disponibili.'
      : 'Non posso confermare il tavolo: la richiesta resta senza prenotazione.';
  }
  return [
    'Controllo la disponibilità per te.',
    'Verifico lo slot richiesto.',
    `Per ${scenario.day} alle ${scenario.time} risultano 2 posti disponibili. Vuoi confermare per due?`,
    'Conferma ricevuta. Ricontrollo lo slot per te.',
    'Grazie. Disponibilità, capienza e conferma sono verificate.',
    'Prenotazione registrata nel pannello dimostrativo.',
  ][step];
}

export default function ConversationStory() {
  const [scenarioId, setScenarioId] = useState('available');
  const [step, setStep] = useState(0);
  const [ready, setReady] = useState(false);
  const scene = useRef(null);
  const scenario = scenarios[scenarioId];
  const last = scenario.steps.length - 1;
  const availabilityVerified = scenarioId === 'available' ? step >= 4 : scenarioId === 'unavailable' && step >= 1;
  const customerConfirmed = scenarioId === 'available' && step >= 3;
  const canBook = scenarioId === 'available' && bookingAllowed({
    capacity: scenario.capacity,
    partySize: 2,
    availabilityVerified,
    customerConfirmed,
  });
  const state = scenario.steps[step];
  const availabilityVisible = (scenarioId === 'available' && step >= 2 && !(canBook && step >= last))
    || (scenarioId === 'unavailable' && step >= 1);
  const chapterIndex = scenarioId === 'available'
    ? (step === 0 ? 0 : step <= 3 ? 1 : 2)
    : Math.min(step, 2);

  useEffect(() => setReady(true), []);
  useEffect(() => {
    if (!ready || !scene.current) return undefined;
    let disposed = false;
    let context;
    let media;
    import('gsap').then(({ gsap }) => {
      if (disposed || !scene.current) return;
      context = gsap.context(() => {
        media = gsap.matchMedia(scene.current);
        media.add('(prefers-reduced-motion: no-preference)', () => {
          const recent = scene.current?.querySelectorAll('.mv2-story-response-bubble, .mv2-story-operation-content');
          if (recent?.length) gsap.fromTo(recent, { autoAlpha: 0.94, y: 6 }, {
            autoAlpha: 1, y: 0, duration: 0.28, ease: 'power2.out', clearProps: 'transform,opacity,visibility',
          });
        });
      }, scene.current);
    });
    return () => { disposed = true; media?.revert(); context?.revert(); };
  }, [ready, scenarioId, step]);

  function changeScenario(event) {
    setScenarioId(event.target.value);
    setStep(0);
  }

  return <div className="mv2-story-demo" ref={scene}>
    <aside className="mv2-story-rail" aria-label="Percorso della conversazione">
      <div className="mv2-story-controls">
        <label htmlFor="mv2-story-scenario">Scegli uno scenario</label>
        <select id="mv2-story-scenario" value={scenarioId} disabled={!ready} onChange={changeScenario}>
          {Object.entries(scenarios).map(([id, item]) => <option value={id} key={id}>{item.label}</option>)}
        </select>
      </div>
      <ol className="mv2-story-chapters" data-progress={`${step + 1}-${last + 1}`} aria-label="Fasi della demo">
        {chapters.map((chapter, index) => <li key={chapter} aria-current={chapterIndex === index ? 'step' : undefined} data-complete={chapterIndex > index ? 'true' : undefined}>
          <span className="mv2-story-chapter-number" aria-hidden="true">0{index + 1}</span>
          <span>{chapter}</span>
        </li>)}
      </ol>
      <p className="mv2-story-progress">Passaggio {step + 1} di {last + 1}</p>
    </aside>

    <section className="mv2-story-stage" aria-label="Conversazione ed esito dimostrativo">
      <div className="mv2-story-stage-grid">
        <div className="mv2-story-conversation">
          <div className="mv2-story-window">
            <span className="mv2-story-window-dot"></span><span className="mv2-story-window-dot"></span><span className="mv2-story-window-dot"></span>
            <div className="mv2-story-window-identity"><span className="mv2-story-avatar mv2-story-avatar-client" aria-hidden="true">MR</span><span><strong>Conversazione demo</strong><small>Ristorante · 13 ott 2026</small></span></div>
          </div>
          <div className="mv2-story-chat">
            <div className="mv2-story-bubble mv2-story-customer-bubble">
              <span className="mv2-story-avatar mv2-story-avatar-client" aria-hidden="true">MR</span>
              <div><small>CLIENTE</small><p>{scenario.customer}</p></div>
            </div>
            {customerConfirmed && <div className="mv2-story-bubble mv2-story-customer-bubble mv2-story-confirmation-bubble"><span className="mv2-story-avatar mv2-story-avatar-client" aria-hidden="true">MR</span><div><small>CLIENTE</small><p>«Confermo, grazie.»</p></div></div>}
            <div className="mv2-story-bubble mv2-story-assistant-bubble mv2-story-response-bubble" key={`${scenarioId}-${step}`}>
              <span className="mv2-story-avatar mv2-story-avatar-assistant" aria-hidden="true"><img src="/landing-v2/assets/mv2-symbol.svg" alt="" width="16" height="12" /></span>
              <div><small>MELPIS · DEMO</small><p>{replyFor(scenarioId, step, scenario)}</p></div>
            </div>
          </div>
        </div>

        <aside className="mv2-story-operations" aria-label="Riepilogo operativo">
          <div className="mv2-story-operation-content" key={`operation-${scenarioId}-${step}`}>
            <header className="mv2-story-operation-heading"><small>STATO DELLA RICHIESTA</small><strong>{state[0]}</strong><span>{state[1]}</span></header>
            {availabilityVisible && <section className="mv2-story-availability" aria-label="Verifica disponibilità">
              <h3>Verifica disponibilità</h3>
              <dl><div><dt>Giorno</dt><dd>{scenario.day}</dd></div><div><dt>Orario</dt><dd>{scenario.time}</dd></div><div><dt>Posti</dt><dd>{scenario.capacity} disponibili</dd></div></dl>
            </section>}
            {canBook && step >= last && <div className="mv2-story-record"><header><strong>Prenotazioni</strong><span>Confermata nell’esempio</span></header><p>Cliente demo · 2 persone</p><time>{scenario.day} · {scenario.time}</time><footer>Conversazione in Inbox</footer></div>}
            {scenarioId === 'unavailable' && step === last && <p className="mv2-story-guard">Prenotazione non creata · nessun posto disponibile</p>}
            {scenarioId === 'team' && step === last && <div className="mv2-story-team-record"><strong>Inbox</strong><span>Richiesta sul menu</span><span>Da prendere in carico</span></div>}
          </div>
        </aside>
      </div>

      <div className="mv2-story-footer">
        <div className="mv2-story-footer-meta"><span>Demo illustrativa · dati sintetici</span><span aria-hidden="true">{String(step + 1).padStart(2, '0')} / {String(last + 1).padStart(2, '0')}</span></div>
        <div className="mv2-story-buttons">
          <button type="button" disabled={!ready || step === 0} onClick={() => setStep(value => Math.max(0, value - 1))}>Indietro</button>
          <button className="mv2-story-primary" type="button" disabled={!ready || step === last} onClick={() => setStep(value => Math.min(last, value + 1))}>{step === 0 ? 'Avvia la demo' : 'Passaggio successivo'}</button>
          <button type="button" disabled={!ready || step === 0} onClick={() => setStep(0)}>Ricomincia</button>
        </div>
      </div>
    </section>
    <p className="mv2-story-live" aria-live="polite" aria-atomic="true">{scenario.label}. {state[0]}.</p>
  </div>;
}
