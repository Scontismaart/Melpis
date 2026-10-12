# Raccolta visuale verificata — Melpis Fase 0

Data: 12 ottobre 2026. Browser Chrome headless, Playwright già presente. Nessuna modifica runtime, nessun invio form, nessuna chiamata o permesso microfono.

## Metodo e limiti

Screenshot REALI 1440×900 desktop e 390×844 mobile; emulazione viewport/touch, non test su dispositivo fisico. Hero dopo DOMContentLoaded + circa 2,2 s; secondo campione +2 s per Bland/Goodcall/Manychat/Retell; scroll a 85% viewport. Cookie banner preservati negli originali: supplementi documentano eventuale rifiuto con UI reale. Ogni hero desktop/mobile è stato ispezionato tramite view_image prima di descriverlo; scroll ispezionato per Bland, Goodcall, Retell, Slang, Hostie, Respond.io. Altri scroll acquisiti come evidenza disponibile, senza dedurne dettagli non ispezionati.

Font sono dichiarazioni computed CSS, non prova del font effettivamente rasterizzato: fallback possibili. Il primo h1/h2 DOM può essere un eyebrow/navigazione e non il titolo display. Zero overflow documento nei 38 campioni non garantisce assenza clipping locale. Non sono state misurate Core Web Vitals, FPS, rete mobile o bundle size; nessun punteggio performance inventato. La densità del primo schermo e gli ostacoli cookie sono osservazioni della percezione, non benchmark tecnico.

WAAPI document.getAnimations fotografa animazioni native/CSS accessibili, non tutte le animazioni JS/GSAP, video o canvas; il limite è 30 risultati, quindi 30 significa almeno 30. Stati running possono includere delay. Screenshot temporali mostrano cambiamento di stato, non misurano fluidità.

## Evidenze per competitor

### manychat

Fonte effettivamente visitata: https://manychat.com/; stato desktop/mobile 403/403; acquisizione UTC 2026-10-12T11:21:14.743Z.

- Hero/palette: HTTP 403, schermata verifica Cloudflare; la landing NON è osservata.
- Mobile e CTA: Stesso blocco nel viewport mobile.
- Mockup/storytelling: Nessuna osservazione visuale valida del prodotto.
- Font computed body desktop: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji"; mobile: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji".
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 0/0.
- Evidenza: [desktop](screenshots/manychat-desktop-hero.png), [mobile](screenshots/manychat-mobile-hero.png), [scroll desktop](screenshots/manychat-desktop-scroll.png), [scroll mobile](screenshots/manychat-mobile-scroll.png).
- Campione temporale: [desktop +2s](screenshots/manychat-desktop-after2s.png), [mobile +2s](screenshots/manychat-mobile-after2s.png).

### goodcall

Fonte effettivamente visitata: https://www.goodcall.com/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:14.743Z.

- Hero/palette: Navy e turchese, headline molto grande; demo bianca con badge Example e tabs Build/Capture/Book. CTA Build it.
- Mobile e CTA: Header mantiene CTA Build it accanto al menu; demo scende sotto copy e CTA, continuando oltre primo schermo.
- Mockup/storytelling: Scroll: First/Then e tre passaggi Build/Test/Approve. Narrazione per fasi, non lista di feature.
- Font computed body desktop: "Plus Jakarta Sans", ui-sans-serif, system-ui, sans-serif; mobile: "Plus Jakarta Sans", ui-sans-serif, system-ui, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 30/30.
- Evidenza: [desktop](screenshots/goodcall-desktop-hero.png), [mobile](screenshots/goodcall-mobile-hero.png), [scroll desktop](screenshots/goodcall-desktop-scroll.png), [scroll mobile](screenshots/goodcall-mobile-scroll.png).
- Campione temporale: [desktop +2s](screenshots/goodcall-desktop-after2s.png), [mobile +2s](screenshots/goodcall-mobile-after2s.png).

### bland

Fonte effettivamente visitata: https://www.bland.ai/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:14.739Z.

