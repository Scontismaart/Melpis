# Revisione Fase 1 — brand autentico e stack

12 ottobre 2026 · Decisione Sol · PR #79 resta aperta. **Brand corretto nella V2; migrazione proposta, NON eseguita. Fase 2 NON iniziata.** Questo documento sostituisce la precedente scelta avorio/verde bosco per la direzione visiva futura, senza riscrivere il blueprint Fase 0 storico.

## Brand: riscontro e proposta

I riferimenti sono stati letti realmente, incluse le regole in coda: `web/landing/brand/melpis-symbol-dark.svg`, `web/design-tokens.css` e `web/style.css`. I token condivisi attivi sono canvas #090909, surface #171717, raised #222222, testo #FAFAFA, muted #C5C5C5 e accent #FFFFFF. La dashboard ha override propri; la sola presenza di #10B981 nei token precedenti non prova che sia il colore finale di ogni controllo. Il dossier `BRAND-STACK-EVIDENCE.md` registra questa differenza. La V2 non importa questi file: ne mantiene il linguaggio con token locali, così non altera dashboard o V1.

| Ruolo V2 proposto | Colore | Motivazione |
|---|---|---|
| Canvas | #07090A | Nero profondo richiesto nella revisione |
| Surface / raised | #111416 / #1B2024 | Pannelli grafite con separazione leggibile |
| Testo | #F7F7F5 | Bianco neutro, nessun avorio caldo |
| Muted | #AAB2C0 | Grigio freddo leggibile sul dark |
| Argento / acciaio | #D7D9DC / #74777B | Derivati cromatici del simbolo originale; acciaio decorativo, non testo piccolo senza contrasto |
| CTA / stato positivo | #10B981 | Emerald esplicitamente richiesto dall'utente; scelta di direzione, non affermazione sull'attuale CTA V1 |
| Testo sulla CTA | #07090A | Contrasto: non mettere piccolo testo bianco sull'emerald |

Geometria, viewBox, tre path e gradienti del simbolo preservati: copie byte-identiche dell'originale in `assets/mv2-symbol.svg` e `assets/favicon.svg`. Header/footer usano l'immagine autentica, non la M inventata. Il file V1 è soltanto letto. SVG esterno evita collisioni dei gradient ID tra istanze.

La revisione mantiene struttura e tipografia della Fase 1, aggiornando superfici, pannelli conversazionali, icone, focus e stati. I gradienti metallici restano piccoli e statici nelle barre di pannello; nessun nuovo shader, effetto cinematografico o ricerca competitor. La firma futura sarà il rapporto fra conversazione e risultato operativo, non il logo ridisegnato.

## Stack raccomandato: B — Astro + React Islands + GSAP selettivo

Il vincolo nuovo è poter usare **componenti React reali** quando migliorano la presentazione e la demo. Non scelgo B per la quantità di dipendenze o perché più recente: evita di tradurre in vanilla componenti React complessi e permette di mantenere statiche le sezioni editoriali. Una pagina premium è ottenibile anche con A; il risultato dipende dal progetto visivo. B offre il miglior rapporto, per questa roadmap, tra riuso dei componenti desiderati e contenimento del runtime nelle sole aree interattive.

Astro produce HTML per i componenti senza direttive client; l'integrazione React consente rendering server e idratazione selettiva. [Documentazione Astro](https://docs.astro.build/en/guides/framework-components/). Questa è una capacità documentata, non una prestazione già misurata nella V2.

| Criterio | A · Vanilla + GSAP | B · Astro + React Islands + GSAP | C · React + Vite + prerender |
|---|---|---|---|
| Qualità finale | Anche molto alta; nessun tetto estetico | Molto alta con composizione statica e widget selettivi | Molto alta; framework non garantisce craft |
| Componenti reali React | Serve un'isola manuale oppure riscrittura; evitare la riscrittura dei complessi | Import nelle isole, audit dipendenze/Tailwind/Motion per singolo componente | Compatibilità diretta nell'app React |
| Motion/interazione | GSAP domina timeline/SVG, stato demo manuale | Stato demo in React; GSAP fuori o dentro un'area con lifecycle definito | Stato e lifecycle React sull'intera pagina |
| Mobile | Costo minimo iniziale, effetti comunque da misurare | React solo per isole scelte, idratazione differita dove utile | Hydration spesso più estesa; splitting e runtime da gestire |
| SEO / HTML | HTML già presente | Static output + contenuto critico renderizzato a build | Prerender da configurare esplicitamente; Vite da solo non lo aggiunge |
| Accessibilità | Buona base attuale, interazioni custom da mantenere | HTML semantico statico, fallback delle isole e focus da verificare | Stessi requisiti; fallback/hydration da verificare |
| CSP | Baseline attuale più facile da conservare | Audit bootstrap hydration e stili inline dei componenti, hash generati dal build | Audit bootstrap, stili React e prerender; nessuna scorciatoia automatica |
| Isolamento V1 | Già dimostrato | Nuovo progetto/config/public/dist SOLO V2, stessi confini | Nuovo progetto/config/public/dist SOLO V2 |
| Manutenzione | Facile ora, più onerosa se reimplementiamo molti widget | Sezioni Astro e poche isole esplicite, responsabilità separate | Uniformità React, pipeline prerender più articolata |
| Dipendenze | GSAP eventuale; React solo se poi introdotto manualmente | Astro, integrazione React, React/ReactDOM; GSAP e componenti selezionati | Vite, React/ReactDOM, prerender e componenti selezionati |
| Migrazione | Bassa | Media: estrarre layout/sezioni, adeguare build/preview/CSP/test | Medio-alta: riscrivere tutta la pagina in JSX e cablare prerender/hydration |

