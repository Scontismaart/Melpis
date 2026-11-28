/**
 * Single source of truth for the /landing-v2/piani/ page.
 *
 * Verified against:
 *  - src/core/billing/plans.py (prices, message limits, operator limits, RAG flag)
 *  - src/core/billing/plans.py TRIAL_MESSAGES_LIMIT = 150 and the 7-day trial copy
 *  - docs/COMMERCIAL-SOURCES.md (approved monthly reference prices)
 *  - the copy already published in src/components/PricingSection.astro and FaqSection.astro
 *
 * Deliberately absent: annual billing, taxes, payment-method rules, renewal rules,
 * plan-change/cancellation policies and Google Business Profile review sync.
 * The Google review integration is not verified end-to-end, so it is not listed
 * as an available plan feature anywhere on this page.
 */

export const trial = {
  days: 7,
  replies: 150,
  label: 'Prova gratuita',
};

export const pageIntro = {
  title: 'Piani e prezzi',
  subnavTitle: 'Piani e prezzi',
  subnavMenuLabel: 'Esplora la pagina',
};

export const subnavLinks = [
  { href: '#mv2-pp-piani', label: 'Piani' },
  { href: '#mv2-pp-confronto', label: 'Confronto' },
  { href: '#mv2-pp-faq', label: 'FAQ' },
];

export const plans = [
  {
    slug: 'free',
    name: 'Prova gratuita',
    badge: 'Fino a 7 giorni o 150 risposte AI',
    subtitle: 'Fino a 7 giorni o 150 risposte AI, al primo limite raggiunto.',
    price: '0',
    currency: '€',
    period: '/ prova',
    priceNote: 'Fino a 150 risposte AI',
    priceLabel: '0 euro fino a 7 giorni o 150 risposte AI, al primo limite raggiunto',
    ctaLabel: 'Prova Melpis',
    ctaPath: '/registrati/',
    ctaAria: 'Prova Melpis, prova gratuita: vai alla registrazione',
    featured: false,
    icon: 'spark',
    plusHeading: '',
    features: [
      { strong: '150', text: 'risposte AI in totale' },
      { text: '1 numero WhatsApp Business' },
      { text: 'Risposte su orari, FAQ e listino' },
      { text: 'Inbox condivisa e passaggio allo staff' },
      { text: 'Gestione delle richieste di prenotazione' },
      { text: 'Accesso alla demo con dati sintetici' },
    ],
  },
  {
    slug: 'essenziale',
    name: 'Essenziale',
    badge: '',
    subtitle: 'Per iniziare a gestire le richieste senza essere sempre al telefono.',
    price: '29',
    currency: '€',
    period: '/ mese',
    priceNote: '',
    priceLabel: '29 euro al mese',
    ctaLabel: 'Prova Melpis',
    ctaPath: '/registrati/?piano=essential',
    ctaAria: 'Prova Melpis, piano Essenziale: vai alla registrazione',
    featured: false,
    icon: 'chat',
    plusHeading: '',
    features: [
      { strong: '500', text: 'risposte AI validate al mese' },
      { text: '1 numero WhatsApp Business' },
      { text: '1 account totale, titolare incluso' },
      { text: 'Risposte su orari, listini e FAQ' },
      { text: 'Inbox condivisa e passaggio allo staff' },
      { text: 'Gestione delle richieste di prenotazione' },
    ],
  },
  {
    slug: 'crescita',
    name: 'Crescita',
    badge: '',
    subtitle: 'Per le attività che ricevono più richieste e lavorano in squadra.',
    price: '69',
    currency: '€',
    period: '/ mese',
    priceNote: '',
    priceLabel: '69 euro al mese',
    ctaLabel: 'Prova Melpis',
    ctaPath: '/registrati/?piano=growth',
    ctaAria: 'Prova Melpis, piano Crescita: vai alla registrazione',
    featured: true,
    icon: 'team',
    plusHeading: 'Tutto ciò che è incluso in Essenziale, più:',
    features: [
      { strong: '2.000', text: 'risposte AI validate al mese' },
      { text: 'Fino a 3 account totali, titolare incluso' },
      { text: 'Bozze di risposta con revisione umana' },
    ],
  },
  {
    slug: 'scala',
    name: 'Scala',
    badge: '',
    subtitle: 'Per volumi elevati di conversazioni e più persone da coordinare.',
    price: '149',
    currency: '€',
    period: '/ mese',
    priceNote: '',
    priceLabel: '149 euro al mese',
    ctaLabel: 'Prova Melpis',
    ctaPath: '/registrati/?piano=scale',
    ctaAria: 'Prova Melpis, piano Scala: vai alla registrazione',
    featured: false,
    icon: 'layers',
    plusHeading: 'Tutto ciò che è incluso in Crescita, più:',
    features: [
      { strong: '10.000', text: 'risposte AI validate al mese' },
      { text: 'Account senza limite numerico di piano, soggetti ai limiti tecnici' },
      { text: 'Numeri WhatsApp senza limite numerico di piano, secondo le configurazioni supportate' },
    ],
  },
];

