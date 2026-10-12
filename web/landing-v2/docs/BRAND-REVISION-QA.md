# QA revisione brand — PR #79

12 ottobre 2026. Brand approvato tecnicamente da Sol; nuova architettura proposta, non applicata. Fase 2 non iniziata.

- Build, controlli statici e due test mirati: PASS.
- Browser: 97/97 controlli PASS sia in Chrome sia in Chromium incluso con Playwright. Hash dei tre file runtime identici prima/dopo QA e ancora corrispondenti alla revisione.
- Desktop 1440/1024/768; mobile 390/360 e reflow 320: PASS. Tastiera/menu/Escape/focus, reduced motion, JS disabilitato, CSP, noindex e axe: PASS. Console senza errori. Questi controlli non sostituiscono una verifica completa con screen reader o misure di performance sul campo.
- 650 file protetti nel worktree byte-identical rispetto alla baseline origin/main; 1036 file tracciati del checkout originale preservati rispetto allo snapshot iniziale, incluse modifiche locali preesistenti.
- Logo e favicon: copie byte-identiche del simbolo canonico; nessuna immagine generata e nessuna nuova dipendenza.
- graphify update completato nel worktree; gli artefatti del grafo non fanno parte della PR.
- Corretto il test di resize: dopo il cambio viewport attende il reset effettivo del menu (timeout 2 secondi), anziché leggere lo stato prima della consegna dell'evento matchMedia. Nessuna modifica al runtime del menu; una mancata chiusura continua a fallire il test.
- Preview locale: http://127.0.0.1:4175/landing-v2/. Nessuna pubblicazione.

Screenshot e report locali, non pubblicati: `artifacts/brand-review/v2-1440-hero.png`, `v2-390-hero.png`, `v2-1440-full.png`, `v2-390-full.png`, `browser-results-chrome.json`, `browser-results-bundled.json`.

Decisione e confronto A/B/C: [ADR-002-BRAND-AND-STACK.md](ADR-002-BRAND-AND-STACK.md). Dati e fonti: [BRAND-STACK-EVIDENCE.md](BRAND-STACK-EVIDENCE.md).

V1, dashboard, backend, homepage e main invariati. PR lasciata aperta. Nessun merge/deploy. L'approvazione della proposta Astro deve precedere qualsiasi migrazione e l'avvio della Fase 2.