Costi e performance sono qualitativi: nessuna tabella di KB/FPS inventata senza build comparabili. C resta sensato per un'esperienza prevalentemente applicativa; la landing prevista è soprattutto contenuto e dimostrazione di prodotto. [Prerender React](https://react.dev/reference/react-dom/static/prerender), [SSR/prerender Vite](https://vite.dev/guide/ssr.html).

### React necessario?

**YES per utilizzare senza riscrittura i componenti React effettivamente selezionati. NO per layout, copy, pricing, FAQ, logo e motion GSAP generico.** La raccomandazione non significa rendere ogni sezione un componente client. La demo conversazionale potrà essere un'unica isola, con stato condiviso per messaggi, slot, conferma e risultato; header/footer/FAQ restano HTML statico. Niente isole per ogni bolla.

Per la hero, renderizzare comunque contenuto e primo stato significativi prima dell'idratazione; non usare `client:only` per titolo/CTA/risultato principale. `client:visible` per widget sotto il fold; hero idratata con priorità scelta dopo misura e fallback. [Direttive Astro](https://docs.astro.build/en/reference/directives-reference/#client-directives).

### Componenti e licenze: selezione limitata

- Magic UI: valutare Animated Beam e Animated List per filo e aggiornamenti della demo; sorgente MIT, adattamento visivo Melpis e controllo di dipendenze/layout/reduced motion.
- React Bits: massimo un effetto mirato, ad esempio testo o fondo controllato, solo se aggiunge comprensione. Licenza corrente MIT + Commons Clause: non equivale a MIT puro; conservare condizioni e verificare il componente/versione selezionati. Niente rivendita di componenti/template come prodotto.
- 21st.dev: catalogo multi-autore, non licenza globale unica. Verificare diritti dell'item e termini su redistribuzione/attribuzione prima di adottare codice; nessun blocco copiato automaticamente.
- GSAP: timeline della demo e transizioni di stato; `matchMedia`/cleanup, nessun double loop con Motion. Non usare contemporaneamente GSAP e una libreria Motion per lo stesso elemento.
- Shaders: famiglia ancora non identificata come pacchetto preciso. Eventuale fondo decorativo dopo scelta di tool/licenza, GPU cap e fallback statico; nessuna dipendenza scelta ora.
- Lenis: non selezionato per default. Lo scroll nativo è sufficiente finché una scena specifica non dimostra beneficio; nessuno scroll hijacking su mobile.

Licenze e fonti nel dossier di Luna; nessun pacchetto nuovo installato in questa revisione.

### Gate di migrazione da approvare

Prima di cambiare stack: approvazione utente di B. Poi incremento isolato di equivalenza strutturale, senza nuova hero: output statico con base `/landing-v2/`, assets/public/outDir/lockfile solo V2, noindex, nessuna sitemap pubblica e preview loopback. PoC minima per un componente realmente scelto, non una demo di ogni libreria.

CSP è un gate: mantenere script same-origin e connect none; valutare hash del bootstrap nel build, senza abilitare indiscriminatamente `unsafe-inline`/`unsafe-eval`. Componenti con stili inline, canvas e SSR-only/browser-only vanno provati nel PoC. Se una scelta richiede indebolimento ingiustificato o compromette fallback/contrasto, scartare quel componente. Preservare demo locale e nessun privilegio/backend.

## Roadmap Fasi 2–6 proposta, subordinata ad approvazione

Questa numerazione amplia il piano precedente, che arrivava a quattro incrementi; non implica che esista già una specifica Fase 5/6 approvata.

| Fase | Impatto / risultato | Gate |
|---|---|---|
| Revisione 1 attuale | Brand corretto, logo autentico, confronto stack | Approvare palette e raccomandazione; nessun merge |
| 2 | Se B approvato: migrazione di equivalenza isolata, poi hero ad alto impatto; composizione argento/grafite, visual originali/reali Melpis anonimizzati, stato iniziale della demo | Review separata di equivalenza e hero; budget mobile/JS/CSP |
| 3 | Demo scenografica locale: messaggio → risposta → slot → conferma → prenotazione → pannello; stato umano/esaurito | Nessun dato reale, API o LLM live; focus e reduced motion |
| 4 | Storytelling sezioni, casi verticali, prodotto, setup e piani confermati; eventuale un solo componente di catalogo utile | Copy verificato, mobile completo, niente clienti/KPI inventati |
| 5 | Motion finale controllato, eventuale shader selettivo e responsive assets; pause/offscreen, fallback energetici/mobile | Misure prima/dopo; non installare tutte le librerie |
| 6 | QA/review finale: font/CSP/licenze, fallback JS off, accessibilità, link, SEO statico/noindex sperimentale, performance e rollback progettato | Nessun rilascio/sostituzione V1 automatici; decisione esclusiva utente |

Direzione estetica per Fase 2: gerarchia più espressiva, composizione che mostra il prodotto davvero, sottili gradienti metallici e un unico punto di attenzione; non semplice aggiunta di glow alle card. Visual originali generati solo se utili e ottimizzati; mai dashboard finte presentate come screenshot autentici. La presente revisione è un esempio di brand aggiornato, non il livello finale promesso per la hero.

**STOP: nessuna migrazione, Fase 2, merge o deploy senza la nuova approvazione richiesta.**
