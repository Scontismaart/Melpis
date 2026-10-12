# Melpis Fase 0 — evidenza prodotto e compatibilità tecnica

Data verifica: 12 ottobre 2026. Revisione locale read-only: non prova il contenuto del dominio pubblico, credenziali attive, migrazioni applicate o collaudo di ogni integrazione. Nessuna modifica runtime, installazione, merge o deploy.

## Metodo e gerarchia delle fonti

È stata eseguita prima `graphify query "What are Melpis implemented product capabilities, current landing stack, branding, pricing and prior redesign documents?"`. I nodi sono stati poi verificati sui file correnti. Per comportamento e prezzi prevale codice corrente + pagine correnti; documenti storici non sono fonte di claim commerciali. Osservato = presente nei file ispezionati; certificato = richiede distinta prova operativa; proposta = raccomandazione per Fase 1.

## Capacità e limiti da rappresentare

| Capacità | Stato osservato | Fonte file:riga | Claim prudente per landing |
|---|---|---|---|
| Risposte ai messaggi WhatsApp e Instagram | Implementazione e integrazioni configurabili; account cliente richiede autorizzazioni/credenziali | `README.md:5`; `src/api/routes/integrations.py:148`; `src/api/routes/integrations.py:209`; `docs/mirofish-context-melpis.md:56` | Assistente AI sui canali collegati; spiegare prerequisiti Meta |
| Inbox condivisa e intervento staff | UI presente; risposta manuale richiede presa in carico | `web/index.html:1568`; `web/index.html:1597`; `web/app.js:4838` | Quando serve, il team prende in carico la conversazione |
| Knowledge base | UI Conoscenza e fonti aziendali presenti | `web/index.html:68`; `web/app.js:4769`; `docs/mirofish-context-melpis.md:73` | Usa le informazioni che fornisci sulla tua attività; contenuti e regole richiedono verifica |
| Guardrail | Validazione applicativa presente prima del completamento del flusso | `src/core/receptionist/conversation_orchestrator.py:26`; `src/core/receptionist/conversation_orchestrator.py:272` | Risposte e richieste passano attraverso controlli; evitare garanzie di infallibilità |
| Disponibilità e prenotazione | Motore interno e servizio di prenotazione; verifica capienza e serializzazione nello stesso servizio | `src/core/bookings/service.py:112`; `src/core/bookings/service.py:184`; `src/core/bookings/service.py:209`; `src/core/receptionist/conversation_orchestrator.py:377` | L'assistente raccoglie i dati; Melpis verifica lo slot e registra la prenotazione |
| Google Calendar | Creazione, aggiornamento, cancellazione eventi e freebusy implementati, connessione OAuth necessaria | `src/core/calendar/service.py:144`; `src/core/calendar/service.py:196`; `src/core/calendar/service.py:241`; `src/core/calendar/service.py:269` | Prenotazioni e calendario collegato; non promettere sincronizzazione universale o istantanea |
| Recensioni Google | Import/sync e generazione bozze presenti; revisione umana | `src/core/reviews/google_service.py:227`; `src/core/reviews/google_service.py:414`; `docs/mirofish-context-melpis.md:59` | Prepara bozze di risposta alle recensioni; non campagne automatiche post-servizio |
| Dashboard | Panoramica, Inbox, Prenotazioni, Recensioni, Team, Simulatore, Conoscenza e Configurazione | `docs/mirofish-context-melpis.md:46`; `web/index.html:62` | Visualizza conversazioni e prenotazioni e gestisci lo staff |
| Preview onboarding | Usa profilo inserito e responder; distinto dal simulatore generale | `docs/mirofish-context-melpis.md:99` | Testa una preview prima di collegare i canali; distinguere configurazione personale e demo |
| Simulatore generale | UI presente; endpoint generico con profili demo statici secondo documentazione tecnica | `docs/mirofish-context-melpis.md:100` | Esempio dimostrativo, non prova del tuo account in produzione |
| Voice/telefonate | Nessuna evidenza raccolta per prodotto telefonico V1; esplicitamente fuori scope utente | Vincolo utente; ricerca limitata ai flussi messaggi | Escludere receptionist telefonica, telefonate automatiche, waveforms che suggeriscono chiamate |