export const pageNotes = [
  'Prenotazioni con verifica disponibilità, inbox condivisa e intervento dello staff sono previsti su tutti i piani, secondo la configurazione. Google Calendar è opzionale e previsto per Essenziale, Crescita e Scala, con attivazione subordinata alla certificazione operativa. PDF/RAG è previsto per tutti e tre i piani, con attivazione subordinata al collaudo e all’approvazione di sicurezza.',
  'La prova gratuita dura 7 giorni oppure 150 risposte AI, a seconda di quale limite viene raggiunto per primo. Per iniziare non è richiesta una carta di credito. Qualsiasi addebito richiede l’adesione esplicita a un abbonamento.',
  'Prezzi di abbonamento mensili. L’attivazione delle integrazioni richiede i collaudi previsti.',
  'Costi WhatsApp Meta esclusi. Eventuali costi applicati da Meta per l’invio dei messaggi WhatsApp Business vengono addebitati separatamente in base al tipo di messaggio e al Paese del destinatario.',
];

export const comparison = {
  title: 'Confronta le funzionalità dei piani',
  note: 'Confronto dei piani a pagamento. La prova gratuita dura fino a 7 giorni o 150 risposte AI, al primo limite raggiunto.',
  plans: [
    { slug: 'essenziale', name: 'Essenziale', href: '/registrati/?piano=essential' },
    { slug: 'crescita', name: 'Crescita', href: '/registrati/?piano=growth' },
    { slug: 'scala', name: 'Scala', href: '/registrati/?piano=scale' },
  ],
  groups: [
    {
      id: 'assistente',
      label: 'Conversazioni e assistente AI',
      rows: [
        {
          label: 'Risposte su orari, servizi e FAQ',
          keywords: 'apertura chiusura orari servizi domande frequenti',
          values: [true, true, true],
        },
        {
          label: 'Risposte su listini e prezzi',
          keywords: 'listino prezzi menu tariffa',
          values: [true, true, true],
        },
        {
          label: 'Bozze di risposta con revisione umana',
          keywords: 'bozze suggerimenti approvazione revisione',
          values: [false, true, true],
        },
        {
          label: 'Passaggio della conversazione a un operatore',
          keywords: 'staff umano escalation inbox',
          values: [true, true, true],
        },
      ],
    },
    {
      id: 'conoscenza',
      label: 'Conoscenza aziendale',
      rows: [
        {
          label: "Informazioni e regole dell’attività",
          keywords: 'conoscenza base dati attivita regole',
          values: [true, true, true],
        },
        {
          label: 'Conoscenza aziendale da documenti (PDF/RAG)',
          keywords: 'documenti pdf file allegati',
          values: ['Previsto', 'Previsto', 'Previsto'],
        },
      ],
    },
    {
      id: 'canali',
      label: 'Canali di comunicazione',
      rows: [
        {
          label: 'WhatsApp Business',
          keywords: 'whatsapp canale messaggi',
          values: [true, true, true],
        },
        {
          label: 'Numero WhatsApp Business',
          keywords: 'numeri canali whatsapp business',
          values: ['1', '1', 'Senza limite di piano**'],
        },
      ],
    },
    {
      id: 'prenotazioni',
      label: 'Prenotazioni e disponibilità',
      rows: [
        {
          label: 'Verifica della disponibilità',
          keywords: 'disponibilita agenda appuntamenti calendario',
          values: [true, true, true],
        },
        {
          label: 'Gestione delle richieste di prenotazione',
          keywords: 'prenotazioni appuntamenti richieste',
          values: [true, true, true],
        },
        {
          label: 'Google Calendar (opzionale)',
          keywords: 'calendario google appuntamenti',
          values: ['Previsto', 'Previsto', 'Previsto'],
        },
      ],
    },
    {
      id: 'team',
      label: 'Team e accessi',
      rows: [
        {
          label: 'Account totali, titolare incluso',
          keywords: 'operatori team utenti collaboratori posti',
          values: ['1', 'Fino a 3', 'Senza limite di piano*'],
        },
        {
          label: 'Inbox condivisa',
          keywords: 'inbox condivisa conversazioni team',
          values: [true, true, true],
        },
      ],
    },
    {
      id: 'limiti',
      label: 'Limiti di utilizzo',
      rows: [
        {
          label: 'Risposte AI validate incluse ogni mese',
          keywords: 'quota risposte ai limiti consumo',
          values: ['500', '2.000', '10.000'],
        },
        {
          label: 'Passaggio al team oltre la quota',
          keywords: 'limite quota staff operatore',
          values: [true, true, true],
        },
      ],
    },
    {
      id: 'prezzi',
      label: 'Prezzi',
      rows: [
        {
          label: 'Prezzo mensile',
          keywords: 'prezzo costo mensile euro',
          values: ['29 €/mese', '69 €/mese', '149 €/mese'],
        },
      ],
    },
  ],
  footnotes: [
    '* Il piano Scala non applica un limite numerico di posti operatore previsto dal piano; restano i limiti tecnici del servizio.',
    '** Il piano Scala non applica un limite numerico di piano ai numeri WhatsApp. Valgono le configurazioni supportate.',
    'Google Calendar è opzionale e previsto per Essenziale, Crescita e Scala, con attivazione subordinata alla certificazione operativa. PDF/RAG è previsto per tutti e tre i piani, con attivazione subordinata al collaudo e all’approvazione di sicurezza.',
  ],
  emptyLabel: 'Nessuna funzionalità corrisponde alla ricerca.',
  searchLabel: 'Cerca nelle funzionalità',
  searchPlaceholder: 'Cerca',
  clearLabel: 'Cancella ricerca',
};