- Hero/palette: Bianco/nero, arancio ambra sulla CTA. Hero due colonne: headline compatta e tre card sovrapposte, orb lucido centrale, frecce e play. Loghi monocromatici sotto hero.
- Mobile e CTA: Mobile: headline 36 px e CTA sopra una sola card; scompaiono le card laterali, restano frecce e play. Cookie piccolo in basso a destra.
- Mockup/storytelling: Prima sezione: tre card Build/Monitor/Improve con mini workflow, log di verifica e confronto versioni. Non è una bento asimmetrica.
- Font computed body desktop: soehne, "soehne Fallback", soehneAux, "soehneAux Fallback", Söhne, Inter, system-ui, -apple-system, BlinkMacSystemFont, sans-serif; mobile: soehne, "soehne Fallback", soehneAux, "soehneAux Fallback", Söhne, Inter, system-ui, -apple-system, BlinkMacSystemFont, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 11/11.
- Evidenza: [desktop](screenshots/bland-desktop-hero.png), [mobile](screenshots/bland-mobile-hero.png), [scroll desktop](screenshots/bland-desktop-scroll.png), [scroll mobile](screenshots/bland-mobile-scroll.png).
- Campione temporale: [desktop +2s](screenshots/bland-desktop-after2s.png), [mobile +2s](screenshots/bland-mobile-after2s.png).

### slang

Fonte effettivamente visitata: https://www.slang.ai/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:27.657Z.

- Hero/palette: Nero, gradient viola/blu, arco ampio dietro heading centrale AI Superhost for Restaurants. CTA Try Superhost.
- Mobile e CTA: Annuncio su due righe più menu alto; arco tagliato ai lati, titolo e CTA centrati. Non mostra subito una Inbox.
- Mockup/storytelling: Scroll: embed YouTube, loghi hospitality, card scure con +; posizionamento specifico ospite/ristorazione.
- Font computed body desktop: "Work Sans 2022", sans-serif; mobile: "Work Sans 2022", sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 2/2.
- Evidenza: [desktop](screenshots/slang-desktop-hero.png), [mobile](screenshots/slang-mobile-hero.png), [scroll desktop](screenshots/slang-desktop-scroll.png), [scroll mobile](screenshots/slang-mobile-scroll.png).

### hostie

Fonte effettivamente visitata: https://hostie.ai/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:28.769Z.

- Hero/palette: Giallo pieno/nero, illustrazione hospitality a tratto; hero due colonne. CTA Book a demo / Hear a demo, loghi ristoranti sotto.
- Mobile e CTA: Headline spezza in due righe; cookie modal copre gran parte di copy e CTA. Il banner è limite reale del primo accesso.
- Mockup/storytelling: Scroll: sei card rettangolari con bordi neri, benefici operativi e proprietà dati; CTA Schedule a demo flottante.
- Font computed body desktop: Manrope, sans-serif; mobile: Manrope, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 0/1.
- Evidenza: [desktop](screenshots/hostie-desktop-hero.png), [mobile](screenshots/hostie-mobile-hero.png), [scroll desktop](screenshots/hostie-desktop-scroll.png), [scroll mobile](screenshots/hostie-mobile-scroll.png).

### retell

Fonte effettivamente visitata: https://www.retellai.com/it; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:26.670Z.

- Hero/palette: Pannello hero con angoli arrotondati, gradient blu/teal, serif display bianco; CTA commerciale e prova nel menu, demo come tile in basso a destra. Redirect /it.
- Mobile e CTA: Hero quasi a tutta altezza; CTA Contact Sales e Prova gratis rimangono nel menu; demo tile dentro hero. Banner cookie non visibile nel campione mobile.
- Mockup/storytelling: Scroll: due fasce di loghi e grande messaggio serif con simbolo al centro; navbar diventa flottante.
- Font computed body desktop: "Untitled Sans", Georgia, sans-serif; mobile: "Untitled Sans", Georgia, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 4/4.
- Evidenza: [desktop](screenshots/retell-desktop-hero.png), [mobile](screenshots/retell-mobile-hero.png), [scroll desktop](screenshots/retell-desktop-scroll.png), [scroll mobile](screenshots/retell-mobile-scroll.png).
- Campione temporale: [desktop +2s](screenshots/retell-desktop-after2s.png), [mobile +2s](screenshots/retell-mobile-after2s.png).