La demo landing deve usare dati fittizi dichiarati: «Demo illustrativa · dati di esempio · nessun messaggio inviato». Flusso consigliato: messaggio cliente → risposta AI → verifica disponibilità → prenotazione confermata dal sistema → dashboard. Inserire almeno un ramo di disponibilità esaurita e uno di intervento staff, senza far sembrare l'LLM una fonte di autorizzazione autonoma.

## Integrazioni: implementato non equivale a certificato

Il router include SimplyBook, Cal.com, Apaleo, Beds24 e ZaK: `src/core/bookings/router.py:183`, `:203`, `:217`, `:256`. Il runbook dichiara verifica live di Apaleo, ma questa revisione non ha acquisito il verbale o un test live indipendente: `docs/operations/external_booking_integrations.md:80`. Trattare questo come dichiarazione nel runbook, non come certificazione universale.

ZaK resta gated: vietate modalità authoritative/mirror nelle route, consentite shadow/local_only (`src/api/routes/integrations.py:371`, `:492`; `docs/operations/external_booking_integrations.md:82`). La modalità mirror non è implementata con webhook PMS; router fallback ad authoritative e blocco difensivo (`src/core/bookings/router.py:177`, `:330`). Non mettere una barra «integrazioni certificate» con tutti i loghi. Non annunciare MioDottore o gestionali non verificati. Una connessione richiede credenziali, permessi, eventuale contratto e verifica della specifica configurazione.

## Prezzi da mantenere coerenti

| Piano | Codice mensile | Quota | Staff | Annuale nel codice |
|---|---:|---:|---:|---:|
| Essenziale / starter | €29 | 500 messaggi | 1 | €288 |
| Crescita / pro | €69 | 2.000 messaggi | 3 | €708 |
| Scala / business | €149 | 10.000 messaggi | senza limite numerico nel piano | €1.548 |

Fonte primaria: `src/core/billing/plans.py:22`, `:35`, `:48`. Quote pubblicate corrispondenti: `web/landing/prezzi/index.html:408`, `:442`, `:475`. Trial: 150 messaggi AI (`src/core/billing/plans.py:4`); durata configurazione 7 giorni (`src/core/billing/config.py:6`). Il sito presenta prova senza carta e senza rinnovo automatico (`web/landing/prezzi/index.html:75`). Non ripubblicare prezzi annuali senza confermare la modalità commerciale effettiva del checkout.

Il documento chiamato «definitivo» è in conflitto con corrente: prezzi €49/99/199 (`melpis-piano-pricing-definitivo.md:35`) e trial 14 giorni/150 conversazioni (`:86`). È storico, non fonte vigente. Distinguere messaggi da conversazioni. Non citare come attuale la sua affermazione sui costi Meta di ottobre 2026 (`:5`): richiede verifica esterna dedicata. Niente overage automatico €0,08 e nessun piano free permanente se non attivi.

## Claim da escludere o rivedere

- AI voice receptionist, telefonate automatiche, «risponde alle chiamate».
- «In 2 click», «attivo in 5 minuti», migrazione o coesistenza garantita del numero WhatsApp: la connessione documentata richiede prerequisiti Meta e credenziali (`docs/mirofish-context-melpis.md:56`).
- «Raccoglie automaticamente recensioni dopo ogni appuntamento»: non dimostrato; l'attuale home lo suggerisce (`web/landing/index.html:588`) ma il flusso verificato è sync/import e bozze.
- Qualunque numero di clienti, messaggi reali, percentuale di conversione o ROI non supportato da dataset e autorizzazione. La specifica storica include 500 attività/10.000 conversazioni/78% (`docs/superpowers/specs/2026-07-26-landing-page-design.md:65`, `:103`): non è prova.
- «Nessun cliente perso», «sempre disponibile», «zero errori», «ogni prenotazione garantita», riduzione no-show quantificata non verificata.
- «Apprende automaticamente da ogni conversazione», «ogni gestionale integrato», «certificato» senza attestazione.
- Diagnosi, prescrizioni o garanzie mediche sulle allergie; valutazione delle richieste sensibili da parte dello staff (`docs/mirofish-context-melpis.md:93`).