export const faq = {
  title: 'Domande frequenti',
  tabsLabel: 'Categorie delle domande frequenti',
  categories: [
    {
      id: 'piani',
      label: 'Piani e utilizzo',
      items: [
        {
          q: "Che differenza c’è tra la prova gratuita e i piani a pagamento?",
          a: "La prova gratuita dura fino a 7 giorni o 150 risposte AI, al primo limite raggiunto. Essenziale, Crescita e Scala prevedono una quota mensile di risposte AI e un numero crescente di account operatore.",
        },
        {
          q: 'Come funziona la prova gratuita?',
          a: 'La prova consente di usare Melpis per 7 giorni oppure per 150 risposte AI, a seconda di quale limite viene raggiunto per primo. Per iniziare non è richiesta una carta di credito. Qualsiasi addebito richiede l’adesione esplicita a un abbonamento.',
        },
        {
          q: 'Cosa succede quando raggiungo il limite di risposte AI?',
          a: "Puoi controllare il consumo dal pannello. Quando la quota del piano viene raggiunta, l’assistente interrompe le nuove risposte automatiche e le richieste vengono affidate al personale.",
        },
        {
          q: 'Le funzionalità sono le stesse su tutti i piani?',
          a: 'Prenotazioni con verifica disponibilità, inbox condivisa e intervento dello staff sono previsti su tutti i piani, secondo la configurazione. Google Calendar è opzionale e previsto per Essenziale, Crescita e Scala, con attivazione subordinata alla certificazione operativa. PDF/RAG è previsto per tutti e tre i piani, con attivazione subordinata al collaudo e all’approvazione di sicurezza. Cambiano la quota di risposte AI, gli account operatore, i numeri WhatsApp e i moduli inclusi.',
        },
        {
          q: 'Posso lavorare con più operatori?',
          a: 'Essenziale include 1 account totale e Crescita fino a 3, titolare incluso. Scala prevede account senza limite numerico di piano, soggetti ai limiti tecnici.',
        },
      ],
    },
    {
      id: 'fatturazione',
      label: 'Fatturazione e pagamenti',
      items: [
        {
          q: 'Quanto costano i piani?',
          a: 'I prezzi mensili sono 29 € per Essenziale, 69 € per Crescita e 149 € per Scala.',
        },
        {
          q: 'La prova gratuita richiede una carta di credito?',
          a: 'No, per iniziare la prova non è richiesta una carta di credito.',
        },
        {
          q: 'Quando si attivano le integrazioni?',
          a: 'Le integrazioni previste da ciascun piano si attivano una volta completati i collaudi e ottenute le approvazioni necessarie.',
        },
      ],
    },
    {
      id: 'configurazione',
      label: 'Configurazione e canali',
      items: [
        {
          q: 'Quali canali posso collegare?',
          a: 'Il canale previsto per Melpis V1 è WhatsApp Business. Il collegamento richiede un account idoneo, le autorizzazioni e i passaggi previsti da Meta.',
        },
        {
          q: 'Dove configuro le informazioni della mia attività?',
          a: 'Nella sezione Conoscenza del pannello puoi organizzare orari, servizi, listini e domande frequenti. Melpis usa queste informazioni per rispondere ai clienti.',
        },
        {
          q: 'Posso collegare Google Calendar?',
          a: 'Google Calendar è opzionale e previsto per Essenziale, Crescita e Scala, con attivazione subordinata alla certificazione operativa.',
        },
        {
          q: 'Le recensioni Google vengono sincronizzate?',
          a: "La sincronizzazione delle recensioni Google è ancora da verificare. Eventuali anteprime mostrano scenari illustrativi.",
        },
        {
          q: 'La demo sul sito invia messaggi reali?',
          a: 'No. La demo usa scenari e dati illustrativi. Non invia messaggi a clienti reali e non registra prenotazioni effettive.',
        },
      ],
    },
  ],
};