### rosie

Fonte effettivamente visitata: https://heyrosie.com/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:41.096Z.

- Hero/palette: Lavanda, viola e navy; headline SMB e tre benefit spuntati. Prezzo di ingresso visibile nel copy; CTA trial e rassicurazione 7 giorni/cancellazione. Mockup smartphone chiamata a destra.
- Mobile e CTA: Copy e tre benefit prima della CTA larga; mockup sotto primo schermo. Pricing rimane nel copy mobile.
- Mockup/storytelling: Semplicità commerciale osservata; le funzionalità telefoniche del mockup non sono trasferibili a Melpis V1.
- Font computed body desktop: sans-serif; mobile: sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 3/1.
- Evidenza: [desktop](screenshots/rosie-desktop-hero.png), [mobile](screenshots/rosie-mobile-hero.png), [scroll desktop](screenshots/rosie-desktop-scroll.png), [scroll mobile](screenshots/rosie-mobile-scroll.png).

### frontdesk

Fonte effettivamente visitata: https://www.myaifrontdesk.com/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:40.166Z.

- Hero/palette: My AI Frontdesk oggi mostra marchio frontdesk, A Bevado Company. Hero video urbano a pieno schermo, headline grande e campo URL con CTA Get My AI Worker.
- Mobile e CTA: Copy sopra campo URL e bottone a tutta larghezza; video ancora visibile; pausa video presente.
- Mockup/storytelling: Non è la vecchia proposta solo receptionist SMB: considerare il sito attuale, non ricordi storici.
- Font computed body desktop: "General Sans", ui-sans-serif, system-ui, sans-serif, system-ui, -apple-system, sans-serif; mobile: "General Sans", ui-sans-serif, system-ui, sans-serif, system-ui, -apple-system, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 30/30.
- Evidenza: [desktop](screenshots/frontdesk-desktop-hero.png), [mobile](screenshots/frontdesk-mobile-hero.png), [scroll desktop](screenshots/frontdesk-desktop-scroll.png), [scroll mobile](screenshots/frontdesk-mobile-scroll.png).

### zendesk

Fonte effettivamente visitata: https://www.zendesk.com/it/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:47.434Z.

- Hero/palette: Hero fotografico umano con cuffie; grande headline bianca con parole evidenziate lime. Email di lavoro + prova gratuita; header demo/trial.
- Mobile e CTA: Foto esce dal primo schermo: headline su bianco, CTA email/prova impilate. Barra annuncio occupa parte superiore.
- Mockup/storytelling: Non interpretare cuffie/foto come mockup del software: sono visual editoriali.
- Font computed body desktop: "Vanilla Sans", Arial, sans-serif; mobile: "Vanilla Sans", Arial, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 3/0.
- Evidenza: [desktop](screenshots/zendesk-desktop-hero.png), [mobile](screenshots/zendesk-mobile-hero.png), [scroll desktop](screenshots/zendesk-desktop-scroll.png), [scroll mobile](screenshots/zendesk-mobile-scroll.png).

### freshchat

Fonte effettivamente visitata: https://www.freshworks.com/products/what-is-freshchat/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:50.545Z.

