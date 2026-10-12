# QA / Security documentale — Melpis Fase 0

Revisione: 12 ottobre 2026. Ambito: BLUEPRINT.md, design-board.html, benchmark, evidenze prodotto/visive e manifest. Read-only sugli artefatti esaminati; nessun runtime modificato. Questa revisione non certifica prodotto live, performance o autorizzazioni esterne.

## Esito

Pacchetto completo per approvazione della direzione. Recheck finale delle correzioni eseguito sui file correnti: tutti e tre i findings iniziali risolti. Nessun blocker documentale, di sicurezza o promessa V1 non supportata residuo trovato nel blueprint. L'esito non autorizza Fase 1, merge o deploy.

## Copertura dei dieci deliverable

| Deliverable | Esito | Evidenza |
|---|---|---|
| 1 Benchmark tutti competitor | PASS | luna-research.md: 13 schede × 15 voci; existing-competitors.md: 6 schede × 15 voci. Totale 19 × 15. |
| 2 Shortlist 5 | PASS con limite esplicito | Goodcall, Bland, Retell, Hostie, Manychat. Manychat visual/motion NV per challenge; Respond.io fallback del trattamento Inbox. |
| 3 Screenshot disponibili | PASS | screenshot-manifest.json: 19 competitor; supplement-manifest.json e final-focus-manifest.json. 110 PNG referenziati complessivamente, tutti presenti in screenshots/. Manychat challenge distinto da landing. |
| 4 Animazioni efficaci | PASS con limiti | visual-observations.md motion e interazioni innocue; BLUEPRINT.md tabella effetto/costo/mobile. Campioni non equivalenti a video o misure performance. |
| 5 Matrice idee | PASS | 19 righe individuali, più pattern da evitare, perché/adattamento/libreria/priorità e classi A/B/C. |
| 6 Moodboard originale | PASS | design-board.html palette/composizione originali; differenza dal brand attuale dichiarata; Arial sostituto font esplicito. |
| 7 Hero demo | PASS | Copy e macchina di stati proposta, dati fittizi, nessuna AI live o raccolta contatti. |
| 8 Wireframe desktop/mobile | PASS | Board desktop e mobile, sezioni e ordine; bento a tre celle e dettagli servizio/durata/risorsa ora coerenti. |
| 9 Mappa librerie/componenti | PASS | Magic UI, shader, GSAP, Lenis, React Bits, 21st.dev valutati; vanilla baseline; GSAP unica opzione eventuale. Nessuna installazione. |
| 10 Piano per fasi | PASS | 0–4 con uscita/gate, approvazione obbligatoria prima di Fase 1, nessun merge/deploy implicito. |

## Findings — recheck finale

### F0-QA-01 — RISOLTO: collegamento manifest

Riletto `BLUEPRINT.md:19`: il link punta ora a `screenshot-manifest.json`, presente nella directory del blueprint. Sono stati aggiunti galleria e indice screenshot. Collegamenti relativi verificati sui file correnti; nessun collegamento mancante nella verifica conclusiva.

### F0-QA-02 — RISOLTO: bento desktop

Riletto `design-board.html:15`: tre celle distinte “Informazioni”, “Regole”, “Staff”, con Staff sulla seconda riga. La tavola ora corrisponde ai tre oggetti dichiarati in `BLUEPRINT.md:156` e all'ordine concettuale mobile.

### F0-QA-03 — RISOLTO: dettagli prenotazione dimostrativa

Riletto `design-board.html:14`: controllo disponibilità esplicita “Taglio · 30 min · Postazione 1” e risultato “15:30–16:00 · Postazione 1”, insieme alle etichette dati dimostrativi e assenza di prenotazioni/messaggi reali. “Domani” resta testo del copione fittizio dichiarato, senza calcolo di disponibilità reale né backend: non rappresenta una promessa di data reale. Nella Fase 1 usare un giorno demo fisso o relativo dichiarato, coerente con gli altri stati.

Recheck supplementare: appendice Luna usa ora “message-to-booking” e “inbound messaging” per Melpis. La fonte Inbox è aggiornata; nessuna telefonia V1 suggerita da questi passaggi. Nessun blocker residuo.
## Verifica confini prodotto / security

- Voice/call esclusi esplicitamente dal progetto Melpis; pattern telefonici studiati soltanto come riferimenti.
- Nessuna integrazione presentata come universale o già certificata; prerequisiti canali espliciti. Goodcal/Goodcall identità non fusa.
- Nessuna promessa di recensioni richieste automaticamente, crescita garantita, onboarding in 2 click, metriche/testimonial clienti Melpis inventati.
- Pricing/trial sospesi al gate commerciale; report tecnico documenta €29/69/149 attuali vs documento storico differente. Non viene creato un nuovo listino.
- Demo proposta è copione locale deterministico; niente chiamate LLM, API prenotazione, dati account, microfono, numero/email o effetti esterni. LLM non autorizza prenotazioni.
- Board attuale è HTML/CSS statico: nessun tag script, fetch, form/input, iframe, audio/video o codice di eventi rilevato. Due CTA rappresentate come span dichiarati elementi schematici; non sono pulsanti funzionanti di prodotto.
- Dati illustrativi marcati persistentemente; prenotazione nell'esempio e risultato separati dal prodotto live. Rami disponibilità esaurita e intervento staff previsti.
- Nessun segreto/credenziale di Melpis rilevato negli artefatti progettuali letti. Le acquisizioni appartengono a pagine pubbliche, non a dashboard cliente.
- Originalità delimitata: asset acquisiti per ricerca interna; futura landing usa mockup/text/asset originali. Librerie non attribuite ai competitor senza prova.
- Reduced motion, JS assente, tastiera, stop offscreen e scheda nascosta sono requisiti di Fase 1, non dichiarazioni di QA runtime già superata.

## Coerenza delle evidenze

Luna è esplicitamente una revisione testuale; l'appendice visuale aggiunge il rendering osservato senza fingere che tutte le dimensioni fossero verificate nella prima passata. Le schede mantengono NV dove appropriato. Il manifest originale usa nomi file base: risolverli rispetto a screenshots/, non alla directory del JSON. Tutti i 110 file così referenziati sono presenti.

Non sono stati ritestati i siti live né ispezionate indipendentemente tutte le 110 immagini dal reviewer: il report visivo esplicita quali campioni il raccoglitore ha ispezionato. Le verifiche qui sono di copertura, consistenza, presenza file, confini funzionali e assenza di effetti privilegiati. Nessun giudizio di conversione misurata o certificazione CWV.

## Gate da mantenere

Prima di Fase 1: approvazione utente della direzione/palette/hero. Prima di pubblicazione futura: prezzi e checkout coerenti, prerequisiti integrazioni, revisione dettagli demo, link reali, contrasto/mobile/keyboard e misure performance. Se una demo futura usa dati o backend veri, nuova verifica Architect/Security obbligatoria: è un ambito differente dal copione illustrativo autorizzato qui.

