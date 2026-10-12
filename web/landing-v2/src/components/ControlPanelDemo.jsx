import { useEffect, useState } from 'react';

export default function ControlPanelDemo() {
  const [active, setActive] = useState('team');
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setHydrated(true);
  }, []);

  const requestActive = active === 'request';
  const teamActive = active === 'team';

  return (
    <div className="mv2-control-visual">
      <div className="mv2-control-window">
        <div className="mv2-window-bar mv2-window-dark">
          <span /><span /><span /><small>Gestione conversazioni</small>
        </div>
        <div className="mv2-control-options" role="group" aria-label="Simulazione del passaggio al team">
          <button
            className={`mv2-control-row mv2-control-row-button${requestActive ? ' mv2-control-row-active' : ''}`}
            type="button"
            aria-pressed={requestActive}
            disabled={!hydrated}
            onClick={() => setActive('request')}
          >
            <span className="mv2-avatar">M</span>
            <span><strong>Richiesta fuori dalle informazioni</strong><small>Serve una verifica del team</small></span>
            <span className="mv2-control-pill">Da seguire</span>
          </button>
          <button
            className={`mv2-control-row mv2-control-row-button${teamActive ? ' mv2-control-row-active' : ''}`}
            type="button"
            aria-pressed={teamActive}
            disabled={!hydrated}
            onClick={() => setActive('team')}
          >
            <span className="mv2-avatar mv2-avatar-team">T</span>
            <span><strong>Team in conversazione</strong><small>La persona prende in carico</small></span>
            <span className={`mv2-control-pill${teamActive ? ' mv2-control-pill-active' : ''}`}>Al team</span>
          </button>
        </div>
        <p className="mv2-control-footer" aria-live="polite" aria-atomic="true">
          <span className="mv2-control-check" aria-hidden="true">✓</span>
          {teamActive ? 'Passaggio visibile e gestito dal pannello' : 'Richiesta in attesa di una verifica del team'}
        </p>
      </div>
      <div className="mv2-control-label">Esempio di gestione · non è un dato reale</div>
      <noscript><p className="mv2-control-label">Esempio statico: i controlli richiedono JavaScript.</p></noscript>
    </div>
  );
}