- Hero/palette: Pagina editoriale What is Freshchat?, non landing prodotto principale. Gradient scuro in hero e paragrafi multicanale/AI Freddy/produttività. Overlay scuro osservato nel primo campione.
- Mobile e CTA: Heading serif nel campione mobile mentre desktop è sans; corpo testo a colonna. Overlay limita giudizio contrasto/performance.
- Mockup/storytelling: DOM headings documentano Multichannel engagement, AI-powered chatbots, Agent productivity, analytics; presenza DOM non prova animazione.
- Font computed body desktop: Roboto, "Helvetica Neue", "Arial Nova", sans-serif; mobile: Roboto, "Helvetica Neue", "Arial Nova", sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 3/3.
- Evidenza: [desktop](screenshots/freshchat-desktop-hero.png), [mobile](screenshots/freshchat-mobile-hero.png), [scroll desktop](screenshots/freshchat-desktop-scroll.png), [scroll mobile](screenshots/freshchat-mobile-scroll.png).

### synthflow

Fonte effettivamente visitata: https://synthflow.ai/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:50.613Z.

- Hero/palette: Lavanda/nero; enterprise voice AI, hero due colonne con grafico posizionamento analisti. Cookie modal centrale copre molto del primo campione desktop.
- Mobile e CTA: Headline 30 px, CTA Get a demo, grafico sotto; menu compatto. Nel campione mobile iniziale modal assente.
- Mockup/storytelling: Grafico di autorevolezza, non screenshot prodotto.
- Font computed body desktop: Inter, Arial, sans-serif; mobile: Inter, Arial, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 1/1.
- Evidenza: [desktop](screenshots/synthflow-desktop-hero.png), [mobile](screenshots/synthflow-mobile-hero.png), [scroll desktop](screenshots/synthflow-desktop-scroll.png), [scroll mobile](screenshots/synthflow-mobile-scroll.png).

### vapi

Fonte effettivamente visitata: https://vapi.ai/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:21:58.067Z.

- Hero/palette: Sfondo verde scuro striato, grande sans bianco. Posizionamento builder e demo selettore Customer Support / Start call; avviso Mic permissions needed.
- Mobile e CTA: Headline 56 px; cookie banner nero copre metà inferiore e CTA/demo.
- Mockup/storytelling: Non avviata chiamata né autorizzato microfono; niente prova interazione voce.
- Font computed body desktop: seasonSans, "seasonSans Fallback"; mobile: seasonSans, "seasonSans Fallback".
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 9/8.
- Evidenza: [desktop](screenshots/vapi-desktop-hero.png), [mobile](screenshots/vapi-mobile-hero.png), [scroll desktop](screenshots/vapi-desktop-scroll.png), [scroll mobile](screenshots/vapi-mobile-scroll.png).

### cloudtalk

Fonte effettivamente visitata: https://www.cloudtalk.io/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:22:00.609Z.

- Hero/palette: Blu elettrico/viola con gradient granuloso; copy telefono + persone/AI. Card demo bianca Live, play Hear me First, campi telefono/email, CTA call. Cookie modal centrale.
- Mobile e CTA: Card demo diventa verticale, telefono precompilato prefisso IT +39; cookie banner in basso.
- Mockup/storytelling: Nessuna compilazione o chiamata. Form call è estraneo al modello Melpis V1.
- Font computed body desktop: silka, "silka Fallback", Arial, sans-serif, Arial, sans-serif; mobile: silka, "silka Fallback", Arial, sans-serif, Arial, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 5/3.
- Evidenza: [desktop](screenshots/cloudtalk-desktop-hero.png), [mobile](screenshots/cloudtalk-mobile-hero.png), [scroll desktop](screenshots/cloudtalk-desktop-scroll.png), [scroll mobile](screenshots/cloudtalk-mobile-scroll.png).

### smith

Fonte effettivamente visitata: https://smith.ai/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:22:00.206Z.

- Hero/palette: Crema/carbone/ruggine; promessa specifica law firms. Grande card trascrizione con waveform, play e nota This is a recording, più CTA live demo.
- Mobile e CTA: Headline più piccola, badges in fascia; cookie banner copre CTA. Il mockup non appare nel primo schermo.
- Mockup/storytelling: La distinzione recording/live è un buon pattern di trasparenza; nessuna demo avviata.
- Font computed body desktop: Iiincrementssans, Arial, sans-serif; mobile: Iiincrementssans, Arial, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 0/0.
- Evidenza: [desktop](screenshots/smith-desktop-hero.png), [mobile](screenshots/smith-mobile-hero.png), [scroll desktop](screenshots/smith-desktop-scroll.png), [scroll mobile](screenshots/smith-mobile-scroll.png).

