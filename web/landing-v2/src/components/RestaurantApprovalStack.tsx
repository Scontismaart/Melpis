import React from 'react';

export interface RestaurantApprovalStackProps {
  variant?: 'restaurants' | 'hotel' | 'barbieri' | 'saloni';
}

export default function RestaurantApprovalStack({
  variant = 'restaurants',
}: RestaurantApprovalStackProps) {
  const configs = {
    restaurants: {
      card1: {
        kicker: 'Richiesta prenotazione',
        title: 'Gruppo aziendale',
        f1Label: 'Data',
        f1Val: 'Ven 23 mag, 20:30',
        f2Label: 'Coperti',
        f2Val: '12 persone',
        f3Label: 'Area richiesta',
        f3Val: 'Sala principale',
        f4Label: 'Note',
        f4Val: 'Cena aziendale, con menù dedicato.',
      },
      card2: {
        kicker: 'Email di richiesta',
        sender: 'gruppo@azienda.it',
        subject: 'Prenotazione per 12 persone',
        body: 'Buongiorno, vorremmo prenotare un tavolo per 12 persone venerdì 23 mag per una cena aziendale.',
      },
      card3: {
        kicker: 'Approvazione prenotazione',
        title: 'Tavolo per 12 persone',
        body: 'La richiesta supera la soglia di 8 coperti e richiede la tua approvazione.',
      },
    },
    hotel: {
      card1: {
        kicker: 'Richiesta soggiorno speciale',
        title: 'Gruppo familiare',
        f1Label: 'Date',
        f1Val: '14–18 Giu (4 notti)',
        f2Label: 'Ospiti',
        f2Val: '6 persone (3 camere)',
        f3Label: 'Tipologia',
        f3Val: 'Matrimoniali comunicanti',
        f4Label: 'Note',
        f4Val: 'Richiesta culla neonato e piano alto.',
      },
      card2: {
        kicker: 'Messaggio WhatsApp',
        sender: '+39 347 556 7890',
        subject: 'Soggiorno 3 camere per 4 notti',
        body: 'Buongiorno, vorremmo riservare 3 camere matrimoniali vicine con culla da venerdì 14 a martedì 18 giugno.',
      },
      card3: {
        kicker: 'Verifica reception',
        title: 'Soggiorno oltre 2 camere',
        body: 'La richiesta supera la soglia di assegnazione automatica e richiede la verifica della reception.',
      },
    },
    barbieri: {
      card1: {
        kicker: 'Richiesta servizio speciale',
        title: 'Pacchetto Grooming Sposo',
        f1Label: 'Data',
        f1Val: 'Sab 21 giu, 10:30',
        f2Label: 'Durata',
        f2Val: '90 minuti',
        f3Label: 'Poltrona',
        f3Val: 'Poltrona 1 (VIP)',
        f4Label: 'Note',
        f4Val: 'Taglio rifinito, barba completa e trattamento cute.',
      },
      card2: {
        kicker: 'Messaggio WhatsApp',
        sender: '+39 338 987 6543',
        subject: 'Prenotazione servizio sposo',
        body: 'Ciao! Vorrei prenotare il pacchetto sposo per sabato mattina 21 giugno prima della cerimonia.',
      },
      card3: {
        kicker: 'Approvazione barbiere',
        title: 'Servizio oltre 60 minuti',
        body: 'La richiesta richiede l\'approvazione del barbiere per bloccare la poltrona dedicata.',
      },
    },
    saloni: {
      card1: {
        kicker: 'Richiesta percorso combinato',
        title: 'Viso Glow & Massaggio',
        f1Label: 'Data',
        f1Val: 'Ven 20 giu, 15:30',
        f2Label: 'Durata',
        f2Val: '120 minuti',
        f3Label: 'Cabina',
        f3Val: 'Cabina Relax 1',
        f4Label: 'Note',
        f4Val: 'Pulizia viso profonda abbinata a massaggio drenante.',
      },
      card2: {
        kicker: 'Messaggio WhatsApp',
        sender: '+39 349 876 5432',
        subject: 'Pacchetto viso e massaggio',
        body: 'Buongiorno! Vorrei combinare pulizia viso e massaggio drenante nello stesso pomeriggio.',
      },
      card3: {
        kicker: 'Approvazione cabina',
        title: 'Percorso oltre 90 minuti',
        body: 'La durata complessiva supera i 90 minuti e richiede la conferma dell\'operatrice di cabina.',
      },
    },
  };

  const cfg = configs[variant] || configs.restaurants;

  return (
    <div className="mv2-rest-approval-stage" aria-label="Showcase approvazione richieste">
      {/* Card 1: Richiesta prenotazione (Back / Top-Left) */}
      <div className="mv2-rest-approval-card mv2-rest-approval-card-1">
        <div className="mv2-rest-card-kicker">{cfg.card1.kicker}</div>
        <h3 className="mv2-rest-card-title">{cfg.card1.title}</h3>

        <div className="mv2-rest-card-fields">
          <div className="mv2-rest-card-field">
            <span className="mv2-rest-field-label">{cfg.card1.f1Label}</span>
            <span className="mv2-rest-field-value">{cfg.card1.f1Val}</span>
          </div>

          <div className="mv2-rest-card-field">
            <span className="mv2-rest-field-label">{cfg.card1.f2Label}</span>
            <span className="mv2-rest-field-value">{cfg.card1.f2Val}</span>
          </div>

          <div className="mv2-rest-card-field">
            <span className="mv2-rest-field-label">{cfg.card1.f3Label}</span>
            <span className="mv2-rest-field-value">{cfg.card1.f3Val}</span>
          </div>

          <div className="mv2-rest-card-field">
            <span className="mv2-rest-field-label">{cfg.card1.f4Label}</span>
            <span className="mv2-rest-field-value">{cfg.card1.f4Val}</span>
          </div>
        </div>
      </div>

      {/* Card 2: Messaggio / Email di richiesta (Middle / Staggered Center) */}
      <div className="mv2-rest-approval-card mv2-rest-approval-card-2">
        <div className="mv2-rest-card-kicker">{cfg.card2.kicker}</div>
        <div className="mv2-rest-card-email">{cfg.card2.sender}</div>

        <div className="mv2-rest-card-subject-group">
          <div className="mv2-rest-card-kicker">Oggetto</div>
          <div className="mv2-rest-card-subject">{cfg.card2.subject}</div>
        </div>

        <p className="mv2-rest-card-body">{cfg.card2.body}</p>
      </div>

      {/* Card 3: Approvazione (Front / Bottom-Right) */}
      <div className="mv2-rest-approval-card mv2-rest-approval-card-3">
        <div className="mv2-rest-card-kicker">{cfg.card3.kicker}</div>
        <h3 className="mv2-rest-card-title">{cfg.card3.title}</h3>

        <p className="mv2-rest-card-body mv2-rest-card-body--highlight">{cfg.card3.body}</p>

        <div className="mv2-rest-card-actions">
          <button type="button" className="mv2-rest-action-btn mv2-rest-action-btn--reject">
            Rifiuta
          </button>
          <button type="button" className="mv2-rest-action-btn mv2-rest-action-btn--approve">
            Approva
          </button>
        </div>
      </div>
    </div>
  );
}
