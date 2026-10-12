# Sol — Fase 2: hero prodotto e review

## Direzione approvata per l'implementazione

L'ultima richiesta utente sostituisce la palette emerald della Fase 1B: V2 interamente black/graphite/silver, senza modificare la dashboard reale. Canvas #07090A, surface #111416, raised #1B2024, text #F7F7F5, muted #AAB2C0, silver #D7D9DC, steel #74777B. Il simbolo autentico, favicon inclusa, conserva geometria, proporzioni e gradienti originali.

Hero a due livelli: headline «Ogni conversazione. Sotto controllo.» e copy concreto su messaggi, prenotazioni e team; sotto, grande interfaccia Melpis, quasi full-container. Sidebar interna e topbar autenticamente ispirate al prodotto, bordi sottili, pannelli matte e profondità argento controllata. La qualità viene dalla scala e dalla leggibilità dell'interfaccia, non da card fluttuanti.

Un solo ProductShowcase React con stato locale di navigazione e sei viste reali: Panoramica, Inbox, Prenotazioni, Recensioni, Knowledge Base, Team. Ogni vista ha una struttura propria e dati sintetici; selezioni di conversazioni/prenotazioni/conoscenza sono locali e read-only. Nessuna azione di invio, pubblicazione, invito o salvataggio simulata come azione reale. Ruoli limitati ai ruoli riscontrati nel prodotto. Il precedente piccolo widget React sotto il fold può diventare statico per evitare una seconda isola senza necessità.

Mobile adattato, non ridotto in scala: selettore di sezione, contenuto a colonna, conversazioni selezionabili e testo leggibile; target almeno 44 px. Tablet intermedio dedicato. Prima vista già presente in SSR; niente client:only o skeleton obbligatorio. Navigazione a pulsanti con aria-pressed e annuncio discreto del titolo, oppure tabs solo con pattern completo.

## Fonti realmente esaminate

- Blueprint storico e ADR-002/Phase1B validation: riferimenti della roadmap, superati nelle parti di palette dalla nuova decisione utente.
- `web/index.html` in sola lettura: sidebar 24–74, topbar 125–155, KPI/Panoramica 160–270, prenotazioni 528–620, recensioni 729–900, team 988–1060, Knowledge Base 1090–1200, Inbox 1460–1560.
- `web/style.css`: override finali dashboard shell da 12274 circa, canvas inset, sidebar e topbar compatte, bordi white alpha; non deduciamo token attivi dalla prima dichiarazione.
- Screenshot reale storico `docs/screenshots/frontend-2026-09-23/dashboard-overview-desktop.jpg` ispezionato; README dichiara rendering browser con dati locali di test, ritagliato senza account. È riferimento storico di struttura, non prova della versione corrente o dato cliente.
- Il percorso richiesto `web/dashboard-router.js` non esiste nel checkout primario attuale; documentare il riscontro e leggere i moduli effettivi senza inventare un file esaminato.
- Screenshot Linear fornito dall'utente, localizzato in Pictures/Screenshots e ispezionato realmente: C:/Users/muzzo/Pictures/Screenshots/Screenshot 2026-10-12 212747.png. Osservati grande frame circa 1320 px, sidebar ampia, testo leggibile e luce neutra morbida dietro la cornice; nav, icone, colori semantici e contenuti Linear non sono copiati. Il confronto riguarda scala, densità, gerarchia e profondità; il file di riferimento non è copiato nel runtime V2.

## Scelta motion e componenti

