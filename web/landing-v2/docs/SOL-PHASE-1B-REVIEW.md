# Sol — Fase 1B: decisione architetturale e gate di review

## Autorizzazione e perimetro

L'utente ha approvato Astro + React Islands + GSAP selettivo, autorizzato il solo merge commit della PR #79 su `codex/landing-v2` e l'apertura della PR 1B. La 1B non si mergia. Nessun deploy, cambio main, sostituzione V1 o sviluppo Fase 2.

## Architettura approvata per la migrazione

- Output Astro statico; una sola pagina sotto `/landing-v2/`, dist, lockfile, font e asset locali alla V2.
- Layout Astro e sezioni editoriali statiche: titolo/hero/CTA/prezzi/FAQ/footer già presenti nell'HTML, senza dipendere da React.
- Una sola React Island sul pannello sintetico esistente sotto il fold, con `client:visible`, stato controllato locale e SSR significativo. Il widget prova idratazione e interazione, non anticipa la demo scenografica.
- CSS della V2 preservato e isolato. Navbar e FAQ non richiedono React; nessuna migrazione della dashboard.
- Nessuna libreria motion/GPU/catalogo aggiunta ora. GSAP verrà scelto solo per una sequenza concreta nelle fasi autorizzate.

## Sicurezza preventiva

Bootstrap Astro e React richiedono esecuzione di script locali e piccoli bootstrap inline. Consentire soltanto gli hash generati nella build, oltre a `'self'`; mantenere `connect-src 'none'`, nessun `unsafe-inline`/`unsafe-eval`, nessuna API/backend/LLM/tracking. Header della preview e CSP meta devono essere coerenti: un header più restrittivo del meta potrebbe bloccare l'isola.

Usare la configurazione CSP supportata dalla versione stabile selezionata, verificandola nell'output reale. Non aggiungere `strict-dynamic` se gli hash e same-origin bastano. Mantenere `frame-ancestors 'none'` nell'header HTTP (non supportato nel meta). Preview loopback, noindex/nofollow, nessuna sitemap pubblica.

[Configurazione ufficiale Astro CSP](https://docs.astro.build/en/reference/configuration-reference/#securitycsp) · [React integration](https://docs.astro.build/en/guides/integrations-guide/react/).

## Gate finale

Review personale Sol: granularità delle isole, HTML iniziale/SEO, CSP, cleanup, accessibilità, performance misurate prima/dopo, geometria logo, equivalenza desktop/mobile, isolamento e manutenzione. QA di Luna è l'evidenza da esaminare, senza rieseguire inutilmente l'intera suite. Nessun P0/P1/P2 introdotto può restare irrisolto.

Stato iniziale: implementazione e gate finali in corso. Non costituisce ancora approvazione del codice.

## Review finale personale Sol — 12 ottobre 2026

Architettura e codice runtime esaminati direttamente: configurazione Astro, layout, pagina, sezioni statiche, ControlPanelDemo.jsx, CSS, script menu, build/check e server preview. Esaminati i risultati di Luna e confrontati visivamente gli screenshot prima/dopo della hero desktop 1440 e mobile 390, oltre alla pagina desktop completa. Layout, copy, geometria logo, colori, gerarchia e responsive preservati. La trasformazione delle due righe del pannello in pulsanti è limitata alla prova interattiva autorizzata; stato SSR iniziale e aspetto corrispondono alla baseline.

| Gate | Review |
|---|---|
| Architettura | Astro 7.3.8 statico, base/outDir V2; @astrojs/react 6.0.2 stabile compatibile, lock esatto |
| Granularità React | Una sola isola `client:visible`, React/ReactDOM 19.3.0; nessun React sulla hero o sulle sezioni editoriali |
| Static HTML / SEO | Titolo, CTA, prezzi, FAQ e stato iniziale già renderizzati; noindex/nofollow e robots disallow |
| CSP / sicurezza | Hash build same-origin coerenti fra meta e header; connect none, no unsafe-inline/eval, no API/LLM/tracking/segreti; loopback e frame-ancestors none |
| Accessibilità | QA 133 controlli browser PASS, tastiera/focus/menu/FAQ/JS-off/reduced motion/axe; etichette dei pulsanti corrette. Audit automatico ed emulazione non equivalgono a audit completo screen reader/dispositivo reale |
| Cleanup | Stato React locale; nessun timer, subscription o risorsa asincrona persistente da rilasciare. SSR/fallback/idratazione e transizioni provati realmente |
| Brand / equivalenza | Simbolo/favicons e font originali; namespace MV2; nessun redesign aggiuntivo. Selezione e divisore del pannello preservati dopo fix del reset pulsanti |
| Performance | Misure Chromium 151, CPU 4x, 3 contesti nuovi per viewport; font body + headline precaricati, nessun preload Latin Extended inutilizzato. CLS 0 osservato in tutti i sei viewport |
| Isolamento | V2 e suo workflow come unico perimetro; gate byte-identical e snapshot checkout originale richiesti prima del commit |
| Manutenzione | Sezioni Astro leggibili, stato locale in un componente; CSS/asset/build indipendenti. Harness riproducibile e baseline compatta tracciata |

Costo reale iniziale: HTML 22.567 byte (baseline 15.276), CSS 32.601 (33.400), JavaScript inline 5.965 (0) e JavaScript esterno 0 (1.135), 5 richieste invece di 6. Gli script inline sono inclusi nei byte HTML, non sommati una seconda volta. Il bundle React differito aggiunge 223.234 byte JS esterni / 3 richieste quando il pannello entra in vista; non si dichiara quindi assenza di JavaScript. Visible-to-enabled osservato circa 230–259 ms include caricamento locale più idratazione, non solo tempo CPU di React.

LCP locale finale 1440: 472→448 ms; 390: 492→452 ms; altri viewport oscillano rispetto alla baseline (320: 448→676, 360: 428→448, 768: 448→552, 1024: 444→412). Il costo maggiore osservato è +228 ms a 320 px; va mantenuto visibile nel report, non descritto come miglioramento generalizzato. Non sono risultati sul campo o promesse di velocità; le mediane locali restano sotto 700 ms ma non sostituiscono una prova su rete/dispositivo reale. Questi dati misurano una migrazione tecnica e non provano un miglioramento universale. Gli effetti futuri richiedono nuovi budget e prove su rete/dispositivo reali. Il font-swap che aveva prodotto CLS mobile è stato corretto, senza nasconderlo con tolleranze.

Review indipendente del codice/sicurezza e recheck del fix CSS: nessun P0/P1/P2 introdotto residuo. Sicurezza verificata nel perimetro statico locale e delle dipendenze, non presentata come penetration test completo.

**APPROVAZIONE TECNICA SOL:** PASS del codice e del design equivalente; autorizzato procedere con commit/push/apertura PR 1B dopo i gate finali di isolamento e diff check, quindi CI sull'esatto commit. PR 1B da lasciare aperta. Questa approvazione non autorizza merge 1B, deploy o Fase 2.
