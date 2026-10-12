> Nota di revisione: documento della Fase 1 iniziale (8ce2c2d). Per palette, logo e raccomandazione stack correnti prevale ADR-002-BRAND-AND-STACK.md; nessuna migrazione approvata o effettuata.

# QA finale browser — Fase 1

12 ottobre 2026. Eseguito da Sol dopo le correzioni di Luna sul build congelato: **97 controlli, 97 PASS, zero errori**. Evidenza locale `artifacts/browser-qa/results.json`; include SHA256 prima/dopo identici dei tre file runtime. Il workflow ripete gli stessi test su Chromium e sullo SHA head esatto; il risultato remoto va verificato nella PR, non dedotto da questo report locale.

## Verifica mirata

| Controllo | Risultato |
|---|---|
| Desktop 1440/1024/768px | PASS: zero overflow, font locali caricati, ancore reali |
| Mobile 390/360px | PASS: zero overflow, menu touch e tastiera |
| Reflow 320px (equivalente layout a zoom 400%) | PASS: nessuno scroll orizzontale |
| Menu | PASS: Enter, Tab su link con focus visibile, Escape con focus restituito, chiusura su link e reset resize |
| Tastiera pagina | PASS: elementi raggiungibili, focus visibile, nessuna CTA vuota |
| JavaScript disabilitato 1440/390/360px | PASS: navigazione esposta, ancore funzionanti, header non copre titolo |
| Reduced motion | PASS: zero durate e nessuna animazione attiva |
| Axe A/AA nei cinque viewport principali | PASS: zero violazioni rilevate |
| Console, pageerror, richieste fallite e CSP | PASS: zero errori rilevati |
| Rete | PASS: solo asset locali nel namespace `/landing-v2/`, nessun servizio esterno |
| Isolamento CSS/JS | PASS: import V2 in un documento senza namespace V2 non cambia DOM o stile del documento sentinella |
| Demo e prezzo | PASS: dati sintetici, nessun form, solo importi mensili di riferimento e CTA setup |
| Meta/header | PASS: noindex/nofollow, CSP senza connect/form/frame/object |

Correzioni effettivamente verificate: clipping limitato alle decorazioni che causavano overflow; contrasto metadati e label; nome accessibile del brand; favicon locale; fallback menu senza JavaScript nel flusso; guard del modulo JS prima di registrare eventi o modificare DOM. Timeout del test no-JS risolto riportando lo scroll in cima istantaneamente tra azioni distinte, senza forzare click su elementi instabili.

## Screenshot comparativi

Evidenze in `artifacts/browser-qa/`: hero e pagina completa V2 nei cinque viewport, tre varianti senza JS, V1 protetta a 1440/360px e `compare.html`. Rendering V1 contemporaneo del baseline origin/main con contesto anonimo, server statico read-only separato e nessun invio/API; quattro file V1 hash invariati prima/dopo. Non sono screenshot di clienti o di produzione. Gli asset V1 non entrano nel bundle V2.

## Limiti della verifica

Viewport/touch emulati, non telefoni fisici. Axe e test focus non sono una certificazione WCAG completa né un audit con screen reader. Il reflow è verifica del layout a 320px, non tutte le tecnologie di zoom. Nessun baseline pixel approvato: regressione visuale pixel INCONCLUSIVE, distinta dall'ispezione visiva e dall'isolamento byte-identico. Nessuna misura sul campo di LCP/INP/CLS o esito commerciale.

La sola preview utente resta `http://127.0.0.1:4175/landing-v2/`; server temporanei di QA e confronto chiusi. Nessun deploy o cambiamento della V1.