### loman

Fonte effettivamente visitata: https://loman.ai/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:22:09.562Z.

- Hero/palette: Off-white con grid fine, sans molto bold, nav a pillola; hero centrato, input ristorante e mockup smartphone con KPI, messaggi flottanti gialli.
- Mobile e CTA: CTA Get Started mantiene presenza nell’header; mockup smartphone largo, messaggi laterali scompaiono. KPI nei due campioni differiscono.
- Mockup/storytelling: I KPI del mockup sono visual promozionali osservati, non risultati indipendentemente verificati.
- Font computed body desktop: Geist, Arial, sans-serif; mobile: Geist, Arial, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 2/2.
- Evidenza: [desktop](screenshots/loman-desktop-hero.png), [mobile](screenshots/loman-mobile-hero.png), [scroll desktop](screenshots/loman-desktop-scroll.png), [scroll mobile](screenshots/loman-mobile-scroll.png).

### hijiffy

Fonte effettivamente visitata: https://www.hijiffy.com/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:22:10.912Z.

- Hero/palette: Lavanda/viola/navy; hero centrato, tre benefici sintetici e messaggio AI + passaggio umano. CTA demo/tour e grande dashboard sotto. Fascia review in alto.
- Mobile e CTA: Titolo ridotto, benefici su due righe, CTA impilate; dashboard quasi tutta sotto fold.
- Mockup/storytelling: Claim percentuali e review sono contenuti dichiarati dal competitor, non metriche validate.
- Font computed body desktop: "Work Sans", sans-serif; mobile: "Work Sans", sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 1/0.
- Evidenza: [desktop](screenshots/hijiffy-desktop-hero.png), [mobile](screenshots/hijiffy-mobile-hero.png), [scroll desktop](screenshots/hijiffy-desktop-scroll.png), [scroll mobile](screenshots/hijiffy-mobile-scroll.png).

### fresha

Fonte effettivamente visitata: https://www.fresha.com/it/for-business; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:22:19.464Z.

- Hero/palette: Bianco/nero con glow viola dietro mockup calendario desktop + mobile. Headline saloni/spa, CTA inizia subito / video di presentazione. Cookie card in basso a destra.
- Mobile e CTA: CTA impilate prima del mockup; cookie card ampia copre il mockup. Redirect /it/for-business.
- Mockup/storytelling: Il calendario visualizza immediatamente il lavoro quotidiano, non tecnologia astratta.
- Font computed body desktop: RoobertPRO, AktivGroteskVF, sans-serif; mobile: RoobertPRO, AktivGroteskVF, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 2/2.
- Evidenza: [desktop](screenshots/fresha-desktop-hero.png), [mobile](screenshots/fresha-mobile-hero.png), [scroll desktop](screenshots/fresha-desktop-scroll.png), [scroll mobile](screenshots/fresha-mobile-scroll.png).

### thefork

Fonte effettivamente visitata: https://www.theforkmanager.com/it/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:22:17.436Z.

- Hero/palette: Bianco/verde petrolio; hero centrato parla ricavi/gestione prenotazioni. CTA Inizia ora + scopri piano; grande tablet sala/prenotazioni, foto ristoratore a lato. Cookie modal.
- Mobile e CTA: CTA nel menu; copy molto lungo, cookie modal con contenuto parzialmente tagliato nel campione.
- Mockup/storytelling: Non attribuire overflow globale: misura documentElement è zero, ma il testo del modal appare localmente tagliato.
- Font computed body desktop: Montserrat, arial, helvetica, arial, sans-serif; mobile: Montserrat, arial, helvetica, arial, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 0/0.
- Evidenza: [desktop](screenshots/thefork-desktop-hero.png), [mobile](screenshots/thefork-mobile-hero.png), [scroll desktop](screenshots/thefork-desktop-scroll.png), [scroll mobile](screenshots/thefork-mobile-scroll.png).

