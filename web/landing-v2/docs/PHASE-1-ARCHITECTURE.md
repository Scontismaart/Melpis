# Landing V2 — Fase 1: decisione Sol

Base: origin/main `2994fae7ea54094482a40374ea46c08aae8d0407`, aggiornato il 12 ottobre 2026. Integrazione `codex/landing-v2`; implementazione `codex/landing-v2-phase-1`. Nessuna sostituzione V1, merge o deploy autorizzati.

Riferimento: blueprint e allegati originali in `C:/Users/muzzo/Desktop/whatsapp-ai-responder/docs/marketing/melpis-phase0-2026-10-12/`. Copie testuali in `docs/reference/` conservano contenuti storici senza correggere la Fase 0 retroattivamente; link a screenshot/galleria nei riferimenti rimandano al pacchetto originale, non sono pagine distribuibili. 110 immagini di benchmark restano fuori dal bundle V2.

## Scelte approvate da Sol per questa fase

- Stack del blueprint: HTML semantico, CSS, SVG originali, JavaScript vanilla in modulo. Zero librerie runtime; nessun React, GSAP, Lenis, shader o canvas. Il copione interattivo e la hero cinematografica sono fuori Fase 1, secondo l'ultimo scope utente.
- Tutti i file applicativi, tool, test, font e documenti appartengono a `web/landing-v2/`. Solo il workflow dedicato `.github/workflows/landing-v2.yml` vive dove GitHub richiede i workflow. Non modificare alcun workflow esistente.
- Il dark elegante richiesto ora usa i colori del blueprint con ruoli adattati: fondo inchiostro `#17211C`, testo carta `#F4F2EA`, superfici chiare `#FFFEFA`, verde `#176B4D`, mint `#DCEFE5`, terracotta `#B7523B`. Non introdurre neon, glow diffuso o identità di concorrenti. Conferma dei contrasti nel QA.
- Outfit e Inter self-hosted copiati dal baseline V1 in font V2 rinominati; file V1 soltanto letti. Conservare licenze upstream e hash/provenienza. Nessuna dipendenza al percorso V1 nel browser.
- Namespace classi/ID `mv2-`, custom properties `--mv2-`, JS module-scoped senza globali. CSS rooted in `.mv2-page`; reset confinato a quella radice. Nessun service worker o localStorage.
- Preview HTTP Node, loopback `127.0.0.1`, porta dedicata, docroot limitata a dist V2, URL `/landing-v2/`. Nessun proxy a backend o V1; traversal e file di sviluppo non serviti. CSP same-origin, connect-src none, frame/object/base/form disabilitati; script/style esterni locali. Noindex/nofollow meta e header, robots disallow all.
- Build separata Node senza dipendenze: allowlist dei soli asset statici → dist ignorata all'interno V2. Tool mai scrivono fuori V2; nessun Dockerfile o configurazione produzione modificata.
- Nessun asset generato in questa fase: texture e immagini non migliorano le fondamenta. SVG decorativi originali, senza dashboard fittizie. Asset generation autorizzata per fasi successive, non obbligo a usarla ora.

## Contenuto e conversione

Navbar, mobile disclosure, hero introduttiva statica, struttura casi/come funziona/pannello/controlli/setup/piani/FAQ/footer. Quattro settori richiesti: ristoranti, hotel, barbieri, centri estetici. Placeholder futuri chiaramente marcati, nessun falso testimonial, certificazione o KPI. Esempio di percorso statico con dati sintetici identificati; nessun invio o prenotazione reale.

L'hostname `melpis.it` compare nei file canonici ma DNS non risolvibile in questa sessione (curl e web). Le CTA Fase 1 devono usare ancore reali presenti nella V2, ad esempio Scopri il percorso → #mv2-percorso e Consulta i piani → #mv2-piani. Nessun link account/trial esterno attivato se non verificabile; documentare il gate, senza bottone finto o href vuoto. Footer link interni validi. Non aggirare il problema servendo auth/V1 nel server V2.

Prezzi previsti utente 29/69/149 e piano applicativo aggiornato concordano. Verificare e documentare questa riconciliazione limitata; non desumere checkout live, IVA, quote, trial o sconti annuali. Se mostrati, qualificarli come listino mensile di riferimento e lasciare fuori inclusioni non confermate.

## Verifiche / confini

Developer (Luna) implementa solo le fondamenta e tool isolati. QA verifica desktop 1440/1024/768 e mobile390/360, menu con mouse/touch/tastiera/Escape, fallback JS off e reduced motion, zoom/reflow, console/rete/CSP, ancore e assets. Sol esamina rendering e codice, contrasti e scope. Security: nessuna API, form, privilegio, tracking, dati reali o modifica auth/provider.

Isolamento su due piani: git diff rispetto al base è limitato a V2 e nuovo workflow (graphify-out dirty locale atteso); hash SHA256 di tutti i file protetti uguale nel worktree. Checkout primario sporco confrontato con snapshot prima/dopo; niente stash/reset sulle modifiche dell'utente.

CI dedicata: Node build, test mirati e isolamento, browser QA se disponibile con dev dependencies solo V2. Trigger push `codex/landing-v2-phase-*` e PR verso `codex/landing-v2`; nessuna permission write, backend, Docker publish o deploy. Checkout del commit esatto per push, SHA head della PR quando pull_request. Workflow esistente main invariato.

Gate: solo dopo QA e approvazione tecnica Sol, commit/push/PR aperta verso `codex/landing-v2`; controllo CI sul SHA definitivo. Nessun merge automatico. Fase 2 richiede nuova approvazione utente.