## Stack e brand osservati

La landing corrente usa HTML, CSS e JavaScript vanilla. `package.json:1` contiene strumenti di lint/test e non dipendenze React. `web/landing/index.html:29` carica brand-logo e `:1563` carica app.js. Font locali Outfit e Inter (`web/landing/style.css:9`, `:25`; preload `web/landing/index.html:30`). Alcuni override nominano Geist Sans con fallback Inter (`web/landing/style.css:5831`): non prova di font Geist realmente caricato.

Fonte visuale primaria: `web/design-tokens.css:1`: canvas #090909, surface #171717, raised #222222, testo #fafafa, muted #c5c5c5, accent bianco #ffffff, card radius 24px. Lo stylesheet conserva commenti storici Emerald/Lime, ma `--lime` corrente è bianco (`web/landing/style.css:53`): non derivare palette dai commenti. La specifica luglio «Sempre», Fraunces/salvia/Tailwind/AOS è superata rispetto ai file correnti (`docs/superpowers/specs/2026-07-26-landing-page-design.md:11`).

Sono già presenti motion vanilla, IntersectionObserver, animazione contatori, chat timeline, reveal e sequenze KB/calendar (`web/landing/app.js:15`, `:221`, `:315`, `:507`, `:852`, `:957`). La chat hero dispone di gestione visibilitychange (`:331`) e reduced motion; parte dei CSS flow ha fallback (`web/landing/style.css:6254`). Da consolidare in Fase 1, senza sommare timer/observer duplicati.

Documenti di redesign precedenti rilevanti: luglio design/implementation; settembre landing-critical-fixes; 2 settembre eliminate-ai-slop-and-brand-alignment e ui-frontend-redesign. Sono contesto storico, non istruzione a reintrodurre brand Sempre o stack CDN.

## Librerie: valutazione per la landing esistente

Valutazioni costo/performance qui sotto sono stime tecniche, non benchmark misurati. Nessuna libreria installata. «shader-effects / shaders» non identifica un pacchetto preciso: Paper Shaders è un candidato da valutare, non l'identità verificata della libreria già selezionata dall'utente.

