/**
 * Melpis — i18n Extended Copy Dictionary
 * Translations for sector pages and the documentation hub.
 */

const SECTOR_SHARED = {
  en: {
    trustCard: "No credit card required",
    trustTrial: "Full 7-day free trial",
    trustApi: "Official Meta Cloud API",
    ctaSecondary: "See how it works",
    stepsEyebrow: "Quick &amp; Easy Setup",
    faqEyebrow: "Frequently Asked Questions"
  },
  es: {
    trustCard: "Sin tarjeta de crédito",
    trustTrial: "Prueba completa de 7 días",
    trustApi: "API oficial Meta Cloud",
    ctaSecondary: "Cómo funciona",
    stepsEyebrow: "Activación Sencilla",
    faqEyebrow: "Preguntas Frecuentes"
  },
  fr: {
    trustCard: "Sans carte bancaire",
    trustTrial: "Essai complet de 7 jours",
    trustApi: "API officielle Meta Cloud",
    ctaSecondary: "Comment ça marche",
    stepsEyebrow: "Simplicité d'Activation",
    faqEyebrow: "Foire Aux Questions"
  },
  de: {
    trustCard: "Keine Kreditkarte nötig",
    trustTrial: "7 Tage vollwertige Testphase",
    trustApi: "Offizielle Meta Cloud API",
    ctaSecondary: "So funktioniert's",
    stepsEyebrow: "Einfache Einrichtung",
    faqEyebrow: "Häufig gestellte Fragen"
  }
};