### respond

Fonte effettivamente visitata: https://respond.io/; stato desktop/mobile 200/200; acquisizione UTC 2026-10-12T11:22:22.121Z.

- Hero/palette: Nero/navy, glow viola/blu. Headline descrive problema tra chat/chiamata/email; CTA sales/trial, badge review e grande Inbox mockup.
- Mobile e CTA: Headline centrale lunga; cookie banner e chat widget occupano la metà inferiore, CTA non leggibili nel primo schermo.
- Mockup/storytelling: Scroll osservato: overlay Start interactive tour senza signup sopra Inbox. Interattività invitata, tour non ancora avviato.
- Font computed body desktop: Inter, sans-serif; mobile: Inter, sans-serif.
- Overflow documento desktop/mobile: 0/0 px. Animazioni native nel campione desktop/mobile: 10/10.
- Evidenza: [desktop](screenshots/respond-desktop-hero.png), [mobile](screenshots/respond-mobile-hero.png), [scroll desktop](screenshots/respond-desktop-scroll.png), [scroll mobile](screenshots/respond-mobile-scroll.png).

## Motion osservato e trasferimento (inferenze progettuali)

| Riferimento | Evidenza concreta | Perché può funzionare (inferenza, non A/B test) | Trasferimento originale Melpis | Limite / fallback |
|---|---|---|---|---|
| Bland | Orb cambia aspetto tra campioni; card/carousel con frecce. CSS/native animations registrate. | Punto focale controllato rende demo riconoscibile senza competere con headline. | Card conversazione con stato attivo e lieve transizione; evitare orb telefonico e richiesta microfono. | Static card con reduced motion; niente GPU obbligatoria. |
| Goodcall | Testo demo progredisce fra hero/+2s; tabs reali Build/Capture/Book; badge Example. Animazioni mv-arrive 450 ms, mv-settle 350 ms, wave 1100 ms nel DOM. | Progressione riduce distanza tra messaggio, azione e risultato; step selezionabile dà controllo. | Messaggio → risposta AI → disponibilità verificata → prenotazione → dashboard, sempre Demo simulata. | Pause/replay e step manuali; reduced motion stato finale leggibile. |
| Retell | Gradient/orb cambia tra campioni; marquee-left 100 s nel DOM, loghi visibili allo scroll; demo tile distinto. | CTA demo fisicamente vicina al valore aiuta esplorazione; gerarchia chiara. | Demo testuale aperta senza signup, prova prodotto separata da CTA commerciale. | Gradient statico mobile; non fare autoplay audio né assumere libreria shader usata. |
| Manychat | Solo challenge 403. | Nessuna conclusione visuale supportata da questa acquisizione. | Eventuali pattern conversation/automation vanno corroborati da altre fonti ufficiali dal team ricerca. | Non trattare screenshot challenge come landing. |
| Respond.io | Start interactive tour visibile su mockup Inbox, no-signup dichiarato. | Riduce attrito per capire prodotto e handoff. | Mostrare thread simulato e vista operatore nel medesimo racconto. | Mockup statico semanticamente leggibile mobile/reduced motion. |

Librerie dei competitor NON identificate né dedotte dall’aspetto. Nessuna prova che GSAP, Magic UI, React Bits, Lenis o shader siano usati da questi siti.

## Approfondimenti con interazioni innocue verificate

