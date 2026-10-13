# Sol — revisione estetica hero e storytelling

Richiesta utente: ulteriore spazio sopra la dashboard, sfondo hero più suggestivo ma semplice e coerente, estetica e fluidità migliori nella sezione «Da una domanda, al prossimo passo». Base corrente: `36ec6020ea0e3af15b7e32b542c02f2454440ded`, branch `codex/landing-v2-phase-3`; integration verificata `fc6457bbd03c92618f7bea2e00a577591ed6eda3`. Revisione della stessa Fase 3, non avvio Fase 4. Nessun merge/deploy/V1/main.

## Direzione

Hero: atmosfera full width, invece del campo visivamente confinato nel container. Luce argento più percepibile e radente, un solo arco ellittico sottile e un alone sfumato dietro il prodotto. Centro del testo calmo e fondo che sfuma nel canvas #07090A. CSS statico, nessuna immagine/shader o dipendenza. Dashboard +24 desktop, +16 tablet, +8 mobile rispetto all'ultimo commit, da verificare sul rendering. Mantenerne scala, sei viste e logo originale.

Storytelling: il rail a sinistra racconta la progressione con piccoli capitoli numerati e stato corrente; scenario selezionabile con controllo nativo. La scena a destra presenta conversazione ed esito con gerarchia e identità Melpis, evitando l'accumulo di grandi blocchi ripetitivi. Avanti/Indietro/Ricomincia raccolti nel footer stabile della scena. La prenotazione finale può sostituire il riquadro disponibilità positivo già mostrato; resta visibile lo stato zero nel ramo negativo. Mobile in colonna, controlli leggibili e almeno 44 px, niente pannello microscopico o altezza fissa che taglia testo. I capitoli sono indicatori, non scorciatoie che saltano il copione.

Motion: transizioni brevi e locali dei messaggi/esiti, leggera traslazione e opacity da .94 a 1, senza attenuare o rianimare tutto il frame a ogni clic. Un solo motore per proprietà/elemento, scoped GSAP già presente; cleanup e reduced motion anche dinamico. Nessun autoplay, cambio focus o scroll forzato. Conservare SSR, hydration differita e macchina di stati/guard: zero capacità e informazioni mancanti non producono prenotazioni.

## Riferimenti osservati

- Linear: screenshot fornito dall'utente già realmente ispezionato `C:/Users/muzzo/Pictures/Screenshots/Screenshot 2026-10-12 212747.png`: grande prodotto, rim sottile e luce neutra morbida. La pagina [Linear](https://linear.app/) è stata riletta, senza attribuire effetti nuovi dal solo testo.
- [Resend](https://resend.com/): home aperta realmente nel browser e screenshot ispezionato in questa revisione. Osservati nero, luce radente e profondità matte attorno al soggetto. Riprendiamo solo il principio di illuminazione; nessun cubo 3D, font, testo, immagine o codice Resend trasferito. Prima navigazione timeout, poi tab recuperata e rendering verificato.

Il fondo finale e la composizione sono originali Melpis. Palette esclusivamente black/graphite/silver, nessun emerald. Asset generati NONE.

## Verifica e consegna

Completare entrambe le modifiche prima della campagna mirata finale. Build/check/Astro check, guard/static/component/browser sui sei viewport e tre scenari, Axe/tastiera/JS-off/reduced/reflow, CSP/noindex/no API/isolation. Non ripetere la precedente campagna completa contro tre fasi: misurare il build finale e confrontare costi con il report congelato del commit precedente; stesse definizioni inline + encoded body, distinguere iniziale/isola/totale e diagnostica shift dai Web Vitals canonici. Root review visiva su screenshot veri e fresh review read-only dei soli cambi.

Commit/push sullo stesso branch soltanto dopo review Sol PASS; CI esatta SHA. Il connettore PR ha già rifiutato 403: non ripetere né estrarre credenziali, aggiornare il fallback compare/body se l'accesso resta invariato. Git push standard è autorizzato ed è già funzionante, senza esporre credenziali. Nessun nuovo merge/deploy.

Stato: PASS; review finale completata e dettagliata qui sotto.

## Final Sol review — 13 ottobre 2026

**PASS.** Sol ha esaminato personalmente hero 1440/1024/768/390/360/320 e gli esiti positivo/esaurito/team desktop e mobile. Fondo full width con luce radente e filamento argento percepibile ma discreto; separazione dashboard +24/+16/+8 px. Nel racconto, rail, conversazione/esito affiancati e footer rendono la gerarchia più chiara, senza ridisegnare il prodotto. Individuata e corretta in review la disposizione troppo stretta del titolo mobile: testo in colonna e scenario a tutta larghezza, poi Axe/reflow/tre esiti ricontrollati a 390/360/320. Source/security recheck dei soli cambi: P0/P1/P2 residui 0. Il guard e i tre risultati restano invariati; nessuna prenotazione con capacità zero o informazioni mancanti.

Build/check/Astro, tre test, componenti, browser sei viewport/sei viste/tre scenari, audit e isolamento PASS. Reduced motion testato sul contenuto effettivo e sul cambio dinamico preferenza. Motion locale 0.28s, frame/footer stabili e nessun autoplay. La validazione automatica è emulata, non una certificazione universale su dispositivi fisici o screen reader.

Costi accettati: JS iniziale invariato 318.931 byte; totale 327.499 (+2.151), nuova isola 8.568 differiti. CSS +9.590 e HTML +1.380; zero nuovi asset o dipendenze. LCP corrente 480–624ms, nessun layout shift nella finestra iniziale. Non dichiariamo CLS zero nell'intera esperienza: somme diagnostiche post selezioni programmatiche mobile ~0.47–0.51 conservate nel report. Errore nell'opzione della prima misura e provenienza dei dati storici documentati senza ricostruire campioni mancanti; il run finale è current-only.

Consegna autorizzata: commit/push sul branch sperimentale e CI sullo SHA esatto, con fallback PR già autorizzato se il permesso resta 403. Nessun merge, deploy, modifica main/V1/dashboard/backend o Fase 4.