GSAP core selettivo: entrata minima di headline/cornice e transizioni delle viste, un unico motore per elemento, senza loop o scroll hijacking. `gsap.context` / `matchMedia` e cleanup su cambio preferenza/unmount; niente contenuto essenziale invisibile senza JS. Reduced motion mantiene tutto statico e usabile. [Documentazione GSAP matchMedia](https://gsap.com/docs/v3/GSAP/gsap.matchMedia()/).

Magic UI Animated Beam/List: non servono alla navigazione di sei viste e appartengono eventualmente allo storytelling. React Bits fondi GPU/testo: non necessari al look attuale; nessuna complessità/licenza aggiunta senza valore. 21st.dev: nessun componente specifico migliora la fedeltà al prodotto rispetto alla struttura originale; catalogo non installato. Lenis/shader esclusi in questa fase. CSS lighting graphite/silver preferibile a una nuova immagine: generated assets NONE salvo necessità reale emersa in review.

CSP resta hash/same-origin con connect none, nessuna API/LLM/cookie/storage/iframe/tracking. GSAP va provato sotto la CSP reale, senza aggiungere unsafe-inline/eval. Font self-hosted, nessuna immagine decorativa grande.

## Gate di review finale

Sol esamina personalmente composizione desktop/tablet/mobile e almeno tre viste, fedeltà al prodotto, contrasto, scala leggibile, motion e costi misurati. Luna certifica build/Astro check, componenti, browser/axe/CSP, assenza verde, perf prima/dopo nelle stesse condizioni, integrità file protetti e checkout dirty. Nessun test verde sostituisce la review grafica. Poi commit/push/PR su codex/landing-v2, CI esatto SHA e PR lasciata aperta. Nessun merge Fase 2, main, GHCR, deploy o Fase 3.

Stato: implementazione esaminata e approvata tecnicamente da Sol; dettagli e limiti nella review finale qui sotto.


## Review finale Sol — 12 ottobre 2026

**PASS — accettazione tecnica e visiva.** Sol ha esaminato personalmente gli screenshot desktop 1440, tablet 768, hero mobile 390, tutte le sei viste desktop e Inbox/Prenotazioni/Knowledge Base mobile. Confronto diretto con lo screenshot Linear fornito (scala, gerarchia, sidebar, cornice) e con screenshot/codice reale Melpis; nessun asset Linear usato nella landing. La preview da 1240 px a desktop è ora il soggetto principale della hero, con testo leggibile, sei strutture realmente diverse e adattamento mobile in colonna. Non è una piccola immagine decorativa.

Codice esaminato: layout Astro, ProductShowcase e stato locale, script menu e early bootstrap, CSS e CSP. Review indipendente di codice/sicurezza, con recheck dei fix: **P0 0 / P1 0 / P2 introdotti 0**. Corrette le due etichette di contrasto: KPI e OGGI usano muted #AAB2C0. GSAP gestisce entrata minima su elementi distinti dalle transizioni CSS delle viste; context/matchMedia/disposed cleanup rispettano reduced motion. Contenuto SSR leggibile e controlli disabilitati con spiegazione quando JavaScript è assente.

Il problema mobile misurato (CLS circa 0.28) non è stato nascosto: il fallback navbar espanso collassava dopo il primo paint. Il piccolo script esterno same-origin in head, con guard `html.mv2-document`, applica la classe V2 prima del paint senza mutare pagine estranee. CSS e menu conservano il fallback espanso senza JavaScript. CSP continua a usare self/hash, connect none, nessun unsafe-inline/eval; nuovo script autorizzato da self, non da un'eccezione permissiva.

Luna certifica build, vero Astro check (13 file, zero errori/warning/hint), test statici/componenti, browser/axe su sei viewport e sei viste, console/CSP/reduced motion/JS-off/selezioni, audit npm 0 vulnerabilità, syntax/diff e integrità 650 file protetti. La certificazione browser è in viewport emulati; non sostituisce test su device fisico/screen reader completo.

Prestazioni accettate con costo esplicito, non descritte come miglioramento: il confronto accoppiato usa lo stesso Chromium 151, CPU 4x, tre contesti nuovi per viewport, font ready e quiet interval 750 ms. CLS finale osservato 0 in tutti i viewport. LCP Fase1B→2: 1440 432→696 ms; 768 480→596; 390 516→584; 320 784→764 (gli altri valori sono nel report). Iniziale JS incluso inline 5.965→318.436 byte; dopo interazione totale 229.199→318.436. La hero navigabile è eager per offrire sei viste subito, mentre il widget precedente era sotto il fold. Aumento totale circa 89 kB motivato dalla preview a sei viste e da GSAP core; nessuna libreria di catalogo/GPU aggiuntiva. CSS 32.601→60.283, HTML 22.567→27.344; 10 richieste iniziali contro 5. Nessuna immagine enorme. Risposta locale click→heading circa 67 ms mobile / 134–158 ms tablet-desktop: comprende il controllo browser e non è INP sul campo.

Generated assets **NONE**: luce/cornice graphite-silver in CSS, dashboard costruita in React/HTML, simbolo e favicon Melpis originali preservati. Magic UI / React Bits / 21st.dev / Lenis / shader non installati perché non aggiungono una funzione necessaria al requisito corrente. Licenze e versioni nel report di Luna; GSAP usa la propria licenza standard, non è presentato come MIT.

**Autorizzazione alla consegna:** dopo il controllo finale dello scope e della preservazione dei file locali estranei, Luna procede con commit/push/PR verso codex/landing-v2 e CI sull'esatto SHA. PR Fase 2 resta APERTA, auto-merge disattivato. Preview solo locale. Questa accettazione NON autorizza merge Fase 2, main, GHCR, deploy, sostituzione V1 o Fase 3.

