import React, { useState } from 'react';

interface SetupStep {
  id: string;
  stepTotal: string;
  tabTitle: string;
  tabDesc: string;
  cardTitle: string;
  cardDesc: string;
  rows: { label: string; value: string }[];
  hasCta?: boolean;
}

const STEPS: SetupStep[] = [
  {
    id: 'info',
    stepTotal: 'CONFIGURAZIONE GUIDATA',
    tabTitle: 'Le informazioni',
    tabDesc: 'Menu, allergeni, orari cucina e tono di voce del ristorante.',
    cardTitle: 'Le informazioni della sala',
    cardDesc: 'La base di conoscenza dell\'assistente: ciò che sa, come lo dice e con quale tono.',
    rows: [
      { label: 'Orari della cucina e della sala', value: 'Pranzo 12:00 - 15:00 • Cena 19:30 - 23:30' },
      { label: 'Menu alla carta e degustazione', value: 'Piatti, prezzi, allergeni e intolleranze' },
      { label: 'Politiche di cancellazione', value: 'Modifiche e no-show' },
      { label: 'FAQ e note utili', value: 'Parcheggio, cani ammessi, seggioloni' },
    ],
  },
  {
    id: 'turni',
    stepTotal: 'CONFIGURAZIONE GUIDATA',
    tabTitle: 'Il percorso e i turni',
    tabDesc: 'Durata media tavolo, coperti max e regole di approvazione.',
    cardTitle: 'Il percorso e le regole dei turni',
    cardDesc: 'Definisci come Melpis deve raccogliere le prenotazioni e quando chiedere conferma al responsabile di sala.',
    rows: [
      { label: 'Gestione turni serali', value: '1° turno 19:30 • 2° turno 21:30' },
      { label: 'Soglia approvazione manuale', value: 'Oltre 8 coperti -> Staff' },
      { label: 'Dati richiesti ai clienti', value: 'Nome, ospiti, orario, allergie' },
    ],
  },
  {
    id: 'canali',
    stepTotal: 'CONFIGURAZIONE GUIDATA',
    tabTitle: 'I canali e l\'agenda',
    tabDesc: 'WhatsApp Business e integrazioni in verifica.',
    cardTitle: 'I canali e Google Calendar',
    cardDesc: 'Il collegamento a WhatsApp Business segue i passaggi previsti da Meta.',
    rows: [
      { label: 'WhatsApp Business API', value: 'Numero ufficiale del ristorante' },
      { label: 'Google Calendar', value: 'In attesa di certificazione operativa' },
      { label: 'Instagram Direct & Messenger', value: 'Disponibilità da verificare' },
    ],
  },
  {
    id: 'verifica',
    stepTotal: 'CONFIGURAZIONE GUIDATA',
    tabTitle: 'La verifica in simulazione',
    tabDesc: 'Testa le risposte dal vivo prima di andare online.',
    cardTitle: 'La verifica e la messa online',
    cardDesc: 'Usa il Simulatore AI integrato per fare domande difficili a Melpis come se fossi un cliente prima di attivarlo.',
    rows: [
      { label: 'Simulatore AI integrato', value: 'Testa casi reali in sandbox' },
      { label: 'Rifinitura risposte', value: 'Aggiornamento istantaneo regole' },
      { label: 'Attivazione live in 1 clic', value: 'Pronto per il servizio' },
    ],
    hasCta: true,
  },
];

export default function RestaurantSetupAccordion() {
  const [activeStepIndex, setActiveStepIndex] = useState(0);
  const activeStep = STEPS[activeStepIndex];

  return (
    <div className="mv2-rest-setup-interactive-grid">
      {/* Left Column: Vertical Tabs */}
      <div className="mv2-rest-setup-tabs-col" role="tablist" aria-label="Passaggi di configurazione sala">
        {STEPS.map((step, idx) => {
          const isActive = idx === activeStepIndex;
          return (
            <button
              key={step.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-controls={`setup-panel-${step.id}`}
              id={`setup-tab-${step.id}`}
              className={`mv2-rest-setup-tab-item ${isActive ? 'is-active' : ''}`}
              onClick={() => setActiveStepIndex(idx)}
            >
              <div className="mv2-rest-setup-tab-header">
                <span className="mv2-rest-setup-tab-title">{step.tabTitle}</span>
                {isActive && <span className="mv2-rest-setup-tab-arrow" aria-hidden="true">→</span>}
              </div>
              <p className="mv2-rest-setup-tab-desc">{step.tabDesc}</p>
            </button>
          );
        })}
      </div>

      {/* Right Column: Accordion Stage */}
      <div className="mv2-rest-setup-stage-col">
        <div className="mv2-rest-setup-accordion-container">
          {STEPS.map((step, idx) => {
            const isActive = idx === activeStepIndex;

            if (isActive) {
              return (
                <div
                  key={step.id}
                  id={`setup-panel-${step.id}`}
                  role="tabpanel"
                  aria-labelledby={`setup-tab-${step.id}`}
                  className="mv2-rest-setup-panel-expanded"
                >
                  <div className="mv2-rest-setup-card-glow" aria-hidden="true" />
                  <div className="mv2-rest-setup-card-inner">
                    <span className="mv2-rest-setup-card-step-tag">{step.stepTotal}</span>
                    <h3 className="mv2-rest-setup-card-title">{step.cardTitle}</h3>
                    <p className="mv2-rest-setup-card-desc">{step.cardDesc}</p>

                    <div className="mv2-rest-setup-card-rows">
                      {step.rows.map((row, rIdx) => (
                        <div key={rIdx} className="mv2-rest-setup-card-row">
                          <span className="mv2-rest-setup-row-label">
                            <span className="mv2-rest-setup-dash-icon" aria-hidden="true">—</span>
                            {row.label}
                          </span>
                          <span className="mv2-rest-setup-row-value">{row.value}</span>
                        </div>
                      ))}
                    </div>

                    {step.hasCta && (
                      <div className="mv2-rest-setup-cta-wrap">
                        <a href="#mv2-rest-piani" className="mv2-rest-setup-btn-active">
                          <span>Esegui test rapido</span>
                          <span aria-hidden="true">→</span>
                        </a>
                      </div>
                    )}
                  </div>
                </div>
              );
            }

            return (
              <button
                key={step.id}
                type="button"
                className="mv2-rest-setup-panel-collapsed"
                onClick={() => setActiveStepIndex(idx)}
                aria-label={`Espandi passaggio: ${step.tabTitle}`}
              >
                <div className="mv2-rest-setup-collapsed-vertical-text">
                  <span className="mv2-rest-setup-vertical-label">{step.tabTitle.toUpperCase()}</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
