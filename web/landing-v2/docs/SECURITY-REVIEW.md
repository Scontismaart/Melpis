# Senior code / Security review — Landing V2 Fase 1

12 ottobre 2026; baseline 2994fae7ea54094482a40374ea46c08aae8d0407. Review index/styles/app, build/preview/check/isolation, test statici/browser e nuovo workflow. Nessuna modifica UI/tool/test/CI/V1 dal reviewer; soltanto documenti Security e provenance. Preview loopback statica, non servizio produzione.

## Esito finale dopo recheck

Nessun P1 o blocker Security residuo rilevato nello scope statico locale. Entrambi i P2 risolti e verificati sul markup corrente. QA browser completo resta responsabilità del reviewer QA separato; qui nessuna certificazione screen reader o CWV.

### V2-REVIEW-01 — RISOLTO — quote fuori scope

Recheck index.html:70–72: le tre card mostrano soltanto nome, “Listino mensile di riferimento”, importo mensile 29/69/149 e ancora setup. Quote e inclusioni rimosse. Il contenuto corrisponde al perimetro commerciale Fase 1; checkout live non dichiarato confermato.

### V2-REVIEW-02 — RISOLTO — CTA con promessa non consegnata

Recheck index.html:70–72: testo ora “Come si configura” → #mv2-setup. La sezione setup presente espone informazioni, percorso, canale e verifica: testo e destinazione coerenti, nessun form/auth/contatto esterno fittizio.
## Verifiche eseguite

- Attestazione storica del reviewer: tools/isolation.mjs PASS su prima versione, 560 file protetti byte-identici e 31 file fase nel perimetro. Versione corrente riletta ora estende la protezione a tutto web (oltre src/locales/config), con controllo separato dello scope committed e deroga soltanto locale per graphify-out. Nessuna nuova esecuzione finale attestata da questo reviewer: il conteggio ampliato e relativo PASS provengono dal check finale Sol/QA. Nessun contenuto protetto o segreto stampato.
- tools/check.mjs su dist corrente PASS. Tre font V2 SHA256 uguali a V1; due licenze source/dist identiche. Provenance aggiornato dopo recheck: decorazioni UI HTML/CSS/Unicode; introdotto un solo favicon SVG originale manuale.
- Preview avviata dal reviewer su porta effimera loopback e terminata dopo probe; nessuna chiamata esterna/backend.
- Raw HTTP: GET home 200; HEAD 200 body 0; POST 405; traversal encoded 403; backslash encoded 403; NUL 403; percent invalido 400; docs/tool 404; /app/ e /registrati/ 404; licenza font 200. CSP connect-src none presente anche errori. Nessun body sensibile.

## Analisi codice e confini

Preview deriva dist da import.meta.url, GET/HEAD soltanto, decode invalido intercettato, NUL/backslash/.. respinti e path.resolve containment con separatore. Nessun proxy/V1/auth. readFile nel catch restituisce 404 senza stack o percorsi. File piccoli bufferizzati interamente; niente stream parziali/error path da correggere. HEAD senza body. Bind 127.0.0.1. Server serve il dist controllato dalla build allowlist: niente upload/untrusted files, non trattarlo come servizio pubblico di produzione.

CSP script/style/font self, connect none, frame/object/base/form/ancestors none. Nosniff, no-referrer, no-store. No inline handler/eval. Licenza .txt application/octet-stream scaricabile: non vulnerabilità. Robots/noindex riducono indicizzazione e non sono controlli accessi.

Build root fisso V2 e dist figlio confinato; rm ricorsivo solo quel target, copie esplicite di 3 file applicativi, 3 font, 2 licenze, robots e favicon SVG aggiunto alla allowlist. Nessun docs/reference/benchmark/tool/secret nel bundle. Nessuna dipendenza runtime, write fuori V2 o config Docker/prod.

Isolation compara diff+untracked e byte protetti tramite git cat-file --filters per checkout Windows. execFileSync con args strutturati, niente shell. Baseline fissato; env override è tooling locale, non input visitatore. Nessun reset/stash.

UI JS è modulo e modifica solo menu/ARIA/dataset, Escape e ritorno focus. Nessuna innerHTML input, fetch/XHR/WebSocket, API, storage persistente, serviceworker o tracking. HTML ancore/dati sintetici; niente form/microfono/prenotazione/invio/LLM. Nessun effetto privilegiato: boundary tenant/consenso/billing/auth invariati. Nessun claim voice/KPI/clienti veri inventati.

CSS usa font V2 self e token --mv2-. ID/classi mv2; :root contiene solo token mv2, html rule namespace mv2-document; reset confinato mv2-page. Niente import remoto. Eventuale caricamento ridondante font viene misurato da QA performance, non finding Security.

CI nuovo dedicato push codex/landing-v2-phase-* e PR codex/landing-v2, paths circoscritti, contents read, checkout head SHA effettivo, persist-credentials false, npm ci ignore-scripts/audit. Nessun deploy/publish/segreto/provider/backend. Playwright/axe dev confinati V2. Artifacts QA sintetici. Static test pertinente; raw HTTP probe aggiunge malformed/HEAD/auth. Non audit di tutto Melpis: landing non espone quel sistema.

## Gate

I due P2 sono risolti e codice finale riletto; completare o acquisire QA browser/console/CSP/reflow e isolamento sullo stato definitivo. Sol sign-off tecnico prima commit/PR. Nessun merge/deploy/Fase 2 impliciti.


## Favicon aggiunto nel recheck

assets/favicon.svg contiene soltanto svg/rect/path, forma geometrica originale della lettera m e colori Melpis. Nessun script, href esterno, foreignObject o dato sensibile. index.html:10 lo carica same-origin. È asset scritto manualmente, non immagine prodotta con tool generativo né derivata da competitor. La review del SVG è diretta sul file corrente.

