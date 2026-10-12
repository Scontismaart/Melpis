import { useEffect, useRef, useState } from 'react';
import { bookingAllowed } from './booking-guard.mjs';

const scenarios = {
  available: {
    label: 'Posti disponibili', capacity: 2,
    day: 'Martedì 13 ottobre 2026', time: '19:30',
    customer: 'Avete un tavolo per due martedì 13 ottobre alle 19:30?',
    steps: [
      ['Richiesta ricevuta', 'La cliente chiede un tavolo per due · martedì 13 ottobre, ore 19:30.'],
      ['Informazioni consultate', 'Le regole configurate indicano come verificare lo slot richiesto.'],
      ['Disponibilità verificata', 'Il dato sintetico indica 2 posti disponibili.'],
      ['Conferma della cliente', '«Confermo, grazie.»'],
      ['Tutto pronto per la prenotazione', 'Slot disponibile, capienza sufficiente e conferma della cliente verificati.'],
      ['Attività nel pannello', 'La prenotazione illustrativa compare in Prenotazioni e la conversazione resta in Inbox.'],
    ],
  },
  unavailable: {
    label: 'Nessun posto', capacity: 0,
    customer: 'Avete un tavolo per due martedì 13 ottobre alle 19:30?',
    steps: [
      ['Richiesta ricevuta', 'La cliente chiede un tavolo per due · martedì 13 ottobre, ore 19:30.'],
      ['Disponibilità verificata', 'Per lo slot richiesto non ci sono posti disponibili.'],
      ['Nessuna prenotazione', 'La richiesta resta senza prenotazione perché la capienza disponibile è zero.'],
    ],
  },
  team: {
    label: 'Da seguire dal team', capacity: null,
    customer: 'Per un evento, quali piatti sono disponibili?',
    steps: [
      ['Richiesta ricevuta', 'La cliente chiede informazioni sul menu di un evento.'],
      ['Informazioni non sufficienti', 'Le informazioni configurate non coprono questa richiesta.'],
      ['Conversazione da seguire', 'La conversazione resta visibile in Inbox perché il team possa prenderla in carico.'],
    ],
  },
};

export default function ConversationStory() {
  const [scenarioId, setScenarioId] = useState('available');
  const [step, setStep] = useState(0);
  const [ready, setReady] = useState(false);
  const scene = useRef(null);
  const scenario = scenarios[scenarioId];
  const last = scenario.steps.length - 1;
  const canBook = scenarioId === 'available' && bookingAllowed({
    capacity: scenario.capacity, partySize: 2, availabilityVerified: true,
    customerConfirmed: step >= 3,
  });
  const state = scenario.steps[step];
  const availabilityVisible = (scenarioId === 'available' && step >= 2) || (scenarioId === 'unavailable' && step >= 1);

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
          gsap.fromTo('.mv2-story-scene', { opacity: 0.72, y: 5 }, { opacity: 1, y: 0, duration: 0.22, ease: 'power2.out', clearProps: 'transform,opacity' });
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
    <div className="mv2-story-controls">
      <label htmlFor="mv2-story-scenario">Scegli un esito</label>
      <select id="mv2-story-scenario" value={scenarioId} disabled={!ready} onChange={changeScenario}>
        {Object.entries(scenarios).map(([id, item]) => <option value={id} key={id}>{item.label}</option>)}
      </select>
      <div className="mv2-story-buttons">
        <button type="button" disabled={!ready || step === 0} onClick={() => setStep(value => Math.max(0, value - 1))}>Indietro</button>
        <button className="mv2-story-primary" type="button" disabled={!ready || step === last} onClick={() => setStep(value => Math.min(last, value + 1))}>{step === 0 ? 'Avvia la demo' : 'Passaggio successivo'}</button>
        <button type="button" disabled={!ready || step === 0} onClick={() => setStep(0)}>Ricomincia</button>
      </div>
      <p className="mv2-story-progress">Passaggio {step + 1} di {last + 1}</p>
    </div>
    <div className="mv2-story-scene" aria-label="Conversazione ed esito dimostrativo">
      <div className="mv2-story-window"><span></span><span></span><span></span><small>Ristorante demo · 13 ott 2026</small></div>
      <div className="mv2-story-chat">
        <p className="mv2-story-message-label">CLIENTE · MESSAGGIO</p>
        <p className="mv2-story-message">{scenario.customer}</p>
        <div className="mv2-story-response"><span>RISCONTRO</span><p>{scenarioId === 'team' ? 'Non ho informazioni sufficienti per rispondere.' : scenarioId === 'unavailable' && step >= 1 ? 'Per martedì 13 ottobre alle 19:30 risultano 0 posti disponibili.' : step >= 4 ? 'Conferma ricevuta. Disponibilità e capienza superano il controllo.' : step >= 2 ? `Per ${scenario.day} alle ${scenario.time} risultano 2 posti disponibili.` : step >= 1 ? 'Verifico la disponibilità per te.' : 'Certo, controllo la disponibilità per te.'}</p></div>
        {scenarioId === 'available' && step >= 3 && <p className="mv2-story-customer-confirmation">«Confermo, grazie.»</p>}
        <div className="mv2-story-result">
          <span className="mv2-story-result-index">0{step + 1}</span>
          <div><small>{state[0]}</small><p>{state[1]}</p></div>
        </div>
        {availabilityVisible && <section className="mv2-story-availability" aria-label="Verifica disponibilità"><h3>Verifica disponibilità</h3><dl><div><dt>Giorno</dt><dd>Martedì 13 ottobre 2026</dd></div><div><dt>Orario</dt><dd>19:30</dd></div><div><dt>Posti</dt><dd>{scenario.capacity} disponibili</dd></div></dl></section>}
        {canBook && step >= 5 && <div className="mv2-story-record"><header><strong>Prenotazioni</strong><span>Confermata nell’esempio</span></header><p>Cliente demo · 2 persone</p><time>{scenario.day} · {scenario.time}</time><footer>Conversazione in Inbox</footer></div>}
        {scenarioId === 'unavailable' && step === last && <p className="mv2-story-guard">Prenotazione non creata · nessun posto disponibile</p>}
        {scenarioId === 'team' && step === last && <div className="mv2-story-team-record"><strong>Inbox</strong><span>Richiesta sul menu</span><span>Da prendere in carico</span></div>}
      </div>
    </div>
    <p className="mv2-story-live" aria-live="polite" aria-atomic="true">{scenario.label}. {state[0]}.</p>
    <p className="mv2-story-disclosure">Demo illustrativa · dati sintetici</p>
  </div>;
}