const SECTOR_EXTRAS = {
  restaurants: {
    en: {
      vantaggiEyebrow: "Tailored for dining rooms &amp; kitchens",
      vantaggiTitle: "Concrete benefits for your restaurant",
      row1Eyebrow: "WhatsApp Automation",
      row1Title: "Instant WhatsApp replies and table bookings",
      row1Desc: "While the room is full and the kitchen is firing on all burners, every guest receives a reply in under 3 seconds. Melpis verifies available seats, records dietary requests, and confirms bookings without distracting your staff.",
      chatInAuthor: "Guest · 7:12 PM",
      chatInQuote: "“Good evening! Do you have a table for 4 tonight around 8:30 PM?”",
      chatOutAuthor: "Melpis for your restaurant",
      chatOutQuote: "“Table for 4 confirmed at 8:30 PM. I have noted your preference for indoor seating.”",
      chatSyncPill: "Google Calendar synchronized",
      row2Eyebrow: "Seating Shifts",
      row2Title: "Double shifts and optimized table turnover",
      row2Desc: "Configure dinner shifts (e.g. 1st shift 7:45 PM, 2nd shift 9:30 PM). If the first is full, Melpis smoothly suggests the second slot so you never lose a guest.",
      row3Eyebrow: "Contextual Intelligence",
      row3Title: "Menus, dietary allergies, and wine lists from PDF",
      row3Desc: "Upload your PDF menu and allergy list. Melpis answers with pinpoint precision on gluten-free, vegan dishes, and pairings without ever hallucinating.",
      row4Eyebrow: "Zero No-Shows",
      row4Title: "Interactive reminders and calendar synchronization",
      row4Desc: "Send an automated WhatsApp reminder ahead of service. Guests confirm or cancel with one tap, freeing up tables for waitlisted diners. Everything stays synced to Google Calendar.",
      noshowQuote: "“Dear Marco, friendly reminder about your table for 4 tonight at 8:30 PM at Osteria Bella Vista.”",
      noshowConfirm: "Confirm attendance",
      noshowCancel: "Cancel booking",
      noshowFeedback: "Attendance confirmed by guest · Synced with Google Calendar",
      stepsTitle: "Ready for your service in 3 steps",
      step1Title: "Connect your WhatsApp",
      step1Desc: "Connect your WhatsApp Business number via the official Meta Cloud API with enterprise security and full compliance.",
      step2Title: "Upload menus, hours, and shifts",
      step2Desc: "Upload your PDF menu, allergen list, opening hours, and seating preferences. Melpis replies strictly according to these facts.",
      step3Title: "Tables confirm automatically",
      step3Desc: "Every confirmed table appears straight on your Google Calendar and inside the Melpis Inbox, complete with allergy notes, highchair requests, and seating preferences.",
      faqTitle: "Everything you need to know for your restaurant",
      faqItems: [
        {
          q: "How does Melpis handle dinner seating shifts (e.g. 7:45 PM and 9:30 PM)?",
          a: "In your dashboard you define shift durations and start times. When a guest requests a booking, Melpis checks slot availability and suggests the next optimal shift if the first is fully booked."
        },
        {
          q: "What happens when a guest asks for specific ingredients or dietary allergies?",
          a: "Melpis references the uploaded menu PDF and allergen list. For complex requests or custom menu alterations, it logs a note in the reservation and alerts your staff."
        },
        {
          q: "How does the assistant reduce weekend no-shows?",
          a: "Melpis sends polite interactive WhatsApp reminders ahead of the reservation, allowing guests to confirm or cancel with a single tap so you can reassign tables quickly."
        },
        {
          q: "Can staff take over chats or halt bookings when the dining room is full?",
          a: "Yes. With the Manual Takeover button you can step into any thread instantly. You can also toggle online bookings off for the evening with one click."
        },
        {
          q: "How does the 7-day free trial work for my restaurant?",
          a: "Sign up with your venue name and email, no credit card required. You get 7 full days to upload menus, test AI responses, and manage real bookings before choosing a plan."
        }
      ],
      finalTitle: "Spend more time on food and hospitality.<br>Let Melpis manage the reservations.",
      finalSub: "Configure Melpis in a few simple steps and try it free for 7 days without a credit card.",
      finalBtn: "Activate the trial for your restaurant"
    },
    es: {
      vantaggiEyebrow: "Pensado para sala y cocina",
      vantaggiTitle: "Ventajas reales para tu restaurante",
      row1Eyebrow: "Automatización WhatsApp",
      row1Title: "Respuestas y reservas instantáneas por WhatsApp",
      row1Desc: "Mientras la sala está llena y los fogones a pleno rendimiento, cada cliente recibe respuesta en menos de 3 segundos. Melpis comprueba los comensales disponibles, registra las notas y confirma la reserva sin distraer al equipo.",
      chatInAuthor: "Cliente · 19:12",
      chatInQuote: "“¡Buenas tardes! ¿Tienen mesa para 4 esta noche sobre las 20:30?”",
      chatOutAuthor: "Melpis para tu local",
      chatOutQuote: "“Mesa para 4 confirmada a las 20:30. He anotado su preferencia por el salón interior.”",
      chatSyncPill: "Google Calendar sincronizado",
      row2Eyebrow: "Rotación de Sala",
      row2Title: "Turnos dobles y capacidad optimizada",
      row2Desc: "Configura turnos de noche (ej. 1º turno 19:45, 2º turno 21:30). Si el primero está completo, Melpis propone con naturalidad el segundo slot para no perder comensales.",
      row3Eyebrow: "Inteligencia Contextual",
      row3Title: "Menús, alérgenos y carta de vinos desde PDF",
      row3Desc: "Sube el PDF de tu carta y la lista de alérgenos. Melpis responde con precisión exacta sobre celiaquía, opciones veganas y maridajes sin improvisar jamás.",
      row4Eyebrow: "Cero No-Shows",
      row4Title: "Recordatorios interactivos y sincronización de agenda",
      row4Desc: "Envía un recordatorio automático por WhatsApp unas horas antes. El comensal confirma o cancela con un toque y la mesa queda libre para la lista de espera.",
      noshowQuote: "“Estimado Marco, le recordamos su mesa para 4 esta noche a las 20:30 en Osteria Bella Vista.”",
      noshowConfirm: "Confirmo asistencia",
      noshowCancel: "Debo cancelar",
      noshowFeedback: "Asistencia confirmada por el cliente · Sincronizado con Google Calendar",
      stepsTitle: "Listo para tu servicio en 3 pasos",
      step1Title: "Conecta tu WhatsApp",
      step1Desc: "Vincula tu número de WhatsApp Business a través de la API oficial de Meta Cloud con total seguridad y cumplimiento normativo.",
      step2Title: "Sube carta, horarios y turnos",
      step2Desc: "Carga el PDF de tu menú, alérgenos, horarios de apertura y preferencias de reserva. Melpis responderá única y exclusivamente con estos datos.",
      step3Title: "Las mesas se confirman solas",
      step3Desc: "Cada reserva confirmada aparece al instante en tu Google Calendar y en la bandeja de Melpis con notas sobre alergias, tronas o preferencias de mesa.",
      faqTitle: "Todo lo que necesitas saber para tu restaurante",
      faqItems: [
        {
          q: "¿Cómo gestiona los turnos dobles de noche (ej. 19:45 y 21:30)?",
          a: "En el panel defines la duración media y las horas de inicio. Cuando un cliente pide mesa, Melpis comprueba el aforo y ofrece automáticamente la mejor alternativa si el turno está lleno."
        },
        {
          q: "¿Qué sucede si un comensal consulta ingredientes o intolerancias complejas?",
          a: "Melpis responde consultando directamente la carta en PDF y la tabla de alérgenos subida al panel. Si la consulta supera las reglas, añade una nota a la reserva y avisa al equipo."
        },
        {
          q: "¿De qué forma ayuda el asistente a reducir el no-show en fin de semana?",
          a: "Melpis envía recordatorios automáticos por WhatsApp unas horas antes de la llegada, permitiendo a los clientes confirmar o cancelar en un clic para reasignar la mesa."
        },
        {
          q: "¿Podemos intervenir manualmente o cerrar reservas si la sala está llena?",
          a: "Sí. Con la opción de Pase al Operador puedes entrar en cualquier conversación al instante. Además, puedes pausar las reservas de la noche con un solo interruptor."
        },
        {
          q: "¿Cómo funciona la prueba gratuita de 7 días para mi restaurante?",
          a: "Regístrate con el nombre de tu restaurante y tu email, sin tarjeta de crédito. Tienes 7 días completos para subir tu menú y probar el servicio antes de decidir si continúas."
        }
      ],
      finalTitle: "Dedica más tiempo a la cocina y a la sala.<br>De las reservas se encarga Melpis.",
      finalSub: "Configura Melpis en pocos pasos y pruébalo gratis durante 7 días sin tarjeta de crédito.",
      finalBtn: "Activar la prueba para tu restaurante"
    },
    fr: {
      vantaggiEyebrow: "Conçu pour la salle et la cuisine",
      vantaggiTitle: "Les atouts concrets pour votre restaurant",
      row1Eyebrow: "Automatisation WhatsApp",
      row1Title: "Réponses et réservations instantanées sur WhatsApp",
      row1Desc: "Même en plein coup de feu quand la cuisine tourne à plein régime, chaque client reçoit une réponse en moins de 3 secondes. Melpis vérifie les couverts disponibles, enregistre les demandes et bloque la réservation.",
      chatInAuthor: "Client · 19h12",
      chatInQuote: "“Bonsoir ! Avez-vous une table pour 4 ce soir vers 20h30 ?”",
      chatOutAuthor: "Melpis pour votre établissement",
      chatOutQuote: "“Table pour 4 confirmée à 20h30. J'ai noté votre préférence pour la salle intérieure.”",
      chatSyncPill: "Google Calendar synchronisé",
      row2Eyebrow: "Gestion des Services",
      row2Title: "Double service et capacité optimisée",
      row2Desc: "Paramétrez vos services du soir (ex. 1er service à 19h45, 2nd service à 21h30). Si le premier est complet, Melpis oriente le client avec courtoisie vers le second créneau.",
      row3Eyebrow: "Intelligence Contextuelle",
      row3Title: "Menus, allergènes et carte des vins via PDF",
      row3Desc: "Importez le PDF de votre carte et vos allergènes. Melpis répond au détail près sur les intolérances au gluten, les plats végétariens et les suggestions sans jamais improviser.",
      row4Eyebrow: "Zéro No-Show",
      row4Title: "Rappels interactifs et synchronisation d'agenda",
      row4Desc: "Envoyez un rappel WhatsApp automatique avant le service. Le client valide ou annule en un clic, ce qui libère immédiatement la table pour votre liste d'attente.",
      noshowQuote: "“Cher Marco, rappel pour votre table de 4 ce soir à 20h30 à l'Osteria Bella Vista.”",
      noshowConfirm: "Je confirme",
      noshowCancel: "Annuler",
      noshowFeedback: "Présence confirmée par l'invité · Synchronisé avec Google Calendar",
      stepsTitle: "Opérationnel pour votre service en 3 étapes",
      step1Title: "Connectez votre WhatsApp",
      step1Desc: "Reliez votre numéro WhatsApp Business via l'API officielle Meta Cloud en toute sécurité et conformément aux exigences de conformité.",
      step2Title: "Importez menus, horaires et services",
      step2Desc: "Téléversez le PDF de votre carte, vos allergènes et vos horaires. Melpis répondra exclusivement sur la base de ces documents officiels.",
      step3Title: "Les tables se confirment toutes seules",
      step3Desc: "Chaque réservation confirmée s'inscrit en direct sur votre Google Calendar et dans la boîte Melpis avec les mentions utiles (allergies, chaise bébé, etc.).",
      faqTitle: "Tout ce que vous devez savoir pour votre restaurant",
      faqItems: [
        {
          q: "Comment fonctionne la gestion des doubles services (ex. 19h45 et 21h30) ?",
          a: "Depuis votre tableau de bord, vous définissez la durée moyenne et les heures de service. Quand un client sollicite une heure précise, Melpis contrôle la jauge et oriente vers le créneau disponible."
        },
        {
          q: "Que se passe-t-il si un client signale des allergies ou demande des plats sur mesure ?",
          a: "Melpis s'appuie directement sur votre carte PDF et la liste des allergènes. En cas de demande complexe ou non prévue, l'assistant ajoute une mention et prévient votre personnel."
        },
        {
          q: "De quelle manière l'assistant prévient-il les no-shows du week-end ?",
          a: "Melpis envoie un rappel courtois par WhatsApp quelques heures avant le service : le client confirme ou annule d'un simple clic pour libérer la table sans délai."
        },
        {
          q: "Pouvons-nous reprendre la main ou suspendre les réservations quand c'est complet ?",
          a: "Oui. Grâce au bouton de reprise en main, vous intervenez à tout moment. Vous pouvez également bloquer les réservations de la soirée en un clic."
        },
        {
          q: "Comment se déroule l'essai gratuit de 7 jours pour mon restaurant ?",
          a: "Inscrivez-vous avec le nom de votre établissement et votre e-mail, sans carte bancaire. Vous bénéficiez de 7 jours complets pour tester les réponses et les réservations."
        }
      ],
      finalTitle: "Consacrez plus de temps à la cuisine et au service.<br>Melpis gère les réservations.",
      finalSub: "Configurez Melpis en quelques étapes et testez-le gratuitement pendant 7 jours sans carte bancaire.",
      finalBtn: "Activer l'essai pour votre restaurant"
    },
    de: {
      vantaggiEyebrow: "Maßgeschneidert für Gastraum und Küche",
      vantaggiTitle: "Echte Vorteile für Ihren Gastronomiebetrieb",
      row1Eyebrow: "WhatsApp-Automation",
      row1Title: "Sofortige Antworten und Tischreservierungen über WhatsApp",
      row1Desc: "Selbst bei vollem Gastraum und Hochbetrieb in der Küche erhält jeder Gast in unter 3 Sekunden eine Antwort. Melpis prüft freie Plätze, erfasst Sonderwünsche und reserviert zuverlässig.",
      chatInAuthor: "Gast · 19:12",
      chatInQuote: "“Guten Abend! Haben Sie heute Abend gegen 20:30 Uhr einen Tisch für 4?”",
      chatOutAuthor: "Melpis für Ihren Betrieb",
      chatOutQuote: "“Tisch für 4 um 20:30 Uhr bestätigt. Ihr Wunsch nach Innenbereich ist vermerkt.”",
      chatSyncPill: "Google Calendar synchronisiert",
      row2Eyebrow: "Schichtverwaltung",
      row2Title: "Zwei Schichten und optimale Auslastung",
      row2Desc: "Richten Sie Abendschichten ein (z. B. 1. Schicht 19:45 Uhr, 2. Schicht 21:30 Uhr). Ist die erste belegt, schlägt Melpis höflich den zweiten Termin vor.",
      row3Eyebrow: "Kontextuelle Intelligenz",
      row3Title: "Speisekarten, Allergene und Weinauswahl aus PDF",
      row3Desc: "Laden Sie Ihre Menü-PDF und Allergenliste hoch. Melpis antwortet präzise zu glutenfreien Gerichten, veganen Optionen und Empfehlungen, ohne jemals zu halluzinieren.",
      row4Eyebrow: "Keine No-Shows",
      row4Title: "Interaktive Terminerinnerungen und Kalendersynchronisation",
      row4Desc: "Versenden Sie automatische WhatsApp-Erinnerungen vor dem Service. Gäste bestätigen oder stornieren mit einem Fingertipp – so wird der Tisch sofort wieder frei.",
      noshowQuote: "“Lieber Marco, wir erinnern an Ihren Tisch für 4 heute um 20:30 Uhr in der Osteria Bella Vista.”",
      noshowConfirm: "Ich bestätige",
      noshowCancel: "Stornieren",
      noshowFeedback: "Gäste-Teilnahme bestätigt · Mit Google Calendar synchronisiert",
      stepsTitle: "In 3 Schritten startklar für Ihren Service",
      step1Title: "WhatsApp verknüpfen",
      step1Desc: "Verbinden Sie Ihre WhatsApp Business-Nummer über die offizielle Meta Cloud API mit höchster Sicherheit und voller DSGVO-Konformität.",
      step2Title: "Speisekarte, Zeiten &amp; Schichten hochladen",
      step2Desc: "Laden Sie Speisekarte, Allergene und Öffnungszeiten hoch. Melpis antwortet ausschließlich auf Grundlage dieser verifizierten Unterlagen.",
      step3Title: "Tische bestätigen sich automatisch",
      step3Desc: "Jede bestätigte Reservierung erscheint direkt in Ihrem Google Calendar und im Melpis-Posteingang, inklusive Hinweisen zu Unverträglichkeiten oder Kinderstühlen.",
      faqTitle: "Alles Wissenswerte für Ihren Gastronomiebetrieb",
      faqItems: [
        {
          q: "Wie handhabt das System Abendschichten (z. B. 19:45 und 21:30 Uhr)?",
          a: "Im Dashboard legen Sie Schichtzeiten und Verweildauern fest. Fragt ein Gast nach einem Termin, prüft Melpis die Kapazität und bietet bei Vollbelegung die nächste Schicht an."
        },
        {
          q: "Was passiert, wenn Gäste nach Allergenen oder Zutaten fragen?",
          a: "Melpis greift direkt auf Ihre hochgeladene Menü-PDF und Allergentabelle zu. Bei komplexen Sonderwünschen vermerkt der Assistent dies und leitet an Ihr Personal weiter."
        },
        {
          q: "Wie hilft Melpis, No-Shows am Wochenende zu reduzieren?",
          a: "Melpis versendet rechtzeitig vor Beginn interaktive WhatsApp-Erinnerungen. Gäste können mit einem Klick bestätigen oder absagen, sodass Tische nachbesetzt werden."
        },
        {
          q: "Können Mitarbeiter manuell eingreifen oder bei Vollbelegung stoppen?",
          a: "Ja. Mit der Mitarbeiterübernahme können Sie jederzeit die Konversation übernehmen. Zudem lässt sich die Online-Reservierung für den Abend mit einem Klick pausieren."
        },
        {
          q: "Wie läuft die 7-tägige kostenlose Testphase für meinen Betrieb ab?",
          a: "Registrieren Sie sich einfach mit Ihrem Betriebsnamen und E-Mail ohne Kreditkarte. Sie haben 7 Tage lang vollen Zugriff, um das System live zu testen."
        }
      ],
      finalTitle: "Konzentrieren Sie sich auf Küche und Gäste.<br>Die Reservierungen regelt Melpis.",
      finalSub: "Richten Sie Melpis in wenigen Schritten ein und testen Sie es 7 Tage kostenlos ohne Kreditkarte.",
      finalBtn: "Testphase für Ihren Betrieb starten"
    }
  },
  beauty: {
    en: {
      vantaggiEyebrow: "Tailored for salons, barbers &amp; stylists",
      vantaggiTitle: "Concrete benefits for your salon",
      stepsTitle: "Ready for your salon in 3 steps",
      step1Title: "Connect your WhatsApp",
      step1Desc: "Connect your WhatsApp Business number via the official Meta Cloud API with enterprise reliability.",
      step2Title: "Upload treatment list &amp; staff hours",
      step2Desc: "Add treatments, individual operator schedules, and service durations for seamless automated bookings.",
      step3Title: "Appointments book themselves",
      step3Desc: "Clients book appointments directly into Google Calendar while you focus on the client in the styling chair.",
      faqTitle: "Everything you need to know for your salon",
      faqItems: [
        {
          q: "Can clients book with a specific stylist or aesthetician?",
          a: "Yes. Melpis checks availability for each operator independently and books the appointment directly in their individual calendar."
        },
        {
          q: "How does the assistant calculate combined treatments (e.g. cut + color)?",
          a: "You define the duration for each individual treatment. Melpis automatically sums required slots and finds back-to-back open intervals."
        },
        {
          q: "How do automated appointment reminders prevent cancellations?",
          a: "Melpis sends polite reminders 24 hours prior via WhatsApp, allowing clients to confirm or reschedule without staff intervention."
        },
        {
          q: "Can I manage emergency block-outs or staff sick leave?",
          a: "Yes. Blocking hours in Google Calendar or toggling unavailability in the dashboard instantly updates the assistant's booking slots."
        },
        {
          q: "How does the 7-day free trial work for beauty salons?",
          a: "Sign up in 2 minutes without a credit card. You can configure your services, staff, and test bookings immediately for 7 days."
        }
      ],
      finalTitle: "More focus on your salon clients.<br>Let Melpis manage your appointments.",
      finalSub: "Configure Melpis in a few steps and test it free for 7 days without a credit card.",
      finalBtn: "Activate the trial for your salon"
    },
    es: {
      vantaggiEyebrow: "Pensado para salones, barberos y estilistas",
      vantaggiTitle: "Ventajas reales para tu salón",
      stepsTitle: "Listo para tu salón en 3 pasos",
      step1Title: "Conecta tu WhatsApp",
      step1Desc: "Vincula tu WhatsApp Business a través de la API oficial de Meta Cloud con total seguridad y fiabilidad.",
      step2Title: "Sube servicios, tarifas y estilistas",
      step2Desc: "Añade tratamientos, turnos por profesional y duraciones para una gestión de citas impecable.",
      step3Title: "Las citas se agendan solas",
      step3Desc: "Los clientes reservan directamente en Google Calendar mientras tú atiendes sin distracciones a tus clientes en el salón.",
      faqTitle: "Todo lo que necesitas saber para tu salón",
      faqItems: [
        {
          q: "¿Pueden los clientes reservar con su estilista preferido?",
          a: "Sí. Melpis comprueba la disponibilidad de cada profesional por separado y sincroniza la cita en su agenda personal."
        },
        {
          q: "¿Cómo gestiona el asistente los servicios combinados (ej. corte + tinte)?",
          a: "Configuras la duración de cada servicio y Melpis calcula el tiempo total continuo para encajar la cita en un bloque libre."
        },
        {
          q: "¿De qué forma evitan las cancelaciones los avisos automáticos?",
          a: "Melpis envía recordatorios 24 horas antes por WhatsApp, permitiendo confirmar o cambiar cita sin que tengas que llamar."
        },
        {
          q: "¿Puedo bloquear franjas horarias por descansos o imprevistos?",
          a: "Sí. Cualquier bloqueo que añadas en Google Calendar o en el panel actualiza al instante las horas disponibles del asistente."
        },
        {
          q: "¿Cómo funciona la prueba gratuita de 7 días para salones?",
          a: "Empieza en 2 minutos sin tarjeta de crédito. Tienes 7 días para configurar tu carta de servicios y comprobar el ahorro de tiempo."
        }
      ],
      finalTitle: "Más atención a tus clientes en el salón.<br>De las citas se encarga Melpis.",
      finalSub: "Configura Melpis en pocos pasos y pruébalo gratis durante 7 días sin tarjeta de crédito.",
      finalBtn: "Activar la prueba para tu salón"
    },
    fr: {
      vantaggiEyebrow: "Conçu pour les salons, barbiers et stylistes",
      vantaggiTitle: "Les atouts concrets pour votre salon",
      stepsTitle: "Opérationnel pour votre salon en 3 étapes",
      step1Title: "Connectez votre WhatsApp",
      step1Desc: "Reliez votre compte WhatsApp Business via l'API officielle Meta Cloud avec un niveau de sécurité optimal.",
      step2Title: "Renseignez soins, tarifs et collaborateurs",
      step2Desc: "Indiquez les prestations, la durée des soins et les plannings par collaborateur pour automatiser les prises de rendez-vous.",
      step3Title: "Les rendez-vous se réservent tout seuls",
      step3Desc: "Les clients planifient directement dans Google Calendar pendant que vous vous consacrez pleinement à vos prestations.",
      faqTitle: "Tout ce que vous devez savoir pour votre salon",
      faqItems: [
        {
          q: "Les clients peuvent-ils choisir un collaborateur en particulier ?",
          a: "Oui. Melpis vérifie les disponibilités individuelles de chaque praticien et synchronise le rendez-vous dans son calendrier."
        },
        {
          q: "Comment sont calculées les prestations combinées (ex. coupe + coloration) ?",
          a: "Vous renseignez la durée de chaque prestation. Melpis additionne le temps nécessaire et trouve un créneau consécutif adapté."
        },
        {
          q: "Comment les rappels automatiques évitent-ils les rendez-vous manqués ?",
          a: "Melpis transmet un rappel 24 heures avant par WhatsApp. Le client peut confirmer ou reprogrammer son créneau en toute autonomie."
        },
        {
          q: "Est-il possible de bloquer des créneaux en cas d'absence imprévue ?",
          a: "Oui. Tout événement bloqué dans Google Calendar ou dans le tableau de bord actualise instantanément les créneaux proposés."
        },
        {
          q: "Comment se déroule l'essai gratuit de 7 jours pour les salons ?",
          a: "Lancez votre essai en 2 minutes sans carte bancaire. Vous disposez de 7 jours complets pour tester la réservation automatisée."
        }
      ],
      finalTitle: "Plus d'attention pour vos clients au salon.<br>Melpis s'occupe de vos rendez-vous.",
      finalSub: "Configurez Melpis en quelques étapes et testez-le gratuitement pendant 7 jours sans carte bancaire.",
      finalBtn: "Activer l'essai pour votre salon"
    },
    de: {
      vantaggiEyebrow: "Maßgeschneidert für Salons, Barbiere und Stylisten",
      vantaggiTitle: "Echte Vorteile für Ihren Salon",
      stepsTitle: "In 3 Schritten startklar für Ihren Salon",
      step1Title: "WhatsApp verknüpfen",
      step1Desc: "Verbinden Sie Ihre WhatsApp Business-Nummer über die offizielle Meta Cloud API für höchste Zuverlässigkeit.",
      step2Title: "Leistungen, Preise &amp; Mitarbeiter hinterlegen",
      step2Desc: "Tragen Sie Behandlungen, Behandlungsdauern und Arbeitszeiten Ihrer Mitarbeiter für nahtlose Buchungen ein.",
      step3Title: "Termine buchen sich von selbst",
      step3Desc: "Kunden buchen freie Zeiten direkt in Google Calendar, während Sie sich ganz auf den Kunden im Stuhl konzentrieren.",
      faqTitle: "Alles Wissenswerte für Ihren Salon",
      faqItems: [
        {
          q: "Können Kunden Termine bei bestimmten Mitarbeitern buchen?",
          a: "Ja. Melpis prüft die Verfügbarkeit jedes Stylisten separat und trägt den Termin direkt in den jeweiligen Kalender ein."
        },
        {
          q: "Wie berechnet der Assistent Kombi-Behandlungen (z. B. Schnitt + Farbe)?",
          a: "Sie hinterlegen die Dauer für jede Einzelleistung. Melpis addiert die benötigten Zeiten und findet passende zusammenhängende Slots."
        },
        {
          q: "Wie verhindern automatische Erinnerungen Terminausfälle?",
          a: "Melpis versendet 24 Stunden vorher eine WhatsApp-Erinnerung. Kunden können mit einem Fingertipp bestätigen oder verschieben."
        },
        {
          q: "Können Pausen oder spontane Ausfälle blockiert werden?",
          a: "Ja. Jeder in Google Calendar eingetragene Blocker wird in Echtzeit von Melpis für Buchungen gesperrt."
        },
        {
          q: "Wie funktioniert die 7-tägige Testphase für Salons?",
          a: "Starten Sie in 2 Minuten ohne Kreditkarte. Sie können 7 Tage lang Ihre Leistungen hinterlegen und Kundenchats testen."
        }
      ],
      finalTitle: "Mehr Zeit für Ihre Kunden im Salon.<br>Die Terminvergabe regelt Melpis.",
      finalSub: "Richten Sie Melpis in wenigen Schritten ein und testen Sie es 7 Tage kostenlos ohne Kreditkarte.",
      finalBtn: "Testphase für Ihren Salon starten"
    }
  },
  medical: {
    en: {
      vantaggiEyebrow: "Tailored for medical &amp; dental practices",
      vantaggiTitle: "Concrete benefits for your medical clinic",
      stepsTitle: "Ready for your clinic in 3 steps",
      step1Title: "Connect your WhatsApp",
      step1Desc: "Connect your official WhatsApp number under strict GDPR encryption and zero public AI model training.",
      step2Title: "Set medical intake rules &amp; hours",
      step2Desc: "Upload clinic hours, preparation guidelines for checkups, and strict clinical fail-closed escalation triggers.",
      step3Title: "Safe, automated patient intake",
      step3Desc: "Routine patient queries and visit slots are handled automatically, leaving staff free for on-site care.",
      faqTitle: "Everything you need to know for your clinic",
      faqItems: [
        {
          q: "How does Melpis comply with strict GDPR health data regulations?",
          a: "All patient messages are processed with tenant isolation, Fernet credential encryption, and zero third-party AI training."
        },
        {
          q: "What happens in case of acute medical emergencies?",
          a: "The assistant applies a strict fail-closed clinical rule: it directs patients immediately to emergency services and alerts clinic staff."
        },
        {
          q: "Can patients receive appointment preparation instructions?",
          a: "Yes. Melpis can deliver fasting guidelines, required documents, or pre-visit instructions automatically upon appointment confirmation."
        },
        {
          q: "How does staff takeover work from the reception desk?",
          a: "Reception staff can claim any patient conversation with one click, viewing the full previous dialogue history."
        },
        {
          q: "How does the 7-day free trial work for medical practices?",
          a: "Register with your clinic name and email without a credit card. Test pre-visit triage and patient messaging for 7 full days."
        }
      ],
      finalTitle: "More time dedicated to patient care.<br>Let Melpis manage front-desk communications.",
      finalSub: "Configure Melpis in a few steps and test it free for 7 days without a credit card.",
      finalBtn: "Activate the trial for your clinic"
    },
    es: {
      vantaggiEyebrow: "Pensado para médicos, especialistas y dentistas",
      vantaggiTitle: "Ventajas reales para tu clínica médica",
      stepsTitle: "Listo para tu consulta en 3 pasos",
      step1Title: "Conecta tu WhatsApp",
      step1Desc: "Vincula tu WhatsApp con cifrado de nivel médico y cumplimiento estricto del RGPD sin entrenamiento de modelos externos.",
      step2Title: "Define reglas de triaje y horarios",
      step2Desc: "Configura horarios de consulta, pautas de preparación para visitas y criterios de derivación clínica inmediata.",
      step3Title: "Atención al paciente segura y fluida",
      step3Desc: "Las consultas rutinarias se gestionan solas, liberando al personal de recepción para atender a los pacientes presenciales.",
      faqTitle: "Todo lo que necesitas saber para tu clínica",
      faqItems: [
        {
          q: "¿Cómo garantiza Melpis el cumplimiento del RGPD en datos de salud?",
          a: "Los datos de los pacientes cuentan con aislamiento estricto por centro, cifrado de credenciales y no se usan para entrenar IA de terceros."
        },
        {
          q: "¿Cómo responde el asistente ante urgencias médicas?",
          a: "Aplica un protocolo fail-closed estricto: indica de inmediato acudir a urgencias o llamar al 112 y notifica al personal del centro."
        },
        {
          q: "¿Pueden enviarse instrucciones previas a la consulta?",
          a: "Sí. Melpis informa sobre ayuno, documentación requerida o pautas previas en cuanto se confirma la cita."
        },
        {
          q: "¿Cómo toma el control el personal de recepción si es necesario?",
          a: "La recepción puede entrar en cualquier chat con un solo clic, visualizando el historial completo de la interacción."
        },
        {
          q: "¿Cómo funciona la prueba gratuita de 7 días para centros médicos?",
          a: "Date de alta con el nombre de tu clínica y tu email sin tarjeta de crédito. Prueba el triaje y la gestión de citas durante 7 días."
        }
      ],
      finalTitle: "Más tiempo para el cuidado de tus pacientes.<br>De la secretaría se encarga Melpis.",
      finalSub: "Configura Melpis en pocos pasos y pruébalo gratis durante 7 días sin tarjeta de crédito.",
      finalBtn: "Activar la prueba para tu clínica"
    },
    fr: {
      vantaggiEyebrow: "Conçu pour les médecins, spécialistes et dentistes",
      vantaggiTitle: "Les atouts concrets pour votre cabinet médical",
      stepsTitle: "Opérationnel pour votre cabinet en 3 étapes",
      step1Title: "Connectez votre WhatsApp",
      step1Desc: "Reliez votre WhatsApp officiel avec chiffrement strict, conformité RGPD et aucun entraînement de modèles publics.",
      step2Title: "Définissez consignes et plages de consultation",
      step2Desc: "Paramétrez vos créneaux, les consignes pré-visite et les règles de bascule clinique immédiate.",
      step3Title: "Accueil des patients fluide et sécurisé",
      step3Desc: "Les demandes courantes sont traitées automatiquement, libérant le secrétariat pour l'accueil sur place.",
      faqTitle: "Tout ce que vous devez savoir pour votre cabinet",
      faqItems: [
        {
          q: "Comment Melpis protège-t-il les données de santé conformément au RGPD ?",
          a: "Tous les échanges sont cloisonnés par établissement, chiffrés avec Fernet et exclus de tout entraînement de modèles tiers."
        },
        {
          q: "Quelle est la procédure en cas d'urgence médicale aiguë ?",
          a: "L'assistant applique une règle fail-closed stricte : il oriente aussitôt vers les services d'urgence (15/112) et prévient le cabinet."
        },
        {
          q: "Les patients peuvent-ils recevoir les consignes de préparation aux examens ?",
          a: "Oui. Melpis rappelle les consignes (jeûne, documents à apporter, antécédents) dès la confirmation du rendez-vous."
        },
        {
          q: "Comment le secrétariat reprend-il la main sur une conversation ?",
          a: "Le personnel d'accueil peut intervenir en un clic sur n'importe quel échange avec l'historique complet sous les yeux."
        },
        {
          q: "Comment se déroule l'essai gratuit de 7 jours pour les cabinets médicaux ?",
          a: "Inscrivez-vous avec le nom de votre structure et votre e-mail, sans carte bancaire. Testez l'accueil patient pendant 7 jours."
        }
      ],
      finalTitle: "Plus de temps pour soigner vos patients.<br>Melpis prend en charge le secrétariat.",
      finalSub: "Configurez Melpis en quelques étapes et testez-le gratuitement pendant 7 jours sans carte bancaire.",
      finalBtn: "Activer l'essai pour votre cabinet"
    },
    de: {
      vantaggiEyebrow: "Maßgeschneidert für Ärzte, Fachpraxen und Zahnärzte",
      vantaggiTitle: "Echte Vorteile für Ihre Arztpraxis",
      stepsTitle: "In 3 Schritten startklar für Ihre Praxis",
      step1Title: "WhatsApp verknüpfen",
      step1Desc: "Verbinden Sie Ihre offizielle WhatsApp-Nummer mit DSGVO-Verschlüsselung und ohne Training öffentlicher KI-Modelle.",
      step2Title: "Praxisregeln &amp; Sprechzeiten hinterlegen",
      step2Desc: "Erfassen Sie Sprechzeiten, Vorbereitungsrichtlinien für Behandlungen und Notfall-Weiterleitungsregeln.",
      step3Title: "Sicherer und entlasteter Patientenempfang",
      step3Desc: "Routinefragen werden automatisiert beantwortet, sodass der Empfang den Rücken frei hat für Patienten vor Ort.",
      faqTitle: "Alles Wissenswerte für Ihre Praxis",
      faqItems: [
        {
          q: "Wie gewährleistet Melpis den Schutz sensibler Gesundheitsdaten (DSGVO)?",
          a: "Patientendaten werden strikt mandantenspezifisch isoliert, verschlüsselt gespeichert und keinesfalls für Drittmodelle genutzt."
        },
        {
          q: "Was geschieht bei akuten medizinischen Notfällen?",
          a: "Der Assistent wendet ein strenges klinisches Fail-Closed an: Er verweist sofort auf den Notruf (112) und benachrichtigt das Team."
        },
        {
          q: "Können Patienten Hinweise zur Untersuchungsvorbereitung erhalten?",
          a: "Ja. Melpis übermittelt wichtige Vorbereitungshinweise (Nüchternheit, mitzubringende Befunde) direkt bei der Terminbestätigung."
        },
        {
          q: "Wie übernimmt das Praxisteam bei Bedarf die Konversation?",
          a: "Das Personal kann sich mit einem Klick in jeden Chat einschalten und den gesamten bisherigen Verlauf einsehen."
        },
        {
          q: "Wie funktioniert die 7-tägige Testphase für Arztpraxen?",
          a: "Melden Sie sich mit Praxisnamen und E-Mail ohne Kreditkarte an. Testen Sie den Patientenempfang 7 Tage lang unverbindlich."
        }
      ],
      finalTitle: "Mehr Zeit für die Behandlung Ihrer Patienten.<br>Den Telefonservice übernimmt Melpis.",
      finalSub: "Richten Sie Melpis in wenigen Schritten ein und testen Sie es 7 Tage kostenlos ohne Kreditkarte.",
      finalBtn: "Testphase für Ihre Praxis starten"
    }
  },
  hotels: {
    en: {
      vantaggiEyebrow: "Tailored for hotels, B&amp;Bs &amp; boutique resorts",
      vantaggiTitle: "Concrete benefits for your hospitality business",
      stepsTitle: "Ready for your property in 3 steps",
      step1Title: "Connect your WhatsApp",
      step1Desc: "Connect your official WhatsApp Business concierge number to assist guests before, during, and after their stay.",
      step2Title: "Upload property guide &amp; amenities",
      step2Desc: "Upload check-in instructions, parking codes, breakfast hours, and local recommendations into the knowledge base.",
      step3Title: "24/7 multilingual guest concierge",
      step3Desc: "Guests receive instant answers in their native language, taking the load off your front desk day and night.",
      faqTitle: "Everything you need to know for your property",
      faqItems: [
        {
          q: "Can Melpis communicate with international guests in multiple languages?",
          a: "Yes. Melpis natively understands and replies in English, Italian, German, French, Spanish, and over 40 languages automatically."
        },
        {
          q: "How does the assistant handle late check-ins and access codes?",
          a: "Melpis delivers verified keypad codes, parking directions, and arrival instructions automatically at guest check-in time."
        },
        {
          q: "Can guests book hotel amenities like restaurant tables or bike rentals?",
          a: "Yes. The assistant answers inquiries on in-house services, spa bookings, and local recommendations according to your rules."
        },
        {
          q: "How does front-desk collaboration work across devices?",
          a: "Staff can monitor all guest conversations from smartphones, tablets, or front-desk computers and intervene anytime."
        },
        {
          q: "How does the 7-day free trial work for hospitality businesses?",
          a: "Get started in minutes with no credit card required. Upload your hotel guide and test the concierge live for 7 days."
        }
      ],
      finalTitle: "Elevate every guest experience.<br>Let Melpis power your WhatsApp concierge.",
      finalSub: "Configure Melpis in a few steps and test it free for 7 days without a credit card.",
      finalBtn: "Activate the trial for your hotel"
    },
    es: {
      vantaggiEyebrow: "Pensado para hoteles, B&amp;B y alojamientos con encanto",
      vantaggiTitle: "Ventajas reales para tu hotel",
      stepsTitle: "Listo para tu alojamiento en 3 pasos",
      step1Title: "Conecta tu WhatsApp",
      step1Desc: "Vincula tu WhatsApp oficial para atender a los huéspedes antes, durante y después de su estancia.",
      step2Title: "Sube guías, servicios y horarios",
      step2Desc: "Carga instrucciones de check-in, códigos de acceso, horarios de desayuno y recomendaciones locales.",
      step3Title: "Conserje virtual 24/7 multilingüe",
      step3Desc: "Los viajeros reciben respuestas inmediatas en su idioma, aliviando la carga de trabajo en recepción.",
      faqTitle: "Todo lo que necesitas saber para tu alojamiento",
      faqItems: [
        {
          q: "¿Puede Melpis comunicarse con huéspedes internacionales en varios idiomas?",
          a: "Sí. Melpis comprende y responde de forma natural en español, inglés, alemán, francés, italiano y más de 40 idiomas."
        },
        {
          q: "¿Cómo gestiona el asistente los check-in tardíos y códigos de acceso?",
          a: "Envía códigos de apertura, ubicación GPS del parking y normas de llegada a la hora programada para cada huésped."
        },
        {
          q: "¿Pueden los huéspedes reservar servicios como spa, bicicletas o restaurante?",
          a: "Sí. Melpis atiende solicitudes sobre servicios del hotel, reservas internas y consejos turísticos según tus directrices."
        },
        {
          q: "¿Cómo colabora el personal de recepción desde distintos dispositivos?",
          a: "El equipo puede supervisar las conversaciones desde móviles, tablets o el ordenador de recepción y responder cuando quiera."
        },
        {
          q: "¿Cómo funciona la prueba gratuita de 7 días para hoteles?",
          a: "Comienza en pocos minutos sin tarjeta de crédito. Sube la información de tu hotel y prueba el conserje virtual 7 días."
        }
      ],
      finalTitle: "Mejora la experiencia de cada viajero.<br>Del conserje en WhatsApp se encarga Melpis.",
      finalSub: "Configura Melpis en pocos pasos y pruébalo gratis durante 7 días sin tarjeta de crédito.",
      finalBtn: "Activar la prueba para tu hotel"
    },
    fr: {
      vantaggiEyebrow: "Conçu pour les hôtels, chambres d'hôtes et résidences",
      vantaggiTitle: "Les atouts concrets pour votre établissement hôtelier",
      stepsTitle: "Opérationnel pour votre établissement en 3 étapes",
      step1Title: "Connectez votre WhatsApp",
      step1Desc: "Reliez votre numéro WhatsApp officiel pour accompagner vos clients avant, pendant et après leur séjour.",
      step2Title: "Renseignez livret d'accueil et services",
      step2Desc: "Importez les consignes d'arrivée, codes d'accès, horaires du petit-déjeuner et recommandations touristiques.",
      step3Title: "Conciergerie virtuelle 24/7 multilingue",
      step3Desc: "Vos voyageurs obtiennent des réponses instantanées dans leur langue maternelle, soulageant la réception.",
      faqTitle: "Tout ce que vous devez savoir pour votre hôtel",
      faqItems: [
        {
          q: "Melpis peut-il échanger avec les voyageurs internationaux dans leur langue ?",
          a: "Oui. Melpis prend en charge naturellement le français, l'anglais, l'allemand, l'espagnol, l'italien et plus de 40 langues."
        },
        {
          q: "Comment l'assistant gère-t-il les arrivées tardives et les codes de porte ?",
          a: "Il transmet automatiquement les codes d'accès, indications de stationnement et consignes d'arrivée aux clients prévus."
        },
        {
          q: "Les clients peuvent-ils réserver les services de l'hôtel (spa, vélos, restaurant) ?",
          a: "Oui. Melpis informe sur les prestations sur place, enregistre les souhaits et valorise vos services selon vos règles."
        },
        {
          q: "Comment l'équipe de réception collabore-t-elle au quotidien ?",
          a: "La réception suit l'ensemble des échanges sur tablette, mobile ou poste fixe et peut intervenir d'un simple clic."
        },
        {
          q: "Comment se déroule l'essai gratuit de 7 jours pour l'hôtellerie ?",
          a: "Démarrez en quelques minutes sans carte bancaire. Téléversez votre guide d'accueil et testez la conciergerie pendant 7 jours."
        }
      ],
      finalTitle: "Sublimez le séjour de chaque voyageur.<br>Melpis assure votre conciergerie WhatsApp.",
      finalSub: "Configurez Melpis en quelques étapes et testez-le gratuitement pendant 7 jours sans carte bancaire.",
      finalBtn: "Activer l'essai pour votre hôtel"
    },
    de: {
      vantaggiEyebrow: "Maßgeschneidert für Hotels, Pensionen und Boutique-Resorts",
      vantaggiTitle: "Echte Vorteile für Ihren Beherbergungsbetrieb",
      stepsTitle: "In 3 Schritten startklar für Ihr Hotel",
      step1Title: "WhatsApp verknüpfen",
      step1Desc: "Verbinden Sie Ihren offiziellen WhatsApp-Kanal, um Gäste vor, während und nach dem Aufenthalt zu betreuen.",
      step2Title: "Gästemappe &amp; Services hinterlegen",
      step2Desc: "Laden Sie Check-in-Hinweise, Türcodes, Frühstückszeiten und Ausflugstipps in die Wissensdatenbank.",
      step3Title: "Mehrsprachiger 24/7-WhatsApp-Concierge",
      step3Desc: "Gäste erhalten sofortige Antworten in ihrer Muttersprache – das entlastet Ihre Rezeption Tag und Nacht.",
      faqTitle: "Alles Wissenswerte für Ihr Hotel",
      faqItems: [
        {
          q: "Kann Melpis mit internationalen Gästen in verschiedenen Sprachen kommunizieren?",
          a: "Ja. Melpis versteht und antwortet auf Deutsch, Englisch, Französisch, Spanisch, Italienisch und über 40 weiteren Sprachen."
        },
        {
          q: "Wie handhabt der Assistent Spätanreisen und Zugangscodes?",
          a: "Melpis versendet Tastaturcodes, Parkplatzhinweise und Anreiseanleitungen automatisch zum gewünschten Check-in-Zeitpunkt."
        },
        {
          q: "Können Gäste Zusatzleistungen wie Spa, Fahrradverleih oder Restaurant buchen?",
          a: "Ja. Melpis beantwortet Anfragen zu internen Angeboten, Wellnesszeiten und Tischreservierungen strikt nach Ihren Vorgaben."
        },
        {
          q: "Wie arbeitet das Rezeptionsteam geräteübergreifend zusammen?",
          a: "Mitarbeiter können Chats über Smartphone, Tablet oder Rezeptions-PC einsehen und jederzeit nahtlos übernehmen."
        },
        {
          q: "Wie funktioniert die 7-tägige Testphase für Hotels?",
          a: "Starten Sie in wenigen Schritten ohne Kreditkarte. Hinterlegen Sie Ihre Hoteldaten und testen Sie den Concierge 7 Tage lang."
        }
      ],
      finalTitle: "Begeistern Sie jeden Gast ab der ersten Sekunde.<br>Den WhatsApp-Concierge übernimmt Melpis.",
      finalSub: "Richten Sie Melpis in wenigen Schritten ein und testen Sie es 7 Tage kostenlos ohne Kreditkarte.",
      finalBtn: "Testphase für Ihr Hotel starten"
    }
  }
};

