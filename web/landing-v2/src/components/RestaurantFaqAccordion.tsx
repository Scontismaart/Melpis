import React, { useState } from 'react';

export interface FaqItem {
  q: string;
  a: string;
}

const DEFAULT_FAQS: FaqItem[] = [
  {
    q: 'Come fa Melpis a conoscere il nostro menu, i piatti del giorno e gli allergeni?',
    a: 'Puoi configurare menu, allergeni e opzioni alimentari nelle informazioni del locale. PDF/RAG previsto per tutti i piani, previa approvazione di sicurezza e collaudo. Per i piatti del giorno, puoi aggiornare la nota rapida in pochi secondi dall\'app o dal pannello prima del servizio.',
  },
  {
    q: 'Come vengono gestite le richieste per tavolate numerose o eventi privati?',
    a: 'Puoi impostare una soglia massima per la prenotazione automatica (es. 6 o 8 coperti). Oltre questa soglia, Melpis raccoglie i dettagli dell\'evento (numero invitati, budget, orario desiderato) e trasferisce la conversazione direttamente al direttore di sala con una notifica prioritaria.',
  },
  {
    q: 'Posso usare il numero WhatsApp che i clienti del ristorante conoscono già?',
    a: 'Sì. Melpis si integra tramite WhatsApp Business API ufficiale. Puoi mantenere il numero telefonico storico del tuo locale oppure configurarne uno dedicato esclusivamente alle prenotazioni e al servizio clienti.',
  },
  {
    q: 'Posso intervenire personalmente in una conversazione quando voglio?',
    a: 'In qualsiasi momento. La dashboard di Melpis mostra tutte le chat attive in tempo reale. Se vuoi rispondere personalmente a un cliente abituale o gestire una situazione particolare, puoi prendere il controllo della chat con un clic e l\'assistente si mette in pausa.',
  },
  {
    q: 'Melpis applica commissioni a coperto sulle prenotazioni ricevute?',
    a: 'No, nessuna commissione. I clienti prenotano direttamente sul tuo numero WhatsApp Business e i coperti confermati sono tuoi al 100%. Il canone Melpis è mensile e gli eventuali costi WhatsApp Meta sono separati.',
  },
];

interface RestaurantFaqAccordionProps {
  items?: FaqItem[];
  ariaLabel?: string;
}

export default function RestaurantFaqAccordion({
  items = DEFAULT_FAQS,
  ariaLabel = 'Domande frequenti',
}: RestaurantFaqAccordionProps) {
  const [openIndices, setOpenIndices] = useState<number[]>([]);

  const toggle = (idx: number) => {
    setOpenIndices((prev) =>
      prev.includes(idx) ? prev.filter((i) => i !== idx) : [...prev, idx]
    );
  };

  return (
    <div className="mv2-rest-faq-list" role="region" aria-label={ariaLabel}>
      {items.map((faq, idx) => {
        const isOpen = openIndices.includes(idx);
        return (
          <div key={idx} className={`mv2-rest-faq-item ${isOpen ? 'is-open' : ''}`}>
            <button
              type="button"
              className="mv2-rest-faq-trigger"
              onClick={() => toggle(idx)}
              aria-expanded={isOpen}
              aria-controls={`rest-faq-panel-${idx}`}
              id={`rest-faq-trig-${idx}`}
            >
              <span className="mv2-rest-faq-question">{faq.q}</span>
              <span className="mv2-rest-faq-icon" aria-hidden="true">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="8" y1="2" x2="8" y2="14" className="mv2-rest-faq-icon-v" />
                  <line x1="2" y1="8" x2="14" y2="8" />
                </svg>
              </span>
            </button>
            <div
              id={`rest-faq-panel-${idx}`}
              role="region"
              aria-labelledby={`rest-faq-trig-${idx}`}
              className="mv2-rest-faq-pane"
              hidden={!isOpen}
            >
              <p className="mv2-rest-faq-answer">{faq.a}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
