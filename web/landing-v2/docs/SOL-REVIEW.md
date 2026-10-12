> Nota di revisione: documento della Fase 1 iniziale (8ce2c2d). Per palette, logo e raccomandazione stack correnti prevale ADR-002-BRAND-AND-STACK.md; nessuna migrazione approvata o effettuata.

# Sol — revisione della direzione e confini Fase 1

Riferimento effettivamente letto: `docs/reference/BLUEPRINT.md` e allegati, provenienti dal pacchetto Fase 0 del 12 ottobre 2026. La richiesta successiva limita questa fase alle fondamenta, mantenendo la hero completa e il movimento nelle fasi successive.

## Direzione

Calma operativa conservata: inchiostro dominante, superfici carta per gli esempi, mint per l'accento e il feedback. La variazione dark rispetto alla moodboard chiara applica lo stile espresso dall'utente nella Fase 1 e cambia i ruoli dei colori, non combina identità dei competitor. Outfit per titoli/UI e Inter per il corpo, entrambi locali V2. Gerarchia allineata a 56–64px desktop e 36–40px mobile; corpo leggibile e metadati separati.

La pagina contiene componenti di fondazione: contenitore, navbar/disclosure, bottoni, pannelli, sezioni, card, FAQ disclosure native e footer. La rappresentazione del percorso è statica, originale e dichiaratamente sintetica; nessun autoplay, video, shader, demo LLM o prenotazione reale. Non viene presentata come screenshot autentico della dashboard.

## Riscontri della revisione visiva

- Visionati screenshot desktop 1440px e mobile 390px: promessa, CTA e esempio sono distinguibili; il layout mobile usa una colonna, senza ridurre una dashboard completa a miniatura.
- Richiesta la correzione dei testi inizialmente troppo piccoli e del tracking del titolo mobile rispetto al blueprint.
- Richiesta la correzione del menu fallback senza JavaScript, che nel primo screenshot copriva il titolo: deve restare nel flusso e lasciare il contenuto interamente accessibile.
- Gli overflow iniziali sono stati ricondotti alle sole decorazioni trasformate e corretti nel contenitore decorativo; niente mascheramento globale degli overflow.
- Contrast review affidata a misure axe e controlli del focus, con correzioni effettive, non sola valutazione estetica.
- Tolte quote e inclusioni tariffarie fuori dal controllo commerciale ristretto. I link dei piani conducono a una sezione coerente con il testo della CTA; nessuna destinazione esterna non verificata.

## Stack, licenze e costi misurati

Runtime: HTML/CSS/JS vanilla, SVG favicon originale. Nessuna nuova libreria client. Magic UI, React Bits, 21st.dev restano riferimenti; GSAP/Lenis/shader assenti. Font OFL 1.1 e testi licenza conservati nella distribuzione. Playwright 1.62.1 (Apache-2.0) e axe-core 4.14.0 (MPL-2.0) sono esclusivamente strumenti di sviluppo/test, fuori dist. Audit npm senza vulnerabilità al controllo del 12 ottobre 2026.

Misure sul bundle statico locale, da riconfermare con il report QA finale: JavaScript circa 0,5 KB gzip, CSS circa 7 KB gzip; nessuna immagine hero, video o dipendenza remota. Non sono misure LCP/INP/CLS sul campo.

## Gate tecnico

L'approvazione tecnica finale di questa sola fase richiede report browser aggiornato e stabile sullo stesso contenuto sorgente, isolamento byte-identico, build e test mirati superati e nessun finding Security residuo. Il report conclusivo registra tale esito; questa nota non sostituisce prove mancanti.

Nessuna autorizzazione a Fase 2, merge, deploy o sostituzione V1. Il branch di integrazione resta sperimentale. Font e markup V1 non sono mai sovrascritti; Docker, routing ufficiale, auth/provider e backend non sono modificati.