const DOCS_EXTRAS = {
  en: {
    sidebarGroups: {
      g1: "Getting Started",
      g2: "Channels &amp; Integrations",
      g3: "AI Configuration",
      g4: "Daily Operations",
      g5: "Privacy &amp; Security"
    },
    searchPlaceholder: "Search guides (Ctrl+K)..."
  },
  es: {
    sidebarGroups: {
      g1: "Primeros Pasos",
      g2: "Canales e Integraciones",
      g3: "Configuración de IA",
      g4: "Operativa Diaria",
      g5: "Privacidad y Seguridad"
    },
    searchPlaceholder: "Buscar guías (Ctrl+K)..."
  },
  fr: {
    sidebarGroups: {
      g1: "Premiers Pas",
      g2: "Canaux et Intégrations",
      g3: "Configuration de l'IA",
      g4: "Opérations Quotidiennes",
      g5: "Confidentialité &amp; Sécurité"
    },
    searchPlaceholder: "Rechercher dans les guides (Ctrl+K)..."
  },
  de: {
    sidebarGroups: {
      g1: "Erste Schritte",
      g2: "Kanäle &amp; Integrationen",
      g3: "KI-Konfiguration",
      g4: "Täglicher Betrieb",
      g5: "Datenschutz &amp; Sicherheit"
    },
    searchPlaceholder: "Anleitungen durchsuchen (Strg+K)..."
  }
};

module.exports = {
  SECTOR_SHARED,
  SECTOR_EXTRAS,
  DOCS_EXTRAS
};