- **Goodcall:** clic reali sui tabs Capture e Book, desktop/mobile, senza compilare dati né avviare chiamate. [Capture desktop](screenshots/goodcall-desktop-capture.png), [Book desktop](screenshots/goodcall-desktop-book.png), [Capture mobile](screenshots/goodcall-mobile-capture.png), [Book mobile](screenshots/goodcall-mobile-book.png). Il tab attivo diventa turchese, l’avatar cambia e il titolo esplicita lo scenario. I campioni +4 s mostrano il saluto iniziale, non ancora l’esito. [Book dopo circa 23 s](screenshots/goodcall-desktop-book-later.png), ispezionato: dialogo disponibilità mercoledì / proposta ore 14 / accettazione / conferma, poi mini calendario con slot Booked e due card di notifica cliente/team. Il timer interno visualizza 00:14 e Call ended; è una simulazione Example, non prova di una telefonata reale. **Inferenza:** rendere visibile il risultato operativo dentro la stessa card riduce lo sforzo per collegare conversazione e beneficio; questa è l’idea più trasferibile a Melpis.
- **Retell:** [sezione demo desktop](screenshots/retell-desktop-demo-section.png) e [mobile](screenshots/retell-mobile-demo-section.png) ispezionate dopo rifiuto cookie. Grande orb a sinistra e form a destra; su mobile orb precede il form, richiedendo ulteriore scroll. [Selector iniziale](screenshots/retell-desktop-usecase.png) e [stato Receptionist selezionato](screenshots/retell-desktop-usecase-changed.png), ispezionati. Select nativo con receptionist, appuntamenti, lead qualification, supporto, recupero crediti, sondaggio. Si è cambiata solo l’opzione; nome/telefono sono placeholder del sito, nessun dato inserito, Ricevi una chiamata mai attivato. **Inferenza:** adattare scelta scenario a messaggi/preventivo/prenotazione reali Melpis, con demo locale senza raccogliere telefono.
- **Bland:** nessun clic sul play hero perché il testo invita a parlare con un agente; non è stata verificata una registrazione innocua. Il benchmark descrive affordance e motion decorativo realmente visibili, non qualità della conversazione.
- **Freshchat:** [campione desktop dopo Reject all](screenshots/freshchat-desktop-clean.png) ispezionato risolve l’overlay: layout editoriale off-white, testo scuro, heading serif. Questo aggiorna il primo campione desktop, in cui font/CSS erano ancora in transizione e heading risultava sans. Non assumere differenza tipografica desktop/mobile come scelta intenzionale: probabile caricamento asincrono, da verificare separatamente. Gradient iniziale non stabile nel campione successivo; non usarlo come riferimento definitivo di palette.
- **Hostie:** [mobile dopo Decline](screenshots/hostie-mobile-supplement-hero.png) ispezionato: CTA Book a demo / Hear a demo impilate, illustrazione desktop omessa, copy ben leggibile su giallo. I cookie originali restano documentati come esperienza iniziale.
- **Synthflow:** [desktop dopo DECLINE ALL](screenshots/synthflow-desktop-supplement-hero.png) ispezionato: hero lavanda e grafico confermati; pillola flottante Talk to Synthflow in basso. Nessuna chiamata avviata.
- **CloudTalk:** [desktop dopo Reject All](screenshots/cloudtalk-desktop-supplement-hero.png) ispezionato: card form e rassicurazione trial/crediti AI ben distinguibili; challenge umano sotto CTA call. Nessun invio o challenge aggirato.
- **Fresha:** [desktop dopo Solo necessari](screenshots/fresha-desktop-supplement-hero.png) ispezionato: calendario desktop e mobile sovrapposti leggibili, forte associazione fra software e agenda operativa.
- **TheFork:** [desktop dopo Continua senza accettare](screenshots/thefork-desktop-supplement-hero.png) ispezionato: mockup sala/prenotazioni centrato e fotografie di servizio/ristorante ai lati. Le foto presentano contesto operativo, non un risultato Melpis da copiare.

Azioni, URL e timestamp integrativi in [supplement-manifest.json](supplement-manifest.json) e [final-focus-manifest.json](final-focus-manifest.json). Gli screenshot supplementari sono sessioni nuove, non la continuazione della sessione originale: non comparare tempi caricamento come benchmark performance.
