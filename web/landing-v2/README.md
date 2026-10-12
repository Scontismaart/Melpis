# Melpis Landing V2 — Fase 1

Landing statica dimostrativa, isolata dalla V1. Usa HTML semantico, CSS e JavaScript vanilla; non contiene account, backend, API, invio di messaggi o prenotazioni. I casi mostrati sono esempi sintetici. I tre prezzi sono un listino mensile di riferimento e non attivano un acquisto.

## Comandi

Da questa cartella:

- La build e la preview richiedono Node.js 24 e non richiedono librerie client.
- Per i controlli browser: `npm ci --ignore-scripts`, poi `npx playwright install chromium`. Su Windows con Chrome già disponibile: `$env:V2_BROWSER_CHANNEL='chrome'` prima del test.

- `npm run build` compila solo la allowlist V2 dentro `dist/`.
- `npm run check` verifica struttura, ancore, namespace, font e vincoli statici della build.
- `npm test` esegue i test statici e quelli del server preview.
- `npm run preview` serve `dist/` solo su `127.0.0.1:4175`, al percorso `/landing-v2/`.
- `npm run test:browser` è riservato al QA browser della fase.
- `node tools/isolation.mjs` confronta scope e byte dei file protetti con il baseline documentato.

La build non modifica file fuori da `web/landing-v2/`. Il preview serve esclusivamente `dist/`, imposta CSP senza connessioni in uscita e intestazioni noindex, e rifiuta richieste traversal. Non è un server di produzione. Nessun link di account è abilitato: le call to action portano a sezioni presenti in questa pagina.

## Ambito

Navbar con menu mobile accessibile, percorso dimostrativo statico, quattro verticali (ristoranti, hotel, barbieri e centri estetici), controlli e passaggio al team, configurazione, prezzi di riferimento e FAQ. La hero resta statica; non ci sono demo animate, shader, librerie runtime, tracker o moduli di conversione.

Evidenze e approvazione tecnica: [validazione locale](docs/LOCAL-VALIDATION.md), [QA browser](docs/QA-BROWSER.md), [review Sol](docs/SOL-REVIEW.md), [Security](docs/SECURITY-REVIEW.md). Screenshot in `artifacts/browser-qa/` esclusi dal bundle; workflow di CI ripetibile su commit esatto, senza deploy.