| Libreria / equivalente | Uso eventuale | Compatibilità | Costo tecnico | Impatto atteso | Decisione e fallback |
|---|---|---|---|---|---|
| Magic UI Animated Beam, Animated List, Bento Grid | Legame messaggio → azione; elenco eventi dashboard | Componenti React; non drop-in vanilla | Medio se adattamento originale SVG/CSS; alto se introdurre React solo per questo | Basso SVG/CSS limitato; maggiore con runtime e motori aggiunti | ADATTARE il pattern con SVG/CSS originale, non installare. Mobile percorso verticale; reduced motion linea statica e tutti gli stati visibili |
| GSAP core + ScrollTrigger | Una timeline narrativa sincronizzata, se CSS/WAAPI non bastano | Imperativo JS, compatibile con DOM corrente | Medio, richiede cleanup/media queries/refresh | Moderato, dipende da pinning e layout; animare transform/opacity | Unica dipendenza candidata. Condizionata a prototipo e misure. Mobile senza pin; reduced motion sequenza completa statica |
| Lenis | Smooth scroll | JS compatibile; esiste già scroll-behavior smooth e gestione scroll | Medio per coordinamento/ancore/focus | RAF continuo e rischio conflitti | EVITARE nella prima implementazione. Scroll nativo conserva accessibilità e prevedibilità |
| React Bits (BlurText / background simili) | Motion tipografia o accento visuale | React, alcune varianti richiedono ulteriori motori | Alto rispetto a semplice CSS | Variabile; WebGL continuo più costoso | EVITARE runtime. Headline sempre leggibile, reveal originale CSS senza blur mobile |
| 21st.dev | Catalogo per studiare pattern di card/tabs | Componenti React con dipendenze per singolo componente | Medio/alto e audit licenze/dipendenze | Non generalizzabile al catalogo | Solo riferimento. Costruire UI originale vanilla; non importare blocchi interi |
| Paper Shaders candidato | Halo cromatico/texture astratta discreta | Pacchetto vanilla disponibile, nessuna React necessaria | Medio/alto per GPU, resize, context loss | GPU/energia e startup; costo dipendente risoluzione shader | POSTICIPARE. Solo decorativo, lazy/offscreen stop, DPR limitato, static WebP su mobile/reduced motion/assenza WebGL |
| CSS + Web Animations API + SVG | Chat a stati, badge disponibilità, prenotazione, handoff | Nativo stack esistente | Basso/medio | Basso se dimensioni riservate e no blur pesante | ADOTTARE come baseline; controlli pausa/ripeti e niente loop obbligatorio |

Fonti ufficiali consultate il 12 ottobre 2026: [Magic UI Animated Beam](https://magicui.design/docs/components/animated-beam), [GSAP ScrollTrigger](https://gsap.com/docs/v3/Plugins/ScrollTrigger/), [Lenis](https://github.com/darkroomengineering/lenis), [React Bits](https://reactbits.dev/), [21st.dev](https://21st.dev/), [Paper Shaders](https://github.com/paper-design/shaders). Nessuna deduzione sul software usato dai competitor: un effetto simile non prova una libreria.

## Gate tecnico e QA proposto per Fase 1

1. Approvazione direzione e script demo, mantenendo demo locale senza LLM calls/side effects/credenziali.
2. Consolidare design tokens reali e font; creare asset originali e UI fittizia esplicitamente etichettata.
3. Implementare prima HTML/CSS leggibile senza JavaScript; baseline mobile 360/390px e desktop 1440px. Mantenere ordinamento narrativo e CTA sopra la demo mobile.
4. Aggiungere timeline originale con pausa/ripeti, attivazione quando visibile, stop fuori schermo/tab nascosta, reduced motion con contenuto completo statico. Non usare ARIA live per ogni animazione decorativa.
5. Misurare baseline e versione prototipo in condizioni ripetibili. Target progettuali, non risultati attuali: LCP ≤2,5s, INP ≤200ms, CLS ≤0,1; immagini dimensionate, budget asset motivato, niente shader che blocchi headline/CTA.
6. Verificare keyboard/focus, contrasto, target touch, overflow, CTA reali, pricing coerente anche nei metadati/schema/i18n, assenza claim voice e dati di prova sociale inventati. Eseguire lint/test web e audit pertinenti in Fase 1; questa Fase 0 non esegue test runtime.
7. Security obbligatoria se la Fase 1 introduce collegamenti a LLM tool, dati account, auth o effetti esterni. Evitare tali dipendenze nella demo illustrativa.
8. Dopo eventuali modifiche codice Fase 1: `graphify update .`. In questa revisione documentale non è stato modificato codice.

Conclusione tecnica: la direzione può raccontare prenotazioni via messaggi, controllo disponibilità e collaborazione con lo staff usando capacità già presenti. La demo deve rendere espliciti dati fittizi, controlli e prerequisiti. La scelta minima è HTML/CSS/vanilla + SVG; GSAP soltanto se il prototipo dimostra valore. Nessuna necessità di migrare la landing a React o installare tutto il catalogo.
